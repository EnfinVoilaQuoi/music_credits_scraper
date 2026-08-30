"""Génère le SVG « Structure » (barres de sections) d'un album.

Une ligne par morceau : barre normalisée à 100 % de la durée, découpée par type de
section (intro/outro, couplet, refrain, pont), plus un rectangle « durée » collé à
droite dont la longueur encode la durée absolue.

Usage:
    python scripts/structure.py "Django" --list-albums
    python scripts/structure.py "Django" "Athanor"
    python scripts/structure.py "Django" "Athanor" --out exports/structure.svg
    python scripts/structure.py "Django" "Athanor" --no-project
"""

import argparse
import sys

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.dataviz.structure import generate_structure, list_albums, select_album_tracks
from src.dataviz.structure_style_io import load_style, style_path
from src.utils.data_manager import DataManager


def main() -> int:
    parser = argparse.ArgumentParser(description="SVG « Structure » d'un album")
    parser.add_argument("artist", help="Nom de l'artiste (tel qu'en base)")
    parser.add_argument("album", nargs="?", default=None, help="Nom de l'album")
    parser.add_argument("--list-albums", action="store_true", help="Liste les albums et sort")
    parser.add_argument("--out", default=None, help="Chemin du SVG (défaut : exports/…)")
    parser.add_argument(
        "--no-project", action="store_true", help="Sans la ligne d'agrégat « Structure du Projet »"
    )
    args = parser.parse_args()

    dm = DataManager()
    artist = dm.get_artist_by_name(args.artist)
    if artist is None:
        print(f"❌ Artiste introuvable : {args.artist!r}")
        return 1
    tracks = artist.tracks or []

    if args.list_albums or not args.album:
        albums = list_albums(tracks)
        if not albums:
            print(f"❌ Aucun album détecté pour {artist.name}")
            return 1
        print(f"🎼 {len(albums)} album(s) pour {artist.name} :")
        for album in albums:
            print(f"  - {album} ({len(select_album_tracks(tracks, album))} morceaux)")
        return 0

    try:
        result = generate_structure(
            tracks,
            args.album,
            artist_name=artist.name,
            style=load_style(),
            with_project=not args.no_project,
            output_path=args.out,
        )
    except ValueError as exc:
        print(f"❌ {exc}")
        return 1

    print(f"✅ SVG (aperçu)      : {result.path}")
    print(f"✅ JSON (Illustrator) : {result.json_path}")
    print(f"🎼 {result.track_count} morceau(x), {result.section_count} section(s)")
    print(f"🎛 Réglages : {style_path()}")
    if result.unnumbered_count:
        print(
            f"⚠️  {result.unnumbered_count} morceau(x) sans n° de piste → ordre "
            f"ALPHABÉTIQUE, pas celui de l'album. Corrige via la vue Albums "
            f"(clic droit dans le vide → importer l'album Genius par son URL)."
        )
    if result.unaligned_count:
        print(
            f"⚠️  {result.unaligned_count} section(s) non alignée(s) sur le LRC "
            f"(temps absorbé par la section précédente)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
