"""Recalcule les colonnes DÉRIVÉES de toutes les fiches (ou de celles d'un
artiste) depuis ce qui les fonde : observations, vidéos, paroles, ID Spotify.

    python scripts/rematerialiser.py                    # à blanc (défaut)
    python scripts/rematerialiser.py --artiste Booba    # un artiste
    python scripts/rematerialiser.py --apply            # backup + écriture

Amorce du chantier A de l'audit du 2026-10-11 (`TrackRepository.rematerialiser`).
"""

import argparse
import collections
import sys

from src.utils.data_manager import DataManager
from src.utils.database_backup import get_backup_manager
from src.utils.logger import configurer_journaux


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    parser.add_argument("--artiste", help="limiter à cet artiste (nom exact en base)")
    args = parser.parse_args()
    configurer_journaux()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    dm = DataManager()
    ids = None
    if args.artiste:
        artiste = dm.get_artist_by_name(args.artiste)
        if artiste is None:
            print(f"❌ Artiste inconnu : {args.artiste}")
            return 1
        ids = [t.id for t in dm.get_artist_tracks(artiste.id)]

    changements = dm.rematerialiser(ids)
    par_colonne = collections.Counter(c for d in changements.values() for c in d)
    print(f"{len(changements)} fiche(s) changent : {dict(par_colonne)}")
    if not args.apply or not changements:
        print("(à blanc — relancer avec --apply pour écrire)")
        return 0
    backup = get_backup_manager().create_backup("before_rematerialiser")
    if not backup:
        print("❌ Backup impossible — abandon.")
        return 1
    print(f"💾 Backup : {backup}")
    print(f"✅ {len(dm.rematerialiser(ids, appliquer=True))} fiche(s) recalculée(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
