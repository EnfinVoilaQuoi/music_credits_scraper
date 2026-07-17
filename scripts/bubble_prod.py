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
from src.dataviz.collab_graph import BROAD_PRODUCER_ROLES, DEFAULT_SEED, STRICT_PRODUCER_ROLES
from src.utils.data_manager import DataManager


def _debug_preview(spec) -> None:
    """Aperçu matplotlib (import lazy) : carrés, arêtes et ellipses sur le même spec."""
    import matplotlib.pyplot as plt
    from matplotlib.patches import Ellipse

    fig, ax = plt.subplots(figsize=(11, 9))
    for e in spec.edges:
        ax.plot([e.x1, e.x2], [e.y1, e.y2], color="gray", lw=0.6, zorder=0)
    for tr in spec.tracks:
        el = tr.ellipse
        ax.add_patch(
            Ellipse(
                (el.cx, el.cy),
                width=2 * el.rx,
                height=2 * el.ry,
                angle=el.angle,
                fill=False,
                edgecolor="tab:blue",
                alpha=0.6,
            )
        )
    for n in spec.nodes:
        ax.plot(n.x, n.y, "s", color="black", markersize=4)
        ax.annotate(
            f"{n.display} ({n.track_count})", (n.x, n.y), fontsize=8, ha="center", va="bottom"
        )
    ax.set_aspect("equal")
    ax.invert_yaxis()  # repère canevas (y vers le bas), comme le SVG
    ax.set_title("Bubble Prod — aperçu debug")
    plt.show()


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
            output_path=args.out,
        )
    except ValueError as exc:
        print(f"❌ {exc}")
        return 1

    filtre = "large" if args.broad_roles else "strict (Producer)"
    print(f"✅ SVG écrit : {result.path}")
    print(
        f"   {result.producer_count} producteur(s), "
        f"{result.track_count}/{album_total} morceau(x) crédités  ·  filtre {filtre}"
    )
    print("   Participation (nb morceaux) :")
    for node in sorted(result.spec.nodes, key=lambda n: (-n.track_count, n.display.lower())):
        print(f"     {node.track_count:>3}  {node.display}")

    if args.debug:
        _debug_preview(result.spec)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
