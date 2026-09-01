"""Le crible d'audit d'une planche Bubble (`src.dataviz.bubble_audit`).

Les règles sont celles de `scripts/bubble_prod.py --audit`, factorisées pour le
harnais `bubble_compare` : on vérifie ici qu'elles détectent bien chaque défaut
(sur des specs synthétiques) et qu'une planche réelle du moteur passe sans
entorse (c'est la garantie).
"""

from src.dataviz.bubble_audit import check_spec
from src.dataviz.bubble_prod import build_bubble_spec
from src.dataviz.bubble_svg import BubbleSpec, GroupShape, LabelRing, NodeSpec, SvgStyle
from src.dataviz.collab_graph import (
    aggregate_collab_groups,
    build_collab_graph,
    extract_track_groups,
)
from src.dataviz.geometry import EllipseSpec
from src.models.track import Credit, CreditRole, Track


def _node(key, x, y, size=100.0):
    return NodeSpec(key=key, display=key, x=x, y=y, size=size, track_count=1, label_font_size=20.0)


def _spec(nodes=(), groups=()):
    style = SvgStyle()
    return BubbleSpec(
        width=style.frame_width,
        height=style.frame_height,
        nodes=tuple(nodes),
        edges=(),
        groups=tuple(groups),
        style=style,
    )


def _groupe(members, ellipse, rings=()):
    return GroupShape(
        member_keys=tuple(members),
        ellipse=ellipse,
        label_lines=(),
        rings=tuple(rings),
        track_count=1,
    )


def test_chevauchement_detecte():
    audit = check_spec(_spec(nodes=[_node("a", 200, 200), _node("b", 250, 200)]))
    assert any("chevauche" in e for e in audit.entorses)


def test_capture_detectee():
    # « b » n'est pas membre du groupe mais son centre est dans l'ovale.
    ovale = EllipseSpec(cx=300, cy=300, rx=120, ry=80, angle=0.0)
    audit = check_spec(
        _spec(
            nodes=[_node("a", 300, 300), _node("b", 350, 300)],
            groups=[_groupe(("a",), ovale)],
        )
    )
    assert any("chevauche" in e for e in audit.entorses)  # a et b se touchent aussi
    assert any("capturé" in e for e in audit.entorses)


def test_ovales_etrangers_croises_en_compromis():
    a = EllipseSpec(cx=300, cy=300, rx=120, ry=80, angle=0.0)
    b = EllipseSpec(cx=380, cy=300, rx=120, ry=80, angle=0.0)
    audit = check_spec(
        _spec(
            nodes=[_node("a", 250, 300), _node("b", 450, 300)],
            groups=[_groupe(("a", "x"), a), _groupe(("b", "y"), b)],
        )
    )
    assert any("croisés" in c for c in audit.compromis)
    # Un croisement est un COMPROMIS (optimisé sans promesse), pas une entorse.
    assert not any("croisés" in e for e in audit.entorses)


def test_titre_hors_cadre_en_compromis():
    # Ovale collé au bord gauche : le titre posé à son sommet gauche sort du cadre.
    ovale = EllipseSpec(cx=10, cy=300, rx=150, ry=80, angle=0.0)
    ring = LabelRing(text="Titre", offset=25.0, t=180.0, sweep=1)
    audit = check_spec(
        _spec(nodes=[_node("a", 120, 300)], groups=[_groupe(("a", "x"), ovale, rings=[ring])])
    )
    assert any("hors cadre" in c for c in audit.compromis)


def test_planche_propre_sans_entorse():
    audit = check_spec(_spec(nodes=[_node("a", 200, 200), _node("b", 600, 400)]))
    assert audit.entorses == ()
    assert audit.compromis == ()
    assert audit.void > 0
    assert audit.void_ratio > 0


def test_planche_reelle_du_moteur_sans_entorse():
    # La garantie du moteur, vue au travers du crible : le réseau dense des
    # tests d'invariants doit passer sans aucune entorse.
    def _prod(name):
        return Credit(name=name, role=CreditRole.PRODUCER)

    def _track(tid, title, *credits):
        t = Track(id=tid, title=title, album="Al")
        t.credits = list(credits)
        return t

    tracks = [
        _track(1, "T1", _prod("Hub"), _prod("A")),
        _track(2, "T2", _prod("Hub"), _prod("B")),
        _track(3, "T3", _prod("D"), _prod("E")),
        _track(4, "T4", _prod("F")),
    ]
    groups = extract_track_groups(tracks)
    spec = build_bubble_spec(build_collab_graph(groups), aggregate_collab_groups(groups))
    assert check_spec(spec).entorses == ()
