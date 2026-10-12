"""Le brut des sources, conservé (chantier E, 2026-10-12).

Décisions utilisateur : les 2 dernières lectures par requête, compressées,
hors git. Une réponse identique ne fait que rafraîchir la date ; jamais de
secret écrit ; une écriture ratée ne fait jamais échouer l'appel.
"""

import asyncio

import httpx
import pytest

from src.persistence import brut

URL = "https://api.deezer.com/search"


class TestRequete:
    def test_parametres_tries_et_fusionnes(self):
        a = brut.url_sans_secret(URL + "?q=Booba", {"limit": 5})
        b = brut.url_sans_secret(URL, {"limit": 5, "q": "Booba"})
        assert a == b == "https://api.deezer.com/search?limit=5&q=Booba"

    @pytest.mark.parametrize("param", ["api_key", "access_token", "apikey", "client_secret"])
    def test_aucun_secret_n_est_ecrit(self, param):
        assert param not in brut.url_sans_secret(URL, {param: "SECRET", "q": "x"})
        assert "SECRET" not in brut.url_sans_secret(URL + f"?{param}=SECRET&q=x")


class TestLectures:
    def test_une_source_non_activee_n_est_pas_gardee(self):
        brut.consigner("genius", URL, 200, b"{}")
        assert brut.lectures("genius", URL) == []

    def test_identique_rafraichit_different_s_ajoute_deux_au_plus(self):
        brut.consigner("deezer", URL, 200, b'{"v": 1}', {"q": "x"})
        brut.consigner("deezer", URL, 200, b'{"v": 1}', {"q": "x"})
        assert [lu["corps"] for lu in brut.lectures("deezer", URL, {"q": "x"})] == [b'{"v": 1}']
        brut.consigner("deezer", URL, 200, b'{"v": 2}', {"q": "x"})
        brut.consigner("deezer", URL, 200, b'{"v": 3}', {"q": "x"})
        corps = [lu["corps"] for lu in brut.lectures("deezer", URL, {"q": "x"})]
        assert corps == [b'{"v": 3}', b'{"v": 2}']

    def test_compresse_et_comptabilise(self):
        corps = b'{"data": [' + b'{"title": "Ratpi World"},' * 200 + b"{}]}"
        brut.consigner("deezer", URL, 200, corps, {"q": "booba"})
        b = brut.bilan()["deezer"]
        assert b["requetes"] == 1 and b["octets_bruts"] == len(corps)
        assert b["octets"] < len(corps) // 5

    def test_une_ecriture_ratee_ne_leve_jamais(self, monkeypatch, tmp_path):
        brut.fermer()
        fichier = tmp_path / "pas_un_dossier"
        fichier.write_text("x")
        monkeypatch.setattr(brut, "CHEMIN", fichier / "brut.db")
        brut.consigner("deezer", URL, 200, b"{}")  # ne lève pas


class TestPointsDePassage:
    def test_deezer_sync(self, monkeypatch):
        from src.api.deezer_api import DeezerAPI

        class _Rep:
            status_code = 200
            headers = {}
            content = b'{"data": []}'

            def raise_for_status(self):
                pass

            def json(self):
                return {"data": []}

        api = DeezerAPI()
        monkeypatch.setattr(api.session, "get", lambda url, **k: _Rep())
        api._make_request("search", {"q": 'artist:"Booba"'})
        assert brut.lectures("deezer", URL, {"q": 'artist:"Booba"'})

    def test_session_async_partagee(self):
        from src.api.async_http import AsyncHttpSession

        def repondre(request):
            return httpx.Response(200, content=b'{"id": 1}')

        session = AsyncHttpSession(min_delay=0, transport=httpx.MockTransport(repondre))

        async def appel():
            try:
                await session.get("https://api.deezer.com/track/1")
            finally:
                await session.aclose()

        asyncio.run(appel())
        assert brut.lectures("deezer", "https://api.deezer.com/track/1")[0]["corps"] == b'{"id": 1}'


class TestRejeu:
    """Lot 2 : servir les sources depuis le brut, sans réseau."""

    @staticmethod
    def _session_qui_interdit_le_reseau():
        from src.api.async_http import AsyncHttpSession

        def interdit(request):
            raise AssertionError(f"appel réseau pendant un rejeu : {request.url}")

        return AsyncHttpSession(min_delay=0, transport=httpx.MockTransport(interdit))

    def test_session_async_servie_sans_reseau(self):
        brut.consigner("deezer", "https://api.deezer.com/track/1", 200, b'{"id": 1}')
        session = self._session_qui_interdit_le_reseau()

        async def appel():
            return await session.get("https://api.deezer.com/track/1")

        with brut.rejeu() as compte:
            reponse = asyncio.run(appel())
        assert reponse.json() == {"id": 1}
        assert compte["servies"] == 1 and compte["absentes"] == []

    def test_absente_ou_source_non_gardee_jamais_le_reseau(self):
        session = self._session_qui_interdit_le_reseau()

        async def appel(url):
            return await session.get(url)

        with brut.rejeu() as compte:
            for url in ("https://api.deezer.com/track/2", "https://api.genius.com/songs/1"):
                with pytest.raises(httpx.ConnectError):
                    asyncio.run(appel(url))
        assert len(compte["absentes"]) == 2

    def test_shim_requests_et_session_deezer(self, monkeypatch):
        import requests

        from src.api.deezer_api import DeezerAPI
        from src.observability.source_usage import requests_get

        brut.consigner("deezer", URL, 200, b'{"data": [1]}', {"q": "x"})

        def interdit(*a, **k):
            raise AssertionError("appel réseau pendant un rejeu")

        monkeypatch.setattr(requests, "get", interdit)
        api = DeezerAPI()
        monkeypatch.setattr(api.session, "get", interdit)
        with brut.rejeu():
            assert requests_get("deezer", URL, params={"q": "x"}).json() == {"data": [1]}
            assert api._make_request("search", {"q": "x"}) == {"data": [1]}
            with pytest.raises(requests.ConnectionError):
                requests_get("deezer", URL, params={"q": "absent"})
        assert not brut.rejeu_actif()


def test_une_detection_enregistree_puis_rejouee_donne_le_meme_bilan(data_manager):
    """LE contrat du chantier E : ce qu'un run a lu, un rejeu le relit à
    l'identique, sans réseau — une règle changée se juge hors ligne."""
    from src.api.async_http import AsyncHttpSession
    from src.api.deezer_api import DeezerAPI
    from src.models import Artist
    from src.services import ecarts_deezer

    artiste = Artist(name="Booba")
    artiste.id = data_manager.save_artist(artiste)
    reponses = {
        "/artist/390/albums": {"data": [{"id": 10, "title": "Ultra"}], "total": 1},
        "/album/10": {
            "id": 10,
            "title": "Ultra",
            "record_type": "album",
            "artist": {"id": 390, "name": "Booba"},
            "release_date": "2021-03-05",
        },
        "/album/10/tracks": {
            "data": [
                {
                    "id": 100,
                    "title": "Mona Lisa",
                    "title_short": "Mona Lisa",
                    "isrc": "FR0000000001",
                    "duration": 200,
                    "track_position": 1,
                    "artist": {"id": 390, "name": "Booba"},
                }
            ],
            "total": 1,
        },
    }

    def servir(request):
        import json as _json

        corps = reponses.get(request.url.path, {"data": [], "total": 0})
        return httpx.Response(200, content=_json.dumps(corps).encode())

    def interdit(request):
        raise AssertionError(f"appel réseau pendant un rejeu : {request.url}")

    def detecter(transport):
        http = AsyncHttpSession(min_delay=0, transport=httpx.MockTransport(transport))

        async def run():
            try:
                return await ecarts_deezer.detecter_async(
                    DeezerAPI(), http, data_manager, artiste, deezer_id=390
                )
            finally:
                await http.aclose()

        return asyncio.run(run())

    enregistre = detecter(servir)
    with brut.rejeu() as compte:
        rejoue = detecter(interdit)
    assert compte["absentes"] == [] and compte["servies"] >= 3

    def resume(b):
        return sorted((e.nature, e.titre, e.piste.id) for e in b.ecarts)

    assert resume(enregistre) == resume(rejoue) and resume(rejoue)


def test_les_pages_genius_sont_gardees_puis_rejouees():
    """Lot 3 : une page crawlée d'une source activée (`genius_scrape`) est
    gardée ; en rejeu, elle est servie sans navigateur."""
    from src.scrapers.crawl4ai_scraper_base import CrawlAIScraperBase

    class _Stub(CrawlAIScraperBase):
        def __init__(self):
            super().__init__(headless=True)
            self.crawls = 0

        async def _acrawl_page_reseau(self, url, *args):
            self.crawls += 1
            return "<html>Ratpi World</html>"

    url = "https://genius.com/Booba-ratpi-world-lyrics"
    stub = _Stub()
    assert stub._crawl_page(url) == (None, "<html>Ratpi World</html>")
    assert brut.lectures("genius_scrape", url)[0]["corps"] == b"<html>Ratpi World</html>"
    with brut.rejeu() as compte:
        assert stub._crawl_page(url) == (None, "<html>Ratpi World</html>")
        assert stub._crawl_page(url + "-absente") == (None, None)
    assert stub.crawls == 1
    assert compte["servies"] == 1 and len(compte["absentes"]) == 1


def test_une_session_de_bibliotheque_est_gardee_puis_rejouee(monkeypatch):
    """lyricsgenius garde sa propre `requests.Session` : `garder_session` la
    branche sur le brut — gardée en temps normal, servie en rejeu."""
    import requests

    session = requests.Session()
    appels = []

    def reseau(method, url, *args, **kwargs):
        appels.append(url)
        reponse = requests.models.Response()
        reponse.status_code, reponse._content = 200, b'{"song": 1}'
        return reponse

    monkeypatch.setattr(session, "request", reseau)
    brut.garder_session(session, "genius_api")
    url = "https://api.genius.com/songs/1"
    assert session.request("GET", url).json() == {"song": 1}
    with brut.rejeu():
        assert session.request("GET", url).json() == {"song": 1}
        with pytest.raises(requests.ConnectionError):
            session.request("GET", url + "0")
    assert appels == [url]
