"""Sépare les parutions d'album fondues avec le single de leur titre.

Le single Deezer d'une chanson-titre (« Vinyl Days », 1 piste) avait adopté,
au seul titre, la parution de l'album Genius du même nom (28 pistes) : ID
Deezer, code-barres, type et date du single, contrôles Apple / MusicBrainz sur
ce code-barres (9 parutions le 2026-11-13, famille d'audit `single_fondu`).
Le single retrouve sa parution ; celle de l'album perd ce qui venait de lui et
se fond dans la parution de l'album Deezer du même titre quand elle existe
(`TrackRepository.singles_fondus`).

    python scripts/separer_singles_fondus.py                 # à blanc (défaut)
    python scripts/separer_singles_fondus.py --apply         # backup + écriture
    python scripts/separer_singles_fondus.py --base copie.db # sur une autre base
"""

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    parser.add_argument("--base", type=Path, help="autre base que data/music_credits.db")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    import src.utils.data_manager as module_dm
    from src.utils.logger import configurer_journaux

    configurer_journaux()
    if args.base:
        module_dm.DATABASE_URL = f"sqlite:///{args.base.resolve().as_posix()}"
    dm = module_dm.DataManager()
    print(f"Base : {dm.db_path}")

    plan = dm.singles_fondus()
    print(f"{len(plan)} parution(s) d'album fondue(s) avec un single")
    for cas in plan:
        s = cas["single"]
        print(
            f"\n  #{cas['release_id']} « {cas['titre']} » ({cas['pistes']} pistes) — "
            f"single Deezer {s['deezer']}, UPC {s['upc'] or '—'}, date {s['date'] or '—'}"
        )
        rendu = ["ID Deezer", "code-barres", "type", "date", "constats Apple / MusicBrainz"]
        if s["spotify_web"]:
            rendu.append(f"album Spotify {', '.join(s['spotify_web'])}")
        if s["apple"] and cas["apple_au_single"]:
            rendu.append(f"album Apple {', '.join(s['apple'])}")
        print(f"    → rendu au single : {', '.join(rendu)} ; piste(s) {cas['pistes_single']}")
        if s["apple"] and not cas["apple_au_single"]:
            print(f"    · l'album Apple {', '.join(s['apple'])} reste à l'album (places d'album)")
        if cas["album_deezer"] is not None:
            print(
                f"    → fondue dans l'album Deezer #{cas['album_deezer']} "
                f"({cas['communes']} / {cas['pistes']} pistes déjà liées)"
            )
        else:
            print("    → pas d'album Deezer du même titre : identifiants du single retirés seuls")

    if not args.apply or not plan:
        print("\n(à blanc — relancer avec --apply pour écrire)")
        return 0
    if not _sauvegarder(dm, args, "before_separer_singles_fondus"):
        return 1
    print(f"\n✅ {len(dm.singles_fondus(appliquer=True))} parution(s) séparée(s).")
    return 0


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
