"""Réimporte en base les décisions exportées par `exporter_decisions.py` (2026-10-19).

Import ADDITIF (`src/services/decisions.py`) : insère ce qui manque, ne
remplace ni n'efface rien ; une entrée dont la clé existe avec un autre contenu
est montrée, pas écrasée ; une décision Kworb ou un verdict de revue dont la
fiche ou l'artiste manque à la base reste en attente (relancer après avoir
reconstruit la discographie). Passage à blanc par défaut ; `--apply` fait une
sauvegarde de la base, puis écrit tout dans une seule transaction.

    python scripts/importer_decisions.py                       # à blanc, DECISIONS_DIR
    python scripts/importer_decisions.py --dossier X --apply   # backup + écriture
    python scripts/importer_decisions.py --base neuve.db --apply
"""

import argparse
import sys

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src import config
from src.services import decisions
from src.utils.database_backup import DatabaseBackupManager


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dossier", help="dossier de l'export (défaut : DECISIONS_DIR)")
    parser.add_argument("--base", help="fichier SQLite visé (défaut : la base du projet)")
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    parser.add_argument("--detail", type=int, default=5, help="cas montrés par table")
    args = parser.parse_args()

    dossier = args.dossier or config.DECISIONS_DIR
    if not dossier:
        print("❌ Aucun dossier : donner --dossier ou régler DECISIONS_DIR (.env)")
        return 1
    try:
        donnees = decisions.charger(dossier)
    except decisions.ExportIllisible as e:
        print(f"❌ Export illisible, rien n'est importé : {e}")
        return 1
    if args.base:
        from src.utils.db import Database

        db = Database(args.base)
        engine, chemin = db.engine, db.db_path
    else:
        from src.utils.data_manager import DataManager

        dm = DataManager()
        engine, chemin = dm.engine, dm.db_path
    print(f"Base : {chemin}\nExport : {dossier}\n")
    plans = decisions.a_blanc(engine, donnees)
    print(decisions.resume(plans, detail=args.detail))
    a_inserer = sum(len(p.a_inserer) for p in plans.values())
    if not args.apply:
        print("\n(à blanc — relancer avec --apply pour écrire)")
        return 0
    if not a_inserer:
        print("\nRien à insérer.")
        return 0
    sauvegarde = DatabaseBackupManager(db_path=chemin).create_backup("before_importer_decisions")
    if sauvegarde is None:
        print("❌ Sauvegarde impossible : rien n'est écrit")
        return 1
    print(f"\n💾 Backup : {sauvegarde}")
    plans = decisions.importer(engine, donnees)
    print(f"✅ Inséré : { {t: len(p.a_inserer) for t, p in plans.items() if p.a_inserer} }")
    return 0


if __name__ == "__main__":
    sys.exit(main())
