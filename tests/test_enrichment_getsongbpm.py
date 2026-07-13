"""Tests du provider GetSongBPM (src/enrichment/providers/getsongbpm) — sans réseau."""

from dataclasses import dataclass

from src.enrichment.context import EnrichmentContext
from src.enrichment.providers.getsongbpm import GetSongBpmProvider
from src.models.artist import Artist
from src.models.track import Track


@dataclass
class _FakeSongData:
    bpm: int | None = None
    key: str | None = None
    mode: str | None = None
    time_signature: str | None = None
    error: str | None = None


class _FakeFetcher:
    def __init__(self, song_data):
        self._song_data = song_data
        self.calls = []

    def fetch_track_bpm(self, artist, title):
        self.calls.append((artist, title))
        return self._song_data


def _track():
    # key/mode sont des attributs dynamiques posés par le mapper DB (pas des
    # champs de la dataclass) : on les initialise comme un track chargé depuis
    # la base, sinon le provider (fidèle à l'historique) lève AttributeError.
    track = Track(title="Solo", artist=Artist(name="Sofiane Pamart"))
    track.key = None
    track.mode = None
    return track


def test_is_available():
    assert GetSongBpmProvider(None).is_available() is False
    assert GetSongBpmProvider(_FakeFetcher(_FakeSongData())).is_available() is True


def test_bpm_devient_candidat_et_key_mode_poses():
    song = _FakeSongData(bpm=142, key="F#m", mode="minor")
    provider = GetSongBpmProvider(_FakeFetcher(song))
    track = _track()
    ctx = EnrichmentContext()
    assert provider.enrich(track, ctx) is True
    assert ("getsongbpm", 142) in ctx.bpm_ballot.candidates
    assert track.bpm == 142
    assert track.mode == 0  # minor → 0
    assert track.key_mode_source == "getsongbpm"


def test_erreur_api_renvoie_false():
    provider = GetSongBpmProvider(_FakeFetcher(_FakeSongData(error="not found")))
    assert provider.enrich(_track(), EnrichmentContext()) is False


def test_indisponible_renvoie_false():
    assert GetSongBpmProvider(None).enrich(_track(), EnrichmentContext()) is False


def test_gate_ne_skip_jamais():
    # API gratuite/rapide : appelée systématiquement (2ᵉ vote BPM, §8.3)
    track = _track()
    track.bpm = 120
    track.key = 5
    track.mode = 1
    ctx = EnrichmentContext()
    ctx.results["reccobeats"] = True  # même quand tout est déjà présent
    assert GetSongBpmProvider().gate(track, ctx) is None
