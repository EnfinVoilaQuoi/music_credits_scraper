"""Tests du générateur « Structure » (barres de sections par morceau).

Couvre : le découpage temporel `extract_sections` (dont la MONOTONIE, invariant
partagé avec `annotate_sections`), la classification des tags en 4 types, la
déduction intro/outro à partir des silences, la fusion des segments contigus,
l'échelle min/max du rectangle « durée », la ligne d'agrégat pondérée, la
validation bloquante et la byte-identité du SVG entre deux rendus.
"""

import json
import xml.etree.ElementTree as ET

import pytest

from src.dataviz.structure import (
    build_structure_spec,
    generate_structure,
    section_kind,
)
from src.dataviz.structure_json import PAYLOAD_VERSION, build_payload, write_structure_json
from src.dataviz.structure_style_io import default_payload, load_style, strip_comments
from src.dataviz.structure_svg import (
    StructureStyle,
    _segment_bounds,
    compute_layout,
    format_duration,
    write_structure_svg,
)
from src.models.track import Track
from src.utils.lyrics_sync import extract_sections
from src.utils.title_matching import clean_display_title, split_title_paren

SVG_NS = "{http://www.w3.org/2000/svg}"


def _lrc(*lignes: tuple[int, str]) -> str:
    return "\n".join(f"[{t // 60:02d}:{t % 60:02d}.00] {txt}" for t, txt in lignes)


def _track(tid, title, duration, structured, lrc, *, number=None, bpm=None, album="TestAlbum"):
    t = Track(id=tid, title=title, album=album, duration=duration, track_number=number)
    t.lyrics.text = structured
    t.lyrics.synced = lrc
    t.audio.bpm = bpm
    return t


# Morceau de référence : 60 s, lignes toutes les 4 s (cadence réaliste d'un LRC
# de rap — un espacement plus large déclencherait la détection de plage
# instrumentale). Attendu : intro déduite 0→10, couplet 10→18, refrain 18→26,
# pont 26→34, couplet 34→42 (dernière ligne à 38 + 4 s), outro déduite 42→60.
_STRUCTURED = "\n".join(
    [
        "[Couplet 1 : Isha]",
        "premiere ligne du couplet",
        "deuxieme ligne du couplet",
        "[Refrain]",
        "la ligne du refrain",
        "la seconde ligne du refrain",
        "[Pont]",
        "la ligne du pont",
        "la seconde ligne du pont",
        "[Couplet 2]",
        "encore une ligne de couplet",
        "et la toute derniere ligne",
    ]
)
_LRC = _lrc(
    (10, "premiere ligne du couplet"),
    (14, "deuxieme ligne du couplet"),
    (18, "la ligne du refrain"),
    (22, "la seconde ligne du refrain"),
    (26, "la ligne du pont"),
    (30, "la seconde ligne du pont"),
    (34, "encore une ligne de couplet"),
    (38, "et la toute derniere ligne"),
)


def _ref_track(**kwargs):
    return _track(1, "Ref", 60, _STRUCTURED, _LRC, **kwargs)


# ── Découpage temporel ───────────────────────────────────────────────────────


class TestExtractSections:
    def test_intervalles(self):
        sections = extract_sections(_STRUCTURED, _LRC)
        assert [s.label for s in sections] == ["Couplet 1 : Isha", "Refrain", "Pont", "Couplet 2"]
        # `end` = borne d'affichage (début de la section suivante) ;
        # `sung_end` = dernière ligne réellement chantée de la section.
        assert [(s.start, s.end) for s in sections] == [
            (10.0, 18.0),
            (18.0, 26.0),
            (26.0, 34.0),
            (34.0, 38.0),
        ]
        assert [s.sung_end for s in sections] == [14.0, 22.0, 30.0, 38.0]

    def test_section_non_alignee(self):
        structured = "[Intro]\nune ligne absente du lrc\n[Couplet]\npremiere ligne du couplet"
        sections = extract_sections(structured, _LRC)
        assert sections[0].start is None and sections[0].end is None
        assert sections[1].start == 10.0

    def test_monotonie_refrain_repete(self):
        # Le 2e refrain ne doit PAS se caler sur la 1ʳᵉ occurrence (piège CLAUDE.md).
        structured = "\n".join(
            [
                "[Refrain]",
                "meme ligne de refrain",
                "[Couplet]",
                "une autre ligne",
                "[Refrain 2]",
                "meme ligne de refrain",
            ]
        )
        lrc = _lrc(
            (5, "meme ligne de refrain"), (20, "une autre ligne"), (40, "meme ligne de refrain")
        )
        starts = [s.start for s in extract_sections(structured, lrc)]
        assert starts == [5.0, 20.0, 40.0]

    def test_alignement_par_prefixe_genius_agrege(self):
        # Genius fusionne 2 lignes LRC en une → ni exact, ni similaire, mais
        # l'une commence par l'autre : l'ancrage temporel est certain.
        structured = "[Couplet 1]\nune ligne assez longue pour compter, et sa suite ici"
        lrc = _lrc((12, "une ligne assez longue pour compter"), (30, "fin"))
        assert extract_sections(structured, lrc)[0].start == 12.0

    def test_alignement_par_prefixe_hook_double(self):
        # Genius double la ligne de hook, le LRC la joint à la suivante :
        # aucun n'est préfixe de l'autre, mais le début commun est franc.
        structured = "[Refrain]\nchaque jour j ai le demon chaque jour j ai le demon"
        lrc = _lrc((20, "chaque jour j ai le demon l ennemi ira voir les anges"), (60, "fin"))
        assert extract_sections(structured, lrc)[0].start == 20.0

    def test_prefixe_trop_court_ne_matche_pas(self):
        # « j te jure » (< 15 car.) ne doit pas ouvrir n'importe quelle ligne.
        structured = "[Couplet]\nj te jure mon frere"
        lrc = _lrc((10, "j te jure ça va aller"), (40, "fin"))
        assert extract_sections(structured, lrc)[0].start is None

    def test_curseur_saute_les_lignes_restantes_de_la_section(self):
        # RÉGRESSION (« 3ein / Risotto Gambas ») : la 1ʳᵉ ligne du couplet clôt
        # aussi l'intro. En n'avançant que d'une ligne, le couplet s'accrochait à
        # l'occurrence DANS l'intro (2:37) au lieu de son vrai début (2:44).
        structured = "\n".join(
            [
                "[Intro]",
                "yeah ouais",
                "on peut t eteindre aussi vite qu on peut t allumer",
                "fuck yeah ouais",
                "[Couplet unique]",
                "on peut t eteindre aussi vite qu on peut t allumer",
            ]
        )
        lrc = _lrc(
            (154, "yeah ouais"),
            (157, "on peut t eteindre aussi vite qu on peut t allumer"),
            (159, "fuck yeah ouais"),
            (164, "on peut t eteindre aussi vite qu on peut t allumer"),
        )
        starts = [s.start for s in extract_sections(structured, lrc)]
        assert starts == [154.0, 164.0]

    def test_sans_lrc_ou_sans_sections(self):
        assert extract_sections(_STRUCTURED, "") == []
        assert extract_sections("pas de section", _LRC) == []


# ── Classification ───────────────────────────────────────────────────────────


class TestSectionKind:
    @pytest.mark.parametrize(
        ("label", "expected"),
        [
            ("Intro", "intro_outro"),
            ("Outro : Isha", "intro_outro"),
            ("Interlude", "intro_outro"),
            ("Couplet 1 : Isha", "couplet"),
            ("Verse 2", "couplet"),
            ("Partie 3", "couplet"),
            ("Refrain", "refrain"),
            ("Chorus", "refrain"),
            ("Hook", "refrain"),
            ("Pont", "pont"),
            ("Bridge", "pont"),
            ("Pre-Chorus", "pont"),  # priorité sur « chorus »
            ("Prechorus", "pont"),
            # Une plage non chantée est une intro/outro comme les autres.
            ("Outro Instrumentale", "intro_outro"),
            ("Piano", "intro_outro"),
            ("Solo de guitare", "intro_outro"),
            ("Instrumental", "intro_outro"),
            ("Tag exotique", "couplet"),  # fallback neutre
        ],
    )
    def test_mapping(self, label, expected):
        assert section_kind(label) == expected


# ── Segments d'un morceau ────────────────────────────────────────────────────


class TestSegments:
    def _segments(self, track):
        spec = build_structure_spec([track], StructureStyle(), with_project=False)
        return spec.rows[0].segments

    def test_intro_et_outro_deduites(self):
        segs = self._segments(_ref_track())
        assert [s.kind for s in segs] == [
            "intro_outro",
            "couplet",
            "refrain",
            "pont",
            "couplet",
            "intro_outro",
        ]
        assert (segs[0].start, segs[0].end) == (0.0, 10.0)  # avant la 1ʳᵉ parole
        # L'outro part du DERNIER MOT : dernière ligne à 38 s + sa durée estimée
        # (intervalle médian du LRC = 4 s) → 42 s.
        assert (segs[-1].start, segs[-1].end) == (42.0, 60.0)

    def test_somme_des_ratios_vaut_un(self):
        segs = self._segments(_ref_track())
        assert sum(s.ratio for s in segs) == pytest.approx(1.0)

    def test_fusion_intro_explicite_et_silence(self):
        # Un [Intro] tagué juste après le silence initial → UN seul bloc rouge.
        structured = "[Intro]\nligne d intro\n[Couplet]\nligne de couplet"
        lrc = _lrc((5, "ligne d intro"), (9, "ligne de couplet"), (13, "fin"))
        segs = self._segments(_track(1, "T", 30, structured, lrc))
        assert [s.kind for s in segs] == ["intro_outro", "couplet", "intro_outro"]
        assert (segs[0].start, segs[0].end) == (0.0, 9.0)  # silence + [Intro] fusionnés

    def test_deux_sections_de_meme_type_restent_distinctes(self):
        # Deux couplets d'affilée : PAS de fusion (le rendu pose un filet entre
        # les deux). Idem outro d'une partie + intro de la suivante (2-en-1).
        structured = "\n".join(
            [
                "[Couplet 1]",
                "premiere ligne du couplet",
                "[Couplet 2]",
                "encore une ligne de couplet",
                "[Outro]",
                "la ligne du refrain",
                "[Intro]",
                "la ligne du pont",
            ]
        )
        lrc = _lrc(
            (10, "premiere ligne du couplet"),
            (14, "encore une ligne de couplet"),
            (18, "la ligne du refrain"),
            (22, "la ligne du pont"),
        )
        segs = self._segments(_track(1, "T", 40, structured, lrc))
        assert [s.kind for s in segs] == [
            "intro_outro",  # silence initial
            "couplet",  # Couplet 1
            "couplet",  # Couplet 2 — distinct
            "intro_outro",  # Outro partie 1
            "intro_outro",  # Intro partie 2 + silence final fusionné
        ]

    def test_outro_part_du_dernier_mot(self):
        # Le LRC horodate le DÉBUT d'une ligne : l'outro ne peut pas commencer là,
        # il faut compter la durée de la dernière ligne (médiane bornée = 5 s ici).
        structured = "[Couplet 1]\npremiere ligne du couplet"
        lrc = _lrc((10, "premiere ligne du couplet"), (15, "deux"), (20, "trois"), (25, "fin"))
        segs = self._segments(_track(1, "T", 60, structured, lrc))
        assert segs[-1].kind == "intro_outro"
        assert segs[-1].start == 30.0  # 25 s + 5 s d'intervalle médian

    def test_plage_instrumentale_detectee(self):
        # Solo / interlude non chanté entre deux sections : le silence entre la
        # dernière ligne d'une section et la 1ʳᵉ de la suivante est une plage
        # instrumentale, pas la fin du couplet précédent.
        structured = "[Couplet 1]\nune ligne\nencore une ligne\n[Couplet 2]\nautre ligne\net fin"
        lrc = _lrc((10, "une ligne"), (14, "encore une ligne"), (60, "autre ligne"), (64, "et fin"))
        segs = self._segments(_track(1, "T", 90, structured, lrc))
        assert [s.kind for s in segs] == [
            "intro_outro",  # silence initial
            "couplet",  # Couplet 1 : 10 → 18 (dernière ligne 14 + 4 s)
            "intro_outro",  # PLAGE INSTRUMENTALE 18 → 60
            "couplet",  # Couplet 2
            "intro_outro",  # outro déduite
        ]
        assert (segs[2].start, segs[2].end) == (18.0, 60.0)

    def test_respiration_courte_nest_pas_instrumentale(self):
        # 2 s entre deux sections : une respiration, pas un solo — on prolonge
        # la section plutôt que d'afficher un éclat rouge parasite.
        structured = "[Couplet 1]\nune ligne\nencore une ligne\n[Couplet 2]\nautre ligne\net fin"
        lrc = _lrc((10, "une ligne"), (14, "encore une ligne"), (20, "autre ligne"), (24, "et fin"))
        segs = self._segments(_track(1, "T", 40, structured, lrc))
        assert [s.kind for s in segs] == ["intro_outro", "couplet", "couplet", "intro_outro"]
        assert (segs[1].start, segs[1].end) == (10.0, 20.0)  # prolongé jusqu'au couplet 2

    def test_clamp_sur_la_duree(self):
        # LRC qui dépasse la durée déclarée → aucun segment au-delà.
        lrc = _lrc((10, "premiere ligne du couplet"), (200, "derniere ligne"))
        structured = "[Couplet 1]\npremiere ligne du couplet"
        segs = self._segments(_track(1, "T", 60, structured, lrc))
        assert segs[-1].end == 60.0
        assert sum(s.ratio for s in segs) == pytest.approx(1.0)


# ── Rectangle « durée » (échelle min/max de l'album) ─────────────────────────


class TestDurationWidth:
    def _rows(self, durations):
        tracks = [
            _track(i, f"T{i}", d, _STRUCTURED, _LRC, number=i)
            for i, d in enumerate(durations, start=1)
        ]
        return build_structure_spec(tracks, StructureStyle(), with_project=False).rows

    def test_echelle_min_max(self):
        style = StructureStyle()
        rows = self._rows([120, 180, 240])
        assert rows[0].duration_ratio == 0.0
        assert rows[2].duration_ratio == 1.0
        assert 0.0 < rows[1].duration_ratio < 1.0
        # Le ratio se traduit en largeur via les bornes du style.
        assert rows[0].duration_width(style) == style.duration_width_min
        assert rows[2].duration_width(style) == style.duration_width_max

    def test_duree_unique_pas_de_division_par_zero(self):
        rows = self._rows([120, 120])
        assert {r.duration_ratio for r in rows} == {0.0}

    def test_au_dela_de_cinq_minutes_le_ratio_depasse_un(self):
        # Borne haute plafonnée à 5:00 : un morceau plus long sort de sa colonne
        # et attire l'œil. L'échelle des autres reste calée sur la référence.
        style = StructureStyle()
        rows = self._rows([120, 300, 420])
        assert rows[1].duration_ratio == 1.0  # 5:00 pile = bord de colonne
        assert rows[2].duration_ratio > 1.0  # 7:00 déborde
        assert rows[2].duration_width(style) > style.duration_width_max

    def test_album_entierement_au_dela_de_la_reference(self):
        # Repli sur le min/max pur : sans lui, dmin > ref_max donnerait des ratios
        # négatifs ou une division par un écart nul.
        rows = self._rows([360, 420, 480])
        assert rows[0].duration_ratio == 0.0
        assert rows[2].duration_ratio == 1.0


# ── Répartition verticale ────────────────────────────────────────────────────


class TestLayout:
    def test_album_long_remplit_la_zone(self):
        # 19 morceaux : le plafond ne mord pas, le bloc occupe toute la hauteur
        # utile (110 px de marge haute, 27 px de marge basse).
        style = StructureStyle()
        layout = compute_layout(19, style, with_project=True)
        assert layout.row_height < style.row_height_max
        bas = layout.project_top + layout.row_height
        assert bas == pytest.approx(style.zone_height - style.padding_bottom)
        assert layout.row_tops[0] == style.padding_top

    def test_album_court_ne_remplit_pas_a_tout_prix(self):
        # 10 morceaux : sans plafond les lignes seraient étirées. Le bloc reste
        # calé en haut et laisse de l'espace en bas — c'est voulu.
        style = StructureStyle()
        layout = compute_layout(10, style, with_project=True)
        assert layout.row_height == style.row_height_max
        bas = layout.project_top + layout.row_height
        assert bas < style.zone_height - style.padding_bottom

    def test_ecart_avant_la_ligne_projet(self):
        style = StructureStyle()
        layout = compute_layout(12, style, with_project=True)
        dernier_bas = layout.row_tops[-1] + layout.row_height
        assert layout.project_top - dernier_bas == pytest.approx(style.project_gap)

    def test_sans_ligne_projet(self):
        layout = compute_layout(12, StructureStyle(), with_project=False)
        assert layout.project_top is None


# ── Ligne d'agrégat ──────────────────────────────────────────────────────────


class TestProjectRow:
    def test_ponderation_par_la_duree(self):
        # Deux morceaux identiques en structure mais de durées différentes : les
        # parts de l'agrégat valent celles d'un morceau (mêmes proportions).
        tracks = [_ref_track(number=1), _track(2, "B", 60, _STRUCTURED, _LRC, number=2)]
        spec = build_structure_spec(tracks, StructureStyle())
        project = spec.project_row
        assert project.duration == 120
        assert sum(s.ratio for s in project.segments) == pytest.approx(1.0)
        # Rouge scindé en deux blocs symétriques (début et fin).
        reds = [s for s in project.segments if s.kind == "intro_outro"]
        assert len(reds) == 2
        assert reds[0].ratio == pytest.approx(reds[1].ratio)
        assert project.segments[0].kind == "intro_outro"
        assert project.segments[-1].kind == "intro_outro"

    def test_hors_echelle_des_durees(self):
        # L'agrégat couvre l'album entier : il prend la largeur maximale.
        style = StructureStyle()
        spec = build_structure_spec([_ref_track()], style)
        assert spec.project_row.duration_ratio == 1.0
        assert spec.project_row.duration_width(style) == style.duration_width_max


# ── Validation bloquante ─────────────────────────────────────────────────────


class TestValidation:
    def test_album_inconnu(self):
        with pytest.raises(ValueError, match="Aucun morceau"):
            generate_structure([_ref_track()], "AlbumFantome")

    @pytest.mark.parametrize(
        ("mutate", "motif"),
        [
            (lambda t: setattr(t, "duration", None), "durée manquante"),
            (lambda t: setattr(t.lyrics, "text", None), "paroles manquantes"),
            (lambda t: setattr(t.lyrics, "synced", None), "synchronisées"),
            (lambda t: setattr(t.lyrics, "text", "juste du texte"), "aucune section"),
        ],
    )
    def test_morceau_inexploitable_bloque(self, mutate, motif, tmp_path):
        bad = _track(2, "Bancal", 120, _STRUCTURED, _LRC, number=2)
        mutate(bad)
        with pytest.raises(ValueError) as exc:
            generate_structure(
                [_ref_track(number=1), bad], "TestAlbum", output_path=tmp_path / "s.svg"
            )
        assert "Bancal" in str(exc.value)
        assert motif in str(exc.value)

    def test_sections_non_alignables_bloquent(self, tmp_path):
        bad = _track(2, "Desync", 120, "[Couplet]\nrien qui ne matche le lrc", _lrc((5, "zzz")))
        with pytest.raises(ValueError, match="alignable"):
            generate_structure([bad], "TestAlbum", output_path=tmp_path / "s.svg")


# ── Rendu SVG ────────────────────────────────────────────────────────────────


class TestSvg:
    def _spec(self):
        tracks = [
            _ref_track(number=1, bpm=140),
            _track(2, "Deux", 180, _STRUCTURED, _LRC, number=2, bpm=95),
        ]
        return build_structure_spec(tracks, StructureStyle())

    def test_calques_dans_l_ordre(self):
        root = ET.fromstring(write_structure_svg(self._spec()))
        groups = [g.get("id") for g in root.findall(f"{SVG_NS}g")]
        assert groups == ["durations", "segments", "labels"]

    def test_ids_stables(self):
        svg = write_structure_svg(self._spec())
        for ident in (
            'id="row-1"',
            'id="segment-1-0-intro_outro"',
            'id="duration-1"',
            'id="label-index-1"',
            'id="label-title-1"',
            'id="label-duration-1"',
            'id="row-project"',
        ):
            assert ident in svg

    def test_ligne_projet_sans_rectangle_de_duree(self):
        # La « durée » de l'agrégat est celle de l'album entier, hors de
        # l'échelle des morceaux : un rectangle y serait trompeur.
        svg = write_structure_svg(self._spec())
        assert 'id="duration-project"' not in svg
        assert 'id="label-duration-project"' in svg  # le texte, lui, reste

    def test_pas_de_clippath(self):
        # Illustrator avertit à l'ouverture d'un SVG écrêté (« l'écrêtage sera
        # perdu à la réexportation au format Tiny ») : les bouts de barre sont
        # arrondis par le chemin des segments, pas par un détourage.
        svg = write_structure_svg(self._spec())
        assert "clipPath" not in svg and "clip-path" not in svg

    def test_ligne_projet_sans_numero(self):
        # L'agrégat n'a pas de rang dans la tracklist.
        assert 'id="label-index-project"' not in write_structure_svg(self._spec())

    def test_vide_entre_sections_de_meme_type(self):
        style = StructureStyle()
        structured = "[Couplet 1]\npremiere ligne du couplet\n[Couplet 2]\nla ligne du refrain"
        lrc = _lrc((10, "premiere ligne du couplet"), (14, "la ligne du refrain"), (18, "fin"))
        spec = build_structure_spec(
            [_track(1, "T", 40, structured, lrc, number=1)], style, with_project=False
        )
        bounds = _segment_bounds(spec.rows[0], style)
        assert [seg.kind for seg, _, _ in bounds] == [
            "intro_outro",
            "couplet",
            "couplet",
            "intro_outro",
        ]
        # Frontière rouge→bleu : jointive. Frontière bleu→bleu : écartée du vide.
        assert bounds[1][1] == pytest.approx(bounds[0][2])
        assert bounds[2][1] - bounds[1][2] == pytest.approx(style.separator_gap)

    def test_pas_de_dominant_baseline(self):
        # Attribut ignoré par Illustrator → centrage vertical par offset manuel.
        assert "dominant-baseline" not in write_structure_svg(self._spec())

    def test_segments_couvrent_toute_la_barre(self):
        # Sans frontière de même couleur, les segments pavent exactement la barre.
        spec = self._spec()
        style = spec.style
        bounds = _segment_bounds(spec.rows[0], style)
        assert bounds[0][1] == pytest.approx(style.col_bar_x)
        assert bounds[-1][2] == pytest.approx(style.col_bar_x + style.col_bar_width)
        assert sum(x1 - x0 for _, x0, x1 in bounds) == pytest.approx(style.col_bar_width)

    def test_byte_identite(self):
        spec = self._spec()
        assert write_structure_svg(spec) == write_structure_svg(spec)

    def test_generation_deterministe(self, tmp_path):
        tracks = [_ref_track(number=1), _track(2, "Deux", 180, _STRUCTURED, _LRC, number=2)]
        a = tmp_path / "a.svg"
        b = tmp_path / "b.svg"
        generate_structure(tracks, "TestAlbum", output_path=a)
        generate_structure(list(reversed(tracks)), "TestAlbum", output_path=b)
        assert a.read_bytes() == b.read_bytes()  # le tri rend l'ordre d'entrée indifférent

    def test_result(self, tmp_path):
        out = tmp_path / "structure.svg"
        result = generate_structure([_ref_track(number=1)], "TestAlbum", output_path=out)
        assert result.path == out
        assert result.track_count == 1
        assert result.section_count == 6
        assert result.unaligned_count == 0
        assert result.unnumbered_count == 0
        assert out.exists()

    def test_morceaux_sans_numero_signales(self, tmp_path):
        # Sans n° de piste l'ordre retombe sur l'alphabétique : ce n'est PAS
        # celui de l'album, et rien d'autre ne le signale.
        sans_numero = _ref_track()  # number=None
        result = generate_structure([sans_numero], "TestAlbum", output_path=tmp_path / "s.svg")
        assert result.unnumbered_count == 1

    def test_faux_entete_genius_ignore(self, tmp_path):
        # « [Paroles de "X" : Django] » n'est pas une section : ni segment, ni
        # comptabilisé comme non aligné.
        track = _ref_track(number=1)
        track.lyrics.text = '[Paroles de "Ref" : Django]\n' + track.lyrics.text
        result = generate_structure([track], "TestAlbum", output_path=tmp_path / "s.svg")
        assert result.section_count == 6
        assert result.unaligned_count == 0

    def test_section_non_alignee_comptee(self, tmp_path):
        track = _ref_track(number=1)
        track.lyrics.text = "[Intro]\nligne absente du lrc\n" + track.lyrics.text
        result = generate_structure([track], "TestAlbum", output_path=tmp_path / "s.svg")
        assert result.unaligned_count == 1

    def test_entete_sans_paroles_pas_un_echec(self, tmp_path):
        # En-tête de regroupement d'un 2-en-1 / « [Outro Instrumentale] » : aucune
        # parole à ancrer, ce n'est pas un alignement raté.
        track = _ref_track(number=1)
        track.lyrics.text = "[Partie 1 : Untel]\n" + track.lyrics.text + "\n[Outro Instrumentale]\n"
        result = generate_structure([track], "TestAlbum", output_path=tmp_path / "s.svg")
        assert result.unaligned_count == 0


class TestPayloadJson:
    """Le JSON est ce que consomme le script Illustrator — sa forme est un contrat."""

    def _payload(self):
        tracks = [
            _track(1, "Fiesta (Interlude)", 60, _STRUCTURED, _LRC, number=1),
            _track(2, "POP", 90, _STRUCTURED, _LRC, number=2),
        ]
        tracks[1].featured_artists = "Guy2Bezbar, Laylow"
        spec = build_structure_spec(tracks, StructureStyle())
        return build_payload(spec, artist_name="Josman", album="M.A.N")

    def test_forme_generale(self):
        payload = self._payload()
        assert payload["version"] == PAYLOAD_VERSION
        assert payload["artist"] == "Josman" and payload["album"] == "M.A.N"
        assert len(payload["rows"]) == 2
        assert payload["project_row"]["title"] == "Structure du Projet"

    def test_titre_parenthese_et_invites(self):
        rows = self._payload()["rows"]
        assert rows[0]["title"] == "Fiesta" and rows[0]["title_paren"] == "(Interlude)"
        assert rows[0]["feats"] == []
        assert rows[1]["title_paren"] is None
        assert rows[1]["feats"] == ["Guy2Bezbar", "Laylow"]

    def test_ratios_et_duree_lisible(self):
        rows = self._payload()["rows"]
        assert rows[0]["duration"] == "1:00" and rows[0]["duration_seconds"] == 60
        assert sum(s["ratio"] for s in rows[0]["segments"]) == pytest.approx(1.0)

    def test_style_sans_fond_de_previsualisation(self):
        # `background_fill` n'existe que pour le SVG : Illustrator a le vrai fond.
        style = self._payload()["style"]
        assert "background_fill" not in style
        assert style["colors"]["intro_outro"] == "#a93f3b"
        assert style["font_semibold"] == "Montserrat-SemiBold"

    def test_byte_identite(self):
        payload = self._payload()
        assert write_structure_json(payload, None) == write_structure_json(payload, None)

    def test_ecriture_a_cote_du_svg(self, tmp_path):
        out = tmp_path / "structure.svg"
        result = generate_structure([_ref_track(number=1)], "TestAlbum", output_path=out)
        assert result.json_path == tmp_path / "structure.json"
        assert json.loads(result.json_path.read_text(encoding="utf-8"))["rows"][0]["index"] == 1


class TestCleanDisplayTitle:
    def test_retire_les_barres_decoratives(self):
        # Genius laisse parfois des barres combinantes : « F̶i̶e̶s̶t̶a̶ ».
        assert clean_display_title("F̶i̶e̶s̶t̶a̶") == "Fiesta"

    def test_preserve_les_accents(self):
        assert clean_display_title("Brûle") == "Brûle"
        assert clean_display_title("L’Œil de la Joconde") == "L’Œil de la Joconde"


class TestSplitTitleParen:
    @pytest.mark.parametrize(
        ("titre", "attendu"),
        [
            ("Fiesta (Interlude)", ("Fiesta", "(Interlude)")),
            ("Outro (Labrador bleu)", ("Outro", "(Labrador bleu)")),
            ("McQueen / Givenchy", ("McQueen / Givenchy", None)),
            ("", ("", None)),
            # Parenthèse INTERNE : on ne coupe que celle de fin.
            ("3ein (part 1) / Risotto", ("3ein (part 1) / Risotto", None)),
            # Titre entièrement parenthésé : pas de « tête » à isoler.
            ("(Interlude)", ("(Interlude)", None)),
        ],
    )
    def test_split(self, titre, attendu):
        assert split_title_paren(titre) == attendu


class TestFormatDuration:
    def test_minutes_secondes(self):
        assert format_duration(147) == "2:27"
        assert format_duration(60) == "1:00"

    def test_pas_de_bascule_en_heures(self):
        # La ligne d'agrégat dépasse l'heure mais se lit en minutes : c'est une
        # durée de projet, pas une horloge.
        assert format_duration(3788) == "63:08"
        assert format_duration(3720) == "62:00"


class TestStyleOverrides:
    """Le fichier de réglages : c'est LUI qui persiste, pas le structure.json."""

    def test_cree_le_fichier_avec_les_defauts(self, tmp_path):
        path = tmp_path / "structure_style.json"
        style = load_style(path)
        assert path.exists()
        assert style == StructureStyle()
        # Le fichier est ANNOTÉ : il passe par `strip_comments` avant json.
        payload = json.loads(strip_comments(path.read_text(encoding="utf-8")))
        assert payload["row_height_max"] == StructureStyle().row_height_max
        assert payload["colors"]["intro_outro"] == "#a93f3b"

    def test_surcharge_appliquee(self, tmp_path):
        path = tmp_path / "structure_style.json"
        path.write_text(
            json.dumps({"row_height_max": 38.5, "colors": {"pont": "#ff00ff"}}),
            encoding="utf-8",
        )
        style = load_style(path)
        assert style.row_height_max == 38.5
        assert style.colors["pont"] == "#ff00ff"
        # Fichier PARTIEL : le reste garde ses défauts (fusion, pas remplacement).
        assert style.colors["intro_outro"] == "#a93f3b"
        assert style.gap_ratio == StructureStyle().gap_ratio

    def test_cle_inconnue_ignoree(self, tmp_path):
        # Une faute de frappe ne doit pas faire échouer tout l'export.
        path = tmp_path / "structure_style.json"
        path.write_text(json.dumps({"cle_bidon": 1, "gap_ratio": 0.4}), encoding="utf-8")
        assert load_style(path).gap_ratio == 0.4

    def test_json_casse_retombe_sur_les_defauts(self, tmp_path):
        path = tmp_path / "structure_style.json"
        path.write_text("{ pas du json", encoding="utf-8")
        assert load_style(path) == StructureStyle()


class TestStyleComments:
    """Le fichier de réglages est annoté : JSON n'admet pas les commentaires,
    on les tolère et on les retire à la lecture."""

    def test_fichier_cree_est_commente(self, tmp_path):
        path = tmp_path / "structure_style.json"
        load_style(path)
        text = path.read_text(encoding="utf-8")
        assert text.lstrip().startswith("//")
        # Une ligne d'explication au-dessus de CHAQUE réglage.
        for key in default_payload():
            assert f'"{key}"' in text
        assert "NE MODIFIE PAS le structure.json" in text

    def test_relecture_du_fichier_commente(self, tmp_path):
        # Aller-retour : ce qui est écrit doit se relire aux valeurs par défaut.
        path = tmp_path / "structure_style.json"
        assert load_style(path) == StructureStyle()
        assert load_style(path) == StructureStyle()  # 2e passe : lecture réelle

    def test_valeur_editee_dans_un_fichier_commente(self, tmp_path):
        path = tmp_path / "structure_style.json"
        load_style(path)
        text = path.read_text(encoding="utf-8").replace('"gap_ratio": 0.25', '"gap_ratio": 0.4')
        path.write_text(text, encoding="utf-8")
        assert load_style(path).gap_ratio == 0.4

    def test_commentaire_ajoute_a_la_main(self, tmp_path):
        path = tmp_path / "structure_style.json"
        path.write_text(
            '// mon commentaire\n{\n  // encore un\n  "gap_ratio": 0.3\n}\n', encoding="utf-8"
        )
        assert load_style(path).gap_ratio == 0.3

    def test_slash_dans_une_valeur_preserve(self):
        # On ne retire QUE les lignes entièrement commentées : un « // » à
        # l'intérieur d'une chaîne ne doit pas être amputé.
        text = '{\n  "font_family": "a//b"\n}'
        assert json.loads(strip_comments(text))["font_family"] == "a//b"
