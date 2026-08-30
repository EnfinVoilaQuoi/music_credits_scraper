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
import zlib
from dataclasses import dataclass
from pathlib import Path

import networkx as nx

from src.dataviz.bubble_json import build_payload, write_bubble_json
from src.dataviz.bubble_svg import (
    BubbleSpec,
    EdgeSpec,
    GroupShape,
    LabelRing,
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

# Nombre de points échantillonnés sur le contour d'un cercle pour caler
# l'ellipse englobante : 8 suffisent (l'écart max au vrai cercle vaut 7,6 % du
# rayon, absorbé par `ellipse_margin`), et ça reste 2× moins de points que les
# 4 coins d'un carré ne coûtaient en précision inutile.
_CIRCLE_SAMPLES = 8

# Passes de resserrage du hub dans la zone (mesure → homothétie → reconstruction
# des ellipses). Le point fixe est atteint en 2-3 passes ; au-delà, c'est que
# l'anti-chevauchement bloque et qu'il faut assumer le débordement.
_FIT_PASSES = 4

# Étalement du hub dans la zone (dichotomie sur le facteur d'écartement).
# `_SPREAD_MAX` borne le cas dégénéré du duo, qu'un facteur libre enverrait à
# chaque bout de la planche ; 8 passes donnent le facteur à ~0,5 % près.
_SPREAD_MAX = 3.0
_SPREAD_PASSES = 8

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


def clean_track_title(title: str) -> str:
    """Titre allégé pour la légende : on garde le nom du morceau, rien d'autre.

    Sur une bulle, « Fiesta (Interlude) » ne dit rien de plus que « Fiesta » et
    coûte la moitié de la place — or la légende est posée sur l'ellipse, où la
    place est comptée. Les mentions entre parenthèses (interlude, feat., bonus,
    version) sautent donc. Ce qui est ENTRE parenthèses au milieu du titre est
    conservé : il fait partie du nom.
    """
    cleaned = (title or "").strip()
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
        # Producteur seul sur N morceaux → « N solo » (affiché DANS son carré).
        return (f"{collab_group.track_count} solo",)
    if collab_group.track_count <= style.label_track_threshold:
        return tuple(clean_track_title(t) for t in collab_group.track_titles)
    return (_count_label(collab_group.track_count),)


def _remove_overlaps(
    canvas: dict[str, tuple[float, float]], sizes: dict[str, float], style: SvgStyle
) -> dict[str, tuple[float, float]]:
    """Écarte itérativement les cercles qui se superposent (disques, pas points).

    `spring_layout` ignore la taille des nœuds → passe corrective : à chaque
    paire en collision, translation minimale le long de la ligne des centres,
    répartie sur les deux cercles. Ordre et positions fixes → déterministe.

    Sur des DISQUES la séparation est radiale (distance des centres ≥ somme des
    rayons + gap) : la règle « boîtes » d'avant écartait sur l'axe de moindre
    recouvrement, ce qui laissait deux cercles se frôler en diagonale.
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
                dist = math.hypot(ddx, ddy)
                if dist >= min_sep:
                    continue
                moved = True
                if dist < 1e-9:
                    # Centres confondus : aucune direction ne se dégage. On sépare
                    # horizontalement (choix arbitraire mais FIXE → déterministe).
                    ux, uy = 1.0, 0.0
                    dist = 0.0
                else:
                    ux, uy = ddx / dist, ddy / dist
                shift = (min_sep - dist) / 2.0
                pos[ki][0] -= ux * shift
                pos[ki][1] -= uy * shift
                pos[kj][0] += ux * shift
                pos[kj][1] += uy * shift
        if not moved:
            break
    return {k: (pos[k][0], pos[k][1]) for k in keys}


def _label_tip(
    ellipse, center_x: float, center_y: float, inward: bool = False
) -> tuple[float, int]:
    """Où poser la légende curviligne sur l'ellipse : `(paramètre t, sens)`.

    Au SOMMET (ou au creux) de l'ovale, pas à la pointe de son grand axe : c'est
    le seul point où la tangente est horizontale, donc le seul où le texte se
    lit à plat. Sur un ovale très incliné — le cas de tous les îlots — la pointe
    du grand axe est quasi verticale, et le titre s'y écrivait de haut en bas.

    Des deux (le sommet et le creux), on retient celui qui est le plus LOIN du
    centre du dessin : la légende part ainsi vers l'extérieur de l'image plutôt
    que de traverser le tas. `inward` inverse ce choix pour les îlots calés dans
    un coin, qui n'ont de place que du côté du centre.

    Le sens de parcours est ensuite choisi pour que les lettres avancent vers la
    droite : sur un chemin, elles suivent la tangente, et l'autre sens les écrit
    à l'envers.

    ⚠️ `ellipse` doit être celle qui PORTE le texte, c'est-à-dire l'ellipse
    écartée — pas le tracé visible. Le paramètre du sommet dépend du rapport
    `ry/rx` : écarter les deux axes de la même quantité arrondit l'ovale et
    déplace son sommet. Calculé sur le mauvais des deux, le titre atterrit sur
    un flanc et repart à la verticale.
    """
    a = math.radians(ellipse.angle)
    # dy/dt = 0 → le sommet et le creux, aux deux solutions opposées.
    t = math.degrees(math.atan2(ellipse.ry * math.cos(a), ellipse.rx * math.sin(a)))
    top, bottom = (t, t + 180.0)
    if ellipse.point_at(top)[1] > ellipse.point_at(bottom)[1]:
        top, bottom = bottom, top

    # Le point le plus ÉLOIGNÉ du centre du dessin : le titre part ainsi vers
    # l'extérieur de l'image au lieu de traverser le tas. Comparer les centres
    # d'ellipse ne suffisait pas — les gros ovales du noyau sont tous centrés au
    # milieu, et leurs titres se retrouvaient empilés là.
    def _far(t):
        px, py = ellipse.point_at(t)
        return (px - center_x) ** 2 + (py - center_y) ** 2

    outer, inner = (top, bottom) if _far(top) >= _far(bottom) else (bottom, top)
    chosen = inner if inward else outer
    tx, _ty = ellipse.tangent_at(chosen)
    return chosen, (1 if tx >= 0 else 0)


def _perimeter(rx: float, ry: float) -> float:
    """Périmètre d'ellipse (approximation de Ramanujan, exacte à 1e-5 près ici)."""
    h = (rx - ry) ** 2 / max(1e-9, (rx + ry) ** 2)
    return math.pi * (rx + ry) * (1.0 + 3.0 * h / (10.0 + math.sqrt(4.0 - 3.0 * h)))


def _label_offset(style: SvgStyle, ellipse=None, text: str = "") -> float:
    """Écart entre le tracé de l'ellipse et le texte curviligne posé dessus.

    Écart de base, PLUS ce qu'il faut pour que le titre ne s'enroule pas : sur
    un petit ovale, « 3ein / Risotto Gambas » couvrirait plus de la moitié du
    tour et se lirait à la verticale à ses extrémités. On écarte alors la
    couronne — son périmètre grandit d'environ 2π par pixel — jusqu'à ce que le
    texte n'en occupe plus qu'une fraction, donc reste à peu près droit.
    L'écart supplémentaire est plafonné : au-delà, la légende ne semblerait plus
    appartenir à son ovale.
    """
    base = style.ellipse_stroke_width / 2.0 + style.ellipse_label_gap
    if ellipse is None or not text:
        return base
    needed = len(text) * style.ellipse_label_font_size * _CHAR_WIDTH_RATIO
    target = needed / style.ellipse_label_max_arc
    extra = 0.0
    for _ in range(3):  # Newton : le périmètre croît de ~2π par pixel d'écart.
        perim = _perimeter(ellipse.rx + base + extra, ellipse.ry + base + extra)
        if perim >= target:
            break
        extra += (target - perim) / (2.0 * math.pi)
    return base + min(extra, style.ellipse_label_max_extra_offset)


# Largeur moyenne d'un caractère, en fraction de la taille de police. 0,55 est
# la valeur usuelle d'une grotesque comme Montserrat — il ne s'agit que de
# dimensionner la couronne, pas de mesurer le texte au pixel.
_CHAR_WIDTH_RATIO = 0.55


def _label_rings(ellipse, lines, style: SvgStyle, center_x, center_y, inward=False):
    """Un anneau par titre, empilés vers l'extérieur, dans l'ordre de lecture.

    Deux morceaux sur un même ovale s'écrivaient à la suite sur une seule
    couronne : la ligne faisait le tour de l'ovale et se lisait mal. Ils sont
    maintenant posés l'un « sous » l'autre, sur deux couronnes concentriques.

    Chaque anneau prend l'écart dont SON texte a besoin, sans jamais repasser
    sous le précédent (`ellipse_label_line_gap` les sépare). L'ordre suit la
    lecture : posée EN HAUT de l'ovale, la première ligne est la plus éloignée
    du tracé — c'est elle qui est le plus haut ; posée en bas, c'est l'inverse.
    """
    if not lines:
        return ()
    base = _label_offset(style)
    t_base, _sweep = _label_tip(ellipse.inflated(base), center_x, center_y, inward=inward)
    # Le texte est-il posé au-dessus de l'ovale ? Alors s'éloigner du tracé,
    # c'est monter, et la 1ʳᵉ ligne doit être la plus éloignée.
    above = ellipse.inflated(base + 1.0).point_at(t_base)[1] < ellipse.point_at(t_base)[1]

    offsets = []
    previous = None
    for line in lines:
        offset = _label_offset(style, ellipse, line)
        if previous is not None:
            offset = max(offset, previous + style.ellipse_label_line_gap)
        offsets.append(offset)
        previous = offset
    ordered = list(lines) if not above else list(reversed(lines))

    rings = []
    for text, offset in zip(ordered, offsets, strict=True):
        t, sweep = _label_tip(ellipse.inflated(offset), center_x, center_y, inward=inward)
        rings.append(LabelRing(text=text, offset=offset, t=t, sweep=sweep))
    if above:
        rings.reverse()  # rendu dans l'ordre de lecture
    return tuple(rings)


def _label_allowance(style: SvgStyle) -> float:
    """De combien une légende curviligne déborde de son ellipse.

    Elle est posée SUR le tracé, écartée vers l'extérieur : elle ne coûte plus
    que l'écart et la hauteur des lettres, là où une légende posée au bout de
    l'ellipse revendiquait la moitié de sa longueur — c'est ce qui étranglait la
    mise en page (le texte occupait le cadre pendant que les cercles se tassaient).
    """
    return (
        style.ellipse_stroke_width / 2.0 + style.ellipse_label_gap + style.ellipse_label_font_size
    )


def _cloud_half_extents(
    canvas: dict[str, tuple[float, float]], sizes: dict[str, float]
) -> tuple[float, float, float, float]:
    """Centre + demi-largeur/hauteur d'un nuage de cercles (boîte englobante)."""
    xs: list[float] = []
    ys: list[float] = []
    for key, (x, y) in canvas.items():
        half = sizes[key] / 2.0
        xs.extend((x - half, x + half))
        ys.extend((y - half, y + half))
    cx = (min(xs) + max(xs)) / 2.0
    cy = (min(ys) + max(ys)) / 2.0
    return cx, cy, (max(xs) - min(xs)) / 2.0, (max(ys) - min(ys)) / 2.0


def _zone_radius(angle: float, half_w: float, half_h: float) -> float:
    """Rayon de la zone (ellipse inscrite `half_w` × `half_h`) dans la direction `angle`.

    La zone étant PAYSAGE (860 × 520), un rayon isotrope laisserait deux gros
    vides à gauche et à droite : les pétales doivent s'étirer plus loin à
    l'horizontale qu'à la verticale, dans le rapport même de la zone.
    """
    ca, sa = math.cos(angle), math.sin(angle)
    return 1.0 / math.hypot(ca / half_w, sa / half_h)


def _radialize_main(
    canvas: dict[str, tuple[float, float]],
    sizes: dict[str, float],
    style: SvgStyle,
    collab_groups,
    fill_ratio: float,
) -> dict[str, tuple[float, float]]:
    """Répartit les feuilles du hub en angle (pétales sur 360°), clusters compacts.

    Le `spring_layout` oriente les pétales arbitrairement (souvent tassés d'un
    côté, ce qui les fait « pointer » vers les îlots par coïncidence). Ici :
    pivot = plus gros cercle (le hub). Les feuilles sont regroupées en **unités** :
    une combinaison de ≥ 3 feuilles forme un **cluster** posé en bloc compact à
    2 colonnes le long de sa direction (rayons étagés → ellipse étroite, membres
    PAS tous à la même distance du hub) ; les autres feuilles sont isolées et
    gardent leur rayon (plancher `hub_clearance` pour dégager le pivot). Les
    unités reçoivent des parts angulaires (cluster = 2 parts), dans leur ordre
    angulaire d'origine. Déterministe : tris explicites partout.

    `fill_ratio` = jusqu'où les feuilles isolées s'étirent vers le bord de la
    zone (0,85 quand le hub est seul ; réduit quand des îlots doivent tenir dans
    les coins, sinon les pétales viendraient les percuter).
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

    # Géométrie des clusters (départ, pas). La profondeur de référence des
    # feuilles isolées ne vient plus du contenu mais de la ZONE FIXE (voir
    # `_zone_radius` plus bas) : c'est elle qu'il s'agit d'occuper.
    cluster_geo: dict[int, tuple[float, float]] = {}
    for idx, (_, ks) in enumerate(units):
        if len(ks) > 1:
            biggest = max(sizes[k2] for k2 in ks)
            # Première rangée bien dégagée du hub (0.85), pas au gap PLEIN :
            # les membres d'un gros groupe respirent.
            r0 = (sizes[pivot] + biggest) * 0.85 + style.overlap_gap / 2.0
            step = biggest + style.overlap_gap
            cluster_geo[idx] = (r0, step)

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
            # Étirement vers le bord de la ZONE dans cette direction : le rayon
            # disponible suit la forme paysage de la zone (plus loin à
            # l'horizontale), sinon le hub dessine un disque au milieu d'un
            # rectangle et laisse deux vides sur les côtés.
            boundary = _zone_radius(
                a,
                style.frame_width / 2.0 - style.margin,
                style.frame_height / 2.0 - style.margin,
            )
            # Ce n'est pas le CENTRE du cercle qui doit tenir dans la zone mais
            # tout ce qui pend au bout du pétale : son rayon, l'ellipse qui
            # l'entoure et la légende posée derrière. Sans cette déduction, une
            # feuille poussée au bord emmène systématiquement son titre dehors.
            hang = sizes[k] / 2.0 + style.ellipse_margin + _label_allowance(style)
            target = max(0.0, boundary - hang)
            # Étirement vers le bord, PUIS plafond au même bord : le rayon hérité
            # du spring layout pouvait à lui seul sortir de la zone (le plafond
            # `floor_r × 1,25` ne connaît pas la zone, il ne connaît que le hub).
            # Le dégagement du hub reste prioritaire sur le plafond : mieux vaut
            # déborder que superposer deux cercles.
            r = max(floor_r, min(max(r, fill_ratio * target), target))
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


def _fit_to_zone(
    canvas: dict[str, tuple[float, float]], sizes: dict[str, float], style: SvgStyle
) -> dict[str, tuple[float, float]]:
    """Resserre les DISTANCES du nuage pour qu'il tienne dans la zone.

    Ne touche JAMAIS aux diamètres : seuls les écarts entre cercles sont réduits
    (homothétie des positions autour du centre du nuage). L'encodage visuel de
    la participation reste donc comparable d'un album à l'autre, alors que le
    vide, lui, n'a aucune raison d'être conservé — un duo layouté par
    `spring_layout` à `canvas_scale` fait 600 px de haut pour deux cercles.

    L'anti-chevauchement repasse APRÈS : sur un album vraiment dense il
    ré-écartera les cercles et le dessin débordera quand même. C'est voulu —
    mieux vaut un débordement signalé que des cercles qui se marchent dessus.
    """
    # Ce qui pend hors des cercles (ellipse + légende) doit tenir aussi.
    hang = style.ellipse_margin + _label_allowance(style)
    avail_w = style.frame_width / 2.0 - style.margin - hang
    avail_h = style.frame_height / 2.0 - style.margin - hang
    cx, cy, hw, hh = _cloud_half_extents(canvas, sizes)
    ratios = [1.0]
    if hw > 1e-9 and avail_w > 0:
        ratios.append(avail_w / hw)
    if hh > 1e-9 and avail_h > 0:
        ratios.append(avail_h / hh)
    ratio = min(ratios)
    if ratio >= 1.0:
        return canvas
    return {k: (cx + (x - cx) * ratio, cy + (y - cy) * ratio) for k, (x, y) in canvas.items()}


def _component_canvas(
    graph,
    comp: list[str],
    sizes: dict[str, float],
    style: SvgStyle,
    seed: int,
    is_main: bool,
    collab_groups=(),
    fill_ratio: float = 1.0,
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
        canvas = _radialize_main(canvas, sizes, style, collab_groups, fill_ratio)
    canvas = _remove_overlaps(canvas, sizes, style)
    if is_main:
        # Le hub occupe le centre de la zone : c'est lui qui doit y tenir (les
        # îlots sont calés aux coins ensuite, ils ne débordent pas par nature).
        canvas = _remove_overlaps(_fit_to_zone(canvas, sizes, style), sizes, style)
    cx, cy, _, _ = _cloud_half_extents(canvas, sizes)
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
    # Le hub n'occupe toute la zone que s'il y est seul : dès qu'il y a des
    # îlots à caler dans les coins, on lui laisse moins de place, sinon ses
    # pétales viennent buter dessus (la zone ne grandit plus pour absorber).
    fill_ratio = style.radial_fill if len(comps) == 1 else style.radial_fill_islands
    main_local = _component_canvas(
        graph,
        main,
        sizes,
        style,
        seed,
        is_main=True,
        collab_groups=collab_groups,
        fill_ratio=fill_ratio,
    )
    canvas.update(main_local)
    _, _, main_hw, main_hh = _cloud_half_extents(main_local, sizes)

    for idx, comp in enumerate(comps[1:]):
        local = _component_canvas(graph, comp, sizes, style, seed, is_main=False)
        _, _, hw, hh = _cloud_half_extents(local, sizes)
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
    """Assemble le `BubbleSpec` : composition par composante, ellipses, zone fixe.

    Chaque composante connexe est layoutée séparément (hub central, îlots dans
    les coins de la zone). Puis **une ellipse par combinaison de producteurs**
    (`CollabGroup`), légendée (titres ou « N morceaux ») et ancrée vers
    l'extérieur. Le contenu est centré dans la **zone fixe**
    `style.frame_width × frame_height` — jamais mis à l'échelle pour y tenir :
    un dépassement est signalé (`BubbleSpec.overflow`), pas corrigé.
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

    def _make_groups(canvas):
        """Ellipses + légendes pour un état donné du nuage.

        Refait à chaque resserrage du hub : une ellipse et sa légende dépendent
        des positions, on ne peut pas les calculer une fois pour toutes avant de
        savoir si le dessin tient dans la zone.
        """
        center_x = sum(canvas[k][0] for k in ordered) / len(canvas)
        center_y = sum(canvas[k][1] for k in ordered) / len(canvas)
        raw_groups = []
        for cg in collab_groups:
            member_pts = []
            for k in cg.keys:
                x, y = canvas[k]
                half = sizes[k] / 2.0
                member_pts.extend(
                    (
                        x + half * math.cos(2.0 * math.pi * i / _CIRCLE_SAMPLES),
                        y + half * math.sin(2.0 * math.pi * i / _CIRCLE_SAMPLES),
                    )
                    for i in range(_CIRCLE_SAMPLES)
                )
            ellipse = enclosing_shape(
                member_pts,
                padding=style.ellipse_margin,
                min_radius=style.ellipse_margin,
                min_axis_ratio=style.min_axis_ratio,
            )
            label_lines = _ellipse_label(cg, style)
            # Un seul traitement pour tout le monde, producteur solo compris :
            # la légende est curviligne sur son ellipse, il n'y a plus de cas
            # « où poser le texte ? » à distinguer.
            rings = _label_rings(
                ellipse,
                label_lines,
                style,
                center_x,
                center_y,
                inward=not set(cg.keys) <= main_set,
            )
            raw_groups.append((cg, ellipse, label_lines, rings))
        return raw_groups

    raw_groups = _make_groups(canvas)
    _state = {"canvas": canvas, "groups": raw_groups}

    def _bbox(keys, canvas=None, raw_groups=None, what="all"):
        """Boîte englobante des nœuds `keys`, à un des TROIS niveaux de budget.

        `canvas`/`raw_groups` explicites pour mesurer un état CANDIDAT (la passe
        d'étalement essaie plusieurs écartements avant d'en retenir un).

        Trois niveaux, du plus contraignant au plus lâche :

        - `"circles"` : les cercles seuls. Ils doivent tenir dans la zone MOINS
          la marge — c'est le seul budget vraiment dur.
        - `"ellipses"` : + les ovales. Ils ont droit à la marge : un ovale
          enveloppe forcément plusieurs cercles et déborde d'eux, l'exiger dans
          la même boîte que les cercles interdirait à ceux-ci d'approcher du bord.
        - `"all"` : + la couronne du titre curviligne. Elle n'est PAS une
          contrainte : le titre n'est qu'à quelques dizaines de pixels au-delà
          de son ovale, visiblement rattaché à lui, et le compter tassait tout
          le dessin au centre. L'aperçu SVG l'affiche en débord (`bleed`).
        """
        canvas = canvas if canvas is not None else _state["canvas"]
        raw_groups = raw_groups if raw_groups is not None else _state["groups"]
        subset = set(keys)
        xs: list[float] = []
        ys: list[float] = []
        for key in subset:
            px, py = canvas[key]
            half = sizes[key] / 2.0
            xs.extend((px - half, px + half))
            ys.extend((py - half, py + half))
        if what != "circles":
            for cg, ellipse, _lines, rings in raw_groups:
                if not set(cg.keys) <= subset:
                    continue
                x0, y0, x1, y1 = ellipse.bbox()
                outer = max((r.offset for r in rings), default=0.0)
                pad = (outer + style.ellipse_label_font_size) if what == "all" else 0.0
                xs.extend((x0 - pad, x1 + pad))
                ys.extend((y0 - pad, y1 + pad))
        return min(xs), min(ys), max(xs), max(ys)

    # Resserrage du hub, LÉGENDES COMPRISES. `_fit_to_zone` ne connaît que les
    # cercles, or ce qui sort de la zone est le plus souvent un titre posé au
    # bout d'un pétale. On mesure donc la boîte réelle, on resserre les
    # distances, on reconstruit les ellipses — quelques passes suffisent, et
    # l'anti-chevauchement finit par s'y opposer : un album dense débordera
    # plutôt que de voir ses cercles se coller.
    # SEULS LES CERCLES sont contraints (zone moins la marge). Un ovale enveloppe
    # plusieurs cercles et déborde forcément d'eux : l'exiger dans le cadre
    # empêchait les cercles d'en approcher — sur M.A.N le noyau se retrouvait sur
    # 44 % de la largeur pour 93 % de la hauteur, un boudin vertical au milieu.
    # Un ovale coupé par le bord est d'ailleurs conforme à la DA (cf. maquette).
    avail_w = style.frame_width - 2 * style.margin
    avail_h = style.frame_height - 2 * style.margin
    main_sizes = {k: sizes[k] for k in comps[0]}
    for _ in range(_FIT_PASSES):
        bx0, by0, bx1, by1 = _bbox(comps[0], what="circles")
        ratios = [1.0]
        if bx1 - bx0 > avail_w:
            ratios.append(avail_w / (bx1 - bx0))
        if by1 - by0 > avail_h:
            ratios.append(avail_h / (by1 - by0))
        ratio = min(ratios)
        if ratio > 0.999:
            break
        cx, cy = (bx0 + bx1) / 2.0, (by0 + by1) / 2.0
        shrunk = {
            k: (cx + (canvas[k][0] - cx) * ratio, cy + (canvas[k][1] - cy) * ratio)
            for k in comps[0]
        }
        canvas = {**canvas, **_remove_overlaps(shrunk, main_sizes, style)}
        raw_groups = _make_groups(canvas)
        _state["canvas"], _state["groups"] = canvas, raw_groups

    # Zone FIXE : rien n'est mis à l'échelle pour y tenir (l'échelle doit rester
    # comparable d'un album à l'autre — cf. `_node_size`).
    #
    # C'est le HUB qu'on centre, pas le contenu entier : les îlots sont calés aux
    # coins de la zone juste après, donc les inclure dans le centrage décalerait
    # tout le dessin d'un côté (et les îlots seraient recalés depuis un repère
    # devenu faux). Sans îlot, hub = contenu et le centrage est le même.
    min_x, min_y, max_x, max_y = _bbox(comps[0], what="circles")
    content_w = max_x - min_x
    content_h = max_y - min_y
    width = style.frame_width
    height = style.frame_height
    dx = (width - content_w) / 2.0 - min_x
    dy = (height - content_h) / 2.0 - min_y
    frame = None
    if style.draw_frame:
        frame = (0.0, 0.0, width, height)

        # Îlots calés aux EXTRÉMITÉS de la zone (coin intérieur - marge - pad).
        # Translation rigide par composante (repère brut) : nœuds + ellipses +
        # ancres de légende bougent ensemble.
        raw_fx0 = style.margin - dx
        raw_fx1 = (width - style.margin) - dx
        raw_fy0 = style.margin - dy
        raw_fy1 = (height - style.margin) - dy
        pad = style.island_corner_pad
        for idx, comp in enumerate(comps[1:]):
            comp_set = set(comp)
            # Boîte englobante de l'îlot : cercles + ellipses (budget dur), et
            # la même légendes comprises, qui servira à retenir la translation.
            bx0, by0, bx1, by1 = _bbox(comp, what="circles")
            ax0, ay0, ax1, ay1 = _bbox(comp, what="all")
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
            # Un îlot poussé dans son coin par ses seuls cercles y emmène sa
            # légende, qui déborde alors du cadre : on retient la translation
            # juste assez pour que le titre reste dedans (il a droit à la marge,
            # pas au-delà — même règle que pour le hub).
            tx = min(max(tx, (-dx) - ax0), (width - dx) - ax1)
            ty = min(max(ty, (-dy) - ay0), (height - dy) - ay1)
            for k in comp:
                canvas[k] = (canvas[k][0] + tx, canvas[k][1] + ty)
            for i, (cg, ellipse, label_lines, rings) in enumerate(raw_groups):
                if set(cg.keys) <= comp_set:
                    # Les légendes suivent leur ellipse : une translation ne
                    # change ni leur point d'ancrage ni leur sens de lecture.
                    raw_groups[i] = (cg, _shift_ellipse(ellipse, tx, ty), label_lines, rings)

    # ── Étalement : occuper la zone au lieu de se tasser au centre ───────────
    # Le resserrage ci-dessus ne sait que RÉDUIRE. Sur la plupart des albums le
    # hub finit donc bien plus petit que la zone, avec de grands vides entre lui
    # et les îlots des coins. On écarte ici les cercles du hub (leurs DISTANCES,
    # jamais leurs diamètres) jusqu'à ce qu'il touche soit le bord de la zone,
    # soit un îlot. Recherche par dichotomie sur le facteur : chaque candidat est
    # mesuré pour de vrai (ellipses et légendes reconstruites), car les légendes
    # ne grandissent PAS avec l'écartement — un facteur appliqué à l'aveugle
    # sortirait du cadre.
    hub_keys = comps[0]
    if len(hub_keys) > 1:
        zx0, zy0 = style.margin - dx, style.margin - dy
        zx1, zy1 = (width - style.margin) - dx, (height - style.margin) - dy
        # Les îlots sont figés (calés aux coins) : on retient leurs CERCLES.
        island_circles = [
            (canvas[k][0], canvas[k][1], sizes[k] / 2.0) for comp in comps[1:] for k in comp
        ]
        gap = style.component_gap
        hx0, hy0, hx1, hy1 = _bbox(hub_keys, canvas, raw_groups, what="circles")
        pivot_x, pivot_y = (hx0 + hx1) / 2.0, (hy0 + hy1) / 2.0

        def _spread(fx, fy, base=None):
            """État candidat : hub écarté de `fx`/`fy` autour de son centre."""
            base = base if base is not None else canvas
            moved = {
                k: (
                    pivot_x + (base[k][0] - pivot_x) * fx,
                    pivot_y + (base[k][1] - pivot_y) * fy,
                )
                for k in hub_keys
            }
            cand = {**base, **moved}
            return cand, _make_groups(cand)

        def _fits(cand, box):
            """Le hub tient-il dans la zone sans venir toucher un îlot ?

            Seuls les CERCLES sont bornés (zone moins la marge) : leurs ovales
            ont le droit d'être coupés par le bord, c'est même ce que montre la
            maquette. La proximité des îlots, elle, se teste
            CERCLE À CERCLE : la boîte d'un hub large recouvre forcément la
            bande d'un îlot de coin, et un test de boîtes bloquerait tout
            étalement dès le premier pixel. Ce qui doit être garanti, c'est que
            deux cercles ne se marchent pas dessus.
            """
            if box[0] < zx0 or box[1] < zy0 or box[2] > zx1 or box[3] > zy1:
                return False
            for k in hub_keys:
                hx, hy = cand[k]
                hr = sizes[k] / 2.0
                for ix, iy, ir in island_circles:
                    if math.hypot(hx - ix, hy - iy) < hr + ir + gap:
                        return False
            return True

        def _best_factor(axis, base):
            """Plus grand écartement tenable sur cet axe seul, par dichotomie."""

            def candidate(f):
                fx, fy = (f, 1.0) if axis == "x" else (1.0, f)
                cand, cand_groups = _spread(fx, fy, base)
                return _fits(cand, _bbox(hub_keys, cand, cand_groups, what="circles"))

            if candidate(_SPREAD_MAX):
                return _SPREAD_MAX
            lo, hi = 1.0, _SPREAD_MAX
            for _ in range(_SPREAD_PASSES):
                mid = (lo + hi) / 2.0
                if candidate(mid):
                    lo = mid
                else:
                    hi = mid
            return lo

        # Un facteur PAR AXE, et non un facteur unique : dans une zone paysage,
        # la hauteur sature bien avant la largeur (le hub touche déjà le haut et
        # le bas alors qu'il reste deux grands vides à gauche et à droite). Un
        # facteur commun serait donc bloqué à 1 par l'axe le plus contraint et
        # n'étalerait rien. Les cercles gardent leur diamètre : seules les
        # DISTANCES s'allongent, le nuage s'ovalise à l'image de la zone.
        # Deux tours : élargir en x libère parfois du jeu en y, et inversement.
        for axis in ("x", "y", "x", "y"):
            factor = _best_factor(axis, canvas)
            if factor <= 1.0 + 1e-3:
                continue
            fx, fy = (factor, 1.0) if axis == "x" else (1.0, factor)
            canvas, raw_groups = _spread(fx, fy, canvas)
            _state["canvas"], _state["groups"] = canvas, raw_groups

    # Dépassement de la zone, mesuré APRÈS le calage des îlots (qui déplace du
    # contenu) et sur les mêmes éléments que le cadrage : cercles, ellipses,
    # légendes. Le contenu n'est pas réduit pour rentrer — on se contente de le
    # DIRE (`BubbleSpec.overflow`), à charge de l'appelant d'avertir.
    fx0, fy0, fx1, fy1 = _bbox(graph.nodes, canvas, raw_groups, what="circles")
    over_w = max(0.0, (fx1 - fx0) - width)
    over_h = max(0.0, (fy1 - fy0) - height)
    overflow = (over_w, over_h) if (over_w > 0 or over_h > 0) else None

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
            label_font_size=_label_font_size(sizes[key], style),
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
            rings=rings,
            track_count=cg.track_count,
        )
        for cg, ellipse, label_lines, rings in raw_groups
    )

    return BubbleSpec(
        width=width,
        height=height,
        nodes=nodes,
        edges=edges,
        groups=groups_shapes,
        style=style,
        frame=frame,
        overflow=overflow,
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
    kind: str = "prod",
    style: SvgStyle | None = None,
    seed: int = DEFAULT_SEED,
    output_path=None,
) -> BubbleResult:
    """Cœur commun Bubble Prod / Bubble Feat : le réseau des crédits `roles`.

    Écrit DEUX fichiers côte à côte, comme « Structure » : le `.svg` (aperçu de
    contrôle) et le `.json` (données de la planche Illustrator). `credit_label`
    sert aux messages d'erreur (« producteur », « featuring »), `filename` au
    chemin de sortie par défaut, `kind` distingue prod/feat dans le payload.
    Lève `ValueError` si l'album n'a aucun morceau ou aucun crédit dans `roles`.
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
        kind="prod",
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
