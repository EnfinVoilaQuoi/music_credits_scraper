"""Le brut de Musixmatch, Discogs et YouTube Data API (chantier E, lot 6).

Musixmatch passe par l'`AsyncHttpSession` (qui garde déjà le brut) : il lui
manquait son domaine, une clé de requête stable (l'horodatage `t` change à
chaque appel) et l'exclusion de `token.get`, dont la RÉPONSE est le jeton.
Discogs et YouTube Data API passent par leur bibliothèque : le brut se branche
sur le fetcher de `discogs_client`, et sur l'exécution des requêtes Google.
"""

import asyncio
import json
import sqlite3
from contextlib import closing

import httpx
import pytest
import requests

from src.persistence import brut

MXM = "https://apic.musixmatch.com/ws/1.1"


def _tout_le_brut() -> bytes:
    with closing(sqlite3.connect(str(brut.CHEMIN))) as conn:
        lignes = conn.execute("SELECT source, requete, corps FROM brut").fetchall()
    return b"".join(s.encode() + r.encode() + bytes(c) for s, r, c in lignes)


class TestMusixmatch:
    @staticmethod
    def _session(repondre):
        from src.api.async_http import AsyncHttpSession

        return AsyncHttpSession(min_delay=0, transport=httpx.MockTransport(repondre))

    @staticmethod
    def _get(session, action, params):
        async def appel():
            try:
                return await session.get(f"{MXM}/{action}", params=params)
            finally:
                await session.aclose()

        return asyncio.run(appel())

    def test_gardee_sans_jeton_ni_horodatage_puis_rejouee(self):
        corps = b'{"message": {"body": {"macro_calls": {}}}}'
        params = {"q_track": "Ratpi World", "usertoken": "JETON", "t": "1700000000000"}
        self._get(
            self._session(lambda r: httpx.Response(200, content=corps)),
            "macro.subtitles.get",
            params,
        )
        lus = brut.lectures(
            "musixmatch", f"{MXM}/macro.subtitles.get", {**params, "t": "1800000000000"}
        )
        assert [lu["corps"] for lu in lus] == [corps]
        assert b"JETON" not in _tout_le_brut() and b"1700000000000" not in _tout_le_brut()

        def interdit(requete):
            raise AssertionError(f"appel réseau pendant un rejeu : {requete.url}")

        with brut.rejeu() as compte:
            rejoue = self._get(self._session(interdit), "macro.subtitles.get", {**params, "t": "9"})
        assert rejoue.content == corps and compte["servies"] == 1

    def test_la_reponse_de_token_get_n_est_jamais_gardee(self):
        corps = b'{"message": {"body": {"user_token": "JETON-SECRET"}}}'
        self._get(
            self._session(lambda r: httpx.Response(200, content=corps)), "token.get", {"t": "1"}
        )
        assert brut.lectures("musixmatch", f"{MXM}/token.get") == []
        assert b"JETON-SECRET" not in _tout_le_brut()


class TestDiscogs:
    URL = "https://api.discogs.com/releases/249504"

    @staticmethod
    def _client(monkeypatch, repondre):
        from discogs_client import fetchers

        from src.api.discogs_api import DiscogsClient

        monkeypatch.setattr(fetchers.requests, "request", repondre)
        return DiscogsClient(user_token="JETON-DISCOGS")

    def test_gardee_sans_jeton_puis_rejouee(self, monkeypatch):
        corps = json.dumps({"id": 249504, "title": "Panthéon"}).encode()
        appels = []

        def reseau(method, url, **kwargs):
            appels.append((url, kwargs.get("params")))
            reponse = requests.models.Response()
            reponse.status_code, reponse._content = 200, corps
            return reponse

        client = self._client(monkeypatch, reseau)
        assert client.client.release(249504).title == "Panthéon"
        assert [lu["corps"] for lu in brut.lectures("discogs", self.URL)] == [corps]
        assert appels[0][1] == {"token": "JETON-DISCOGS"}  # le jeton part bien…
        assert b"JETON-DISCOGS" not in _tout_le_brut()  # … mais n'est jamais gardé

        with brut.rejeu() as compte:
            assert self._client(monkeypatch, reseau).client.release(249504).title == "Panthéon"
            with pytest.raises(requests.ConnectionError):
                self._client(monkeypatch, reseau).client.release(1).refresh()
        assert len(appels) == 1 and compte["servies"] == 1

    def test_un_404_est_garde_avec_son_statut(self, monkeypatch):
        def absent(method, url, **kwargs):
            reponse = requests.models.Response()
            reponse.status_code, reponse._content = 404, b'{"message": "Release not found."}'
            return reponse

        from discogs_client.exceptions import HTTPError

        with pytest.raises(HTTPError):
            self._client(monkeypatch, absent).client.release(2).refresh()
        lus = brut.lectures("discogs", "https://api.discogs.com/releases/2")
        assert [lu["statut"] for lu in lus] == [404]


class TestYouTubeData:
    class _Requete:
        def __init__(self, uri, reponse=None, erreur=None):
            self.uri, self.reponse, self.erreur, self.executions = uri, reponse, erreur, 0

        def execute(self):
            self.executions += 1
            if self.erreur:
                raise self.erreur
            return self.reponse

    URI = "https://youtube.googleapis.com/youtube/v3/videos?part=statistics&id=a%2Cb&key=CLE-YT&alt=json"

    def test_gardee_sans_cle_puis_rejouee(self):
        from src.api import ytmusic_brut

        reponse = {"items": [{"id": "a", "statistics": {"viewCount": "12"}}]}
        requete = self._Requete(self.URI, reponse)
        assert ytmusic_brut.executer_youtube_data(requete) == reponse
        assert b"CLE-YT" not in _tout_le_brut()
        with brut.rejeu() as compte:
            assert ytmusic_brut.executer_youtube_data(requete) == reponse
            with pytest.raises(OSError):
                ytmusic_brut.executer_youtube_data(self._Requete(self.URI.replace("a%2Cb", "c")))
        assert requete.executions == 1 and compte["servies"] == 1

    def test_un_refus_est_garde_avec_son_statut(self):
        from googleapiclient.errors import HttpError

        from src.api import ytmusic_brut

        refus = HttpError(
            type("R", (), {"status": 403, "reason": "quota"})(), b'{"error": "quotaExceeded"}'
        )
        with pytest.raises(HttpError):
            ytmusic_brut.executer_youtube_data(self._Requete(self.URI, erreur=refus))
        lus = brut.lectures("youtube_data", self.URI)
        assert [(lu["statut"], lu["corps"]) for lu in lus] == [(403, b'{"error": "quotaExceeded"}')]
