"""Liste les morceaux SANS BPM ou tonalité, prêts à analyser sur Sonoteller.

Deux sections :
  1. Avec lien YouTube en base → à coller dans sonoteller.ai (quota gratuit
     PARTAGÉ entre tous les visiteurs : quelques titres par jour, patience) ;
     astuce : un morceau déjà analysé par quelqu'un se consulte sans quota via
     sonoteller.ai/{videoId}.
  2. Sans lien YouTube → pour plus tard (BPM Finder accepte les fichiers locaux).

Saisie des résultats : clic droit sur le morceau → « ✏️ Saisir BPM / Tonalité… ».

Usage :
    python scripts/sonoteller_worklist.py Isha
"""

import argparse
import re
import sqlite3
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.config import DATABASE_URL

parser = argparse.ArgumentParser()
parser.add_argument("artist")
args = parser.parse_args()

conn = sqlite3.connect(DATABASE_URL.replace("sqlite:///", ""))
conn.row_factory = sqlite3.Row

# E7-D2 : bpm/key/mode vivent dans `observations` (colonnes droppées). Un morceau
# « manque » un champ audio quand il n'a AUCUNE observation de ce champ. `duration`
# reste une colonne.
rows = conn.execute(
    """
    SELECT t.id, t.title, t.duration, t.youtube_url,
           EXISTS(SELECT 1 FROM observations o WHERE o.track_id=t.id AND o.field='bpm') AS has_bpm,
           EXISTS(SELECT 1 FROM observations o WHERE o.track_id=t.id AND o.field='key') AS has_key,
           EXISTS(SELECT 1 FROM observations o WHERE o.track_id=t.id AND o.field='mode') AS has_mode
    FROM tracks t JOIN artists a ON a.id = t.artist_id
    WHERE a.name = ?
      AND (
        NOT EXISTS(SELECT 1 FROM observations o WHERE o.track_id=t.id AND o.field='bpm')
        OR NOT EXISTS(SELECT 1 FROM observations o WHERE o.track_id=t.id AND o.field='key')
        OR NOT EXISTS(SELECT 1 FROM observations o WHERE o.track_id=t.id AND o.field='mode')
        OR t.duration IS NULL
      )
    ORDER BY t.title COLLATE NOCASE
""",
    (args.artist,),
).fetchall()

with_yt, without_yt = [], []
for r in rows:
    missing = [
        m
        for m, present in (
            ("BPM", r["has_bpm"]),
            ("key", r["has_key"]),
            ("mode", r["has_mode"]),
        )
        if not present
    ]
    if r["duration"] is None:
        missing.append("durée")
    (with_yt if r["youtube_url"] else without_yt).append((r, missing))

print(f"\n🎧 {len(rows)} morceau(x) incomplet(s) pour {args.artist}\n")

if with_yt:
    print(f"─── {len(with_yt)} avec lien YouTube → sonoteller.ai ───")
    for r, missing in with_yt:
        m = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", r["youtube_url"] or "")
        vid = m.group(1) if m else "?"
        print(
            f"  #{r['id']:>4}  {r['title'][:45]:47} manque {'/'.join(missing):12} "
            f"{r['youtube_url']}"
        )
        print(f"        (déjà analysé ? → https://sonoteller.ai/{vid})")

if without_yt:
    print(f"\n─── {len(without_yt)} sans lien YouTube (plus tard : fichier local / BPM Finder) ───")
    for r, missing in without_yt:
        print(f"  #{r['id']:>4}  {r['title'][:45]:47} manque {'/'.join(missing)}")
