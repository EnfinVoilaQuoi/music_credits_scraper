"""Tests du LyricsProvider (src/enrichment/providers/lyrics) — sans réseau.

Le provider POSSÈDE les clients (lazy selon les sources) et APPLIQUE la résolution
au morceau. On injecte des stubs et on vérifie l'application (colonnes + observations
+ fallback texte) + le respect des flags de source (pas de client créé si off).
"""

import asyncio

import httpx

from src.api.async_http import AsyncHttpSession
from src.concurrency.rate_limiter import DomainRateLimiter
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
    t.lyrics.present = has_lyrics
    t.lyrics.text = lyrics
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
    assert track.lyrics.synced == _LRC
    assert track.lyrics.synced_source == "LRCLIB"
    assert track.lyrics.synced_confidence == 1
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
    assert track.lyrics.text == "des paroles"
    assert track.lyrics.present is True
    assert track.lyrics.source == "YouTube Music"
    assert track.lyrics.scraped_at is not None
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


# ── Voie async : pont LRCLIB par défaut + fermeture de la session ────────────


def _offline_http(handler):
    """AsyncHttpSession offline (transport mocké, sans délai)."""
    return AsyncHttpSession(transport=httpx.MockTransport(handler), limiter=DomainRateLimiter(0.0))


def test_pont_lrclib_async_utilise_quand_non_injecte():
    """Sans client injecté, LRCLIB passe par le jumeau async sur la session partagée."""

    def handler(request):
        # `/get` exact (durée + album fournis) renvoie la synchro.
        return httpx.Response(200, json={"id": 1, "syncedLyrics": _LRC, "duration": 200})

    provider = LyricsProvider(
        sync_lrclib=True,
        sync_ytm=False,
        sync_musixmatch=False,
        lyrics_ytm=False,
        http=_offline_http(handler),
        runner=asyncio.run,  # runner offline (pas de boucle applicative en test)
    )
    track = _track()
    provider.enrich(track, "X", need_sync=True, need_text=False)
    assert track.lyrics.synced == _LRC
    assert track.lyrics.synced_source == "LRCLIB"
    assert [o.source for o in track.observations] == ["lrclib"]


def test_close_ferme_la_session_async():
    closed = {"n": 0}

    class _FakeHttp:
        async def aclose(self):
            closed["n"] += 1

    provider = LyricsProvider(sync_lrclib=True, http=_FakeHttp(), runner=asyncio.run)
    # Force la création du pont (donc l'usage de la session).
    assert provider._lrclib_client() is not None
    provider.close()
    assert closed["n"] == 1
    assert provider._http is None
