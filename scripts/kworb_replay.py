"""Rejoue le rapprochement Kworb à BLANC sur un ou tous les artistes — zéro écriture.

Le harnais du tour du run streams (2026-09-20/21) : la vraie page Kworb, la
vraie base, le vrai `update_kworb_streams`, mais un DataManager qui NOTE les
écritures au lieu de les faire. Ce qu'il imprime : les voies de rapprochement,
les renditions rattachées, les propositions (remix) avec leur défaut, les IDs
partagés, les lignes multiples et leur sommation, les variantes suspectes.

    python scripts/kworb_replay.py Booba
    python scripts/kworb_replay.py --all --sans-embed     # aucune requête Spotify

`--sans-embed` remplace le lecteur d'identité par un muet : les homonymes et
les remix ne sont plus départagés par Spotify, mais rien n'est dépensé.
"""

import argparse
import sys
import time

if sys.platform == "win32" and "pytest" not in sys.modules:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.utils.data_manager import DataManager
from src.utils.spotify_identity import lire_identite_http
from src.utils.update_kworb import update_kworb_streams


class _Enregistreur:
    """Lit la vraie base, n'écrit rien : chaque écriture est notée."""

    def __init__(self, dm: DataManager):
        self._dm = dm
        self.streams = []
        self.variantes = []
        self.ids = []

    def get_artist_tracks(self, artist_id):
        return self._dm.get_artist_tracks(artist_id)

    def record_spotify_streams(self, track_id, streams, source, updated_at=None, **kw):
        self.streams.append((track_id, streams, kw.get("daily_streams")))
        return True

    def record_variant_streams(
        self, track_id, spotify_id, streams, daily, seen_at, label=None, variant_track_id=None
    ):
        self.variantes.append((track_id, spotify_id, streams, label))
        return True

    def update_track_spotify_id(self, track_id, spotify_id, source="kworb"):
        self.ids.append((track_id, spotify_id))
        return True

    def update_artist_spotify_id(self, artist_id, spotify_id):
        return True

    def update_artist_kworb_totals(self, artist_id, **kwargs):
        return True

    def upsert_album(self, *a, **kw):
        return True


def _n(x):
    return f"{x:,}".replace(",", " ")


def rejouer(dm: DataManager, nom: str, lire_identite) -> dict | None:
    artist = dm.get_artist_by_name(nom)
    if artist is None:
        print(f"!! artiste inconnu : {nom}")
        return None
    enr = _Enregistreur(dm)
    res = update_kworb_streams(artist, enr, lire_identite=lire_identite)
    if not res["artist_name"]:
        print(f"\n=== {nom} : pas de page Kworb")
        return None
    total = res["matched"] + res["unmatched"] + len(res["suggestions"])
    print(
        f"\n=== {nom} : {total} lignes — matchés {res['matched']} "
        f"(id {res['matched_by_id']}, titre {res['matched_by_title']}, flou {res['matched_by_fuzzy']}) · "
        f"renditions {len(res['renditions_rattachees'])} · propositions {len(res['suggestions'])} · "
        f"non matchés {res['unmatched']} · IDs partagés {len(res['ids_partages'])} · "
        f"écritures notées {len(enr.streams)} streams / {len(enr.variantes)} variantes / {len(enr.ids)} IDs"
    )
    for kw, parent, st in res["renditions_rattachees"]:
        print(f"   🎚️ « {kw} » → « {parent} »  {_n(st)}")
    for s in res["suggestions"]:
        if s.get("kind"):
            print(
                f"   ❓ « {s['kworb_title']} »  {_n(s['streams'])}  {s['kind']} → {s['proposition']}"
                + (
                    f"  [existant : {s['existants'][0][1]}]"
                    if len(s.get("existants") or []) == 1
                    else ""
                )
                + (f"  socle « {s['parent_title']} »" if s.get("parent_title") else "")
                + ("".join(f"\n        · {m}" for m in s.get("motifs") or []))
            )
        else:
            print(f"   ❓ « {s['kworb_title']} » ≈ « {s['db_title']} » ({s['score']:.0%})")
    for sid, titres in res["ids_partages"]:
        print(f"   ⚠️ ID partagé {sid} : {' | '.join(titres)}")
    for base, spotify, sid in res["variantes_suspectes"]:
        print(f"   🔀 base « {base} » ↔ Spotify « {spotify} »  ({sid})")
    for titre, n, retenues, tot in res["multi_lignes"]:
        print(f"   🎛️ « {titre} » : {n} lignes, {retenues} comptées → {_n(tot)}")
    for kw, st, base in res["lignes_ecartees"]:
        print(f"   ⤫ « {kw} » {_n(st)} écartée (≠ « {base} »)")
    for titre, st in res["unmatched_details"]:
        print(f"   ✗ « {titre} »  {_n(st)}")
    return res


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artiste", nargs="?", help="nom exact en base")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--sans-embed", action="store_true", help="aucune requête Spotify")
    args = parser.parse_args()
    if not args.artiste and not args.all:
        parser.error("un artiste ou --all")

    dm = DataManager()
    lire = (lambda sid: None) if args.sans_embed else lire_identite_http
    noms = dm.get_artist_names() if args.all else [args.artiste]
    cumul = {"lignes": 0, "matches": 0, "renditions": 0, "propositions": 0, "non": 0, "partages": 0}
    for nom in noms:
        res = rejouer(dm, nom, lire)
        if res:
            cumul["lignes"] += res["matched"] + res["unmatched"] + len(res["suggestions"])
            cumul["matches"] += res["matched"]
            cumul["renditions"] += len(res["renditions_rattachees"])
            cumul["propositions"] += len(res["suggestions"])
            cumul["non"] += res["unmatched"]
            cumul["partages"] += len(res["ids_partages"])
        time.sleep(1.0)
    print(f"\nTOTAL {cumul}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
