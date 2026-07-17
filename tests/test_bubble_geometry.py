"""Tests de `src.dataviz.geometry` : ellipses englobantes + dispatch.

Couvre les pièges du plan Bubble Prod : solo → cercle, duo → ellipse non plate,
3+ colinéaires → pas de singularité Khachiyan, déterminisme (SVG reproductible).
"""

import math

import numpy as np
import pytest

from src.dataviz.geometry import EllipseSpec, enclosing_shape, min_enclosing_ellipse


def _inside(c, A, pts, eps=1e-6):
    """Tous les points sont dans/sur l'ellipse conique (c, A)."""
    return all(float((p - c) @ A @ (p - c)) <= 1 + eps for p in np.asarray(pts, dtype=float))


# ── Solo ─────────────────────────────────────────────────────────────────────


def test_solo_est_un_cercle():
    spec = enclosing_shape([[3, 4]], padding=0, min_radius=5, min_axis_ratio=0.3)
    assert spec == EllipseSpec(cx=3.0, cy=4.0, rx=5.0, ry=5.0, angle=0.0)


def test_solo_padding_domine_min_radius():
    spec = enclosing_shape([[0, 0]], padding=8, min_radius=5)
    assert spec.rx == spec.ry == 8.0


# ── Duo ──────────────────────────────────────────────────────────────────────


def test_duo_horizontal():
    spec = enclosing_shape([[0, 0], [10, 0]], padding=0, min_radius=1, min_axis_ratio=0.2)
    assert spec.cx == 5.0
    assert spec.cy == 0.0
    assert spec.rx == 5.0
    assert spec.ry == 1.0  # max(0.2*5, 1, 0)
    assert spec.angle == pytest.approx(0.0)


def test_duo_non_plat():
    # Le petit axe ne doit jamais être nul (piège Khachiyan sur 2 points).
    spec = enclosing_shape([[0, 0], [100, 0]], padding=0, min_radius=2, min_axis_ratio=0.1)
    assert spec.ry > 0


def test_duo_vertical_angle_90():
    spec = enclosing_shape([[0, 0], [0, 10]], padding=0, min_radius=1, min_axis_ratio=0.2)
    assert spec.cx == 0.0
    assert spec.cy == 5.0
    assert spec.angle == pytest.approx(90.0)


# ── Triangle / 3+ ────────────────────────────────────────────────────────────


def test_triangle_englobe_tous_les_points():
    pts = [[0, 0], [4, 0], [1, 3]]
    c, A = min_enclosing_ellipse(pts)
    assert _inside(c, A, pts)


def test_carre_unite_est_quasi_circulaire():
    # L'ellipse minimale d'un carré est un cercle centré, rayon = demi-diagonale.
    pts = [[0, 0], [1, 0], [1, 1], [0, 1]]
    c, A = min_enclosing_ellipse(pts)
    assert c == pytest.approx([0.5, 0.5], abs=1e-3)
    assert _inside(c, A, pts)
    # rx ≈ ry (cercle) après décomposition
    spec = enclosing_shape(pts, padding=0, min_radius=0, min_axis_ratio=0)
    assert spec.rx == pytest.approx(math.sqrt(2) / 2, abs=1e-2)
    assert spec.ry == pytest.approx(spec.rx, abs=1e-2)


# ── Colinéaires (piège de singularité) ───────────────────────────────────────


def test_min_enclosing_ellipse_singuliere_sur_colineaires():
    # Justifie le test de rang en amont : Khachiyan seul explose sur colinéaires.
    with pytest.raises(np.linalg.LinAlgError):
        min_enclosing_ellipse([[0, 0], [1, 0], [2, 0]])


def test_trois_colineaires_sans_exception():
    spec = enclosing_shape([[0, 0], [1, 0], [2, 0]], padding=0, min_radius=1, min_axis_ratio=0.2)
    assert isinstance(spec, EllipseSpec)
    assert spec.cx == pytest.approx(1.0)
    assert spec.cy == pytest.approx(0.0)
    assert spec.angle == pytest.approx(0.0)
    assert spec.ry > 0


def test_points_confondus_sans_exception():
    spec = enclosing_shape([[5, 5], [5, 5], [5, 5]], padding=3, min_radius=1)
    assert spec.cx == pytest.approx(5.0)
    assert spec.cy == pytest.approx(5.0)


# ── Déterminisme & utilitaires ───────────────────────────────────────────────


def test_deterministe():
    pts = [[0, 0], [4, 0], [1, 3], [3, 2]]
    a = enclosing_shape(pts, padding=1.5, min_radius=2, min_axis_ratio=0.3)
    b = enclosing_shape(pts, padding=1.5, min_radius=2, min_axis_ratio=0.3)
    assert a == b


def test_bbox_cercle():
    spec = EllipseSpec(cx=0.0, cy=0.0, rx=5.0, ry=5.0, angle=0.0)
    assert spec.bbox() == pytest.approx((-5.0, -5.0, 5.0, 5.0))


def test_bbox_ellipse_axis_aligned():
    spec = EllipseSpec(cx=1.0, cy=2.0, rx=5.0, ry=1.0, angle=0.0)
    assert spec.bbox() == pytest.approx((-4.0, 1.0, 6.0, 3.0))


def test_zero_point_leve():
    with pytest.raises(ValueError):
        enclosing_shape([])
