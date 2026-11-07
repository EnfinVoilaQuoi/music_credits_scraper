"""Retire les textes qui ne sont pas des paroles (`paroles_genius.paroles_factices`).

    python scripts/retirer_paroles_factices.py                 # à blanc (défaut)
    python scripts/retirer_paroles_factices.py --apply         # backup + écriture
    python scripts/retirer_paroles_factices.py --base copie.db # sur une autre base

« . », « TBD », « [? : Kanye West] », le seul titre : `has_lyrics` tombe à 0 par le
verdict de la colonne, et le texte part ici, sinon la fiche ne se relirait jamais
(`Lyrics.a_chercher` lit le texte). Non transcrites : constat daté, relu après 30 j,
sans toucher au LRC. Instrumentales (« Just beat ») : `constater_instrumental`, dont
le LRC part aussi. Une relecture de la page rend le texte si Genius en a un.
"""

import argparse
import sys
from collections import Counter
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    parser.add_argument("--base", type=Path, help="autre base que data/music_credits.db")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    import src.utils.data_manager as module_dm
    from src.utils.logger import configurer_journaux
    from src.utils.paroles_genius import INSTRUMENTAL

    configurer_journaux()
    if args.base:
        module_dm.DATABASE_URL = f"sqlite:///{args.base.resolve().as_posix()}"
    dm = module_dm.DataManager()
    print(f"Base : {dm.db_path}")

    cas = dm.paroles_factices_de_la_base()
    for c in sorted(cas, key=lambda c: (c["nature"], c["lyrics"].strip().lower())):
        lrc = " (a un LRC)" if c["lyrics_synced"] else ""
        print(f"  {c['nature']} #{c['id']} {c['title']} : {c['lyrics'].strip()[:70]!r}{lrc}")
    print(f"{len(cas)} fiche(s) : {dict(Counter(c['nature'] for c in cas))}")

    if not args.apply or not cas:
        print("(à blanc — relancer avec --apply pour écrire)")
        return 0
    if not _sauvegarder(dm, args, "before_retirer_paroles_factices"):
        return 1
    echecs = 0
    for c in cas:
        if c["nature"] == INSTRUMENTAL:
            ok = dm.constater_instrumental(c["id"])
        else:
            ok = dm.constater_paroles_factices(c["id"])
        echecs += 0 if ok else 1
    reste = dm.paroles_factices_de_la_base()
    print(f"✅ {len(cas) - echecs} fiche(s) traitée(s) ; reste {len(reste)}.")
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
