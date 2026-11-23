"""Règle 4 (décision utilisateur 2026-11-22) : les paroles de repli d'une fiche
comparées aux paroles Genius des fiches des AUTRES artistes de la base.

Le détecteur `paroles_autre_fiche` du panneau ne comparait qu'à l'intérieur
d'un artiste (HORS_AUDIT du 2026-11-10 : les paroles de secours du
« Lollipop (Remix) » de Kanye West sur le « Lollipop (French Remix) » de
Booba). Un index de bigrammes de toute la base coûte ~65 s (26 429 pages,
3,06 M paires, mesuré le 2026-11-22), une requête `instr` par texte ~2 s :
trop pour l'ouverture d'un panneau. Les candidates se trouvent donc en UN
passage sur les paroles Genius des autres artistes : les pages qui ont une
LIGNE du texte telle quelle (`lignes`, minuscules ; ~1,5 s de balayage) ; les
bigrammes confirment ensuite (le juge, seuils compris, reste celui du
détecteur). Lignes sœurs (même page) et fiches du même artiste exclues : le
détecteur les juge déjà. Lecture seule.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from pathlib import Path

from src.utils.concordance_paroles import bigrammes, paroles_de_reference

#: Une ligne plus courte se retrouve partout (« yeah yeah yeah »).
LIGNE_MIN = 25
#: Au-delà, une ligne est un refrain partagé par trop de pages (un sample).
CANDIDATES_MAX = 20

_HORODATAGE = re.compile(r"^\[\d+:\d+(?:[.:]\d+)?\]\s*")


def _cle(ligne: str) -> str:
    return ligne.strip().lower().replace("’", "'")


def lignes(texte: str | None) -> set[str]:
    """PUR. Les lignes du texte assez longues pour désigner une chanson, en
    minuscules (horodatages de LRC et en-têtes de section retirés)."""
    out = set()
    for ligne in (texte or "").splitlines():
        ligne = _HORODATAGE.sub("", ligne.strip())
        if ligne.startswith("["):
            continue
        if len(cle := _cle(ligne)) >= LIGNE_MIN:
            out.add(cle)
    return out


def candidates(db_path, artist_id: int, textes: dict[int, list[tuple]]) -> dict[int, list[dict]]:
    """`{fiche: [candidate]}` pour les textes de repli `{fiche: [(libellé,
    texte, page Genius de la fiche)]}` d'un artiste : les fiches d'AUTRES
    artistes, d'une autre page, dont les paroles Genius ont une ligne du texte
    (`lignes`), avec `part` (paires du texte dans leurs paroles) et `couvert`
    (paires de leurs paroles dans le texte). La meilleure part d'abord."""
    out: dict[int, list[dict]] = {}
    par_ligne: dict[str, set] = {}
    for tid, liste in textes.items():
        for n, (_libelle, texte, _page) in enumerate(liste):
            for ligne in lignes(texte):
                par_ligne.setdefault(ligne, set()).add((tid, n))
    if not par_ligne:
        return out
    touchees: dict[tuple, list] = {}
    uri = f"file:{Path(db_path).as_posix()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as c:
        c.row_factory = sqlite3.Row
        for r in c.execute(
            "SELECT t.id, t.title, t.genius_id, t.genius_url, t.relationships, t.lyrics, "
            "t.lyrics_source, a.name AS artiste FROM tracks t "
            "JOIN artists a ON a.id = t.artist_id "
            "WHERE t.artist_id != ? AND t.genius_id IS NOT NULL AND t.lyrics IS NOT NULL",
            (artist_id,),
        ):
            # Le texte entier d'un coup (minuscules, apostrophes) : normaliser
            # ligne à ligne en Python coûtait 441 s sur la base (2026-11-22).
            texte = r["lyrics"].lower().replace("’", "'")
            communes = par_ligne.keys() & set(texte.splitlines())
            for cle in communes:
                for texte in par_ligne[cle]:
                    touchees.setdefault(texte, []).append(r)
    for (tid, n), rows in touchees.items():
        libelle, texte, page = textes[tid][n]
        rows = list({r["id"]: r for r in rows if not page or r["genius_id"] != page}.values())
        if len({r["genius_id"] for r in rows}) > CANDIDATES_MAX:
            continue
        paires = bigrammes(texte)
        for r in rows:
            ref = paroles_de_reference(r["lyrics"], r["lyrics_source"])
            les_siens = bigrammes(ref) if ref else set()
            if not paires or not les_siens:
                continue
            commun = len(les_siens & paires)
            out.setdefault(tid, []).append(
                {
                    "autre": r["id"],
                    "title": r["title"],
                    "genius_id": r["genius_id"],
                    "genius_url": r["genius_url"],
                    "relationships": r["relationships"],
                    "artiste": r["artiste"],
                    "part": commun / len(paires),
                    "couvert": commun / len(les_siens),
                    "texte": libelle,
                }
            )
    for tid in out:
        out[tid].sort(key=lambda x: (-x["part"], x["autre"]))
    return out
