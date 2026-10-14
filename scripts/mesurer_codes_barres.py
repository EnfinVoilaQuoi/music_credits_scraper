"""Mesure ce que le CODE-BARRES des parutions ouvre ailleurs — LECTURE SEULE.

Le code-barres (`releases.upc`, e54) désigne l'édition chez toutes les sources.
Avant de bâtir dessus, mesurer sur la base :

  · MusicBrainz — la parution de ce code-barres est-elle connue, et lie-t-elle
    l'album Spotify, Deezer, Apple de cette édition (sans rapprochement de
    titre) ? C'est la clé qui manque pour rattacher un album Spotify ;
  · Discogs — un pressage porte-t-il ce code-barres ? C'est la seule voie par
    laquelle une place Discogs (« 1-03 », « A1 ») rejoint une de nos parutions.

Sonde du 2026-10-14 (API, 279 albums Deezer de 19 artistes, hors base) :
  · MusicBrainz connaît 85 UPC sur 207 du rap français (41 % ; albums 59/94,
    singles 18/98), 54 avec le trio Spotify + Deezer + Apple ; 42 sur 72 aux
    États-Unis (58 %). Swing 0/12, Booba 5/23, Diam's 15/17 ;
  · Discogs : 1 code-barres numérique sur 40 (Booba *Panthéon*, réédition CD)
    — il catalogue le physique, la place Discogs rejoindra rarement une
    parution Deezer par cette voie.

Usage (réseau : MusicBrainz 1 req/s, Discogs 1 req/s) :
    python scripts/mesurer_codes_barres.py
    python scripts/mesurer_codes_barres.py --artiste Booba --sans-discogs
    python scripts/mesurer_codes_barres.py --limite 50 --json mesure.json
"""

import argparse
import json
import sys
import time
from collections import Counter, defaultdict

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests
from sqlalchemy import text

from src.api.discogs_api import token_discogs
from src.api.musicbrainz_api import MusicBrainzAPI
from src.utils.codes_barres import cle_upc
from src.utils.data_manager import DataManager


def _parutions(dm, artiste: str | None, limite: int | None) -> list[dict]:
    filtre = "AND a.name = :artiste" if artiste else ""
    with dm.engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT r.id, r.title, r.upc, a.name AS artiste FROM releases r "
                "JOIN artists a ON a.id = r.artist_id "
                f"WHERE r.upc IS NOT NULL AND r.status = 'confirmed' {filtre} ORDER BY a.name, r.id"
            ),
            {"artiste": artiste} if artiste else {},
        ).mappings()
        vues, out = set(), []
        for r in rows:
            cle = cle_upc(r["upc"])
            if cle and cle not in vues:
                vues.add(cle)
                out.append(dict(r))
    return out[:limite] if limite else out


def _discogs(session: requests.Session, upc: str) -> list[int]:
    cle = cle_upc(upc)
    ids: set[int] = set()
    for variante in sorted({cle, cle.zfill(12), cle.zfill(13)}):
        for _ in range(3):
            time.sleep(1.1)
            r = session.get(
                "https://api.discogs.com/database/search",
                params={"type": "release", "barcode": variante},
                timeout=20,
            )
            if r.status_code == 429:
                time.sleep(30)
                continue
            ids |= {x["id"] for x in r.json().get("results", [])}
            break
    return sorted(ids)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artiste")
    parser.add_argument("--limite", type=int)
    parser.add_argument("--sans-discogs", action="store_true")
    parser.add_argument("--json", help="écrit le détail ligne par ligne")
    args = parser.parse_args()

    parutions = _parutions(DataManager(), args.artiste, args.limite)
    print(f"{len(parutions)} code(s)-barres distincts à sonder\n")
    mb = MusicBrainzAPI()
    discogs = requests.Session()
    discogs.headers["User-Agent"] = "MusicCreditsScraper/1.0"
    if token_discogs():
        discogs.headers["Authorization"] = f"Discogs token={token_discogs()}"

    lignes = []
    try:
        for p in parutions:
            trouvees = mb.parutions_par_code_barres(p["upc"])
            albums: dict[str, set] = defaultdict(set)
            for t in trouvees:
                for plateforme, ids in t["albums"].items():
                    albums[plateforme] |= set(ids)
            ligne = {
                **p,
                "musicbrainz": [t["mbid"] for t in trouvees],
                "albums": {k: sorted(v) for k, v in albums.items()},
                "discogs": None if args.sans_discogs else _discogs(discogs, p["upc"]),
            }
            lignes.append(ligne)
            print(
                f"   {p['artiste']} — « {p['title']} » ({p['upc']}) : "
                f"MB {len(trouvees)} {sorted(albums)}"
                + ("" if args.sans_discogs else f", Discogs {len(ligne['discogs'])}")
            )
    finally:
        mb.close()

    par = defaultdict(Counter)
    for li in lignes:
        c = par[li["artiste"]]
        c["total"] += 1
        c["mb"] += bool(li["musicbrainz"])
        for plateforme in ("spotify", "deezer", "apple_music"):
            c[plateforme] += plateforme in li["albums"]
        c["discogs"] += bool(li["discogs"])
    print("\nArtiste               total   MB  Spotify Deezer Apple  Discogs")
    for artiste, c in sorted(par.items()):
        print(
            f"{artiste[:20]:20} {c['total']:6} {c['mb']:4} {c['spotify']:8} {c['deezer']:6}"
            f" {c['apple_music']:5} {'—' if args.sans_discogs else c['discogs']:8}"
        )
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(lignes, f, ensure_ascii=False, indent=1, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
