"""Tests du provider Spotify ID (src/enrichment/providers/spotify_id) — sans réseau."""

from src.enrichment.context import EnrichmentContext
from src.enrichment.providers.spotify_id import SpotifyIdProvider
from src.models.artist import Artist
from src.models.track import Track


class _FakeScraper:
    def __init__(self, spotify_id=None, page_title=None):
        self._id = spotify_id
        self._title = page_title
        self.calls = []

    def get_spotify_id(self, artist, title):
        self.calls.append((artist, title))
        return self._id

    def get_spotify_page_title(self, spotify_id):
        return self._title


def _track():
    return Track(title="Solo", artist=Artist(name="Sofiane Pamart"))


def test_is_available():
    assert SpotifyIdProvider(None).is_available() is False
    assert SpotifyIdProvider(_FakeScraper()).is_available() is True


def test_enrich_pose_id_et_titre_page():
    scraper = _FakeScraper(spotify_id="abc123", page_title="Solo - Sofiane Pamart")
    provider = SpotifyIdProvider(scraper)
    track = _track()
    ctx = EnrichmentContext(validate_spotify_id_unique=lambda *a: True)
    assert provider.enrich(track, ctx) is True
    assert track.spotify_id == "abc123"
    assert track.spotify_page_title == "Solo - Sofiane Pamart"


def test_enrich_echec_si_scraper_vide():
    provider = SpotifyIdProvider(_FakeScraper(spotify_id=None))
    assert provider.enrich(_track(), EnrichmentContext()) is False


def test_id_rejete_si_duplicata():
    scraper = _FakeScraper(spotify_id="dup")
    provider = SpotifyIdProvider(scraper)
    track = _track()
    ctx = EnrichmentContext(
        artist_tracks=[Track(title="Autre")],
        validate_spotify_id_unique=lambda sid, t, tracks: False,
    )
    assert provider.enrich(track, ctx) is False
    assert track.spotify_id is None


def test_get_unique_reutilise_id_existant_valide_sans_force():
    provider = SpotifyIdProvider(_FakeScraper(spotify_id="scraped"))
    track = _track()
    track.spotify_id = "existant"
    ctx = EnrichmentContext(validate_spotify_id_unique=lambda *a: True)
    # force_scraper=False → l'ID existant validé est réutilisé, pas de scrape
    assert provider.get_unique_spotify_id(track, ctx, force_scraper=False) == "existant"


def test_indisponible_renvoie_none():
    assert SpotifyIdProvider(None).get_unique_spotify_id(_track(), EnrichmentContext()) is None
