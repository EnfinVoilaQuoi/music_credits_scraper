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

sous DEUX contraintes dures : l'inclinaison (`ellipse_label_max_angle`, un texte
trop penché ne se lit plus) et le cadre — un titre hors zone est un titre perdu,
aucune qualité ne rachète ça. Le terme `w_zone` ne sert plus qu'à départager
quand AUCUN emplacement ne tient dedans.

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

# Allers-retours « écart ↔ côté » pour poser une ligne. Le point fixe est
# atteint en une ou deux passes ; la borne évite un aller-retour perpétuel
# quand deux emplacements se valent exactement.
_POINT_FIXE_PASSES = 4


def _perimeter(rx: float, ry: float) -> float:
    """Périmètre d'ellipse (approximation de Ramanujan, exacte à 1e-5 près ici)."""
    h = (rx - ry) ** 2 / max(1e-9, (rx + ry) ** 2)
    return math.pi * (rx + ry) * (1.0 + 3.0 * h / (10.0 + math.sqrt(4.0 - 3.0 * h)))


def label_offset(style: SvgStyle, ellipse=None, text: str = "") -> float:
    """Écart entre le tracé de l'ellipse et le texte curviligne posé dessus.

    L'écart est **fixe par principe** : c'est ce qui rattache le titre à son
    ovale, et un écart qui varie d'un titre à l'autre se lit comme un défaut
    (retour utilisateur du 2026-09-01 : « certains partent trop loin de leur
    cercle, j'aimerais garder un espace fixe »).

    Une seule exception, bornée court par `ellipse_label_max_extra_offset` : un
    texte qui couvrirait plus de `ellipse_label_max_arc` du tour s'enroule et
    se lit à la verticale à ses extrémités. On écarte alors un peu la couronne
    — son périmètre grandit d'environ 2π par pixel. Le vrai remède pour un titre
    long est ailleurs : le COUPER EN DEUX (`split_text`), un morceau en haut, un
    en bas. L'écartement ne fait que rattraper le reliquat.
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


def _tient_sur_le_tour(ellipse, text: str, style: SvgStyle) -> bool:
    """Le texte tient-il dans la part de tour autorisée, à l'écart FIXE ?

    Mesuré sur la couronne porteuse (l'ellipse écartée de l'écart de base), et
    non sur le tracé visible : c'est là que le texte est réellement posé.
    """
    base = style.ellipse_stroke_width / 2.0 + style.ellipse_label_gap
    perim = _perimeter(ellipse.rx + base, ellipse.ry + base)
    needed = len(text) * style.ellipse_label_font_size * _CHAR_WIDTH_RATIO
    return needed <= perim * style.ellipse_label_max_arc


def split_text(text: str) -> tuple[str, ...] | None:
    """Coupe un titre en deux moitiés, sur l'espace le plus proche du milieu.

    Pour un titre long sur un petit ovale — « On sourit pas sur les photos » sur
    la bulle d'un producteur solo —, mieux vaut deux moitiés posées de part et
    d'autre qu'une ligne écartée si loin qu'elle ne semble plus rattachée à rien.

    Renvoie `None` quand la coupe n'a pas de sens : moins de deux mots (un mot
    seul ne se coupe pas au milieu), ou une moitié qui resterait vide.
    """
    mots = (text or "").split()
    if len(mots) < 2:
        return None
    milieu = len(text) / 2.0
    meilleur, ecart = 1, math.inf
    longueur = 0
    for i, mot in enumerate(mots[:-1]):
        longueur += len(mot) + (1 if i else 0)
        if abs(longueur - milieu) < ecart:
            ecart, meilleur = abs(longueur - milieu), i + 1
    haut, bas = " ".join(mots[:meilleur]), " ".join(mots[meilleur:])
    return (haut, bas) if haut and bas else None


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


def _tip(
    ellipse, style: SvgStyle, text: str, members, obstacles, zone, cote: int = 0
) -> tuple[float, int]:
    """Meilleur emplacement `(paramètre t, sens de parcours)` pour ce titre.

    Le sens est choisi pour que les lettres avancent vers la droite : sur un
    chemin elles suivent la tangente, et l'autre sens les écrit à l'envers.

    `cote` impose la moitié de l'ovale où chercher (`+1` au-dessus du centre,
    `-1` en dessous, `0` libre) : c'est ce qui pose les deux moitiés d'un titre
    coupé de part et d'autre, au lieu de les laisser se choisir le même bel
    emplacement et se chevaucher.
    """
    span = text_span(ellipse, text, style)
    best = None
    best_dehors = None  # repli si rien ne tient dans le cadre
    for i in range(_TIP_SAMPLES):
        t = i * 360.0 / _TIP_SAMPLES
        if cote and (ellipse.point_at(t)[1] - ellipse.cy) * cote > 0.0:
            continue  # mauvaise moitié (y croît vers le BAS en SVG)
        tx, ty = ellipse.tangent_at(t)
        angle = ((math.degrees(math.atan2(ty, tx)) + 90.0) % 180.0) - 90.0  # → (-90, 90]
        if abs(angle) > style.ellipse_label_max_angle:
            continue  # illisible : aucune autre qualité ne rachète ça
        dehors = _outside(ellipse, t, span, zone)
        score = (
            style.label_weight_clearance * _clearance(ellipse, t, span, obstacles)
            - style.label_weight_member * _distance_to_members(ellipse, t, members)
            - style.label_weight_zone * dehors
        )
        # `t` départage à score égal : le choix reste le même d'une exécution à
        # l'autre, sans dépendre de l'ordre d'itération des flottants.
        candidat = (score, t, tx)
        if best_dehors is None or (score, -t) > (best_dehors[0], -best_dehors[1]):
            best_dehors = candidat
        if dehors > 0.0:
            continue  # hors cadre : écarté tant qu'il reste un emplacement dedans
        if best is None or (score, -t) > (best[0], -best[1]):
            best = candidat
    best = best or best_dehors
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


def _cote_haut(ellipse, offset: float, t: float) -> bool:
    """Le texte posé en `t` est-il AU-DESSUS de l'ovale ?

    C'est-à-dire : s'éloigner du tracé, à cet endroit, fait-il monter ? La
    réponse décide de deux choses — le sens d'empilement des lignes, et s'il
    faut décaler la ligne de base.
    """
    return ellipse.inflated(offset + 1.0).point_at(t)[1] < ellipse.inflated(offset).point_at(t)[1]


def _pose_une_ligne(ellipse, text, style, members, obstacles, zone, plancher, cote=0):
    """Pose UNE ligne : son écart au tracé, où la centrer, dans quel sens.

    Par POINT FIXE, et c'est le point délicat. L'écart dépend du côté (sous
    l'ovale, les lettres poussent vers lui : sans un décalage d'une hauteur de
    capitale, le tracé les barre), mais le côté n'est connu qu'une fois la place
    choisie — et changer l'écart peut déplacer la place, donc changer le côté.

    Décidé une fois pour toutes AVANT le placement — ce qu'on faisait — le
    décalage se retrouvait appliqué à un texte finalement posé de l'autre côté :
    il s'éloignait alors du double de ce qu'il fallait, et paraissait ne plus
    être relié à rien. On itère donc jusqu'à ce que les deux s'accordent.
    """
    socle = max(plancher, label_offset(style, ellipse, text))
    offset = socle
    t, sweep = _tip(ellipse.inflated(offset), style, text, members, obstacles, zone, cote)
    haut = _cote_haut(ellipse, offset, t)
    vus = {offset}
    for _ in range(_POINT_FIXE_PASSES):
        voulu = socle if haut else socle + style.ellipse_label_font_size
        if abs(voulu - offset) < 0.5:
            break  # l'écart et le côté s'accordent : c'est fini
        if voulu in vus:
            # Aller-retour : les deux emplacements se valent, le côté bascule à
            # chaque essai. On tranche pour le PLUS ÉCARTÉ — un texte un peu
            # loin du tracé reste lisible, un texte barré par le tracé, non.
            offset = socle + style.ellipse_label_font_size
            t, sweep = _tip(ellipse.inflated(offset), style, text, members, obstacles, zone, cote)
            break
        vus.add(voulu)
        offset = voulu
        t, sweep = _tip(ellipse.inflated(offset), style, text, members, obstacles, zone, cote)
        haut = _cote_haut(ellipse, offset, t)
    return LabelRing(text=text, offset=offset, t=t, sweep=sweep), haut


def _rings(ellipse, lines, style: SvgStyle, members, obstacles, zone) -> tuple[LabelRing, ...]:
    """Les titres d'un ovale : chacun à l'écart FIXE, répartis autour du tracé.

    **Deux textes se posent DE PART ET D'AUTRE** (un en haut, un en bas), et non
    plus empilés sur des couronnes concentriques : l'empilement éloignait le
    second de l'écart d'une ligne — mesuré jusqu'à 75 px du tracé, ce que
    l'utilisateur voyait comme « des titres qui partent trop loin de leur
    cercle ». Sur le corpus, aucun ovale n'en porte plus de deux (24 ovales à
    un titre, 5 à deux) : l'empilement ne subsiste que pour ce cas théorique.

    Deux textes, deux origines : soit l'ovale porte deux morceaux distincts,
    soit il porte UN titre trop long pour le tour de son ovale (bulle d'un
    producteur solo) — coupé en deux plutôt qu'écarté.
    """
    if not lines:
        return ()

    if (
        style.split_long_titles
        and len(lines) == 1
        and not _tient_sur_le_tour(ellipse, lines[0], style)
    ):
        moities = split_text(lines[0])
        if moities is not None:
            return _rings_haut_bas(ellipse, moities, style, members, obstacles, zone)

    if len(lines) == 2:
        return _rings_haut_bas(ellipse, tuple(lines), style, members, obstacles, zone)

    # Trois titres ou plus (jamais rencontré sur le corpus — `label_track_threshold`
    # bascule sur « N morceaux » au-delà) : faute de place autour du tracé, ils
    # s'empilent sur des couronnes concentriques, dans l'ordre de lecture.
    # La première ligne fixe le côté ; les suivantes s'empilent vers l'extérieur.
    premier, haut = _pose_une_ligne(
        ellipse, lines[0], style, members, obstacles, zone, label_offset(style)
    )
    rings = [premier]
    plancher = premier.offset
    for text in lines[1:]:
        plancher += style.ellipse_label_line_gap
        suivant, _ = _pose_une_ligne(ellipse, text, style, members, obstacles, zone, plancher)
        plancher = suivant.offset
        rings.append(suivant)

    # Posé AU-DESSUS de l'ovale, s'éloigner du tracé c'est monter : la dernière
    # couronne est la plus haute, donc c'est elle qui doit porter la 1ʳᵉ ligne.
    if haut:
        textes = [r.text for r in rings][::-1]
        rings = [
            LabelRing(text=texte, offset=r.offset, t=r.t, sweep=r.sweep)
            for texte, r in zip(textes, rings, strict=True)
        ][::-1]
    return tuple(rings)


def _rings_haut_bas(ellipse, textes, style, members, obstacles, zone) -> tuple[LabelRing, ...]:
    """Deux textes de part et d'autre : le 1ᵉʳ en HAUT, le 2ᵈ en BAS.

    Sert aux deux cas — les deux moitiés d'un titre coupé, ou deux morceaux
    distincts sur le même ovale. Chacun est posé à l'écart FIXE, sur sa propre
    moitié d'ovale : le côté est IMPOSÉ (`cote`), pas négocié, sinon les deux se
    choisiraient le même bel emplacement. Le premier devient un obstacle pour le
    second, comme deux titres voisins.
    """
    ecart = label_offset(style)
    haut_texte, bas_texte = textes[0], textes[1]
    ring_haut, _ = _pose_une_ligne(
        ellipse, haut_texte, style, members, obstacles, zone, ecart, cote=1
    )
    obstacles_bas = tuple(obstacles) + tuple(ring_obstacles(ellipse, (ring_haut,), style))
    ring_bas, _ = _pose_une_ligne(
        ellipse, bas_texte, style, members, obstacles_bas, zone, ecart, cote=-1
    )
    return (ring_haut, ring_bas)


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
