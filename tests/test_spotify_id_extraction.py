"""Régression drift site 2026-07-18 : la recherche Spotify sert des hrefs
RELATIFS (`/track/<id>`, `/artist/<id>`) — l'ancien garde « 'spotify' dans
l'URL » les rejetait tous et rendait la source muette (sync ET async, la
logique d'extraction étant partagée)."""

import pytest

from src.scrapers.spotify_id_scraper_v2 import SpotifyIDScraper

TRACK_ID = "6xIDZPcKh7x070OUZkt7Fr"
ARTIST_ID = "0OQMxt6l70FM2YlLwYDiEn"


@pytest.fixture()
def scraper(tmp_path):
    return SpotifyIDScraper(cache_file=str(tmp_path / "cache.json"))


@pytest.mark.parametrize(
    "url",
    [
        f"/track/{TRACK_ID}",  # href relatif (drift 2026-07-18)
        f"/track/{TRACK_ID}?si=abc",
        f"https://open.spotify.com/track/{TRACK_ID}",
        f"https://open.spotify.com/intl-fr/track/{TRACK_ID}?si=abc",
        f"spotify:track:{TRACK_ID}",
    ],
)
def test_track_id_extrait(scraper, url):
    assert scraper.extract_spotify_id_from_url(url) == TRACK_ID


@pytest.mark.parametrize(
    "url",
    [
        f"/artist/{ARTIST_ID}",  # href relatif (drift 2026-07-18)
        f"https://open.spotify.com/artist/{ARTIST_ID}",
        f"spotify:artist:{ARTIST_ID}",
    ],
)
def test_artist_id_extrait(scraper, url):
    assert scraper.extract_artist_id_from_url(url) == ARTIST_ID


@pytest.mark.parametrize("url", [None, "", "/album/xyz", "/track/tropcourt", "https://example.com"])
def test_rejets_track(scraper, url):
    assert scraper.extract_spotify_id_from_url(url) is None


def test_relatif_track_ne_matche_pas_artist(scraper):
    assert scraper.extract_artist_id_from_url(f"/track/{TRACK_ID}") is None
