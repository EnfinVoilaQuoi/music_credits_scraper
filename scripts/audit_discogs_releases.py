"""Retire les DISQUES Discogs rattachés à un morceau qui ne sont pas les siens.

Jusqu'au 2026-09-23 la recherche Discogs par morceau ne vérifiait jamais
l'artiste du disque trouvé : Django « Nuages » portait le disque de Django
Reinhardt (même titre de piste, recherche libre), et un crédit photo en avait
été importé. La recherche applique désormais `release_concorde` à chaque
candidat ; ce script rejoue le MÊME prédicat sur ce qui est déjà en base.

Un disque est relu une fois (cache par id, 1 req/s). Ce que le retrait efface
(`DataManager.clear_track_discogs_release`) : `discogs_id`, `genre` (Discogs en
est le seul écrivain) et les crédits `source='discogs'`, chez le morceau et ses
lignes sœurs qui portent ce disque.

Usage :
    python scripts/audit_discogs_releases.py                 # dry-run (défaut)
    python scripts/audit_discogs_releases.py --apply         # backup + écriture
    python scripts/audit_discogs_releases.py --artiste "Django"
    python scripts/audit_discogs_releases.py --json ecarts.json
    python scripts/audit_discogs_releases.py --depuis ecarts.json --apply
    python scripts/audit_discogs_releases.py --exclure 1233 1252
"""

import argparse
import json
import sys

# Fix encodage Windows (règle projet : reconfigure, jamais de re-wrapping)
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.utils.data_manager import DataManager
from src.utils.database_backup import get_backup_manager
from src.utils.discogs_audit import (
    lignes_a_verifier,
    noms_acceptes_par_artiste,
    verifier_lignes,
)


def auditer(dm: DataManager, artiste: str | None, pause: float) -> dict:
    from src.api.discogs_api import DiscogsClient, token_discogs

    lignes = lignes_a_verifier(dm.engine, artiste)
    disques = len({ligne["discogs_id"] for ligne in lignes})
    print(f"🔎 {len(lignes)} morceau(x) rattaché(s) à {disques} disque(s) Discogs\n")
    client = DiscogsClient(token_discogs())

    def _progression(faits, total):
        if faits % 50 == 0:
            print(f"   … {faits}/{total}")

    rapport = verifier_lignes(
        lignes,
        client.lire_disque,
        noms_par_artiste=noms_acceptes_par_artiste(dm, lignes),
        pause=pause,
        progression=_progression,
    )
    print(
        f"   {rapport['verifies']} vérifié(s), {rapport['illisibles']} illisible(s), "
        f"{rapport['disques_lus']} disque(s) lu(s), {len(rapport['ecarts'])} écart(s)"
    )
    return rapport


def imprimer(ecarts: list[dict]) -> None:
    print(f"\n{'─' * 78}")
    print(f"{len(ecarts)} disque(s) d'un autre artiste\n")
    for e in ecarts:
        credite = ", ".join(e["credite_a"]) or "?"
        print(f"  #{e['track_id']:<6} {e['artiste']} — « {e['titre']} »")
        print(f"         disque {e['discogs_id']} « {e['disque']} » — {credite}")


def appliquer(dm: DataManager, ecarts: list[dict]) -> None:
    sauvegarde = get_backup_manager().create_backup("before_audit_discogs_releases")
    print(f"\n💾 Backup : {sauvegarde}")
    lignes = credits = 0
    for e in ecarts:
        r = dm.clear_track_discogs_release(e["track_id"], e["discogs_id"])
        lignes += len(r["lignes"])
        credits += r["credits_retires"]
    print(f"\n✅ {len(ecarts)} disque(s) retiré(s) — {lignes} ligne(s) (sœurs comprises)")
    print(f"   {credits} crédit(s) Discogs supprimé(s), genres effacés")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Écrire (défaut : dry-run)")
    parser.add_argument("--artiste", help="Limiter à un artiste (nom exact en base)")
    parser.add_argument("--depuis", help="Reprendre le JSON d'un audit précédent")
    parser.add_argument("--json", help="Écrire le rapport complet dans ce fichier")
    parser.add_argument("--exclure", nargs="*", type=int, default=[], help="track_id à épargner")
    parser.add_argument("--pause", type=float, default=1.0, help="Pause entre deux disques")
    args = parser.parse_args()

    dm = DataManager()
    if args.depuis:
        with open(args.depuis, encoding="utf-8") as f:
            data = json.load(f)
        ecarts = data["ecarts"] if isinstance(data, dict) else data
    else:
        rapport = auditer(dm, args.artiste, args.pause)
        ecarts = rapport["ecarts"]
        if args.json:
            with open(args.json, "w", encoding="utf-8") as f:
                json.dump(rapport, f, ensure_ascii=False, indent=2)
            print(f"📝 Rapport : {args.json}")

    if args.exclure:
        avant = len(ecarts)
        ecarts = [e for e in ecarts if e["track_id"] not in set(args.exclure)]
        print(f"({avant - len(ecarts)} ligne(s) épargnée(s) par --exclure)")

    imprimer(ecarts)
    if not ecarts:
        print("\nRien à retirer.")
        return 0
    if not args.apply:
        print("\n(dry-run — relancer avec --apply pour écrire)")
        return 0

    attendu = str(len(ecarts))
    reponse = input(f"\nTaper {attendu} pour confirmer le retrait : ").strip()
    if reponse != attendu:
        print("Annulé.")
        return 1
    appliquer(dm, ecarts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
