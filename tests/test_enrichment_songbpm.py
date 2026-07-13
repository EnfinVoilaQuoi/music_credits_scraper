"""Tests du provider SongBPM (src/enrichment/providers/songbpm) — sans réseau."""

from src.enrichment.context import EnrichmentContext
from src.enrichment.providers.songbpm import SongBpmProvider
from src.models.artist import Artist
from src.models.track import Track


class _FakeScraper:
    def __init__(self, data):
        self._data = data
        self.calls = []

    def search_track(self, title, artist, spotify_id=None, fetch_details=False):
        self.calls.append((title, artist, spotify_id, fetch_details))
        return self._data


def _track():
    return Track(title="Solo", artist=Artist(name="Sofiane Pamart"))


def test_is_available():
    assert SongBpmProvider(None).is_available() is False
    assert SongBpmProvider(_FakeScraper({})).is_available() is True


def test_bpm_candidat_key_mode_duration():
    data = {"bpm": 90, "key": 5, "mode": 1, "duration": 200}
    provider = SongBpmProvider(_FakeScraper(data))
    track = _track()
    ctx = EnrichmentContext()
    assert provider.enrich(track, ctx) is True
    assert ("songbpm", 90) in ctx.bpm_ballot.candidates
    assert track.key == 5
    assert track.mode == 1
    assert track.key_mode_source == "songbpm"
    assert track.duration == 200


def test_candidat_bpm_compte_comme_succes_meme_si_bpm_deja_present():
    # Régression : SongBPM confirme un BPM déjà présent → SUCCÈS (2ᵉ vote),
    # plus « ÉCHEC » à tort. Le candidat rejoint le scrutin.
    provider = SongBpmProvider(_FakeScraper({"bpm": 146}))
    track = _track()
    track.bpm = 146  # déjà renseigné
    ctx = EnrichmentContext()
    assert provider.enrich(track, ctx) is True
    assert ("songbpm", 146) in ctx.bpm_ballot.candidates


def test_scraper_vide_renvoie_false():
    provider = SongBpmProvider(_FakeScraper(None))
    assert provider.enrich(_track(), EnrichmentContext()) is False


def test_spotify_id_de_songbpm_rejete_si_duplicata():
    # Un validateur qui refuse tout : l'ID trouvé par SongBPM n'est PAS posé
    data = {"spotify_id": "dup123"}
    provider = SongBpmProvider(_FakeScraper(data))
    track = _track()
    other = Track(title="Autre", artist=track.artist)
    ctx = EnrichmentContext(
        artist_tracks=[other],
        validate_spotify_id_unique=lambda sid, t, tracks: False,
    )
    provider.enrich(track, ctx)
    assert track.spotify_id is None


def test_indisponible_renvoie_false():
    assert SongBpmProvider(None).enrich(_track(), EnrichmentContext()) is False
