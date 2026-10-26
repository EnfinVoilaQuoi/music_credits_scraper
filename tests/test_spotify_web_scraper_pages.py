"""Le scraper Spotify web sur des pages RÉELLES, sans navigateur (2026-10-14).

Une fausse session sert les fixtures capturées (`tests/fixtures/spotify_web/`)
à la place de patchright : le verdict de chaque `afetch_*` (page vide,
structure changée, page incomplète) se teste hors ligne.
"""

import asyncio
import logging
from pathlib import Path

import pytest

from src.scrapers.spotify_web_scraper import SpotifyWebScraper

_FIXTURES = Path(__file__).parent / "fixtures" / "spotify_web"


def _page(nom: str) -> str:
    chemin = _FIXTURES / nom
    if not chemin.exists():
        pytest.skip(f"fixture absente : {chemin}")
    return chemin.read_text(encoding="utf-8")


class _Session:
    """Rend `html` à chaque `fetch`, et note les URL demandées."""

    def __init__(self, html):
        self.html, self.urls, self.options = html, [], []

    async def fetch(self, url, **options):
        self.urls.append(url)
        self.options.append(options)
        return self.html


def _lancer(coro):
    return asyncio.run(coro)


@pytest.fixture
def scraper():
    return SpotifyWebScraper(headless=True)


class TestAlbum:
    def test_page_incomplete_rend_ce_qu_elle_a_et_le_dit(self, scraper, caplog):
        sess = _Session(_page("album_incomplet.html"))
        with caplog.at_level(logging.WARNING):
            album = _lancer(scraper.afetch_album(sess, "41zFwISc8IsYEATo4qi1mU"))
        assert album["title"] == "Civilisation Édition Ultime"
        assert (len(album["tracks"]), album["announced"]) == (23, 25)
        assert album["track_ids"] == [tid for tid, _ in album["tracks"]]
        assert "23 piste(s) rendues sur 25" in caplog.text
        assert sess.urls == [scraper.album_url("41zFwISc8IsYEATo4qi1mU")]

    def test_page_vide(self, scraper):
        assert _lancer(scraper.afetch_album(_Session(None), "X")) is None

    def test_la_page_album_demande_une_fenetre_haute(self, scraper):
        """2026-10-15 : en 900 px, 23 pistes sur 25-26 ; en 12 000 px, toutes
        (*Futur 2.0 Deluxe* 25/25, *OCHO Deluxe* 26/26, vérifié sur la vraie
        session)."""
        sess = _Session(_page("album_incomplet.html"))
        _lancer(scraper.afetch_album(sess, "X"))
        assert sess.options[0]["viewport"]["height"] >= 10_000

    def test_page_sans_tracklist(self, scraper):
        assert _lancer(scraper.afetch_album(_Session("<html></html>"), "X")) is None


class TestArtiste:
    def test_page_artiste(self, scraper):
        data = _lancer(scraper.afetch_artist(_Session(_page("artist.html")), "A"))
        assert data["name"] and data["playcounts"] and data["albums"]

    def test_page_sans_nom_ni_titre(self, scraper):
        assert _lancer(scraper.afetch_artist(_Session("<html></html>"), "A")) is None

    def test_page_vide(self, scraper):
        assert _lancer(scraper.afetch_artist(_Session(None), "A")) is None


class TestTitre:
    def test_page_titre_rend_compteurs_et_duree(self, scraper):
        html = _page("track.html")
        track_id = "3EDDunSmj8RbiVGU0Qr0M8"  # sentinelle de capture_fixtures
        data = _lancer(scraper.afetch_track(_Session(html), track_id))
        assert data["playcounts"].get(track_id)
        assert data["durations"].get(track_id)

    def test_page_sans_compteur(self, scraper):
        assert _lancer(scraper.afetch_track(_Session("<html></html>"), "T")) is None

    def test_la_page_titre_attend_l_en_tete_pas_le_compteur(self, scraper):
        """Spotify n'affiche aucun compteur sur certaines pages (*VULTURES* de
        ¥$, 2026-10-26) : attendre `playcount` coûtait le délai entier."""
        sess = _Session(_page("track.html"))
        _lancer(scraper.afetch_track(sess, "3EDDunSmj8RbiVGU0Qr0M8"))
        assert sess.options[0]["wait_for"] == 'css:[data-testid="entityTitle"]'

    def test_en_tete_sans_compteur_affiche_la_duree_reste_lue(self, scraper, caplog):
        """« FIELD TRIP » : en-tête sans compteur. La durée se lit, le message
        est une INFO — rien à corriger chez nous."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(_page("track.html"), "html.parser")
        for noeud in soup.select('[data-testid="playcount"]'):
            noeud.decompose()
        track_id = "3EDDunSmj8RbiVGU0Qr0M8"
        with caplog.at_level(logging.INFO):
            data = _lancer(scraper.afetch_track(_Session(str(soup)), track_id))
        assert track_id not in data["playcounts"]
        assert data["durations"].get(track_id)
        assert "n'affiche pas de compteur" in caplog.text
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    def test_identite_d_une_page_titre(self, scraper):
        identite = _lancer(
            scraper.afetch_track_identity(
                _Session(_page("track_remix.html")), "1W4mPC2NDpoKeolyEckBnm"
            )
        )
        assert identite and identite.get("artists")

    def test_identite_page_sans_entete(self, scraper):
        assert _lancer(scraper.afetch_track_identity(_Session("<html></html>"), "T")) is None
