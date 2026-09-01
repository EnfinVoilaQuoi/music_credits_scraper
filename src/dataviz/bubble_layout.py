"""Placement des cercles Bubble : une relaxation sous contraintes explicites.

Remplace six passes correctives empilées au fil des retours sur planche
(resserrage, calage des îlots aux coins, séparation îlots↔îlots, séparation
noyau↔îlots, étalement par dichotomie). Chacune corrigeait un symptôme ; aucune
n'énonçait la règle. Deux défauts passaient donc au travers : un cercle étranger
capturé par une ellipse, et un dessin tassé au centre.

Les règles sont désormais dites une fois, sous forme de **forces** :

- `lloyd` — chaque cercle se déplace vers le barycentre de la part de zone
  qu'il occupe (relaxation de Lloyd, dite « de Voronoï »). C'est ce qui donne
  une répartition HOMOGÈNE : un vide est une part que personne ne réclame, et
  le cercle le plus proche y est attiré ;
- `cohesion` — les membres d'un même groupe se rapprochent : un groupe compact
  donne une petite ellipse, donc peu d'occasions d'attraper un étranger ;
- `exclusion` — un cercle NON membre est repoussé hors de l'ellipse d'un groupe ;
- `disjonction` — deux ovales SANS MEMBRE COMMUN ne se croisent pas. Deux ovales
  qui partagent un artiste doivent forcément se recouvrir (ils passent tous deux
  par lui) ; deux ovales étrangers l'un à l'autre, non — leur croisement ne dit
  rien et brouille la lecture ;
- `separation` — deux cercles gardent `gap` entre eux, quelle que soit leur
  composante. CONTRAINTE, pas force : projetée après coup ;
- `containment` — rien ne sort de la zone.

La version précédente répartissait par répulsion mutuelle bornée et étirait le
nuage jusqu'à ce que sa BOÎTE remplisse la zone. La boîte remplissait bien, mais
l'intérieur restait troué : étirer n'est pas répartir, et deux cercles éloignés
de plus que la portée cessaient de se voir. Lloyd n'a pas ce défaut — il
raisonne sur la surface, pas sur les distances deux à deux.

Module de **géométrie pure** : il ne connaît ni `Track`, ni la GUI, ni le rendu.
Entrées = des diamètres et des ensembles de clés, sortie = des coordonnées.

**Déterminisme** (byte-identité de la sortie) : nombre d'itérations FIXE — pas
de critère d'arrêt sur un seuil, qui dépendrait de l'ordre des flottants —,
clés parcourues en ordre trié partout, et aucune source d'aléa.
"""

import math
import zlib

import numpy as np

from src.dataviz.geometry import EllipseSpec, enclosing_shape

# Itérations de la relaxation. Mesuré sur M.A.N (18 cercles) : au-delà de 40 la
# répartition ne bouge plus (plus grand vide 134 px à 40, 132 à 140) — 60 laisse
# de la marge aux planches plus denses. Le nombre est FIXE et non un critère de
# convergence : c'est ce qui garantit une sortie byte-identique.
ITERATIONS = 60

# Les ellipses ne sont recalculées qu'une itération sur cinq : `enclosing_shape`
# (Khachiyan) est le seul point coûteux de la boucle, et la force d'exclusion
# n'a pas besoin d'une forme fraîche au pixel près pour pousser dans le bon sens.
ELLIPSE_REFRESH = 5

# Passes de la projection de séparation à chaque itération : écarter une paire
# peut en rapprocher une autre. 10 suffisent en cours de route (les itérations
# suivantes rattrapent), la passe FINALE en fait davantage — c'est elle qui doit
# tenir. Nombres FIXES, pour ne pas dépendre d'un seuil.
SEPARATION_PASSES = 10
FINAL_SEPARATION_PASSES = 60

# Points échantillonnés sur le contour d'un cercle pour caler l'ellipse d'un
# groupe : elle doit envelopper les DISQUES de ses membres, pas leurs centres.
_CIRCLE_SAMPLES = 8


def group_ellipse(members, positions, radii, style) -> EllipseSpec | None:
    """Ellipse englobant les disques des `members`, ou `None` si le groupe est vide."""
    points: list[tuple[float, float]] = []
    for key in members:
        x, y = positions[key]
        radius = radii[key]
        points.extend(
            (
                x + radius * math.cos(2.0 * math.pi * i / _CIRCLE_SAMPLES),
                y + radius * math.sin(2.0 * math.pi * i / _CIRCLE_SAMPLES),
            )
            for i in range(_CIRCLE_SAMPLES)
        )
    if not points:
        return None
    return enclosing_shape(
        points,
        padding=style.ellipse_margin,
        min_radius=style.ellipse_margin,
        min_axis_ratio=style.min_axis_ratio,
    )


def _local(ellipse: EllipseSpec, x: float, y: float) -> tuple[float, float]:
    """Coordonnées de `(x, y)` dans le repère propre de l'ellipse (rotation défaite)."""
    angle = math.radians(ellipse.angle)
    dx, dy = x - ellipse.cx, y - ellipse.cy
    return (
        dx * math.cos(angle) + dy * math.sin(angle),
        -dx * math.sin(angle) + dy * math.cos(angle),
    )


def _from_local(ellipse: EllipseSpec, lx: float, ly: float) -> tuple[float, float]:
    """Inverse de `_local` : du repère de l'ellipse vers le repère du dessin."""
    angle = math.radians(ellipse.angle)
    return (
        ellipse.cx + lx * math.cos(angle) - ly * math.sin(angle),
        ellipse.cy + lx * math.sin(angle) + ly * math.cos(angle),
    )


def encloses(ellipse: EllipseSpec, x: float, y: float, radius: float = 0.0) -> bool:
    """L'ellipse contient-elle le disque `(x, y, radius)` (ne serait-ce en partie) ?

    Sert à la fois à la force d'exclusion et aux tests d'invariant : c'est LA
    définition de « ce cercle a l'air d'appartenir à ce groupe ».
    """
    lx, ly = _local(ellipse, x, y)
    rx = max(1e-9, ellipse.rx + radius)
    ry = max(1e-9, ellipse.ry + radius)
    return (lx / rx) ** 2 + (ly / ry) ** 2 < 1.0


_CONTOUR_SAMPLES = 16


def _ellipses_croisent(a: EllipseSpec, b: EllipseSpec) -> bool:
    """Les deux ovales se recouvrent-ils ? (échantillonnage de leurs contours)

    Test approché mais symétrique : un point du contour de l'un dans l'autre,
    ou l'inverse. Il attrape aussi le cas d'un ovale entièrement dans l'autre,
    par le test des centres.
    """
    if encloses(b, a.cx, a.cy) or encloses(a, b.cx, b.cy):
        return True
    for shape, other in ((a, b), (b, a)):
        for i in range(_CONTOUR_SAMPLES):
            px, py = shape.point_at(i * 360.0 / _CONTOUR_SAMPLES)
            if encloses(other, px, py):
                return True
    return False


def _push_out(ellipse: EllipseSpec, x: float, y: float, radius: float) -> tuple[float, float]:
    """Déplacement minimal qui sort le disque `(x, y, radius)` de l'ellipse.

    Le point est repoussé RADIALEMENT dans le repère propre de l'ellipse : c'est
    la direction la plus courte au premier ordre, et elle reste stable d'une
    itération à l'autre (une normale exacte ferait vibrer les cercles proches du
    centre).
    """
    lx, ly = _local(ellipse, x, y)
    rx = max(1e-9, ellipse.rx + radius)
    ry = max(1e-9, ellipse.ry + radius)
    k = math.hypot(lx / rx, ly / ry)
    if k >= 1.0:
        return 0.0, 0.0
    if k < 1e-9:
        # Cercle pile au centre : aucune direction ne se dégage. On sort par la
        # droite — choix arbitraire mais FIXE, donc déterministe.
        lx, ly, k = rx * 1e-3, 0.0, 1e-3
    scale = 1.0 / k
    tx, ty = _from_local(ellipse, lx * scale, ly * scale)
    return tx - x, ty - y


def _grid(style):
    """Points d'échantillonnage de la zone, calculés une fois par appel.

    Résolution volontairement grossière : on cherche des barycentres, pas des
    frontières. `numpy` fait le travail d'un coup, la boucle Python resterait
    des dizaines de fois plus lente pour un résultat visuellement identique.
    """
    cols = max(8, int(style.frame_width / style.lloyd_cell))
    rows = max(8, int(style.frame_height / style.lloyd_cell))
    xs = (np.arange(cols) + 0.5) * (style.frame_width / cols)
    ys = (np.arange(rows) + 0.5) * (style.frame_height / rows)
    gx, gy = np.meshgrid(xs, ys)
    return gx.ravel(), gy.ravel()


def _jitter(key: str, style) -> tuple[float, float]:
    """Décalage fixe propre à une clé, pour casser la régularité du pavage."""
    h = zlib.crc32(key.encode("utf-8"))
    angle = (h & 0xFFFF) / 65535.0 * 2.0 * math.pi
    rayon = ((h >> 16) & 0xFFFF) / 65535.0 * style.spread_jitter
    return rayon * math.cos(angle), rayon * math.sin(angle)


def _lloyd_targets(pos, keys, radii, style):
    """Barycentre de la part de zone revenant à chaque cercle.

    Chaque point de la zone est attribué au cercle dont le BORD est le plus
    proche — pas le centre : un gros cercle réclame ainsi plus de place, ce qui
    est exactement ce qu'on veut d'un artiste plus présent.
    """
    gx, gy = _grid(style)
    cx = np.array([pos[k][0] for k in keys])
    cy = np.array([pos[k][1] for k in keys])
    r = np.array([radii[k] for k in keys])
    # (points × cercles) : distance au bord de chaque cercle.
    d = np.hypot(gx[:, None] - cx[None, :], gy[:, None] - cy[None, :]) - r[None, :]
    owner = np.argmin(d, axis=1)
    counts = np.bincount(owner, minlength=len(keys))
    sum_x = np.bincount(owner, weights=gx, minlength=len(keys))
    sum_y = np.bincount(owner, weights=gy, minlength=len(keys))
    targets = {}
    for i, k in enumerate(keys):
        if counts[i]:  # un cercle sans part reste où il est
            targets[k] = (sum_x[i] / counts[i], sum_y[i] / counts[i])
    return targets


def largest_void(pos, sizes, style) -> float:
    """Rayon du plus grand disque vide qu'on puisse loger dans la zone.

    LA mesure de « trou » — celle qui manquait. L'occupation par boîte
    englobante valait 100 % dès que quelques cercles touchaient les bords,
    pendant que l'intérieur restait béant.
    """
    gx, gy = _grid(style)
    keys = sorted(pos)
    cx = np.array([pos[k][0] for k in keys])
    cy = np.array([pos[k][1] for k in keys])
    r = np.array([sizes[k] / 2.0 for k in keys])
    d = np.hypot(gx[:, None] - cx[None, :], gy[:, None] - cy[None, :]) - r[None, :]
    return float(np.max(np.min(d, axis=1)))


def solve(sizes, groups, style, seed_positions) -> dict[str, tuple[float, float]]:
    """Positions des cercles, par relaxation sous contraintes.

    `sizes` = diamètre par clé, `groups` = un tuple de clés par combinaison
    d'artistes, `seed_positions` = amorce (le `spring_layout` du graphe de
    collaboration, qui porte la structure). Renvoie le centre de chaque cercle
    dans la zone `style.frame_width × frame_height`.
    """
    keys = sorted(sizes)
    radii = {k: sizes[k] / 2.0 for k in keys}
    width, height = style.frame_width, style.frame_height

    # Amorce recentrée sur la zone : le `spring_layout` sort dans [-1, 1].
    pos = {
        k: (width / 2.0 + seed_positions[k][0], height / 2.0 + seed_positions[k][1]) for k in keys
    }

    real_groups = [tuple(sorted(g)) for g in groups if len(g) > 1]
    ellipses: dict[tuple[str, ...], EllipseSpec] = {}

    for step in range(ITERATIONS):
        if step % ELLIPSE_REFRESH == 0:
            ellipses = {}
            for members in real_groups:
                shape = group_ellipse(members, pos, radii, style)
                if shape is not None:
                    ellipses[members] = shape

        disp = {k: [0.0, 0.0] for k in keys}

        # ── Cohésion : resserrer chaque groupe sur son barycentre ──
        for members in real_groups:
            cx = sum(pos[k][0] for k in members) / len(members)
            cy = sum(pos[k][1] for k in members) / len(members)
            for k in members:
                disp[k][0] += (cx - pos[k][0]) * style.force_cohesion
                disp[k][1] += (cy - pos[k][1]) * style.force_cohesion

        # ── Exclusion : un étranger ne reste pas dans l'ellipse d'un groupe ──
        for members, shape in sorted(ellipses.items()):
            inside = set(members)
            for k in keys:
                if k in inside:
                    continue
                px, py = pos[k]
                if not encloses(shape, px, py, radii[k]):
                    continue
                ox, oy = _push_out(shape, px, py, radii[k])
                disp[k][0] += ox * style.force_exclusion
                disp[k][1] += oy * style.force_exclusion

        # ── Disjonction : deux ovales étrangers ne se croisent pas ──
        for i, (membres_a, forme_a) in enumerate(sorted(ellipses.items())):
            for membres_b, forme_b in sorted(ellipses.items())[i + 1 :]:
                if set(membres_a) & set(membres_b):
                    continue  # un artiste commun : le recouvrement est inévitable
                if not _ellipses_croisent(forme_a, forme_b):
                    continue
                dx = forme_b.cx - forme_a.cx
                dy = forme_b.cy - forme_a.cy
                dist = math.hypot(dx, dy)
                ux, uy = (1.0, 0.0) if dist < 1e-9 else (dx / dist, dy / dist)
                # Les deux groupes s'écartent en bloc, chacun de son côté.
                for membres, sens in ((membres_a, -1.0), (membres_b, 1.0)):
                    for k in membres:
                        disp[k][0] += ux * sens * style.force_disjunction
                        disp[k][1] += uy * sens * style.force_disjunction

        # ── Lloyd : chacun vers le milieu de la part de zone qu'il occupe ──
        # C'est CE terme qui répartit. Un vide est une part que personne ne
        # réclame vraiment : le cercle qui en hérite s'y déplace.
        for k, (tx, ty) in _lloyd_targets(pos, keys, radii, style).items():
            # Écart propre à chaque cercle, DÉTERMINISTE (crc32 du nom, jamais
            # `random` ni `hash()`) : une relaxation de Lloyd pure converge vers
            # un pavage régulier, et les membres d'un groupe s'y alignent en
            # rangées — un effet de grille qui fait mécanique. Ce décalage les
            # laisse s'étaler dans leur ovale.
            jx, jy = _jitter(k, style)
            disp[k][0] += (tx + jx - pos[k][0]) * style.force_spread
            disp[k][1] += (ty + jy - pos[k][1]) * style.force_spread

        for k in keys:
            pos[k] = (pos[k][0] + disp[k][0], pos[k][1] + disp[k][1])

        # ── Séparation : une CONTRAINTE, pas une force ──
        # Projetée après les forces, et répétée jusqu'à ce qu'elle tienne.
        # Traitée comme une force parmi les autres, elle se faisait défaire par
        # l'exclusion — mesuré : le nuage convergeait en 20 itérations, puis se
        # dégradait jusqu'à 88 px de chevauchement et s'y stabilisait.
        pos = _separate(pos, keys, radii, style)

        # ── Cadrage : rien ne sort de la zone ──
        for k in keys:
            r = radii[k]
            x = min(max(pos[k][0], r), max(r, width - r))
            y = min(max(pos[k][1], r), max(r, height - r))
            pos[k] = (x, y)

    # Dernier mot à la contrainte dure : le cadrage a pu re-serrer des cercles.
    return _separate(pos, keys, radii, style, passes=FINAL_SEPARATION_PASSES)


def _separate(pos, keys, radii, style, passes=SEPARATION_PASSES) -> dict[str, tuple[float, float]]:
    """Écarte les disques jusqu'à ce qu'aucun ne se chevauche (projection).

    Passes successives : écarter une paire peut en rapprocher une autre. Le
    nombre de passes est FIXE (déterminisme) et généreux — c'est la seule
    contrainte qu'on ne négocie pas.
    """
    gap = style.gap
    current = {k: [pos[k][0], pos[k][1]] for k in keys}
    for _ in range(passes):
        moved = False
        for i, ki in enumerate(keys):
            for kj in keys[i + 1 :]:
                dx = current[kj][0] - current[ki][0]
                dy = current[kj][1] - current[ki][1]
                dist = math.hypot(dx, dy)
                needed = radii[ki] + radii[kj] + gap
                if dist >= needed:
                    continue
                moved = True
                if dist < 1e-9:
                    # Centres confondus : aucune direction ne se dégage. On
                    # sépare horizontalement — arbitraire mais FIXE.
                    ux, uy, dist = 1.0, 0.0, 0.0
                else:
                    ux, uy = dx / dist, dy / dist
                shift = (needed - dist) / 2.0
                current[ki][0] -= ux * shift
                current[ki][1] -= uy * shift
                current[kj][0] += ux * shift
                current[kj][1] += uy * shift
        if not moved:
            break
    return {k: (current[k][0], current[k][1]) for k in keys}

    return pos
