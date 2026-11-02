"""Lecture de la base pour le juge des pistes Discogs (`utils.discogs_durees`).

Partagée par les deux rattrapages : `scripts/retirer_durees_discogs.py` (la
durée, 2026-10-30) et `scripts/retirer_pistes_discogs.py` (le disque, la place
et les crédits de la même piste, 2026-11-02).

Le juge part de la durée Discogs : c'est elle qui désigne la page d'origine et
que le témoin dément. Après le premier rattrapage, la durée des pistes
démenties n'est plus en base ; la `reference` (la sauvegarde prise avant lui,
`before_retirer_durees_discogs`) la rend, pour la seule fiche qui porte
encore le MÊME disque sur la MÊME page — une autre lecture depuis ne se juge
pas sur l'ancienne.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from contextlib import closing
from pathlib import Path

from sqlalchemy import text

from src.utils.discogs_durees import Fiche


def durees_de_reference(base: Path) -> dict[int, tuple[int | None, int, object]]:
    """`{track_id: (genius_id, discogs_id, durée Discogs)}` d'une autre base
    (ouverte en LECTURE SEULE), pour les fiches qui y avaient disque et durée."""
    uri = f"file:{base.resolve().as_posix()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        return {
            tid: (gid, did, valeur)
            for tid, gid, did, valeur in conn.execute(
                "SELECT t.id, t.genius_id, t.discogs_id, o.value FROM observations o "
                "JOIN tracks t ON t.id = o.track_id "
                "WHERE o.field = 'duration' AND o.source = 'discogs' "
                "AND t.discogs_id IS NOT NULL"
            )
        }


def lire_fiches(dm, reference: Path | None = None) -> tuple[list[Fiche], dict]:
    """Les fiches de la base, avec leurs durées observées et leur place Discogs,
    et `{id: tracks.duration}`. Avec `reference`, la durée Discogs retirée
    depuis est rendue à la fiche qui porte encore le même disque."""
    with dm.engine.connect() as conn:
        durees: dict[int, dict] = defaultdict(dict)
        for tid, source, valeur in conn.execute(
            text("SELECT track_id, source, value FROM observations WHERE field = 'duration'")
        ):
            durees[tid][source] = valeur
        places = dict(
            conn.execute(
                text(
                    "SELECT track_id, value FROM observations "
                    "WHERE field = 'discogs_position' AND source = 'discogs'"
                )
            ).all()
        )
        lignes = list(
            conn.execute(
                text(
                    "SELECT t.id, t.artist_id, a.name AS artiste, t.title, t.album, "
                    "t.genius_id, t.discogs_id, t.duration, t.primary_artist_name "
                    "FROM tracks t JOIN artists a ON a.id = t.artist_id"
                )
            ).mappings()
        )
    anciennes = durees_de_reference(reference) if reference else {}
    noms: dict[int, frozenset] = {}
    for r in lignes:
        if r["artist_id"] not in noms:
            noms[r["artist_id"]] = frozenset(
                dm.noms_de_lartiste(r["artist_id"], r["artiste"])
                | dm.noms_des_formations(r["artist_id"])
            )
    fiches, colonnes = [], {}
    for r in lignes:
        colonnes[r["id"]] = r["duration"]
        observees = dict(durees.get(r["id"], {}))
        ancienne = anciennes.get(r["id"])
        if (
            "discogs" not in observees
            and ancienne is not None
            and r["discogs_id"] is not None
            and (ancienne[0], ancienne[1]) == (r["genius_id"], r["discogs_id"])
        ):
            observees["discogs"] = ancienne[2]
        fiches.append(
            Fiche(
                id=r["id"],
                artist_id=r["artist_id"],
                artiste=r["artiste"],
                title=r["title"] or "",
                album=r["album"],
                genius_id=r["genius_id"],
                discogs_id=r["discogs_id"],
                position=places.get(r["id"]),
                durations_observees=observees,
                principal=r["primary_artist_name"] or r["artiste"],
                noms_de_l_artiste=noms[r["artist_id"]],
            )
        )
    return fiches, colonnes
