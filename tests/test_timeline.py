"""Tests du moteur « Timeline » (`src/dataviz/timeline.py`).

Jeu de référence = une miniature d'Isha en mémoire : un album à singles extraits
et bonus 13 mois plus tard, un album commun tout-feat, des freestyles, des feats,
un morceau sans date, un sans streams. Aucune lecture de `data/` réel : les
pochettes passent par un résolveur monkeypatché vers `tmp_path`.
"""

import json
import xml.etree.ElementTree as ET
from datetime import date, datetime

import pytest

from src.dataviz import timeline as tl
from src.dataviz.timeline import (
    PAGE_SIZE,
    Candidate,
    EntryChoice,
    album_date,
    best_cert,
    build_candidates,
    build_timeline_spec,
    cumulative_streams,
    default_page_count,
    default_selection,
    detect_reedition,
    disc_count,
    generate_timeline,
    generate_timeline_preview,
    is_freestyle,
    sort_candidates,
    track_date,
)
from src.dataviz.timeline_svg import TimelineStyle
from src.models.track import Track


def _track(
    tid,
    title,
    album=None,
    day=None,
    sp=None,
    yt=None,
    *,
    feat=False,
    primary=None,
    number=None,
    cover=None,
    certs=(),
    album_certs=(),
    secondary=None,
):
    t = Track(
        id=tid,
        title=title,
        album=album,
        release_date=f"{day} 00:00:00" if day else None,
        track_number=number,
        is_featuring=feat,
        primary_artist_name=primary,
        secondary_role=secondary,
    )
    t.streams.spotify_streams = sp
    t.streams.ytm_streams = yt
    t.media.cover_path = cover
    t.certs.entries = list(certs)
    t.certs.album_entries = list(album_certs)
    return t


def _cert(level, body="SNEP", category="album"):
    return {"certification": level, "body": body, "category": category}


@pytest.fixture(autouse=True)
def _covers(monkeypatch, tmp_path):
    """Résolveur de pochettes : existe ssi le fichier est créé dans `tmp_path`."""
    (tmp_path / "covers").mkdir()
    for name in ("a1", "a3", "bonus", "b1", "feat1", "grunt"):
        (tmp_path / "covers" / f"{name}.png").write_bytes(b"png")
    monkeypatch.setattr(
        tl,
        "resolve_cover",
        lambda rel: (tmp_path / rel) if rel and (tmp_path / rel).is_file() else None,
    )
    return tmp_path


def _corpus():
    """18 morceaux : voir le docstring de module."""
    album_or = [_cert("Or")]
    tracks = [
        # Album A : 2 singles extraits AVANT, 6 titres le jour J, 2 bonus 13 mois après.
        _track(
            1,
            "Single A1",
            "Album A",
            "2020-01-10",
            5_000_000,
            1_000_000,
            number=1,
            cover="covers/a1.png",
            album_certs=album_or,
        ),
        _track(
            2,
            "Single A2",
            "Album A",
            "2020-03-01",
            2_000_000,
            500_000,
            number=2,
            album_certs=album_or,
        ),
        _track(
            3,
            "A3",
            "Album A",
            "2020-06-05",
            1_000_000,
            200_000,
            number=3,
            cover="covers/a3.png",
            album_certs=album_or,
        ),
        _track(
            4, "A4", "Album A", "2020-06-05", 1_000_000, 200_000, number=4, album_certs=album_or
        ),
        _track(
            5, "A5", "Album A", "2020-06-05", 1_000_000, 200_000, number=5, album_certs=album_or
        ),
        _track(
            6, "A6", "Album A", "2020-06-05", 1_000_000, 200_000, number=6, album_certs=album_or
        ),
        _track(7, "A7", "Album A", "2020-06-05", 500_000, 100_000, number=7, album_certs=album_or),
        _track(8, "A8", "Album A", "2020-06-05", 500_000, 100_000, number=8, album_certs=album_or),
        _track(
            9,
            "Bonus 1",
            "Album A",
            "2021-07-10",
            800_000,
            100_000,
            number=9,
            cover="covers/bonus.png",
            album_certs=album_or,
        ),
        _track(
            10, "Bonus 2", "Album A", "2021-07-10", 200_000, 50_000, number=10, album_certs=album_or
        ),
        # Album B : commun, tout en feat avec le même primary.
        _track(
            11,
            "B1",
            "Album B",
            "2022-02-02",
            3_000_000,
            500_000,
            feat=True,
            primary="Limsa",
            number=1,
            cover="covers/b1.png",
        ),
        _track(
            12,
            "B2",
            "Album B",
            "2022-02-02",
            2_000_000,
            500_000,
            feat=True,
            primary="Limsa",
            number=2,
        ),
        _track(
            13,
            "B3",
            "Album B",
            "2022-02-02",
            1_000_000,
            500_000,
            feat=True,
            primary="Limsa",
            number=3,
        ),
        _track(
            14,
            "B4",
            "Album B",
            "2022-02-02",
            1_000_000,
            500_000,
            feat=True,
            primary="Limsa",
            number=4,
        ),
        # Freestyles, feats, divers.
        _track(
            15,
            "Grünt #33",
            "Grünt",
            "2019-03-27",
            None,
            4_000_000,
            feat=True,
            primary="Grünt",
            cover="covers/grunt.png",
        ),
        _track(
            16,
            "Dans mon élément",
            "XX5",
            "2018-11-23",
            20_000_000,
            5_000_000,
            feat=True,
            primary="Georgio",
            cover="covers/feat1.png",
            certs=[_cert("Double Platine", category="single")],
        ),
        _track(17, "Booska Pogo", None, "2019-12-16", 400_000, 100_000),
        _track(18, "Petit EP 1", "Petit EP", "2021-01-01", 100_000, None, number=1),
        _track(19, "Petit EP 2", "Petit EP", "2021-01-01", 100_000, None, number=2),
        _track(20, "Muet", None, "2023-05-05", None, None),
        _track(21, "Sans date", None, None, 9_000_000, 9_000_000),
        _track(
            22,
            "Apparition",
            "Album de X",
            "2023-09-09",
            300_000,
            None,
            primary="X",
            secondary="Additional Vocals",
        ),
    ]
    return tracks


# ── 1. Dates ─────────────────────────────────────────────────────────────────


def test_track_date_formats():
    assert track_date(_track(1, "x", day="2020-01-10")) == date(2020, 1, 10)
    t = Track(id=1, title="x", release_date=datetime(2021, 2, 3, 4, 5))
    assert track_date(t) == date(2021, 2, 3)
    assert track_date(_track(1, "x")) is None
    assert track_date(Track(id=1, title="x", release_date="n'importe quoi")) is None


# ── 2. Date modale et réédition ──────────────────────────────────────────────


def test_album_date_modale_ignores_singles_extraits():
    album = [t for t in _corpus() if t.album == "Album A"]
    assert album_date(album) == date(2020, 6, 5)


def test_reedition_late_batch_detected():
    album = [t for t in _corpus() if t.album == "Album A"]
    reed = detect_reedition(album)
    assert reed is not None
    d, tracks = reed
    assert d == date(2021, 7, 10)
    assert sorted(t.id for t in tracks) == [9, 10]


def test_reedition_not_detected_under_180_days_or_before():
    album = [t for t in _corpus() if t.album == "Album A" and t.id <= 8]
    album.append(_track(30, "Bonus proche", "Album A", "2020-10-01", 1, 1))  # 4 mois
    assert detect_reedition(album) is None


def test_album_date_none_without_dates():
    assert album_date([_track(1, "x", "A")]) is None
    assert detect_reedition([_track(1, "x", "A")]) is None


# ── 3. Album commun tout-feat ────────────────────────────────────────────────


def test_album_commun_tout_feat_is_candidate_with_primary():
    cands = {c.key: c for c in build_candidates(_corpus())}
    b = cands["album:album b"]
    assert b.kind == "album"
    assert b.line1_default == "Album avec **Limsa**"
    assert b.line2_default == "**Album B**"
    assert b.size_default == "grand"
    assert b.cover == "covers/b1.png"


# ── 4. Freestyles ────────────────────────────────────────────────────────────


def test_is_freestyle_on_album_primary_title():
    assert is_freestyle(_track(1, "Grünt #33", "Grünt")) == (True, "Grünt")
    assert is_freestyle(_track(1, "Session", None, primary="Planète Rap Lomepal")) == (
        True,
        "Planète Rap Lomepal",
    )
    assert is_freestyle(_track(1, "Booska Pogo")) == (True, "Booska")
    assert is_freestyle(_track(1, "Freestyle Skyrock")) == (True, "Skyrock")
    assert is_freestyle(_track(1, "Mon freestyle")) == (True, None)
    assert is_freestyle(_track(1, "Paire blanche", "No colors")) == (False, None)


def test_freestyle_line1_default():
    cands = {c.key: c for c in build_candidates(_corpus())}
    assert cands["track:15"].line1_default == "Freestyle **Grünt**"
    assert cands["track:17"].line1_default == "Freestyle **Booska**"
    assert cands["track:16"].line1_default == "Feat avec **Georgio**"
    assert cands["track:22"].line1_default == "Apparition sur **X**"
    assert cands["track:18"].line1_default == "Single"


# ── 5. Candidats : ordre, exclusions, plafond ────────────────────────────────


def test_build_candidates_order_and_exclusions():
    cands = build_candidates(_corpus())
    keys = [c.key for c in cands]
    # Projets d'abord, par date (album A, sa réédition, album B), puis les autres
    # par streams décroissants.
    assert keys[:3] == ["album:album a", "reedition:album a", "album:album b"]
    others = cands[3:]
    assert all(c.kind == "track" for c in others)
    # Morceaux par streams, puis les freestyles à part (par streams aussi).
    plain = [c for c in others if not c.is_freestyle]
    free = [c for c in others if c.is_freestyle]
    assert others == plain + free and free
    assert [c.streams for c in plain] == sorted((c.streams for c in plain), reverse=True)
    assert [c.streams for c in free] == sorted((c.streams for c in free), reverse=True)
    # Singles extraits proposés à part ; titres du jour J et bonus consommés.
    assert "track:1" in keys and "track:2" in keys
    assert "track:3" not in keys and "track:9" not in keys
    # Sans date → exclu ; petit EP (< 4) → morceaux à part.
    assert "track:21" not in keys
    assert "track:18" in keys and "album:petit ep" not in keys


def test_album_candidate_streams_cover_and_ids():
    cands = {c.key: c for c in build_candidates(_corpus())}
    a = cands["album:album a"]
    assert a.date == date(2020, 6, 5)
    assert a.track_ids == tuple(range(1, 11))
    # Pochette du jour J (a3), pas celle du single extrait en piste 1.
    assert a.cover == "covers/a3.png"
    r = cands["reedition:album a"]
    assert r.cover == "covers/bonus.png"
    assert r.line1_default == "Réédition de"
    corpus = {t.id: t for t in _corpus()}
    assert r.streams == tl.track_streams(corpus[9]) + tl.track_streams(corpus[10])


def test_candidate_without_id_excluded():
    cands = build_candidates([_track(None, "x", None, "2020-01-01", 1, 1)])
    assert cands == []


def test_no_cap_on_candidates():
    tracks = _corpus()
    tracks += [_track(100 + i, f"Hit {i}", None, "2022-01-01", 50_000_000, 1) for i in range(40)]
    cands = build_candidates(tracks)
    assert len(cands) > 40
    assert cands[-1].is_freestyle  # les freestyles ferment la liste


def test_sort_candidates_by_date():
    cands = build_candidates(_corpus())
    by_date = sort_candidates(cands, by_date=True)
    assert [c.date for c in by_date] == sorted(c.date for c in cands)
    assert sort_candidates(cands, by_date=False) == cands


# ── 6. Cumul ─────────────────────────────────────────────────────────────────


def test_disabled_tracks_stay_candidates_but_leave_the_cumul():
    """Grünt désactivé : proposable (⛔), jamais coché d'office, hors cumul."""
    tracks = _corpus()
    disabled = frozenset({15})
    cands = {c.key: c for c in build_candidates(tracks, disabled=disabled)}
    assert cands["track:15"].disabled and not cands["track:16"].disabled
    assert "track:15" not in default_selection(list(cands.values()), 20)
    at = date(2099, 1, 1)
    assert cumulative_streams(tracks, at, disabled) == cumulative_streams(
        tracks, at
    ) - tl.track_streams(tracks[14])
    entries = _entries(list(cands.values()), 12) + [
        EntryChoice("track:15", "Freestyle **Grünt**", "**Grünt #33**", "petit")
    ]
    spec = build_timeline_spec(tracks, entries, candidates=list(cands.values()), disabled=disabled)
    assert any(p.key == "track:15" for page in spec.pages for p in page.points)


def test_forced_background_per_page():
    tracks = _corpus()
    cands = build_candidates(tracks)
    entries = _entries(cands, 12)
    # « Dans mon élément » (track:16, pochette) fournit le fond de la page 1 même
    # s'il n'est pas un album ; page 2 reste automatique.
    entries = [
        EntryChoice(e.key, e.line1, e.line2, e.size, "auto", 1 if e.key == "track:16" else 0)
        for e in entries
    ]
    spec = build_timeline_spec(tracks, entries, candidates=cands)
    assert spec.pages[0].background_key == "track:16"
    auto = build_timeline_spec(tracks, _entries(cands, 12), candidates=cands)
    assert spec.pages[1].background_key == auto.pages[1].background_key
    # Deux projets pour la même page → refus nommé ; page hors bornes → refus.
    dup = [EntryChoice(e.key, e.line1, e.line2, e.size, "auto", 2) for e in entries[:2]]
    with pytest.raises(ValueError, match="page 2"):
        build_timeline_spec(tracks, dup + entries[2:], candidates=cands)
    bad = [EntryChoice(entries[0].key, "a", "b", "grand", "auto", 7)] + entries[1:]
    with pytest.raises(ValueError, match="hors de 1-4"):
        build_timeline_spec(tracks, bad, candidates=cands)
    # Projet sans pochette : avertissement, règle automatique.
    nocover = [
        EntryChoice(e.key, e.line1, e.line2, e.size, "auto", 3 if e.key == "track:17" else 0)
        for e in entries
    ]
    assert (
        build_timeline_spec(tracks, nocover, candidates=cands).pages[2].background_key != "track:17"
    )


def test_candidates_carry_has_cert():
    cands = {c.key: c for c in build_candidates(_corpus())}
    assert cands["album:album a"].has_cert and cands["reedition:album a"].has_cert
    assert cands["track:16"].has_cert
    assert not cands["album:album b"].has_cert and not cands["track:17"].has_cert


def test_cumulative_streams_inclusive_and_none_as_zero():
    tracks = _corpus()
    before = cumulative_streams(tracks, date(2020, 6, 4))
    on_day = cumulative_streams(tracks, date(2020, 6, 5))
    assert on_day > before
    # Les bonus n'entrent qu'à la réédition.
    assert cumulative_streams(tracks, date(2021, 7, 9)) < cumulative_streams(
        tracks, date(2021, 7, 10)
    )
    # Sans date : jamais compté, même « à la fin des temps ».
    total = cumulative_streams(tracks, date(2099, 1, 1))
    assert total == sum(tl.track_streams(t) for t in tracks if t.id != 21)
    assert tl.track_streams(_track(1, "x", None, "2020-01-01", None, None)) == 0


# ── 7. Spec : pagination, années, courbe ─────────────────────────────────────


def _entries(cands, count):
    return tl._entries_from_defaults(cands, default_selection(cands, count))


def test_default_selection_projects_then_streams_sorted_by_date():
    cands = build_candidates(_corpus())
    keys = default_selection(cands, 4)
    by_key = {c.key: c for c in cands}
    assert set(keys) == {"album:album a", "reedition:album a", "album:album b", "track:16"}
    assert [by_key[k].date for k in keys] == sorted(by_key[k].date for k in keys)


def test_default_page_count():
    cands = build_candidates(_corpus())
    assert default_page_count(cands) == (4 if len(cands) >= 16 else 3)
    assert default_page_count(cands[:5]) == 3


def test_build_spec_pages_alternance_years_and_curve():
    tracks = _corpus()
    cands = build_candidates(tracks)
    entries = _entries(cands, 12)
    spec = build_timeline_spec(tracks, entries, TimelineStyle(), candidates=cands)
    assert len(spec.pages) == 3
    points = [p for page in spec.pages for p in page.points]
    assert [p.above for p in points] == [i % 2 == 0 for i in range(12)]
    dates = [p.date for p in points]
    assert dates == sorted(dates)
    # Année au PREMIER projet de chaque année, toutes pages confondues.
    seen = set()
    for p in points:
        year = p.date[:4]
        assert p.year_label == (year if year not in seen else None)
        seen.add(year)
    # Courbe monotone, échelle globale, continuité entre pages.
    ratios = [p.ratio for p in points]
    assert ratios == sorted(ratios) and ratios[-1] == 1.0
    assert spec.pages[0].entry_ratio == 0.0
    for prev, nxt in zip(spec.pages, spec.pages[1:], strict=False):
        assert prev.exit_ratio == nxt.entry_ratio
        assert nxt.entry_cumul == prev.points[-1].cumul
        assert prev.points[-1].ratio <= prev.exit_ratio <= nxt.points[0].ratio
    # Dernière page : la courbe finit au bord droit, au cumul final — le
    # 4ᵉ point n'est PAS un sommet (il stagnerait depuis le dernier album).
    last = spec.pages[-1]
    assert last.exit_ratio == 1.0
    assert last.curve[-1][:2] == (TimelineStyle().page_width, 1.0)
    assert len(last.curve) == 1 + PAGE_SIZE - 1 + 1
    assert last.cartouche_value == spec.total_cumul
    # Décalage : chaque sommet intérieur est à x + curve_lag de son point.
    first = spec.pages[0]
    assert first.curve[1][0] == first.points[0].x + TimelineStyle().curve_lag
    # Les ratios des sommets restent monotones de page en page, les pentes
    # lissées ne sont jamais négatives, et la jonction entre deux pages porte
    # la MÊME tangente des deux côtés.
    all_r = [r for page in spec.pages for _x, r, _m in page.curve]
    assert all_r == sorted(all_r)
    assert all(m >= 0 for page in spec.pages for _x, _r, m in page.curve)
    for prev, nxt in zip(spec.pages, spec.pages[1:], strict=False):
        assert prev.curve[-1][2] == nxt.curve[0][2]

    # Fond de page : le projet de l'artiste le plus streamé de la page.
    for page in spec.pages:
        candidates_bg = [p for p in page.points if p.cover_abs and p.kind != "track"]
        if candidates_bg:
            assert page.background_key == max(candidates_bg, key=lambda p: p.streams).key
            assert page.background_abs
    # Disques : côté droit sur les deux premiers slots, gauche sur les deux derniers.
    assert [p.disc_side for p in spec.pages[0].points] == ["right", "right", "left", "left"]


def test_monotone_slopes_never_overshoot():
    m = tl._monotone_slopes([0, 100, 200, 300], [0.0, 0.5, 0.5, 1.0])
    assert m[1] == 0.0 and m[2] == 0.0  # palier : tangentes plates
    assert m[0] > 0 and m[3] > 0
    assert tl._monotone_slopes([0], [0.0]) == [0.0]


def test_build_spec_rejects_bad_counts_keys_and_sizes():
    tracks = _corpus()
    cands = build_candidates(tracks)
    thirteen = _entries(cands, 12) + _entries(cands, 1)
    with pytest.raises(ValueError, match="13 entrée"):
        build_timeline_spec(tracks, thirteen, candidates=cands)
    bad = _entries(cands, 12)
    bad[0] = EntryChoice("album:disparu", "a", "b", "grand")
    with pytest.raises(ValueError, match="album:disparu"):
        build_timeline_spec(tracks, bad, candidates=cands)
    bad = _entries(cands, 12)
    bad[0] = EntryChoice(bad[0].key, "a", "b", "moyen")
    with pytest.raises(ValueError, match="Taille"):
        build_timeline_spec(tracks, bad, candidates=cands)
    bad = _entries(cands, 12)
    bad[0] = EntryChoice(bad[0].key, "a", "b", "grand", "milieu")
    with pytest.raises(ValueError, match="Côté"):
        build_timeline_spec(tracks, bad, candidates=cands)


def test_disc_side_override_beats_auto():
    tracks = _corpus()
    cands = build_candidates(tracks)
    entries = _entries(cands, 12)
    entries[0] = EntryChoice(entries[0].key, "a", "b", "grand", "gauche")
    entries[3] = EntryChoice(entries[3].key, "a", "b", "grand", "droite")
    spec = build_timeline_spec(tracks, entries, candidates=cands)
    sides = [p.disc_side for p in spec.pages[0].points]
    assert sides == ["left", "right", "left", "right"]


def test_build_spec_rejects_zero_streams():
    tracks = [_track(i, f"t{i}", None, f"2020-01-{i:02d}", None, None) for i in range(1, 13)]
    cands = build_candidates(tracks)
    with pytest.raises(ValueError, match="Aucun stream"):
        build_timeline_spec(tracks, _entries(cands, 12), candidates=cands)


# ── 8. Certifications ────────────────────────────────────────────────────────


def test_disc_count():
    assert disc_count("Double Platine") == (2, "platine")
    assert disc_count("3x Platine") == (3, "platine")
    assert disc_count("Or") == (1, "or")
    assert disc_count("2x Double Or") == (4, "or")


def test_best_cert_ranking_and_tiebreak():
    assert best_cert([]) is None
    assert best_cert([_cert("Or"), _cert("Platine")]).level == "Platine"
    assert best_cert([_cert("2x Platine"), _cert("Platine")]).multiplier == 2
    tie = best_cert([_cert("Or", body="BRMA"), _cert("Or", body="SNEP")])
    assert tie.body == "SNEP"
    assert best_cert([_cert("Inconnu")]) is None
    assert best_cert([_cert("Gold", body="RIAA Latin")]).palier == "gold"


def test_cert_album_vs_track_in_spec():
    tracks = _corpus()
    cands = build_candidates(tracks)
    entries = _entries(cands, 12)
    spec = build_timeline_spec(tracks, entries, candidates=cands)
    by_key = {p.key: p for page in spec.pages for p in page.points}
    assert by_key["album:album a"].cert.level == "Or"
    assert by_key["reedition:album a"].cert.level == "Or"
    assert by_key["track:16"].cert.multiplier == 2
    assert by_key["album:album b"].cert is None


# ── 14. Bout en bout ─────────────────────────────────────────────────────────


def test_generate_timeline_end_to_end(tmp_path):
    tracks = _corpus()
    out = tmp_path / "out" / "timeline.svg"
    out.parent.mkdir()
    result = generate_timeline(tracks, artist_name="Test", pages=3, output_path=out)
    assert result.path == out and result.json_path == out.with_suffix(".json")
    assert result.page_count == 3 and result.entry_count == 12
    assert result.undated_count == 1
    assert result.unstreamed_count == 1
    assert "album:album a" not in result.missing_covers
    assert any(k.startswith("track:") for k in result.missing_covers)
    ET.fromstring(out.read_text(encoding="utf-8"))
    payload = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert payload["artist"] == "Test" and len(payload["pages"]) == 3

    # Déterminisme : régénérer dans l'ordre inverse des morceaux → octets identiques.
    out2 = tmp_path / "out2" / "timeline.svg"
    out2.parent.mkdir()
    generate_timeline(list(reversed(tracks)), artist_name="Test", pages=3, output_path=out2)
    assert out.read_bytes() == out2.read_bytes()
    assert out.with_suffix(".json").read_bytes() == out2.with_suffix(".json").read_bytes()


def test_generate_timeline_preview_html(tmp_path):
    out = tmp_path / "apercu.html"
    html_path = generate_timeline_preview(_corpus(), artist_name="Test", pages=3, output_path=out)
    html = html_path.read_text(encoding="utf-8")
    assert html.count('<use href="#page-') == 3
    assert 'viewBox="0 0 1080 1350"' in html and 'id="page-3"' in html
    assert not (tmp_path / "timeline.json").exists()


def test_generate_timeline_default_output_path(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.EXPORTS_DIR", tmp_path / "exports")
    result = generate_timeline(_corpus(), artist_name="Te/st", pages=3)
    assert result.path == tmp_path / "exports" / "Te_st" / "_timeline" / "timeline.svg"
    assert result.path.exists()
    html = generate_timeline_preview(_corpus(), artist_name="Te/st", pages=3)
    assert html == tmp_path / "exports" / "Te_st" / "_timeline" / "apercu" / "apercu.html"


def test_candidate_is_frozen_dataclass():
    c = Candidate(
        "k", "track", date(2020, 1, 1), "t", "l1", "l2", "petit", 0, False, False, None, (1,)
    )
    with pytest.raises(AttributeError):
        c.key = "x"


def test_record_types_drive_the_default_label():
    """« EP » / « Album » / « Compilation » viennent de `albums.record_type`
    (Deezer ou saisie) ; sans donnée, « Album ». Clé = titre normalisé."""
    types = tl.record_types_par_titre(
        [
            {"title": "ALBUM A", "record_type": "ep"},
            {"title": "Album B", "record_type": "compile"},
            {"title": "Sans type", "record_type": None},
        ]
    )
    assert types == {"album a": "ep", "album b": "compile"}
    cands = {c.key: c for c in build_candidates(_corpus(), record_types=types)}
    assert cands["album:album a"].line1_default == "EP"
    assert cands["reedition:album a"].line1_default == "Réédition de"
    assert cands["album:album b"].line1_default == "Compilation avec **Limsa**"
    assert {c.key: c for c in build_candidates(_corpus())}["album:album a"].line1_default == "Album"
