"""Simulation À BLANC : la passe Spotify par les requêtes internes contre `spotify_web`.

Avant de brancher `spotify_pathfinder` sur l'étape streams (réglage
`SPOTIFY_STREAMS_PAR_PATHFINDER`), on compare ce qu'il rendrait à ce que les pages
ont écrit, sur un échantillon tiré au hasard :

- pistes : dernier compteur et dernière durée `spotify_web` de la fiche, contre la
  lecture du même ID par `SpotifyPathfinderScraper.afetch_track` ;
- artistes : auditeurs mensuels en base contre `afetch_artist` ;
- albums totalisés par `spotify_web` : le total recalculé sur leurs éditions
  (`spotify_album_ids`) avec les compteurs de `afetch_album`, dédupliqués par TITRE
  comme `_totaliser_albums`, contre le total en base.

N'écrit RIEN en base (ouverte en lecture seule ; une COPIE de préférence,
`scripts/copie_base.py`) ; le brut des requêtes va dans `--brut` (un fichier à soi),
jamais dans `data/brut/`.

    python scripts/simuler_spotify_pathfinder.py --base <copie.db> --brut <dossier>
        [--pistes 200] [--artistes 30] [--albums 40] [--jours 14] [--sortie x.json]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sqlite3
import statistics
import time
from contextlib import closing
from pathlib import Path

from src.observability.issues import SansReponse
from src.persistence import brut
from src.scrapers.spotify_pathfinder_scraper import SpotifyPathfinderScraper
from src.utils.title_matching import normalize_title

GRAINE = 20261120


def _echantillons(base: Path, n_pistes: int, n_artistes: int, n_albums: int, jours: int):
    with closing(sqlite3.connect(f"file:{base.as_posix()}?mode=ro", uri=True)) as c:
        pistes = c.execute(
            """
            SELECT t.id, t.title, t.spotify_id, o.value, o.seen_at,
              (SELECT d.value FROM observations d WHERE d.track_id = t.id
                 AND d.field = 'duration' AND d.source = 'spotify_web'
               ORDER BY d.seen_at DESC LIMIT 1)
            FROM tracks t JOIN observations o ON o.track_id = t.id
            WHERE o.source = 'spotify_web' AND o.field = 'spotify_streams'
              AND t.spotify_id IS NOT NULL
              AND o.seen_at = (SELECT max(x.seen_at) FROM observations x
                               WHERE x.track_id = t.id AND x.source = 'spotify_web'
                                 AND x.field = 'spotify_streams')
              AND o.seen_at >= datetime('now', ?)
            """,
            (f"-{jours} days",),
        ).fetchall()
        artistes = c.execute(
            "SELECT name, spotify_id, spotify_monthly_listeners FROM artists "
            "WHERE spotify_id IS NOT NULL AND spotify_monthly_listeners > 0"
        ).fetchall()
        albums = c.execute(
            "SELECT title, spotify_album_ids, spotify_streams, spotify_streams_updated "
            "FROM albums WHERE spotify_streams_source = 'spotify_web' "
            "AND spotify_album_ids IS NOT NULL AND spotify_streams > 0 "
            "AND spotify_streams_updated >= datetime('now', ?)",
            (f"-{jours} days",),
        ).fetchall()
    hasard = random.Random(GRAINE)
    uniques, vus = [], set()
    for p in hasard.sample(pistes, len(pistes)):
        if p[2] not in vus:
            vus.add(p[2])
            uniques.append(p)
    return (
        uniques[:n_pistes],
        hasard.sample(artistes, min(n_artistes, len(artistes))),
        hasard.sample(albums, min(n_albums, len(albums))),
    )


async def _simuler(pistes, artistes, albums) -> dict:
    scraper = SpotifyPathfinderScraper()
    res: dict = {"pistes": [], "artistes": [], "albums": [], "echecs": []}
    async with scraper.session() as sess:

        async def lire(genre, cle, coro):
            debut = time.perf_counter()
            try:
                return await coro, round(time.perf_counter() - debut, 2)
            except SansReponse as e:
                res["echecs"].append({"genre": genre, "id": cle, "nature": str(e.kind)})
                return None, round(time.perf_counter() - debut, 2)

        for fiche, titre, sid, streams, vu, duree in pistes:
            page, s = await lire("piste", sid, scraper.afetch_track(sess, sid))
            res["pistes"].append(
                {
                    "fiche": fiche,
                    "titre": titre,
                    "id": sid,
                    "web_streams": int(streams),
                    "web_vu": vu,
                    "web_duree": float(duree) if duree else None,
                    "lu": page,
                    "s": s,
                }
            )
        for nom, sid, auditeurs in artistes:
            page, s = await lire("artiste", sid, scraper.afetch_artist(sess, sid))
            res["artistes"].append(
                {
                    "nom": nom,
                    "web": auditeurs,
                    "lu": page and page["monthly_listeners"],
                    "nom_lu": page and page["name"],
                    "s": s,
                }
            )
        for titre, ids, total, maj in albums:
            editions = [i.strip() for i in ids.split(",") if i.strip()]
            compositions, s_total, manque = {}, 0.0, False
            for album_id in editions:
                album, s = await lire("album", album_id, scraper.afetch_album(sess, album_id))
                s_total += s
                if album is None:
                    manque = True
                    break
                compositions[album_id] = album
            recalcule = None
            if not manque:
                # Même déduplication que `_totaliser_albums` : un enregistrement = un titre.
                par_titre: dict[str, int | None] = {}
                for album in compositions.values():
                    for track_id, titre_piste in album["tracks"]:
                        par_titre.setdefault(
                            normalize_title(titre_piste), album["playcounts"].get(track_id)
                        )
                if all(v is not None for v in par_titre.values()):
                    recalcule = sum(par_titre.values())
            res["albums"].append(
                {
                    "titre": titre,
                    "editions": len(editions),
                    "web_total": int(total),
                    "web_maj": maj,
                    "recalcule": recalcule,
                    "s": round(s_total, 2),
                }
            )
    return res


def _resume(res: dict) -> dict:
    def ratios(couples):
        return [b / a for a, b in couples if a and b is not None]

    pistes = [p for p in res["pistes"] if p["lu"]]
    compteurs = [(p["web_streams"], p["lu"]["playcounts"].get(p["id"])) for p in pistes]
    r_pistes = ratios(compteurs)
    en_baisse = [p for p, (a, b) in zip(pistes, compteurs, strict=True) if b is not None and b < a]
    sans = [p for p in pistes if p["lu"]["sans_compteur"]]
    durees = [
        abs(p["lu"]["durations"][p["id"]] - p["web_duree"])
        for p in pistes
        if p["web_duree"] and p["id"] in p["lu"]["durations"]
    ]
    r_art = ratios([(a["web"], a["lu"]) for a in res["artistes"] if a["lu"] is not None])
    albums = [a for a in res["albums"] if a["recalcule"] is not None]
    r_alb = ratios([(a["web_total"], a["recalcule"]) for a in albums])

    def stats(valeurs):
        if not valeurs:
            return None
        return {
            "n": len(valeurs),
            "min": round(min(valeurs), 4),
            "mediane": round(statistics.median(valeurs), 4),
            "max": round(max(valeurs), 4),
        }

    temps = [x["s"] for genre in ("pistes", "artistes") for x in res[genre]]
    return {
        "pistes_lues": f"{len(pistes)} / {len(res['pistes'])}",
        "compteur_ratio_lu_sur_web": stats(r_pistes),
        "compteurs_en_baisse": [(p["titre"], p["web_streams"]) for p in en_baisse],
        "pistes_sans_compteur": [p["titre"] for p in sans],
        "duree_ecart_s": stats(durees),
        "durees_ecart_plus_de_1s": sum(1 for d in durees if d > 1),
        "auditeurs_ratio": stats(r_art),
        "albums_recalcules": f"{len(albums)} / {len(res['albums'])}",
        "album_total_ratio": stats(r_alb),
        "albums_hors_5_pct": [
            (a["titre"], a["web_total"], a["recalcule"])
            for a in albums
            if not 0.95 <= a["recalcule"] / a["web_total"] <= 1.10
        ],
        "echecs": res["echecs"],
        "temps_par_requete_s": stats(temps),
        "temps_total_s": round(sum(temps) + sum(a["s"] for a in res["albums"]), 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base", type=Path, required=True, help="copie de la base")
    parser.add_argument("--brut", type=Path, required=True, help="dossier du brut de la mesure")
    parser.add_argument("--pistes", type=int, default=200)
    parser.add_argument("--artistes", type=int, default=30)
    parser.add_argument("--albums", type=int, default=40)
    parser.add_argument("--jours", type=int, default=14, help="fraîcheur des relevés web")
    parser.add_argument("--sortie", type=Path)
    args = parser.parse_args()

    brut.CHEMIN = args.brut / "brut.db"
    pistes, artistes, albums = _echantillons(
        args.base, args.pistes, args.artistes, args.albums, args.jours
    )
    print(f"Échantillon : {len(pistes)} pistes, {len(artistes)} artistes, {len(albums)} albums")
    res = asyncio.run(_simuler(pistes, artistes, albums))
    resume = _resume(res)
    print(json.dumps(resume, ensure_ascii=False, indent=1))
    if args.sortie:
        args.sortie.write_text(
            json.dumps({"resume": resume, "detail": res}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
