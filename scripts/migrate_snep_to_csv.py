"""Migration UNIQUE : ancien certifications.db (+ brut certif-.csv) → certif_snep.csv.

Seed le CSV canonique versionné à partir de la DB gitignorée (qui contient
l'historique accumulé, ~292 certifs de plus que le brut courant), puis fusionne
le brut (accumulation). À lancer une fois, avant de basculer le matcher sur le
CSV (chantier certifs, étape 1-C). La DB est retirée en fin de chantier.

Usage : python scripts/migrate_snep_to_csv.py
"""

import sys
from pathlib import Path

from src.config import DATA_PATH
from src.utils.snep_build import (
    bootstrap_rows_from_db,
    canonical_rows_from_raw,
    merge_canonical,
    read_raw_snep_csv,
    write_canonical_csv,
    write_meta,
)

SNEP_DIR = Path(DATA_PATH) / "certifications" / "snep"
DB_PATH = SNEP_DIR / "certifications.db"
RAW_PATH = SNEP_DIR / "certif-.csv"
CSV_PATH = SNEP_DIR / "certif_snep.csv"
META_PATH = SNEP_DIR / "certif_snep.meta.json"


def main() -> int:
    db_rows = bootstrap_rows_from_db(DB_PATH) if DB_PATH.exists() else []
    raw_rows = canonical_rows_from_raw(read_raw_snep_csv(RAW_PATH))
    merged = merge_canonical(db_rows, raw_rows)

    write_canonical_csv(merged, CSV_PATH)
    write_meta(META_PATH, source="MIGRATION", count=len(merged))

    print(f"DB      : {len(db_rows)} lignes")
    print(f"Brut    : {len(raw_rows)} lignes canoniques")
    print(f"Fusion  : {len(merged)} lignes → {CSV_PATH.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
