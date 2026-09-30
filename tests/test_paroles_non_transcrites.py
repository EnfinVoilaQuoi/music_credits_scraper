"""Pages Genius « Lyrics for this song have yet to be transcribed » (2026-09-29).

Une trentaine de pages (Django : l'album à paraître ; B.B. Jacques : des titres
provisoires) rendaient une chaîne vide lue comme un ÉCHEC : re-crawlées à chaque
run avec 12 s de timeout, comptées en échec. Ce n'est ni un instrumental (rien
n'est constaté à ce sujet) ni un inédit (la raison « unreleased » de Genius ne
le prouve pas) : c'est un constat DATÉ, relu au bout de 30 jours — les paroles
arrivent souvent avec la sortie.

Fixture réelle : B.B. Jacques — Thankful, capturée le 2026-09-30
(`scripts/capture_fixtures.py --only genius_non_transcrites_page`).
"""

from datetime import datetime, timedelta

import pytest
from bs4 import BeautifulSoup

from src.gui import helpers
from src.models import Artist, Track
from src.models.track import RELIRE_NON_TRANSCRITES, SOURCE_NON_TRANSCRITES
from src.scrapers.genius_scraper_v3 import GeniusScraperV3
from src.services import credits
from src.services.runtime import Hooks, Manque, est_manquant
from tests.conftest import load_fixture
from tests.test_services_credits import _clients, _rt, _track


@pytest.fixture(scope="module")
def scraper():
    return GeniusScraperV3.__new__(GeniusScraperV3)


@pytest.fixture(scope="module")
def page_non_transcrite():
    return load_fixture("genius/non_transcrites_page.html")


def _constatee(il_y_a=timedelta(days=1)):
    t = Track(title="T", artist=None)
    t.lyrics.source = SOURCE_NON_TRANSCRITES
    t.lyrics.scraped_at = datetime.now() - il_y_a
    return t


class TestDetectionPage:
    def test_placeholder_reconnu(self, scraper, page_non_transcrite):
        assert scraper._non_transcrites_bs4(BeautifulSoup(page_non_transcrite, "html.parser"))

    def test_ce_n_est_pas_un_instrumental(self, scraper, page_non_transcrite):
        soup = BeautifulSoup(page_non_transcrite, "html.parser")
        assert not scraper._is_instrumental_bs4(soup)

    def test_une_page_instrumentale_n_est_pas_non_transcrite(self, scraper):
        page = load_fixture("genius/instrumental_page.html")
        assert not scraper._non_transcrites_bs4(BeautifulSoup(page, "html.parser"))

    def test_une_page_a_paroles_non_plus(self, scraper):
        page = load_fixture("genius/song_page.html")
        assert not scraper._non_transcrites_bs4(BeautifulSoup(page, "html.parser"))

    def test_apply_date_le_constat(self, scraper, page_non_transcrite):
        t = Track(title="Thankful", artist=None)
        assert scraper._apply_lyrics_from_html(page_non_transcrite, t) == ""
        assert t.lyrics.source == SOURCE_NON_TRANSCRITES
        assert t.lyrics.scraped_at is not None
        assert t.lyrics.text is None and t.lyrics.instrumental is None
        assert t.lyrics.non_transcrites()

    def test_des_paroles_arrivees_levent_le_constat(self, scraper):
        t = _constatee()
        scraper._apply_lyrics_from_html(load_fixture("genius/song_page.html"), t)
        assert t.lyrics.text and t.lyrics.source == "genius"
        assert not t.lyrics.non_transcrites()


class TestPredicat:
    def test_constat_recent_la_page_n_est_pas_relue(self):
        assert not _constatee().lyrics.page_genius_a_relire()

    def test_constat_perime_la_page_est_relue(self):
        t = _constatee(il_y_a=RELIRE_NON_TRANSCRITES + timedelta(hours=1))
        assert t.lyrics.page_genius_a_relire()

    def test_date_relue_en_texte_depuis_la_base(self):
        t = _constatee()
        t.lyrics.scraped_at = str(t.lyrics.scraped_at)  # le mapper la rend en str
        assert not t.lyrics.page_genius_a_relire()

    def test_les_paroles_restent_a_chercher_ailleurs(self):
        """YTM peut avoir le texte d'un morceau sorti : le constat ne dispense
        QUE la page Genius."""
        t = _constatee()
        assert t.lyrics.a_chercher()
        assert est_manquant(t, Manque.PAROLES)

    def test_sans_constat_la_page_est_lue(self):
        assert Track(title="T", artist=None).lyrics.page_genius_a_relire()


class TestBatch:
    def test_non_transcrites_n_est_pas_un_echec(self, page_non_transcrite):
        sc = GeniusScraperV3.__new__(GeniusScraperV3)
        sc.scrape_track_lyrics = lambda t: sc._apply_lyrics_from_html(page_non_transcrite, t)
        res = sc.scrape_lyrics_batch([Track(title="A", artist=None)])
        assert res["failed"] == 0 and res["non_transcrites"] == 1 and res["success"] == 1

    def test_constat_recent_pas_de_re_crawl(self):
        sc = GeniusScraperV3.__new__(GeniusScraperV3)
        appels = []
        sc.scrape_track_lyrics = lambda t: appels.append(t.title) or ""
        res = sc.scrape_lyrics_batch([_constatee()])
        assert appels == [] and res["non_transcrites"] == 1


class TestFluxCredits:
    _OPTS = credits.OptionsCredits(
        genius=False, discogs=False, paroles_ytm=False, sync_lrclib=False, sync_ytm=False
    )

    def test_genius_n_est_pas_rappele(self):
        t = _track("A")
        t.lyrics.source = SOURCE_NON_TRANSCRITES
        t.lyrics.scraped_at = datetime.now()
        cl, genius, _ = _clients()
        credits.run(_rt(), Artist(name="S"), [t], self._OPTS, Hooks(), cl)
        assert genius.lyrics_calls == 0

    def test_force_paroles_relit_la_page(self):
        t = _track("A")
        t.lyrics.source = SOURCE_NON_TRANSCRITES
        t.lyrics.scraped_at = datetime.now()
        cl, genius, _ = _clients()
        opts = credits.OptionsCredits(
            genius=False,
            discogs=False,
            paroles_ytm=False,
            sync_lrclib=False,
            sync_ytm=False,
            force_paroles=True,
        )
        credits.run(_rt(), Artist(name="S"), [t], opts, Hooks(), cl)
        assert genius.lyrics_calls == 1


def test_cellule_gui():
    assert helpers.format_lyrics_cell(_constatee()) == "📭"
