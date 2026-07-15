"""Contrôle READ-ONLY de la cohérence de la table `observations` (phases E4-E5).

Depuis E5c-2a, la triple écriture émet des observations PAR SOURCE depuis le vrai
flux d'enrichissement (une ligne `bpm` par source ayant voté), ce qui rompt
l'invariant strict « 1 observation par colonne legacy » de l'ère backfill (E4).
La table est aussi MIXTE transitoirement : lignes backfill « source combinée »
(`reccobeats+songbpm`) + lignes par source — inoffensif, la table est write-only
jusqu'à E6, et les lignes combinées seront nettoyées en E5c-2b.

Le contrôle passe donc à un invariant ROBUSTE, valable quel que soit le style de
source :
  1. aucune observation orpheline (track_id inexistant) ;
  2. aucune observation sur un champ inconnu ;
  3. `obs ⇒ colonne legacy présente` : tout morceau porteur d'une observation
     `bpm`/`key`/`mode` a la colonne legacy correspondante non nulle (la triple
     écriture pose les deux ensemble, dans une seule transaction).

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

# Champs scalaires attendus dans `observations` et leur colonne legacy « miroir »
# (le nom de colonne est cité verbatim : `key`/`mode` sont sensibles au dialecte).
_KNOWN_FIELDS = {
    "bpm": "bpm",
    "key": '"key"',
    "mode": '"mode"',
    "lyrics_synced": "lyrics_synced",
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

        # (1) Orphelines : track_id sans morceau (FK non imposée, PRAGMA off).
        orphans = conn.execute(
            "SELECT COUNT(*) FROM observations o "
            "WHERE NOT EXISTS (SELECT 1 FROM tracks t WHERE t.id = o.track_id)"
        ).fetchone()[0]
        if orphans:
            ok = False
            print(f"  ❌ {orphans} observation(s) orpheline(s) (track_id inexistant)")
        else:
            print("  ✅ aucune observation orpheline")

        # (2) Champs inconnus.
        placeholders = ",".join("?" * len(_KNOWN_FIELDS))
        unknown = conn.execute(
            f"SELECT DISTINCT field FROM observations WHERE field NOT IN ({placeholders})",  # noqa: S608
            tuple(_KNOWN_FIELDS),
        ).fetchall()
        if unknown:
            ok = False
            print(f"  ❌ champ(s) inconnu(s) : {', '.join(r[0] for r in unknown)}")
        else:
            print("  ✅ tous les champs sont connus")

        # (3) obs ⇒ colonne legacy présente (triple écriture cohérente).
        for field, column in _KNOWN_FIELDS.items():
            dangling = conn.execute(
                f"SELECT COUNT(*) FROM observations o "  # noqa: S608 (colonne d'une whitelist)
                f"JOIN tracks t ON t.id = o.track_id "
                f"WHERE o.field = ? AND t.{column} IS NULL",
                (field,),
            ).fetchone()[0]
            status = "✅" if dangling == 0 else "❌"
            if dangling:
                ok = False
            print(f"  {status} {field:5} : {dangling} observation(s) sans colonne legacy")

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
