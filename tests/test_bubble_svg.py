"""Smoke test du pipeline SVG Bubble Prod (structure + tailles + déterminisme).

Vérifie les invariants : 6 groupes-calques, nb rects = nb producteurs, nb
ellipses = nb combinaisons de producteurs distinctes, légendes (titres ou
« N morceaux »), ids stables (`square-kalim`, `ellipse-…`), badge du bon compte,
coins arrondis, gradation monotone et bornée des tailles, byte-identité entre
deux runs, et le `ValueError` explicite quand l'album n'a aucun producteur.
"""

import xml.etree.ElementTree as ET

import networkx as nx
import pytest

from src.dataviz.bubble_prod import (
    build_bubble_spec,
    generate_bubble_prod,
    list_albums,
    select_album_tracks,
)
from src.dataviz.collab_graph import (
    aggregate_collab_groups,
    build_collab_graph,
    extract_track_groups,
)
from src.models.track import Credit, CreditRole, Track

SVG_NS = "{http://www.w3.org/2000/svg}"


def _prod(name):
    return Credit(name=name, role=CreditRole.PRODUCER)


def _track(tid, title, album, *credits):
    t = Track(id=tid, title=title, album=album)
    t.credits = list(credits)
    return t


def _album_tracks():
    # Comptes contrôlés : Big=3, Kalim=2, Other=1, Solo=1 (max=3, min=1).
    return [
        _track(1, "T1", "TestAlbum", _prod("Kalim"), _prod("Big")),
        _track(2, "T2", "TestAlbum", _prod("Kalim"), _prod("Big")),
        _track(3, "T3", "TestAlbum", _prod("Big"), _prod("Other")),
        _track(4, "T4", "TestAlbum", _prod("Solo")),
    ]


def _spec(tracks):
    track_groups = extract_track_groups(tracks)
    graph = build_collab_graph(track_groups)
    collab_groups = aggregate_collab_groups(track_groups)
    return build_bubble_spec(graph, collab_groups)


# ── Structure du SVG ─────────────────────────────────────────────────────────


def test_smoke_structure(tmp_path):
    out = tmp_path / "bubble.svg"
    res = generate_bubble_prod(_album_tracks(), "TestAlbum", output_path=out)
    assert res.node_count == 4
    assert res.track_count == 4  # nb de morceaux crédités (pas de combinaisons)

    root = ET.parse(out).getroot()
    groups = {g.get("id"): g for g in root.findall(f"{SVG_NS}g")}
    assert set(groups) == {
        "frame",
        "edges",
        "ellipses",
        "ellipse-labels",
        "squares",
        "badges",
        "labels",
    }
    # Cadre carré présent.
    assert groups["frame"].find(f"{SVG_NS}rect").get("id") == "frame-border"

    rects = groups["squares"].findall(f"{SVG_NS}rect")
    assert len(rects) == 4  # un carré par producteur
    assert all(r.get("rx") is not None for r in rects)  # coins arrondis

    # 3 combinaisons distinctes : {big,kalim}, {big,other}, {solo}.
    ellipses = groups["ellipses"].findall(f"{SVG_NS}ellipse")
    assert len(ellipses) == 3

    ids = {el.get("id") for el in root.iter()}
    assert "square-kalim" in ids
    assert "ellipse-big--kalim" in ids
    assert "ellipse-solo" in ids

    badge = next(el for el in root.iter() if el.get("id") == "badge-count-kalim")
    assert badge.text == "2"


def test_legende_solo_un_morceau_affiche_le_titre(tmp_path):
    out = tmp_path / "bubble.svg"
    generate_bubble_prod(_album_tracks(), "TestAlbum", output_path=out)
    root = ET.parse(out).getroot()
    labels = root.find(f"{SVG_NS}g[@id='ellipse-labels']")
    texts = {el.get("id"): el.text for el in labels.findall(f"{SVG_NS}text")}
    # Un seul morceau → son TITRE (« 1 morceau » n'apporterait rien — cas mammouth).
    assert texts["ellipse-label-solo-0"] == "T4"


def test_legende_solo_plusieurs_morceaux_compte(tmp_path):
    # Producteur seul sur 2 morceaux → « 2 solo » (affiché dans son carré).
    tracks = [
        _track(1, "S1", "Al", _prod("X")),
        _track(2, "S2", "Al", _prod("X")),
    ]
    out = tmp_path / "bubble.svg"
    generate_bubble_prod(tracks, "Al", output_path=out)
    root = ET.parse(out).getroot()
    labels = root.find(f"{SVG_NS}g[@id='ellipse-labels']")
    texts = [el.text for el in labels.findall(f"{SVG_NS}text")]
    assert texts == ["2 solo"]


def test_name_lines():
    from src.dataviz.bubble_svg import _name_lines

    assert _name_lines("PRICE D.") == ("PRICE D.",)  # initiale collée, une ligne
    assert _name_lines("LEWIS AMBER") == ("LEWIS", "AMBER")
    assert _name_lines("ANTOINE BOREY") == ("ANTOINE", "BOREY")
    assert _name_lines("JOHNNY OLA") == ("JOHNNY", "OLA")
    assert _name_lines("MYSTR") == ("MYSTR",)
    assert _name_lines("J. COLE") == ("J. COLE",)  # initiale en tête aussi


def test_legende_angle_amorti():
    # La rotation du texte est bornée (± max_angle) : reste « un peu droit ».
    spec = _spec(_album_tracks())
    for gs in spec.groups:
        assert abs(gs.label_angle) <= spec.style.ellipse_label_max_angle + 1e-9


def test_legende_duo_liste_les_titres(tmp_path):
    out = tmp_path / "bubble.svg"
    generate_bubble_prod(_album_tracks(), "TestAlbum", output_path=out)
    root = ET.parse(out).getroot()
    labels = root.find(f"{SVG_NS}g[@id='ellipse-labels']")
    lines = [
        el.text
        for el in labels.findall(f"{SVG_NS}text")
        if el.get("id").startswith("ellipse-label-big--kalim")
    ]
    assert lines == ["T1", "T2"]  # duo à 2 morceaux (≤ seuil) → titres


def test_legende_combinaison_au_dela_du_seuil(tmp_path):
    # Duo présent sur 4 morceaux (> seuil 3) → « 4 morceaux », pas les titres.
    tracks = [_track(i, f"T{i}", "Al", _prod("X"), _prod("Y")) for i in range(1, 5)]
    out = tmp_path / "bubble.svg"
    generate_bubble_prod(tracks, "Al", output_path=out)
    root = ET.parse(out).getroot()
    labels = root.find(f"{SVG_NS}g[@id='ellipse-labels']")
    texts = [el.text for el in labels.findall(f"{SVG_NS}text")]
    assert texts == ["4 morceaux"]


def test_cadre_carre():
    spec = _spec(_album_tracks())
    assert spec.width == spec.height  # cadre carré
    assert spec.frame is not None
    fx, fy, fw, fh = spec.frame
    assert fw == fh  # le cadre lui-même est carré


def test_ilots_dans_les_coins():
    # Un hub (A avec B,C,D) + deux îlots (duo E-F, solo G) → îlots écartés du hub.
    tracks = [
        _track(1, "H1", "Al", _prod("A"), _prod("B")),
        _track(2, "H2", "Al", _prod("A"), _prod("C")),
        _track(3, "H3", "Al", _prod("A"), _prod("D")),
        _track(4, "Duo", "Al", _prod("E"), _prod("F")),
        _track(5, "Solo", "Al", _prod("G")),
    ]
    spec = _spec(tracks)
    pos = {n.key: (n.x, n.y) for n in spec.nodes}
    hub = pos["a"]
    # Les membres d'îlots sont plus loin du hub que ses propres voisins.
    d_island = min(
        ((pos[k][0] - hub[0]) ** 2 + (pos[k][1] - hub[1]) ** 2) ** 0.5 for k in ("e", "f", "g")
    )
    d_hub = max(
        ((pos[k][0] - hub[0]) ** 2 + (pos[k][1] - hub[1]) ** 2) ** 0.5 for k in ("b", "c", "d")
    )
    assert d_island > d_hub


def test_spec_insensible_a_l_ordre_d_insertion_des_noeuds():
    # Garde-fou déterminisme : l'ordre d'insertion des nœuds du graphe (qui
    # variait d'un process à l'autre via les sous-graphes) ne doit PAS changer le
    # layout. Sinon les îlots permutent → SVG non byte-identique entre deux runs.
    tracks = [
        _track(1, "H1", "Al", _prod("A"), _prod("B")),
        _track(2, "H2", "Al", _prod("A"), _prod("C")),
        _track(3, "Duo", "Al", _prod("E"), _prod("F")),  # îlot 2 nœuds
    ]
    tg = extract_track_groups(tracks)
    cg = aggregate_collab_groups(tg)
    g1 = build_collab_graph(tg)

    g2 = nx.Graph()  # même graphe, nœuds insérés en ordre inverse
    for node in reversed(list(g1.nodes)):
        g2.add_node(node, **g1.nodes[node])
    g2.add_edges_from(g1.edges(data=True))

    s1 = build_bubble_spec(g1, cg)
    s2 = build_bubble_spec(g2, cg)
    assert [(n.key, n.x, n.y) for n in s1.nodes] == [(n.key, n.x, n.y) for n in s2.nodes]


def test_transform_rotate_present_sur_ellipses(tmp_path):
    out = tmp_path / "bubble.svg"
    generate_bubble_prod(_album_tracks(), "TestAlbum", output_path=out)
    root = ET.parse(out).getroot()
    for el in root.iter(f"{SVG_NS}ellipse"):
        assert el.get("transform", "").startswith("rotate(")


def test_trio_produit_une_ellipse(tmp_path):
    tracks = [_track(1, "Trio", "Al", _prod("A"), _prod("B"), _prod("C"))]
    out = tmp_path / "trio.svg"
    res = generate_bubble_prod(tracks, "Al", output_path=out)
    assert res.node_count == 3
    root = ET.parse(out).getroot()
    ellipses = root.find(f"{SVG_NS}g[@id='ellipses']").findall(f"{SVG_NS}ellipse")
    assert len(ellipses) == 1


# ── Tailles pondérées ────────────────────────────────────────────────────────


def test_tailles_monotones_et_bornees():
    spec = _spec(_album_tracks())
    size = {n.key: n.size for n in spec.nodes}
    style = spec.style
    assert size["big"] > size["kalim"] > size["other"]  # gradation par participation
    assert size["other"] == size["solo"]  # comptes égaux → tailles égales
    assert size["other"] == pytest.approx(style.square_size_min)  # compte 1 = min
    assert size["big"] == pytest.approx(style.square_size_max)  # plus gros compte = max
    assert min(size.values()) >= style.square_size_min - 1e-9
    assert max(size.values()) <= style.square_size_max + 1e-9


def test_tous_comptes_egaux_donnent_taille_min():
    # Deux prods à 2 morceaux chacun : aucune gradation → tous à la taille min.
    tracks = [
        _track(1, "A", "Al", _prod("X"), _prod("Y")),
        _track(2, "B", "Al", _prod("X"), _prod("Y")),
    ]
    spec = _spec(tracks)
    for n in spec.nodes:
        assert n.size == spec.style.square_size_min


# ── Déterminisme ─────────────────────────────────────────────────────────────


def test_deterministe_byte_identique(tmp_path):
    a = tmp_path / "a.svg"
    b = tmp_path / "b.svg"
    generate_bubble_prod(_album_tracks(), "TestAlbum", output_path=a)
    generate_bubble_prod(_album_tracks(), "TestAlbum", output_path=b)
    assert a.read_bytes() == b.read_bytes()


# ── Sélection d'album ────────────────────────────────────────────────────────


def test_list_albums_dedup_normalise():
    tracks = [
        _track(1, "a", "Vol.3", _prod("X")),
        _track(2, "b", "Vol. 3", _prod("Y")),
        _track(3, "c", "Other", _prod("Z")),
    ]
    albums = list_albums(tracks)
    assert len(albums) == 2  # « Vol.3 » et « Vol. 3 » fusionnés


def test_select_album_tracks_normalise():
    tracks = [
        _track(1, "a", "Vol.3", _prod("X")),
        _track(2, "b", "Vol. 3", _prod("Y")),
        _track(3, "c", "Other", _prod("Z")),
    ]
    assert len(select_album_tracks(tracks, "Vol.3")) == 2


# ── Grille d'aperçus (variantes de seed) ─────────────────────────────────────


def test_preview_grid(tmp_path):
    from src.dataviz.bubble_prod import generate_preview_grid

    html_path = generate_preview_grid(
        _album_tracks(), "TestAlbum", output_dir=tmp_path, seeds=(42, 7)
    )
    assert html_path.exists()
    html = html_path.read_text(encoding="utf-8")
    assert 'src="bubble_prod_seed42.svg"' in html
    assert 'src="bubble_prod_seed7.svg"' in html
    assert "Variante 42 (défaut)" in html
    # Les SVG des variantes existent et sont des SVG valides.
    for seed in (42, 7):
        svg = tmp_path / f"bubble_prod_seed{seed}.svg"
        assert svg.exists()
        ET.parse(svg)  # parse sans erreur


# ── Erreurs ──────────────────────────────────────────────────────────────────


def test_aucun_producteur_leve_valueerror():
    tracks = [_track(1, "A", "Al", Credit(name="Z", role=CreditRole.MIXING_ENGINEER))]
    with pytest.raises(ValueError):
        generate_bubble_prod(tracks, "Al", output_path="unused.svg")


def test_album_inconnu_leve_valueerror():
    with pytest.raises(ValueError):
        generate_bubble_prod(_album_tracks(), "AlbumInexistant", output_path="unused.svg")
