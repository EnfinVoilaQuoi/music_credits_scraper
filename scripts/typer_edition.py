"""Type une édition d'un projet « second disque » (point à elle dans la Timeline
du studio), ou l'oublie : elle redevient un « bonus » (décision utilisateur
2026-11-25, gardée dans `acceptations`).

    python scripts/typer_edition.py <artiste>                       # projets à plusieurs éditions
    python scripts/typer_edition.py <artiste> --second 11476        # à blanc
    python scripts/typer_edition.py <artiste> --second 11476 --apply
    python scripts/typer_edition.py <artiste> --oublier 11476 --apply
"""

import argparse
import sys


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("artiste")
    parser.add_argument("--second", type=int, nargs="*", default=[], help="ID d'éditions")
    parser.add_argument("--oublier", type=int, nargs="*", default=[], help="ID d'éditions")
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    from src.utils.data_manager import DataManager
    from src.utils.database_backup import get_backup_manager
    from src.utils.logger import configurer_journaux

    configurer_journaux()
    dm = DataManager()
    artiste = dm.get_artist_by_name(args.artiste)
    if artiste is None:
        print(f"❌ Artiste inconnu : {args.artiste}")
        return 1
    demandes = {**dict.fromkeys(args.second, True), **dict.fromkeys(args.oublier, False)}
    vus = set()
    for projet in dm.get_projets_for_artist(artiste.id):
        ids = {e["release_id"] for e in projet["editions"]}
        if len(ids) < 2 or (demandes and not ids & set(demandes)):
            continue
        print(f"« {projet['titre']} »")
        for e in projet["editions"]:
            vus.add(e["release_id"])
            marque = {True: " → second disque", False: " → bonus"}.get(
                demandes.get(e["release_id"]), ""
            )
            print(
                f"   {e['release_id']:6} {e['date'] or '?':10} {len(e['track_ids']):3} titres  "
                f"{e['type']:14} « {e['titre']} »{marque}"
            )
    if inconnus := set(demandes) - vus:
        print(f"❌ Édition(s) hors d'un projet à plusieurs éditions : {sorted(inconnus)}")
        return 1
    if not demandes or not args.apply:
        if demandes:
            print("\n(à blanc — relancer avec --apply pour écrire)")
        return 0
    backup = get_backup_manager().create_backup("before_typer_edition")
    if not backup:
        print("❌ Backup impossible — abandon (règle projet : jamais d'écriture sans backup).")
        return 1
    print(f"💾 Backup : {backup}")
    n = sum(dm.poser_second_disque(artiste.id, r, oui) for r, oui in demandes.items())
    print(f"✅ {n} édition(s) changée(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
