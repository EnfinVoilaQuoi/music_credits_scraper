"""Crédits d'ALBUM de Genius : le bloc « Album Credits » de la page album.

`GET /albums/{id}` de l'API rend deux listes (mesuré le 2026-11-20 sur 9 albums,
*M.A.N* de Josman compris) :
- `performance_groups` : les crédits PROPRES à l'album (Artwork, Photography,
  Graphic Design, Executive Producer, A&R, Label, Mastering…), que les pages de
  morceau ne portent pas — *M.A.N* : Artwork = Dorian Feraud, Marius Gonzalez ;
- `song_performances` : l'UNION des crédits de ses morceaux, déjà en base fiche
  par fiche (605 noms sur 606 retrouvés sur les fiches des 8 albums mesurés).
  Non gardée en table : la réponse entière reste dans le brut.

Mêmes juges que les crédits de morceau : `map_role` traduit le libellé,
`valeur_vide` écarte « Deleted Artist », « Unknown », « N/A »… et
`LIBELLES_HORS_CREDITS` ce qui n'est pas un crédit.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.models.track import CreditRole
from src.utils.credit_roles import LIBELLES_HORS_CREDITS, map_role, valeur_vide

#: Issue d'une lecture (`genius_album_lectures.issue`).
LU = "lu"
ABSENT = "absent"
ECHEC = "echec"


@dataclass(frozen=True)
class CreditAlbum:
    name: str
    role: str
    role_detail: str | None
    libelle: str
    genius_artist_id: int | None = None


def credits_de_l_album(album: dict) -> list[CreditAlbum]:
    """Les crédits d'album d'une fiche `GET /albums/{id}` (clé `album`),
    dédoublonnés par (nom, rôle, détail) comme ceux des morceaux."""
    credits: list[CreditAlbum] = []
    vus: set[tuple[str, str, str | None]] = set()
    for groupe in (album or {}).get("performance_groups") or []:
        if not isinstance(groupe, dict):
            continue
        libelle = (groupe.get("label") or "").strip()
        if not libelle or libelle.lower() in LIBELLES_HORS_CREDITS:
            continue
        role = map_role(libelle)
        # Même convention que les fiches : le libellé ne double le rôle que
        # pour un `Other` ; il reste de toute façon dans `libelle`.
        detail = libelle if role == CreditRole.OTHER else None
        for art in groupe.get("artists") or []:
            if not isinstance(art, dict):
                continue
            nom = (art.get("name") or "").strip()
            if len(nom) < 2 or valeur_vide(nom):
                continue
            cle = (nom.lower(), role.value, detail)
            if cle in vus:
                continue
            vus.add(cle)
            ident = art.get("id")
            credits.append(
                CreditAlbum(
                    name=nom,
                    role=role.value,
                    role_detail=detail,
                    libelle=libelle,
                    genius_artist_id=ident if isinstance(ident, int) else None,
                )
            )
    return credits
