"""Les invariants d'une planche Bubble, énoncés une fois pour toutes.

Le moteur a longtemps été une pile de passes correctives ajoutées au fil des
retours sur planche. Chacune corrigeait un symptôme, aucune n'écrivait la règle :
deux défauts sont passés au travers (un cercle étranger capturé par une ellipse,
un dessin tassé au centre) parce qu'ils ne violaient rien d'écrit.

Ce fichier est la réponse : les règles y sont dites, et vérifiées. Un futur
réglage qui les casse rougit ici, et non trois semaines plus tard sur une planche.
"""

import math

from src.dataviz.bubble_layout import encloses
from src.dataviz.bubble_prod import build_bubble_spec
from src.dataviz.bubble_svg import SvgStyle
from src.dataviz.collab_graph import (
    aggregate_collab_groups,
    build_collab_graph,
    extract_track_groups,
)
from src.models.track import Credit, CreditRole, Track


def _prod(name):
    return Credit(name=name, role=CreditRole.PRODUCER)


def _track(tid, title, album, *credits):
    t = Track(id=tid, title=title, album=album)
    t.credits = list(credits)
    return t


def _spec(tracks, style=None, seed=42):
    groups = extract_track_groups(tracks)
    return build_bubble_spec(
        build_collab_graph(groups), aggregate_collab_groups(groups), style, seed=seed
    )


def _reseau_dense():
    """Un noyau, deux îlots, des combinaisons qui se recoupent."""
    return [
        _track(1, "T1", "Al", _prod("Hub"), _prod("A")),
        _track(2, "T2", "Al", _prod("Hub"), _prod("B")),
        _track(3, "T3", "Al", _prod("Hub"), _prod("C")),
        _track(4, "T4", "Al", _prod("Hub"), _prod("A"), _prod("B")),
        _track(5, "T5", "Al", _prod("Hub")),
        _track(6, "T6", "Al", _prod("D"), _prod("E")),
        _track(7, "T7", "Al", _prod("F")),
        _track(8, "T8", "Al", _prod("G"), _prod("H")),
    ]


# ── I1 : deux cercles ne se chevauchent jamais ───────────────────────────────


def test_i1_aucun_chevauchement():
    spec = _spec(_reseau_dense())
    nodes = list(spec.nodes)
    for i, a in enumerate(nodes):
        for b in nodes[i + 1 :]:
            distance = math.hypot(a.x - b.x, a.y - b.y)
            assert distance >= (a.size + b.size) / 2.0 - 1e-6, f"{a.key} touche {b.key}"


def test_i1_tient_meme_en_zone_etroite():
    # Quand la zone force le rapprochement, la séparation reste la contrainte
    # qui ne se négocie pas : le dessin déborde plutôt que de se superposer.
    spec = _spec(_reseau_dense(), style=SvgStyle(frame_width=420.0, frame_height=300.0))
    nodes = list(spec.nodes)
    for i, a in enumerate(nodes):
        for b in nodes[i + 1 :]:
            assert math.hypot(a.x - b.x, a.y - b.y) >= (a.size + b.size) / 2.0 - 1e-6


# ── I2 : les cercles tiennent dans la zone ───────────────────────────────────


def test_i2_cercles_dans_la_zone():
    spec = _spec(_reseau_dense())
    for n in spec.nodes:
        assert n.x - n.size / 2.0 >= -1.0
        assert n.y - n.size / 2.0 >= -1.0
        assert n.x + n.size / 2.0 <= spec.width + 1.0
        assert n.y + n.size / 2.0 <= spec.height + 1.0


# ── I3 : une ellipse ne contient que ses membres ─────────────────────────────


def test_i3_aucune_ellipse_ne_capture_un_etranger():
    # LE test qui manquait. Sans lui, un artiste qui passait par là se lisait
    # comme membre d'un groupe auquel il n'appartient pas (cas réel : Sofiane
    # Pamart avalé par l'ovale de Lucci' et Crayon).
    spec = _spec(_reseau_dense())
    for groupe in spec.groups:
        membres = set(groupe.member_keys)
        for n in spec.nodes:
            if n.key in membres:
                continue
            assert not encloses(
                groupe.ellipse, n.x, n.y
            ), f"{n.key} est dans l'ellipse {groupe.member_keys}"


# ── I4 : un titre est rattachable à son ovale ────────────────────────────────


def test_i4_titre_pres_de_ses_membres():
    # L'autre règle jamais écrite : un titre posé sur un arc éloigné de tous ses
    # membres devient impossible à attribuer à l'œil.
    spec = _spec(_reseau_dense())
    for groupe in spec.groups:
        membres = [n for n in spec.nodes if n.key in set(groupe.member_keys)]
        for ring in groupe.rings:
            px, py = groupe.ellipse.inflated(ring.offset).point_at(ring.t)
            distance = min(math.hypot(px - m.x, py - m.y) - m.size / 2.0 for m in membres)
            assert distance <= 140.0, f"{ring.text!r} est à {distance:.0f} px de ses membres"


def test_i4_titre_lisible():
    # Le texte suit la courbe : au-delà d'une certaine inclinaison il s'écrit
    # de haut en bas. C'est la seule contrainte DURE du placement des titres.
    spec = _spec(_reseau_dense())
    for groupe in spec.groups:
        for ring in groupe.rings:
            tx, ty = groupe.ellipse.inflated(ring.offset).tangent_at(ring.t)
            angle = ((math.degrees(math.atan2(ty, tx)) + 90.0) % 180.0) - 90.0
            assert abs(angle) <= spec.style.ellipse_label_max_angle + 1e-6
            assert (tx >= 0) == bool(ring.sweep)  # se lit de gauche à droite


# ── I5 : le dessin occupe la zone ────────────────────────────────────────────


def test_i5_occupation_de_la_zone():
    spec = _spec(_reseau_dense())
    xs = [n.x - n.size / 2.0 for n in spec.nodes] + [n.x + n.size / 2.0 for n in spec.nodes]
    ys = [n.y - n.size / 2.0 for n in spec.nodes] + [n.y + n.size / 2.0 for n in spec.nodes]
    assert (max(xs) - min(xs)) / spec.width >= 0.75
    assert (max(ys) - min(ys)) / spec.height >= 0.75


# ── I6 : la sortie ne bouge pas d'une exécution à l'autre ────────────────────


def test_i6_deterministe():
    a = _spec(_reseau_dense())
    b = _spec(_reseau_dense())
    assert [(n.key, n.x, n.y) for n in a.nodes] == [(n.key, n.x, n.y) for n in b.nodes]
    assert [(g.member_keys, g.ellipse, tuple(g.rings)) for g in a.groups] == [
        (g.member_keys, g.ellipse, tuple(g.rings)) for g in b.groups
    ]


def test_i6_le_seed_change_le_dessin():
    # Contrepartie du déterminisme : les seeds doivent bien produire des
    # variantes, sinon la grille d'aperçus ne sert à rien.
    a = _spec(_reseau_dense(), seed=42)
    b = _spec(_reseau_dense(), seed=7)
    assert [(n.key, n.x) for n in a.nodes] != [(n.key, n.x) for n in b.nodes]
