"""Tests des jumeaux async des clients API (Phase F2b) — transport mocké.

Chaque jumeau `*_async` partage sa logique pure (params, sélection, extraction,
vérifications, cache) avec la voie sync : on vérifie ici que la voie async
produit les mêmes structures de retour depuis les mêmes payloads.
"""

import asyncio

import httpx

from src.api.async_http import AsyncHttpSession
from src.api.deezer_api import DeezerAPI
from src.api.getsongbpm_api import GetSongBPMFetcher
from src.api.reccobeats_api import ReccoBeatsIntegratedClient
from src.concurrency.rate_limiter import DomainRateLimiter


def _http(handler) -> AsyncHttpSession:
    return AsyncHttpSession(transport=httpx.MockTransport(handler), limiter=DomainRateLimiter(0.0))


# ──────────────────────────────────────────────────────────────────────
# Deezer
# ──────────────────────────────────────────────────────────────────────

_DEEZER_HIT = {
    "id": 3135556,
    "isrc": "FRXXX2000001",
    "bpm": 0,  # Deezer renvoie souvent 0 → doit devenir None
    "duration": 240,
    "explicit_lyrics": True,
    "readable": True,
    "release_date": "2020-01-10",
    "rank": 100,
    "link": "https://www.deezer.com/track/3135556",
    "album": {"id": 42, "cover_medium": "https://img/album.jpg", "cover_xl": "https://img/xl.jpg"},
    "artist": {"id": 7, "picture_xl": "https://img/artist.jpg"},
}


def test_deezer_enrich_track_async_success():
    def handler(request):
        assert request.url.host == "api.deezer.com"
        assert 'artist:"X"' in request.url.params["q"]
        return httpx.Response(200, json={"data": [_DEEZER_HIT]})

    result = asyncio.run(
        DeezerAPI().enrich_track_async(
            _http(handler), "X", "Solo", previous_duration=241, scraped_release_date="2020-01-10"
        )
    )

    assert result["success"] is True
    data = result["data"]
    assert data["deezer_track_id"] == 3135556
    assert data["deezer_isrc"] == "FRXXX2000001"
    assert data["deezer_bpm"] is None  # 0 filtré
    assert data["deezer_duration"] == 240
    assert data["deezer_album_id"] == 42
    assert result["verifications"]["duration"]["is_valid"] is True  # diff 1 s ≤ 2
    assert result["verifications"]["release_date"]["dates_match"] is True


def test_deezer_enrich_track_async_not_found():
    def handler(request):
        return httpx.Response(200, json={"data": []})

    result = asyncio.run(DeezerAPI().enrich_track_async(_http(handler), "X", "Inconnu"))
    assert result["success"] is False
    assert result["data"] is None


def test_deezer_get_isrc_async():
    def handler(request):
        return httpx.Response(200, json={"data": [_DEEZER_HIT]})

    assert asyncio.run(DeezerAPI().get_isrc_async(_http(handler), "X", "Solo")) == "FRXXX2000001"


def test_deezer_api_error_payload_gives_none():
    def handler(request):
        return httpx.Response(200, json={"error": {"type": "Oauth", "message": "quota"}})

    assert asyncio.run(DeezerAPI().get_isrc_async(_http(handler), "X", "Solo")) is None


# ──────────────────────────────────────────────────────────────────────
# GetSongBPM
# ──────────────────────────────────────────────────────────────────────


def _gsb_fetcher(tmp_path) -> GetSongBPMFetcher:
    return GetSongBPMFetcher(api_key="k", cache_file=str(tmp_path / "gsb_cache.json"))


def test_getsongbpm_fetch_async_success_and_cache(tmp_path):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        assert request.url.params["api_key"] == "k"
        assert request.url.params["lookup"] == "song:Solo artist:X"
        return httpx.Response(
            200,
            json={
                "search": [
                    {
                        "id": "abc",
                        "title": "Solo",
                        "tempo": "220",  # string malgré la doc → cast robuste
                        "key_of": "F#m",
                        "time_sig": "4/4",
                        "open_key": "11m",
                        "artist": {"name": "X", "genres": ["rap"]},
                    }
                ]
            },
        )

    fetcher = _gsb_fetcher(tmp_path)
    http = _http(handler)

    song = asyncio.run(fetcher.fetch_track_bpm_async(http, "X", "Solo"))
    assert song.error is None
    assert song.bpm == 220
    assert song.key == "F#m"
    assert song.mode == "minor"  # déduit du 'm' final
    assert song.time_signature == "4/4"
    assert song.genres == ["rap"]

    # 2ᵉ appel : servi par le cache, aucun nouvel appel HTTP
    again = asyncio.run(fetcher.fetch_track_bpm_async(http, "X", "Solo"))
    assert again.bpm == 220
    assert calls["n"] == 1


def test_getsongbpm_fetch_async_not_found(tmp_path):
    def handler(request):
        return httpx.Response(200, json={"search": []})

    song = asyncio.run(_gsb_fetcher(tmp_path).fetch_track_bpm_async(_http(handler), "X", "Rien"))
    assert song.error is not None
    assert song.bpm is None


def test_getsongbpm_fetch_async_wrong_artist_rejected(tmp_path):
    """L'ancre artiste stricte de `_select_hit` s'applique aussi à la voie async."""

    def handler(request):
        return httpx.Response(
            200,
            json={"search": [{"title": "Solo", "tempo": "120", "artist": {"name": "Autre"}}]},
        )

    song = asyncio.run(_gsb_fetcher(tmp_path).fetch_track_bpm_async(_http(handler), "X", "Solo"))
    assert song.error is not None


# ──────────────────────────────────────────────────────────────────────
# ReccoBeats
# ──────────────────────────────────────────────────────────────────────


def _recco(tmp_path) -> ReccoBeatsIntegratedClient:
    return ReccoBeatsIntegratedClient(cache_file=str(tmp_path / "recco_cache.json"))


def test_recco_get_track_info_async(tmp_path):
    def handler(request):
        if request.url.path == "/v1/track":
            assert request.url.params["ids"] == "SPID123"
            return httpx.Response(
                200,
                json={"content": [{"id": "rb1", "trackTitle": "Solo", "durationMs": 180000}]},
            )
        if request.url.path == "/v1/track/rb1/audio-features":
            return httpx.Response(200, json={"tempo": 100, "key": 5, "mode": 1, "energy": 0.5})
        raise AssertionError(f"URL inattendue: {request.url}")

    result = asyncio.run(_recco(tmp_path).get_track_info_async(_http(handler), "SPID123"))

    assert result["success"] is True
    assert result["source"] == "reccobeats"
    assert result["bpm"] == 100
    assert result["key"] == 5
    assert result["mode"] == 1
    assert result["duration"] == 180
    assert result["musical_key"]  # calculé depuis key+mode


def test_recco_get_track_info_by_isrc_async_picks_most_popular(tmp_path):
    def handler(request):
        if request.url.path == "/v1/track":
            return httpx.Response(
                200,
                json={
                    "content": [
                        {"id": "rb_low", "trackTitle": "Solo", "popularity": 10},
                        {
                            "id": "rb_hi",
                            "trackTitle": "Solo",
                            "popularity": 90,
                            "durationMs": 200000,
                        },
                    ]
                },
            )
        if request.url.path == "/v1/track/rb_hi/audio-features":
            return httpx.Response(200, json={"tempo": 95, "key": 2, "mode": 0})
        raise AssertionError(f"URL inattendue: {request.url}")

    result = asyncio.run(
        _recco(tmp_path).get_track_info_by_isrc_async(_http(handler), "FRXXX2000001")
    )

    assert result["success"] is True
    assert result["source"] == "reccobeats_isrc"
    assert result["id"] == "rb_hi"  # le plus populaire
    assert result["bpm"] == 95
    assert result["duration"] == 200


def test_recco_not_found_async(tmp_path):
    def handler(request):
        return httpx.Response(200, json={"content": []})

    client = _recco(tmp_path)
    assert asyncio.run(client.get_track_info_async(_http(handler), "SPID404")) is None
    # L'échec est mémorisé dans le cache (comme la voie sync)
    assert client.cache["spotify_id::SPID404"]["error"] == "not_found"
