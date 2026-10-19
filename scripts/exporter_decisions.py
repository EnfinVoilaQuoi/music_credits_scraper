"""Exporte les décisions humaines de la base en JSON versionnable (2026-10-19).

Tables `refus`, `acceptations`, `corrections`, `kworb_decisions`,
`revue_verdicts` → un fichier `<table>.json` chacune, une entrée par ligne
(`src/services/decisions.py`). Le même export est écrit en fin de run CLI quand
`DECISIONS_DIR` est réglé. Lecture seule sur la base.

    python scripts/exporter_decisions.py                 # vers DECISIONS_DIR
    python scripts/exporter_decisions.py --dossier X     # ailleurs
    python scripts/exporter_decisions.py --base autre.db --dossier X
"""

import argparse
import sys

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src import config
from src.services import decisions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dossier", help="dossier d'export (défaut : DECISIONS_DIR)")
    parser.add_argument("--base", help="fichier SQLite à lire (défaut : la base du projet)")
    args = parser.parse_args()

    dossier = args.dossier or config.DECISIONS_DIR
    if not dossier:
        print("❌ Aucun dossier : donner --dossier ou régler DECISIONS_DIR (.env)")
        return 1
    if args.base:
        from src.utils.db import Database

        engine = Database(args.base).engine
    else:
        from src.utils.data_manager import DataManager

        engine = DataManager().engine
    for table, (n, change) in decisions.exporter(engine, dossier).items():
        print(f"{table:<16} {n:>5} entrées  {'réécrit' if change else 'inchangé'}")
    print(f"→ {dossier}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
