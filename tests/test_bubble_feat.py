"""Smoke test du pipeline Bubble Feat (même moteur que Bubble Prod, rôle feat).

Vérifie les invariants propres au feat : filtre `FEAT_ROLES` (les crédits
producteurs sont ignorés, l'artiste principal n'apparaît pas), structure SVG
(un carré par invité, une ellipse par combinaison), nom de fichier par défaut
`bubble_feat.svg`, grille d'aperçus `bubble_feat_seed<N>.svg` dans
`apercus_feat/`, byte-identité, et le `ValueError` explicite (« featuring »)
quand l'album n'a aucun feat. La géométrie et le rendu détaillés sont couverts
par les tests Bubble Prod (moteur partagé).
"""

import xml.etree.ElementTree as ET

import pytest

from src.dataviz.bubble_feat import generate_bubble_feat, generate_feat_preview_grid
from src.dataviz.collab_graph import FEAT_ROLES, extract_track_groups
from src.models.track import Credit, CreditRole, Track

SVG_NS = "{http://www.w3.org/2000/svg}"


def _feat(name):
    return Credit(name=name, role=CreditRole.FEATURED)


def _prod(name):
    return Credit(name=name, role=CreditRole.PRODUCER)


def _track(tid, title, album, *credits):
    t = Track(id=tid, title=title, album=album)
    t.credits = list(credits)
    return t


def _album_tracks():
    # Comptes contrôlés : Damso=2, Nekfeu=1, Alpha Wann=1 ; T4 sans feat
    # (crédit producteur seulement) → hors graphe.
    return [
        _track(1, "T1", "TestAlbum", _feat("Damso"), _feat("Nekfeu"), _prod("Kalim")),
        _track(2, "T2", "TestAlbum", _feat("Damso"), _prod("Kalim")),
        _track(3, "T3", "TestAlbum", _feat("Alpha Wann")),
        _track(4, "T4", "TestAlbum", _prod("Kalim")),
    ]


# ── Filtre de rôles ──────────────────────────────────────────────────────────


def test_filtre_feat_ignore_les_producteurs():
    groups = extract_track_groups(_album_tracks(), FEAT_ROLES)
    assert len(groups) == 3  # T4 (producteur seul) ne produit pas de groupe
    keys = {k for g in groups for k in g.keys}
    assert keys == {"damso", "nekfeu", "alpha wann"}  # jamais « kalim »


# ── Structure du SVG ─────────────────────────────────────────────────────────


def test_smoke_structure(tmp_path):
    out = tmp_path / "bubble.svg"
    res = generate_bubble_feat(_album_tracks(), "TestAlbum", output_path=out)
    assert res.node_count == 3
    assert res.track_count == 3  # morceaux AVEC feat (T4 exclu)

    root = ET.parse(out).getroot()
    groups = {g.get("id"): g for g in root.findall(f"{SVG_NS}g")}
    rects = groups["squares"].findall(f"{SVG_NS}rect")
    assert len(rects) == 3  # un carré par artiste invité

    # 3 combinaisons distinctes : {damso, nekfeu}, {damso}, {alpha wann}.
    ellipses = groups["ellipses"].findall(f"{SVG_NS}ellipse")
    assert len(ellipses) == 3

    ids = {el.get("id") for el in root.iter()}
    assert "square-damso" in ids
    assert "ellipse-damso--nekfeu" in ids
    assert "ellipse-alpha-wann" in ids

    badge = next(el for el in root.iter() if el.get("id") == "badge-count-damso")
    assert badge.text == "2"


def test_nom_de_fichier_par_defaut(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.EXPORTS_DIR", str(tmp_path))
    res = generate_bubble_feat(_album_tracks(), "TestAlbum", artist_name="A")
    assert res.path == tmp_path / "A" / "TestAlbum" / "bubble_feat.svg"
    assert res.path.exists()


# ── Déterminisme ─────────────────────────────────────────────────────────────


def test_deterministe_byte_identique(tmp_path):
    a = tmp_path / "a.svg"
    b = tmp_path / "b.svg"
    generate_bubble_feat(_album_tracks(), "TestAlbum", output_path=a)
    generate_bubble_feat(_album_tracks(), "TestAlbum", output_path=b)
    assert a.read_bytes() == b.read_bytes()


# ── Grille d'aperçus (variantes de seed) ─────────────────────────────────────


def test_preview_grid_feat(tmp_path):
    html_path = generate_feat_preview_grid(
        _album_tracks(), "TestAlbum", output_dir=tmp_path, seeds=(42, 7)
    )
    assert html_path.exists()
    html = html_path.read_text(encoding="utf-8")
    assert "Bubble Feat — TestAlbum" in html
    assert 'src="bubble_feat_seed42.svg"' in html
    assert 'src="bubble_feat_seed7.svg"' in html
    for seed in (42, 7):
        svg = tmp_path / f"bubble_feat_seed{seed}.svg"
        assert svg.exists()
        ET.parse(svg)  # parse sans erreur


def test_preview_grid_feat_dossier_par_defaut(tmp_path, monkeypatch):
    # Sans output_dir : les aperçus feat vivent dans <album>/apercus_feat/
    # (ne PAS partager <album>/apercus/ : l'apercus.html prod y serait écrasé).
    monkeypatch.setattr("src.config.EXPORTS_DIR", str(tmp_path))
    html_path = generate_feat_preview_grid(
        _album_tracks(), "TestAlbum", artist_name="A", seeds=(42,)
    )
    assert html_path == tmp_path / "A" / "TestAlbum" / "apercus_feat" / "apercus.html"


# ── Erreurs ──────────────────────────────────────────────────────────────────


def test_aucun_feat_leve_valueerror():
    tracks = [_track(1, "A", "Al", _prod("Kalim"))]
    with pytest.raises(ValueError, match="featuring"):
        generate_bubble_feat(tracks, "Al", output_path="unused.svg")
