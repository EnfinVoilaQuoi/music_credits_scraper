"""Payload `bubble_<kind>.json` + fichier de réglages `data/bubble_style.json`.

Vérifie ce dont dépend la planche Illustrator : structure et déterminisme du
payload, cohérence avec le SVG (mêmes coordonnées, mêmes découpages de noms),
repli sans photo, et le contrat du fichier de réglages (création annotée,
surcharge partielle, clé inconnue ignorée, commentaires tolérés).
"""

import json

import pytest

from src.dataviz.bubble_json import PAYLOAD_VERSION, build_payload, write_bubble_json
from src.dataviz.bubble_prod import build_bubble_spec, generate_bubble_prod
from src.dataviz.bubble_style_io import default_payload, load_style, write_default_style
from src.dataviz.bubble_svg import SvgStyle
from src.dataviz.collab_graph import (
    aggregate_collab_groups,
    build_collab_graph,
    extract_track_groups,
)
from src.dataviz.style_io import strip_comments
from src.models.track import Credit, CreditRole, Track


@pytest.fixture(autouse=True)
def _sans_photo_sur_disque(monkeypatch):
    """Isole les tests du dossier RÉEL `data/images/artistes/`.

    `bubble_json` cherche la photo de chaque producteur sur le disque. Les noms
    utilisés ici (« Kalim », « Lewis Amber ») sont de vrais producteurs : le jour
    où l'app a téléchargé leurs photos (2026-09-03), deux tests sont passés au
    rouge sur cette machine alors qu'ils restaient verts sur un clone frais.
    Un test ne doit pas dépendre de ce que l'utilisateur a enrichi entre-temps.
    """
    monkeypatch.setattr("src.dataviz.bubble_json.find_artist_image", lambda name: None)


def _prod(name):
    return Credit(name=name, role=CreditRole.PRODUCER)


def _track(tid, title, album, *credits):
    t = Track(id=tid, title=title, album=album)
    t.credits = list(credits)
    return t


def _album_tracks():
    return [
        _track(1, "T1", "TestAlbum", _prod("Kalim"), _prod("Big")),
        _track(2, "T2", "TestAlbum", _prod("Kalim"), _prod("Big")),
        _track(3, "T3", "TestAlbum", _prod("Big"), _prod("Lewis Amber")),
        _track(4, "T4", "TestAlbum", _prod("Solo")),
    ]


def _payload(tracks=None, style=None):
    tracks = tracks or _album_tracks()
    track_groups = extract_track_groups(tracks)
    spec = build_bubble_spec(
        build_collab_graph(track_groups), aggregate_collab_groups(track_groups), style
    )
    return build_payload(spec, kind="prod", artist_name="Artiste", album="TestAlbum", seed=42)


# ── Structure du payload ─────────────────────────────────────────────────────


def test_structure_du_payload():
    p = _payload()
    assert p["version"] == PAYLOAD_VERSION
    assert p["kind"] == "prod"
    assert (p["artist"], p["album"], p["seed"]) == ("Artiste", "TestAlbum", 42)
    # La zone du payload est celle du style — pas une cote en dur ici : elle
    # bouge avec la maquette, et le JSX s'en sert comme référence d'échelle.
    assert p["zone"] == {"width": SvgStyle().frame_width, "height": SvgStyle().frame_height}
    assert len(p["nodes"]) == 4
    assert len(p["groups"]) == 3  # {big,kalim}, {big,lewis amber}, {solo}
    assert {n["id"] for n in p["nodes"]} == {"big", "kalim", "lewis-amber", "solo"}

    node = next(n for n in p["nodes"] if n["id"] == "kalim")
    assert node["track_count"] == 2
    assert node["name"] == "Kalim"
    assert node["name_lines"] == ["KALIM"]  # majuscules par défaut


def test_coordonnees_identiques_au_svg():
    # Le JSON et le SVG doivent décrire le MÊME dessin : c'est ce qui permet de
    # contrôler une planche sur l'aperçu avant de la monter dans Illustrator.
    tracks = _album_tracks()
    track_groups = extract_track_groups(tracks)
    spec = build_bubble_spec(
        build_collab_graph(track_groups), aggregate_collab_groups(track_groups)
    )
    p = build_payload(spec, kind="prod", artist_name="A", album="TestAlbum", seed=42)
    for node, payload_node in zip(spec.nodes, p["nodes"], strict=True):
        assert payload_node["x"] == pytest.approx(node.x, abs=0.01)
        assert payload_node["y"] == pytest.approx(node.y, abs=0.01)
        assert payload_node["diameter"] == pytest.approx(node.size, abs=0.01)
        assert payload_node["font_size"] == pytest.approx(node.label_font_size, abs=0.01)


def test_nom_multi_mots_decoupe_comme_dans_le_svg():
    node = next(n for n in _payload()["nodes"] if n["id"] == "lewis-amber")
    assert node["name_lines"] == ["LEWIS", "AMBER"]


def test_sans_photo_le_payload_ne_ment_pas():
    # Aucune photo en base de test : le JSX doit recevoir null et poser un
    # cercle plein, jamais un chemin qui n'existe pas.
    p = _payload()
    assert all(n["image"] is None and n["image_abs"] is None for n in p["nodes"])


def test_aretes_absentes_quand_non_dessinees():
    assert _payload()["edges"] == []
    avec = _payload(style=SvgStyle(draw_edges=True))
    # 2 arêtes : big↔kalim et big↔lewis amber (« Solo » n'a pas de voisin).
    assert len(avec["edges"]) == 2


def test_debordement_transporte():
    tracks = [_track(i, f"T{i}", "Al", _prod("Hub"), _prod(f"P{i}")) for i in range(1, 13)]
    p = _payload(tracks, style=SvgStyle(frame_width=200.0, frame_height=120.0))
    assert p["overflow"] is not None
    assert len(p["overflow"]) == 2


def test_deterministe_byte_identique(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    write_bubble_json(_payload(), a)
    write_bubble_json(_payload(), b)
    assert a.read_bytes() == b.read_bytes()


def test_generate_ecrit_les_deux_fichiers(tmp_path):
    out = tmp_path / "bubble_prod.svg"
    result = generate_bubble_prod(_album_tracks(), "TestAlbum", output_path=out)
    assert result.path.exists()
    assert result.json_path == tmp_path / "bubble_prod.json"
    assert json.loads(result.json_path.read_text(encoding="utf-8"))["kind"] == "prod"
    # Sans photo sur disque, tous les producteurs sont signalés à l'appelant.
    assert len(result.missing_images) == 4


# ── Fichier de réglages ──────────────────────────────────────────────────────


def test_fichier_cree_annote_et_relisible(tmp_path):
    path = tmp_path / "bubble_style.json"
    style = load_style(path)  # crée le fichier
    assert path.exists()
    assert style == SvgStyle()
    text = path.read_text(encoding="utf-8")
    assert text.lstrip().startswith("//")  # annoté
    assert json.loads(strip_comments(text)) == json.loads(json.dumps(default_payload()))


def test_surcharge_partielle(tmp_path):
    path = tmp_path / "bubble_style.json"
    path.write_text('{"node_size_max": 200}', encoding="utf-8")
    style = load_style(path)
    assert style.node_size_max == 200
    assert style.node_size_min == SvgStyle().node_size_min  # le reste inchangé


def test_cle_inconnue_ignoree(tmp_path):
    path = tmp_path / "bubble_style.json"
    path.write_text('{"node_size_min": 60, "coucou": 1}', encoding="utf-8")
    assert load_style(path).node_size_min == 60


def test_champ_verrouille_non_surchargeable(tmp_path):
    # `coord_precision` fixe la byte-identité : le laisser régler ouvrirait la
    # porte à des sorties qui diffèrent d'un run à l'autre.
    path = tmp_path / "bubble_style.json"
    path.write_text('{"coord_precision": 6}', encoding="utf-8")
    assert load_style(path).coord_precision == SvgStyle().coord_precision


def test_fichier_illisible_retombe_sur_les_defauts(tmp_path):
    path = tmp_path / "bubble_style.json"
    path.write_text("{ pas du json", encoding="utf-8")
    assert load_style(path) == SvgStyle()


def test_commentaire_seulement_en_ligne_entiere(tmp_path):
    # Un `//` DANS une valeur n'est pas un commentaire : le retirer casserait
    # les couleurs et les noms de police.
    path = tmp_path / "bubble_style.json"
    write_default_style(path)
    text = path.read_text(encoding="utf-8").replace(
        '"font_bold": "Montserrat-Bold"', '"font_bold": "a//b"'
    )
    path.write_text(text, encoding="utf-8")
    assert load_style(path).font_bold == "a//b"
