"""Fiches « principales » portant un artiste principal : chimères d'avant les logs.

Mesuré le 2026-09-24 : 32 fiches `is_featuring=0` avaient un
`primary_artist_name` (Travis « Money, Power, Respect » → Meek Mill) ou un
`secondary_role`. Le détail Genius les donne TOUTES principales : la fiche porte
le genius_id du morceau de l'artiste, mais l'artiste principal ET l'album
viennent d'un homonyme fusionné avant e36 (PLK « Hiver » : album « Indigo », la
page dit « Polak » ; « Money, Power, Respect » : album « DC4 », celui de Meek
Mill). Les collisions d'avant le 2026-09-08 n'étaient pas loggées, d'où leur
absence de `repair_chimeres_genius`.

Remède identique : `chimeres_genius.reposer_page` avec la page de la fiche
(relation, album, date, et effacement des crédits / paroles Genius mélangés, à
re-scraper). L'homonyme a sa fiche depuis e36 (créée au run discographie).

    python scripts/repair_relation_artiste.py            # dry-run
    python scripts/repair_relation_artiste.py --apply    # backup + écriture
"""

import argparse
import sqlite3
import sys
import time
from pathlib import Path

if sys.platform == "win32" and "pytest" not in sys.modules:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.config import DATA_DIR

_REQUETE = (
    "SELECT t.id, t.title, t.album, t.genius_id, t.primary_artist_name, t.secondary_role, "
    "a.name AS artiste, a.genius_id AS artiste_genius_id "
    "FROM tracks t JOIN artists a ON a.id = t.artist_id "
    "WHERE COALESCE(t.is_featuring, 0) = 0 AND t.genius_id IS NOT NULL AND ("
    "(t.primary_artist_name IS NOT NULL AND t.primary_artist_name != '') OR "
    "(t.secondary_role IS NOT NULL AND t.secondary_role != '')) ORDER BY a.name, t.title"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="écrire (défaut : dry-run)")
    args = parser.parse_args()

    from src.api.genius_api import GeniusAPI
    from src.services.chimeres_genius import reposer_page

    genius = GeniusAPI()
    dm = None
    if args.apply:
        from src.utils.data_manager import DataManager
        from src.utils.database_backup import get_backup_manager

        dm = DataManager()
        print(f"💾 Backup : {get_backup_manager().create_backup('before_repair_relation_artiste')}")

    base = sqlite3.connect(f"file:{Path(DATA_DIR) / 'music_credits.db'}?mode=ro", uri=True)
    base.row_factory = sqlite3.Row
    fiches = base.execute(_REQUETE).fetchall()
    for f in fiches:
        chanson = genius.genius.song(f["genius_id"])["song"]
        time.sleep(0.2)
        album_page = (chanson.get("album") or {}).get("name")
        ligne = (
            f"{f['artiste']} — « {f['title']} » : principal {f['primary_artist_name']!r}, "
            f"rôle {f['secondary_role']!r}, album {f['album']!r} (page : {album_page!r})"
        )
        if dm is not None:
            ligne += "  ⇒ " + reposer_page(dm, genius, f["id"], f["artiste_genius_id"], chanson)
        print(ligne)
    print(f"\nTOTAL {len(fiches)}")
    if dm is None:
        print("(dry-run — relancer avec --apply pour écrire)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
