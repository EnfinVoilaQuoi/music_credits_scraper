"""Mesure la numérotation des disques — LECTURE SEULE, hors ligne (2026-10-14).

Deux fiches au même emplacement d'un même disque sont un seul morceau ; à deux
emplacements, deux morceaux — à source égale (`src/utils/positions_disque.py`).
La règle ne sert qu'en APPOINT d'un 👯 tant que la numérotation est maigre :
ce script dit où elle en est, pour savoir quand la reprendre.

Mesure du 2026-10-12, avant e52 (par source du LIEN) : 2 790 liens numérotés
sur 9 930 ; deezer 1 256 / 1 627, legacy 1 485 / 6 962, genius 49 / 1 334 ;
6 paires à la même place ; aucun des 16 👯 n'avait de disque numéroté commun.

Usage :
    python scripts/mesurer_positions.py
    python scripts/mesurer_positions.py --artiste A2H
    python scripts/mesurer_positions.py --panneau     # 👯 du panneau, avec appoint 📀
"""

import argparse
import sys
from collections import Counter

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import text

from src.utils.data_manager import DataManager
from src.utils.positions_disque import SOURCES_COMPARABLES


def _liens(dm, artiste: str | None) -> list[dict]:
    filtre = "WHERE a.name = :artiste" if artiste else ""
    with dm.engine.connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                text(
                    "SELECT rt.release_id, r.title AS disque, rt.track_id, t.title, a.name AS artiste, "
                    "rt.source, rt.position_source, rt.disc_number, rt.track_number "
                    "FROM release_tracks rt JOIN releases r ON r.id = rt.release_id "
                    "JOIN tracks t ON t.id = rt.track_id JOIN artists a ON a.id = t.artist_id "
                    f"{filtre}"
                ),
                {"artiste": artiste} if artiste else {},
            ).mappings()
        ]


def densite(liens: list[dict]) -> None:
    numerotes = [li for li in liens if li["track_number"] is not None]
    print(f"Liens : {len(liens)}   ·   numérotés : {len(numerotes)}")
    par_lien, par_place = Counter(li["source"] for li in liens), Counter()
    num_par_lien = Counter(li["source"] for li in numerotes)
    for li in numerotes:
        par_place[li["position_source"] or "?"] += 1
    print("\nPar source du LIEN (comparable à la mesure du 2026-10-12) :")
    for source, total in par_lien.most_common():
        print(f"   {source or '?':14} {num_par_lien[source]:>6} numérotés sur {total}")
    print("\nPar source de la PLACE (e52) — seules les comparables se confrontent :")
    for source, n in par_place.most_common():
        marque = "✔" if source in SOURCES_COMPARABLES else "✘"
        print(f"   {marque} {source:12} {n:>6}")


def places_partagees(liens: list[dict]) -> None:
    par_place: dict[tuple, list[dict]] = {}
    for li in liens:
        if li["track_number"] is None:
            continue
        cle = (li["release_id"], li["disc_number"] or 1, li["track_number"])
        par_place.setdefault(cle, []).append(li)
    paires = [g for g in par_place.values() if len(g) >= 2]
    comparables = [
        g
        for g in paires
        if len({li["position_source"] for li in g}) == 1
        and g[0]["position_source"] in SOURCES_COMPARABLES
    ]
    print(f"\nPlaces portées par plusieurs fiches : {len(paires)}")
    print(f"   dont à source égale et comparable (preuve d'appoint) : {len(comparables)}")
    for g in paires:
        egale = g in comparables
        print(
            f"   {'✔' if egale else '✘'} {g[0]['artiste']} — « {g[0]['disque']} » "
            f"piste {g[0]['track_number']} : "
            + " / ".join(f"« {li['title']} » ({li['position_source'] or '?'})" for li in g)
        )


def panneau(dm, artiste: str | None) -> None:
    from src.services.revue import DETECTEURS, analyser

    doublons = {d.code for d in DETECTEURS if d.icone == "👯"}
    noms = [artiste] if artiste else sorted(dm.get_artist_names())
    total = avec = 0
    for nom in noms:
        a = dm.get_artist_by_name(nom)
        if a is None:
            continue
        a.tracks = dm.get_artist_tracks(a.id)
        for c in analyser(dm, a).actifs:
            if c.detecteur not in doublons:
                continue
            total += 1
            if "📀" in c.motif:
                avec += 1
                print(f"   📀 {nom} — {c.morceau} : {c.motif}")
    print(f"\n👯 actifs au panneau : {total}   ·   avec une place en appoint : {avec}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artiste", help="Limiter à un artiste (nom exact en base)")
    parser.add_argument("--panneau", action="store_true", help="Compter les 👯 et leur appoint")
    args = parser.parse_args()

    dm = DataManager()
    liens = _liens(dm, args.artiste)
    densite(liens)
    places_partagees(liens)
    if args.panneau:
        panneau(dm, args.artiste)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
