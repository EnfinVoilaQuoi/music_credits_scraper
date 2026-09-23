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
from src.utils.deezer_audit import (
    isrc_partages,
    lignes_a_verifier,
    lignes_isrc_a_verifier,
    noms_acceptes_par_artiste,
    orphelins_reccobeats,
    verifier_lignes,
    verifier_lignes_isrc,
)


def _fautif(ecart: dict) -> bool:
    return bool(ecart.get("variante_etrangere"))


def auditer(dm: DataManager, artiste: str | None, pause: float) -> dict:
    lignes = lignes_a_verifier(dm.engine, artiste, None)
    print(f"🔎 {len(lignes)} identifiant(s) Deezer à vérifier sur /track/{{id}}\n")

    def _progression(faits, total):
        if faits % 50 == 0:
            print(f"   … {faits}/{total}")

    rapport = verifier_lignes(
        lignes,
        noms_par_artiste=noms_acceptes_par_artiste(dm, lignes),
        pause=pause,
        progression=_progression,
    )
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


def auditer_orphelins(dm: DataManager) -> list[dict]:
    lignes = orphelins_reccobeats(dm.engine)
    print(f"🔎 {len(lignes)} orphelin(s) ReccoBeats (reliquat de l'audit du 2026-09-22)\n")
    for ligne in lignes:
        print(
            f"  #{ligne['id']:<6} {ligne['artiste']} — « {ligne['title']} » "
            f"(bpm={ligne['bpm']}, duration={ligne['duration']})"
        )
    return lignes


def appliquer_orphelins(dm: DataManager, lignes: list[dict]) -> None:
    sauvegarde = get_backup_manager().create_backup("before_repair_deezer_ids_orphelins")
    print(f"\n💾 Backup : {sauvegarde}")
    retirees = 0
    for ligne in lignes:
        r = dm.clear_orphan_reccobeats_measures(ligne["id"])
        retirees += len(r["reccobeats_retirees"])
    print(
        f"\n✅ {len(lignes)} morceau(x) nettoyé(s), {retirees} observation(s) ReccoBeats retirée(s)"
    )


def auditer_isrc(dm: DataManager, artiste: str | None, pause: float) -> dict:
    lignes = lignes_isrc_a_verifier(dm.engine, artiste, None)
    print(f"🔎 {len(lignes)} ISRC hérité(s) à vérifier sur /track/isrc:{{isrc}}\n")

    partages = isrc_partages(lignes)
    if partages:
        print(f"⚠️ {len(partages)} ISRC partagé(s) par des titres différents (même artiste) :")
        for isrc, groupe in partages.items():
            titres = ", ".join(f"« {g['title']} »" for g in groupe)
            print(f"   {isrc} — {groupe[0]['artiste']} : {titres}")
        print()

    def _progression(faits, total):
        if faits % 50 == 0:
            print(f"   … {faits}/{total}")

    rapport = verifier_lignes_isrc(
        lignes,
        noms_par_artiste=noms_acceptes_par_artiste(dm, lignes),
        pause=pause,
        progression=_progression,
    )
    rapport["partages"] = {isrc: [g["id"] for g in groupe] for isrc, groupe in partages.items()}
    print(
        f"   {rapport['verifies']} vérifié(s), {rapport['illisibles']} illisible(s), "
        f"{len(rapport['ecarts'])} écart(s)"
    )
    return rapport


def imprimer_isrc(fautifs: list[dict], autres: list[dict]) -> None:
    print(f"\n{'─' * 78}")
    print(f"{len(fautifs)} ISRC à retirer\n")
    for f in fautifs:
        d = f["deezer"]
        print(f"  #{f['track_id']:<6} {f['artiste']} — « {f['titre']} »")
        print(f"         isrc {f['isrc']} → « {d['title']} » — {d['artist']}  ({f['motif']})")
    if autres:
        print(f"\n{len(autres)} écart(s) SIGNALÉ(S) sans retrait (titre identique, ou durée) :")
        for f in autres:
            print(f"  #{f['track_id']:<6} {f['artiste']} — « {f['titre']} » : {f['motif'][:110]}")


def appliquer_isrc(dm: DataManager, fautifs: list[dict]) -> None:
    sauvegarde = get_backup_manager().create_backup("before_repair_deezer_ids_isrc")
    print(f"\n💾 Backup : {sauvegarde}")
    reccobeats = 0
    for f in fautifs:
        r = dm.clear_track_isrc(f["track_id"])
        reccobeats += len(r["reccobeats_retirees"])
    print(f"\n✅ {len(fautifs)} ISRC retiré(s), {reccobeats} mesure(s) ReccoBeats retirée(s)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Écrire (défaut : dry-run)")
    parser.add_argument("--artiste", help="Limiter à un artiste (nom exact en base)")
    parser.add_argument("--depuis", help="Reprendre le JSON d'un audit précédent")
    parser.add_argument("--json", help="Écrire le rapport complet dans ce fichier")
    parser.add_argument("--exclure", nargs="*", type=int, default=[], help="track_id à épargner")
    parser.add_argument("--pause", type=float, default=0.2, help="Pause entre deux requêtes")
    parser.add_argument(
        "--orphelins-reccobeats",
        action="store_true",
        help="Rattrapage : retire les mesures ReccoBeats résolues par un ISRC "
        "qui n'existe plus en base (aucun appel réseau)",
    )
    parser.add_argument(
        "--isrc",
        action="store_true",
        help="Audite les ISRC HÉRITÉS de l'ancien get_isrc (avant le gate), "
        "jamais vérifiés depuis — au lieu des deezer_id",
    )
    args = parser.parse_args()

    dm = DataManager()

    if args.isrc:
        if args.depuis:
            ecarts = depuis_json(args.depuis)
        else:
            rapport = auditer_isrc(dm, args.artiste, args.pause)
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

        imprimer_isrc(fautifs, autres)
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
        appliquer_isrc(dm, fautifs)
        return 0

    if args.orphelins_reccobeats:
        lignes = auditer_orphelins(dm)
        if not lignes:
            print("\nRien à retirer.")
            return 0
        if not args.apply:
            print("\n(dry-run — relancer avec --apply pour écrire)")
            return 0
        attendu = str(len(lignes))
        reponse = input(f"\nTaper {attendu} pour confirmer le retrait : ").strip()
        if reponse != attendu:
            print("Annulé.")
            return 1
        appliquer_orphelins(dm, lignes)
        return 0

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
