"""Rafraîchit `spotify_page_title` depuis la dernière lecture de la page de
l'ID principal gardée dans le brut (`src.utils.titres_pages_spotify`).

    python scripts/rafraichir_titres_spotify.py                 # à blanc (défaut)
    python scripts/rafraichir_titres_spotify.py --apply         # backup + écriture
    python scripts/rafraichir_titres_spotify.py --base copie.db # sur une autre base

Hors ligne : aucune page n'est relue, seul le brut l'est. Un ID sans lecture
gardée garde son titre ; un titre n'est jamais vidé.
"""

import argparse
import collections
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    parser.add_argument("--base", type=Path, help="autre base que data/music_credits.db")
    parser.add_argument("--brut", type=Path, help="autre brut que data/brut/brut.db")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    import src.utils.data_manager as module_dm
    from src.utils import titres_pages_spotify
    from src.utils.logger import configurer_journaux

    configurer_journaux()
    if args.base:
        module_dm.DATABASE_URL = f"sqlite:///{args.base.resolve().as_posix()}"
    dm = module_dm.DataManager()
    print(f"Base : {dm.db_path}")

    fiches = dm.titres_pages_spotify()
    lectures = titres_pages_spotify.dernieres_lectures(args.brut)
    cas = titres_pages_spotify.ecarts(fiches, lectures)
    lues = sum(1 for f in fiches if f["spotify_id"] in lectures)
    print(f"{len(fiches)} fiche(s) à ID Spotify, {lues} lue(s) dans le brut : {len(cas)} écart(s)")
    for artiste, n in collections.Counter(c["artiste"] for c in cas).most_common():
        print(f"  {artiste} : {n}")
    for c in cas:
        print(
            f"  #{c['track_id']} {c['artiste']} — {c['titre']}\n      {c['page']!r}\n    → {c['neuf']!r}"
        )

    if not args.apply or not cas:
        print("(à blanc — relancer avec --apply pour écrire)")
        return 0
    if not _sauvegarder(dm, args, "before_rafraichir_titres_spotify"):
        return 1
    ecrites = dm.poser_titres_pages_spotify(cas)
    reste = titres_pages_spotify.ecarts(dm.titres_pages_spotify(), lectures)
    print(f"✅ {ecrites} fiche(s) écrite(s) ; reste {len(reste)} écart(s).")
    return 0 if not reste else 2


def _sauvegarder(dm, args, nom: str) -> bool:
    from src.utils.database_backup import DatabaseBackupManager, get_backup_manager

    sauvegarde = (
        DatabaseBackupManager(
            db_path=str(dm.db_path), backup_dir=str(Path(dm.db_path).parent / "backups")
        )
        if args.base
        else get_backup_manager()
    )
    backup = sauvegarde.create_backup(nom)
    if not backup:
        print("❌ Backup impossible — abandon.")
        return False
    print(f"💾 Backup : {backup}")
    return True


if __name__ == "__main__":
    sys.exit(main())
