"""Contrôle READ-ONLY de la cohérence de la table `observations` (phase E4).

Vérifie que le backfill (et, plus tard, la triple écriture E5) respecte
l'invariant : une observation `bpm` par `bpm_source` non nul, une `key`/`mode`
par champ non nul dont `key_mode_source` est renseigné — et aucune observation
orpheline (track_id inexistant).

    python scripts/check_observations.py            # base réelle (config)
    python scripts/check_observations.py --db X.db  # une autre base (copie)

Code de sortie 1 si un écart est détecté (utilisable en CI / après migration).
N'écrit JAMAIS dans la base.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

if "pytest" not in sys.modules:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.config import DATABASE_URL

# Invariant par champ scalaire E4 : (SQL de comptage attendu depuis `tracks`).
# key/mode partagent `key_mode_source` mais comptent par valeur non nulle.
_EXPECTED = {
    "bpm": "SELECT COUNT(*) FROM tracks WHERE bpm_source IS NOT NULL AND bpm IS NOT NULL",
    "key": 'SELECT COUNT(*) FROM tracks WHERE key_mode_source IS NOT NULL AND "key" IS NOT NULL',
    "mode": 'SELECT COUNT(*) FROM tracks WHERE key_mode_source IS NOT NULL AND "mode" IS NOT NULL',
}


def check(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        has_obs = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='observations'"
        ).fetchone()
        if not has_obs:
            print("❌ Table `observations` absente (migration e4 non appliquée ?)")
            return 1

        ok = True
        for field, expected_sql in _EXPECTED.items():
            got = conn.execute(
                "SELECT COUNT(*) FROM observations WHERE field = ?", (field,)
            ).fetchone()[0]
            expected = conn.execute(expected_sql).fetchone()[0]
            status = "✅" if got == expected else "❌"
            if got != expected:
                ok = False
            print(f"  {status} {field:5} : {got} observation(s) / {expected} attendue(s)")

        # Observations orphelines (track_id sans morceau) — la FK n'est pas
        # imposée (PRAGMA foreign_keys désactivé), donc on vérifie à la main.
        orphans = conn.execute(
            "SELECT COUNT(*) FROM observations o "
            "WHERE NOT EXISTS (SELECT 1 FROM tracks t WHERE t.id = o.track_id)"
        ).fetchone()[0]
        if orphans:
            ok = False
            print(f"  ❌ {orphans} observation(s) orpheline(s) (track_id inexistant)")
        else:
            print("  ✅ aucune observation orpheline")

        total = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        print(f"\n{'✅ Cohérent' if ok else '❌ Incohérent'} — {total} observation(s) au total.")
        return 0 if ok else 1
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Contrôle read-only de la table observations")
    parser.add_argument("--db", metavar="CHEMIN", help="base à contrôler (défaut : config)")
    args = parser.parse_args()

    db_path = args.db or DATABASE_URL.replace("sqlite:///", "")
    if not Path(db_path).exists():
        print(f"❌ Base introuvable : {db_path}")
        return 1
    print(f"Contrôle observations sur : {db_path}\n")
    return check(db_path)


if __name__ == "__main__":
    raise SystemExit(main())
