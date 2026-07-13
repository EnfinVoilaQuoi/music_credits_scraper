"""Tests du provider Deezer (src/enrichment/providers/deezer) — sans réseau.

On injecte un faux client Deezer : on vérifie que le provider pose les champs
(duration/isrc/…) et alimente le scrutin BPM du contexte, pas le track direct.
"""

from src.enrichment.context import EnrichmentContext
from src.enrichment.providers.deezer import DeezerProvider
from src.models.artist import Artist
from src.models.track import Track


class _FakeDeezerClient:
    """Client Deezer minimal : renvoie le result figé passé au constructeur."""

    def __init__(self, result):
        self._result = result
        self.calls = []

    def enrich_track(self, artist, title, previous_duration, scraped_release_date):
        self.calls.append((artist, title, previous_duration, scraped_release_date))
        return self._result


def _track():
    return Track(title="Solo", artist=Artist(name="Sofiane Pamart"))


def test_is_available_reflete_le_client():
    assert DeezerProvider(None).is_available() is False
    assert DeezerProvider(_FakeDeezerClient({})).is_available() is True


def test_enrich_pose_duration_isrc_et_candidat_bpm():
    result = {
        "success": True,
        "verifications": {},
        "data": {
            "deezer_duration": 195,
            "deezer_isrc": "FRX9820001",
            "deezer_bpm": 142,
            "deezer_track_id": 999,
        },
    }
    provider = DeezerProvider(_FakeDeezerClient(result))
    ctx = EnrichmentContext()
    track = _track()

    assert provider.enrich(track, ctx) is True
    assert track.duration == 195
    assert track.isrc == "FRX9820001"
    # Le BPM Deezer est un CANDIDAT du scrutin, pas une valeur imposée
    assert ("deezer", 142) in ctx.bpm_ballot.candidates


def test_enrich_bpm_opportuniste_si_aucun_bpm():
    result = {
        "success": True,
        "verifications": {},
        "data": {"deezer_bpm": 100},
    }
    provider = DeezerProvider(_FakeDeezerClient(result))
    track = _track()
    provider.enrich(track, EnrichmentContext())
    # bpm absent au départ → valeur opportuniste posée en plus du candidat
    assert track.bpm == 100


def test_enrich_echec_client_renvoie_false():
    provider = DeezerProvider(_FakeDeezerClient({"success": False, "error": "not found"}))
    track = _track()
    assert provider.enrich(track, EnrichmentContext()) is False
    assert track.duration is None


def test_enrich_utilise_artiste_principal_si_featuring():
    result = {"success": True, "verifications": {}, "data": {}}
    client = _FakeDeezerClient(result)
    provider = DeezerProvider(client)
    track = Track(title="Feat", artist=Artist(name="Cherché"))
    track.is_featuring = True
    track.primary_artist_name = "Principal"
    provider.enrich(track, EnrichmentContext())
    assert client.calls[0][0] == "Principal"  # artist passé à l'API Deezer


def test_provider_non_disponible_renvoie_false():
    assert DeezerProvider(None).enrich(_track(), EnrichmentContext()) is False
