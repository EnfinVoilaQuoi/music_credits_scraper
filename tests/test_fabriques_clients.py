"""Les fabriques de clients : ce qu'elles construisent, avec quoi, et le
wrapper `GeniusAPI.get_artist_songs`.

Aucun réseau : les constructeurs tiers (`lyricsgenius.Genius`,
`discogs_client.Client`) sont remplacés au point d'import, on vérifie les
ARGUMENTS qu'ils reçoivent — c'est là que vivent les réglages (timeouts,
retries, token) qu'un test d'intégration ne verrait qu'en production.
"""

import pytest

from src.api import discogs_api, genius_api
from src.models import Artist
from src.services import credits


class TestGeniusAPI:
    def test_sans_cle_refuse(self, monkeypatch):
        monkeypatch.setattr(genius_api, "GENIUS_API_KEY", "")
        with pytest.raises(ValueError):
            genius_api.GeniusAPI()

    def test_client_configure_depuis_settings(self, monkeypatch):
        vus = {}

        class _Genius:
            def __init__(self, key, **kw):
                vus.update(key=key, **kw)

        monkeypatch.setattr(genius_api, "GENIUS_API_KEY", "cle")
        monkeypatch.setattr(genius_api, "Genius", _Genius)
        api = genius_api.GeniusAPI()
        assert vus["key"] == "cle" and set(vus) >= {"timeout", "sleep_time", "retries"}
        assert api.genius.verbose is False and api.genius.skip_non_songs is True


class TestGetArtistSongs:
    @pytest.fixture
    def api(self, monkeypatch):
        inst = genius_api.GeniusAPI.__new__(genius_api.GeniusAPI)
        monkeypatch.setattr(genius_api.time, "sleep", lambda *_: None)
        return inst

    def test_sans_id_genius_rend_vide(self, api):
        assert api.get_artist_songs(Artist(name="X")) == []

    def test_prefill_appele_seulement_si_demande(self, api, monkeypatch):
        vus = []
        monkeypatch.setattr(api, "_get_artist_songs_manual", lambda *a, **k: ["t1", "t2"])
        monkeypatch.setattr(
            api,
            "_prefill_via_song_api",
            lambda tracks, known_genius_ids=None: vus.append(known_genius_ids),
        )
        art = Artist(name="X", genius_id=1)
        assert api.get_artist_songs(art, prefill=False) == ["t1", "t2"] and vus == []
        api.get_artist_songs(art, known_genius_ids={5})
        assert vus == [{5}]

    def test_bug_d_orchestration_rend_le_partiel(self, api, monkeypatch):
        monkeypatch.setattr(api, "_get_artist_songs_manual", lambda *a, **k: ["t1"])

        def casse(*a, **k):
            raise RuntimeError("bug")

        monkeypatch.setattr(api, "_prefill_via_song_api", casse)
        assert api.get_artist_songs(Artist(name="X", genius_id=1)) == ["t1"]


class TestDiscogsClient:
    def test_avec_et_sans_token(self, monkeypatch):
        vus = []
        monkeypatch.setattr(
            discogs_api.discogs_client, "Client", lambda ua, **kw: vus.append((ua, kw)) or "client"
        )
        discogs_api.DiscogsClient(user_token="tok")
        discogs_api.DiscogsClient(user_token=None)
        assert vus == [
            ("MusicCreditsScraper/1.0", {"user_token": "tok"}),
            ("MusicCreditsScraper/1.0", {}),
        ]

    def test_token_lu_sous_les_deux_noms(self, monkeypatch):
        monkeypatch.delenv("DISCOGS_TOKEN", raising=False)
        monkeypatch.setenv("DISCOGS_USER_TOKEN", "b")
        assert discogs_api.token_discogs() == "b"
        monkeypatch.setenv("DISCOGS_TOKEN", "a")
        assert discogs_api.token_discogs() == "a"

    def test_constructeur_tiers_qui_leve_est_releve(self, monkeypatch):
        def casse(*a, **k):
            raise RuntimeError("oauth")

        monkeypatch.setattr(discogs_api.discogs_client, "Client", casse)
        with pytest.raises(RuntimeError):
            discogs_api.DiscogsClient(user_token="x")


class TestFabriquesDuServiceCredits:
    def test_genius_scraper_headless(self, monkeypatch):
        vus = {}
        monkeypatch.setattr(
            "src.scrapers.genius_scraper_v3.GeniusScraperV3",
            lambda headless=False: vus.update(headless=headless) or "scraper",
        )
        assert credits._genius_scraper() == "scraper" and vus == {"headless": True}

    def test_discogs_client_avec_le_token(self, monkeypatch):
        vus = {}
        monkeypatch.setattr("src.api.discogs_api.token_discogs", lambda: "tok")
        monkeypatch.setattr(
            "src.api.discogs_api.DiscogsClient",
            lambda user_token=None: vus.update(token=user_token) or "client",
        )
        assert credits._discogs_client() == "client" and vus == {"token": "tok"}

    def test_lyrics_provider_recoit_les_options(self, monkeypatch):
        vus = {}
        monkeypatch.setattr(
            "src.enrichment.providers.lyrics.LyricsProvider",
            lambda **kw: vus.update(kw) or "provider",
        )
        o = credits.OptionsCredits(sync_lrclib=False, sync_musixmatch=True, paroles_ytm=False)
        assert credits._lyrics_provider(o) == "provider"
        assert vus == {
            "sync_lrclib": False,
            "sync_ytm": True,
            "sync_musixmatch": True,
            "lyrics_ytm": False,
        }
        assert credits.Clients().genius is credits._genius_scraper
