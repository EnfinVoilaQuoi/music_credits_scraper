"""Rattrapage : les silhouettes Deezer gardées comme photos d'artistes.

    python scripts/retirer_photos_par_defaut.py           # à blanc (défaut)
    python scripts/retirer_photos_par_defaut.py --apply   # backup + retrait

Deezer sert une silhouette grise pour un artiste sans photo ; jusqu'au
2026-11-03, `download_image` la gardait comme une photo (545 des 6 228 fichiers
de `data/images/artistes/`) : un cercle de Bubble Prod l'affichait au lieu de
rester vide, et le fichier présent empêchait toute nouvelle recherche. Le
téléchargement la refuse désormais (`IMAGES_PAR_DEFAUT_SHA256`).

Ce script retire ces fichiers et vide `artists.image_path` des artistes dont la
photo de profil en était une, pour que le prochain « Télécharger les images… »
cherche à nouveau (Genius en repli).
"""

import argparse
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.config import ARTIST_IMAGES_DIR, IMAGES_DIR
from src.utils.image_downloader import IMAGE_EXTENSIONS, est_image_par_defaut


def silhouettes(dossier: Path) -> list[Path]:
    """Les fichiers du dossier qui sont l'image par défaut d'une source, triés."""
    return sorted(
        p
        for p in dossier.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS and est_image_par_defaut(p)
    )


def artistes_sur_silhouette(dm, fichiers: list[Path]) -> list[tuple[int, str]]:
    """`(id, nom)` des artistes dont `image_path` désigne un de ces fichiers."""
    from sqlalchemy import select

    from src.persistence.schema import artists

    relatifs = {p.relative_to(IMAGES_DIR).as_posix() for p in fichiers}
    with dm.engine.connect() as conn:
        lignes = conn.execute(
            select(artists.c.id, artists.c.name, artists.c.image_path).order_by(artists.c.name)
        ).fetchall()
    return [
        (aid, nom) for aid, nom, chemin in lignes if chemin and Path(chemin).as_posix() in relatifs
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="backup puis retrait")
    args = parser.parse_args(argv)

    from src.utils.data_manager import DataManager

    fichiers = silhouettes(ARTIST_IMAGES_DIR)
    dm = DataManager()
    artistes = artistes_sur_silhouette(dm, fichiers)
    total = sum(1 for p in ARTIST_IMAGES_DIR.iterdir() if p.is_file())
    print(f"{len(fichiers)} silhouettes sur {total} fichiers de {ARTIST_IMAGES_DIR}")
    for p in fichiers[:10]:
        print(f"   {p.name}")
    if len(fichiers) > 10:
        print(f"   … et {len(fichiers) - 10} autres")
    print(f"{len(artistes)} photo(s) de profil à vider : {', '.join(nom for _, nom in artistes)}")

    if not args.apply:
        print("À blanc : rien n'est écrit (--apply pour appliquer).")
        return 0

    from src.utils.database_backup import DatabaseBackupManager

    if artistes and not DatabaseBackupManager().create_backup("retirer_photos_par_defaut"):
        print("❌ Sauvegarde de la base impossible : rien n'est retiré.")
        return 1
    for artist_id, _ in artistes:
        dm.set_artist_image_path(artist_id, None)
    for p in fichiers:
        p.unlink()
    print(f"✅ {len(fichiers)} fichiers retirés, {len(artistes)} photo(s) de profil vidée(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
