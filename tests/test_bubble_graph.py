"""Tests de `src.dataviz.collab_graph` sur des Track/Credit synthétiques.

Vérifie : filtre strict vs large, fusion des graphies d'un même producteur,
poids d'arête = morceaux partagés, absence de self-loop, conservation des solos,
et déterminisme du layout (positions reproductibles à seed fixe).
"""

import numpy as np

from src.dataviz.collab_graph import (
    BROAD_PRODUCER_ROLES,
    STRICT_PRODUCER_ROLES,
    aggregate_collab_groups,
    build_collab_graph,
    compute_layout,
    extract_track_groups,
)
from src.models.track import Credit, CreditRole, Track


def _prod(name, role=CreditRole.PRODUCER):
    return Credit(name=name, role=role)


def _track(tid, title, *credits):
    t = Track(id=tid, title=title)
    t.credits = list(credits)
    return t


# ── Constantes ───────────────────────────────────────────────────────────────


def test_constantes_roles():
    assert STRICT_PRODUCER_ROLES == ("Producer",)
    assert set(BROAD_PRODUCER_ROLES) == {
        "Producer",
        "Co-Producer",
        "Executive Producer",
        "Vocal Producer",
        "Additional Production",
    }


# ── Filtre strict vs large ───────────────────────────────────────────────────


def test_filtre_strict_vs_large():
    track = _track(1, "Song", _prod("A"), _prod("B", CreditRole.CO_PRODUCER))
    strict = extract_track_groups([track], STRICT_PRODUCER_ROLES)
    broad = extract_track_groups([track], BROAD_PRODUCER_ROLES)
    assert strict[0].keys == ("a",)
    assert set(broad[0].keys) == {"a", "b"}


def test_role_hors_filtre_ignore():
    track = _track(1, "Song", _prod("Mixer", CreditRole.MIXING_ENGINEER))
    assert extract_track_groups([track]) == []


# ── Fusion des graphies ──────────────────────────────────────────────────────


def test_fusion_graphies_meme_producteur():
    t1 = _track(1, "Alpha", _prod("Kalim (Producer)"))
    t2 = _track(2, "Beta", _prod("Kalim"))
    groups = extract_track_groups([t1, t2])
    G = build_collab_graph(groups)
    assert list(G.nodes) == ["kalim"]
    assert G.nodes["kalim"]["track_count"] == 2


def test_display_premiere_graphie_vue():
    # Tri par titre : « Alpha » (Kalim) avant « Beta » (KALIM) → display « Kalim ».
    t1 = _track(1, "Alpha", _prod("Kalim"))
    t2 = _track(2, "Beta", _prod("KALIM"))
    G = build_collab_graph(extract_track_groups([t2, t1]))  # ordre d'entrée inversé
    assert G.nodes["kalim"]["display"] == "Kalim"


# ── Arêtes / poids ───────────────────────────────────────────────────────────


def test_poids_arete_morceaux_partages():
    duo1 = _track(1, "One", _prod("A"), _prod("B"))
    duo2 = _track(2, "Two", _prod("A"), _prod("B"))
    other = _track(3, "Three", _prod("A"), _prod("C"))
    G = build_collab_graph(extract_track_groups([duo1, duo2, other]))
    assert G["a"]["b"]["weight"] == 2
    assert G["a"]["c"]["weight"] == 1


def test_pas_de_self_loop_sur_doublon():
    # Même producteur crédité deux fois sur le morceau → 1 nœud, aucune arête.
    track = _track(1, "Song", _prod("A"), _prod("A (Producer)"))
    G = build_collab_graph(extract_track_groups([track]))
    assert list(G.nodes) == ["a"]
    assert nx_number_of_selfloops(G) == 0
    assert G.number_of_edges() == 0


def nx_number_of_selfloops(G):
    return sum(1 for u, v in G.edges() if u == v)


# ── Solos conservés ──────────────────────────────────────────────────────────


def test_solo_conserve_comme_noeud_isole():
    solo = _track(1, "Solo", _prod("A"))
    duo = _track(2, "Duo", _prod("B"), _prod("C"))
    G = build_collab_graph(extract_track_groups([solo, duo]))
    assert "a" in G.nodes
    assert G.degree("a") == 0


# ── Agrégation par combinaison de producteurs ────────────────────────────────


def test_aggregate_regroupe_par_ensemble_de_producteurs():
    tracks = [
        _track(1, "One", _prod("A"), _prod("B")),
        _track(2, "Two", _prod("A"), _prod("B")),
        _track(3, "Solo1", _prod("A")),
        _track(4, "Solo2", _prod("A")),
    ]
    collab = aggregate_collab_groups(extract_track_groups(tracks))
    by_keys = {cg.keys: cg for cg in collab}
    assert set(by_keys) == {("a", "b"), ("a",)}  # 2 combinaisons distinctes
    assert by_keys[("a", "b")].track_count == 2
    assert by_keys[("a", "b")].track_titles == ("One", "Two")  # titres triés
    assert by_keys[("a",)].track_count == 2  # les 2 solos de A fusionnés


def test_aggregate_ordre_deterministe():
    tracks = [
        _track(1, "One", _prod("B"), _prod("C")),
        _track(2, "Two", _prod("A")),
    ]
    collab = aggregate_collab_groups(extract_track_groups(tracks))
    assert [cg.keys for cg in collab] == [("a",), ("b", "c")]  # trié par clés


# ── Déterminisme du layout ───────────────────────────────────────────────────


def test_layout_deterministe():
    tracks = [
        _track(1, "One", _prod("A"), _prod("B")),
        _track(2, "Two", _prod("B"), _prod("C")),
        _track(3, "Three", _prod("A"), _prod("C"), _prod("D")),
    ]
    G = build_collab_graph(extract_track_groups(tracks))
    pos1 = compute_layout(G)
    pos2 = compute_layout(G)
    assert set(pos1) == set(pos2)
    for key in pos1:
        assert np.array_equal(pos1[key], pos2[key])
