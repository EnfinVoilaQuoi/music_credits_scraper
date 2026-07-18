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
import zlib
from dataclasses import dataclass
from pathlib import Path

import networkx as nx

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
    STRICT_PRODUCER_ROLES,
    aggregate_collab_groups,
    build_collab_graph,
    compute_layout,
    extract_track_groups,
)
from src.dataviz.geometry import EllipseSpec, enclosing_shape
from src.utils.title_matching import normalize_title

_SQRT2 = math.sqrt(2.0)

# Caractères interdits dans un nom de dossier Windows.
_FORBIDDEN_DIRNAME = '<>:"/\\|?*'


@dataclass(frozen=True)
class BubbleResult:
    """Retour de `generate_bubble` : le spec rendu + le chemin du SVG.

    `node_count` = nb de nœuds du réseau (producteurs pour Bubble Prod,
    artistes invités pour Bubble Feat).
    """

    spec: BubbleSpec
    path: Path
    node_count: int
    track_count: int


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
    """Côté du carré ∝ sqrt(participation), mappé sur [min, max], borné.

    Échelle ancrée en `[sqrt(1), sqrt(max_count_album)]` : un prod à 1 morceau
    = `square_size_min`, le plus gros compte = `square_size_max`. Si tous les
    comptes de l'album sont égaux (aucune gradation à montrer) → `min` pour tous.
    """
    if max_count == min_count:
        return style.square_size_min
    lo = math.sqrt(1)
    hi = math.sqrt(max_count)
    frac = (math.sqrt(track_count) - lo) / (hi - lo)
    frac = max(0.0, min(1.0, frac))
    return style.square_size_min + frac * (style.square_size_max - style.square_size_min)


# ── Construction du spec ─────────────────────────────────────────────────────


def _count_label(n: int) -> str:
    return f"{n} morceau" if n == 1 else f"{n} morceaux"


def _ellipse_label(collab_group, style: SvgStyle) -> tuple[str, ...]:
    """Légende de l'ellipse : titres des morceaux si peu nombreux, sinon « N morceaux ».

    Un seul morceau → toujours son TITRE (même pour un producteur solo : afficher
    « 1 morceau » n'apporterait rien). Plusieurs morceaux : solo → compte ;
    combinaison → titres jusqu'au seuil, compte au-delà.
    """
    if collab_group.track_count == 1:
        return collab_group.track_titles
    if len(collab_group.keys) == 1:
        # Producteur seul sur N morceaux → « N solo » (affiché DANS son carré).
        return (f"{collab_group.track_count} solo",)
    if collab_group.track_count <= style.label_track_threshold:
        return collab_group.track_titles
    return (_count_label(collab_group.track_count),)


def _remove_overlaps(
    canvas: dict[str, tuple[float, float]], sizes: dict[str, float], style: SvgStyle
) -> dict[str, tuple[float, float]]:
    """Écarte itérativement les carrés qui se superposent (boîtes, pas points).

    `spring_layout` ignore la taille des nœuds → passe corrective : à chaque
    paire en collision, translation minimale (sur l'axe de moindre recouvrement),
    répartie sur les deux carrés. Ordre et positions fixes → déterministe.
    """
    keys = list(canvas.keys())
    pos = {k: [canvas[k][0], canvas[k][1]] for k in keys}
    n = len(keys)
    for _ in range(style.overlap_iterations):
        moved = False
        for i in range(n):
            ki = keys[i]
            for j in range(i + 1, n):
                kj = keys[j]
                min_sep = (sizes[ki] + sizes[kj]) / 2.0 + style.overlap_gap
                ddx = pos[kj][0] - pos[ki][0]
                ddy = pos[kj][1] - pos[ki][1]
                ox = min_sep - abs(ddx)
                oy = min_sep - abs(ddy)
                if ox > 0 and oy > 0:  # boîtes en collision
                    moved = True
                    if ox <= oy:
                        shift = ox / 2.0 * (1.0 if ddx >= 0 else -1.0)
                        pos[ki][0] -= shift
                        pos[kj][0] += shift
                    else:
                        shift = oy / 2.0 * (1.0 if ddy >= 0 else -1.0)
                        pos[ki][1] -= shift
                        pos[kj][1] += shift
        if not moved:
            break
    return {k: (pos[k][0], pos[k][1]) for k in keys}


def _axis_label_anchor(
    ellipse,
    center_x: float,
    center_y: float,
    gap: float,
    line_half: float,
    max_angle: float,
    inward: bool = False,
) -> tuple[float, float, float]:
    """Ancre + angle de la légende, posée à la pointe de l'ellipse, quasi droite.

    Le texte est **tangent** à la pointe (perpendiculaire au grand axe) : un
    pétale vertical porte sa légende à l'horizontale au-dessus/en-dessous, comme
    écrite « au bord » de la bulle. L'angle est normalisé dans `[-90, 90]` puis
    amorti dans `[-max_angle, max_angle]`. Pointe **extérieure** (à l'opposé du
    centre du nuage) par défaut ; `inward=True` pour les îlots calés aux coins
    du cadre (pas de place côté coin → légende côté centre). Renvoie
    `(x, y, angle_degrés)`.
    """
    a = math.radians(ellipse.angle)
    ux, uy = math.cos(a), math.sin(a)  # direction du grand axe
    # Pointes du grand axe (± rx le long de l'axe).
    tip_plus = (ellipse.cx + ellipse.rx * ux, ellipse.cy + ellipse.rx * uy)
    tip_minus = (ellipse.cx - ellipse.rx * ux, ellipse.cy - ellipse.rx * uy)
    d_plus = (tip_plus[0] - center_x) ** 2 + (tip_plus[1] - center_y) ** 2
    d_minus = (tip_minus[0] - center_x) ** 2 + (tip_minus[1] - center_y) ** 2
    outer_is_plus = d_plus >= d_minus
    if outer_is_plus != inward:  # pointe extérieure, ou intérieure si inward
        tip, out_x, out_y = tip_plus, ux, uy
    else:
        tip, out_x, out_y = tip_minus, -ux, -uy

    # Tangente à la pointe = grand axe + 90°, normalisée puis amortie.
    ang = ((ellipse.angle + 90.0 + 180.0) % 360.0) - 180.0  # → (-180, 180]
    if ang > 90.0:
        ang -= 180.0
    elif ang < -90.0:
        ang += 180.0
    ang = max(-max_angle, min(max_angle, ang))  # amortissement (texte quasi droit)

    x = tip[0] + out_x * (gap + line_half)
    y = tip[1] + out_y * (gap + line_half)
    return x, y, ang


def _label_half_width(label_lines: tuple[str, ...], style: SvgStyle) -> float:
    """Demi-largeur estimée d'un bloc de légende (pour le cadrage du viewBox)."""
    if not label_lines:
        return 0.0
    longest = max(len(line) for line in label_lines)
    return longest * style.ellipse_label_font_size * 0.3


def _square_half_extents(
    canvas: dict[str, tuple[float, float]], sizes: dict[str, float]
) -> tuple[float, float, float, float]:
    """Centre + demi-largeur/hauteur d'un nuage de carrés (boîte englobante)."""
    xs: list[float] = []
    ys: list[float] = []
    for key, (x, y) in canvas.items():
        half = sizes[key] / 2.0
        xs.extend((x - half, x + half))
        ys.extend((y - half, y + half))
    cx = (min(xs) + max(xs)) / 2.0
    cy = (min(ys) + max(ys)) / 2.0
    return cx, cy, (max(xs) - min(xs)) / 2.0, (max(ys) - min(ys)) / 2.0


def _radialize_main(
    canvas: dict[str, tuple[float, float]],
    sizes: dict[str, float],
    style: SvgStyle,
    collab_groups,
) -> dict[str, tuple[float, float]]:
    """Répartit les feuilles du hub en angle (pétales sur 360°), clusters compacts.

    Le `spring_layout` oriente les pétales arbitrairement (souvent tassés d'un
    côté, ce qui les fait « pointer » vers les îlots par coïncidence). Ici :
    pivot = plus gros carré (le hub). Les feuilles sont regroupées en **unités** :
    une combinaison de ≥ 3 feuilles forme un **cluster** posé en bloc compact à
    2 colonnes le long de sa direction (rayons étagés → ellipse étroite, membres
    PAS tous à la même distance du hub) ; les autres feuilles sont isolées et
    gardent leur rayon (plancher `hub_clearance` pour dégager le pivot). Les
    unités reçoivent des parts angulaires (cluster = 2 parts), dans leur ordre
    angulaire d'origine. Déterministe : tris explicites partout.
    """
    if len(canvas) < 3:
        return canvas
    keys_set = set(canvas)
    pivot = max(sorted(canvas), key=lambda k: sizes[k])
    px, py = canvas[pivot]

    def angle_of(x: float, y: float) -> float:
        return math.atan2(y - py, x - px)

    # Chaque feuille rejoint sa plus grosse combinaison à ≥ 3 feuilles (hors pivot).
    best_combo: dict[str, tuple[int, tuple[str, ...]]] = {}
    for cg in collab_groups:
        members = tuple(k for k in cg.keys if k != pivot and k in keys_set)
        if len(members) < 3:
            continue
        for k in members:
            cand = (len(members), members)
            if k not in best_combo or cand > best_combo[k]:
                best_combo[k] = cand
    clusters: dict[tuple[str, ...], list[str]] = {}
    for k in sorted(best_combo):
        clusters.setdefault(best_combo[k][1], []).append(k)
    clustered = {k for ks in clusters.values() for k in ks}

    # Unités = clusters + feuilles isolées, ordonnées par angle d'origine.
    units: list[tuple[float, list[str]]] = []
    for _, ks in sorted(clusters.items()):
        cx = sum(canvas[k][0] for k in ks) / len(ks)
        cy = sum(canvas[k][1] for k in ks) / len(ks)
        units.append((angle_of(cx, cy), ks))
    for k in sorted(keys_set - {pivot} - clustered):
        units.append((angle_of(*canvas[k]), [k]))
    units.sort(key=lambda u: (u[0], u[1]))

    weights = [2.0 if len(ks) > 1 else 1.0 for _, ks in units]
    total = sum(weights)
    base = units[0][0]

    # Géométrie des clusters (départ, pas) + profondeur de référence : l'unité
    # la plus profonde fixe le rayon du contenu ; les feuilles isolées
    # s'étireront vers cette profondeur pour OCCUPER le cadre (sinon elles
    # restent au plancher et laissent des vides sur les bords).
    cluster_geo: dict[int, tuple[float, float]] = {}
    r_ref = 0.0
    for idx, (_, ks) in enumerate(units):
        if len(ks) > 1:
            biggest = max(sizes[k2] for k2 in ks)
            # Première rangée bien dégagée du hub (0.85), pas au gap PLEIN :
            # les membres d'un gros groupe respirent.
            r0 = (sizes[pivot] + biggest) * 0.85 + style.overlap_gap / 2.0
            step = biggest + style.overlap_gap
            cluster_geo[idx] = (r0, step)
            rows = (len(ks) + 1) // 2
            r_ref = max(r_ref, r0 + (rows - 1) * step + biggest / 2.0)
        else:
            k = ks[0]
            floor_r = (sizes[pivot] + sizes[k]) * style.hub_clearance + style.overlap_gap
            r = math.hypot(canvas[k][0] - px, canvas[k][1] - py)
            r_ref = max(r_ref, max(min(r, floor_r * 1.25), floor_r) + sizes[k] / 2.0)

    # Angles assignés par part angulaire, puis rotation d'ensemble : le cluster
    # le plus profond pointe vers l'AXE le plus proche (haut/bas/gauche/droite).
    # Les diagonales restent aux îlots (coins du cadre) → pas de frôlement
    # pétale↔îlot.
    angles = []
    acc = 0.0
    for w in weights:
        angles.append(base + (acc + w / 2.0) * 2.0 * math.pi / total)
        acc += w
    deepest_idx = None
    deepest = -1.0
    for idx, (_, ks) in enumerate(units):
        if len(ks) > 1:
            r0, step = cluster_geo[idx]
            depth = r0 + ((len(ks) + 1) // 2 - 1) * step
            if depth > deepest:
                deepest, deepest_idx = depth, idx
    if deepest_idx is not None:
        a_c = angles[deepest_idx] % (2.0 * math.pi)
        axes = (0.0, math.pi / 2.0, math.pi, 3.0 * math.pi / 2.0, 2.0 * math.pi)
        target = min(axes, key=lambda ax: abs(ax - a_c))
        delta = target - a_c
        angles = [a + delta for a in angles]

    out = {pivot: (px, py)}
    for idx, ((_, ks), _w) in enumerate(zip(units, weights, strict=True)):
        a = angles[idx]
        if len(ks) == 1:
            k = ks[0]
            x, y = canvas[k]
            r = math.hypot(x - px, y - py)
            # Plancher pour dégager le pivot, plafond de cohérence d'échelle.
            floor_r = (sizes[pivot] + sizes[k]) * style.hub_clearance + style.overlap_gap
            r = max(min(r, floor_r * 1.25), floor_r)
            # Étirement vers le bord du cadre carré selon la place disponible
            # dans cette direction (plafonné en diagonale à la profondeur de
            # référence : les coins sont aux îlots, un pétale n'y rampe pas).
            denom = max(abs(math.cos(a)), abs(math.sin(a)))
            boundary = min(r_ref / denom, r_ref * 1.02)
            r = max(r, style.radial_fill * boundary - sizes[k] / 2.0)
            out[k] = (px + r * math.cos(a), py + r * math.sin(a))
        else:
            # Bloc compact 2 colonnes le long de la direction : rayons étagés.
            # Serré (demi-gap, départ rapproché) pour ne pas étirer le cadre.
            ux, uy = math.cos(a), math.sin(a)
            qx, qy = -uy, ux
            r0, step = cluster_geo[idx]
            for i, k in enumerate(ks):
                row, col = divmod(i, 2)
                # Colonnes bien écartées (×1,5) : le groupe s'étale visiblement.
                lat = (0.75 if col else -0.75) * step
                if i == len(ks) - 1 and len(ks) % 2 == 1:
                    lat = 0.0  # dernier membre impair centré sur l'axe
                rr = r0 + row * step
                # Décalage organique DÉTERMINISTE (hash crc32 du nom — jamais
                # random ni dépendant du process) : casse l'alignement strict de
                # la grille, chaque membre dévie un peu des autres. Les
                # collisions résiduelles sont résorbées par _remove_overlaps.
                h = zlib.crc32(k.encode("utf-8"))
                j_rad = ((h & 0xFFFF) / 65535.0 - 0.5) * 2.0  # → [-1, 1]
                j_lat = (((h >> 16) & 0xFFFF) / 65535.0 - 0.5) * 2.0
                rr += j_rad * 0.22 * step
                lat += j_lat * 0.30 * step
                out[k] = (px + ux * rr + qx * lat, py + uy * rr + qy * lat)
    return out


def _component_canvas(
    graph,
    comp: list[str],
    sizes: dict[str, float],
    style: SvgStyle,
    seed: int,
    is_main: bool,
    collab_groups=(),
) -> dict[str, tuple[float, float]]:
    """Layout local d'une composante, centré sur l'origine, sans chevauchement.

    Composante principale : `spring_layout` × `canvas_scale × main_component_scale`
    puis **répartition radiale** des feuilles (pétales équirépartis sur 360°) et
    anti-chevauchement. Îlots (2-3 nœuds) : `spring_layout` mis à l'échelle
    pour que la paire la plus proche atteigne juste l'écart minimal (compact).
    """
    keys = sorted(comp)
    if len(keys) == 1:
        return {keys[0]: (0.0, 0.0)}

    # Sous-graphe RECONSTRUIT avec nœuds insérés en ordre trié (et arêtes triées) :
    # `graph.subgraph()` hériterait de l'ordre du parent, qui peut varier d'un
    # process à l'autre → `spring_layout` non reproductible. Ici l'ordre est figé.
    sub = nx.Graph()
    sub.add_nodes_from(keys)
    for u, v in sorted(tuple(sorted(e)) for e in graph.subgraph(keys).edges()):
        sub.add_edge(u, v, weight=graph[u][v].get("weight", 1))

    raw = compute_layout(sub, seed=seed)
    raw = {k: (float(raw[k][0]), float(raw[k][1])) for k in keys}

    if is_main:
        scale = style.canvas_scale * style.main_component_scale
    else:
        scale = 0.0
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                ki, kj = keys[i], keys[j]
                d = math.hypot(raw[ki][0] - raw[kj][0], raw[ki][1] - raw[kj][1])
                if d < 1e-9:
                    continue
                want = (sizes[ki] + sizes[kj]) / 2.0 + style.overlap_gap
                scale = max(scale, want / d)

    canvas = {k: (raw[k][0] * scale, -raw[k][1] * scale) for k in keys}
    if is_main:
        canvas = _radialize_main(canvas, sizes, style, collab_groups)
    canvas = _remove_overlaps(canvas, sizes, style)
    cx, cy, _, _ = _square_half_extents(canvas, sizes)
    return {k: (x - cx, y - cy) for k, (x, y) in canvas.items()}


# Directions de placement des îlots : coins d'abord, puis milieux d'arêtes.
_SLOTS = [(-1, -1), (1, -1), (-1, 1), (1, 1), (0, -1), (0, 1), (-1, 0), (1, 0)]


def _compose_layout(
    graph, sizes: dict[str, float], style: SvgStyle, seed: int, collab_groups=()
) -> tuple[dict[str, tuple[float, float]], list[list[str]]]:
    """Compose les composantes : hub au centre, îlots placés provisoirement autour.

    Composantes triées (taille décroissante puis clés) → déterministe. La plus
    grosse est centrée ; les autres sont posées juste au-delà de la boîte du hub
    (coins puis milieux d'arêtes). Ce placement est **provisoire** : une fois le
    cadre carré connu, `build_bubble_spec` cale chaque îlot à l'extrémité du
    cadre (coin intérieur). Renvoie `(canvas, composantes)`.
    """
    comps = [sorted(c) for c in nx.connected_components(graph)]
    comps.sort(key=lambda c: (-len(c), c))

    canvas: dict[str, tuple[float, float]] = {}
    main = comps[0]
    main_local = _component_canvas(
        graph, main, sizes, style, seed, is_main=True, collab_groups=collab_groups
    )
    canvas.update(main_local)
    _, _, main_hw, main_hh = _square_half_extents(main_local, sizes)

    for idx, comp in enumerate(comps[1:]):
        local = _component_canvas(graph, comp, sizes, style, seed, is_main=False)
        _, _, hw, hh = _square_half_extents(local, sizes)
        sx, sy = _SLOTS[idx % len(_SLOTS)]
        ring = idx // len(_SLOTS) + 1  # anneaux successifs si + de 8 îlots
        if sx and sy:
            # Coin : distance en DIAGONALE (÷√2 par axe) — poser l'îlot à
            # main_hw+gap sur les deux axes à la fois l'enverrait à ×1,41 de la
            # distance voulue et gonflerait le cadre pour rien (le calage final
            # aux coins se fait après le dimensionnement du carré).
            off = (max(main_hw, main_hh) + style.component_gap + math.hypot(hw, hh)) * ring
            off_x = sx * off * 0.72
            off_y = sy * off * 0.72
        else:
            off_x = sx * (main_hw + style.component_gap + hw) * ring if sx else 0.0
            off_y = sy * (main_hh + style.component_gap + hh) * ring if sy else 0.0
        for k, (x, y) in local.items():
            canvas[k] = (x + off_x, y + off_y)
    return canvas, comps


def build_bubble_spec(
    graph, collab_groups, style: SvgStyle | None = None, seed: int = DEFAULT_SEED
) -> BubbleSpec:
    """Assemble le `BubbleSpec` : composition par composante, ellipses, cadre carré.

    Chaque composante connexe est layoutée séparément (hub central agrandi, îlots
    dans les coins d'un cadre). Puis **une ellipse par combinaison de producteurs**
    (`CollabGroup`), légendée (titres ou « N morceaux ») et ancrée vers l'extérieur.
    Le contenu est centré dans un **cadre carré** (côté = plus grande dimension).
    """
    style = style or SvgStyle()
    if graph.number_of_nodes() == 0:
        raise ValueError("build_bubble_spec : graphe vide (aucun producteur)")

    counts = {key: graph.nodes[key]["track_count"] for key in graph.nodes}
    min_count = min(counts.values())
    max_count = max(counts.values())
    sizes = {key: _node_size(counts[key], min_count, max_count, style) for key in graph.nodes}

    # Composition par composante (hub centré, îlots provisoirement autour).
    canvas, comps = _compose_layout(graph, sizes, style, seed, collab_groups)
    main_set = set(comps[0])

    # Centre du nuage (oriente les légendes vers l'extérieur du hub). Somme en
    # ordre trié → indépendante de l'ordre d'insertion des nœuds (byte-identité).
    ordered = sorted(canvas)
    center_x = sum(canvas[k][0] for k in ordered) / len(canvas)
    center_y = sum(canvas[k][1] for k in ordered) / len(canvas)

    # Une ellipse par combinaison de producteurs, calculée sur les COINS des
    # carrés membres (pas leurs centres + padding uniforme : la demi-diagonale du
    # hub gonflerait toute l'ellipse, même aux pointes où il n'y a que des
    # petits carrés). L'ellipse épouse ainsi exactement les carrés + marge.
    raw_groups = []
    for cg in collab_groups:
        member_pts = []
        for k in cg.keys:
            x, y = canvas[k]
            half = sizes[k] / 2.0
            member_pts.extend(
                (
                    (x - half, y - half),
                    (x + half, y - half),
                    (x - half, y + half),
                    (x + half, y + half),
                )
            )
        ellipse = enclosing_shape(
            member_pts,
            padding=style.ellipse_margin,
            min_radius=style.ellipse_margin,
            min_axis_ratio=style.min_axis_ratio,
        )
        label_lines = _ellipse_label(cg, style)
        if len(cg.keys) == 1 and cg.track_count > 1:
            # « N solo » : DANS le carré du producteur, en bas, juste au-dessus
            # du badge (une légende à la pointe du cercle flotterait entre les
            # pétales voisins et semblerait appartenir à un autre ovale).
            k = cg.keys[0]
            x, y = canvas[k]
            anchor = (
                x,
                y
                + sizes[k] / 2.0
                - style.badge_size / 2.0
                - 8.0
                - style.ellipse_label_font_size / 2.0,
                0.0,
            )
        elif len(cg.keys) == 1:
            # Cercle solo à morceau unique : son TITRE sous le cercle
            # (cas mammouth), clairement rattaché.
            anchor = (
                ellipse.cx,
                ellipse.cy
                + ellipse.ry
                + style.ellipse_label_gap
                + style.ellipse_label_line_height / 2.0,
                0.0,
            )
        else:
            anchor = _axis_label_anchor(
                ellipse,
                center_x,
                center_y,
                style.ellipse_label_gap,
                style.ellipse_label_line_height / 2.0,
                style.ellipse_label_max_angle,
                inward=not set(cg.keys) <= main_set,
            )
        raw_groups.append((cg, ellipse, label_lines, anchor))

    # Cadre : boîte englobante des carrés, des ellipses ET des légendes.
    xs: list[float] = []
    ys: list[float] = []
    for key in graph.nodes:
        px, py = canvas[key]
        half = sizes[key] / 2.0
        xs.extend((px - half, px + half))
        ys.extend((py - half, py + half))
    for _, ellipse, label_lines, anchor in raw_groups:
        x0, y0, x1, y1 = ellipse.bbox()
        xs.extend((x0, x1))
        ys.extend((y0, y1))
        # Légende tournée : borne circulaire conservatrice autour de son ancre.
        ax, ay, _ = anchor
        reach = max(
            _label_half_width(label_lines, style),
            len(label_lines) * style.ellipse_label_line_height,
        )
        xs.extend((ax - reach, ax + reach))
        ys.extend((ay - reach, ay + reach))

    # Cadre CARRÉ : côté = plus grande dimension du contenu, contenu centré dedans.
    min_x, min_y = min(xs), min(ys)
    max_x, max_y = max(xs), max(ys)
    content_w = max_x - min_x
    content_h = max_y - min_y
    side = max(content_w, content_h)
    dx = style.margin + (side - content_w) / 2.0 - min_x
    dy = style.margin + (side - content_h) / 2.0 - min_y
    width = height = side + 2 * style.margin
    frame = None
    if style.draw_frame:
        inset = style.frame_inset
        frame = (inset, inset, width - 2 * inset, height - 2 * inset)

        # Îlots calés aux EXTRÉMITÉS du cadre (coin intérieur - pad), maintenant
        # que le carré est connu. Translation rigide par composante (repère brut) :
        # nœuds + ellipses + ancres de légende bougent ensemble.
        raw_fx0 = inset - dx
        raw_fx1 = (width - inset) - dx
        raw_fy0 = inset - dy
        raw_fy1 = (height - inset) - dy
        pad = style.island_corner_pad
        for idx, comp in enumerate(comps[1:]):
            comp_set = set(comp)
            # Boîte englobante de l'îlot : carrés + ellipses + légendes.
            bx0 = by0 = math.inf
            bx1 = by1 = -math.inf
            for k in comp:
                px, py = canvas[k]
                half = sizes[k] / 2.0
                bx0, bx1 = min(bx0, px - half), max(bx1, px + half)
                by0, by1 = min(by0, py - half), max(by1, py + half)
            for cg, ellipse, label_lines, anchor in raw_groups:
                if set(cg.keys) <= comp_set:
                    ex0, ey0, ex1, ey1 = ellipse.bbox()
                    bx0, bx1 = min(bx0, ex0), max(bx1, ex1)
                    by0, by1 = min(by0, ey0), max(by1, ey1)
                    reach = max(
                        _label_half_width(label_lines, style),
                        len(label_lines) * style.ellipse_label_line_height,
                    )
                    bx0, bx1 = min(bx0, anchor[0] - reach), max(bx1, anchor[0] + reach)
                    by0, by1 = min(by0, anchor[1] - reach), max(by1, anchor[1] + reach)
            sx, sy = _SLOTS[idx % len(_SLOTS)]
            if sx < 0:
                tx = (raw_fx0 + pad) - bx0
            elif sx > 0:
                tx = (raw_fx1 - pad) - bx1
            else:
                tx = (raw_fx0 + raw_fx1) / 2.0 - (bx0 + bx1) / 2.0
            if sy < 0:
                ty = (raw_fy0 + pad) - by0
            elif sy > 0:
                ty = (raw_fy1 - pad) - by1
            else:
                ty = (raw_fy0 + raw_fy1) / 2.0 - (by0 + by1) / 2.0
            for k in comp:
                canvas[k] = (canvas[k][0] + tx, canvas[k][1] + ty)
            for i, (cg, ellipse, label_lines, anchor) in enumerate(raw_groups):
                if set(cg.keys) <= comp_set:
                    raw_groups[i] = (
                        cg,
                        _shift_ellipse(ellipse, tx, ty),
                        label_lines,
                        (anchor[0] + tx, anchor[1] + ty, anchor[2]),
                    )

    # Application de la translation (repère positif). Sorties triées par clé →
    # ordre des éléments SVG indépendant de l'ordre d'insertion des nœuds.
    nodes = tuple(
        NodeSpec(
            key=key,
            display=graph.nodes[key]["display"],
            x=canvas[key][0] + dx,
            y=canvas[key][1] + dy,
            size=sizes[key],
            track_count=counts[key],
        )
        for key in ordered
    )

    ordered_edges = sorted((tuple(sorted((u, v))), graph[u][v]["weight"]) for u, v in graph.edges())
    edges = tuple(
        EdgeSpec(
            a=a,
            b=b,
            weight=weight,
            x1=canvas[a][0] + dx,
            y1=canvas[a][1] + dy,
            x2=canvas[b][0] + dx,
            y2=canvas[b][1] + dy,
        )
        for (a, b), weight in ordered_edges
    )

    groups_shapes = tuple(
        GroupShape(
            member_keys=cg.keys,
            ellipse=_shift_ellipse(ellipse, dx, dy),
            label_lines=label_lines,
            label_x=anchor[0] + dx,
            label_y=anchor[1] + dy,
            label_angle=anchor[2],
            track_count=cg.track_count,
        )
        for cg, ellipse, label_lines, anchor in raw_groups
    )

    return BubbleSpec(
        width=width,
        height=height,
        nodes=nodes,
        edges=edges,
        groups=groups_shapes,
        style=style,
        frame=frame,
    )


def _shift_ellipse(ellipse, dx: float, dy: float):
    return EllipseSpec(
        cx=ellipse.cx + dx, cy=ellipse.cy + dy, rx=ellipse.rx, ry=ellipse.ry, angle=ellipse.angle
    )


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
    style: SvgStyle | None = None,
    seed: int = DEFAULT_SEED,
    output_path=None,
) -> BubbleResult:
    """Cœur commun Bubble Prod / Bubble Feat : SVG du réseau des crédits `roles`.

    `credit_label` sert aux messages d'erreur (« producteur », « featuring »),
    `filename` au chemin de sortie par défaut. Lève `ValueError` si l'album n'a
    aucun morceau ou aucun crédit dans `roles`.
    """
    style = style or SvgStyle()
    album_tracks = select_album_tracks(tracks, album)
    if not album_tracks:
        raise ValueError(f"Aucun morceau trouvé pour l'album « {album} »")

    track_groups = extract_track_groups(album_tracks, roles)
    if not track_groups:
        raise ValueError(
            f"Aucun crédit {credit_label} ({', '.join(roles)}) sur l'album « {album} »"
        )

    graph = build_collab_graph(track_groups)
    collab_groups = aggregate_collab_groups(track_groups)
    spec = build_bubble_spec(graph, collab_groups, style, seed=seed)

    if output_path is None:
        output_path = default_output_path(artist_name, album, filename)
    output_path = Path(output_path)
    write_bubble_svg(spec, output_path)

    return BubbleResult(
        spec=spec,
        path=output_path,
        node_count=graph.number_of_nodes(),
        track_count=len(track_groups),
    )


def generate_bubble_prod(
    tracks,
    album: str,
    *,
    artist_name: str = "",
    roles: tuple[str, ...] = STRICT_PRODUCER_ROLES,
    style: SvgStyle | None = None,
    seed: int = DEFAULT_SEED,
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
        style=style,
        seed=seed,
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
    output_dir=None,
) -> Path:
    """Cœur commun des grilles d'aperçus : variantes de `seeds` + HTML 2×2.

    Écrit `<svg_prefix>_seed<N>.svg` par variante et `apercus.html` (SVG
    embarqués par référence relative) dans `<album>/<subdir>/`. Renvoie le
    chemin du HTML — à ouvrir dans le navigateur pour choisir la variante qui
    remplit le mieux.
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
        output_dir=output_dir,
    )
