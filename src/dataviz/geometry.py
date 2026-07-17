"""Géométrie des ellipses englobantes (numpy pur, sans scipy).

Pour chaque morceau, on veut une forme qui entoure les positions de ses
producteurs :
- 1 producteur → **cercle** (l'ellipse minimale n'a pas de sens).
- 2 producteurs → **ellipse-segment** : l'ellipse minimale mathématique dégénère
  en segment plat (Khachiyan singulier) ; on la construit à la main avec un
  petit axe plancher.
- 3+ producteurs → **ellipse minimale de Khachiyan** + padding + plancher du
  petit axe. Points colinéaires = piège (matrice singulière) : on teste le rang
  AVANT d'appeler Khachiyan et on retombe sur un segment le cas échéant.

`enclosing_shape(points, ...)` est le point d'entrée unique (dispatch). Le
résultat est un `EllipseSpec` (centre, demi-axes, angle) directement traduisible
en `<ellipse transform="rotate(angle cx cy)">`. Angle en degrés dans le repère
d'entrée : quand les points sont fournis en coordonnées canvas (y vers le bas),
l'angle est compatible avec `rotate()` de SVG (sens horaire à l'écran).

Fonctions **pures et déterministes** (mêmes points → même spec) : la
reproductibilité byte-à-byte du SVG en dépend.
"""

import math
from dataclasses import dataclass

import numpy as np

# Tolérance relative pour juger un nuage de points « plat » (colinéaire) : si la
# plus petite valeur singulière des points centrés tombe sous ce seuil ×
# l'étendue, Khachiyan serait singulier → on bascule sur un segment.
_RANK_TOL = 1e-9


@dataclass(frozen=True)
class EllipseSpec:
    """Ellipse paramétrée pour le rendu SVG.

    `rx`/`ry` = demi-axes le long des axes locaux (avant rotation), `angle` en
    degrés autour de `(cx, cy)`. Un cercle est un `EllipseSpec` avec `rx == ry`.
    """

    cx: float
    cy: float
    rx: float
    ry: float
    angle: float  # degrés

    def bbox(self) -> tuple[float, float, float, float]:
        """Boîte englobante axis-aligned de l'ellipse tournée (xmin, ymin, xmax, ymax)."""
        t = math.radians(self.angle)
        half_w = math.hypot(self.rx * math.cos(t), self.ry * math.sin(t))
        half_h = math.hypot(self.rx * math.sin(t), self.ry * math.cos(t))
        return (self.cx - half_w, self.cy - half_h, self.cx + half_w, self.cy + half_h)


def min_enclosing_ellipse(
    points, tol: float = 1e-3, max_iter: int = 1000
) -> tuple[np.ndarray, np.ndarray]:
    """Ellipse d'aire minimale englobant `points` (algo de Khachiyan).

    Renvoie `(c, A)` : centre `c` (2,) et matrice SPD `A` (2, 2) telles que
    l'ellipse est `{x : (x - c)ᵀ A (x - c) ≤ 1}`. Lève `numpy.linalg.LinAlgError`
    si les points sont dégénérés (colinéaires) — appeler `enclosing_shape`, qui
    teste le rang en amont, plutôt que cette fonction directement.
    """
    P = np.asarray(points, dtype=float).T  # (2, N)
    d, N = P.shape
    Q = np.vstack([P, np.ones(N)])  # (3, N)
    u = np.ones(N) / N
    for _ in range(max_iter):
        X = Q @ np.diag(u) @ Q.T
        M = np.einsum("ij,ji->i", Q.T @ np.linalg.inv(X), Q)
        j = int(np.argmax(M))
        step = (M[j] - d - 1) / ((d + 1) * (M[j] - 1))
        new_u = (1 - step) * u
        new_u[j] += step
        if np.linalg.norm(new_u - u) < tol:
            u = new_u
            break
        u = new_u
    c = P @ u
    A = np.linalg.inv((P @ np.diag(u) @ P.T) - np.outer(c, c)) / d
    return c, A


def _circle(cx: float, cy: float, radius: float) -> EllipseSpec:
    return EllipseSpec(cx=cx, cy=cy, rx=radius, ry=radius, angle=0.0)


def _segment_ellipse(
    p1, p2, *, padding: float, min_radius: float, min_axis_ratio: float
) -> EllipseSpec:
    """Ellipse construite le long du segment p1→p2 (cas duo / fallback colinéaire)."""
    p1 = np.asarray(p1, dtype=float)
    p2 = np.asarray(p2, dtype=float)
    center = (p1 + p2) / 2.0
    dx, dy = p2 - p1
    length = math.hypot(dx, dy)
    rx = length / 2.0 + padding
    # Le petit axe doit rester non nul (sinon segment plat) ET assez large pour
    # englober visuellement les carrés perpendiculairement (d'où le plancher par
    # `padding`, en plus du ratio et du rayon minimum).
    ry = max(min_axis_ratio * rx, min_radius, padding)
    rx = max(rx, ry)
    angle = math.degrees(math.atan2(dy, dx)) if length > 0 else 0.0
    return EllipseSpec(cx=float(center[0]), cy=float(center[1]), rx=rx, ry=ry, angle=angle)


def _ellipse_from_conic(
    c: np.ndarray, A: np.ndarray, *, padding: float, min_radius: float, min_axis_ratio: float
) -> EllipseSpec:
    """Décompose (c, A) en demi-axes + angle, puis applique padding et planchers."""
    # A symétrique définie positive → eigh (valeurs propres croissantes).
    # Demi-axe = 1/sqrt(valeur propre) : la plus petite valeur propre porte le
    # grand axe. Le vecteur propre associé donne l'orientation.
    eigvals, eigvecs = np.linalg.eigh(A)
    axes = 1.0 / np.sqrt(eigvals)  # axes[0] = grand axe, axes[1] = petit axe
    major_vec = eigvecs[:, 0]
    rx = float(axes[0]) + padding
    ry = float(axes[1]) + padding
    ry = max(ry, min_radius, min_axis_ratio * rx)
    rx = max(rx, ry)
    angle = math.degrees(math.atan2(major_vec[1], major_vec[0]))
    return EllipseSpec(cx=float(c[0]), cy=float(c[1]), rx=rx, ry=ry, angle=angle)


def _two_extreme_points(pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Les deux points extrêmes le long de la direction principale (nuage plat)."""
    centered = pts - pts.mean(axis=0)
    _, _, vt = np.linalg.svd(centered, full_matrices=False)
    proj = centered @ vt[0]
    return pts[int(np.argmin(proj))], pts[int(np.argmax(proj))]


def _is_colinear(pts: np.ndarray) -> bool:
    """Vrai si les points sont (quasi) colinéaires — Khachiyan y serait singulier."""
    centered = pts - pts.mean(axis=0)
    sv = np.linalg.svd(centered, compute_uv=False)
    if sv[0] == 0:  # tous les points confondus
        return True
    return sv[-1] < _RANK_TOL * sv[0]


def enclosing_shape(
    points,
    *,
    padding: float = 0.0,
    min_radius: float = 1.0,
    min_axis_ratio: float = 0.35,
) -> EllipseSpec:
    """Forme englobant `points` selon leur nombre (dispatch solo/duo/3+).

    - 0 point → `ValueError` (aucun producteur : à gérer en amont).
    - 1 point → cercle de rayon `max(min_radius, padding)`.
    - 2 points → ellipse-segment (jamais Khachiyan).
    - 3+ points colinéaires → segment sur les deux extrêmes (pas de singularité).
    - 3+ points → ellipse de Khachiyan + `padding`, petit axe planché.

    `padding` élargit la forme pour englober visuellement les carrés (pas
    seulement leurs centres). `min_axis_ratio` borne l'aplatissement.
    """
    pts = np.asarray(points, dtype=float)
    if pts.ndim != 2 or pts.shape[1] != 2:
        raise ValueError("`points` doit être un tableau (N, 2)")
    n = len(pts)
    if n == 0:
        raise ValueError("enclosing_shape : aucun point")
    if n == 1:
        return _circle(float(pts[0, 0]), float(pts[0, 1]), max(min_radius, padding))
    if n == 2:
        return _segment_ellipse(
            pts[0], pts[1], padding=padding, min_radius=min_radius, min_axis_ratio=min_axis_ratio
        )
    if _is_colinear(pts):
        p1, p2 = _two_extreme_points(pts)
        return _segment_ellipse(
            p1, p2, padding=padding, min_radius=min_radius, min_axis_ratio=min_axis_ratio
        )
    try:
        c, A = min_enclosing_ellipse(pts)
    except np.linalg.LinAlgError:
        # Filet de sécurité : rang mal détecté → segment sur les extrêmes.
        p1, p2 = _two_extreme_points(pts)
        return _segment_ellipse(
            p1, p2, padding=padding, min_radius=min_radius, min_axis_ratio=min_axis_ratio
        )
    return _ellipse_from_conic(
        c, A, padding=padding, min_radius=min_radius, min_axis_ratio=min_axis_ratio
    )
