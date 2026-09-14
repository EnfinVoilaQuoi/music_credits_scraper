"""Fenêtre « Groupes / formations » — confirmer à la main qui joue avec qui.

Rien ne s'écrit sans un clic. MusicBrainz et Discogs proposent, l'utilisateur
tranche : un rapprochement de noms n'a pas le droit de décider seul de
l'appartenance de quelqu'un, et une erreur ici ne se voit pas — elle produit une
discographie crédible et fausse.

Deux choix par ligne, et ils sont distincts :

  · **confirmer ou non** le lien lui-même ;
  · **groupe ou collectif**, qui décide de la LECTURE. Un groupe apporte tous
    ses morceaux au membre ; un collectif seulement ceux où il est présent
    (écriture, production, performance). La nature déjà choisie pour une
    formation est pré-remplie — elle appartient à la formation, pas au lien.

La page d'un groupe, elle, n'absorbe pas le solo de ses membres : elle montre ce
que le groupe a sorti, et le panneau « Membres » sert à naviguer vers eux
(décision utilisateur du 2026-09-08). C'est le rôle des liens `has_member`.

Threads : `start_worker` (contrat `lifecycle.py`) — la recherche est du réseau
pur, à 1 req/s côté MusicBrainz, elle ne doit pas figer l'interface.
"""

import customtkinter as ctk

from src.concurrency.lifecycle import start_worker
from src.utils.formations import chercher_formations, trier_confirmations
from src.utils.logger import get_logger

logger = get_logger(__name__)

#: Libellés des natures de lien, tels qu'affichés.
_LIBELLE_KIND = {
    "member_of": "est membre de",
    "has_member": "a pour membre",
    "alias": "aussi connu comme",
}

_NATURES = ("groupe", "collectif")


class FormationsWindow:
    """Fenêtre CTkToplevel « Groupes / formations » (une instance à la fois)."""

    def __init__(self, app):
        self.app = app
        self.artist = app.current_artist
        self._lignes: list[dict] = []

        self.window = ctk.CTkToplevel(app.root)
        self.window.title(f"Groupes / formations — {self.artist.name}")
        self.window.geometry("880x620")
        self.window.transient(app.root)

        self._build_entete()
        self.corps = ctk.CTkScrollableFrame(self.window)
        self.corps.pack(fill="both", expand=True, padx=12, pady=6)
        self._build_pied()

        self.chercher()

    # ── Construction ─────────────────────────────────────────────────────────

    def _build_entete(self):
        entete = ctk.CTkFrame(self.window)
        entete.pack(fill="x", padx=12, pady=(12, 6))
        ctk.CTkLabel(
            entete,
            text=f"Formations de « {self.artist.name} »",
            font=ctk.CTkFont(size=15, weight="bold"),
        ).pack(anchor="w", padx=10, pady=(8, 2))
        self.statut = ctk.CTkLabel(entete, text="", text_color="gray", justify="left")
        self.statut.pack(anchor="w", padx=10, pady=(0, 8))

    def _build_pied(self):
        pied = ctk.CTkFrame(self.window, fg_color="transparent")
        pied.pack(fill="x", padx=12, pady=(0, 12))
        self.bouton_enregistrer = ctk.CTkButton(
            pied, text="✅ Enregistrer les liens cochés", command=self.enregistrer, width=220
        )
        self.bouton_enregistrer.pack(side="left", padx=4)
        ctk.CTkButton(
            pied,
            text="🔄 Rechercher à nouveau",
            command=self.chercher,
            width=180,
            fg_color="gray30",
            hover_color="gray40",
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            pied,
            text="Fermer",
            command=self.window.destroy,
            width=100,
            fg_color="gray30",
            hover_color="gray40",
        ).pack(side="right", padx=4)

    # ── Recherche ────────────────────────────────────────────────────────────

    def chercher(self):
        """Interroge les deux sources en fond. N'écrit rien."""
        self.statut.configure(
            text="Recherche en cours (MusicBrainz est limité à 1 requête/seconde)…"
        )
        self.bouton_enregistrer.configure(state="disabled")

        def worker():
            try:
                rapport = chercher_formations(self.artist, self.app.data_manager)
            except Exception as e:  # noqa: BLE001 — la fenêtre doit rester utilisable
                logger.exception("Recherche de formations échouée")
                # `e` est effacée à la sortie du `except` : la lambda doit la
                # capturer par défaut, sinon elle lèverait un NameError au
                # moment précis où l'on essaie d'afficher l'erreur.
                self.app.root.after(0, lambda message=str(e): self._echec(message))
                return
            self.app.root.after(0, lambda: self._afficher(rapport))

        start_worker(worker, name="formations")

    def _echec(self, message: str):
        self.statut.configure(text=f"❌ {message}", text_color="#c62828")
        self.bouton_enregistrer.configure(state="normal")

    def _afficher(self, rapport):
        for enfant in self.corps.winfo_children():
            enfant.destroy()
        self._lignes = []

        lignes_statut = []
        if rapport.identite_mb:
            lignes_statut.append(f"MusicBrainz : {rapport.identite_mb}")
        lignes_statut += rapport.diagnostics
        self.statut.configure(
            text="\n".join(lignes_statut) or "—",
            text_color="#e6a700" if rapport.diagnostics else "gray",
        )

        if not rapport.candidats:
            ctk.CTkLabel(
                self.corps,
                text="Aucune formation proposée.\n"
                "Les deux sources sont communautaires : une absence n'est pas une preuve.",
                text_color="gray",
                justify="left",
            ).pack(anchor="w", padx=10, pady=20)
        for candidat in rapport.candidats:
            self._ligne(candidat)

        self.bouton_enregistrer.configure(state="normal")

    def _ligne(self, candidat):
        cadre = ctk.CTkFrame(self.corps)
        cadre.pack(fill="x", padx=6, pady=3)

        cocher = ctk.BooleanVar(value=candidat.deja_confirme or candidat.croise)
        ctk.CTkCheckBox(cadre, text="", variable=cocher, width=28).pack(side="left", padx=(8, 2))

        libelle = f"{_LIBELLE_KIND.get(candidat.kind, candidat.kind)}  «{candidat.related_name}»"
        ctk.CTkLabel(cadre, text=libelle, anchor="w", width=300).pack(side="left", padx=4)

        # Le croisement des deux sources est l'information la plus utile : elle
        # se lit d'un coup d'œil, pas dans une infobulle.
        marque = "✔ deux sources" if candidat.croise else " · ".join(sorted(candidat.sources))
        ctk.CTkLabel(
            cadre,
            text=marque,
            width=130,
            text_color="#1DB954" if candidat.croise else "gray",
        ).pack(side="left", padx=4)

        periode = " → ".join(x for x in (candidat.begin_date, candidat.end_date) if x)
        ctk.CTkLabel(cadre, text=periode, width=110, text_color="gray").pack(side="left", padx=4)

        nature = ctk.StringVar(value=candidat.formation or "groupe")
        if candidat.kind == "alias":
            # La question groupe/collectif ne se pose pas pour un autre nom de scène.
            ctk.CTkLabel(cadre, text="—", width=120, text_color="gray").pack(side="left", padx=4)
        else:
            ctk.CTkSegmentedButton(cadre, values=list(_NATURES), variable=nature, width=190).pack(
                side="left", padx=4
            )

        if candidat.deja_confirme:
            ctk.CTkLabel(cadre, text="déjà en base", text_color="#1DB954", width=90).pack(
                side="left", padx=4
            )

        self._lignes.append({"candidat": candidat, "cocher": cocher, "nature": nature})

    # ── Enregistrement ───────────────────────────────────────────────────────

    def enregistrer(self):
        """Écrit les liens cochés, RETIRE ceux qu'on vient de décocher.

        Décocher un lien déjà en base est le geste de retrait : sans lui, la
        fenêtre ne saurait qu'ajouter, et une erreur de confirmation serait
        définitive.
        """
        dm = self.app.data_manager
        # La DÉCISION est une fonction pure et testée (`trier_confirmations`) :
        # c'est ici qu'une erreur coûterait, la fenêtre ne fait que la relayer.
        a_ecrire, a_oublier = trier_confirmations(
            (ligne["candidat"], ligne["cocher"].get(), ligne["nature"].get())
            for ligne in self._lignes
        )
        for nom, kind in a_oublier:
            dm.forget_artist_relation(self.artist.id, nom, kind)
        retires = len(a_oublier)

        ecrits = dm.record_artist_relations(self.artist.id, a_ecrire) if a_ecrire else 0
        logger.info(
            f"Formations « {self.artist.name} » : {ecrits} lien(s) enregistré(s), "
            f"{retires} retiré(s)"
        )
        self.statut.configure(
            text=f"✅ {ecrits} lien(s) enregistré(s), {retires} retiré(s).",
            text_color="#1DB954",
        )
        # La discographie réunie change : la vue doit repartir de la base.
        self.app._reload_tracks_and_refresh()


def show_formations(app):
    """Ouvre (ou refocus) la fenêtre Groupes / formations."""
    if not app.current_artist:
        return None
    existing = getattr(app, "formations_window", None)
    if existing is not None and existing.artist is app.current_artist:
        try:
            existing.window.deiconify()
            existing.window.lift()
            existing.window.focus_force()
            return existing
        except Exception:  # noqa: BLE001 — fenêtre détruite entre-temps
            pass
    app.formations_window = FormationsWindow(app)
    return app.formations_window
