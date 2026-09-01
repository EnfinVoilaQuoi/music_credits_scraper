"""Orchestrateur Bubble Prod : morceaux d'un album → SVG du réseau producteurs.

Chaîne complète : `select_album_tracks` → `extract_track_groups` (filtre rôles)
→ `build_collab_graph` → `compute_layout` (seed fixe) → `build_bubble_spec`
(positions layout → canevas, tailles pondérées, ellipses englobantes) →
`write_bubble_svg`. Aucun import GUI : utilisable en CLI comme depuis la fenêtre
« Export studio ».

Le cœur (`generate_bubble` / `generate_grid`) est générique — seuls le filtre de
rôles, le nom de fichier et les libellés changent : `bubble_feat.py` le
reconfigure tel quel pour le réseau des artistes invités.
"""

import math
import re
from dataclasses import dataclass
from pathlib import Path

import networkx as nx

from src.dataviz import bubble_labels, bubble_layout
from src.dataviz.bubble_json import build_payload, write_bubble_json
from src.dataviz.bubble_overrides_io import apply_style_override, get_override, resolve_seed
from src.dataviz.bubble_svg import (
    BubbleSpec,
    EdgeSpec,
    GroupShape,
    NodeSpec,
    SvgStyle,
    write_bubble_svg,
)
from src.dataviz.collab_graph import (
    DEFAULT_SEED,
    INSTRUMENT_ROLES,
    STRICT_PRODUCER_ROLES,
    aggregate_collab_groups,
    build_collab_graph,
    compute_layout,
    extract_track_groups,
)
from src.utils.title_matching import normalize_title

# Caractères interdits dans un nom de dossier Windows.
_FORBIDDEN_DIRNAME = '<>:"/\\|?*'


@dataclass(frozen=True)
class BubbleResult:
    """Retour de `generate_bubble` : le spec rendu et les fichiers écrits.

    `node_count` = nb de nœuds du réseau (producteurs pour Bubble Prod,
    artistes invités pour Bubble Feat). `path` = l'aperçu SVG, `json_path` = les
    données de la planche Illustrator, `missing_images` = les artistes sans
    photo (cercle plein en repli).
    """

    spec: BubbleSpec
    path: Path
    node_count: int
    track_count: int
    json_path: Path | None = None
    missing_images: tuple[str, ...] = ()


# ── Sélection d'album ────────────────────────────────────────────────────────


def list_albums(tracks) -> list[str]:
    """Noms d'albums distincts (dédup par titre normalisé), triés.

    Regroupe les graphies d'un même album via `normalize_title` (« Vol.3 » vs
    « Vol. 3 ») et retient la graphie lexicographiquement minimale par groupe
    (déterministe, indépendant de l'ordre des morceaux).
    """
    by_norm: dict[str, list[str]] = {}
    for track in tracks:
        album = (track.album or "").strip()
        if not album:
            continue
        norm = normalize_title(album)
        if not norm:
            continue
        by_norm.setdefault(norm, []).append(album)
    return sorted(min(variants) for variants in by_norm.values())


def select_album_tracks(tracks, album: str) -> list:
    """Morceaux dont l'album normalise vers le même titre que `album`."""
    target = normalize_title((album or "").strip())
    return [t for t in tracks if normalize_title((t.album or "").strip()) == target]


# ── Tailles des carrés (participation) ───────────────────────────────────────


def _node_size(track_count: int, min_count: int, max_count: int, style: SvgStyle) -> float:
    """Diamètre du cercle ∝ sqrt(participation), mappé sur [min, max], borné.

    Échelle ancrée en `[sqrt(1), sqrt(max_count_album)]` : un prod à 1 morceau
    = `node_size_min`, le plus gros compte = `node_size_max`. Si tous les
    comptes de l'album sont égaux (aucune gradation à montrer) → `min` pour tous.

    Les diamètres sont **absolus** (jamais remis à l'échelle pour tenir dans la
    zone) : c'est ce qui rend deux albums comparables à l'œil.
    """
    if max_count == min_count:
        return style.node_size_min
    lo = math.sqrt(1)
    hi = math.sqrt(max_count)
    frac = (math.sqrt(track_count) - lo) / (hi - lo)
    frac = max(0.0, min(1.0, frac))
    return style.node_size_min + frac * (style.node_size_max - style.node_size_min)


def _badge_text(track_count: int, solo_count: int) -> str:
    """Ce qu'affiche le badge : le compte, et le détail des solos s'il y en a.

    « 15 (7 Solos) » pour un producteur qui a des morceaux à plusieurs ET des
    solos. Un producteur qui n'a QUE des solos garde son simple compte : le
    répéter entre parenthèses n'apprendrait rien.
    """
    if not solo_count or solo_count >= track_count:
        return str(track_count)
    unite = "Solo" if solo_count == 1 else "Solos"
    return f"{track_count} ({solo_count} {unite})"


def _format_instrument(role: str) -> str:
    """« Bass Guitar » → « Bass guitar » : minuscules, initiale en capitale."""
    cleaned = (role or "").strip()
    return cleaned[:1].upper() + cleaned[1:].lower() if cleaned else ""


def extract_instruments(tracks, roles: tuple[str, ...]) -> dict[str, str]:
    """`identity_key` → instrument(s) joué(s), pour le sous-titre du cercle.

    Plusieurs instruments pour la même personne sont joints : un musicien qui
    tient le piano sur un morceau et les cordes sur un autre porte les deux.
    """
    from src.utils.credit_normalize import identity_key

    role_set = set(roles)
    found: dict[str, list[str]] = {}
    for track in tracks:
        for credit in track.credits:
            if credit.role.value not in role_set:
                continue
            key = identity_key(credit.name)
            label = _format_instrument(credit.role.value)
            if key and label and label not in found.setdefault(key, []):
                found[key].append(label)
    return {key: ", ".join(sorted(labels)) for key, labels in sorted(found.items())}


def _label_font_size(node_size: float, style: SvgStyle) -> float:
    """Taille du nom dans un cercle : `font_size` au prorata du diamètre.

    Un cercle à `node_size_max` porte `font_size` en entier ; un plus petit
    reçoit la même taille réduite dans le même rapport, jamais sous
    `font_size_min` (en dessous, le nom n'est plus lisible et autant le laisser
    déborder). Résolu ici plutôt qu'à l'affichage pour que l'aperçu SVG et la
    planche Illustrator posent EXACTEMENT la même valeur.
    """
    if style.node_size_max <= 0:
        return style.font_size
    ratio = min(1.0, node_size / style.node_size_max)
    return max(style.font_size_min, style.font_size * ratio)


# ── Construction du spec ─────────────────────────────────────────────────────


# Suffixe entre parenthèses ou crochets en FIN de titre : « (Interlude) »,
# « (feat. X) », « [Bonus] ». Répété pour les titres qui en cumulent deux.
_TITLE_SUFFIX_RE = re.compile(r"\s*[(\[][^()\[\]]*[)\]]\s*$")

# Barres et solidus COMBINANTS (U+0334 à U+0338) : Genius stylise certains
# titres en glissant un « barré » après chaque lettre — « F̶i̶e̶s̶t̶a̶ ». C'est de la
# décoration de page, pas le nom du morceau, et ça le rend illisible sur une
# bulle. Seule cette plage est retirée : les accents combinants, eux, sont de
# vraies lettres (un « e » + accent aigu décomposé doit rester « é »).
_STRIKETHROUGH_RE = re.compile("[̴-̸]")


def clean_track_title(title: str) -> str:
    """Titre allégé pour la légende : on garde le nom du morceau, rien d'autre.

    Deux nettoyages :

    - les **barrés décoratifs** de Genius (« F̶i̶e̶s̶t̶a̶ », un caractère combinant
      après chaque lettre) — c'est de la mise en forme de page, pas le nom du
      morceau, et le titre en devient illisible ;
    - les **mentions entre parenthèses en fin de titre** : sur une bulle,
      « Fiesta (Interlude) » ne dit rien de plus que « Fiesta » et coûte la
      moitié de la place, or la légende est posée sur l'ellipse, où la place est
      comptée. Ce qui est entre parenthèses AU MILIEU du titre est conservé :
      il fait partie du nom.
    """
    cleaned = _STRIKETHROUGH_RE.sub("", title or "").strip()
    previous = None
    while cleaned != previous:
        previous = cleaned
        cleaned = _TITLE_SUFFIX_RE.sub("", cleaned).strip()
    return cleaned or (title or "").strip()


def _count_label(n: int) -> str:
    return f"{n} morceau" if n == 1 else f"{n} morceaux"


def _ellipse_label(collab_group, style: SvgStyle) -> tuple[str, ...]:
    """Légende de l'ellipse : titres des morceaux si peu nombreux, sinon « N morceaux ».

    Un seul morceau → toujours son TITRE (même pour un producteur solo : afficher
    « 1 morceau » n'apporterait rien). Plusieurs morceaux : solo → compte ;
    combinaison → titres jusqu'au seuil, compte au-delà.
    """
    if collab_group.track_count == 1:
        return tuple(clean_track_title(t) for t in collab_group.track_titles)
    if len(collab_group.keys) == 1:
        # Producteur seul sur N morceaux : rien sur l'ovale. « N solo » s'y
        # perdait au milieu des titres voisins alors que l'info tient dans le
        # badge du cercle — « 15 (7 Solos) ».
        return ()
    if collab_group.track_count <= style.label_track_threshold:
        return tuple(clean_track_title(t) for t in collab_group.track_titles)
    return (_count_label(collab_group.track_count),)


def build_bubble_spec(
    graph,
    collab_groups,
    style: SvgStyle | None = None,
    seed: int = DEFAULT_SEED,
    sub_labels: dict[str, str] | None = None,
    solo_badge: bool = True,
) -> BubbleSpec:
    """Assemble le `BubbleSpec` : placement, ellipses, titres, cadrage.

    Le placement est délégué à `bubble_layout.solve` (une relaxation sous
    contraintes) et le placement des titres à `bubble_labels.place`. Ici, on ne
    fait qu'enchaîner : tailles → positions → ellipses → titres → centrage.
    """
    style = style or SvgStyle()
    sub_labels = sub_labels or {}
    if graph.number_of_nodes() == 0:
        raise ValueError("build_bubble_spec : graphe vide (aucun producteur)")

    counts = {key: graph.nodes[key]["track_count"] for key in graph.nodes}
    min_count, max_count = min(counts.values()), max(counts.values())
    sizes = {key: _node_size(counts[key], min_count, max_count, style) for key in graph.nodes}
    radii = {key: sizes[key] / 2.0 for key in sizes}
    # Le détail des solos ne vaut que pour les producteurs : un artiste SEUL
    # invité sur un morceau n'est pas « en solo », le mot induirait en erreur
    # sur une planche Bubble Feat.
    solos = (
        {cg.keys[0]: cg.track_count for cg in collab_groups if len(cg.keys) == 1}
        if solo_badge
        else {}
    )

    # ── Placement ────────────────────────────────────────────────────────────
    seed_positions = _seed_positions(graph, style, seed)
    positions = bubble_layout.solve(sizes, [cg.keys for cg in collab_groups], style, seed_positions)
    circles = {k: (positions[k][0], positions[k][1], radii[k]) for k in sorted(positions)}

    # ── Ellipses, une par combinaison d'artistes ─────────────────────────────
    shapes = []
    for cg in collab_groups:
        ellipse = bubble_layout.group_ellipse(cg.keys, positions, radii, style)
        shapes.append((cg, ellipse, _ellipse_label(cg, style)))

    # ── Titres ───────────────────────────────────────────────────────────────
    rings_by_group = bubble_labels.place(
        [(cg.keys, ellipse, lines) for cg, ellipse, lines in shapes],
        circles,
        style,
        (style.frame_width, style.frame_height),
    )

    ordered = sorted(positions)
    nodes = tuple(
        NodeSpec(
            key=key,
            display=graph.nodes[key]["display"],
            x=positions[key][0],
            y=positions[key][1],
            size=sizes[key],
            track_count=counts[key],
            label_font_size=_label_font_size(sizes[key], style),
            badge_text=_badge_text(counts[key], solos.get(key, 0)),
            sub_label=sub_labels.get(key, ""),
        )
        for key in ordered
    )
    ordered_edges = sorted((tuple(sorted((u, v))), graph[u][v]["weight"]) for u, v in graph.edges())
    edges = tuple(
        EdgeSpec(
            a=a,
            b=b,
            weight=weight,
            x1=positions[a][0],
            y1=positions[a][1],
            x2=positions[b][0],
            y2=positions[b][1],
        )
        for (a, b), weight in ordered_edges
    )
    groups_shapes = tuple(
        GroupShape(
            member_keys=cg.keys,
            ellipse=ellipse,
            label_lines=lines,
            rings=rings,
            track_count=cg.track_count,
        )
        for (cg, ellipse, lines), rings in zip(shapes, rings_by_group, strict=True)
    )

    # ── Débordement : mesuré sur les CERCLES ─────────────────────────────────
    # Un ovale déborde par nature de ses cercles, et un ovale coupé par le bord
    # est conforme à la DA (cf. maquette) : seuls les cercles font foi.
    xs = [c[0] - c[2] for c in circles.values()] + [c[0] + c[2] for c in circles.values()]
    ys = [c[1] - c[2] for c in circles.values()] + [c[1] + c[2] for c in circles.values()]
    over_w = max(0.0, (max(xs) - min(xs)) - style.frame_width)
    over_h = max(0.0, (max(ys) - min(ys)) - style.frame_height)

    return BubbleSpec(
        width=style.frame_width,
        height=style.frame_height,
        nodes=nodes,
        edges=edges,
        groups=groups_shapes,
        style=style,
        frame=(0.0, 0.0, style.frame_width, style.frame_height) if style.draw_frame else None,
        overflow=(over_w, over_h) if (over_w > 0 or over_h > 0) else None,
    )


def _seed_positions(graph, style: SvgStyle, seed: int) -> dict[str, tuple[float, float]]:
    """Amorce du placement : le `spring_layout` du graphe, mis à l'échelle.

    Sous-graphes RECONSTRUITS en ordre trié : `graph.subgraph()` hérite de
    l'ordre d'insertion du parent, qui varie d'un process à l'autre, et
    `spring_layout` n'y serait plus reproductible.
    """
    ordered = nx.Graph()
    ordered.add_nodes_from(sorted(graph.nodes))
    for u, v in sorted(tuple(sorted(e)) for e in graph.edges()):
        ordered.add_edge(u, v, weight=graph[u][v].get("weight", 1))
    raw = compute_layout(ordered, seed=seed)
    scale = style.seed_scale
    # `-y` : le layout raisonne en repère mathématique, le SVG en y vers le bas.
    return {k: (float(raw[k][0]) * scale, -float(raw[k][1]) * scale) for k in sorted(graph.nodes)}


# ── Sortie ───────────────────────────────────────────────────────────────────


def _safe_dirname(name: str) -> str:
    """Nom de dossier sûr sous Windows (chars interdits → '_', pas de point final)."""
    cleaned = (name or "").strip()
    for ch in _FORBIDDEN_DIRNAME:
        cleaned = cleaned.replace(ch, "_")
    cleaned = cleaned.rstrip(" .")
    return cleaned or "_"


def default_output_path(artist_name: str, album: str, filename: str = "bubble_prod.svg") -> Path:
    """`<EXPORTS_DIR>/<artiste>/<album>/<filename>` (dossiers créés lazily)."""
    from src.config import EXPORTS_DIR

    base = Path(EXPORTS_DIR) / _safe_dirname(artist_name) / _safe_dirname(album)
    base.mkdir(parents=True, exist_ok=True)
    return base / filename


# ── Orchestration ────────────────────────────────────────────────────────────


def generate_bubble(
    tracks,
    album: str,
    *,
    artist_name: str = "",
    roles: tuple[str, ...],
    credit_label: str,
    filename: str,
    kind: str = "prod",
    solo_badge: bool = True,
    style: SvgStyle | None = None,
    seed: int | None = None,
    overrides: dict | None = None,
    output_path=None,
) -> BubbleResult:
    """Cœur commun Bubble Prod / Bubble Feat : le réseau des crédits `roles`.

    Écrit DEUX fichiers côte à côte, comme « Structure » : le `.svg` (aperçu de
    contrôle) et le `.json` (données de la planche Illustrator). `credit_label`
    sert aux messages d'erreur (« producteur », « featuring »), `filename` au
    chemin de sortie par défaut, `kind` distingue prod/feat dans le payload.
    Lève `ValueError` si l'album n'a aucun morceau ou aucun crédit dans `roles`.

    `overrides` = les entrées PAR ALBUM (`bubble_overrides_io.load_overrides`),
    passées en donnée pour rester testable sans disque : un `seed` explicite
    gagne toujours, sinon la variante mémorisée pour CETTE planche, sinon
    `DEFAULT_SEED` ; le bloc `style` de la planche surcharge le style global.
    """
    override = get_override(overrides, kind, artist_name, album)
    seed = resolve_seed(override, seed, DEFAULT_SEED)
    style = apply_style_override(style or SvgStyle(), override)
    album_tracks = select_album_tracks(tracks, album)
    if not album_tracks:
        raise ValueError(f"Aucun morceau trouvé pour l'album « {album} »")

    # Les instrumentistes rejoignent le réseau (option) : ce sont des crédits de
    # fabrication au même titre, avec leur instrument affiché sous leur nom.
    effective_roles = roles
    sub_labels: dict[str, str] = {}
    if style.include_instruments:
        effective_roles = roles + INSTRUMENT_ROLES
        sub_labels = extract_instruments(album_tracks, INSTRUMENT_ROLES)

    track_groups = extract_track_groups(album_tracks, effective_roles)
    if not track_groups:
        raise ValueError(
            f"Aucun crédit {credit_label} ({', '.join(roles)}) sur l'album « {album} »"
        )

    graph = build_collab_graph(track_groups)
    collab_groups = aggregate_collab_groups(track_groups)
    spec = build_bubble_spec(
        graph, collab_groups, style, seed=seed, sub_labels=sub_labels, solo_badge=solo_badge
    )

    if output_path is None:
        output_path = default_output_path(artist_name, album, filename)
    output_path = Path(output_path)
    write_bubble_svg(spec, output_path)

    payload = build_payload(spec, kind=kind, artist_name=artist_name, album=album, seed=seed)
    json_path = output_path.with_suffix(".json")
    write_bubble_json(payload, json_path)
    missing = tuple(n["name"] for n in payload["nodes"] if n["image"] is None)

    return BubbleResult(
        spec=spec,
        path=output_path,
        node_count=graph.number_of_nodes(),
        track_count=len(track_groups),
        json_path=json_path,
        missing_images=missing,
    )


def generate_bubble_prod(
    tracks,
    album: str,
    *,
    artist_name: str = "",
    roles: tuple[str, ...] = STRICT_PRODUCER_ROLES,
    style: SvgStyle | None = None,
    seed: int | None = None,
    overrides: dict | None = None,
    output_path=None,
) -> BubbleResult:
    """Génère le SVG Bubble Prod pour `album` et renvoie un `BubbleResult`.

    Lève `ValueError` si l'album n'a aucun morceau ou aucun crédit producteur
    dans `roles`.
    """
    return generate_bubble(
        tracks,
        album,
        artist_name=artist_name,
        roles=roles,
        credit_label="producteur",
        filename="bubble_prod.svg",
        kind="prod",
        style=style,
        seed=seed,
        overrides=overrides,
        output_path=output_path,
    )


# Seeds proposés dans la grille d'aperçus : le principal + 3 variantes qui
# réarrangent les pétales (l'ordre angulaire vient du spring_layout → change
# avec le seed). Choix par album selon ce qui remplit le mieux.
PREVIEW_SEEDS: tuple[int, ...] = (DEFAULT_SEED, 7, 13, 21)

_GRID_CSS = (
    "body{margin:0;font-family:Arial,sans-serif;background:#f5f5f5}"
    ".grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;padding:14px}"
    "figure{margin:0;border:1px solid #ccc;border-radius:6px;background:#fff;overflow:hidden}"
    "img{width:100%;height:auto;display:block}"
    "figcaption{padding:8px 12px;font-size:15px;color:#333;border-top:1px solid #eee}"
)


def generate_grid(
    tracks,
    album: str,
    *,
    artist_name: str = "",
    roles: tuple[str, ...],
    credit_label: str,
    svg_prefix: str,
    title: str,
    subdir: str,
    style: SvgStyle | None = None,
    seeds: tuple[int, ...] = PREVIEW_SEEDS,
    overrides: dict | None = None,
    output_dir=None,
) -> Path:
    """Cœur commun des grilles d'aperçus : variantes de `seeds` + HTML 2×2.

    Écrit `<svg_prefix>_seed<N>.svg` par variante et `apercus.html` (SVG
    embarqués par référence relative) dans `<album>/<subdir>/`. Renvoie le
    chemin du HTML — à ouvrir dans le navigateur pour choisir la variante qui
    remplit le mieux. Les `overrides` ne jouent ici que sur le STYLE (chaque
    aperçu impose son seed) : la grille montre les variantes telles qu'elles
    sortiraient réellement.
    """
    if output_dir is None:
        output_dir = default_output_path(artist_name, album).parent / subdir
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    figures = []
    for seed in seeds:
        svg_name = f"{svg_prefix}_seed{seed}.svg"
        generate_bubble(
            tracks,
            album,
            artist_name=artist_name,
            roles=roles,
            credit_label=credit_label,
            filename=svg_name,
            style=style,
            seed=seed,
            overrides=overrides,
            output_path=output_dir / svg_name,
        )
        suffix = " (défaut)" if seed == DEFAULT_SEED else ""
        figures.append(
            f'<figure><img src="{svg_name}" alt="seed {seed}">'
            f"<figcaption>Variante {seed}{suffix}</figcaption></figure>"
        )

    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{title} — {album}</title><style>{_GRID_CSS}</style></head>"
        f"<body><div class='grid'>{''.join(figures)}</div></body></html>"
    )
    html_path = output_dir / "apercus.html"
    html_path.write_text(html, encoding="utf-8")
    return html_path


def generate_preview_grid(
    tracks,
    album: str,
    *,
    artist_name: str = "",
    roles: tuple[str, ...] = STRICT_PRODUCER_ROLES,
    style: SvgStyle | None = None,
    seeds: tuple[int, ...] = PREVIEW_SEEDS,
    overrides: dict | None = None,
    output_dir=None,
) -> Path:
    """Grille d'aperçus Bubble Prod (`bubble_prod_seed<N>.svg` dans `apercus/`)."""
    return generate_grid(
        tracks,
        album,
        artist_name=artist_name,
        roles=roles,
        credit_label="producteur",
        svg_prefix="bubble_prod",
        title="Bubble Prod",
        subdir="apercus",
        style=style,
        seeds=seeds,
        overrides=overrides,
        output_dir=output_dir,
    )
