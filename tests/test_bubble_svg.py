"""Smoke test du pipeline SVG Bubble Prod (structure + tailles + déterminisme).

Vérifie les invariants : 7 groupes-calques, nb cercles = nb producteurs, nb
ellipses = nb combinaisons de producteurs distinctes, légendes (titres ou
« N morceaux »), ids stables (`node-kalim`, `ellipse-…`), badge du bon compte,
zone de composition FIXE, gradation monotone et bornée des diamètres,
byte-identité entre deux runs, et le `ValueError` explicite quand l'album n'a
aucun producteur.
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
from src.dataviz.bubble_svg import SvgStyle
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


def _ellipse_labels(root):
    """Textes des légendes, par id d'ellipse. Ils vivent dans un `<textPath>` :
    le titre est CURVILIGNE, posé sur le tracé de son ovale."""
    labels = root.find(f"{SVG_NS}g[@id='ellipse-labels']")
    out = {}
    for text in labels.findall(f"{SVG_NS}text"):
        path = text.find(f"{SVG_NS}textPath")
        out[text.get("id")] = path.text if path is not None else text.text
    return out


def _spec(tracks, style=None):
    track_groups = extract_track_groups(tracks)
    graph = build_collab_graph(track_groups)
    collab_groups = aggregate_collab_groups(track_groups)
    return build_bubble_spec(graph, collab_groups, style)


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
        "nodes",
        "badges",
        "labels",
    }
    # Cadre présent : il matérialise la zone.
    assert groups["frame"].find(f"{SVG_NS}rect").get("id") == "frame-border"

    circles = groups["nodes"].findall(f"{SVG_NS}circle")
    assert len(circles) == 4  # un cercle par producteur

    # 3 combinaisons distinctes : {big,kalim}, {big,other}, {solo}.
    ellipses = groups["ellipses"].findall(f"{SVG_NS}path")
    assert len(ellipses) == 3

    ids = {el.get("id") for el in root.iter()}
    assert "node-kalim" in ids
    assert "ellipse-big--kalim" in ids
    assert "ellipse-solo" in ids

    badge = next(el for el in root.iter() if el.get("id") == "badge-count-kalim")
    assert badge.text == "2"


def test_legende_solo_un_morceau_affiche_le_titre(tmp_path):
    out = tmp_path / "bubble.svg"
    generate_bubble_prod(_album_tracks(), "TestAlbum", output_path=out)
    texts = _ellipse_labels(ET.parse(out).getroot())
    # Un seul morceau → son TITRE (« 1 morceau » n'apporterait rien — cas mammouth).
    assert texts["ellipse-label-solo-0"] == "T4"


def test_solos_annonces_par_le_badge_pas_par_une_legende(tmp_path):
    # Un producteur qui a des morceaux à plusieurs ET des solos le dit dans son
    # badge : « 3 (2 Solos) ». Posée sur l'ovale, la mention se perdait au
    # milieu des titres voisins.
    tracks = [
        _track(1, "S1", "Al", _prod("X")),
        _track(2, "S2", "Al", _prod("X")),
        _track(3, "Duo", "Al", _prod("X"), _prod("Y")),
    ]
    out = tmp_path / "bubble.svg"
    generate_bubble_prod(tracks, "Al", output_path=out)
    root = ET.parse(out).getroot()
    badge = next(el for el in root.iter() if el.get("id") == "badge-count-x")
    assert badge.text == "3 (2 Solos)"
    # Aucune légende « N solo » ne subsiste sur les ovales.
    assert not [v for v in _ellipse_labels(root).values() if "olo" in v]


def test_badge_sans_detail_quand_tout_est_solo(tmp_path):
    # Un producteur qui n'a QUE des solos garde son simple compte : le répéter
    # entre parenthèses n'apprendrait rien.
    tracks = [_track(1, "S1", "Al", _prod("X")), _track(2, "S2", "Al", _prod("X"))]
    out = tmp_path / "bubble.svg"
    generate_bubble_prod(tracks, "Al", output_path=out)
    root = ET.parse(out).getroot()
    badge = next(el for el in root.iter() if el.get("id") == "badge-count-x")
    assert badge.text == "2"


def test_name_lines():
    from src.dataviz.bubble_svg import name_lines as _name_lines

    assert _name_lines("PRICE D.") == ("PRICE D.",)  # initiale collée, une ligne
    assert _name_lines("LEWIS AMBER") == ("LEWIS", "AMBER")
    assert _name_lines("ANTOINE BOREY") == ("ANTOINE", "BOREY")
    assert _name_lines("JOHNNY OLA") == ("JOHNNY", "OLA")
    assert _name_lines("MYSTR") == ("MYSTR",)
    assert _name_lines("J. COLE") == ("J. COLE",)  # initiale en tête aussi


def test_legende_posee_a_plat_et_lisible():
    # Le titre est CURVILIGNE. Il glisse vers le bout de son ovale — d'où une
    # tangente qui n'est plus exactement horizontale — mais jamais au-delà de
    # l'inclinaison tolérée, et toujours parcouru dans le sens qui l'écrit de
    # gauche à droite (sinon il est à l'envers).
    import math

    spec = _spec(_album_tracks())
    for gs in spec.groups:
        for ring in gs.rings:
            porteuse = gs.ellipse.inflated(ring.offset)
            tx, ty = porteuse.tangent_at(ring.t)
            angle = math.degrees(math.atan2(ty, tx))
            angle = ((angle + 90.0) % 180.0) - 90.0
            assert abs(angle) <= spec.style.ellipse_label_max_angle + 1e-9
            sens = 1.0 if ring.sweep else -1.0
            assert tx * sens > 0  # les lettres avancent vers la droite


def test_legende_du_noyau_part_vers_le_bord():
    # Consigne DA : les ovales du noyau poussent leur titre vers l'extérieur de
    # l'image. Le point retenu doit donc être plus loin du centre que le centre
    # de l'ovale lui-même.
    spec = _spec(_album_tracks())
    cx, cy = spec.width / 2.0, spec.height / 2.0
    duo = next(g for g in spec.groups if set(g.member_keys) == {"big", "kalim"})
    ring = duo.rings[0]
    px, py = duo.ellipse.inflated(ring.offset).point_at(ring.t)
    depuis_le_point = (px - cx) ** 2 + (py - cy) ** 2
    depuis_le_centre = (duo.ellipse.cx - cx) ** 2 + (duo.ellipse.cy - cy) ** 2
    assert depuis_le_point > depuis_le_centre


def test_deux_titres_sur_deux_anneaux():
    # Deux morceaux sur un même ovale s'empilent l'un « sous » l'autre, sur deux
    # couronnes concentriques — pas en une longue ligne qui ferait le tour.
    spec = _spec(_album_tracks())
    duo = next(g for g in spec.groups if set(g.member_keys) == {"big", "kalim"})
    assert [r.text for r in duo.rings] == ["T1", "T2"]
    offsets = sorted(r.offset for r in duo.rings)
    assert offsets[1] - offsets[0] >= spec.style.ellipse_label_line_gap - 1e-9


def test_legende_duo_liste_les_titres(tmp_path):
    out = tmp_path / "bubble.svg"
    generate_bubble_prod(_album_tracks(), "TestAlbum", output_path=out)
    texts = _ellipse_labels(ET.parse(out).getroot())
    # Duo à 2 morceaux (≤ seuil) → un titre par anneau.
    assert texts["ellipse-label-big--kalim-0"] == "T1"
    assert texts["ellipse-label-big--kalim-1"] == "T2"


def test_legende_combinaison_au_dela_du_seuil(tmp_path):
    # Duo présent sur 4 morceaux (> seuil 3) → « 4 morceaux », pas les titres.
    tracks = [_track(i, f"T{i}", "Al", _prod("X"), _prod("Y")) for i in range(1, 5)]
    out = tmp_path / "bubble.svg"
    generate_bubble_prod(tracks, "Al", output_path=out)
    assert list(_ellipse_labels(ET.parse(out).getroot()).values()) == ["4 morceaux"]


def test_zone_fixe_et_cadre_confondu():
    # La zone ne suit PLUS le contenu : deux albums de tailles différentes
    # doivent sortir sur le même canevas, sinon les cercles ne sont plus
    # comparables d'une planche à l'autre.
    spec = _spec(_album_tracks())
    style = spec.style
    assert (spec.width, spec.height) == (style.frame_width, style.frame_height)
    assert spec.frame == (0.0, 0.0, style.frame_width, style.frame_height)

    petit = _spec([_track(1, "T1", "Al", _prod("Seul"))])
    assert (petit.width, petit.height) == (spec.width, spec.height)


def test_dimensions_independantes_du_nombre_de_producteurs():
    # Corollaire : un prod à 1 morceau fait le même diamètre sur les deux
    # albums (échelle absolue), c'est tout l'intérêt de la zone fixe.
    petit = _spec([_track(1, "T1", "Al", _prod("Seul"))])
    (node,) = petit.nodes
    assert node.size == pytest.approx(petit.style.node_size_min)


def test_debordement_signale_et_non_corrige():
    # Un gros réseau CONNECTÉ dans une zone volontairement minuscule : il DOIT
    # déborder, et le spec doit le dire au lieu de réduire les cercles.
    # (Réseau connecté et pas 12 solos : des composantes isolées sont calées
    # dans les coins de la zone, donc bornées par construction.)
    tracks = [_track(i, f"T{i}", "Al", _prod("Hub"), _prod(f"P{i}")) for i in range(1, 13)]
    spec = _spec(tracks, style=SvgStyle(frame_width=200.0, frame_height=120.0))
    assert spec.overflow is not None
    over_w, over_h = spec.overflow
    assert over_w > 0 or over_h > 0
    # Les diamètres n'ont pas bougé : rien n'a été mis à l'échelle pour rentrer.
    tailles = {n.key: n.size for n in spec.nodes}
    assert tailles["hub"] == pytest.approx(spec.style.node_size_max)
    assert min(v for k, v in tailles.items() if k != "hub") == pytest.approx(
        spec.style.node_size_min
    )


def test_pas_de_debordement_sur_un_album_normal():
    assert _spec(_album_tracks()).overflow is None


def test_ilots_ni_avales_ni_collis():
    # Un groupe isolé reste lisible COMME groupe isolé : ses cercles ne touchent
    # personne, et aucune ellipse du noyau ne les enveloppe — sinon ils
    # passeraient pour des membres.
    #
    # (Cette vérification remplace un test qui affirmait « les îlots sont dans
    # les coins ». C'était le MOYEN d'alors, pas la fin : le placement ne cale
    # plus rien aux coins, mais l'exigence, elle, tient toujours.)
    import math

    from src.dataviz.bubble_layout import encloses

    tracks = [
        _track(1, "H1", "Al", _prod("A"), _prod("B")),
        _track(2, "H2", "Al", _prod("A"), _prod("C")),
        _track(3, "H3", "Al", _prod("A"), _prod("D")),
        _track(4, "Duo", "Al", _prod("E"), _prod("F")),
        _track(5, "Solo", "Al", _prod("G")),
    ]
    spec = _spec(tracks)
    pos = {n.key: n for n in spec.nodes}
    for ilot in ("e", "f", "g"):
        for autre in ("a", "b", "c", "d"):
            distance = math.hypot(pos[ilot].x - pos[autre].x, pos[ilot].y - pos[autre].y)
            assert distance > (pos[ilot].size + pos[autre].size) / 2.0
        for groupe in spec.groups:
            if ilot in set(groupe.member_keys):
                continue
            assert not encloses(groupe.ellipse, pos[ilot].x, pos[ilot].y)


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


def test_ellipses_en_chemin_avec_rotation_cuite(tmp_path):
    # Les ovales sont des <path> et non des <ellipse> : c'est ce qui permet d'y
    # poser du texte curviligne. Et la rotation est CUITE dans le chemin — un
    # `transform` sur le tracé ne suivrait pas le texte posé dessus.
    out = tmp_path / "bubble.svg"
    generate_bubble_prod(_album_tracks(), "TestAlbum", output_path=out)
    root = ET.parse(out).getroot()
    ellipses = root.find(f"{SVG_NS}g[@id='ellipses']").findall(f"{SVG_NS}path")
    assert ellipses
    for el in ellipses:
        assert el.get("transform") is None
        assert " A " in el.get("d")

    # Chaque légende pointe le chemin de SON ovale.
    labels = root.find(f"{SVG_NS}g[@id='ellipse-labels']")
    for text in labels.findall(f"{SVG_NS}text"):
        path = text.find(f"{SVG_NS}textPath")
        href = path.get("{http://www.w3.org/1999/xlink}href") or path.get("href")
        token = text.get("id").replace("ellipse-label-", "")
        assert href == f"#ellipse-labelpath-{token}"


def test_trio_produit_une_ellipse(tmp_path):
    tracks = [_track(1, "Trio", "Al", _prod("A"), _prod("B"), _prod("C"))]
    out = tmp_path / "trio.svg"
    res = generate_bubble_prod(tracks, "Al", output_path=out)
    assert res.node_count == 3
    root = ET.parse(out).getroot()
    ellipses = root.find(f"{SVG_NS}g[@id='ellipses']").findall(f"{SVG_NS}path")
    assert len(ellipses) == 1


# ── Tailles pondérées ────────────────────────────────────────────────────────


def test_tailles_monotones_et_bornees():
    spec = _spec(_album_tracks())
    size = {n.key: n.size for n in spec.nodes}
    style = spec.style
    assert size["big"] > size["kalim"] > size["other"]  # gradation par participation
    assert size["other"] == size["solo"]  # comptes égaux → tailles égales
    assert size["other"] == pytest.approx(style.node_size_min)  # compte 1 = min
    assert size["big"] == pytest.approx(style.node_size_max)  # plus gros compte = max
    assert min(size.values()) >= style.node_size_min - 1e-9
    assert max(size.values()) <= style.node_size_max + 1e-9


def test_taille_du_nom_suit_le_diametre():
    # Un petit cercle ne peut pas porter 25 px : la taille descend au prorata
    # du diamètre, sans jamais passer sous le plancher de lisibilité.
    spec = _spec(_album_tracks())
    style = spec.style
    font = {n.key: n.label_font_size for n in spec.nodes}
    assert font["big"] == pytest.approx(style.font_size)  # plus gros cercle = taille pleine
    assert font["other"] < font["big"]
    assert min(font.values()) >= style.font_size_min


def test_tous_comptes_egaux_donnent_taille_min():
    # Deux prods à 2 morceaux chacun : aucune gradation → tous à la taille min.
    tracks = [
        _track(1, "A", "Al", _prod("X"), _prod("Y")),
        _track(2, "B", "Al", _prod("X"), _prod("Y")),
    ]
    spec = _spec(tracks)
    for n in spec.nodes:
        assert n.size == spec.style.node_size_min


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


def test_titre_nettoye_des_fioritures():
    # Genius stylise certains titres en barrant chaque lettre (« F̶i̶e̶s̶t̶a̶ ») :
    # c'est de la décoration, illisible sur une bulle. Les accents combinants,
    # eux, sont de vraies lettres et doivent survivre.
    from src.dataviz.bubble_prod import clean_track_title

    assert clean_track_title("F̶i̶e̶s̶t̶a̶ (Interlude)") == "Fiesta"
    assert clean_track_title("Brûle") == "Brûle"
    assert clean_track_title("Mort Ce soir (feat. X) [Bonus]") == "Mort Ce soir"
    assert clean_track_title("3ein / Risotto Gambas") == "3ein / Risotto Gambas"
    assert clean_track_title("J'ai (encore) faim") == "J'ai (encore) faim"


def test_aucun_chevauchement_entre_cercles_meme_hors_composante():
    # Deux cercles ne se marchent JAMAIS dessus, y compris quand ils
    # appartiennent à des composantes différentes : l'anti-chevauchement ne
    # travaille qu'à l'intérieur d'une composante, un satellite du noyau venait
    # donc se coller à un îlot calé dans son coin.
    import math

    tracks = [
        _track(1, "H1", "Al", _prod("A"), _prod("B")),
        _track(2, "H2", "Al", _prod("A"), _prod("C")),
        _track(3, "H3", "Al", _prod("A"), _prod("D")),
        _track(4, "Duo", "Al", _prod("E"), _prod("F")),
        _track(5, "Solo", "Al", _prod("G")),
        _track(6, "Solo2", "Al", _prod("H")),
    ]
    spec = _spec(tracks)
    nodes = list(spec.nodes)
    for i, a in enumerate(nodes):
        for b in nodes[i + 1 :]:
            distance = math.hypot(a.x - b.x, a.y - b.y)
            assert distance >= (a.size + b.size) / 2.0 - 1e-6


def test_instrument_sous_le_nom(tmp_path):
    # Les instrumentistes entrent dans le réseau (option) : leur instrument est
    # posé sous leur nom, en minuscules avec une capitale à la première lettre.
    # Passe par `generate_bubble_prod` : c'est lui qui élargit le filtre de
    # rôles et récolte les instruments.
    from src.models.track import Credit, CreditRole

    tracks = _album_tracks()
    tracks[0].credits.append(Credit(name="Sofiane Pamart", role=CreditRole.PIANO))
    result = generate_bubble_prod(tracks, "TestAlbum", output_path=tmp_path / "b.svg")
    pamart = next(n for n in result.spec.nodes if n.key == "sofiane pamart")
    assert pamart.sub_label == "Piano"
    # Un producteur sans instrument n'a rien sous son nom.
    assert next(n for n in result.spec.nodes if n.key == "kalim").sub_label == ""


def test_instrument_desactivable(tmp_path):
    from src.models.track import Credit, CreditRole

    tracks = _album_tracks()
    tracks[0].credits.append(Credit(name="Sofiane Pamart", role=CreditRole.PIANO))
    result = generate_bubble_prod(
        tracks,
        "TestAlbum",
        style=SvgStyle(include_instruments=False),
        output_path=tmp_path / "b.svg",
    )
    assert all(n.key != "sofiane pamart" for n in result.spec.nodes)
