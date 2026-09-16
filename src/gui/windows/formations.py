"""Fenêtre « Groupes / formations » — arbitrer à la main qui joue avec qui.

Depuis le 2026-09-16 la fenêtre est un lieu d'ARBITRAGE : à l'ouverture elle
lit la base (zéro réseau) et montre les liens par statut — proposés par
l'enrichissement (MusicBrainz + Discogs, en fin de run), confirmés, refusés,
et « pour info » (alias d'un type non proposable : état civil, indice de
recherche, variante de graphie). Le bouton « Rechercher » relance les deux
sources à la demande et ajoute ce qu'elles trouvent de neuf aux proposés.

Rien ne se confirme sans un clic. Un rapprochement de noms n'a pas le droit de
décider seul de l'appartenance de quelqu'un — une erreur ici ne se voit pas,
elle produit une discographie crédible et fausse. Et REFUSER est une mémoire :
un run ne repropose jamais un lien refusé.

Deux choix par ligne, distincts :

  · **le statut** — proposé / confirmé / refusé ;
  · **groupe ou collectif**, qui décide de la LECTURE. Un groupe apporte tous
    ses morceaux au membre ; un collectif seulement ceux où il est présent
    (écriture, production, performance). La nature déjà choisie pour une
    formation est pré-remplie — elle appartient à la formation, pas au lien.

La page d'un groupe n'absorbe pas le solo de ses membres : le panneau
« Membres » sert à naviguer vers eux (décision utilisateur du 2026-09-08).

Threads : `start_worker` (contrat `lifecycle.py`) — la recherche est du réseau
pur, à 1 req/s côté MusicBrainz, elle ne doit pas figer l'interface.
"""

import customtkinter as ctk

from src.concurrency.lifecycle import start_worker
from src.utils.formations import (
    candidats_de_base,
    chercher_formations,
    reunir,
    trier_decisions,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)

#: Libellés des natures de lien, tels qu'affichés.
_LIBELLE_KIND = {
    "member_of": "est membre de",
    "has_member": "a pour membre",
    "alias": "aussi connu comme",
}

_NATURES = ("groupe", "collectif")

#: Statuts arbitrables, dans l'ordre du sélecteur, et leurs libellés.
_STATUTS = (("proposed", "Proposé"), ("confirmed", "Confirmé"), ("refused", "Refusé"))
_LIBELLE_STATUT = dict(_STATUTS)
_STATUT_PAR_LIBELLE = {lib: st for st, lib in _STATUTS}

#: Sections de la fenêtre : (statut, titre, couleur).
_SECTIONS = (
    ("proposed", "⏳ Proposés — à arbitrer", "#e6a700"),
    ("confirmed", "✅ Confirmés", "#1DB954"),
    ("refused", "⛔ Refusés (mémoire : jamais reproposés)", "gray"),
    ("info", "ℹ️ Pour info — jamais utilisés (état civil, indices, graphies)", "gray"),
)


class FormationsWindow:
    """Fenêtre CTkToplevel « Groupes / formations » (une instance à la fois)."""

    def __init__(self, app):
        self.app = app
        self.artist = app.current_artist
        self._lignes: list[dict] = []
        self._candidats: list = []

        self.window = ctk.CTkToplevel(app.root)
        self.window.title(f"Groupes / formations — {self.artist.name}")
        self.window.geometry("960x680")
        self.window.transient(app.root)

        self._build_entete()
        self.corps = ctk.CTkScrollableFrame(self.window)
        self.corps.pack(fill="both", expand=True, padx=12, pady=6)
        self._build_pied()

        # Ouverture = LECTURE de la base, instantanée. Le réseau, c'est le bouton.
        self.afficher_base()

    # ── Construction ─────────────────────────────────────────────────────────

    def _build_entete(self):
        entete = ctk.CTkFrame(self.window)
        entete.pack(fill="x", padx=12, pady=(12, 6))
        ctk.CTkLabel(
            entete,
            text=f"Formations et alias de « {self.artist.name} »",
            font=ctk.CTkFont(size=15, weight="bold"),
        ).pack(anchor="w", padx=10, pady=(8, 2))
        self.statut = ctk.CTkLabel(entete, text="", text_color="gray", justify="left")
        self.statut.pack(anchor="w", padx=10, pady=(0, 8))

    def _build_pied(self):
        pied = ctk.CTkFrame(self.window, fg_color="transparent")
        pied.pack(fill="x", padx=12, pady=(0, 12))
        self.bouton_enregistrer = ctk.CTkButton(
            pied, text="✅ Enregistrer les décisions", command=self.enregistrer, width=220
        )
        self.bouton_enregistrer.pack(side="left", padx=4)
        self.bouton_chercher = ctk.CTkButton(
            pied,
            text="🔄 Rechercher (MusicBrainz + Discogs)",
            command=self.chercher,
            width=260,
            fg_color="gray30",
            hover_color="gray40",
        )
        self.bouton_chercher.pack(side="left", padx=4)
        ctk.CTkButton(
            pied,
            text="Fermer",
            command=self.window.destroy,
            width=100,
            fg_color="gray30",
            hover_color="gray40",
        ).pack(side="right", padx=4)

    # ── Données ──────────────────────────────────────────────────────────────

    def afficher_base(self):
        """La mémoire de la base, tous statuts, sans réseau."""
        relations = self.app.data_manager.get_artist_relations(self.artist.id, status=None)
        self._candidats = candidats_de_base(relations)
        n_prop = sum(1 for c in self._candidats if c.status == "proposed")
        self.statut.configure(
            text=(
                f"{len(self._candidats)} lien(s) en base — {n_prop} à arbitrer. "
                "Les propositions viennent de l'enrichissement (fin de run) ; "
                "« Rechercher » interroge à nouveau les deux sources."
            ),
            text_color="#e6a700" if n_prop else "gray",
        )
        self._afficher()

    # ── Recherche ────────────────────────────────────────────────────────────

    def chercher(self):
        """Interroge les deux sources en fond et AJOUTE le neuf. N'écrit rien."""
        self.statut.configure(
            text="Recherche en cours (MusicBrainz est limité à 1 requête/seconde)…"
        )
        self.bouton_enregistrer.configure(state="disabled")
        self.bouton_chercher.configure(state="disabled")

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
            self.app.root.after(0, lambda: self._afficher_rapport(rapport))

        start_worker(worker, name="formations")

    def _echec(self, message: str):
        self.statut.configure(text=f"❌ {message}", text_color="#c62828")
        self.bouton_enregistrer.configure(state="normal")
        self.bouton_chercher.configure(state="normal")

    def _afficher_rapport(self, rapport):
        base = candidats_de_base(
            self.app.data_manager.get_artist_relations(self.artist.id, status=None)
        )
        self._candidats = reunir(base, rapport.candidats)
        lignes_statut = []
        if rapport.identite_mb:
            lignes_statut.append(f"MusicBrainz : {rapport.identite_mb}")
        nouveaux = sum(1 for c in self._candidats if c.status is None)
        lignes_statut.append(
            f"{nouveaux} nouveau(x) candidat(s) — non écrits tant que tu n'enregistres pas."
        )
        lignes_statut += rapport.diagnostics
        self.statut.configure(
            text="\n".join(lignes_statut),
            text_color="#e6a700" if rapport.diagnostics else "gray",
        )
        self._afficher()
        self.bouton_chercher.configure(state="normal")

    # ── Affichage ────────────────────────────────────────────────────────────

    def _afficher(self):
        for enfant in self.corps.winfo_children():
            enfant.destroy()
        self._lignes = []

        if not self._candidats:
            ctk.CTkLabel(
                self.corps,
                text="Aucun lien en base.\n"
                "Lance un enrichissement (case MusicBrainz) ou « Rechercher » ci-dessous.\n"
                "Les deux sources sont communautaires : une absence n'est pas une preuve.",
                text_color="gray",
                justify="left",
            ).pack(anchor="w", padx=10, pady=20)
            self.bouton_enregistrer.configure(state="normal")
            return

        # Un candidat neuf (jamais en base) s'arbitre avec les proposés.
        def section_de(c):
            return "info" if c.status == "info" else (c.status or "proposed")

        for statut, titre, couleur in _SECTIONS:
            groupe = [c for c in self._candidats if section_de(c) == statut]
            if not groupe:
                continue
            ctk.CTkLabel(
                self.corps, text=titre, text_color=couleur, font=ctk.CTkFont(weight="bold")
            ).pack(anchor="w", padx=6, pady=(10, 2))
            for candidat in groupe:
                self._ligne(candidat, lecture_seule=statut == "info")

        self.bouton_enregistrer.configure(state="normal")

    def _ligne(self, candidat, *, lecture_seule: bool):
        cadre = ctk.CTkFrame(self.corps)
        cadre.pack(fill="x", padx=6, pady=3)

        libelle = f"{_LIBELLE_KIND.get(candidat.kind, candidat.kind)}  «{candidat.related_name}»"
        if candidat.detail:
            libelle += f"  ({candidat.detail})"
        ctk.CTkLabel(cadre, text=libelle, anchor="w", width=340).pack(side="left", padx=(10, 4))

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
        ctk.CTkLabel(cadre, text=periode, width=100, text_color="gray").pack(side="left", padx=4)

        if lecture_seule:
            ctk.CTkLabel(cadre, text="pour info", width=200, text_color="gray").pack(
                side="left", padx=4
            )
            return

        statut = ctk.StringVar(value=_LIBELLE_STATUT[candidat.status or "proposed"])
        ctk.CTkSegmentedButton(
            cadre, values=[lib for _st, lib in _STATUTS], variable=statut, width=250
        ).pack(side="left", padx=4)

        nature = ctk.StringVar(value=candidat.formation or "groupe")
        if candidat.kind == "alias":
            # La question groupe/collectif ne se pose pas pour un autre nom de scène.
            ctk.CTkLabel(cadre, text="—", width=120, text_color="gray").pack(side="left", padx=4)
        else:
            ctk.CTkSegmentedButton(cadre, values=list(_NATURES), variable=nature, width=190).pack(
                side="left", padx=4
            )

        self._lignes.append({"candidat": candidat, "statut": statut, "nature": nature})

    # ── Enregistrement ───────────────────────────────────────────────────────

    def enregistrer(self):
        """Applique les décisions : confirmer, refuser, ou garder proposé.

        Refuser n'efface rien — c'est une MÉMOIRE, l'enrichissement ne
        reproposera pas ce lien. La décision est une fonction pure et testée
        (`trier_decisions`) : la fenêtre ne fait que la relayer.
        """
        dm = self.app.data_manager
        decisions = trier_decisions(
            (
                ligne["candidat"],
                _STATUT_PAR_LIBELLE[ligne["statut"].get()],
                ligne["nature"].get(),
            )
            for ligne in self._lignes
        )
        confirmes = refuses = proposes = 0
        for candidat, statut, nature in decisions:
            rel = candidat.vers_relation(nature)
            if statut == "confirmed":
                confirmes += dm.record_artist_relations(self.artist.id, [rel])
            elif statut == "refused":
                if candidat.status is None:
                    dm.propose_artist_relations(self.artist.id, [rel])
                refuses += int(
                    dm.set_relation_status(self.artist.id, rel.related_name, rel.kind, "refused")
                )
            else:  # proposed : un candidat neuf, gardé en mémoire sans le trancher
                if candidat.status is None:
                    proposes += dm.propose_artist_relations(self.artist.id, [rel])
                else:
                    dm.set_relation_status(self.artist.id, rel.related_name, rel.kind, "proposed")
        logger.info(
            f"Formations « {self.artist.name} » : {confirmes} confirmé(s), "
            f"{refuses} refusé(s), {proposes} proposé(s) gardé(s)"
        )
        self.statut.configure(
            text=f"✅ {confirmes} confirmé(s), {refuses} refusé(s), {proposes} proposé(s) gardé(s).",
            text_color="#1DB954",
        )
        self.afficher_base()
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
            existing.afficher_base()
            return existing
        except Exception:  # noqa: BLE001 — fenêtre détruite entre-temps
            pass
    app.formations_window = FormationsWindow(app)
    return app.formations_window
