"""Reposer sur une fiche la page Genius qu'elle décrit (e36, 2026-09-24).

Avant e36, des morceaux Genius différents au même titre fusionnaient en une
fiche : elle a pu garder le genius_id d'un morceau et l'album, l'artiste
principal, les crédits ou les paroles d'un autre. `reposer_page` réaligne la
fiche sur UNE page — la relation à l'artiste tranchée par le détail Genius
(`_verify_artist_credit`, la vérification de l'import) — et efface ce qui venait
des pages mélangées (`TrackRepository.rattacher_page_genius`).

Partagé par `scripts/repair_chimeres_genius.py` (fiche au MAUVAIS genius_id) et
`scripts/repair_relation_artiste.py` (bon genius_id, champs d'un homonyme).
"""

from __future__ import annotations


def reposer_page(dm, genius, track_id: int, artiste_genius_id, chanson: dict) -> str:
    """Pose `chanson` (réponse `GET /songs/{id}`) sur la fiche. Rend un statut."""
    relation = genius._verify_artist_credit(chanson["id"], artiste_genius_id)
    if relation is None:
        return "SAUTÉ : l'artiste n'est pas crédité au détail de la page"
    kind, role = relation
    principal = (chanson.get("primary_artist") or {}).get("name")
    date = genius.precision_de_la_date(chanson)
    if date is None:
        brute = genius._extract_release_date_from_song(chanson)
        date = brute.strftime("%Y-%m-%d") if brute else None
    ok = dm.rattacher_page_genius(
        track_id,
        genius_id=chanson["id"],
        genius_url=chanson.get("url"),
        album=genius._extract_album_from_song(chanson),
        date_observee=date,
        is_featuring=kind != "primary",
        primary_artist_name=None if kind == "primary" else principal,
        secondary_role=role if kind == "secondary" else None,
    )
    return f"corrigé ({kind}{' ' + role if role else ''})" if ok else "REFUSÉ (voir log)"
