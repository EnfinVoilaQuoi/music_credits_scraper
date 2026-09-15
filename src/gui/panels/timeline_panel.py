"""Onglet « Timeline » d'Export studio : cocher les 16 projets, éditer, mémoriser.

Couche mince sur `src.dataviz.timeline` : la liste des candidats vient de
`build_candidates` (tous, sans plafond), la sélection par défaut de
`default_selection`, la mémorisation de `timeline_overrides_io`. Ici on ne fait
que poser des cases à cocher et des champs — et compter.

Une rangée par candidat : case, date, ligne 1 (Light, `**gras**` inline), ligne
2, taille grand/petit, côté des disques (actif seulement si le projet est
certifié), streams. Un morceau DÉSACTIVÉ reste proposable — un Grünt est un
point de carrière, pas un morceau qui compte — marqué ⛔ et hors cumul. Le champ
« ➕ Ajouter » va chercher un titre hors des 30 proposés (un Colors ancien dont
le titre ne dit rien). Le switch d'ordre re-`grid` les rangées sans les
recréer : les saisies survivent. Une clé mémorisée qui ne correspond plus à
aucun candidat (album renommé, morceau supprimé) est affichée grisée et
décochée — l'utilisateur voit ce qu'il a perdu au lieu d'un export amputé.
"""

import os
from dataclasses import dataclass
from tkinter import messagebox

import customtkinter as ctk

from src.concurrency.lifecycle import start_worker
from src.dataviz.timeline import (
    DISC_SIDES,
    PAGE_SIZE,
    PAGES_FULL,
    PAGES_SMALL,
    Candidate,
    EntryChoice,
    build_candidates,
    default_page_count,
    default_selection,
    generate_timeline,
    generate_timeline_preview,
    sort_candidates,
)
from src.dataviz.timeline_overrides_io import (
    entries_from_override,
    get_override,
    load_overrides,
    pages_from_override,
    save_override,
)
from src.dataviz.timeline_style_io import load_style
from src.dataviz.timeline_svg import SIZES, format_streams_short
from src.utils.logger import get_logger

logger = get_logger(__name__)

_PAGES_LABELS = {PAGES_FULL: "4 pages (16)", PAGES_SMALL: "3 pages (12)"}
_BG_NONE = "—"
_ORDER_PROPOSED = "Proposés"
_ORDER_DATE = "Par date"


@dataclass
class _Row:
    candidate: Candidate | None  # None = clé mémorisée introuvable
    key: str
    checked: ctk.BooleanVar
    line1: ctk.StringVar
    line2: ctk.StringVar
    size: ctk.StringVar
    disc_side: ctk.StringVar
    background: ctk.StringVar  # « — » ou « 1 »…« 4 »
    frame: ctk.CTkFrame


class TimelinePanel:
    """Le contenu de l'onglet. `app` = MainWindow (artiste courant, désactivés)."""

    def __init__(self, parent, app, *, safe_after, media_command=None):
        self.app = app
        self._safe_after = safe_after
        self._media_command = media_command
        self.rows: dict[str, _Row] = {}
        self._candidates: list[Candidate] = []
        self._artist_name: str | None = None
        self._build(parent)

    # ── Construction ───────────────────────────────────────────────────────────
    def _build(self, parent):
        head = ctk.CTkFrame(parent, fg_color="transparent")
        head.pack(fill="x", padx=10, pady=(10, 4))
        self.title_label = ctk.CTkLabel(head, text="Candidats", font=("Arial", 13, "bold"))
        self.title_label.pack(side="left")
        self.counter_label = ctk.CTkLabel(head, text="0/16", font=("Arial", 13, "bold"))
        self.counter_label.pack(side="right", padx=(8, 0))
        self.pages_switch = ctk.CTkSegmentedButton(
            head,
            values=list(_PAGES_LABELS.values()),
            command=lambda _v: self._update_counter(),
            width=220,
        )
        self.pages_switch.set(_PAGES_LABELS[PAGES_FULL])
        self.pages_switch.pack(side="right", padx=8)
        self.order_switch = ctk.CTkSegmentedButton(
            head,
            values=[_ORDER_PROPOSED, _ORDER_DATE],
            command=lambda _v: self._regrid(),
            width=180,
        )
        self.order_switch.set(_ORDER_PROPOSED)
        self.order_switch.pack(side="right", padx=8)

        legend = ctk.CTkLabel(
            parent,
            text="Ligne 1 en Light, ligne 2 en SemiBold ; **mot** = SemiBold, \\n = retour à la ligne. "
            "Disque : côté des disques de certif (auto = vers l'intérieur). "
            "Fond : la page dont ce projet fournit la pochette de fond (— = automatique).",
            text_color="gray",
            anchor="w",
        )
        legend.pack(fill="x", padx=12)

        add_row = ctk.CTkFrame(parent, fg_color="transparent")
        add_row.pack(fill="x", padx=10, pady=(2, 0))
        self.search_var = ctk.StringVar(value="")
        entry = ctk.CTkEntry(
            add_row, textvariable=self.search_var, width=260, placeholder_text="Titre à ajouter…"
        )
        entry.pack(side="left")
        entry.bind("<Return>", lambda _e: self.add_by_search())
        ctk.CTkButton(add_row, text="➕ Ajouter", width=100, command=self.add_by_search).pack(
            side="left", padx=(6, 0)
        )

        self.list_frame = ctk.CTkScrollableFrame(parent)
        self.list_frame.pack(fill="both", expand=True, padx=10, pady=6)
        self.list_frame.grid_columnconfigure(2, weight=1)
        self.list_frame.grid_columnconfigure(3, weight=1)

        # Séparateurs entre les trois blocs — PROJETS (albums, rééditions),
        # MORCEAUX, FREESTYLES — posés par `_regrid` en ordre « proposés » seulement.
        self.separator = ctk.CTkFrame(self.list_frame, height=2, fg_color="gray50")
        self.separator_freestyle = ctk.CTkFrame(self.list_frame, height=2, fg_color="gray50")

        # Étapes intermédiaires : mémoriser, juger.
        steps = ctk.CTkFrame(parent, fg_color="transparent")
        steps.pack(fill="x", padx=10, pady=(0, 4))
        self.preview_button = ctk.CTkButton(
            steps, text="👁 Aperçu", width=110, command=self.preview, state="disabled"
        )
        self.preview_button.pack(side="right")
        self.save_button = ctk.CTkButton(steps, text="💾 Mémoriser", width=120, command=self.save)
        self.save_button.pack(side="right", padx=(0, 8))

        self.status_label = ctk.CTkLabel(parent, text="", text_color="gray", anchor="w")
        self.status_label.pack(fill="x", padx=12, pady=(0, 4))

        # Tout en bas : ce qui PRODUIT — images, puis l'export pour Illustrator.
        foot = ctk.CTkFrame(parent, fg_color="transparent")
        foot.pack(fill="x", padx=10, pady=(0, 8))
        self.open_after_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(foot, text="Ouvrir le SVG après export", variable=self.open_after_var).pack(
            side="left"
        )
        self.generate_button = ctk.CTkButton(
            foot, text="Export Illustrator", width=150, command=self.start, state="disabled"
        )
        self.generate_button.pack(side="right")
        self.media_button = ctk.CTkButton(
            foot, text="Télécharger les images…", width=180, command=self._media_command
        )
        self.media_button.pack(side="right", padx=(0, 8))

    # ── Données ────────────────────────────────────────────────────────────────
    def _tracks(self):
        """(artiste, TOUS ses morceaux, ids désactivés) — les désactivés restent
        proposables mais sortent du cumul."""
        artist = getattr(self.app, "current_artist", None)
        if artist is None or not artist.tracks:
            return None, [], frozenset()
        disabled = frozenset(t.id for t in artist.tracks if self.app._is_track_disabled(t))
        return artist, list(artist.tracks), disabled

    def refresh(self):
        """(Re)construit la liste depuis l'artiste courant (appelé au refocus)."""
        artist, tracks, disabled = self._tracks()
        for row in self.rows.values():
            row.frame.destroy()
        self.rows.clear()
        if artist is None:
            self._artist_name = None
            self._candidates = []
            self.title_label.configure(text="Candidats — aucun artiste chargé")
            self._update_counter()
            return

        self._artist_name = artist.name
        self._candidates = build_candidates(tracks, artist.name, disabled)
        by_key = {c.key: c for c in self._candidates}
        override = get_override(load_overrides(), artist.name)
        saved = entries_from_override(override)
        pages = pages_from_override(override) or default_page_count(self._candidates)
        self.pages_switch.set(_PAGES_LABELS[pages])

        if saved:
            chosen = {e.key: e for e in saved}
        else:
            chosen = {
                key: EntryChoice(
                    key,
                    by_key[key].line1_default,
                    by_key[key].line2_default,
                    by_key[key].size_default,
                )
                for key in default_selection(self._candidates, pages * PAGE_SIZE)
            }

        for cand in self._candidates:
            self._add_row(cand, chosen.get(cand.key))
        for key, entry in chosen.items():
            if key not in by_key:
                self._add_missing_row(key, entry)

        self.title_label.configure(
            text=f"Candidats — {artist.name} ({len(self._candidates)})"
            + (" — sélection mémorisée" if saved else " — sélection par défaut")
        )
        self._regrid()
        self._update_counter()

    def _add_row(self, cand: Candidate, entry: EntryChoice | None):
        frame = ctk.CTkFrame(self.list_frame, fg_color="transparent")
        checked = ctk.BooleanVar(value=entry is not None)
        line1 = ctk.StringVar(value=entry.line1 if entry else cand.line1_default)
        line2 = ctk.StringVar(value=entry.line2 if entry else cand.line2_default)
        size = ctk.StringVar(value=entry.size if entry else cand.size_default)
        side = ctk.StringVar(value=entry.disc_side if entry else "auto")
        background = ctk.StringVar(
            value=str(entry.background) if entry and entry.background else _BG_NONE
        )
        ctk.CTkCheckBox(
            frame, text="", width=24, variable=checked, command=self._update_counter
        ).grid(row=0, column=0, padx=(0, 4))
        tag = f"{cand.date}  {cand.kind[:5]:<5}" + (" ⛔" if cand.disabled else "")
        ctk.CTkLabel(frame, text=tag, width=150, anchor="w").grid(row=0, column=1, padx=2)
        ctk.CTkEntry(frame, textvariable=line1, width=230).grid(
            row=0, column=2, padx=2, sticky="ew"
        )
        ctk.CTkEntry(frame, textvariable=line2, width=230).grid(
            row=0, column=3, padx=2, sticky="ew"
        )
        ctk.CTkOptionMenu(frame, variable=size, values=list(SIZES), width=80).grid(
            row=0, column=4, padx=2
        )
        # Côté des disques de certif : « auto » = vers l'intérieur de la page.
        # Sans certification il n'y a rien à poser : grisé, ça se voit d'un coup d'œil.
        ctk.CTkOptionMenu(
            frame,
            variable=side,
            values=list(DISC_SIDES),
            width=90,
            state="normal" if cand.has_cert else "disabled",
        ).grid(row=0, column=5, padx=2)
        # Fond de page forcé : ce projet fournit la pochette de fond de la page N.
        ctk.CTkOptionMenu(
            frame,
            variable=background,
            values=[_BG_NONE] + [str(i) for i in range(1, PAGES_FULL + 1)],
            width=60,
            state="normal" if cand.cover else "disabled",
        ).grid(row=0, column=6, padx=2)
        ctk.CTkLabel(frame, text=format_streams_short(cand.streams), width=60, anchor="e").grid(
            row=0, column=7, padx=(2, 0)
        )
        frame.grid_columnconfigure(2, weight=1)
        frame.grid_columnconfigure(3, weight=1)
        self.rows[cand.key] = _Row(
            cand, cand.key, checked, line1, line2, size, side, background, frame
        )

    def _add_missing_row(self, key: str, entry: EntryChoice):
        frame = ctk.CTkFrame(self.list_frame, fg_color="transparent")
        ctk.CTkLabel(
            frame,
            text=f"⚠️ introuvable : {key} — « {entry.line2} » (album renommé ? morceau supprimé ?)",
            text_color="gray",
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        self.rows[key] = _Row(
            None,
            key,
            ctk.BooleanVar(value=False),
            ctk.StringVar(value=entry.line1),
            ctk.StringVar(value=entry.line2),
            ctk.StringVar(value=entry.size),
            ctk.StringVar(value=entry.disc_side),
            ctk.StringVar(value=str(entry.background) if entry.background else _BG_NONE),
            frame,
        )

    def _regrid(self):
        """Repose les rangées dans l'ordre demandé, sans les recréer."""
        by_date = self.order_switch.get() == _ORDER_DATE
        known = [r.candidate for r in self.rows.values() if r.candidate is not None]
        ordered = [c.key for c in sort_candidates(known, by_date=by_date)]
        ordered += [k for k, r in self.rows.items() if r.candidate is None]
        for row in self.rows.values():
            row.frame.grid_forget()
        self.separator.grid_forget()
        self.separator_freestyle.grid_forget()
        grid_row = 0
        for i, key in enumerate(ordered):
            cand = self.rows[key].candidate
            prev = self.rows[ordered[i - 1]].candidate if i else None
            # En ordre « proposés » : un trait projets → morceaux, un autre
            # morceaux → freestyles.
            if not by_date and cand is not None and prev is not None:
                if cand.kind == "track" and prev.kind != "track":
                    self.separator.grid(row=grid_row, column=0, sticky="ew", pady=6)
                    grid_row += 1
                if cand.is_freestyle and not prev.is_freestyle:
                    self.separator_freestyle.grid(row=grid_row, column=0, sticky="ew", pady=6)
                    grid_row += 1
            self.rows[key].frame.grid(row=grid_row, column=0, sticky="ew", pady=1)
            grid_row += 1
        self.list_frame.grid_columnconfigure(0, weight=1)

    def add_by_search(self):
        """Ajoute à la liste les candidats dont le titre contient le texte saisi
        (parmi ceux qui n'y sont pas déjà — tous le sont désormais, sauf après un
        ajout d'artiste en cours de session), décochés. Sert surtout à RETROUVER
        une ligne : le statut nomme ce qui correspond."""
        needle = self.search_var.get().strip().lower()
        if not needle or not self._candidates:
            return
        added = 0
        for cand in self._candidates:
            if cand.key in self.rows or needle not in cand.title.lower():
                continue
            self._add_row(cand, None)
            added += 1
        if added:
            self._regrid()
            self.status_label.configure(text=f"➕ {added} candidat(s) ajouté(s) pour « {needle} »")
            self.search_var.set("")
        else:
            self.status_label.configure(text=f"Aucun morceau daté contenant « {needle} »")

    # ── Sélection ──────────────────────────────────────────────────────────────
    def _pages(self) -> int:
        label = self.pages_switch.get()
        return next((p for p, lbl in _PAGES_LABELS.items() if lbl == label), PAGES_FULL)

    def _checked_entries(self) -> list[EntryChoice]:
        return [
            EntryChoice(
                r.key,
                r.line1.get().strip(),
                r.line2.get().strip(),
                r.size.get(),
                r.disc_side.get(),
                int(r.background.get()) if r.background.get() != _BG_NONE else 0,
            )
            for r in self.rows.values()
            if r.candidate is not None and r.checked.get()
        ]

    def _update_counter(self):
        n, want = len(self._checked_entries()), self._pages() * PAGE_SIZE
        self.counter_label.configure(
            text=f"{n}/{want}", text_color=("green" if n == want else "orange")
        )
        state = "normal" if n == want else "disabled"
        self.generate_button.configure(state=state)
        self.preview_button.configure(state=state)

    def save(self):
        if not self._artist_name:
            messagebox.showinfo("Export studio", "Chargez un artiste d'abord.")
            return
        entries = self._checked_entries()
        try:
            path = save_override(self._artist_name, entries=entries, pages=self._pages())
        except OSError as exc:
            messagebox.showerror("Export studio", f"Mémorisation impossible : {exc}")
            return
        self.status_label.configure(
            text=f"💾 {len(entries)} projet(s) mémorisé(s) pour {self._artist_name} → {path.name}"
        )

    # ── Génération ─────────────────────────────────────────────────────────────
    def set_busy(self, busy: bool):
        state = "disabled" if busy else "normal"
        self.save_button.configure(state=state)
        if busy:
            self.generate_button.configure(state="disabled")
            self.preview_button.configure(state="disabled")
        else:
            self._update_counter()

    def _run(self, generate, on_done, label: str):
        """Lance `generate(tracks, …)` sur un worker et rend le résultat à `on_done`."""
        artist, tracks, disabled = self._tracks()
        if artist is None:
            messagebox.showinfo("Export studio", "Chargez un artiste d'abord.")
            return
        entries = self._checked_entries()
        pages = self._pages()
        artist_name = artist.name
        self.set_busy(True)
        self.status_label.configure(text=f"{label} ({artist_name})…")

        def worker():
            try:
                result = generate(
                    tracks,
                    artist_name=artist_name,
                    entries=entries,
                    pages=pages,
                    style=load_style(),
                    disabled=disabled,
                )
            except ValueError as exc:  # clé disparue, mauvais compte, aucun stream
                self._safe_after(lambda msg=str(exc): self._on_error(msg))
            except Exception as exc:  # frontière thread→GUI : tout remonte en dialog
                logger.error(f"{label} : erreur inattendue : {exc}")
                self._safe_after(lambda msg=str(exc): self._on_error(msg))
            else:
                self._safe_after(lambda: on_done(result))

        start_worker(worker, name="export_studio:timeline")

    def start(self):
        """Exporte le carrousel (SVG + JSON) avec la sélection cochée."""
        open_after = self.open_after_var.get()
        self._run(generate_timeline, lambda r: self._on_done(r, open_after), "Génération Timeline")

    def preview(self):
        """Aperçu HTML de la sélection cochée, ouvert dans le navigateur."""
        self._run(generate_timeline_preview, self._on_preview_done, "Aperçu Timeline")

    def _on_preview_done(self, html_path):
        self.set_busy(False)
        self.status_label.configure(text=f"👁 Aperçu : {html_path}")
        try:
            os.startfile(html_path)  # noqa: S606 — page HTML locale générée
        except OSError as exc:
            logger.warning(f"Ouverture de l'aperçu impossible : {exc}")

    def _on_done(self, result, open_after: bool):
        warning = ""
        if result.undated_count:
            warning += f" — ⚠️ {result.undated_count} sans date (hors cumul)"
        if result.unstreamed_count:
            warning += f" — ⚠️ {result.unstreamed_count} sans stream"
        if result.missing_covers:
            warning += f" — ⚠️ {len(result.missing_covers)} pochette(s) absente(s)"
        self.set_busy(False)
        self.status_label.configure(
            text=f"✅ {result.path.name} + {result.json_path.name} — {result.page_count} page(s), "
            f"cumul {format_streams_short(result.total_cumul)}{warning}"
        )
        if open_after:
            try:
                os.startfile(result.path)  # noqa: S606 — ouverture du SVG généré
            except OSError as exc:
                logger.warning(f"Ouverture du SVG impossible : {exc}")

    def _on_error(self, message: str):
        self.set_busy(False)
        self.status_label.configure(text="❌ Échec de la génération")
        messagebox.showerror("Export studio", message)
