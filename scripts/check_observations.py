"""Contrôle READ-ONLY de la cohérence de la table `observations`.

Depuis E7-D2 (drop des colonnes audio), les observations sont l'UNIQUE source de
vérité de l'audio (bpm/key/mode/…). Ce contrôle vérifie trois invariants,
valables quel que soit le style de source :
  1. aucune observation orpheline (track_id inexistant) ;
  2. aucune observation sur un champ inconnu ;
  3. `obs ⇒ colonne présente` — SEULEMENT pour les champs encore écrits en
     colonne (lyrics_synced/streams, triple écriture maintenue). L'audio n'a plus
     de colonne miroir (droppée), l'invariant ne s'y applique donc pas.

    python scripts/check_observations.py            # base réelle (config)
    python scripts/check_observations.py --db X.db  # une autre base (copie)
    python scripts/check_observations.py --sizes    # volume par champ (perf E7d)

Code de sortie 1 si un écart est détecté (utilisable en CI / après migration).
`--sizes` n'exécute PAS les invariants : il ne fait qu'un GROUP BY read-only pour
jauger le poids par champ (cible : `lyrics_synced`, LRC bruts). N'écrit JAMAIS.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

if "pytest" not in sys.modules:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.config import DATABASE_URL

# Champs scalaires attendus dans `observations`. bpm/key/mode/bpm_alt/time_signature
# /reccobeats_resolution sont désormais SANS colonne miroir (droppées E7-D2) : ils
# restent des champs d'observation valides (la vérité audio y vit), mais ne sont
# plus contrôlés par l'invariant « obs ⇒ colonne ». reccobeats_resolution est une
# provenance mono-source émise par le provider ReccoBeats (voie ISRC/Spotify ID).
_KNOWN_FIELDS = (
    "bpm",
    "bpm_alt",
    "key",
    "mode",
    "time_signature",
    "reccobeats_resolution",
    "lyrics_synced",
    "spotify_streams",
    "ytm_streams",
)

# Champs ENCORE écrits en colonne (triple écriture maintenue) → seuls concernés
# par l'invariant « obs ⇒ colonne non-null ».
_COLUMN_BACKED_FIELDS = {
    "lyrics_synced": "lyrics_synced",
    "spotify_streams": "spotify_streams",
    "ytm_streams": "ytm_streams",
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
            _KNOWN_FIELDS,
        ).fetchall()
        if unknown:
            ok = False
            print(f"  ❌ champ(s) inconnu(s) : {', '.join(r[0] for r in unknown)}")
        else:
            print("  ✅ tous les champs sont connus")

        # (3) obs ⇒ colonne présente, pour les champs encore écrits en colonne.
        for field, column in _COLUMN_BACKED_FIELDS.items():
            dangling = conn.execute(
                f"SELECT COUNT(*) FROM observations o "  # noqa: S608 (colonne d'une whitelist)
                f"JOIN tracks t ON t.id = o.track_id "
                f"WHERE o.field = ? AND t.{column} IS NULL",
                (field,),
            ).fetchone()[0]
            status = "✅" if dangling == 0 else "❌"
            if dangling:
                ok = False
            print(f"  {status} {field:14} : {dangling} observation(s) sans colonne")

        total = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        print(f"\n{'✅ Cohérent' if ok else '❌ Incohérent'} — {total} observation(s) au total.")
        return 0 if ok else 1
    finally:
        conn.close()


def sizes(db_path: str) -> int:
    """Poids par champ (COUNT + octets de `value`) — read-only, sans invariant."""
    conn = sqlite3.connect(db_path)
    try:
        has_obs = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='observations'"
        ).fetchone()
        if not has_obs:
            print("❌ Table `observations` absente (migration e4 non appliquée ?)")
            return 1
        rows = conn.execute(
            "SELECT field, COUNT(*), SUM(LENGTH(value)), AVG(LENGTH(value)), MAX(LENGTH(value)) "
            "FROM observations GROUP BY field ORDER BY 3 DESC"
        ).fetchall()
        print(f"{'field':16} {'count':>7} {'total':>12} {'avg':>9} {'max':>9}")
        print("-" * 56)
        total_bytes = 0
        for field, count, sum_len, avg_len, max_len in rows:
            sum_len = sum_len or 0
            total_bytes += sum_len
            print(
                f"{field:16} {count:>7} {sum_len/1024:>10.1f}K "
                f"{(avg_len or 0):>8.0f} {(max_len or 0):>8}"
            )
        print("-" * 56)
        print(f"{'TOTAL value':16} {'':>7} {total_bytes/1024:>10.1f}K")
        return 0
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Contrôle read-only de la table observations")
    parser.add_argument("--db", metavar="CHEMIN", help="base à contrôler (défaut : config)")
    parser.add_argument(
        "--sizes",
        action="store_true",
        help="afficher le volume par champ (octets de value) au lieu des invariants",
    )
    args = parser.parse_args()

    db_path = args.db or DATABASE_URL.replace("sqlite:///", "")
    if not Path(db_path).exists():
        print(f"❌ Base introuvable : {db_path}")
        return 1
    if args.sizes:
        print(f"Volume observations sur : {db_path}\n")
        return sizes(db_path)
    print(f"Contrôle observations sur : {db_path}\n")
    return check(db_path)


if __name__ == "__main__":
    raise SystemExit(main())
