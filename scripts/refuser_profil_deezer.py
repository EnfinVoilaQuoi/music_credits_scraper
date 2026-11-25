"""Refuse un PROFIL Deezer pour un artiste : « pas sur Deezer » (2026-11-14).

    python scripts/refuser_profil_deezer.py "The Graduates" 392414            # à blanc
    python scripts/refuser_profil_deezer.py "The Graduates" 392414 --apply    # backup + écriture
    python scripts/refuser_profil_deezer.py "The Graduates" --oublier 392414 --apply
    python scripts/refuser_profil_deezer.py "The Graduates" --liste

Un homonyme seul au nom exact (The Graduates, La Confrérie) ou un profil VIDE
(Delusional Thomas) laissait l'étape identité « ambiguë » à chaque run.
L'oracle `deezer_identite` écarte les profils refusés ; sans autre candidat,
l'artiste est « pas sur Deezer » et l'étape continue sans Deezer. Un AUTRE
profil apparu plus tard reste candidat, jugé par son recouvrement avec la base.
Un profil mémorisé (`artists.deezer_id`) qu'on refuse est effacé.
"""

import argparse
import sys
from pathlib import Path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("artiste")
    parser.add_argument("profil", type=int, nargs="?", help="ID d'artiste Deezer à refuser")
    parser.add_argument("--nom", default="", help="nom du profil, pour mémoire")
    parser.add_argument("--oublier", type=int, help="lève le refus de ce profil")
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
    refuses = cf.profils_deezer_refuses(artiste.name)
    if args.liste:
        print(f"Profils refusés de {artiste.name} : {sorted(refuses) or 'aucun'}")
        return 0
    if not args.profil and not args.oublier:
        parser.error("un profil à refuser, ou --oublier <profil>")

    if args.oublier:
        print(f"Refus du profil {args.oublier} de {artiste.name} à lever")
    else:
        efface = artiste.deezer_id and int(artiste.deezer_id) == args.profil
        print(
            f"Profil {args.profil} refusé pour {artiste.name}"
            + (" (déjà)" if args.profil in refuses else "")
            + (" ; l'ID Deezer mémorisé de l'artiste sera effacé" if efface else "")
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
    if not sauvegarde.create_backup("before_refuser_profil_deezer"):
        print("❌ Backup impossible — abandon (règle projet : jamais d'écriture sans backup).")
        return 1
    if args.oublier:
        fait = cf.oublier_profil_deezer_refuse(artiste.name, args.oublier)
        print("✅ Refus levé" if fait else "Rien à lever")
        return 0
    cf.memoriser_profil_deezer_refuse(artiste.name, args.profil, args.nom or artiste.name)
    if artiste.deezer_id and int(artiste.deezer_id) == args.profil:
        dm.update_artist_deezer_id(artiste.id, None)
        # Les signalements lus sur ce profil partent avec lui, sans attendre
        # l'étape identité (Go Getters, 14 cas restés au panneau, 2026-11-25).
        from src.services import ecarts_deezer

        artiste.deezer_id = None
        ecarts_deezer.enregistrer_signalements(
            dm, artiste, ecarts_deezer.BilanEcarts(complete=True)
        )
    print("✅ Profil refusé")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
