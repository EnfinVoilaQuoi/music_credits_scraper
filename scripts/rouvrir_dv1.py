"""Règle C (décision utilisateur 2026-11-19) : rouvrir les « ✓ Normal » de DV1.

    python scripts/rouvrir_dv1.py                 # à blanc (défaut)
    python scripts/rouvrir_dv1.py --apply         # backup + écriture
    python scripts/rouvrir_dv1.py --base copie.db # sur une autre base

DV1 (2026-10-31) marquait « normal » l'instrumental ou l'a cappella Deezer d'un
morceau connu : pas de fiche. La règle C la lève — une version qui peut avoir
des streams est une fiche secondaire. Chaque cas DV1 est rouvert
(`revue_auto.rouvrir_normal_de_regle` : verdict ôté, correction marquée défaite,
signalement remis) ; `python -m src.cli revue <artiste> --corriger` l'arbitre
ensuite selon les règles d'aujourd'hui. Les artistes à repasser sont listés.
"""

import argparse
import sys
from collections import Counter
from pathlib import Path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    parser.add_argument("--base", type=Path, help="autre base que data/music_credits.db")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    import src.utils.data_manager as module_dm
    from src.services.revue_auto import PREFIXE_REGLE, rouvrir_normal_de_regle
    from src.utils.database_backup import DatabaseBackupManager, get_backup_manager
    from src.utils.logger import configurer_journaux

    configurer_journaux()
    if args.base:
        module_dm.DATABASE_URL = f"sqlite:///{args.base.resolve().as_posix()}"
    dm = module_dm.DataManager()
    print(f"Base : {dm.db_path}")
    artistes = {a.id: a.name for a in dm.artistes_de_la_base()}
    a_rouvrir = [
        c
        for aid in artistes
        for c in dm.corrections_revue(aid)
        if c["detecteur"] == "deezer_version" and c["action"] == f"{PREFIXE_REGLE}normal"
    ]
    par_artiste = Counter(artistes[c["artist_id"]] for c in a_rouvrir)
    print(f"{len(a_rouvrir)} cas DV1 à rouvrir :")
    for nom, n in par_artiste.most_common():
        print(f"   {n:4}  {nom}")
    if not args.apply or not a_rouvrir:
        print("(à blanc — relancer avec --apply pour écrire)")
        return 0
    sauvegarde = (
        DatabaseBackupManager(
            db_path=str(dm.db_path), backup_dir=str(Path(dm.db_path).parent / "backups")
        )
        if args.base
        else get_backup_manager()
    )
    backup = sauvegarde.create_backup("before_rouvrir_dv1")
    if not backup:
        print("❌ Backup impossible — abandon (règle projet : jamais d'écriture sans backup).")
        return 1
    print(f"💾 Backup : {backup}")
    for c in a_rouvrir:
        rouvrir_normal_de_regle(dm, c)
    print(f"✅ {len(a_rouvrir)} cas rouverts. Puis, pour chaque artiste :")
    for nom in sorted(par_artiste):
        print(f'   python -m src.cli revue "{nom}" --corriger')
    return 0


if __name__ == "__main__":
    sys.exit(main())
