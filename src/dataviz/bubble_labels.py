"""Placement des titres curvilignes sur les ellipses Bubble.

Un titre est posé SUR le tracé de son ovale, écarté vers l'extérieur pour que la
ligne ne barre pas les lettres. Reste à choisir OÙ sur le tour, et dans quel sens
le parcourir.

La version précédente enchaînait des filtres (angle, puis cadre, puis tri par
place libre). Un titre pouvait donc glisser très loin de ses propres cercles
pourvu que ce fût dégagé — « AhGars! » flottait dans le vide, et rien ne disait
plus à quel ovale « Peace, Haine, Love » appartenait. Ici, **un seul score** :

    score = w_place  × dégagement autour du texte
          − w_membre × distance au cercle membre le plus proche
          − w_zone   × ce qui sort de la zone

sous la seule contrainte dure de l'inclinaison (`ellipse_label_max_angle`) : un
texte trop penché ne se lit plus, ça ne se compense pas.

Le terme `w_membre` est la traduction directe de « un titre doit être
rattachable à son ovale » — la règle qui manquait.
"""

import math

from src.dataviz.bubble_svg import LabelRing, SvgStyle

# Largeur moyenne d'un caractère, en fraction de la taille de police. 0,55 est
# la valeur usuelle d'une grotesque comme Montserrat — il ne s'agit que de
# dimensionner l'arc occupé, pas de mesurer le texte au pixel.
_CHAR_WIDTH_RATIO = 0.55

# Pas de balayage du contour pour chercher un emplacement (tous les 2°).
_TIP_SAMPLES = 180

# Points testés le long de l'arc d'un titre (dégagement, sortie de zone).
_ARC_PROBES = 7


def _perimeter(rx: float, ry: float) -> float:
    """Périmètre d'ellipse (approximation de Ramanujan, exacte à 1e-5 près ici)."""
    h = (rx - ry) ** 2 / max(1e-9, (rx + ry) ** 2)
    return math.pi * (rx + ry) * (1.0 + 3.0 * h / (10.0 + math.sqrt(4.0 - 3.0 * h)))


def label_offset(style: SvgStyle, ellipse=None, text: str = "") -> float:
    """Écart entre le tracé de l'ellipse et le texte curviligne posé dessus.

    Écart de base, PLUS ce qu'il faut pour que le titre ne s'enroule pas : sur
    un petit ovale, « 3ein / Risotto Gambas » couvrirait plus de la moitié du
    tour et se lirait à la verticale à ses extrémités. On écarte alors la
    couronne — son périmètre grandit d'environ 2π par pixel — jusqu'à ce que le
    texte n'en occupe plus qu'une fraction. L'écart supplémentaire est plafonné :
    au-delà, la légende ne semblerait plus appartenir à son ovale.
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


def label_allowance(style: SvgStyle) -> float:
    """De combien une légende curviligne déborde de son ellipse, au minimum.

    Elle est posée SUR le tracé, écartée vers l'extérieur : elle ne coûte que
    l'écart et la hauteur des lettres, là où une légende plantée au bout de
    l'ellipse revendiquait la moitié de sa longueur.
    """
    return label_offset(style) + style.ellipse_label_font_size


def text_span(ellipse, text: str, style: SvgStyle) -> float:
    """Demi-ouverture angulaire occupée par `text` sur `ellipse`, en degrés."""
    half = len(text) * style.ellipse_label_font_size * _CHAR_WIDTH_RATIO / 2.0
    return math.degrees(half / max(1e-9, min(ellipse.rx, ellipse.ry)))


def _arc_points(ellipse, t_center: float, span_deg: float):
    """Jalons le long de l'arc qu'occuperait le texte."""
    for i in range(_ARC_PROBES):
        t = t_center - span_deg + 2.0 * span_deg * i / (_ARC_PROBES - 1)
        yield ellipse.point_at(t)


def _clearance(ellipse, t_center, span_deg, obstacles) -> float:
    """Plus petit dégagement de l'arc vis-à-vis des obstacles (cercles, titres posés).

    Négatif quand le titre passerait DANS un obstacle : le score l'écarte alors
    naturellement, sans avoir besoin d'un filtre à part.
    """
    if not obstacles:
        return 0.0
    worst = math.inf
    for px, py in _arc_points(ellipse, t_center, span_deg):
        for cx, cy, radius in obstacles:
            worst = min(worst, math.hypot(px - cx, py - cy) - radius)
    return worst


def _outside(ellipse, t_center, span_deg, zone) -> float:
    """De combien l'arc sort de la zone (0 s'il tient dedans)."""
    width, height = zone
    worst = 0.0
    for px, py in _arc_points(ellipse, t_center, span_deg):
        worst = max(worst, -px, -py, px - width, py - height)
    return max(0.0, worst)


def _distance_to_members(ellipse, t_center, members) -> float:
    """Distance du milieu du texte au bord du cercle membre le plus proche.

    C'est ce qui rattache visuellement un titre à son ovale : posé sur un arc
    éloigné de tous ses membres, il devient impossible à attribuer.
    """
    if not members:
        return 0.0
    px, py = ellipse.point_at(t_center)
    return min(math.hypot(px - cx, py - cy) - radius for cx, cy, radius in members)


def _tip(ellipse, style: SvgStyle, text: str, members, obstacles, zone) -> tuple[float, int]:
    """Meilleur emplacement `(paramètre t, sens de parcours)` pour ce titre.

    Le sens est choisi pour que les lettres avancent vers la droite : sur un
    chemin elles suivent la tangente, et l'autre sens les écrit à l'envers.
    """
    span = text_span(ellipse, text, style)
    best = None
    for i in range(_TIP_SAMPLES):
        t = i * 360.0 / _TIP_SAMPLES
        tx, ty = ellipse.tangent_at(t)
        angle = ((math.degrees(math.atan2(ty, tx)) + 90.0) % 180.0) - 90.0  # → (-90, 90]
        if abs(angle) > style.ellipse_label_max_angle:
            continue  # illisible : aucune autre qualité ne rachète ça
        score = (
            style.label_weight_clearance * _clearance(ellipse, t, span, obstacles)
            - style.label_weight_member * _distance_to_members(ellipse, t, members)
            - style.label_weight_zone * _outside(ellipse, t, span, zone)
        )
        # `t` départage à score égal : le choix reste le même d'une exécution à
        # l'autre, sans dépendre de l'ordre d'itération des flottants.
        if best is None or (score, -t) > (best[0], -best[1]):
            best = (score, t, tx)
    if best is None:
        # Ovale si vertical qu'aucun point n'est lisible : on prend son sommet,
        # le moins mauvais des compromis.
        a = math.radians(ellipse.angle)
        t = math.degrees(math.atan2(ellipse.ry * math.cos(a), ellipse.rx * math.sin(a)))
        if ellipse.point_at(t)[1] > ellipse.point_at(t + 180.0)[1]:
            t += 180.0
        tx, _ty = ellipse.tangent_at(t)
        return t, (1 if tx >= 0 else 0)
    return best[1], (1 if best[2] >= 0 else 0)


def _rings(ellipse, lines, style: SvgStyle, members, obstacles, zone) -> tuple[LabelRing, ...]:
    """Un anneau par titre, empilés vers l'extérieur, dans l'ordre de lecture.

    Deux morceaux sur un même ovale s'écrivaient à la suite sur une seule
    couronne : la ligne faisait le tour et se lisait mal. Ils sont posés l'un
    « sous » l'autre, sur deux couronnes concentriques.
    """
    if not lines:
        return ()
    base = label_offset(style)
    t_base, _sweep = _tip(ellipse.inflated(base), style, lines[0], members, obstacles, zone)
    # Le texte est-il posé au-dessus de l'ovale ? Alors s'éloigner du tracé,
    # c'est monter, et la 1ʳᵉ ligne doit être la plus éloignée pour se lire en
    # premier. Posé dessous, c'est l'inverse.
    above = ellipse.inflated(base + 1.0).point_at(t_base)[1] < ellipse.point_at(t_base)[1]
    # Côté BAS, les lettres poussent vers l'ovale : sur un chemin elles se
    # dressent à gauche du sens de lecture, qui pointe vers l'intérieur quand le
    # texte est sous la courbe. Sans ce décalage d'une hauteur de capitale, le
    # tracé barre le titre.
    baseline = 0.0 if above else style.ellipse_label_font_size

    offsets = []
    previous = None
    for line in lines:
        offset = label_offset(style, ellipse, line) + baseline
        if previous is not None:
            offset = max(offset, previous + style.ellipse_label_line_gap)
        offsets.append(offset)
        previous = offset
    ordered = list(lines) if not above else list(reversed(lines))

    rings = []
    for text, offset in zip(ordered, offsets, strict=True):
        carrier = ellipse.inflated(offset)
        t, sweep = _tip(carrier, style, text, members, obstacles, zone)
        rings.append(LabelRing(text=text, offset=offset, t=t, sweep=sweep))
    if above:
        rings.reverse()  # rendu dans l'ordre de lecture
    return tuple(rings)


def ring_obstacles(ellipse, rings, style: SvgStyle) -> list[tuple[float, float, float]]:
    """Jalons occupés par des titres déjà posés, pour que les suivants s'écartent.

    Un titre curviligne est approché par une poignée de disques le long de son
    arc : c'est grossier, mais il suffit que le voisin renonce à l'emplacement.
    """
    marks: list[tuple[float, float, float]] = []
    radius = style.ellipse_label_font_size * 0.9
    for ring in rings:
        carrier = ellipse.inflated(ring.offset)
        span = text_span(carrier, ring.text, style)
        marks.extend((px, py, radius) for px, py in _arc_points(carrier, ring.t, span))
    return marks


def place(entries, circles, style: SvgStyle, zone):
    """Pose les titres de tous les groupes, dans l'ordre.

    `entries` = `(clés_membres, ellipse, lignes)` par groupe ; `circles` =
    `clé → (x, y, rayon)`. Renvoie un tuple d'anneaux par entrée, dans le même
    ordre. Les titres déjà posés deviennent des obstacles pour les suivants,
    sinon deux voisins se choisissent le même bel emplacement.
    """
    obstacles = [circles[k] for k in sorted(circles)]
    placed = []
    for member_keys, ellipse, lines in entries:
        members = [circles[k] for k in member_keys if k in circles]
        rings = _rings(ellipse, lines, style, members, tuple(obstacles), zone)
        obstacles.extend(ring_obstacles(ellipse, rings, style))
        placed.append(rings)
    return placed
