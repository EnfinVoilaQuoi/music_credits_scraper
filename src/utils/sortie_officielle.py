"""Une page Genius « Non-Music » est-elle SORTIE officiellement ?

Décision utilisateur du 2026-11-10 : parmi les pages « Non-Music », on ne garde
que ce qui est sorti officiellement — un skit ou un interlude parlé d'un album,
mais aussi un vrai morceau que Genius range à tort en Non-Music (« Pree Me »
de Booba, « Day and Night » de Kid Cudi) ou une piste parlée qui ne s'appelle
ni skit ni interlude (« Siiiiiiiiilver Surffffeeeeer Intermission »). La
vidéo ne prouve rien : les skits des « Snippet Tape » d'Eminem en ont une.

Preuves, mesurées sur la copie du 2026-11-10 (100 pages Non-Music) :
- un identifiant Spotify, Deezer ou Apple de la fiche, déjà jugé à la pose
  (16 pages) ;
- son titre dans la liste Deezer de son album, lue au brut (2 de plus :
  « Paul (Skit) » de 2009 sur *Relapse*, « Interlude Fatima »). Le titre
  EXACT (`titres_equivalents`) : le titre de base ferait tomber les pages
  « (Pochette) » de La Fouine sur le morceau-titre de leur album.
L'année qu'ajoute Genius (« Paul (Skit) [2009] ») ne compte pas, la partie
d'un titre joint (« Encore / Curtains Up ») vaut le titre. Manqué : « King
Mathers (Intro) » (*Relapse: Refill*, absent de la liste Deezer).
"""

from __future__ import annotations

import json
import sqlite3
import zlib
from contextlib import closing
from pathlib import Path

from src.utils.version_descriptors import meme_disque, titres_equivalents


def page_non_music(track) -> bool:
    """L'URL Genius `-annotated` : le tag « Non-Music »."""
    return (track.genius_url or "").endswith("-annotated")


def a_un_identifiant(track) -> bool:
    """Un identifiant de plateforme, jugé par son gate à la pose."""
    return bool(track.spotify_id or track.deezer_id or track.apple_music_id)


def dans_la_liste(titre: str | None, liste: list[str]) -> bool:
    """PUR. Le titre de la fiche est-il une piste de la liste (titre exact,
    à la graphie près) ? Genius ajoute parfois l'année d'un homonyme
    (« Paul (Skit) [2009] », celui de *Relapse*) : elle ne compte pas
    (`titre_annee.base_et_annee`). Un titre JOINT dont une partie est une
    piste du disque en est une (« Encore / Curtains Up » : « Curtains Up »)."""
    from src.utils.contenants import parties_du_titre
    from src.utils.titre_annee import base_et_annee

    if not titre:
        return False
    base, annee = base_et_annee(titre)
    formes = [titre] + ([base] if annee else []) + list(parties_du_titre(titre))
    return any(titres_equivalents(p, f) for p in liste for f in formes)


def liste_deezer(album_id, chemin_brut: Path | None = None) -> list[str] | None:
    """Les titres de la DERNIÈRE liste Deezer gardée de l'album (fiche
    d'album ou `/tracks`), EN LECTURE SEULE ; None si le brut ne l'a pas."""
    from src.persistence import brut

    chemin = Path(chemin_brut or brut.CHEMIN)
    if not chemin.exists():
        return None
    base = f"https://api.deezer.com/album/{album_id}"
    with closing(sqlite3.connect(f"file:{chemin.as_posix()}?mode=ro", uri=True)) as conn:
        lignes = conn.execute(
            "SELECT requete, corps FROM brut WHERE source = 'deezer' "
            "AND (requete = ? OR requete LIKE ?) ORDER BY derniere_lecture",
            (base, f"{base}/tracks%"),
        ).fetchall()
    titres = None
    for requete, corps in lignes:
        try:
            d = json.loads(zlib.decompress(corps))
        except (zlib.error, ValueError):
            continue
        if not isinstance(d, dict) or "error" in d:
            continue
        data = d.get("data") if "/tracks" in requete else (d.get("tracks") or {}).get("data")
        if isinstance(data, list):
            titres = [p.get("title") for p in data if isinstance(p, dict) and p.get("title")]
    return titres


def sorties_par_leur_disque(conn, fiches, chemin_brut: Path | None = None) -> set[int]:
    """Les fiches « Non-Music » sans identifiant dont le titre est dans la
    liste Deezer de leur album (une parution de leur artiste au même nom de
    disque, `meme_disque`). Lecture seule ; ne lit le brut que pour elles."""
    from sqlalchemy import text

    candidates = [
        t for t in fiches if page_non_music(t) and not a_un_identifiant(t) and t.album and t.id
    ]
    if not candidates:
        return set()
    artistes = {t.artist.id for t in candidates if t.artist and t.artist.id}
    if not artistes:
        return set()
    parutions: dict[int, list[tuple[str, str]]] = {}
    for aid, titre, dz in conn.execute(
        text(
            "SELECT artist_id, title, deezer_album_id FROM releases "
            f"WHERE deezer_album_id IS NOT NULL AND artist_id IN ({','.join(map(str, artistes))})"
        )
    ):
        parutions.setdefault(aid, []).append((titre, str(dz)))
    listes: dict[str, list[str] | None] = {}
    sorties = set()
    for t in candidates:
        for titre_disque, dz in parutions.get(t.artist.id if t.artist else None, []):
            if not meme_disque(t.album, titre_disque):
                continue
            if dz not in listes:
                listes[dz] = liste_deezer(dz, chemin_brut)
            if dans_la_liste(t.title, listes[dz] or []):
                sorties.add(t.id)
                break
    return sorties
