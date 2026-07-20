"""Tests du LyricsProvider (src/enrichment/providers/lyrics) — sans réseau.

Le provider POSSÈDE les clients (lazy selon les sources) et APPLIQUE la résolution
au morceau. On injecte des stubs et on vérifie l'application (colonnes + observations
+ fallback texte) + le respect des flags de source (pas de client créé si off).
"""

from src.enrichment.base import Capability
from src.enrichment.providers.lyrics import LyricsProvider
from src.models.artist import Artist
from src.models.track import Track

_LRC = "[00:10.00] alpha\n[00:20.00] beta\n[00:30.00] gamma\n[00:40.00] delta"


class _FakeLRCLIB:
    def __init__(self, lrc=None):
        self._lrc = lrc

    def get_synced(self, title, artist, album_name=None, duration=None):
        return {"lyrics_synced": self._lrc} if self._lrc else None


class _FakeYTM:
    def __init__(self, lrc=None, text=None, source=None):
        self._res = {"lyrics_synced": lrc, "lyrics": text, "duration": None, "source": source}

    def get_lyrics(self, artist, title):
        return dict(self._res)


def _track(has_lyrics=False, lyrics=None):
    t = Track(title="Solo", artist=Artist(name="X"))
    t.duration = 200
    t.album = "Album"
    t.has_lyrics = has_lyrics
    t.lyrics = lyrics
    return t


def test_capability_lyrics():
    assert LyricsProvider().capabilities == {Capability.LYRICS}
    assert LyricsProvider().name == "lyrics"


def test_applique_synchro_lrclib():
    provider = LyricsProvider(
        sync_lrclib=True,
        sync_ytm=False,
        sync_musixmatch=False,
        lyrics_ytm=False,
        lrclib=_FakeLRCLIB(_LRC),
    )
    track = _track()
    outcome = provider.enrich(track, "X", need_sync=True, need_text=False)
    # Appliqué au track
    assert track.lyrics_synced == _LRC
    assert track.lyrics_synced_source == "LRCLIB"
    assert track.lyrics_synced_confidence == 1
    assert [o.source for o in track.observations] == ["lrclib"]
    # Outcome renvoyé pour l'agrégation des compteurs
    assert outcome.synced_kind == "lrclib"
    assert outcome.synced_is_cross is False


def test_applique_fallback_texte():
    provider = LyricsProvider(
        sync_lrclib=False,
        sync_ytm=False,
        sync_musixmatch=False,
        lyrics_ytm=True,
        ytm=_FakeYTM(text="des paroles", source="YouTube Music"),
    )
    track = _track(has_lyrics=False, lyrics=None)
    outcome = provider.enrich(track, "X", need_sync=False, need_text=True)
    assert track.lyrics == "des paroles"
    assert track.has_lyrics is True
    assert track.lyrics_source == "YouTube Music"
    assert track.lyrics_scraped_at is not None
    assert outcome.text == "des paroles"


def test_clients_lazy_respectent_les_flags():
    # Toutes sources OFF → aucun client créé (pas d'instanciation réseau).
    provider = LyricsProvider(
        sync_lrclib=False, sync_ytm=False, sync_musixmatch=False, lyrics_ytm=False
    )
    assert provider._lrclib_client() is None
    assert provider._ytm_client() is None
    assert provider._mxm_client() is None


def test_client_injecte_est_utilise():
    fake = _FakeLRCLIB(_LRC)
    provider = LyricsProvider(
        sync_lrclib=True, sync_ytm=False, sync_musixmatch=False, lyrics_ytm=False, lrclib=fake
    )
    assert provider._lrclib_client() is fake


def test_close_ne_leve_pas_sans_close_client():
    # LRCLIB/YTM/Musixmatch n'ont pas de close() → no-op silencieux.
    provider = LyricsProvider(lrclib=_FakeLRCLIB(_LRC), ytm=_FakeYTM())
    provider.close()  # ne doit pas lever
