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
