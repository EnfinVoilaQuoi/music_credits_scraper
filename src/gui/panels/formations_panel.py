"""Panneau « Formations » — les liens confirmés d'un artiste, cliquables.

C'est la contrepartie de la décision du 2026-09-08 : la page d'un GROUPE
n'absorbe pas le solo de ses membres. Elle montre ce que le groupe a sorti, et
c'est ce panneau qui donne accès aux membres — **navigation, pas agrégation**.

Ce qui a tranché, mesuré sur la base : Swing seul pèse 126 morceaux et
38 albums, là où le catalogue propre de L'Or du Commun tient en quelques albums.
Verser l'un dans l'autre ferait de l'œuvre du groupe une minorité sur sa propre
page, et ses totaux agrégeraient ce que ses membres ont sorti seuls.

Le panneau donne donc son rôle aux liens `has_member`, qui ne réunissent aucune
discographie : ils peuplent cette barre.
"""

import customtkinter as ctk

from src.utils.logger import get_logger

logger = get_logger(__name__)

#: Comment présenter chaque nature de lien, du point de vue de l'artiste affiché.
_ENTETES = {
    "member_of": "Membre de",
    "has_member": "Membres",
    "alias": "Alias",
}

#: Les natures dans l'ordre d'affichage. `member_of` d'abord : savoir de quoi
#: l'artiste fait partie situe mieux qu'un inventaire de ses membres.
_ORDRE = ("member_of", "has_member", "alias")


def _lien_cliquable(parent, nom: str, ouvrir) -> None:
    """Un nom qui ouvre l'artiste correspondant, s'il est en base."""
    etiquette = ctk.CTkLabel(
        parent, text=nom, text_color="#4FC3F7", cursor="hand2", font=ctk.CTkFont(size=12)
    )
    etiquette.pack(side="left", padx=(0, 10))
    etiquette.bind("<Button-1>", lambda _e: ouvrir(nom))


def build(parent, app) -> None:
    """(Re)construit le panneau pour `app.current_artist`.

    Vide le cadre à chaque appel : les liens changent dès qu'on en confirme un,
    et un panneau qui garderait ses anciennes lignes mentirait sur l'état de la
    base.
    """
    for enfant in parent.winfo_children():
        enfant.destroy()

    artiste = app.current_artist
    if not artiste or not artiste.id:
        return
    try:
        relations = app.data_manager.get_artist_relations(artiste.id)
    except Exception as e:  # noqa: BLE001 — un panneau ne doit pas casser la fenêtre
        logger.warning(f"Panneau formations indisponible : {e}")
        return
    if not relations:
        return

    def ouvrir(nom: str) -> None:
        """Charge cet artiste — même chemin que « Charger existant »."""
        app.artist_entry.delete(0, "end")
        app.artist_entry.insert(0, nom)
        app._search_artist()

    for kind in _ORDRE:
        du_type = [r for r in relations if r.kind == kind]
        if not du_type:
            continue
        ligne = ctk.CTkFrame(parent, fg_color="transparent")
        ligne.pack(fill="x", anchor="w", pady=1)
        ctk.CTkLabel(
            ligne,
            text=f"{_ENTETES[kind]} : ",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="gray",
        ).pack(side="left")

        for relation in du_type:
            if relation.related_artist_id:
                _lien_cliquable(ligne, relation.related_name, ouvrir)
            else:
                # Formation connue mais absente de la base : le lien vaut quand
                # même, il n'y a simplement nulle part où aller.
                ctk.CTkLabel(
                    ligne,
                    text=f"{relation.related_name} (pas en base)",
                    text_color="gray",
                    font=ctk.CTkFont(size=12),
                ).pack(side="left", padx=(0, 10))
