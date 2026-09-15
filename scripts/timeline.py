"""Génère le carrousel « Timeline » (SVG d'aperçu + JSON Illustrator) d'un artiste.

Quatre pages de quatre projets, cartouche de streams cumulés estimés, courbe
continue. La sélection et les libellés viennent de `data/timeline_overrides.json`
(bouton « Mémoriser » de l'onglet Timeline d'Export studio) ; à défaut, la
sélection par défaut (tous les albums, puis les morceaux les mieux streamés).

Usage:
    python scripts/timeline.py "Isha" --candidats            # liste les candidats et sort
    python scripts/timeline.py "Isha" --candidats --par-date
    python scripts/timeline.py "Isha"                        # override mémorisé, sinon défaut
    python scripts/timeline.py "Isha" --pages 3 --defaut     # ignore l'override
    python scripts/timeline.py "Isha" --out exports/timeline.svg
"""

import argparse
import sys

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.dataviz.timeline import (
    PAGE_SIZE,
    build_candidates,
    default_page_count,
    default_selection,
    generate_timeline,
    generate_timeline_preview,
    sort_candidates,
)
from src.dataviz.timeline_overrides_io import (
    entries_from_override,
    get_override,
    load_overrides,
    pages_from_override,
)
from src.dataviz.timeline_style_io import load_style, style_path
from src.dataviz.timeline_svg import format_streams_short
from src.utils.data_manager import DataManager
from src.utils.disabled_tracks_manager import DisabledTracksManager


def main() -> int:
    parser = argparse.ArgumentParser(description="Carrousel « Timeline » d'un artiste")
    parser.add_argument("artist", help="Nom de l'artiste (tel qu'en base)")
    parser.add_argument("--candidats", action="store_true", help="Liste les candidats et sort")
    parser.add_argument(
        "--par-date", action="store_true", help="Candidats par date (défaut : proposés)"
    )
    parser.add_argument("--pages", type=int, choices=(3, 4), default=None, help="Nombre de pages")
    parser.add_argument("--defaut", action="store_true", help="Ignore l'override mémorisé")
    parser.add_argument("--out", default=None, help="Chemin du SVG (défaut : exports/…)")
    parser.add_argument(
        "--apercu", action="store_true", help="Page HTML d'aperçu seulement (pas d'export SVG/JSON)"
    )
    args = parser.parse_args()

    dm = DataManager()
    artist = dm.get_artist_by_name(args.artist)
    if artist is None:
        print(f"❌ Artiste introuvable : {args.artist!r}")
        return 1
    # Les désactivés restent PROPOSABLES (un Grünt est un point de carrière)
    # mais sortent du cumul — même règle que la GUI.
    disabled = frozenset(DisabledTracksManager().load_disabled_tracks(artist.name))
    tracks = list(artist.tracks or [])

    candidates = build_candidates(tracks, artist.name, disabled)
    override = {} if args.defaut else get_override(load_overrides(), artist.name)
    entries = entries_from_override(override)
    pages = args.pages or pages_from_override(override) or default_page_count(candidates)

    if args.candidats:
        chosen = (
            {e.key for e in entries}
            if entries
            else set(default_selection(candidates, pages * PAGE_SIZE))
        )
        source = "mémorisés" if entries else "par défaut"
        listed = sort_candidates(candidates, by_date=args.par_date)
        print(
            f"🗂 {len(candidates)} candidat(s) pour {artist.name} — "
            f"✓ = {source} ({len(chosen)}/{pages * PAGE_SIZE}) :"
        )
        for c in listed:
            mark = "✓" if c.key in chosen else " "
            tags = ("💿" if c.has_cert else "  ") + ("⛔" if c.disabled else "  ")
            print(
                f"  {mark} {c.date}  {format_streams_short(c.streams):>7}  {c.size_default:5} {tags} "
                f"{c.line1_default} | {c.line2_default}   [{c.key}]"
            )
        return 0

    if args.apercu:
        try:
            html = generate_timeline_preview(
                tracks,
                artist_name=artist.name,
                entries=entries,
                pages=pages,
                style=load_style(),
                disabled=disabled,
            )
        except ValueError as exc:
            print(f"❌ {exc}")
            return 1
        print(f"👁 Aperçu : {html}")
        return 0

    try:
        result = generate_timeline(
            tracks,
            artist_name=artist.name,
            entries=entries,
            pages=pages,
            style=load_style(),
            output_path=args.out,
            disabled=disabled,
        )
    except ValueError as exc:
        print(f"❌ {exc}")
        return 1

    print(f"✅ SVG  : {result.path}")
    print(f"✅ JSON : {result.json_path}")
    print(
        f"   {result.page_count} page(s), {result.entry_count} projet(s), "
        f"cumul final {format_streams_short(result.total_cumul)} ({result.total_cumul:,} streams estimés)"
        f" — {len(disabled)} désactivé(s) hors cumul"
    )
    print(f"🎛 Réglages : {style_path()}")
    if result.undated_count:
        print(f"⚠️  {result.undated_count} morceau(x) SANS date : hors candidats et hors cumul")
    if result.unstreamed_count:
        print(f"⚠️  {result.unstreamed_count} morceau(x) daté(s) sans aucun stream (comptent 0)")
    if result.missing_covers:
        print(f"⚠️  Pochette absente : {', '.join(result.missing_covers)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
