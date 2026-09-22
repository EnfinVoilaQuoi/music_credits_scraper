"""Retire les identifiants Deezer qui désignent un autre morceau.

Mesuré le 2026-09-22 : la recherche avancée Deezer est morte pour tout le monde
et le repli par id d'artiste prenait le PREMIER hit de l'artiste sans regarder
le titre — sur 60 fiches tirées au sort, 3 (5 %) portaient l'id de « Rolling
200 Deep » (DJ Kay Slay) : Kanye « I Got a Love », « The Joy », Kid Cudi
« Run ». Un hit faux écrit l'id, l'ISRC, la durée, une date, un BPM et une
parution : `DataManager.clear_track_deezer_id` fait les trois gestes.

**Critère de retrait : le TITRE** (`variante_etrangere` — Deezer sert un
autre morceau ou une autre version), et lui seul. Mesuré sur la base réelle
(660 ids, 50 écarts) : les 20 « Rolling 200 Deep » ont tous un titre
étranger ; mais 19 fiches à titre IDENTIQUE sont créditées à quelqu'un
d'autre — KIDS SEE GHOSTS (le duo Kanye/Cudi), les albums de Tiers et BRAV
où Médine est invité — et ce sont très probablement les BONS morceaux, que
Deezer crédite au projet. Ceux-là sont SIGNALÉS (« artiste étranger, titre
identique »), jamais retirés d'office. Un écart de durée seul ne retire rien.

Aucune règle réimplémentée : le verdict vient de `deezer_identity.hit_concorde`,
le même prédicat que le gate du client et que l'audit (`deezer_audit`).

Usage :
    python scripts/repair_deezer_ids.py                     # dry-run (défaut)
    python scripts/repair_deezer_ids.py --apply             # backup + écriture
    python scripts/repair_deezer_ids.py --artiste "Kanye West"
    python scripts/repair_deezer_ids.py --json ecarts.json  # écrit le rapport complet
    python scripts/repair_deezer_ids.py --depuis ecarts.json
    python scripts/repair_deezer_ids.py --exclure 1233 1252
"""

import argparse
import json
import sys

# Fix encodage Windows (règle projet : reconfigure, jamais de re-wrapping)
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.utils.data_manager import DataManager
from src.utils.database_backup import get_backup_manager
from src.utils.deezer_audit import lignes_a_verifier, verifier_lignes


def _fautif(ecart: dict) -> bool:
    return bool(ecart.get("variante_etrangere"))


def auditer(dm: DataManager, artiste: str | None, pause: float) -> dict:
    lignes = lignes_a_verifier(dm.engine, artiste, None)
    print(f"🔎 {len(lignes)} identifiant(s) Deezer à vérifier sur /track/{{id}}\n")

    def _progression(faits, total):
        if faits % 50 == 0:
            print(f"   … {faits}/{total}")

    rapport = verifier_lignes(lignes, pause=pause, progression=_progression)
    print(
        f"   {rapport['verifies']} vérifié(s), {rapport['illisibles']} illisible(s), "
        f"{len(rapport['ecarts'])} écart(s)"
    )
    return rapport


def depuis_json(chemin: str) -> list[dict]:
    with open(chemin, encoding="utf-8") as f:
        data = json.load(f)
    return data["ecarts"] if isinstance(data, dict) else data


def imprimer(fautifs: list[dict], autres: list[dict]) -> None:
    print(f"\n{'─' * 78}")
    print(f"{len(fautifs)} identifiant(s) à retirer\n")
    for f in fautifs:
        d = f["deezer"]
        print(f"  #{f['track_id']:<6} {f['artiste']} — « {f['titre']} »")
        print(f"         id {f['deezer_id']} → « {d['title']} » — {d['artist']}  ({f['motif']})")
    if autres:
        print(f"\n{len(autres)} écart(s) SIGNALÉ(S) sans retrait (titre identique, ou durée) :")
        for f in autres:
            print(f"  #{f['track_id']:<6} {f['artiste']} — « {f['titre']} » : {f['motif'][:110]}")


def appliquer(dm: DataManager, fautifs: list[dict]) -> None:
    sauvegarde = get_backup_manager().create_backup("before_repair_deezer_ids")
    print(f"\n💾 Backup : {sauvegarde}")
    observations = liens = reperes = 0
    for f in fautifs:
        r = dm.clear_track_deezer_id(f["track_id"], f["deezer_id"])
        observations += len(r["observations_retirees"])
        liens += r["liens_parution_retires"]
        if r["parution_reperee"]:
            reperes += 1
            print(f"   ⚠️ #{f['track_id']} : parution repère « {r['parution_reperee']} » laissée")
    print(f"\n✅ {len(fautifs)} id(s) retiré(s)")
    print(f"   {observations} observation(s) deezer supprimée(s), colonnes ré-arbitrées")
    print(f"   {liens} lien(s) de parution retiré(s), {reperes} repère(s) à traiter à la main")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Écrire (défaut : dry-run)")
    parser.add_argument("--artiste", help="Limiter à un artiste (nom exact en base)")
    parser.add_argument("--depuis", help="Reprendre le JSON d'un audit précédent")
    parser.add_argument("--json", help="Écrire le rapport complet dans ce fichier")
    parser.add_argument("--exclure", nargs="*", type=int, default=[], help="track_id à épargner")
    parser.add_argument("--pause", type=float, default=0.2, help="Pause entre deux requêtes")
    args = parser.parse_args()

    dm = DataManager()
    if args.depuis:
        ecarts = depuis_json(args.depuis)
    else:
        rapport = auditer(dm, args.artiste, args.pause)
        ecarts = rapport["ecarts"]
        if args.json:
            with open(args.json, "w", encoding="utf-8") as f:
                json.dump(rapport, f, ensure_ascii=False, indent=2)
            print(f"📝 Rapport : {args.json}")

    fautifs = [e for e in ecarts if _fautif(e)]
    autres = [e for e in ecarts if not _fautif(e)]
    if args.exclure:
        avant = len(fautifs)
        fautifs = [f for f in fautifs if f["track_id"] not in set(args.exclure)]
        print(f"({avant - len(fautifs)} ligne(s) épargnée(s) par --exclure)")

    imprimer(fautifs, autres)
    if not fautifs:
        print("\nRien à retirer.")
        return 0
    if not args.apply:
        print("\n(dry-run — relancer avec --apply pour écrire)")
        return 0

    attendu = str(len(fautifs))
    reponse = input(f"\nTaper {attendu} pour confirmer le retrait : ").strip()
    if reponse != attendu:
        print("Annulé.")
        return 1
    appliquer(dm, fautifs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
