"""Tests du rendu « Timeline » : briques pures, SVG, JSON, fichier de réglages."""

import json
import xml.etree.ElementTree as ET

from src.dataviz.style_io import strip_comments
from src.dataviz.timeline_json import build_payload, write_timeline_json
from src.dataviz.timeline_style_io import load_style, style_path, write_default_style
from src.dataviz.timeline_svg import (
    CertSpec,
    PageSpec,
    PointSpec,
    TextRun,
    TimelineSpec,
    TimelineStyle,
    background_point,
    cover_box,
    curve_y,
    format_streams_short,
    parse_marked,
    parse_marked_lines,
    plain_text,
    slot_x,
    write_timeline_svg,
)

_NS = {"svg": "http://www.w3.org/2000/svg"}


# ── 9. Briques pures ─────────────────────────────────────────────────────────


def test_parse_marked():
    assert parse_marked("Feat avec **Scylla**") == (
        TextRun("Feat avec ", False),
        TextRun("Scylla", True),
    )
    assert parse_marked("**a** b **c**") == (
        TextRun("a", True),
        TextRun(" b ", False),
        TextRun("c", True),
    )
    assert parse_marked("orphelin ** ici") == (TextRun("orphelin ** ici", False),)
    assert parse_marked("") == ()
    assert plain_text(parse_marked("Feat avec **Scylla**")) == "Feat avec Scylla"


def test_parse_marked_lines():
    """`\\n` tapé dans un champ ET vrai retour replient ; lignes vides omises."""
    assert parse_marked_lines("**Faites pas chier,**\\n**je prépare un album**") == (
        (TextRun("Faites pas chier,", True),),
        (TextRun("je prépare un album", True),),
    )
    assert parse_marked_lines("a\n\nb ") == ((TextRun("a", False),), (TextRun("b", False),))
    assert parse_marked_lines("") == ()


def test_format_streams_short():
    assert format_streams_short(175_400_000) == "175 M"
    assert format_streams_short(4_600_000) == "5 M"
    assert format_streams_short(850_000) == "850 k"
    assert format_streams_short(999) == "999"
    assert format_streams_short(0) == "0"


def test_slot_and_curve_geometry():
    style = TimelineStyle(zone_x=100, zone_width=800, curve_top=100, curve_bottom=1100)
    assert [slot_x(i, style) for i in range(4)] == [200.0, 400.0, 600.0, 800.0]
    assert curve_y(0.0, style) == 1100 and curve_y(1.0, style) == 100
    assert curve_y(0.5, style) == 600


def _point(i, *, above, size="grand", cert=None, cover_abs=None, year=None, x=None, kind="track"):
    style = TimelineStyle()
    return PointSpec(
        key=f"{kind}:{i}",
        kind=kind,
        date=f"202{i}-01-01",
        year_label=year,
        line1=parse_marked_lines("Feat avec **X**"),
        line2=parse_marked_lines("**Titre**\\n**suite**" if i == 0 else "**Titre**"),
        size=size,
        above=above,
        cover=None,
        cover_abs=cover_abs,
        cert=cert,
        disc_side="right" if i % 4 < 2 else "left",
        streams=1000 * (8 - i),
        cumul=(i + 1) * 1000,
        ratio=(i + 1) / 8,
        x=x if x is not None else slot_x(i % 4, style),
    )


def _spec(pages=2, cert_mult=3, cover_abs=None):
    points = [
        _point(
            i,
            above=i % 2 == 0,
            size="grand" if i % 2 == 0 else "petit",
            cert=CertSpec("SNEP", "platine", cert_mult, "3x Platine", "album") if i == 1 else None,
            cover_abs=cover_abs if i == 0 else None,
            year=str(2020 + i) if i % 3 == 0 else None,
        )
        for i in range(pages * 4)
    ]
    page_specs = []
    for p in range(pages):
        chunk = tuple(points[p * 4 : (p + 1) * 4])
        background = background_point(chunk)
        entry_ratio = 0.0 if p == 0 else 0.55
        exit_ratio = 0.55 if p == 0 else chunk[-1].ratio
        page_specs.append(
            PageSpec(
                index=p + 1,
                points=chunk,
                background_key=background.key if background else None,
                background_abs=background.cover_abs if background else None,
                entry_cumul=points[p * 4 - 1].cumul if p else 0,
                entry_ratio=entry_ratio,
                exit_ratio=exit_ratio,
                curve=(
                    (0.0, entry_ratio, 0.0),
                    *((pt.x, pt.ratio, 0.0) for pt in chunk),
                    (1080.0, exit_ratio, 0.0),
                ),
                cartouche_value=chunk[-1].cumul,
                cartouche_text=format_streams_short(chunk[-1].cumul),
            )
        )
    return TimelineSpec(
        pages=tuple(page_specs), total_cumul=points[-1].cumul, style=TimelineStyle()
    )


def test_background_point_prefers_artist_project_then_streams():
    pts = (
        _point(0, above=True, cover_abs="/c0.png"),  # track, 8000 streams
        _point(1, above=False, cover_abs="/c1.png", kind="album"),  # album, 7000
        _point(2, above=True, cover_abs="/c2.png", kind="album"),  # album, 6000
        _point(3, above=False),  # sans pochette
    )
    assert background_point(pts).key == "album:1"
    assert background_point(pts[:1]).key == "track:0"
    assert background_point((pts[3],)) is None


def test_bezier_handles_follow_slopes():
    from src.dataviz.timeline_svg import bezier_handles

    curve = ((0.0, 0.0, 0.0), (300.0, 0.3, 0.001), (600.0, 0.6, 0.001))
    (c1x, c1r, c2x, c2r), second = bezier_handles(curve)
    assert (c1x, c1r) == (100.0, 0.0)  # tangente plate au départ
    assert c2x == 200.0 and abs(c2r - (0.3 - 0.1)) < 1e-9
    assert second[0] == 400.0


def test_cover_box_above_and_below():
    style = TimelineStyle()
    above = cover_box(_point(0, above=True), style)
    below = cover_box(_point(1, above=False, size="petit"), style)
    assert above[1] + above[2] == style.line_y - style.cover_offset
    assert below[1] == style.line_y + style.cover_offset
    assert above[2] == style.cover_large and below[2] == style.cover_small


# ── 10. SVG ──────────────────────────────────────────────────────────────────


def test_svg_structure_layers_and_ids(tmp_path):
    spec = _spec(pages=2, cover_abs=str(tmp_path / "cover.png"))
    svg = write_timeline_svg(spec, tmp_path / "t.svg")
    root = ET.fromstring(svg)
    pages = root.findall("svg:g", _NS)
    assert [g.get("id") for g in pages] == ["page-1", "page-2"]
    layers = [g.get("id") for g in pages[0].findall("svg:g", _NS)]
    assert layers == [
        "background-1",
        "line-1",
        "curve-1",
        "discs-1",
        "covers-1",
        "labels-1",
        "years-1",
        "cartouche-1",
    ]
    # Courbe : bord gauche + 4 points + bord droit = 6 sommets → 5 segments
    # Bézier ; la zone sous la courbe reprend le tracé et ferme par les coins bas.
    paths = pages[0].findall("svg:g[@id='curve-1']/svg:path", _NS)
    under, line = paths
    assert line.get("d").count(" C") == 5 and line.get("d").startswith("M0.00,")
    assert under.get("d").endswith(" Z") and under.get("d").count(" L") == 2
    assert under.get("style") == "mix-blend-mode:hard-light"
    # Fond : la pochette du point 0 (seule existante) recadrée, puis le voile ;
    # page 2 sans pochette → aplat seul.
    bg1 = pages[0].find("svg:g[@id='background-1']", _NS)
    assert [el.tag.split("}")[1] for el in bg1] == ["rect", "image", "rect"]
    assert bg1[1].get("preserveAspectRatio") == "xMidYMid slice"
    bg2 = pages[1].find("svg:g[@id='background-2']", _NS)
    assert [el.tag.split("}")[1] for el in bg2] == ["rect"]
    # Disques : autant que le multiplicateur, ids stables.
    discs = pages[0].findall("svg:g[@id='discs-1']/svg:g", _NS)
    assert [d.get("id") for d in discs] == ["disc-1-1-2", "disc-1-1-1", "disc-1-1-0"]
    # Pochette : image en URI file:// quand elle existe, aplat sinon.
    assert 'href="file:///' in svg
    assert 'id="cover-1-1"' in svg
    # Libellés : les runs portent leur graisse ; la ligne 2 du point 0 est repliée
    # sur deux lignes ; années au bon endroit.
    assert 'id="label1-1-0-0"' in svg and 'id="label2-1-0-1"' in svg
    assert 'id="label2-1-0-2"' in svg and 'id="label2-1-1-2"' not in svg
    assert 'font-weight="600">X</tspan>' in svg
    assert 'font-weight="300">Feat avec </tspan>' in svg
    assert 'id="year-1-0"' in svg and 'id="year-1-1"' not in svg
    assert 'id="cartouche-value-2"' in svg
    assert "dominant-baseline" not in svg and "clipPath" not in svg
    assert (tmp_path / "t.svg").read_text(encoding="utf-8") == svg


def test_svg_byte_identical_and_width():
    spec = _spec(pages=3)
    a, b = write_timeline_svg(spec), write_timeline_svg(spec)
    assert a == b
    style = spec.style
    root = ET.fromstring(a)
    assert float(root.get("width")) == 3 * style.page_width + 2 * style.page_gap


# ── 11. JSON ─────────────────────────────────────────────────────────────────


def test_json_payload_shape_and_continuity():
    spec = _spec(pages=2)
    payload = build_payload(spec, artist_name="Isha")
    assert payload["version"] == 1 and payload["artist"] == "Isha"
    assert payload["total_cumul"] == spec.total_cumul
    assert "background_opacity" not in payload["style"]  # aperçu seulement
    assert payload["style"]["cartouche_lines"] == list(TimelineStyle().cartouche_lines)
    pages = payload["pages"]
    assert [p["index"] for p in pages] == [1, 2]
    assert pages[0]["exit"]["ratio"] == pages[1]["entry"]["ratio"]
    assert pages[0]["curve"][0] == {"x_ratio": 0.0, "ratio": 0.0, "slope": 0.0}
    assert pages[0]["curve"][-1]["x_ratio"] == 1.0 and len(pages[0]["curve"]) == 6
    assert pages[1]["entry"]["cumul"] == pages[0]["points"][-1]["cumul"]
    assert pages[-1]["points"][-1]["ratio"] == 1.0
    p0 = pages[0]["points"][0]
    assert p0["slot"] == 0 and p0["above"] is True and p0["cover"] is None
    assert p0["line1"] == [[{"text": "Feat avec ", "bold": False}, {"text": "X", "bold": True}]]
    assert p0["line2"] == [[{"text": "Titre", "bold": True}], [{"text": "suite", "bold": True}]]
    assert p0["streams"] == 8000
    assert pages[0]["background"] == {"key": None, "cover_abs": None}
    assert "line_width" not in payload["style"] and "cartouche_x" not in payload["style"]
    p1 = pages[0]["points"][1]
    assert p1["cert"] == {
        "body": "SNEP",
        "palier": "platine",
        "multiplier": 3,
        "level": "3x Platine",
        "category": "album",
    }
    assert write_timeline_json(payload, None) == write_timeline_json(payload, None)
    assert json.loads(write_timeline_json(payload, None)) == payload


# ── 12. Fichier de réglages ──────────────────────────────────────────────────


def test_style_file_created_with_defaults(tmp_path):
    path = tmp_path / "timeline_style.json"
    style = load_style(path)
    assert style == TimelineStyle()
    text = path.read_text(encoding="utf-8")
    assert text.startswith("// Réglages du générateur « Timeline »")
    raw = json.loads(strip_comments(text))
    assert raw["size_label"] == 30.0
    assert raw["cartouche_lines"] == ["de streams", "cumulés", "estimés*"]
    assert "coord_precision" not in raw


def test_style_partial_override_unknown_key_and_tuple(tmp_path, caplog):
    path = tmp_path / "timeline_style.json"
    path.write_text(
        '// commentaire\n{"size_label": 34, "cartouche_lines": ["a", "b"], "inconnue": 1}',
        encoding="utf-8",
    )
    style = load_style(path)
    assert style.size_label == 34
    assert style.cartouche_lines == ("a", "b")
    assert style.page_width == TimelineStyle().page_width
    assert "inconnue" in caplog.text


def test_style_broken_json_falls_back(tmp_path):
    path = tmp_path / "timeline_style.json"
    path.write_text("{pas du json", encoding="utf-8")
    assert load_style(path) == TimelineStyle()


def test_style_path_and_write_default(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.DATA_DIR", tmp_path)
    assert style_path() == tmp_path / "timeline_style.json"
    out = write_default_style(tmp_path / "sub" / "s.json")
    assert out.exists()
