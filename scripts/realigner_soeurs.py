"""Réaligne les lignes SŒURS (même page Genius chez plusieurs artistes) :
rejoue la synchronisation de `save_track`, dont la règle « la lecture la plus
récente de la page l'emporte » (anecdotes, relations, paroles — décision
utilisateur 2026-10-12), puis recalcule les colonnes dérivées des membres.

    python scripts/realigner_soeurs.py            # à blanc (défaut)
    python scripts/realigner_soeurs.py --apply    # backup + écriture

Chantier A, lot 3 (`TrackRepository.realigner_soeurs`). Mesure avant/après :
invariant d'audit `soeurs_divergentes`.
"""

import argparse
import sys

from src.utils.data_manager import DataManager
from src.utils.database_backup import get_backup_manager
from src.utils.logger import configurer_journaux


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    args = parser.parse_args()
    configurer_journaux()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    dm = DataManager()
    changes = dm.realigner_soeurs()
    print(
        f"{len(changes)} famille(s) changent, {sum(changes.values())} ligne(s) de données "
        "de page réécrite(s)"
    )
    if not args.apply:
        print("(à blanc — relancer avec --apply pour écrire)")
        return 0
    if not changes:
        return 0
    backup = get_backup_manager().create_backup("before_realigner_soeurs")
    print(f"💾 Backup : {backup}")
    dm.realigner_soeurs(appliquer=True)
    print(f"✅ {len(changes)} famille(s) réalignée(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
