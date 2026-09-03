"""Que dit Genius des personnes que Discogs crédite ? — oracle pour les alias de rôles.

Discogs et Genius se recouvrent : quand les DEUX créditent la même personne sur
le même morceau, ce que Genius en dit renseigne sur le libellé Discogs. C'est ce
qui a permis, le 2026-09-03, de trancher les alias de `role_mapping`
(`discogs_api`) sur mesure plutôt qu'à l'intuition — et de démentir deux
propositions plausibles : « Music By » n'est pas COMPOSER (Genius dit Producer /
Mixing Engineer) et « Realization » n'est pas sans équivalent.

L'oracle est MINCE (93 personnes communes au 2026-09-03) : il tranche
franchement quand le verdict Genius est unanime, et doit être laissé de côté
quand il est partagé. Ce script existe pour rejouer la mesure quand le corpus
aura grossi.

LECTURE SEULE — n'écrit jamais rien.

Usage :
    python scripts/discogs_genius_oracle.py              # libellés non mappés
    python scripts/discogs_genius_oracle.py --tous       # tous les rôles Discogs
"""

import argparse
import collections
import re
import sqlite3
import sys

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.api.discogs_api import DiscogsClient
from src.models.track import CreditRole
from src.utils.credit_normalize import identity_key

DB_PATH = "data/music_credits.db"

# `role_detail` peut porter la liste des pistes au lieu du libellé (cf. WIP).
_PISTES_RE = re.compile(r"^[\d\s,&at to\.\-]+$")


def couverture(conn):
    """Ce que Discogs apporte face à Genius : additif ou confirmatif ?"""
    cur = conn.cursor()
    par_track = collections.defaultdict(lambda: {"genius": set(), "discogs": set()})
    noms = collections.defaultdict(lambda: {"genius": set(), "discogs": set()})
    for tid, name, role, src in cur.execute(
        "SELECT track_id, name, role, source FROM credits WHERE source IN ('genius','discogs')"
    ):
        par_track[tid][src].add((identity_key(name), role))
        noms[tid][src].add(identity_key(name))

    communs = [t for t, d in par_track.items() if d["genius"] and d["discogs"]]
    memes = sum(len(par_track[t]["genius"] & par_track[t]["discogs"]) for t in communs)
    d_seul = sum(len(par_track[t]["discogs"] - par_track[t]["genius"]) for t in communs)
    pers_communes = sum(len(noms[t]["genius"] & noms[t]["discogs"]) for t in communs)
    pers_d_seul = sum(len(noms[t]["discogs"] - noms[t]["genius"]) for t in communs)

    print(f"\n{len(communs)} morceau(x) crédité(s) par les DEUX sources\n")
    print(f"   paires (personne, rôle) identiques des deux côtés : {memes:5}  ← confirmation")
    print(f"   paires vues seulement par Discogs                 : {d_seul:5}  ← apport")
    print(f"   personnes connues des deux sources                : {pers_communes:5}  ← l'ORACLE")
    print(f"   personnes vues seulement par Discogs              : {pers_d_seul:5}")


def oracle(conn, tous: bool):
    """Pour chaque libellé Discogs, ce que Genius dit des mêmes personnes."""
    client = DiscogsClient.__new__(DiscogsClient)
    cur = conn.cursor()

    genius = collections.defaultdict(set)
    for tid, name, role in cur.execute(
        "SELECT track_id, name, role FROM credits WHERE source = 'genius'"
    ):
        genius[(tid, identity_key(name))].add(role)

    verdicts = collections.defaultdict(collections.Counter)
    volumes = collections.Counter()
    for tid, name, role, role_detail in cur.execute(
        "SELECT track_id, name, role, role_detail FROM credits WHERE source = 'discogs'"
    ):
        libelle = role_detail if role == CreditRole.OTHER.value else role
        if role == CreditRole.OTHER.value and (not role_detail or _PISTES_RE.match(role_detail)):
            continue
        if not tous and role != CreditRole.OTHER.value:
            continue
        volumes[libelle] += 1
        for r in genius.get((tid, identity_key(name)), ()):
            verdicts[libelle][r] += 1

    titre = "TOUS les rôles Discogs" if tous else "libellés Discogs NON mappés"
    print(f"\n── {titre} → ce que Genius dit des mêmes personnes ──\n")
    for libelle, n in volumes.most_common():
        v = verdicts[libelle]
        total = sum(v.values())
        if not total:
            detail = "aucun recoupement — l'oracle ne dit rien"
        else:
            detail = ", ".join(f"{r} ×{c}" for r, c in v.most_common(3))
            tete = v.most_common(1)[0][1]
            detail += "   [UNANIME]" if tete == total else "   [partagé]"
        mappe = client._map_discogs_role_to_enum(libelle)
        marque = "" if mappe == CreditRole.OTHER else f" → {mappe.value}"
        print(f"   {n:4}  {libelle!r:32}{marque}")
        print(f"         oracle sur {total:3} : {detail}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tous", action="store_true", help="inclut les rôles déjà mappés")
    args = parser.parse_args()

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        couverture(conn)
        oracle(conn, args.tous)
        print(
            "\nℹ️  Un verdict UNANIME sur un volume suffisant justifie un alias dans"
            "\n    `role_mapping` (src/api/discogs_api.py). Un verdict PARTAGÉ, ou sans"
            "\n    recoupement, ne justifie rien : laisser en Other est plus honnête que"
            "\n    d'inventer une précision que la donnée n'a pas."
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
