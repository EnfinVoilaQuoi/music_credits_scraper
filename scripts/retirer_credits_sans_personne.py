"""Retire les crédits dont le « nom » ne désigne personne : « Deleted Artist » (ce
que Genius affiche à la place d'un compte d'artiste supprimé), « Unknown », « N/A »…

    python scripts/retirer_credits_sans_personne.py                 # à blanc (défaut)
    python scripts/retirer_credits_sans_personne.py --apply         # backup + écriture
    python scripts/retirer_credits_sans_personne.py --base copie.db # sur une autre base

Le juge est `credit_roles.valeur_vide`, celui des lecteurs (pages et API Genius,
descriptions YouTube), qui les refusent à l'entrée depuis le 2026-11-15 (mesuré
ce jour : 4 « Deleted Artist », 1 « Unknown »). Garde : invariant d'audit
`credit_sans_personne`. Les autres crédits des fiches restent.
"""

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    parser.add_argument("--base", type=Path, help="autre base que data/music_credits.db")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    import src.utils.data_manager as module_dm
    from src.utils.logger import configurer_journaux

    configurer_journaux()
    if args.base:
        module_dm.DATABASE_URL = f"sqlite:///{args.base.resolve().as_posix()}"
    dm = module_dm.DataManager()
    print(f"Base : {dm.db_path}")

    cas = dm.credits_sans_personne()
    print(f"{len(cas)} crédit(s) à retirer, sur {len({c['track_id'] for c in cas})} fiche(s) :")
    for c in cas:
        print(f"  #{c['track_id']} {c['title']} — {c['role']} « {c['name']} » ({c['source']})")

    if not args.apply or not cas:
        print("(à blanc — relancer avec --apply pour écrire)")
        return 0
    if not _sauvegarder(dm, args, "before_retirer_credits_sans_personne"):
        return 1
    _, retires = dm.reclasser_credits({}, [c["id"] for c in cas])
    reste = dm.credits_sans_personne()
    print(f"✅ {retires} crédit(s) retiré(s) ; reste {len(reste)}.")
    return 0 if not reste else 2


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
