"""Rattrapage : les photos d'artiste gardées comme pochettes de morceaux isolés.

    python scripts/retirer_pochettes_photo_artiste.py           # à blanc (défaut)
    python scripts/retirer_pochettes_photo_artiste.py --apply   # backup + retrait

Sans pochette, Genius sert la photo de l'artiste comme `song_art_image_url`
(2 827 morceaux sur 10 730 du brut, 2026-11-03) ; `media_enricher` la gardait
comme pochette des morceaux sans album (691 pochettes de Kanye identiques).
`GeniusAPI._extract_song_art` l'écarte désormais.

Ne part que ce qu'on peut montrer : un morceau sans album dont la page Genius
(lue dans le brut) sert la photo d'un artiste crédité, ET dont le fichier est un
PNG / WebP (Deezer ne sert que du JPEG : il vient donc de Genius) ou identique à
celui d'un AUTRE morceau. Un JPEG unique peut être la pochette Deezer (repli
quand l'artwork Genius manquait) : il reste, compté à part.
Le fichier part, et `cover_path` est vidé sur toutes les lignes qui le désignent
(`oublier_pochettes`, sœurs comprises) ; le prochain « Télécharger les images… »
cherchera la pochette Deezer.
"""

import argparse
import hashlib
import json
import sqlite3
import sys
import zlib
from collections import defaultdict
from contextlib import closing
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.api.genius_api import GeniusAPI
from src.config import IMAGES_DIR
from src.persistence import brut
from src.utils.image_downloader import cover_image_path, existing_image


def pages_a_photo(chemin_brut: Path) -> set[int]:
    """Les pages Genius dont la « pochette » est la photo d'un artiste crédité."""
    pages: set[int] = set()
    vues: set[str] = set()
    with closing(sqlite3.connect(f"file:{chemin_brut}?mode=ro", uri=True)) as conn:
        lignes = conn.execute(
            "SELECT requete, corps FROM brut WHERE source = 'genius_api' "
            "AND requete LIKE 'https://api.genius.com/songs/%'"
        )
        for requete, corps in lignes:
            page = requete.split("/songs/", 1)[1].split("?", 1)[0]
            if page in vues or not page.isdigit():
                continue
            try:
                song = json.loads(zlib.decompress(corps))["response"]["song"]
            except (zlib.error, ValueError, KeyError, TypeError):
                continue
            vues.add(page)
            art = song.get("song_art_image_url")
            if art and art in GeniusAPI._photos_des_artistes(song):
                pages.add(int(page))
    return pages


def candidats(dm, pages: set[int]) -> tuple[list[Path], list[Path]]:
    """(fichiers prouvés, JPEG uniques laissés) des morceaux isolés de ces pages."""
    from sqlalchemy import select

    from src.persistence.schema import artists, tracks

    with dm.engine.connect() as conn:
        lignes = conn.execute(
            select(artists.c.name, tracks.c.title, tracks.c.genius_id)
            .join(artists, artists.c.id == tracks.c.artist_id)
            .where(tracks.c.genius_id.in_(sorted(pages)), tracks.c.album.is_(None))
        ).fetchall()
    pages_par_fichier: dict[Path, set[int]] = defaultdict(set)
    for nom, titre, page in lignes:
        fichier = existing_image(cover_image_path(nom, titre))
        if fichier:
            pages_par_fichier[fichier].add(page)
    pages_par_contenu: dict[str, set[int]] = defaultdict(set)
    empreintes = {}
    for fichier, pages_du_fichier in pages_par_fichier.items():
        empreintes[fichier] = hashlib.sha256(fichier.read_bytes()).hexdigest()
        pages_par_contenu[empreintes[fichier]] |= pages_du_fichier
    prouves, laisses = [], []
    for fichier in sorted(pages_par_fichier):
        partage = len(pages_par_contenu[empreintes[fichier]]) > 1
        (prouves if partage or fichier.suffix.lower() != ".jpg" else laisses).append(fichier)
    return prouves, laisses


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="backup puis retrait")
    args = parser.parse_args(argv)

    from src.utils.data_manager import DataManager

    dm = DataManager()
    pages = pages_a_photo(brut.CHEMIN)
    prouves, laisses = candidats(dm, pages)
    print(f"{len(pages)} pages Genius servent la photo d'un artiste en guise de pochette")
    print(f"{len(prouves)} pochettes prouvées à retirer, {len(laisses)} JPEG uniques laissés")
    for fichier in prouves[:10]:
        print(f"   {fichier.name}")
    if len(prouves) > 10:
        print(f"   … et {len(prouves) - 10} autres")

    if not args.apply:
        print("À blanc : rien n'est écrit (--apply pour appliquer).")
        return 0

    from src.utils.database_backup import DatabaseBackupManager

    if not DatabaseBackupManager().create_backup("retirer_pochettes_photo_artiste"):
        print("❌ Sauvegarde de la base impossible : rien n'est retiré.")
        return 1
    lignes = dm.oublier_pochettes(f.relative_to(IMAGES_DIR).as_posix() for f in prouves)
    for fichier in prouves:
        fichier.unlink()
    print(f"✅ {len(prouves)} fichiers retirés, `cover_path` vidé sur {lignes} lignes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
