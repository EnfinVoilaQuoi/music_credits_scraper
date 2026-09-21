"""Retire les « crédits » qui sont des références à un autre morceau.

Genius affiche, à côté des crédits, les sections « Remixes », « Samples »,
« Songs That Interpolate », « Translations », « Is A Remix Of »… Le LLM les
avalait comme des personnes : « Dolce Camara by\u00a0Booba (Ft.\u00a0SDM) » en
« Other / DCR Is A Remix Of ». Mesuré le 2026-09-21 : 7 867 lignes sur 3 763
morceaux (Kanye 3 251, Travis 1 812). Le scraper les écarte désormais
(`genius_scraper_v3.est_relation_deguisee`) ; ce script nettoie l'existant.

Aucune règle réimplémentée : le prédicat est celui du scraper, l'écriture
celle du repository (`purger_credits_relations_deguisees`).

Usage :
    python scripts/purge_credits_relations.py            # dry-run (défaut)
    python scripts/purge_credits_relations.py --apply    # backup + écriture
"""

import argparse
import sys

if sys.platform == "win32" and "pytest" not in sys.modules:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.utils.data_manager import DataManager
from src.utils.database_backup import get_backup_manager


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Écrire (défaut : dry-run)")
    args = parser.parse_args()

    dm = DataManager()
    lignes, morceaux = dm.compter_credits_relations_deguisees()
    print(f"{lignes} « crédit(s) » relationnel(s) sur {morceaux} morceau(x)")
    if not lignes:
        print("Rien à faire.")
        return 0
    if not args.apply:
        print("(dry-run — relancer avec --apply pour écrire)")
        return 0
    print(f"💾 Backup : {get_backup_manager().create_backup('before_purge_credits_relations')}")
    retires = dm.purger_credits_relations_deguisees()
    print(f"✅ {retires} ligne(s) retirée(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
