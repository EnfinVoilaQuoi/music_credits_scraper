"""Placement des cercles Bubble : une relaxation sous contraintes explicites.

Remplace six passes correctives empilées au fil des retours sur planche
(resserrage, calage des îlots aux coins, séparation îlots↔îlots, séparation
noyau↔îlots, étalement par dichotomie). Chacune corrigeait un symptôme ; aucune
n'énonçait la règle. Deux défauts passaient donc au travers : un cercle étranger
capturé par une ellipse, et un dessin tassé au centre.

Les règles sont désormais dites une fois, sous forme de **forces** :

- `separation` — deux cercles gardent `gap` entre eux, quelle que soit leur
  composante ;
- `cohesion` — les membres d'un même groupe se rapprochent : un groupe compact
  donne une petite ellipse, donc peu d'occasions d'attraper un étranger ;
- `exclusion` — un cercle NON membre est repoussé hors de l'ellipse d'un groupe ;
- `repulsion` — les cercles se répartissent au lieu de se contenter d'éviter
  le contact ;
- `expansion` — le nuage grandit jusqu'à occuper la zone ;
- `containment` — et n'en sort pas.

Module de **géométrie pure** : il ne connaît ni `Track`, ni la GUI, ni le rendu.
Entrées = des diamètres et des ensembles de clés, sortie = des coordonnées.

**Déterminisme** (byte-identité de la sortie) : nombre d'itérations FIXE — pas
de critère d'arrêt sur un seuil, qui dépendrait de l'ordre des flottants —,
clés parcourues en ordre trié partout, et aucune source d'aléa.
"""

import math

from src.dataviz.geometry import EllipseSpec, enclosing_shape

# Itérations de la relaxation. 140 suffisent à atteindre le point fixe sur les
# planches réelles (20 nœuds, 12 groupes) ; le nombre est FIXE et non un critère
# de convergence, c'est ce qui garantit une sortie byte-identique.
ITERATIONS = 140

# Les ellipses ne sont recalculées qu'une itération sur cinq : `enclosing_shape`
# (Khachiyan) est le seul point coûteux de la boucle, et la force d'exclusion
# n'a pas besoin d'une forme fraîche au pixel près pour pousser dans le bon sens.
ELLIPSE_REFRESH = 5

# Passes de la projection de séparation, à chaque itération. Écarter une paire
# peut en rapprocher une autre : 40 passes suffisent largement sur une planche
# réelle, et le nombre reste FIXE pour ne pas dépendre d'un seuil.
SEPARATION_PASSES = 40

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

        # ── Expansion : le nuage grandit jusqu'à occuper la zone ──
        # Un facteur PAR AXE : la zone est paysage, un facteur commun serait
        # bloqué par la hauteur et laisserait deux vides sur les côtés. Seules
        # les DISTANCES changent — les diamètres encodent la participation et
        # doivent rester comparables d'un album à l'autre.
        pos = _expand(pos, keys, radii, style)

        # ── Répulsion : répartir, pas seulement éviter le contact ──
        # La séparation ne fait que garantir un écart minimal ; sans répulsion,
        # le nuage garde la forme de son amorce et se retrouve de guingois — le
        # noyau d'un côté, tous les îlots empilés de l'autre. La portée est
        # bornée pour que deux cercles éloignés cessent de s'influencer.
        reach = style.repulsion_range
        for i, ki in enumerate(keys):
            for kj in keys[i + 1 :]:
                dx = pos[kj][0] - pos[ki][0]
                dy = pos[kj][1] - pos[ki][1]
                dist = math.hypot(dx, dy)
                if dist >= reach:
                    continue
                ux, uy = (1.0, 0.0) if dist < 1e-9 else (dx / dist, dy / dist)
                force = style.force_repulsion * (reach - dist) / reach
                disp[ki][0] -= ux * force
                disp[ki][1] -= uy * force
                disp[kj][0] += ux * force
                disp[kj][1] += uy * force

        for k in keys:
            pos[k] = (pos[k][0] + disp[k][0], pos[k][1] + disp[k][1])

        # ── Séparation : une CONTRAINTE, pas une force ──
        # Elle est projetée après coup, et répétée jusqu'à ce qu'elle tienne.
        # Traitée comme une force parmi les autres, elle se faisait défaire par
        # l'exclusion — mesuré : le nuage convergeait en 20 itérations, puis se
        # dégradait jusqu'à 88 px de chevauchement et s'y stabilisait. Les deux
        # se renvoyaient la balle, chacune à moitié satisfaite.
        pos = _separate(pos, keys, radii, style)

        # ── Cadrage : rien ne sort de la zone ──
        for k in keys:
            r = radii[k]
            x = min(max(pos[k][0], r), max(r, width - r))
            y = min(max(pos[k][1], r), max(r, height - r))
            pos[k] = (x, y)

    # Dernier mot à la contrainte dure : le cadrage a pu re-serrer des cercles.
    return _separate(pos, keys, radii, style)


def _separate(pos, keys, radii, style) -> dict[str, tuple[float, float]]:
    """Écarte les disques jusqu'à ce qu'aucun ne se chevauche (projection).

    Passes successives : écarter une paire peut en rapprocher une autre. Le
    nombre de passes est FIXE (déterminisme) et généreux — c'est la seule
    contrainte qu'on ne négocie pas.
    """
    gap = style.gap
    current = {k: [pos[k][0], pos[k][1]] for k in keys}
    for _ in range(SEPARATION_PASSES):
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


def _expand(pos, keys, radii, style) -> dict[str, tuple[float, float]]:
    """Rapproche le nuage de la taille de la zone, un axe à la fois."""
    xs = [pos[k][0] - radii[k] for k in keys] + [pos[k][0] + radii[k] for k in keys]
    ys = [pos[k][1] - radii[k] for k in keys] + [pos[k][1] + radii[k] for k in keys]
    span_x, span_y = max(xs) - min(xs), max(ys) - min(ys)
    target_x = style.frame_width - 2 * style.margin
    target_y = style.frame_height - 2 * style.margin
    cx = (max(xs) + min(xs)) / 2.0
    cy = (max(ys) + min(ys)) / 2.0

    def factor(span, target):
        if span < 1e-9:
            return 1.0
        # Amorti : on ne fait qu'une fraction du chemin à chaque itération,
        # sinon l'expansion et la séparation se renvoient la balle et le nuage
        # oscille au lieu de se poser.
        return 1.0 + (target / span - 1.0) * style.force_expansion

    fx, fy = factor(span_x, target_x), factor(span_y, target_y)
    return {k: (cx + (pos[k][0] - cx) * fx, cy + (pos[k][1] - cy) * fy) for k in keys}
