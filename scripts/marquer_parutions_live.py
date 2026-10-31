"""Pose `releases.mb_live` (e64) depuis les recherches MusicBrainz du brut.

La recherche par code-barres rend le type secondaire « Live » du
release-group ; avant e64 il n'était pas gardé. Ce script le relit dans le
brut (`src.utils.parutions_live`), hors ligne, pour les parutions déjà en
base. La famille d'audit `studio_sur_live` montre ensuite les fiches studio
rattachées à un album live (MC Solaar, *Le tour de la question*).

    python scripts/marquer_parutions_live.py                 # à blanc (défaut)
    python scripts/marquer_parutions_live.py --apply         # backup + écriture
    python scripts/marquer_parutions_live.py --base copie.db # sur une autre base
"""

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    parser.add_argument("--base", type=Path, help="autre base que data/music_credits.db")
    parser.add_argument("--brut", type=Path, help="autre brut que data/brut/brut.db")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    import src.utils.data_manager as module_dm
    from src.utils import parutions_live
    from src.utils.logger import configurer_journaux

    configurer_journaux()
    if args.base:
        module_dm.DATABASE_URL = f"sqlite:///{args.base.resolve().as_posix()}"
    dm = module_dm.DataManager()
    print(f"Base : {dm.db_path}")

    live_par_upc, upc_par_album = parutions_live.lire_le_brut(args.brut)
    parutions = dm.parutions_pour_live()
    cas = parutions_live.a_marquer(parutions, live_par_upc, upc_par_album)
    titres = {p["id"]: p for p in parutions}
    print(
        f"{len(live_par_upc)} code(s)-barres lus chez MusicBrainz "
        f"({sum(live_par_upc.values())} live) ; {len(parutions)} parution(s) : "
        f"{len(cas)} à marquer, dont {sum(cas.values())} live"
    )
    for rid, live in sorted(cas.items()):
        if live:
            print(f"  live : parution {rid} (UPC {titres[rid]['upc'] or '—'})")

    if not args.apply or not cas:
        print("(à blanc — relancer avec --apply pour écrire)")
        return 0
    if not _sauvegarder(dm, args, "before_marquer_parutions_live"):
        return 1
    print(f"✅ {dm.marquer_parutions_live(cas)} parution(s) marquée(s).")
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
