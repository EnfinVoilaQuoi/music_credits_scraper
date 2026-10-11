"""Remplit les ISRC laissés vides alors qu'une observation en donne un.

Avant le 2026-10-11, l'ISRC suivait deux règles selon le chemin d'écriture
(remplir seulement / suivre le verdict) : 80 colonnes restaient vides. La règle
est désormais « remplir sans remplacer » partout ; ce script rattrape l'existant.

    python scripts/combler_isrc.py            # à blanc (défaut)
    python scripts/combler_isrc.py --apply    # backup + écriture
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
    a_combler = dm.combler_isrc_vides()
    for tid, titre, isrc in a_combler:
        print(f"   #{tid} « {titre} » ← {isrc}")
    print(f"\n{len(a_combler)} ISRC à remplir")
    if not args.apply or not a_combler:
        print("(à blanc — relancer avec --apply pour écrire)")
        return 0
    backup = get_backup_manager().create_backup("before_combler_isrc")
    if not backup:
        print("❌ Backup impossible — abandon.")
        return 1
    print(f"💾 Backup : {backup}")
    print(f"✅ {len(dm.combler_isrc_vides(appliquer=True))} ISRC remplis.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
