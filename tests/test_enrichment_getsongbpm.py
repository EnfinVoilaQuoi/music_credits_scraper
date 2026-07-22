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
    track.audio.key = None
    track.audio.mode = None
    return track


def test_is_available():
    assert GetSongBpmProvider(None).is_available() is False
    assert GetSongBpmProvider(_FakeFetcher(_FakeSongData())).is_available() is True


def test_bpm_devient_candidat_et_key_mode_observes():
    # E7 : plus de pose legacy directe — BPM au scrutin, key/mode en observations
    # PAR SOURCE normalisées (F#m → pitch class 6, "minor" → 0). apply_resolutions
    # pose les colonnes en fin de run (hors périmètre du provider).
    song = _FakeSongData(bpm=142, key="F#m", mode="minor")
    provider = GetSongBpmProvider(_FakeFetcher(song))
    track = _track()
    ctx = EnrichmentContext()
    assert provider.enrich(track, ctx) is True
    assert ("getsongbpm", 142) in ctx.bpm_ballot.candidates
    keys = [o for o in ctx.observations if o.field == "key" and o.source == "getsongbpm"]
    modes = [o for o in ctx.observations if o.field == "mode" and o.source == "getsongbpm"]
    assert keys and keys[0].value == 6
    assert modes and modes[0].value == 0  # minor → 0


def test_erreur_api_renvoie_false():
    provider = GetSongBpmProvider(_FakeFetcher(_FakeSongData(error="not found")))
    assert provider.enrich(_track(), EnrichmentContext()) is False


def test_indisponible_renvoie_false():
    assert GetSongBpmProvider(None).enrich(_track(), EnrichmentContext()) is False


def test_gate_ne_skip_jamais():
    # API gratuite/rapide : appelée systématiquement (2ᵉ vote BPM, §8.3)
    track = _track()
    track.audio.bpm = 120
    track.audio.key = 5
    track.audio.mode = 1
    ctx = EnrichmentContext()
    ctx.results["reccobeats"] = True  # même quand tout est déjà présent
    assert GetSongBpmProvider().gate(track, ctx) is None
