"""Copie le BRUT des sources (`data/brut/brut.db`) vers `BRUT_BACKUP_DIR`.

    python scripts/sauvegarder_brut.py                  # si la dernière copie a 7 jours
    python scripts/sauvegarder_brut.py --forcer         # maintenant
    python scripts/sauvegarder_brut.py --dossier E:/sauvegardes/brut --forcer

La même copie que celle de fin de run CLI (`src/persistence/brut_sauvegarde`) :
API backup de SQLite, 2 dernières gardées. Sans `--dossier`, il faut
`BRUT_BACKUP_DIR` dans le `.env`.
"""

import argparse
import sys
from pathlib import Path

from src import config
from src.persistence import brut
from src.persistence.brut_sauvegarde import copies, meme_disque, sauvegarder
from src.utils.logger import configurer_journaux


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dossier", help="destination (sinon BRUT_BACKUP_DIR)")
    parser.add_argument("--forcer", action="store_true", help="copier même si < 7 jours")
    args = parser.parse_args()
    configurer_journaux()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    dossier = Path(args.dossier) if args.dossier else config.BRUT_BACKUP_DIR
    if dossier is None:
        print("❌ Aucune destination : --dossier, ou BRUT_BACKUP_DIR dans le .env.")
        return 1
    if meme_disque(dossier, brut.CHEMIN):
        print(f"⚠️ {dossier} est sur le même disque que {brut.CHEMIN}")
    cible = sauvegarder(
        brut.CHEMIN, dossier, jours=config.settings.brut_backup_jours, forcer=args.forcer
    )
    if cible is None:
        derniere = copies(dossier)[:1]
        print(f"Rien à faire (dernière copie : {derniere[0][1] if derniere else 'aucune'}).")
        return 0
    print(f"💾 {cible} ({cible.stat().st_size / 1e6:.0f} Mo)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
