"""Réunit les parutions d'un artiste qui portent le même code-barres (UPC).

e54 (2026-10-14) empêche les nouveaux doublons, mais ceux d'avant restent deux
parutions : « A V L D » / « AVLD » de SDM, UPC 859733291991 sous deux ID Deezer
(et deux albums Apple, le second « distribution exclusive ADA France ») — une
seconde livraison, pas une autre édition. La plus ancienne parution garde
identifiants, liens et preuves ; l'autre disparaît.

    python scripts/fusionner_parutions_upc.py            # à blanc (défaut)
    python scripts/fusionner_parutions_upc.py --apply    # backup + écriture
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
    plan = dm.fusionner_parutions_meme_upc()
    for f in plan:
        absorbees = ", ".join(f"#{i} « {t} »" for i, t in f["absorbees"])
        print(f"   artiste {f['artist_id']} : #{f['garde']} « {f['titre']} » ← {absorbees}")
    print(f"\n{len(plan)} parution(s) à réunir")
    if not args.apply or not plan:
        print("(à blanc — relancer avec --apply pour écrire)")
        return 0
    backup = get_backup_manager().create_backup("before_fusionner_parutions_upc")
    if not backup:
        print("❌ Backup impossible — abandon.")
        return 1
    print(f"💾 Backup : {backup}")
    print(f"✅ {len(dm.fusionner_parutions_meme_upc(appliquer=True))} parution(s) réunie(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
