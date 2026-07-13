"""Tests du provider ReccoBeats (src/enrichment/providers/reccobeats) — sans réseau.

Deux voies : ISRC (try_by_isrc) et Spotify ID (enrich). Clients mockés.
"""

from src.enrichment.context import EnrichmentContext
from src.enrichment.providers.reccobeats import ReccoBeatsProvider
from src.models.artist import Artist
from src.models.track import Track


class _FakeReccoClient:
    def __init__(self, isrc_info=None, track_info=None):
        self._isrc_info = isrc_info
        self._track_info = track_info

    def get_track_info_by_isrc(self, isrc):
        return self._isrc_info

    def get_track_info(self, spotify_id):
        return self._track_info


class _FakeSpotifyScraper:
    def __init__(self, spotify_id=None, page_title=None):
        self._id = spotify_id
        self._title = page_title

    def get_spotify_id(self, artist, title):
        return self._id

    def get_spotify_page_title(self, spotify_id):
        return self._title


def _track():
    return Track(title="Solo", artist=Artist(name="Sofiane Pamart"))


def test_is_available():
    assert ReccoBeatsProvider(None).is_available() is False
    assert ReccoBeatsProvider(_FakeReccoClient()).is_available() is True


def test_try_by_isrc_applique_bpm_et_resolution():
    client = _FakeReccoClient(isrc_info={"success": True, "bpm": 120, "key": 5, "mode": 1})
    provider = ReccoBeatsProvider(client)
    track = _track()
    track.isrc = "FRX9820001"
    ctx = EnrichmentContext()
    assert provider.try_by_isrc(track, ctx) is True
    assert ("reccobeats", 120) in ctx.bpm_ballot.candidates
    assert track.reccobeats_resolution == "isrc"


def test_try_by_isrc_sans_isrc_renvoie_false():
    provider = ReccoBeatsProvider(_FakeReccoClient())
    assert provider.try_by_isrc(_track(), EnrichmentContext()) is False


def test_gate_skip_avec_resultat_true_si_isrc_satisfaite():
    # La voie ISRC (pré-étape) a déjà satisfait la source : pas de second appel,
    # et le résultat posé dans le dict est True (valeur historique)
    ctx = EnrichmentContext(isrc_satisfied=True)
    assert ReccoBeatsProvider().gate(_track(), ctx) is True


def test_gate_execute_sinon():
    assert ReccoBeatsProvider().gate(_track(), EnrichmentContext()) is None


def test_enrich_par_spotify_id_existant_valide():
    client = _FakeReccoClient(
        track_info={"success": True, "bpm": 140, "key": 7, "mode": 0, "duration": 200}
    )
    provider = ReccoBeatsProvider(client)
    track = _track()
    track.spotify_id = "spot123"
    ctx = EnrichmentContext(
        artist_tracks=[Track(title="Autre")],
        validate_spotify_id_unique=lambda sid, t, tracks: True,
    )
    assert provider.enrich(track, ctx) is True
    assert ("reccobeats", 140) in ctx.bpm_ballot.candidates
    assert track.reccobeats_resolution == "spotify_id"
    assert track.duration == 200


def test_enrich_scrape_spotify_si_autorise():
    client = _FakeReccoClient(track_info={"success": True, "bpm": 100})
    scraper = _FakeSpotifyScraper(spotify_id="scraped99", page_title="Solo - Sofiane Pamart")
    provider = ReccoBeatsProvider(client, spotify_scraper_getter=lambda: scraper)
    track = _track()
    ctx = EnrichmentContext(allow_spotify_scrape=True)
    assert provider.enrich(track, ctx) is True
    assert track.spotify_id == "scraped99"


def test_enrich_sans_id_ni_scrape_renvoie_false():
    scraper = _FakeSpotifyScraper(None)
    provider = ReccoBeatsProvider(_FakeReccoClient(), spotify_scraper_getter=lambda: scraper)
    track = _track()
    ctx = EnrichmentContext(allow_spotify_scrape=False)
    assert provider.enrich(track, ctx) is False
