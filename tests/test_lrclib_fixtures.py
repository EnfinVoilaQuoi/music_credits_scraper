"""Parsers LRCLIB sur réponse réelle enregistrée + comparateurs de matching.

Fixture = réponse /get exacte (Josman — Dans le vide, 243 s).
Re-capture : scripts/capture_fixtures.py --only lrclib.
"""

import re

import pytest

from src.api.lrclib_api import LRCLIBAPI, _artist_match, _title_match
from tests.conftest import load_fixture_json

FIXTURE = "lrclib/get_exact.json"
_LRC_TIMESTAMP_RE = re.compile(r"\[\d{2}:\d{2}")


@pytest.fixture(scope="module")
def api():
    return LRCLIBAPI()  # init = session requests, aucun réseau


def test_pack_fixture(api):
    packed = api._pack(load_fixture_json(FIXTURE))
    assert packed is not None, "réponse /get non packable — format LRCLIB changé ?"
    assert packed["source"] == "LRCLIB"
    assert packed["lrclib_id"]
    assert packed["lyrics_synced"], "pas de syncedLyrics dans la fixture"
    assert _LRC_TIMESTAMP_RE.search(packed["lyrics_synced"]), "format LRC [mm:ss] absent"
    # Durée = désambiguïsateur clé du /get (tolérance ±2 s côté API)
    assert abs(int(packed["duration"]) - 243) <= 2


def test_pack_vide(api):
    assert api._pack({}) is None
    assert api._pack({"instrumental": True}) is None
    assert api._pack("pas un dict") is None


@pytest.mark.parametrize(
    ("a", "b", "minimum"),
    [
        ("Dans le vide", "Dans le vide", 1.0),
        ("Dans le vide (feat. X)", "Dans le vide", 1.0),  # parenthèses feat ignorées
        ("Intro", "Intro - Remastered 2020", 0.9),  # inclusion stricte → bonus
    ],
)
def test_title_match_fort(a, b, minimum):
    assert _title_match(a, b) >= minimum


def test_title_match_faible():
    assert _title_match("Dans le vide", "Complètement Autre Chose") < 0.72


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("Josman", "josman", 1.0),
        ("Josman", "Josman & Kev", 1.0),  # inclusion après normalisation
        ("", "Josman", 0.0),
    ],
)
def test_artist_match(a, b, expected):
    assert _artist_match(a, b) == expected
