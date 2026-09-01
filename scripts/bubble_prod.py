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


def audit(artist, tracks) -> int:
    """Passe tous les albums au crible des invariants.

    Ce qui était mesuré à la main à chaque itération de calibrage devient
    reproductible : un réglage de forces qui casse une planche se voit ici, sur
    la vraie discographie, sans avoir à ouvrir un SVG.

    Deux niveaux, et la distinction compte :

    - les **entorses** violent ce que le moteur GARANTIT (deux cercles qui se
      chevauchent, un artiste capturé par un ovale étranger). Une seule est un
      bug ;
    - les **compromis** portent sur ce qu'il optimise SANS PROMESSE (un titre
      qui ne tient pas dans le cadre, deux ovales étrangers qui se croisent).
      Sur un album dense, aucun placement ne les satisfait tous — vérifié :
      renforcer la disjonction ne les élimine pas et creuse les vides.
    """
    import math

    from src.dataviz.bubble_layout import _ellipses_croisent, encloses, largest_void
    from src.dataviz.collab_graph import FEAT_ROLES

    fautes = 0
    arbitrages = 0
    occupations = []
    print(f"🔍 Audit des planches de {artist.name}\n")
    for album in list_albums(tracks):
        album_tracks = select_album_tracks(tracks, album)
        for label, roles in (("prod", STRICT_PRODUCER_ROLES), ("feat", FEAT_ROLES)):
            try:
                generate = generate_bubble_prod if label == "prod" else None
                spec = _spec_pour_audit(album_tracks, album, artist.name, roles, generate)
            except ValueError:
                continue  # album sans crédit de ce type : rien à auditer
            if spec is None or len(spec.nodes) < 2:
                continue
            ennuis, compromis = [], []
            nodes = list(spec.nodes)
            for i, a in enumerate(nodes):
                for b in nodes[i + 1 :]:
                    if math.hypot(a.x - b.x, a.y - b.y) < (a.size + b.size) / 2.0 - 1e-6:
                        ennuis.append(f"{a.key} chevauche {b.key}")
            for groupe in spec.groups:
                membres = set(groupe.member_keys)
                for n in nodes:
                    if n.key not in membres and encloses(groupe.ellipse, n.x, n.y):
                        ennuis.append(f"{n.key} capturé par l'ovale {groupe.member_keys}")
            gr = [g for g in spec.groups if len(g.member_keys) > 1]
            for i, ga in enumerate(gr):
                for gb in gr[i + 1 :]:
                    if set(ga.member_keys) & set(gb.member_keys):
                        continue  # artiste commun : recouvrement inévitable
                    if _ellipses_croisent(ga.ellipse, gb.ellipse):
                        compromis.append(
                            f"ovales étrangers croisés {ga.member_keys} ✕ {gb.member_keys}"
                        )
            for groupe in spec.groups:
                for ring in groupe.rings:
                    porteuse = groupe.ellipse.inflated(ring.offset)
                    for j in range(7):
                        px, py = porteuse.point_at(ring.t - 20.0 + 40.0 * j / 6.0)
                        if not (-1 <= px <= spec.width + 1 and -1 <= py <= spec.height + 1):
                            compromis.append(f"titre hors cadre : {ring.text!r}")
                            break

            # Le plus grand disque vide qu'on puisse loger dans la zone. C'est
            # CE que l'œil appelle « un trou » — l'occupation par boîte
            # englobante valait 100 % dès que quelques cercles touchaient les
            # bords, pendant que l'intérieur restait béant.
            vide = largest_void(
                {n.key: (n.x, n.y) for n in nodes}, {n.key: n.size for n in nodes}, spec.style
            )
            # Rapporté à ce qui est ATTEIGNABLE pour ce nombre de cercles : à
            # 2 cercles dans 850 × 600, un vide de 330 px est incompressible.
            # Le rapport, lui, se compare d'une planche à l'autre — 1 = aussi
            # homogène que possible, 2 = un trou deux fois trop grand.
            ideal = math.sqrt(spec.width * spec.height / (math.pi * len(nodes)))
            occupations.append(vide / ideal)
            if ennuis:
                fautes += len(ennuis)
                print(f"  ❌ {label} · {album}")
            elif compromis:
                arbitrages += len(compromis)
                print(f"  ⚠️  {label} · {album[:40]:40} vide {vide:4.0f} px ({vide / ideal:.1f}×)")
            else:
                print(f"  ✅ {label} · {album[:40]:40} vide {vide:4.0f} px ({vide / ideal:.1f}×)")
            for ligne in ennuis + compromis:
                print(f"       {ligne}")

    if occupations:
        mediane = sorted(occupations)[len(occupations) // 2]
        print(
            f"\n{len(occupations)} planche(s) · plus grand vide rapporté à l'idéal : "
            f"médiane {mediane:.1f}×, pire {max(occupations):.1f}×"
        )
    print(f"{fautes} entorse(s) à ce qui est garanti · {arbitrages} compromis assumé(s)")
    return 1 if fautes else 0


def _spec_pour_audit(album_tracks, album, artist_name, roles, generate):
    """Le spec d'une planche, sans écrire de fichier."""
    from src.dataviz.bubble_prod import build_bubble_spec, extract_instruments
    from src.dataviz.collab_graph import (
        INSTRUMENT_ROLES,
        aggregate_collab_groups,
        build_collab_graph,
        extract_track_groups,
    )

    style = load_style()
    effective = roles + INSTRUMENT_ROLES if style.include_instruments else roles
    groups = extract_track_groups(album_tracks, effective)
    if not groups:
        return None
    subs = extract_instruments(album_tracks, INSTRUMENT_ROLES) if style.include_instruments else {}
    return build_bubble_spec(
        build_collab_graph(groups),
        aggregate_collab_groups(groups),
        style,
        sub_labels=subs,
        solo_badge=generate is not None,
    )


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
    parser.add_argument(
        "--audit", action="store_true", help="Vérifie les invariants sur tous les albums"
    )
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

    if args.audit:
        return audit(artist, tracks)

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
