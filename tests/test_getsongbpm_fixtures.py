"""Parsers GetSongBPM sur réponse réelle enregistrée (/search).

Fixture = recherche « Daft Punk — Harder, Better, Faster, Stronger » (nécessite
GETSONGBPM_API_KEY à la capture ; la clé n'est jamais stockée).
Re-capture : scripts/capture_fixtures.py --only getsongbpm.
"""

import pytest

from src.api.getsongbpm_api import GetSongBPMFetcher
from tests.conftest import load_fixture_json

FIXTURE = "getsongbpm/search.json"


@pytest.fixture(scope="module")
def fetcher():
    # __init__ exige une clé API et charge un cache disque → esquivé :
    # les méthodes testées sont statiques ou sans état d'instance.
    return GetSongBPMFetcher.__new__(GetSongBPMFetcher)


def _hit_artist_name(hit: dict) -> str:
    """Même extraction que _select_hit (artiste = objet, liste ou string)."""
    ha = hit.get("artist")
    if isinstance(ha, dict):
        return ha.get("name", "")
    if isinstance(ha, list) and ha:
        return ha[0].get("name", "") if isinstance(ha[0], dict) else str(ha[0])
    return str(ha or "")


def test_select_hit_sur_fixture(fetcher):
    data = load_fixture_json(FIXTURE)
    hits = data.get("search")
    assert isinstance(hits, list) and hits, "structure {'search': [...]} changée ?"

    # Un hit 'song' exploitable existe (titre + tempo/key_of)
    songs = [
        h for h in hits if isinstance(h, dict) and "title" in h and ("tempo" in h or "key_of" in h)
    ]
    assert songs, "aucun objet song dans la réponse — format API changé ?"

    # _select_hit retrouve un morceau en ancrant sur l'artiste du hit lui-même
    song = songs[0]
    selected = fetcher._select_hit(hits, _hit_artist_name(song), song["title"])
    assert selected is not None
    assert fetcher._parse_tempo(selected.get("tempo")) is None or isinstance(
        fetcher._parse_tempo(selected.get("tempo")), int
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("220", 220),  # l'API renvoie parfois tempo en string
        (120.4, 120),
        (98, 98),
        (None, None),
        ("abc", None),
    ],
)
def test_parse_tempo(value, expected):
    assert GetSongBPMFetcher._parse_tempo(value) == expected


@pytest.mark.parametrize(
    ("key_of", "expected"),
    [
        ("Em", "minor"),
        ("F#m", "minor"),
        ("C", "major"),
        ("", None),
    ],
)
def test_extract_mode_from_key(fetcher, key_of, expected):
    assert fetcher._extract_mode_from_key(key_of) == expected
