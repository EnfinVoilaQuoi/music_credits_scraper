"""Parser de la page EMBED Spotify sur page réelle enregistrée.

La page /embed/track/{id} est server-rendered : son __NEXT_DATA__ contient les
artistes EXACTS du morceau (contrairement à la page track normale, coquille JS
dont le premier spotify:artist: venu appartient souvent aux recommandations).
Re-capture : scripts/capture_fixtures.py --only spotify_embed.
Sentinelle : Josman — Dans le vide.
"""

import re

import pytest

from src.scrapers.spotify_id_scraper_v2 import SpotifyIDScraper
from tests.conftest import load_fixture

FIXTURE = "spotify_embed/track.html"
_SPOTIFY_ID_RE = re.compile(r"^[A-Za-z0-9]{22}$")


@pytest.fixture(scope="module")
def artists():
    return SpotifyIDScraper._parse_embed_artists(load_fixture(FIXTURE))


def test_artistes_extraits(artists):
    assert artists, "aucun artiste extrait du __NEXT_DATA__ — structure embed changée ?"
    assert any(a["name"] == "Josman" for a in artists)


def test_structure(artists):
    for artist in artists:
        assert set(artist) == {"name", "id"}
        assert artist["name"]
        assert _SPOTIFY_ID_RE.match(artist["id"]), artist["id"]


def test_html_vide():
    assert SpotifyIDScraper._parse_embed_artists("") == []
    assert SpotifyIDScraper._parse_embed_artists("<html><body>rien</body></html>") == []
