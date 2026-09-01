"""Les invariants d'une planche Bubble, énoncés une fois pour toutes.

Le moteur a longtemps été une pile de passes correctives ajoutées au fil des
retours sur planche. Chacune corrigeait un symptôme, aucune n'écrivait la règle :
deux défauts sont passés au travers (un cercle étranger capturé par une ellipse,
un dessin tassé au centre) parce qu'ils ne violaient rien d'écrit.

Ce fichier est la réponse : les règles y sont dites, et vérifiées. Un futur
réglage qui les casse rougit ici, et non trois semaines plus tard sur une planche.
"""

import math

from src.dataviz.bubble_layout import _ellipses_croisent, encloses, largest_void
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


# ── I3bis : deux ovales étrangers ne se croisent pas ─────────────────────────


def test_i3_ovales_sans_membre_commun_ne_se_croisent_pas():
    # Deux ovales qui PARTAGENT un artiste doivent se recouvrir — ils passent
    # tous deux par lui. Deux ovales étrangers l'un à l'autre, non : leur
    # croisement ne dit rien et brouille la lecture.
    spec = _spec(_reseau_dense())
    groupes = [g for g in spec.groups if len(g.member_keys) > 1]
    for i, a in enumerate(groupes):
        for b in groupes[i + 1 :]:
            if set(a.member_keys) & set(b.member_keys):
                continue
            assert not _ellipses_croisent(
                a.ellipse, b.ellipse
            ), f"{a.member_keys} croise {b.member_keys}"


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


def test_i4_titre_dans_la_zone():
    # Un titre hors cadre est un titre perdu : c'est une contrainte DURE, pas
    # une pénalité que la place libre pourrait racheter.
    spec = _spec(_reseau_dense())
    for groupe in spec.groups:
        for ring in groupe.rings:
            porteuse = groupe.ellipse.inflated(ring.offset)
            for i in range(7):
                px, py = porteuse.point_at(ring.t - 20.0 + 40.0 * i / 6.0)
                assert -1.0 <= px <= spec.width + 1.0, f"{ring.text!r} sort du cadre"
                assert -1.0 <= py <= spec.height + 1.0, f"{ring.text!r} sort du cadre"


def test_i4_titre_ni_barre_ni_flottant():
    # Le décalage d'un titre doit correspondre au côté où il est FINALEMENT
    # posé. Décidé avant le placement puis appliqué à un texte posé de l'autre
    # côté, il l'éloignait du double — le titre semblait ne plus être relié à
    # rien (retour utilisateur : « comme placé avant un déplacement de
    # l'ellipse »). Deux bornes, une par travers :
    from src.dataviz.bubble_labels import _cote_haut

    spec = _spec(_reseau_dense())
    capitale = spec.style.ellipse_label_font_size
    for groupe in spec.groups:
        for ring in groupe.rings:
            haut = _cote_haut(groupe.ellipse, ring.offset, ring.t)
            if not haut:
                # Sous l'ovale, les lettres poussent vers lui : sans le recul
                # d'une capitale, le tracé les barre.
                assert ring.offset >= capitale, f"{ring.text!r} est barré par son tracé"
            # Et personne ne part flotter : l'écart reste borné par ce que
            # justifient le côté, l'empilement et l'allongement d'un titre long.
            plafond = (
                capitale
                + spec.style.ellipse_label_max_extra_offset
                + spec.style.ellipse_label_line_gap * len(groupe.rings)
                + 20.0
            )
            assert ring.offset <= plafond, f"{ring.text!r} flotte à {ring.offset:.0f} px"


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


# ── I5 : le dessin occupe la zone SANS TROU ─────────────────────────────────


def test_i5_pas_de_grand_vide():
    # L'invariant se mesure par le plus grand disque VIDE qu'on puisse loger
    # dans la zone, rapporté à ce qui est atteignable pour ce nombre de cercles.
    #
    # (Il se mesurait avant par la boîte englobante. C'était trompeur : elle
    # valait 100 % dès que quelques cercles touchaient les bords, pendant que
    # l'intérieur restait béant — le défaut que l'utilisateur voyait et que la
    # métrique niait.)
    spec = _spec(_reseau_dense())
    vide = largest_void(
        {n.key: (n.x, n.y) for n in spec.nodes},
        {n.key: n.size for n in spec.nodes},
        spec.style,
    )
    ideal = math.sqrt(spec.width * spec.height / (math.pi * len(spec.nodes)))
    assert vide / ideal <= 2.0, f"trou de {vide:.0f} px, soit {vide / ideal:.1f}× l'idéal"


# ── I7 : une bulle solo est isolée par la distance ───────────────────────────


def _solos_du_spec(spec):
    """Les nœuds SANS aucune collaboration (membres d'aucun groupe multi)."""
    multi = {k for g in spec.groups if len(g.member_keys) > 1 for k in g.member_keys}
    return [n for n in spec.nodes if n.key not in multi]


def test_i7_solo_ecarte_du_reseau():
    # Une bulle SOLO n'a pas d'ovale dans le jeu des contraintes (`len(g) > 1`,
    # trois réintégrations tentées et annulées — JOURNAL 2026-09-01) : c'est la
    # DISTANCE qui l'isole. Sans elle, elle se fondait dans le tas et se lisait
    # comme un membre (cas mammouth sur M.A.N).
    spec = _spec(_reseau_dense())
    solos = _solos_du_spec(spec)
    assert solos, "le réseau dense doit avoir au moins un solo (F)"
    minimum = spec.style.gap + spec.style.gap_solo
    for s in solos:
        for other in spec.nodes:
            if other.key == s.key:
                continue
            ecart = math.hypot(s.x - other.x, s.y - other.y) - (s.size + other.size) / 2.0
            assert ecart >= minimum - 1e-6, f"{s.key} à {ecart:.0f} px de {other.key}"


def test_i7_un_artiste_mixte_n_est_pas_un_solo():
    # « Hub » a un morceau seul ET des collaborations : ses ovales le rattachent
    # déjà au réseau, il ne doit PAS être écarté comme un solo.
    spec = _spec(_reseau_dense())
    cles_solo = {n.key for n in _solos_du_spec(spec)}
    hub = next(n.key for n in spec.nodes if n.display == "Hub")
    assert hub not in cles_solo


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


# ── I8 : l'écart d'un titre au tracé reste PRESQUE fixe ──────────────────────


def test_i8_ecart_des_titres_quasi_fixe():
    # « Certains partent trop loin de leur cercle » (2026-09-01) : l'écart au
    # tracé est ce qui rattache un titre à son ovale. Il ne doit varier que de
    # ce que justifient le côté (recul d'une capitale sous l'ovale), le reliquat
    # d'écartement plafonné, et l'empilement des anneaux.
    from src.dataviz.bubble_labels import label_offset

    spec = _spec(_reseau_dense())
    base = label_offset(spec.style)
    # Un ovale porte au plus DEUX titres (au-delà, `label_track_threshold`
    # bascule sur « N morceaux ») : ils se posent de part et d'autre, au même
    # écart, sans empilement. Le seul supplément légitime est le recul d'une
    # capitale sous l'ovale, plus le reliquat d'écartement plafonné.
    plafond = base + spec.style.ellipse_label_font_size + spec.style.ellipse_label_max_extra_offset
    for groupe in spec.groups:
        assert len(groupe.rings) <= 2, f"{groupe.member_keys} empile {len(groupe.rings)} titres"
        for ring in groupe.rings:
            assert base - 1e-6 <= ring.offset <= plafond, (
                f"{ring.text!r} à {ring.offset:.0f} px du tracé (base {base:.0f}, "
                f"plafond {plafond:.0f})"
            )


# ── I9 : un titre trop long est coupé, ses moitiés de part et d'autre ────────


def test_i9_titre_long_coupe_en_haut_et_en_bas():
    # Un producteur SEUL sur un morceau au titre long : son ovale est petit, le
    # titre n'y tient pas. Plutôt que de l'écarter très loin, on le coupe en
    # deux — 1ʳᵉ moitié en haut, 2ᵈᵉ en bas (demande utilisateur 2026-09-01).
    tracks = [
        _track(1, "On sourit pas sur les photos", "Al", _prod("Solo")),
        _track(2, "T2", "Al", _prod("A"), _prod("B")),
        _track(3, "T3", "Al", _prod("A"), _prod("C")),
    ]
    spec = _spec(tracks)
    groupe = next(g for g in spec.groups if g.member_keys == ("solo",))
    assert len(groupe.rings) == 2, "le titre long doit être coupé en deux"
    assert groupe.rings[0].text == "On sourit pas"
    assert groupe.rings[1].text == "sur les photos"
    # Une moitié au-dessus du centre de l'ovale, l'autre en dessous.
    ys = [
        groupe.ellipse.inflated(r.offset).point_at(r.t)[1] - groupe.ellipse.cy for r in groupe.rings
    ]
    assert ys[0] < 0 < ys[1], f"les moitiés ne sont pas de part et d'autre : {ys}"


def test_i9_coupe_desactivable_et_titre_court_intact():
    from src.dataviz.bubble_labels import split_text

    # Un titre qui tient n'est jamais coupé.
    spec = _spec(_reseau_dense())
    for groupe in spec.groups:
        for ring in groupe.rings:
            assert (
                ring.text in ("T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8")
                or len(groupe.rings) > 1
            )

    # La coupe se refuse quand elle n'a pas de sens.
    assert split_text("Fiesta") is None  # un seul mot
    assert split_text("") is None
    assert split_text("On sourit pas sur les photos") == ("On sourit pas", "sur les photos")

    # Et elle est désactivable.
    tracks = [
        _track(1, "On sourit pas sur les photos", "Al", _prod("Solo")),
        _track(2, "T2", "Al", _prod("A"), _prod("B")),
        _track(3, "T3", "Al", _prod("A"), _prod("C")),
    ]
    spec = _spec(tracks, style=SvgStyle(split_long_titles=False))
    groupe = next(g for g in spec.groups if g.member_keys == ("solo",))
    assert len(groupe.rings) == 1


def test_i8_ecart_coherent_avec_le_cote():
    # L'écart doit correspondre au côté RÉELLEMENT obtenu : le point fixe
    # pouvait sortir sur une bascule (garde anti-oscillation) et laisser un
    # texte posé AU-DESSUS porter le recul du bas — il paraissait alors trop
    # loin (« La familia », « Gros spectacle », 2026-09-01).
    from src.dataviz.bubble_labels import _cote_haut, label_offset

    spec = _spec(_reseau_dense())
    base = label_offset(spec.style)
    recul = spec.style.ellipse_label_font_size * spec.style.cap_height_ratio
    for groupe in spec.groups:
        for ring in groupe.rings:
            attendu = base if _cote_haut(groupe.ellipse, ring.offset, ring.t) else base + recul
            assert abs(ring.offset - attendu) < 1e-6, (
                f"{ring.text!r} à {ring.offset:.1f} px alors que son côté impose "
                f"{attendu:.1f} px"
            )


def test_i8_le_recul_vaut_une_capitale_pas_une_police():
    # Le texte du bas doit être à la MÊME distance visuelle du tracé que celui
    # du haut : il recule d'une hauteur de capitale (~0,72 em), pas d'une police
    # entière — sinon l'asymétrie se voit sur les deux moitiés d'un titre coupé.
    from src.dataviz.bubble_labels import label_offset

    style = SvgStyle()
    assert style.cap_height_ratio < 1.0
    base = label_offset(style)
    recul = style.ellipse_label_font_size * style.cap_height_ratio
    assert base + recul < base + style.ellipse_label_font_size


def test_i9_un_titre_ne_traverse_pas_un_cercle_etranger():
    # Le côté préféré ne vaut pas la lisibilité : imposé durement, il enfermait
    # un titre dans une moitié encombrée jusqu'à le faire passer DANS un cercle
    # (« 3ein » à −14 px, « Brûle » caché par Lucci', 2026-09-01).
    from src.dataviz.bubble_labels import _degagement

    spec = _spec(_reseau_dense())
    circles = {n.key: (n.x, n.y, n.size / 2.0) for n in spec.nodes}
    for groupe in spec.groups:
        etrangers = [circles[k] for k in sorted(circles) if k not in groupe.member_keys]
        for ring in groupe.rings:
            degagement = _degagement(groupe.ellipse, ring, spec.style, etrangers)
            assert degagement > 0.0, f"{ring.text!r} traverse un cercle ({degagement:.1f} px)"


def test_i9_deux_titres_ne_se_superposent_jamais():
    # Un ovale traversé par plusieurs autres n'offre parfois qu'UNE zone
    # lisible : y envoyer un titre en haut et l'autre en bas les entassait au
    # même endroit (mesuré 3,3 px entre « 3ein / Risotto Gambas » et « Peace,
    # Haine, Love », 2026-09-01). Ils sont alors réunis sur une seule ligne.
    from src.dataviz.bubble_labels import _arc_points, text_span

    spec = _spec(_reseau_dense())
    arcs = []
    for groupe in spec.groups:
        for ring in groupe.rings:
            porteuse = groupe.ellipse.inflated(ring.offset)
            span = text_span(porteuse, ring.text, spec.style)
            arcs.append((ring.text, list(_arc_points(porteuse, ring.t, span))))
    for i, (t1, p1) in enumerate(arcs):
        for t2, p2 in arcs[i + 1 :]:
            distance = min(math.hypot(a[0] - b[0], a[1] - b[1]) for a in p1 for b in p2)
            assert (
                distance >= spec.style.ellipse_label_font_size
            ), f"{t1!r} et {t2!r} se superposent ({distance:.1f} px)"


def test_i9_paire_jugee_illisible_quand_les_deux_titres_se_touchent():
    # Le garde-fou qui décide de réunir deux titres sur une seule ligne : deux
    # textes posés au MÊME endroit doivent être jugés illisibles, sinon rien ne
    # déclenche le repli et ils restent superposés.
    from src.dataviz.bubble_labels import _paire_lisible
    from src.dataviz.bubble_svg import LabelRing
    from src.dataviz.geometry import EllipseSpec

    style = SvgStyle()
    ellipse = EllipseSpec(cx=400.0, cy=300.0, rx=150.0, ry=100.0, angle=0.0)
    colles = (
        LabelRing(text="Un", offset=11.5, t=0.0, sweep=1),
        LabelRing(text="Deux", offset=11.5, t=2.0, sweep=1),
    )
    assert not _paire_lisible(ellipse, colles, style, ())

    # À l'opposé du tour, la même paire est parfaitement lisible.
    ecartes = (
        LabelRing(text="Un", offset=11.5, t=0.0, sweep=1),
        LabelRing(text="Deux", offset=11.5, t=180.0, sweep=1),
    )
    assert _paire_lisible(ellipse, ecartes, style, ())
    assert style.label_join_separator == "•"
