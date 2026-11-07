"""Rattrapage de l'invariant `orphelin` : l'historique d'usage d'une fiche disparue.

    python scripts/dissocier_echecs_orphelins.py                 # à blanc (défaut)
    python scripts/dissocier_echecs_orphelins.py --apply         # backup + écriture
    python scripts/dissocier_echecs_orphelins.py --base copie.db # sur une autre base

`delete_track` laissait `source_usage_failures.track_id` en place (1 ligne le
2026-11-06, fiche 13406). L'historique de santé d'une source survit à la fiche :
la ligne reste, elle ne la désigne plus.
"""

import argparse
import sys
from pathlib import Path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    parser.add_argument("--base", type=Path, help="autre base que data/music_credits.db")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    import src.utils.data_manager as module_dm
    from src.utils.database_backup import DatabaseBackupManager, get_backup_manager
    from src.utils.logger import configurer_journaux

    configurer_journaux()
    if args.base:
        module_dm.DATABASE_URL = f"sqlite:///{args.base.resolve().as_posix()}"
    dm = module_dm.DataManager()
    print(f"Base : {dm.db_path}")
    orphelines = dm.echecs_d_usage_orphelins()
    print(f"{len(orphelines)} ligne(s) d'historique sans fiche : {orphelines}")
    if not args.apply or not orphelines:
        print("(à blanc — relancer avec --apply pour écrire)")
        return 0
    sauvegarde = (
        DatabaseBackupManager(
            db_path=str(dm.db_path), backup_dir=str(Path(dm.db_path).parent / "backups")
        )
        if args.base
        else get_backup_manager()
    )
    backup = sauvegarde.create_backup("before_dissocier_echecs_orphelins")
    if not backup:
        print("❌ Backup impossible — abandon (règle projet : jamais d'écriture sans backup).")
        return 1
    print(f"💾 Backup : {backup}")
    print(f"✅ {dm.dissocier_echecs_d_usage_orphelins()} ligne(s) dissociée(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
