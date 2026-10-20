"""Recopie une sélection des sauvegardes de la base vers un AUTRE disque.

    python scripts/copier_sauvegardes_hors_disque.py                # à blanc (défaut)
    python scripts/copier_sauvegardes_hors_disque.py --apply
    python scripts/copier_sauvegardes_hors_disque.py --dossier D:/x --apply

Sélection (`src/utils/sauvegardes_hors_disque`) : la dernière de chaque jour sur
7 jours + la dernière de chaque semaine sur 4 semaines ; le dossier externe en
est le miroir. Sans `--dossier`, il faut `BASE_BACKUP_EXTERNE_DIR` (.env).
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

from src import config
from src.utils.database_backup import _DEFAULT_BACKUP_DIR
from src.utils.logger import configurer_journaux
from src.utils.sauvegardes_hors_disque import synchroniser


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dossier", help="destination (sinon BASE_BACKUP_EXTERNE_DIR)")
    parser.add_argument("--apply", action="store_true", help="copier (sinon à blanc)")
    args = parser.parse_args()
    configurer_journaux()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    dest = Path(args.dossier) if args.dossier else config.BASE_BACKUP_EXTERNE_DIR
    if dest is None:
        print("❌ Aucune destination : --dossier, ou BASE_BACKUP_EXTERNE_DIR dans le .env.")
        return 1
    plan = synchroniser(Path(_DEFAULT_BACKUP_DIR), dest, appliquer=args.apply)

    def ligne(p: Path) -> str:
        return f"   {p.name}  ({datetime.fromtimestamp(p.stat().st_mtime):%Y-%m-%d %H:%M})"

    verbe = "copiée(s)" if args.apply else "à copier"
    print(f"{len(plan.a_copier)} {verbe} ({plan.octets_a_copier() / 1e9:.1f} Go) → {dest}")
    for p in plan.a_copier:
        print(ligne(p))
    print(f"{len(plan.a_retirer)} {'retirée(s)' if args.apply else 'à retirer'} de {dest}")
    for p in plan.a_retirer:
        print(f"   {p.name}")
    print(f"{len(plan.deja_la)} déjà là")
    if not args.apply:
        print("(à blanc — relancer avec --apply pour copier)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
