"""Retire les crédits Discogs attribués à une piste qui n'est pas la leur (lot B5).

Discogs crédite au niveau du DISQUE et dit, champ `tracks`, à quelles pistes un
crédit se rapporte (« Mr Hudson — C2 »). Ce champ n'était pas lu avant le
2026-09-22 : chaque crédit de disque était recopié sur chaque morceau. Le run
Discogs filtre désormais à l'écriture (`discogs_positions.concerne_la_piste`),
mais les crédits DÉJÀ en base ne sont pas réparables hors ligne — la position
Discogs d'un morceau n'est stockée nulle part.

Ce script la relit : une requête par DISQUE (tracklist, 1 req/s), la position
du morceau retrouvée par son titre — et, quand le titre revient plusieurs fois
sur le disque, par la durée (± 3 s, un seul candidat) ; sinon on ne conclut pas.
Le verdict vient du MÊME prédicat que le run (`concerne_la_piste`), rien n'est
réimplémenté. Mesuré le 2026-09-29 sur 118 disques : 411 crédits justes,
140 à tort, 126 sur un titre ambigu.

Usage :
    python scripts/repair_discogs_pistes.py                  # dry-run (défaut)
    python scripts/repair_discogs_pistes.py --apply          # backup + écriture
    python scripts/repair_discogs_pistes.py --artiste "Django"
"""

import argparse
import json
import sys
import time
from collections import defaultdict

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import text

from src.api.discogs_api import DiscogsClient, token_discogs
from src.utils.data_manager import DataManager
from src.utils.database_backup import get_backup_manager
from src.utils.discogs_positions import concerne_la_piste

#: Tolérance du départage par la durée d'un titre qui revient sur le disque.
TOLERANCE_S = 3


def secondes(duree) -> int | None:
    try:
        m, s = str(duree).split(":")[-2:]
        return int(m) * 60 + int(s)
    except (ValueError, AttributeError):
        return None


def position_du_morceau(pistes, titre: str, duree: int | None) -> str | None:
    """La position du morceau sur le disque, ou None si on ne peut pas conclure.
    PURE : `pistes` = [(titre, position, durée « m:ss »)]."""
    cle = DiscogsClient._normalize_string(titre)
    candidats = [p for p in pistes if p[1] and DiscogsClient._normalize_string(p[0]) == cle]
    if len(candidats) > 1 and duree:
        candidats = [
            p for p in candidats if secondes(p[2]) and abs(secondes(p[2]) - duree) <= TOLERANCE_S
        ]
    return candidats[0][1] if len(candidats) == 1 else None


class _Disques:
    """Tracklists lues une fois par disque, réessayées sur réponse vide (bridage)."""

    def __init__(self, client: DiscogsClient):
        self.client, self.cache = client, {}

    def pistes(self, rid: int):
        if rid in self.cache:
            return self.cache[rid]
        for essai in range(3):
            self.client._check_rate_limit()
            try:
                r = self.client.client.release(int(rid))
                self.cache[rid] = [(p.title, p.position, p.duration) for p in r.tracklist]
                return self.cache[rid]
            except json.JSONDecodeError:
                time.sleep(20 * (essai + 1))  # réponse vide : bridage, on attend
            except Exception as e:  # noqa: BLE001 — un disque illisible ne conclut rien
                print(f"   ! disque {rid} illisible : {e}")
                break
        self.cache[rid] = None
        return None


def auditer(dm: DataManager, artiste: str | None) -> dict:
    filtre = "AND a.name = :artiste" if artiste else ""
    with dm.engine.connect() as c:
        lignes = c.execute(
            text(
                "SELECT c.track_id, c.name, c.role, c.tracks, t.title, t.discogs_id, t.duration,"
                " a.name AS artiste FROM credits c JOIN tracks t ON t.id = c.track_id"
                " JOIN artists a ON a.id = t.artist_id"
                " WHERE c.source = 'discogs' AND c.tracks IS NOT NULL AND c.tracks <> ''"
                f" AND t.discogs_id IS NOT NULL {filtre}"
            ),
            {"artiste": artiste},
        ).fetchall()
    disques = _Disques(DiscogsClient(token_discogs()))
    rapport = {"justes": 0, "ambigus": 0, "illisibles": 0, "a_tort": defaultdict(list)}
    for i, r in enumerate(lignes, 1):
        if i % 100 == 0:
            print(f"   … {i}/{len(lignes)}")
        pistes = disques.pistes(r.discogs_id)
        if pistes is None:
            rapport["illisibles"] += 1
            continue
        position = position_du_morceau(pistes, r.title, r.duration)
        if position is None:
            rapport["ambigus"] += 1
        elif concerne_la_piste(r.tracks, position):
            rapport["justes"] += 1
        else:
            rapport["a_tort"][r.track_id].append(
                {
                    "nom": r.name,
                    "role": r.role,
                    "pistes": r.tracks,
                    "position": position,
                    "titre": r.title,
                    "artiste": r.artiste,
                }
            )
    return rapport


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--apply", action="store_true", help="backup puis retrait")
    p.add_argument("--artiste", help="un seul artiste")
    a = p.parse_args()
    dm = DataManager()
    rapport = auditer(dm, a.artiste)
    a_tort = rapport["a_tort"]
    n = sum(len(v) for v in a_tort.values())
    print(
        f"\n{rapport['justes']} crédit(s) juste(s), {n} à tort sur {len(a_tort)} fiche(s), "
        f"{rapport['ambigus']} non tranché(s) (titre ambigu), "
        f"{rapport['illisibles']} sur un disque illisible"
    )
    for credits in list(a_tort.values())[:25]:
        c = credits[0]
        noms = ", ".join(f"{x['nom']} ({x['role']}, pistes {x['pistes']})" for x in credits)
        print(f"   {c['artiste']} — « {c['titre']} » (piste {c['position']}) : {noms}")
    if not a.apply:
        print("\n(dry-run — relancer avec --apply pour retirer)")
        return 0
    if not n:
        return 0
    sauvegarde = get_backup_manager().create_backup("before_repair_discogs_pistes")
    if sauvegarde is None:
        print("❌ Sauvegarde impossible : rien n'est retiré.")
        return 1
    retires = sum(
        dm.forget_discogs_credits(tid, [(x["nom"], x["role"], x["pistes"]) for x in credits])
        for tid, credits in a_tort.items()
    )
    print(f"✅ {retires} ligne(s) retirée(s) (lignes sœurs comprises) — sauvegarde : {sauvegarde}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
