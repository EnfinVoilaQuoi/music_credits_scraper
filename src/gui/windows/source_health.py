"""Fenêtre « État des sources ».

Deux informations de nature différente y cohabitent, et elles ne doivent JAMAIS
se mélanger :

  · le **statut de la sonde** — la source répondait-elle à l'instant du clic ?
    C'est la pastille colorée, inchangée depuis toujours ;
  · l'**usage réel** — ce que des centaines d'appels ont révélé pendant les
    enrichissements. Il occupe des colonnes voisines et ne repeint rien : un
    403 anti-bot normal ne doit pas faire clignoter une source qui sert.

Les sources sont regroupées par UTILITÉ (crédits/paroles, données
additionnelles, certifications, streams) parce que la question qu'on se pose
n'est jamais « comment va Kworb ? » mais « puis-je récupérer mes streams ? ».
Une source qui sert deux familles apparaît dans les deux, avec les appels du
flux correspondant — c'est à cela que sert la ventilation par flux.

Couche mince : le calcul vit dans `src.observability.rollup` et
`src.utils.source_health` (pilotables aussi en CLI).

Threads : `start_worker` + `stop_requested` (contrat lifecycle.py). Les sondes
comme les lectures de compteurs sont du fond purement sync, sans lien avec la
boucle asyncio. L'arrêt est coopératif — testé entre deux sources, jamais au
milieu d'une requête.
"""

import os
from tkinter import messagebox

import customtkinter as ctk

from src.concurrency.lifecycle import start_worker, stop_requested
from src.config import BASE_DIR
from src.observability import rollup
from src.observability.registry import FAMILY_LABELS, FAMILY_ORDER
from src.observability.repository import SourceUsageRepository
from src.utils.logger import get_logger
from src.utils.source_health import (
    BREAKAGE_PROCEDURE,
    SOURCES,
    check_all,
    load_health,
    save_health,
)

logger = get_logger(__name__)

_STATUS_COLOR = {
    "ok": "#2e7d32",  # vert
    "degraded": "#ef6c00",  # orange
    "broken": "#c62828",  # rouge
    "unknown": "#616161",  # gris
}
_STATUS_LABEL = {
    "ok": "OK",
    "degraded": "Dégradé",
    "broken": "Cassé",
    "unknown": "Inconnu",
}
_ISSUE_LABEL = {
    "timeout": "délai dépassé",
    "unreachable": "injoignable",
    "throttled": "cadence (429)",
    "blocked": "anti-bot",
    "auth": "authentification",
    "parse": "structure changée",
    "crash": "scrape cassé",
}
_FAMILY_ICON = {"credits": "🎤", "audio": "🎚", "certs": "🏆", "streams": "📈", "media": "🖼"}
_WINDOWS = {"7 jours": 7, "30 jours": 30, "Tout": None}
_TOUS = "Tous les artistes"
_MAINTENANCE_DOC = BASE_DIR / "docs" / "maintenance-sources.md"

_GRIS = "gray"


def _couleur_defaut():
    """Couleur de texte du thème : `configure(text_color=None)` n'est pas valide."""
    return ctk.ThemeManager.theme["CTkLabel"]["text_color"]


class SourceHealthWindow:
    """Fenêtre CTkToplevel « État des sources » (une seule instance à la fois)."""

    def __init__(self, app):
        self.app = app
        self._stop = False  # drapeau d'arrêt LOCAL (fermeture de cette fenêtre)
        self._rows: dict[str, dict] = {}  # clé de source -> widgets de sa ligne
        self._sections: dict[str, dict] = {}  # famille -> widgets de sa section
        self._artists: list[tuple[int, str]] = []
        self._views: dict[str, rollup.SourceUsageView] = {}

        self.window = ctk.CTkToplevel(app.root)
        self.window.title("État des sources")
        self.window.geometry("1080x720")
        self.window.transient(app.root)
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_header()
        self._build_banner()
        self._build_sections()
        self._build_procedure()
        self._load_from_disk()
        self.refresh_usage()

    # ── Construction ───────────────────────────────────────────────────────────
    def _build_header(self):
        header = ctk.CTkFrame(self.window)
        header.pack(fill="x", padx=10, pady=(10, 5))

        ctk.CTkLabel(header, text="🩺 État des sources", font=("Arial", 16, "bold")).pack(
            side="left", padx=10, pady=8
        )

        self.full_button = ctk.CTkButton(
            header, text="Vérif complète", width=130, command=lambda: self._start_check("full")
        )
        self.full_button.pack(side="right", padx=5, pady=8)

        self.fast_button = ctk.CTkButton(
            header, text="Vérif rapide", width=130, command=lambda: self._start_check("fast")
        )
        self.fast_button.pack(side="right", padx=5, pady=8)

        # Le filtre artiste pilote TOUT le panneau : ce n'est pas une vue à part.
        self.artist_box = ctk.CTkComboBox(
            header, width=200, values=[_TOUS], command=lambda _: self.refresh_usage()
        )
        self.artist_box.set(_TOUS)
        self.artist_box.pack(side="right", padx=(10, 5), pady=8)

        self.window_box = ctk.CTkSegmentedButton(
            header, values=list(_WINDOWS), command=lambda _: self.refresh_usage()
        )
        self.window_box.set("7 jours")
        self.window_box.pack(side="right", padx=5, pady=8)

        self.status_label = ctk.CTkLabel(header, text="", text_color=_GRIS)
        self.status_label.pack(side="left", padx=10)

    def _build_banner(self):
        """Bandeau des pannes locales — masqué tant qu'il n'y en a pas."""
        self.banner = ctk.CTkLabel(
            self.window, text="", anchor="w", justify="left", text_color="#ef6c00"
        )

    def _build_sections(self):
        body = ctk.CTkScrollableFrame(self.window, label_text="")
        body.pack(fill="both", expand=True, padx=10, pady=5)
        specs_par_famille: dict[str, list] = {f: [] for f in FAMILY_ORDER}
        for spec in SOURCES:
            for family in spec.families:
                specs_par_famille[family].append(spec)

        for family in FAMILY_ORDER:
            self._build_section(body, family, specs_par_famille[family])

    def _build_section(self, parent, family, specs):
        frame = ctk.CTkFrame(parent)
        frame.pack(fill="x", padx=4, pady=(4, 8))

        entete = ctk.CTkFrame(frame, fg_color="transparent")
        entete.pack(fill="x")
        titre = f"{_FAMILY_ICON.get(family, '•')}  {FAMILY_LABELS[family]}"
        bouton = ctk.CTkButton(
            entete,
            text=f"▾  {titre}",
            anchor="w",
            fg_color="transparent",
            hover=False,
            font=("Arial", 13, "bold"),
            command=lambda f=family: self._toggle(f),
        )
        bouton.pack(side="left", padx=4, pady=(4, 0))
        agregat = ctk.CTkLabel(entete, text="", anchor="e", text_color=_GRIS)
        agregat.pack(side="right", padx=10, pady=(6, 0))

        table = ctk.CTkFrame(frame, fg_color="transparent")
        table.pack(fill="x", padx=6, pady=(2, 6))
        for col, poids in enumerate((4, 1, 2, 2, 2, 4)):
            table.grid_columnconfigure(col, weight=poids)
        for col, texte in enumerate(
            ("Source", "Sonde", "Appels", "Échec comm.", "Nature", "Dernière erreur")
        ):
            ctk.CTkLabel(table, text=texte, font=("Arial", 11, "bold"), anchor="w").grid(
                row=0, column=col, sticky="w", padx=6, pady=(2, 6)
            )

        for i, spec in enumerate(specs, start=1):
            self._build_row(table, i, spec)

        self._sections[family] = {
            "frame": frame,
            "table": table,
            "bouton": bouton,
            "titre": titre,
            "agregat": agregat,
            "ouverte": True,
        }

    def _build_row(self, table, ligne: int, spec):
        nom = ctk.CTkLabel(table, text=spec.label, anchor="w", cursor="hand2")
        nom.grid(row=ligne, column=0, sticky="w", padx=6, pady=2)
        nom.bind("<Button-1>", lambda _e, k=spec.key: self._show_failures(k))

        statut = ctk.CTkLabel(table, text="—", text_color=_GRIS, anchor="w")
        statut.grid(row=ligne, column=1, sticky="w", padx=6, pady=2)
        self._bind_tooltip(statut)
        appels = ctk.CTkLabel(table, text="—", anchor="w", text_color=_GRIS)
        appels.grid(row=ligne, column=2, sticky="w", padx=6, pady=2)
        echecs = ctk.CTkLabel(table, text="—", anchor="w", text_color=_GRIS)
        echecs.grid(row=ligne, column=3, sticky="w", padx=6, pady=2)
        nature = ctk.CTkLabel(table, text="", anchor="w")
        nature.grid(row=ligne, column=4, sticky="w", padx=6, pady=2)
        erreur = ctk.CTkLabel(table, text="", anchor="w", justify="left", wraplength=260)
        erreur.grid(row=ligne, column=5, sticky="w", padx=6, pady=2)

        # Une source bi-famille a DEUX lignes : on les mémorise toutes.
        self._rows.setdefault(spec.key, {"lignes": [], "spec": spec})["lignes"].append(
            {
                "statut": statut,
                "appels": appels,
                "echecs": echecs,
                "nature": nature,
                "erreur": erreur,
            }
        )

    def _build_procedure(self):
        frame = ctk.CTkFrame(self.window)
        frame.pack(fill="x", padx=10, pady=(5, 10))

        top = ctk.CTkFrame(frame, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkLabel(top, text="🔧 En cas de casse", font=("Arial", 13, "bold")).pack(
            side="left", padx=10, pady=(6, 0)
        )
        ctk.CTkButton(
            top, text="Ouvrir la procédure complète", width=220, command=self._open_doc
        ).pack(side="right", padx=10, pady=(6, 0))

        ctk.CTkLabel(
            frame, text=BREAKAGE_PROCEDURE, anchor="w", justify="left", font=("Consolas", 10)
        ).pack(anchor="w", padx=10, pady=(2, 8))

    def _toggle(self, family):
        section = self._sections[family]
        section["ouverte"] = not section["ouverte"]
        if section["ouverte"]:
            section["table"].pack(fill="x", padx=6, pady=(2, 6))
        else:
            section["table"].pack_forget()
        fleche = "▾" if section["ouverte"] else "▸"
        section["bouton"].configure(text=f"{fleche}  {section['titre']}")

    # ── Statut de la sonde ─────────────────────────────────────────────────────
    def _load_from_disk(self):
        saved = load_health()
        if not saved:
            self.status_label.configure(text="Aucune vérification enregistrée")
            return
        for key, data in saved.items():
            self._apply_row(key, data)
        self.status_label.configure(text="Dernier état de sonde chargé")

    def _apply_row(self, key: str, data: dict):
        entree = self._rows.get(key)
        if not entree:
            return
        status = data.get("status", "unknown")
        latence = data.get("latency_ms")
        infobulle = f"{data.get('level', '—')}"
        if latence:
            infobulle += f" · {latence} ms"
        checked = (data.get("last_checked") or "").replace("T", " ")
        for ligne in entree["lignes"]:
            ligne["statut"].configure(
                text=_STATUS_LABEL.get(status, status),
                text_color=_STATUS_COLOR.get(status, _GRIS),
            )
            # Niveau et date de sonde ne concernent QUE la sonde : ils
            # encombraient la ligne, ils vivent désormais dans l'infobulle.
            ligne["statut"].sh_tooltip = (
                f"{infobulle}\n{checked}\n{data.get('message', '')}".strip()
            )

    @staticmethod
    def _bind_tooltip(widget):
        """Infobulle minimale (customtkinter n'en fournit pas).

        Liée UNE FOIS à la construction : la relier à chaque rafraîchissement
        empilerait les callbacks et ouvrirait autant de bulles.
        """
        widget.sh_tooltip = ""
        bulle: dict = {}

        def montrer(_event):
            if bulle.get("fenetre") is not None or not widget.sh_tooltip:
                return
            haut = ctk.CTkToplevel(widget)
            haut.wm_overrideredirect(True)
            haut.wm_geometry(f"+{widget.winfo_rootx() + 20}+{widget.winfo_rooty() + 20}")
            ctk.CTkLabel(haut, text=widget.sh_tooltip, justify="left").pack(padx=6, pady=4)
            bulle["fenetre"] = haut

        def cacher(_event):
            fenetre = bulle.pop("fenetre", None)
            if fenetre is not None:
                fenetre.destroy()

        widget.bind("<Enter>", montrer)
        widget.bind("<Leave>", cacher)

    # ── Usage réel ─────────────────────────────────────────────────────────────
    def _repo(self) -> SourceUsageRepository | None:
        repo = getattr(self.app, "source_usage_repo", None)
        if repo is not None:
            return repo
        engine = getattr(getattr(self.app, "data_manager", None), "engine", None)
        return SourceUsageRepository(engine) if engine is not None else None

    def refresh_usage(self):
        """Recharge les compteurs (lecture DB pure → thread de fond)."""
        repo = self._repo()
        if repo is None:
            return
        jours = _WINDOWS.get(self.window_box.get(), 7)
        artist_id = self._selected_artist_id()

        def worker():
            since = rollup.since_day_for(jours)
            lignes = repo.daily(since_day=since, artist_id=artist_id)
            echecs = repo.recent_failures(limit=400)
            artistes = repo.artists_with_usage()
            vues = rollup.summarize(lignes, echecs, window_days=jours)
            episodes = rollup.network_episodes(echecs)
            self._safe_after(lambda: self._apply_usage(vues, episodes, artistes))

        start_worker(worker, name="source_usage")

    def _selected_artist_id(self) -> int | None:
        choix = self.artist_box.get()
        for artist_id, nom in self._artists:
            if nom == choix:
                return artist_id
        return None

    def _apply_usage(self, vues, episodes, artistes):
        self._views = vues
        self._sync_artists(artistes)
        familles = {spec.key: spec.families for spec in SOURCES}

        for key, entree in self._rows.items():
            vue = vues.get(key)
            for ligne in entree["lignes"]:
                self._apply_usage_row(ligne, vue, entree["spec"])

        for section in rollup.summarize_by_family(vues, familles):
            widgets = self._sections.get(section.family)
            if widgets is None:
                continue
            if section.calls:
                texte = f"{section.calls:,} appels · {section.failure_rate:.1%} d'échec".replace(
                    ",", " "
                )
            else:
                texte = "aucun appel sur la période"
            if section.indeterminate:
                texte += f"  ·  ⚠ {section.indeterminate} indéterminés"
            widgets["agregat"].configure(text=texte)

        self._apply_banner(episodes)

    def _apply_usage_row(self, ligne, vue, spec):
        if vue is None or not (vue.calls or vue.indeterminate or vue.blocked_expected):
            ligne["appels"].configure(text="—", text_color=_GRIS)
            ligne["echecs"].configure(text="—", text_color=_GRIS)
            ligne["nature"].configure(text="")
            ligne["erreur"].configure(text=spec.usage_note or "")
            return

        appels = f"{vue.calls:,}".replace(",", " ")
        if vue.absent:
            appels += f"  (dont {vue.absent} sans donnée)"
        ligne["appels"].configure(text=appels, text_color=_couleur_defaut())

        if vue.comm_failures:
            ligne["echecs"].configure(
                text=f"{vue.comm_failures} ({vue.failure_rate:.1%})",
                text_color=_STATUS_COLOR["broken"],
            )
        else:
            ligne["echecs"].configure(text="0", text_color=_GRIS)

        nature = _ISSUE_LABEL.get(vue.dominant_issue, vue.dominant_issue)
        if vue.blocked_expected:
            # Le contournement anti-bot reste VISIBLE sans être alarmant : s'il
            # grimpe pendant que les succès s'effondrent, Cloudflare a durci.
            suffixe = f"{vue.blocked_expected} anti-bot contournés"
            nature = f"{nature} · {suffixe}" if nature else suffixe
        ligne["nature"].configure(text=nature)

        if vue.last_failure:
            ligne["erreur"].configure(
                text=f"{vue.last_failure.replace('T', ' ')} — {vue.last_failure_msg}"
            )
        else:
            ligne["erreur"].configure(text=spec.usage_note or "")

    def _sync_artists(self, artistes):
        self._artists = artistes
        valeurs = [_TOUS] + [nom for _id, nom in artistes]
        courant = self.artist_box.get()
        self.artist_box.configure(values=valeurs)
        if courant not in valeurs:
            self.artist_box.set(_TOUS)

    def _apply_banner(self, episodes):
        if not episodes:
            self.banner.pack_forget()
            return
        dernier = episodes[-1]
        quand = dernier.start.strftime("%d/%m à %H:%M")
        self.banner.configure(
            text=(
                f"⚠ Connexion locale instable le {quand} — "
                f"{len(dernier.sources)} sources touchées simultanément : "
                "leurs échecs sont écartés du taux."
            )
        )
        self.banner.pack(fill="x", padx=16, pady=(0, 4), after=self.window.winfo_children()[0])

    def _show_failures(self, key: str):
        """Détail d'une source : ses derniers échecs, avec leur nature."""
        repo = self._repo()
        if repo is None:
            return
        echecs = repo.recent_failures(key, limit=50)
        vue = self._views.get(key)

        fenetre = ctk.CTkToplevel(self.window)
        fenetre.title(f"Derniers échecs — {key}")
        fenetre.geometry("760x520")
        fenetre.transient(self.window)

        if vue and vue.by_flow:
            ventilation = " · ".join(f"{flux} : {n}" for flux, n in sorted(vue.by_flow.items()))
            ctk.CTkLabel(fenetre, text=f"Appels par flux — {ventilation}", anchor="w").pack(
                fill="x", padx=12, pady=(10, 4)
            )

        zone = ctk.CTkScrollableFrame(fenetre, label_text="")
        zone.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        if not echecs:
            ctk.CTkLabel(zone, text="Aucun échec enregistré pour cette source.").pack(
                anchor="w", padx=8, pady=8
            )
            return
        for echec in echecs:
            quand = str(echec.get("occurred_at") or "").replace("T", " ")
            nature = _ISSUE_LABEL.get(echec.get("issue"), echec.get("issue"))
            ctk.CTkLabel(
                zone,
                text=f"{quand}  ·  {nature}  ·  {echec.get('message') or ''}",
                anchor="w",
                justify="left",
                wraplength=700,
            ).pack(anchor="w", padx=8, pady=1)

    # ── Vérification (thread) ──────────────────────────────────────────────────
    def _start_check(self, level: str):
        self._stop = False
        self.fast_button.configure(state="disabled")
        self.full_button.configure(state="disabled")
        self.status_label.configure(text=f"Vérification ({level}) en cours…")

        def worker():
            def progress(status):
                # Retour au thread principal Tk pour toucher les widgets
                self._safe_after(lambda s=status: self._apply_row(s.key, _as_dict(s)))

            statuses = check_all(
                level=level,
                progress_cb=progress,
                should_stop=lambda: self._stop or stop_requested(),
            )
            save_health(statuses)
            self._safe_after(lambda: self._on_check_done(level, statuses))

        start_worker(worker, name="source_health")

    def _on_check_done(self, level: str, statuses):
        broken = sum(1 for s in statuses if s.status == "broken")
        if self._stop:
            self.status_label.configure(text="Vérification interrompue")
        else:
            self.status_label.configure(
                text=f"Terminé ({level}) — {len(statuses)} sondée(s), {broken} cassée(s)"
            )
        # La fenêtre a pu être fermée pendant le check → widgets détruits
        try:
            self.fast_button.configure(state="normal")
            self.full_button.configure(state="normal")
        except Exception:
            pass

    def _safe_after(self, fn):
        """Planifie `fn` sur le thread Tk, en ignorant une fenêtre déjà détruite."""
        try:
            self.window.after(0, fn)
        except Exception:
            pass

    # ── Divers ─────────────────────────────────────────────────────────────────
    def _open_doc(self):
        if not _MAINTENANCE_DOC.exists():
            messagebox.showinfo("Procédure", f"Document introuvable :\n{_MAINTENANCE_DOC}")
            return
        try:
            os.startfile(_MAINTENANCE_DOC)  # noqa: S606 — ouverture d'un doc local du projet
        except Exception as e:
            messagebox.showwarning("Procédure", f"Impossible d'ouvrir le document :\n{e}")

    def _on_close(self):
        # Demande l'arrêt coopératif du worker éventuel (testé entre deux sources)
        self._stop = True
        self.window.destroy()
        if getattr(self.app, "source_health_window", None) is self:
            self.app.source_health_window = None


def _as_dict(status) -> dict:
    """SourceStatus → dict pour _apply_row (évite d'importer asdict côté GUI)."""
    return {
        "status": status.status,
        "level": status.level,
        "latency_ms": status.latency_ms,
        "last_checked": status.last_checked,
        "message": status.message,
    }


def show_source_health(app):
    """Ouvre (ou refocus) la fenêtre État des sources."""
    existing = getattr(app, "source_health_window", None)
    if existing is not None:
        try:
            existing.window.deiconify()
            existing.window.lift()
            existing.window.focus_force()
            existing.refresh_usage()
            return existing
        except Exception:
            pass  # fenêtre détruite entre-temps → on en recrée une
    app.source_health_window = SourceHealthWindow(app)
    return app.source_health_window
