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
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from src.config import DATABASE_URL

parser = argparse.ArgumentParser()
parser.add_argument('artist')
args = parser.parse_args()

conn = sqlite3.connect(DATABASE_URL.replace("sqlite:///", ""))
conn.row_factory = sqlite3.Row

rows = conn.execute("""
    SELECT t.id, t.title, t.bpm, t.key, t.mode, t.duration, t.youtube_url
    FROM tracks t JOIN artists a ON a.id = t.artist_id
    WHERE a.name = ?
      AND (t.bpm IS NULL OR t.key IS NULL OR t.mode IS NULL OR t.duration IS NULL)
    ORDER BY t.title COLLATE NOCASE
""", (args.artist,)).fetchall()

with_yt, without_yt = [], []
for r in rows:
    missing = [m for m, v in (("BPM", r['bpm']), ("key", r['key']),
                              ("mode", r['mode']), ("durée", r['duration'])) if v is None]
    (with_yt if r['youtube_url'] else without_yt).append((r, missing))

print(f"\n🎧 {len(rows)} morceau(x) incomplet(s) pour {args.artist}\n")

if with_yt:
    print(f"─── {len(with_yt)} avec lien YouTube → sonoteller.ai ───")
    for r, missing in with_yt:
        m = re.search(r'(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})', r['youtube_url'] or '')
        vid = m.group(1) if m else '?'
        print(f"  #{r['id']:>4}  {r['title'][:45]:47} manque {'/'.join(missing):12} "
              f"{r['youtube_url']}")
        print(f"        (déjà analysé ? → https://sonoteller.ai/{vid})")

if without_yt:
    print(f"\n─── {len(without_yt)} sans lien YouTube (plus tard : fichier local / BPM Finder) ───")
    for r, missing in without_yt:
        print(f"  #{r['id']:>4}  {r['title'][:45]:47} manque {'/'.join(missing)}")
