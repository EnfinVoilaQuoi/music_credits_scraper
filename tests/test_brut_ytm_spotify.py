"""Le brut de YouTube Music et des pages web Spotify (chantier E, lot 5).

Ces deux sources écrivent les durées vidéo, les vues YTM et les compteurs
Spotify ; sans leur brut, rien ne se rejouait ni ne se prouvait. YTM : chaque
réponse de la session `ytmusicapi`, vides et bridées comprises ; Spotify : le
HTML rendu de chaque page lue par la session navigateur.
"""

import asyncio
import json
import sqlite3
from contextlib import closing

import pytest
import requests
from ytmusicapi import YTMusic

from src.api import ytmusic_brut
from src.persistence import brut

ACCUEIL = "https://music.youtube.com"
BROWSE = "https://music.youtube.com/youtubei/v1/browse"
# Factice : la vraie clé du client web (publique, servie à tout visiteur et
# embarquée par `ytmusicapi`) déclenchait l'alerte de secret de GitHub.
CLE_API = "cle-de-test-du-client-web"


def _reponse(statut: int, corps: bytes) -> requests.Response:
    reponse = requests.models.Response()
    reponse.status_code, reponse._content = statut, corps
    return reponse


class _Reseau:
    """Le transport de la session : la page d'accueil (identifiant de
    visiteur), puis les réponses youtubei données, dans l'ordre."""

    def __init__(self, *reponses: tuple[int, bytes]):
        self.reponses = list(reponses)
        self.appels: list[str] = []

    def __call__(self, method, url, *args, **kwargs):
        self.appels.append(f"{method} {url}")
        if url.rstrip("/") == ACCUEIL:
            return _reponse(
                200, f'ytcfg.set({{"VISITOR_DATA": "v", "KEY": "{CLE_API}"}});'.encode()
            )
        return _reponse(*self.reponses.pop(0))


def _client(reseau: _Reseau) -> YTMusic:
    session = requests.Session()
    session.request = reseau
    return YTMusic(requests_session=ytmusic_brut.garder(session))


def _toutes_les_lignes() -> list[tuple]:
    with closing(sqlite3.connect(str(brut.CHEMIN))) as conn:
        return conn.execute("SELECT source, requete, statut FROM brut").fetchall()


class TestCleDeRequete:
    def test_le_contexte_ne_compte_pas(self):
        a = ytmusic_brut.cle_requete({"browseId": "MPREb_1", "context": {"client": {"v": "1.2"}}})
        b = ytmusic_brut.cle_requete({"browseId": "MPREb_1", "context": {"client": {"v": "1.3"}}})
        assert a == b == {"browseId": "MPREb_1"}

    def test_valeur_imbriquee_stable(self):
        a = ytmusic_brut.cle_requete({"cfg": {"b": 1, "a": 2}, "query": "Booba"})
        b = ytmusic_brut.cle_requete({"query": "Booba", "cfg": {"a": 2, "b": 1}})
        assert a == b and a["cfg"] == '{"a": 2, "b": 1}'


class TestYouTubeMusic:
    def test_une_reponse_est_gardee_puis_rejouee_hors_ligne(self):
        corps = json.dumps({"contents": {"titre": "Ratpi World"}}).encode()
        reseau = _Reseau((200, corps))
        assert _client(reseau)._send_request("browse", {"browseId": "MPREb_1"}) == json.loads(corps)
        lus = brut.lectures("ytmusic", BROWSE, {"alt": "json", "browseId": "MPREb_1"})
        assert [(lu["statut"], lu["corps"]) for lu in lus] == [(200, corps)]

        appels = list(reseau.appels)
        with brut.rejeu() as compte:
            # Client NEUF : son identifiant de visiteur vient aussi du rejeu.
            yt = _client(reseau)
            assert yt._send_request("browse", {"browseId": "MPREb_1"}) == json.loads(corps)
            with pytest.raises(requests.ConnectionError):
                yt._send_request("browse", {"browseId": "MPREb_2"})
        assert reseau.appels == appels  # aucun appel réseau en rejeu
        assert compte["servies"] == 1 and len(compte["absentes"]) == 1

    def test_ni_secret_ni_page_d_accueil(self):
        reseau = _Reseau((200, b'{"a": 1}'))
        yt = _client(reseau)
        yt._send_request("next", {"videoId": "x"}, "&ctoken=JETON&continuation=JETON")
        lignes = _toutes_les_lignes()
        assert [(s, st) for s, _, st in lignes] == [("ytmusic", 200)]
        requete = lignes[0][1]
        assert "continuation=JETON" in requete and "ctoken" not in requete
        with closing(sqlite3.connect(str(brut.CHEMIN))) as conn:
            tout = b"".join(bytes(str(r), "utf-8") for r in conn.execute("SELECT * FROM brut"))
        assert CLE_API.encode() not in tout and b"VISITOR_DATA" not in tout

    def test_une_reponse_vide_est_gardee_et_reste_bridee(self):
        """YTM bride par une réponse VIDE : `ytmusicapi` lève `JSONDecodeError`,
        que `YTMusicAPI` traite en bridage (`throttled`). Elle est gardée avec
        son statut — preuve que la source n'a pas répondu — et, rejouée, se
        lit pareil : le rejeu n'invente pas de réponse."""
        from src.api.ytmusic_api import YTMusicAPI

        api = object.__new__(YTMusicAPI)
        api.yt = _client(_Reseau((200, b"")))
        assert api.get_artist_channel_candidates("Booba") == []
        ((source, requete, statut),) = _toutes_les_lignes()
        assert (source, statut) == ("ytmusic", 200) and "query=Booba" in requete
        assert brut.bridee(statut, brut.lectures("ytmusic", requete)[0]["corps"])
        reseau = _Reseau()  # aucune réponse : un appel réseau lèverait
        with brut.rejeu() as compte:
            api.yt = _client(reseau)
            assert api.get_artist_channel_candidates("Booba") == []
        assert compte["servies"] == 1 and reseau.appels == []

    def test_une_lecture_bridee_ne_masque_pas_la_bonne(self):
        reseau = _Reseau((200, b'{"bon": 1}'), (429, b'{"error": {"message": "trop"}}'))
        yt = _client(reseau)
        yt._send_request("browse", {"browseId": "MPREb_1"})
        with pytest.raises(Exception):  # noqa: B017 — YTMusicServerError
            yt._send_request("browse", {"browseId": "MPREb_1"})
        lus = brut.lectures("ytmusic", BROWSE, {"alt": "json", "browseId": "MPREb_1"})
        assert [lu["statut"] for lu in lus] == [429, 200]
        with brut.rejeu():
            assert _client(reseau)._send_request("browse", {"browseId": "MPREb_1"}) == {"bon": 1}


@pytest.mark.parametrize(
    ("statut", "corps", "attendu"),
    [(200, b"", True), (200, b"  ", True), (429, b"x", True), (503, b"x", True)]
    + [(200, b"{}", False), (404, b"{}", False), (None, b"<html>", False)],
)
def test_bridee(statut, corps, attendu):
    assert brut.bridee(statut, corps) is attendu


class TestPagesSpotify:
    URL = "https://open.spotify.com/intl-fr/track/4uLU6hMCjMI75M1A2tKUQC"
    HTML = '<html><span data-testid="playcount">1 234 567</span></html>'

    class _Page:
        def __init__(self, html):
            self.html, self.vues = html, 0

        async def goto(self, url, **kwargs):
            self.vues += 1
            return type("R", (), {"status": 200})()

        async def wait_for_selector(self, *a, **k):
            return None

        async def evaluate(self, *a, **k):
            return None

        async def wait_for_timeout(self, *a, **k):
            return None

        async def content(self):
            return self.html

    def test_la_page_rendue_est_gardee_puis_rejouee_sans_navigateur(self, monkeypatch):
        from src.scrapers.crawl4ai_scraper_base import _BrowserSession
        from src.scrapers.spotify_web_scraper import SpotifyWebScraper

        scraper = SpotifyWebScraper()
        page = self._Page(self.HTML)
        sess = _BrowserSession(scraper, page)
        assert asyncio.run(sess.fetch(self.URL, wait_for="css:x")) == self.HTML
        lus = brut.lectures("spotify_web", self.URL)
        assert [(lu["statut"], lu["corps"]) for lu in lus] == [(200, self.HTML.encode())]

        def _aucun_navigateur(*a, **k):
            raise AssertionError("navigateur lancé en rejeu")

        monkeypatch.setattr(scraper, "_launch_kwargs", _aucun_navigateur)

        async def _rejouer():
            async with scraper.session() as rejoue:
                return await rejoue.fetch(self.URL), await rejoue.fetch(self.URL + "x")

        with brut.rejeu() as compte:
            assert asyncio.run(_rejouer()) == (self.HTML, None)
        assert page.vues == 1
        assert compte["servies"] == 1 and len(compte["absentes"]) == 1

    def test_le_temps_est_compte_par_type_de_page_et_par_phase(self):
        """2026-10-31 : la latence des pages Spotify a triplé en cinq jours sans
        que rien ne dise où ; chaque phase est chronométrée, par type de page,
        et une attente de sélecteur expirée est comptée."""
        from src.scrapers.crawl4ai_scraper_base import PatchrightError, _BrowserSession
        from src.scrapers.spotify_web_scraper import SpotifyWebScraper

        class _SansCompteur(self._Page):
            async def wait_for_selector(self, *a, **k):
                raise PatchrightError("timeout")

        scraper = SpotifyWebScraper()
        asyncio.run(
            _BrowserSession(scraper, self._Page(self.HTML)).fetch(self.URL, wait_for="css:x")
        )
        album = "https://open.spotify.com/album/1"
        asyncio.run(
            _BrowserSession(scraper, _SansCompteur(self.HTML)).fetch(
                album, wait_for="css:x", js_before_wait="1", delay_before_return=0.01
            )
        )
        c = scraper.chrono
        assert c.pages == {"track": 1, "album": 1} and c.delais == {"album": 1}
        assert set(c.phases["track"]) == {"goto", "attente", "html", "brut"}
        assert set(c.phases["album"]) == {"goto", "attente", "script", "pause", "html", "brut"}
        ligne_album, ligne_titre = c.resume()
        assert (
            ligne_album.startswith("album : 1 page(s)") and "1 attente(s) expirée(s)" in ligne_album
        )
        assert ligne_titre.startswith("track : 1 page(s)") and "expirée" not in ligne_titre

    def test_type_de_page(self):
        from src.scrapers.crawl4ai_scraper_base import type_de_page

        assert type_de_page(self.URL) == "track"
        assert type_de_page("https://open.spotify.com/artist/x") == "artist"
        assert type_de_page("https://open.spotify.com/") == "?"
