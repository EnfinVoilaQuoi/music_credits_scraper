"""Génère le SVG « Bubble Feat » d'un album (réseau des artistes invités) en CLI.

Miroir de `scripts/bubble_prod.py` : même moteur `src.dataviz`, seul le filtre
de rôles change (crédits « Featured Artist » — pas d'option --broad-roles,
la famille n'a qu'un rôle).

Usage :
    python scripts/bubble_feat.py "Josman" --list-albums
    python scripts/bubble_feat.py "Josman" "M.A.N"
    python scripts/bubble_feat.py "Josman" "M.A.N" --out out.svg --seed 42
    python scripts/bubble_feat.py "Josman" "M.A.N" --debug
"""

import argparse
import sys

# Encodage Windows (le package est installé via `pip install -e .` : aucun hack sys.path).
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.dataviz.bubble_feat import generate_bubble_feat
from src.dataviz.bubble_prod import list_albums, select_album_tracks
from src.dataviz.collab_graph import DEFAULT_SEED
from src.dataviz.debug_preview import debug_preview
from src.utils.data_manager import DataManager


def main() -> int:
    parser = argparse.ArgumentParser(description="Génère le SVG Bubble Feat d'un album.")
    parser.add_argument("artist", help="Nom exact de l'artiste (tel qu'en base)")
    parser.add_argument("album", nargs="?", default=None, help="Album (ou --list-albums)")
    parser.add_argument("--list-albums", action="store_true", help="Liste les albums et quitte")
    parser.add_argument("--out", default=None, help="Chemin du SVG (défaut : exports/…)")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="Seed du layout")
    parser.add_argument("--debug", action="store_true", help="Aperçu matplotlib du spec")
    args = parser.parse_args()

    dm = DataManager()
    artist = dm.get_artist_by_name(args.artist)
    if artist is None:
        print(f"❌ Artiste introuvable : {args.artist!r}")
        return 1
    tracks = artist.tracks or []
    if not tracks:
        print(f"❌ Aucun morceau en base pour {artist.name!r}")
        return 1

    if args.list_albums:
        albums = list_albums(tracks)
        print(f"🎼 {len(albums)} album(s) pour {artist.name} :")
        for name in albums:
            n = len(select_album_tracks(tracks, name))
            print(f"  • {name}  ({n} morceaux)")
        return 0

    if not args.album:
        print("❌ Précisez un album (ou utilisez --list-albums).")
        return 1

    album_total = len(select_album_tracks(tracks, args.album))

    try:
        result = generate_bubble_feat(
            tracks,
            args.album,
            artist_name=artist.name,
            seed=args.seed,
            output_path=args.out,
        )
    except ValueError as exc:
        print(f"❌ {exc}")
        return 1

    print(f"✅ SVG écrit : {result.path}")
    print(
        f"   {result.node_count} artiste(s) en featuring, "
        f"{result.track_count}/{album_total} morceau(x) avec feat"
    )
    print("   Présence (nb morceaux) :")
    for node in sorted(result.spec.nodes, key=lambda n: (-n.track_count, n.display.lower())):
        print(f"     {node.track_count:>3}  {node.display}")

    if args.debug:
        debug_preview(result.spec, "Bubble Feat — aperçu debug")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
