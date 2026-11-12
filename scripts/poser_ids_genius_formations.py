"""Pose l'ID Genius des formations (e65) depuis les morceaux de leurs membres.

La liste `/artists/{membre}/songs` gardée dans le brut donne l'artiste
principal de chaque morceau avec son ID : un morceau du membre dont l'artiste
principal porte le nom de la formation la désigne, par construction
(`src.utils.formations_genius`). Même geste que la fin d'étape discographie
(`services.formations_genius.poser_ids_genius`), pour les liens déjà en base.

Hors ligne par défaut. `--reseau` ajoute le repli (recherche Genius par nom,
gardée seulement si un morceau du candidat crédite le membre) et lit la liste
des formations dont la nature reste à décider (gardée dans le brut, pour la
règle de nature).

    python scripts/poser_ids_genius_formations.py             # à blanc (défaut)
    python scripts/poser_ids_genius_formations.py --apply     # backup + écriture
    python scripts/poser_ids_genius_formations.py --reseau    # repli et listes (API Genius)
    python scripts/poser_ids_genius_formations.py --base copie.db --brut brut.db
"""

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    parser.add_argument("--reseau", action="store_true", help="repli et listes par l'API Genius")
    parser.add_argument("--base", type=Path, help="autre base que data/music_credits.db")
    parser.add_argument("--brut", type=Path, help="autre brut que data/brut/brut.db")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    import src.utils.data_manager as module_dm
    from src.services.formations_genius import poser_ids_genius
    from src.utils.logger import configurer_journaux

    configurer_journaux()
    if args.base:
        module_dm.DATABASE_URL = f"sqlite:///{args.base.resolve().as_posix()}"
    dm = module_dm.DataManager()
    print(f"Base : {dm.db_path}")
    genius = None
    if args.reseau:
        from src.api.genius_api import GeniusAPI

        genius = GeniusAPI()

    if args.apply and not _sauvegarder(dm, args, "before_ids_genius_formations"):
        return 1
    poses, signales, lues = [], [], []
    for artiste in dm.artistes_de_la_base():
        if not any(r.kind == "member_of" for r in dm.get_artist_relations(artiste.id, None)):
            continue
        artiste = dm.get_artist_by_name(artiste.name)  # avec son ID Genius
        bilan = poser_ids_genius(dm, artiste, genius, chemin_brut=args.brut, appliquer=args.apply)
        poses += [f"{artiste.name} : {p}" for p in bilan.poses]
        signales += [f"{artiste.name} : {s}" for s in bilan.signales]
        lues += bilan.listes_lues
    print(f"\n{len(poses)} ID posé(s) :")
    for ligne in poses:
        print(f"  {ligne}")
    print(f"\n{len(signales)} formation(s) sans ID :")
    for ligne in signales:
        print(f"  {ligne}")
    if lues:
        print(f"\n{len(lues)} liste(s) de formation lue(s) : {', '.join(lues)}")
    if not args.apply:
        print("\n(à blanc — relancer avec --apply pour écrire)")
    return 0


def _sauvegarder(dm, args, nom: str) -> bool:
    from src.utils.database_backup import DatabaseBackupManager, get_backup_manager

    sauvegarde = (
        DatabaseBackupManager(
            db_path=str(dm.db_path), backup_dir=str(Path(dm.db_path).parent / "backups")
        )
        if args.base
        else get_backup_manager()
    )
    backup = sauvegarde.create_backup(nom)
    if not backup:
        print("❌ Backup impossible — abandon.")
        return False
    print(f"💾 Backup : {backup}")
    return True


if __name__ == "__main__":
    sys.exit(main())
