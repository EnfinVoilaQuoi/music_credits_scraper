"""Génère le SVG « Bubble Prod » d'un album (réseau de producteurs) en CLI.

Moteur `src.dataviz` sans GUI : utile pour le dev / batch et pour calibrer le
layout (`--debug` → aperçu matplotlib) avant de brancher la fenêtre Export studio.

Usage :
    python scripts/bubble_prod.py "Josman" --list-albums
    python scripts/bubble_prod.py "Josman" "M.A.N"
    python scripts/bubble_prod.py "Josman" "M.A.N" --out out.svg --seed 42
    python scripts/bubble_prod.py "Josman" "M.A.N" --broad-roles --debug
"""

import argparse
import sys

# Encodage Windows (le package est installé via `pip install -e .` : aucun hack sys.path).
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.dataviz.bubble_prod import (
    generate_bubble_prod,
    list_albums,
    select_album_tracks,
)
from src.dataviz.bubble_style_io import load_style
from src.dataviz.collab_graph import BROAD_PRODUCER_ROLES, DEFAULT_SEED, STRICT_PRODUCER_ROLES
from src.dataviz.debug_preview import debug_preview
from src.utils.data_manager import DataManager


def report_outputs(result) -> None:
    """Affiche les deux fichiers écrits + les alertes (débordement, photos)."""
    print(f"✅ SVG écrit  : {result.path}")
    print(f"✅ JSON écrit : {result.json_path}")
    if result.spec.overflow:
        w, h = result.spec.overflow
        print(f"   ⚠️ Le réseau dépasse la zone de {w:.0f} × {h:.0f} px (album dense).")
    if result.missing_images:
        names = ", ".join(result.missing_images)
        print(f"   ⚠️ {len(result.missing_images)} sans photo (cercle plein) : {names}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Génère le SVG Bubble Prod d'un album.")
    parser.add_argument("artist", help="Nom exact de l'artiste (tel qu'en base)")
    parser.add_argument("album", nargs="?", default=None, help="Album (ou --list-albums)")
    parser.add_argument("--list-albums", action="store_true", help="Liste les albums et quitte")
    parser.add_argument("--out", default=None, help="Chemin du SVG (défaut : exports/…)")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="Seed du layout")
    parser.add_argument(
        "--broad-roles", action="store_true", help="Filtre large (toute la famille production)"
    )
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

    roles = BROAD_PRODUCER_ROLES if args.broad_roles else STRICT_PRODUCER_ROLES
    album_total = len(select_album_tracks(tracks, args.album))

    try:
        result = generate_bubble_prod(
            tracks,
            args.album,
            artist_name=artist.name,
            roles=roles,
            seed=args.seed,
            style=load_style(),
            output_path=args.out,
        )
    except ValueError as exc:
        print(f"❌ {exc}")
        return 1

    filtre = "large" if args.broad_roles else "strict (Producer)"
    report_outputs(result)
    print(
        f"   {result.node_count} producteur(s), "
        f"{result.track_count}/{album_total} morceau(x) crédités  ·  filtre {filtre}"
    )
    print("   Participation (nb morceaux) :")
    for node in sorted(result.spec.nodes, key=lambda n: (-n.track_count, n.display.lower())):
        print(f"     {node.track_count:>3}  {node.display}")

    if args.debug:
        debug_preview(result.spec, "Bubble Prod — aperçu debug")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
