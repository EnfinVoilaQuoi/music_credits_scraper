"""Refuse un PROFIL Spotify d'artiste : un homonyme (2026-11-16).

    python scripts/refuser_profil_spotify.py "The Classmates" 5156YRN7cvcszG3asLPcTF            # à blanc
    python scripts/refuser_profil_spotify.py "The Classmates" 5156YRN7cvcszG3asLPcTF --apply    # backup + écriture
    python scripts/refuser_profil_spotify.py "The Classmates" --oublier 5156YRN7cvcszG3asLPcTF --apply
    python scripts/refuser_profil_spotify.py "The Classmates" --liste

Le vote de l'étape identité (`update_kworb._vote_artist_spotify_id`) retombe sur
une recherche PAR NOM quand les fiches n'ont pas d'ID Spotify : un artiste absent
de Spotify (le duo The Classmates de Travis Scott) reçoit alors le profil d'un
homonyme (un groupe de reprises rock). Le refus l'écarte du vote et de la
recherche ; le profil mémorisé (`artists.spotify_id`) et ses lignes
`artist_spotify_ids` sont retirés. Un AUTRE profil reste candidat.
"""

import argparse
import sys
from pathlib import Path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("artiste")
    parser.add_argument("profil", nargs="?", help="ID d'artiste Spotify à refuser")
    parser.add_argument("--nom", default="", help="nom du profil, pour mémoire")
    parser.add_argument("--oublier", help="lève le refus de ce profil")
    parser.add_argument("--liste", action="store_true", help="profils refusés de l'artiste")
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    parser.add_argument("--base", type=Path, help="autre base que data/music_credits.db")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    import src.utils.data_manager as module_dm
    from src.utils import corrections_fiches as cf
    from src.utils.database_backup import DatabaseBackupManager, get_backup_manager
    from src.utils.logger import configurer_journaux

    configurer_journaux()
    if args.base:
        module_dm.DATABASE_URL = f"sqlite:///{args.base.resolve().as_posix()}"
    dm = module_dm.DataManager()
    print(f"Base : {dm.db_path}")
    artiste = dm.get_artist_by_name(args.artiste)
    if artiste is None:
        print(f"❌ Artiste inconnu : {args.artiste}")
        return 1
    refuses = cf.profils_spotify_refuses(artiste.name)
    if args.liste:
        print(f"Profils Spotify refusés de {artiste.name} : {sorted(refuses) or 'aucun'}")
        return 0
    if not args.profil and not args.oublier:
        parser.error("un profil à refuser, ou --oublier <profil>")

    if args.oublier:
        print(f"Refus du profil Spotify {args.oublier} de {artiste.name} à lever")
    else:
        efface = artiste.spotify_id == args.profil
        print(
            f"Profil Spotify {args.profil} refusé pour {artiste.name}"
            + (" (déjà)" if args.profil in refuses else "")
            + (" ; l'ID Spotify mémorisé de l'artiste sera effacé" if efface else "")
        )
    if not args.apply:
        print("(à blanc — relancer avec --apply pour écrire)")
        return 0

    sauvegarde = (
        DatabaseBackupManager(
            db_path=str(dm.db_path), backup_dir=str(Path(dm.db_path).parent / "backups")
        )
        if args.base
        else get_backup_manager()
    )
    if not sauvegarde.create_backup("before_refuser_profil_spotify"):
        print("❌ Backup impossible — abandon (règle projet : jamais d'écriture sans backup).")
        return 1
    if args.oublier:
        fait = cf.oublier_profil_spotify_refuse(artiste.name, args.oublier)
        print("✅ Refus levé" if fait else "Rien à lever")
        return 0
    cf.memoriser_profil_spotify_refuse(artiste.name, args.profil, args.nom or artiste.name)
    retire = dm.retirer_profil_spotify(artiste.id, args.profil)
    print("✅ Profil refusé" + (" et retiré de l'artiste" if retire else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
