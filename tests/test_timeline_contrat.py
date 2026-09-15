"""Comportements VALIDÉS à l'œil sur Isha le 2026-09-15, gelés ici.

Ce fichier ne teste pas des briques, il gèle des décisions : le contrat que
`timeline.jsx` lit dans `timeline.json`, la forme de la courbe (décalage,
lissage, bord de cadre), les libellés repliés jusqu'au SVG, ce qui NE part PAS
dans le JSON parce que fixe dans le template, et l'organisation de la fenêtre.
Si l'un d'eux casse, ce n'est pas une régression de code, c'est une décision
qui change — et elle doit être prise, pas subie.
"""

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from src.dataviz import timeline as tl
from src.dataviz.timeline import (
    EntryChoice,
    build_candidates,
    build_timeline_spec,
    default_selection,
    generate_timeline,
)
from src.dataviz.timeline_json import build_payload
from src.dataviz.timeline_svg import TimelineStyle, write_timeline_svg
from tests.test_timeline import _corpus, _track

_JSX = Path(__file__).resolve().parent.parent / "scripts" / "illustrator" / "timeline.jsx"


def _spec(tracks=None, **style_kwargs):
    tracks = tracks or _corpus()
    cands = build_candidates(tracks)
    entries = tl._entries_from_defaults(cands, default_selection(cands, 12))
    return build_timeline_spec(tracks, entries, TimelineStyle(**style_kwargs), candidates=cands)


# ── Contrat JSON ↔ JSX ───────────────────────────────────────────────────────


@pytest.mark.skipif(not _JSX.exists(), reason="timeline.jsx est hors dépôt (local seulement)")
def test_jsx_reads_only_fields_the_json_produces():
    """Chaque `page.x`, `point.x`, `style.x`, `cert.x`, sommet `v.x` lu par le
    script existe dans le payload : un champ renommé côté Python casserait le
    JSX en silence (ES3 rend `undefined`, pas d'erreur)."""
    src = _JSX.read_text(encoding="utf-8")
    payload = build_payload(_spec(), artist_name="Test")
    page = payload["pages"][0]
    point = page["points"][0]
    cert_point = next(p for pg in payload["pages"] for p in pg["points"] if p["cert"] is not None)
    vertex = page["curve"][0]

    def fields(prefix):
        # `\b` : « point. » ne doit pas attraper « midpoint. » ; les chiffres
        # font partie du nom (« line1 »). `inside(box, point)` prend un OBJET
        # géométrique nommé `point` : ses `cx`/`cy` ne sont pas des champs JSON.
        found = set(re.findall(r"\b" + prefix + r"\.([a-z_0-9]+)", src))
        return found - {"cx", "cy"}

    assert fields("ctx\\.style") <= set(payload["style"]), fields("ctx\\.style") - set(
        payload["style"]
    )
    assert fields("page") - {"points", "curve", "index", "background", "cartouche"} <= set(page)
    assert fields("point") - {"cert"} <= set(point)
    assert fields("point\\.cert") <= set(cert_point["cert"])
    assert fields("page\\.background") <= set(page["background"])
    assert fields("page\\.cartouche") <= set(page["cartouche"])
    for name in ("v", "cur", "prev", "next"):
        assert fields(name) <= set(vertex), name
    # Symboles : « certif-<organisme>-<palier> », l'organisme en minuscules.
    assert '"certif-" + body + "-" + point.cert.palier' in src


def test_json_never_carries_template_fixed_keys():
    """Ligne, pastilles et forme du cartouche sont FIXES dans le template :
    absents du JSON (décision utilisateur). Ce que le JSX pose, lui, y est."""
    style = build_payload(_spec(), artist_name="Test")["style"]
    for fixed in (
        "line_y",
        "line_width",
        "line_stroke",
        "dot_diameter",
        "dot_fill",
        "cartouche_x",
        "cartouche_width",
        "cartouche_fill",
        "background_opacity",
        "page_gap",
        "zone_x",
        "zone_width",
        "disc_diameter",
    ):
        assert fixed not in style, fixed
    for needed in (
        "cover_large",
        "cover_small",
        "cover_offset",
        "cover_stroke",
        "cover_stroke_width",
        "cover_radius",
        "disc_overlap",
        "disc_step",
        "disc_fill",
        "curve_lag",
        "size_label",
        "size_year",
        "font_light",
        "font_semibold",
        "cartouche_lines",
        "under_curve_fill",
    ):
        assert needed in style, needed


# ── Courbe ───────────────────────────────────────────────────────────────────


def test_curve_contract_end_to_end(tmp_path):
    """Sur un vrai flux : chaque page part du bord gauche et finit au bord droit,
    les sommets sont décalés de `curve_lag`, la jonction entre pages est
    continue (même ratio, même pente), la dernière page finit à 1.0 et la
    courbe ne redescend jamais."""
    out = tmp_path / "t.svg"
    result = generate_timeline(_corpus(), artist_name="Test", pages=3, output_path=out)
    payload = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    lag = payload["style"]["curve_lag"]
    width = payload["style"]["page_width"]
    pages = payload["pages"]
    for page in pages:
        curve = page["curve"]
        assert curve[0]["x_ratio"] == 0.0 and curve[-1]["x_ratio"] == 1.0
        xs = [v["x_ratio"] for v in curve]
        assert xs == sorted(xs)
        assert all(v["slope"] >= 0 for v in curve)
        for v, point in zip(curve[1:], page["points"], strict=False):
            if v["x_ratio"] < 1.0:
                expected = (tl.slot_x(point["slot"], TimelineStyle()) + lag) / width
                assert abs(v["x_ratio"] - expected) < 1e-5
    for prev, nxt in zip(pages, pages[1:], strict=False):
        assert prev["curve"][-1]["ratio"] == nxt["curve"][0]["ratio"]
        assert prev["curve"][-1]["slope"] == nxt["curve"][0]["slope"]
    assert pages[-1]["curve"][-1]["ratio"] == 1.0
    assert len(pages[-1]["curve"]) == len(pages[0]["curve"]) - 1
    assert result.spec.pages[0].entry_ratio == 0.0


def test_curve_lag_is_clamped_inside_the_page():
    spec = _spec(curve_lag=10_000)
    for page in spec.pages:
        assert all(x <= TimelineStyle().page_width for x, _r, _m in page.curve)
    assert tl._curve_lag(TimelineStyle(curve_lag=-5)) == 0.0


def test_curve_is_bezier_in_svg_and_under_area_follows_it():
    svg = write_timeline_svg(_spec())
    root = ET.fromstring(svg)
    ns = {"svg": "http://www.w3.org/2000/svg"}
    for page in root.findall("svg:g", ns):
        under, line = page.findall(f"svg:g[@id='curve-{page.get('id')[5:]}']/svg:path", ns)
        assert " C" in line.get("d") and "L" not in line.get("d")
        assert under.get("d").startswith(line.get("d"))
        assert under.get("style") == "mix-blend-mode:hard-light"


# ── Libellés ─────────────────────────────────────────────────────────────────


def test_multiline_label_reaches_svg_and_json(tmp_path):
    tracks = _corpus()
    cands = build_candidates(tracks)
    entries = tl._entries_from_defaults(cands, default_selection(cands, 12))
    entries[0] = EntryChoice(
        entries[0].key, "Album", "**Faites pas chier,**\\n**je prépare un album**", "grand"
    )
    spec = build_timeline_spec(tracks, entries, candidates=cands)
    point = spec.pages[0].points[0]
    assert len(point.line2) == 2
    svg = write_timeline_svg(spec)
    assert 'id="label2-1-0-1"' in svg and 'id="label2-1-0-2"' in svg
    payload = build_payload(spec, artist_name="T")
    assert payload["pages"][0]["points"][0]["line2"] == [
        [{"text": "Faites pas chier,", "bold": True}],
        [{"text": "je prépare un album", "bold": True}],
    ]


def test_labels_above_stack_away_from_the_line():
    """Au-dessus de la ligne, le bloc de libellé grandit vers le HAUT : la
    dernière ligne reste collée à la pochette, jamais à la ligne de temps."""
    tracks = _corpus()
    cands = build_candidates(tracks)
    entries = tl._entries_from_defaults(cands, default_selection(cands, 12))
    entries[0] = EntryChoice(entries[0].key, "a\\nb\\nc", "**d**", "grand")
    spec = build_timeline_spec(tracks, entries, candidates=cands)
    root = ET.fromstring(write_timeline_svg(spec))
    ns = {"svg": "http://www.w3.org/2000/svg"}
    ys = [
        float(t.get("y"))
        for t in root.findall(".//svg:g[@id='labels-1']/svg:text", ns)
        if t.get("id").endswith("-0-0")
        or t.get("id").startswith("label1-1-0")
        or t.get("id").startswith("label2-1-0")
    ]
    style = spec.style
    cover_top = style.line_y - style.cover_offset - style.cover_large
    assert max(ys) <= cover_top  # tout le bloc est au-dessus de la pochette
    assert ys == sorted(ys)


# ── Sélection ────────────────────────────────────────────────────────────────


def test_default_selection_fills_the_pages_when_there_is_matter():
    tracks = _corpus() + [
        _track(200 + i, f"Hit {i}", None, f"2021-0{1 + i % 9}-01", 3_000_000 - i, 1)
        for i in range(10)
    ]
    cands = build_candidates(tracks)
    assert tl.default_page_count(cands) == 4
    keys = default_selection(cands, 16)
    assert len(keys) == 16
    by_key = {c.key: c for c in cands}
    assert [by_key[k].date for k in keys] == sorted(by_key[k].date for k in keys)
    assert all(
        by_key[k].kind != "track" or not by_key[k].is_freestyle or by_key[k].streams > 0
        for k in keys
    )


def test_freestyle_specific_keyword_beats_generic():
    assert tl.is_freestyle(_track(1, "Freestyle Skyrock")) == (True, "Skyrock")
    assert tl.is_freestyle(_track(1, "Grünt #55", "Grünt")) == (True, "Grünt")


# ── Fenêtre Export studio ────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def racine():
    ctk = pytest.importorskip("customtkinter")
    try:
        root = ctk.CTk()
    except Exception as exc:  # noqa: BLE001 — pas d'affichage (CI headless)
        pytest.skip(f"aucun affichage disponible : {exc!r}")
    root.withdraw()
    yield root
    root.destroy()


def test_export_studio_layout(racine, monkeypatch, tmp_path):
    """4 onglets, l'export JSON brut isolé, images + Export Illustrator en bas
    de l'onglet Timeline, Mémoriser + Aperçu au-dessus (décisions 2026-09-15)."""
    from src.dataviz import timeline_overrides_io as ovr
    from src.gui.windows.export_studio import ExportStudioWindow
    from src.models.artist import Artist

    monkeypatch.setattr(ovr, "overrides_path", lambda: tmp_path / "o.json")
    monkeypatch.setattr(tl, "resolve_cover", lambda rel: None)

    class App:
        root = racine
        current_artist = Artist(id=7, name="Isha", tracks=_corpus())
        export_studio_window = None

        def _is_track_disabled(self, track):
            return False

        def _export_data(self):
            pass

    window = ExportStudioWindow(App())
    try:
        assert window.tabview._name_list == [
            "Analyse de Projet",
            "Timeline",
            "Stats en Vrac",
            "Export Brut",
        ]
        panel = window.timeline_panel
        assert window.media_button is panel.media_button
        assert panel.generate_button.cget("text") == "Export Illustrator"
        assert panel.media_button.master is panel.generate_button.master
        assert panel.save_button.master is panel.preview_button.master
        assert panel.save_button.master is not panel.generate_button.master
        brut = window.tabview.tab("Export Brut")
        texts = [w.cget("text") for w in brut.winfo_children() if hasattr(w, "cget")]
        assert any("Export JSON" in t for t in texts)
    finally:
        window.window.destroy()
