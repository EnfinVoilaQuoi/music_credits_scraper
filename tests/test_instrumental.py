"""Instrumentaux Genius — « pas de paroles par nature » ≠ scrape raté (e27).

Un interlude sans paroles rendait une chaîne vide que tout le monde lisait
comme un ÉCHEC : compté en échec, jamais daté, re-scrapé à chaque run (12 s de
timeout du sélecteur de paroles à chaque fois) et ⚠️ à perpétuité dans la GUI.
Le constat vit dans `tracks.instrumental` (tri-état) et UN seul prédicat,
`Lyrics.a_chercher()`, le consomme partout.

Fixture réelle : Lucio Bukowski — Nuage d'Oort (interlude), capturée le
2026-09-20 (`scripts/capture_fixtures.py --only genius_instrumental_page`).
"""

from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

from src.gui import helpers
from src.models import Artist, Track
from src.models.track import Credit, CreditRole
from src.scrapers.genius_scraper_v3 import GeniusScraperV3
from src.services import credits
from src.services.runtime import Hooks, Manque, est_manquant
from tests.conftest import load_fixture
from tests.test_services_credits import _clients, _rt, _track


@pytest.fixture(scope="module")
def scraper():
    return GeniusScraperV3.__new__(GeniusScraperV3)


@pytest.fixture(scope="module")
def page_instrumentale():
    return load_fixture("genius/instrumental_page.html")


@pytest.fixture(scope="module")
def page_a_paroles():
    return load_fixture("genius/song_page.html")


# ── Le prédicat ─────────────────────────────────────────────────────────────
class TestAChercher:
    def test_rien_en_base(self):
        assert Track(title="T", artist=None).lyrics.a_chercher()

    def test_texte_present(self):
        t = Track(title="T", artist=None)
        t.lyrics.text = "la la"
        assert not t.lyrics.a_chercher()

    def test_instrumental_constate(self):
        t = Track(title="T", artist=None)
        t.lyrics.instrumental = True
        assert not t.lyrics.a_chercher()

    def test_faux_n_est_pas_un_constat_de_paroles(self):
        """`False` = des paroles ont été vues un jour ; sans texte en base, il
        reste à les chercher."""
        t = Track(title="T", artist=None)
        t.lyrics.instrumental = False
        assert t.lyrics.a_chercher()


# ── Détection sur la page ───────────────────────────────────────────────────
class TestDetectionPage:
    def test_placeholder_reconnu(self, scraper, page_instrumentale):
        soup = BeautifulSoup(page_instrumentale, "html.parser")
        assert scraper._is_instrumental_bs4(soup), "placeholder LyricsPlaceholder changé ?"

    def test_une_page_a_paroles_n_est_pas_instrumentale(self, scraper, page_a_paroles):
        soup = BeautifulSoup(page_a_paroles, "html.parser")
        assert not scraper._is_instrumental_bs4(soup)

    def test_le_placeholder_n_est_pas_enregistre_comme_paroles(self, scraper, page_instrumentale):
        """Le wrapper `Lyrics__Container` existe sur un instrumental — le repli
        par classe ne doit pas y lire « This song is an instrumental »."""
        soup = BeautifulSoup(page_instrumentale, "html.parser")
        assert scraper._extract_lyrics_bs4(soup) == ""

    def test_json_d_etat_suffit_sans_placeholder(self, scraper):
        html = (
            "<html><body><script>window.__PRELOADED_STATE__ = JSON.parse("
            '\'{\\"song\\":{\\"instrumental\\":true}}\');</script></body></html>'
        )
        assert scraper._is_instrumental_bs4(BeautifulSoup(html, "html.parser"))

    def test_un_conteneur_de_paroles_l_emporte(self, scraper):
        html = (
            "<div data-lyrics-container='true'>la la</div>"
            "<div class='LyricsPlaceholder__Message-x'>This song is an instrumental</div>"
        )
        assert not scraper._is_instrumental_bs4(BeautifulSoup(html, "html.parser"))

    def test_apply_pose_le_constat(self, scraper, page_instrumentale):
        t = Track(title="Nuage d'Oort (interlude)", artist=Artist(name="Lucio Bukowski"))
        assert scraper._apply_lyrics_from_html(page_instrumentale, t) == ""
        assert t.lyrics.instrumental is True
        assert t.lyrics.present is False and t.lyrics.text is None
        assert t.lyrics.scraped_at is not None, "le scrape a ABOUTI, il se date"
        assert t.lyrics.source == "genius"

    def test_apply_avec_paroles_pose_faux(self, scraper, page_a_paroles):
        t = Track(title="Dans le vide", artist=Artist(name="Josman"))
        assert scraper._apply_lyrics_from_html(page_a_paroles, t)
        assert t.lyrics.instrumental is False and t.lyrics.present is True

    def test_page_muette_ne_conclut_pas(self, scraper):
        """Ni conteneur ni placeholder (page-défi, HTML tronqué) : on ne sait
        rien, le tri-état reste `None` — même règle qu'`absent`."""
        t = Track(title="T", artist=None)
        assert scraper._apply_lyrics_from_html("<html><body>Un instant…</body></html>", t) == ""
        assert t.lyrics.instrumental is None and t.lyrics.scraped_at is None


class TestBatch:
    @staticmethod
    def _scraper_qui_constate(instrumental):
        sc = GeniusScraperV3.__new__(GeniusScraperV3)
        sc.appels = []

        def scrape_track_lyrics(track):
            sc.appels.append(track.title)
            if instrumental:
                track.lyrics.instrumental = True
            return ""

        sc.scrape_track_lyrics = scrape_track_lyrics
        return sc

    def test_instrumental_n_est_pas_un_echec(self):
        sc = self._scraper_qui_constate(True)
        res = sc.scrape_lyrics_batch([Track(title="A", artist=None)])
        assert res["failed"] == 0 and res["instrumental"] == 1 and res["success"] == 1

    def test_vide_sans_constat_reste_un_echec(self):
        sc = self._scraper_qui_constate(False)
        res = sc.scrape_lyrics_batch([Track(title="A", artist=None)])
        assert res["failed"] == 1 and res["instrumental"] == 0

    def test_un_instrumental_constate_n_est_pas_re_crawle(self):
        sc = self._scraper_qui_constate(True)
        t = Track(title="A", artist=None)
        t.lyrics.instrumental = True
        res = sc.scrape_lyrics_batch([t])
        assert sc.appels == [] and res["instrumental"] == 1 and res["success"] == 1


# ── Sélection et flux ───────────────────────────────────────────────────────
class TestSelection:
    def test_ni_paroles_ni_timestamps_manquants(self):
        t = Track(title="T", artist=None)
        t.lyrics.instrumental = True
        assert not est_manquant(t, Manque.PAROLES)
        assert not est_manquant(t, Manque.TIMESTAMPS)

    def test_jamais_constate_reste_manquant(self):
        t = Track(title="T", artist=None)
        assert est_manquant(t, Manque.PAROLES) and est_manquant(t, Manque.TIMESTAMPS)


class TestFluxCredits:
    _OPTS = credits.OptionsCredits(
        genius=False, discogs=False, paroles_ytm=False, sync_lrclib=False, sync_ytm=False
    )

    def test_genius_n_est_pas_rappele_pour_un_instrumental(self):
        t = _track("A")
        t.lyrics.instrumental = True
        cl, genius, _ = _clients()
        credits.run(_rt(), Artist(name="S"), [t], self._OPTS, Hooks(), cl)
        assert genius.lyrics_calls == 0

    def test_la_synchro_ne_le_retente_pas(self):
        class _LyricsEspion:
            appels = []

            def enrich(self, track, artist_name, *, need_sync, need_text):
                self.appels.append((track.title, need_sync, need_text))
                return SimpleNamespace(
                    lyrics_synced=None, synced_kind=None, synced_is_cross=False, text=None
                )

            def close(self):
                pass

        t = _track("A")
        t.lyrics.instrumental = True
        cl, _, _ = _clients(lyrics=_LyricsEspion())
        opts = credits.OptionsCredits(genius=False, discogs=False, paroles_genius=False)
        bilan = credits.run(_rt(), Artist(name="S"), [t], opts, Hooks(), cl)
        assert _LyricsEspion.appels == []
        assert bilan.paroles == {
            "success": 1,
            "failed": 0,
            "errors": [],
            "lyrics_scraped": 0,
            "instrumental": 1,
        }
        assert "instrumentaux" in credits.resume(bilan, opts)

    def test_force_paroles_efface_le_constat(self):
        t = _track("A")
        t.lyrics.instrumental = True
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
        assert genius.lyrics_calls == 1, "forcer = re-constater"


# ── GUI ─────────────────────────────────────────────────────────────────────
class TestGui:
    @staticmethod
    def _complet(instrumental=None, texte=None):
        t = Track(title="T", artist=Artist(name="A"), release_date="2021-06-15", duration="3:48")
        t.id = 1
        t.lyrics.text = texte
        t.lyrics.present = bool(texte)
        t.lyrics.instrumental = instrumental
        t.audio.bpm = 142
        t.audio.key, t.audio.mode = "C", "Major"
        t.add_credit(Credit(name="P", role=CreditRole.PRODUCER))
        t.add_credit(Credit(name="A", role=CreditRole.WRITER))
        t.spotify_id = "ID"
        t.streams.spotify_streams, t.streams.ytm_streams = 10, 5
        return t

    def test_cellule(self):
        assert helpers.format_lyrics_cell(self._complet(True)) == "🎹"
        assert helpers.format_lyrics_cell(self._complet(None)) == ""
        assert helpers.format_lyrics_cell(self._complet(False, "la la")) == "✓"
        t = self._complet(False, "la la")
        t.lyrics.synced = "[00:01.00] la"
        assert helpers.format_lyrics_cell(t) == "✓⏱"
        t.lyrics.text, t.lyrics.present = None, False
        assert helpers.format_lyrics_cell(t) == "⏱"

    def test_statut_complet_avec_un_instrumental(self):
        assert helpers.get_track_status_icon(self._complet(True), set()) == "✅"

    def test_statut_incomplet_sans_constat(self):
        assert helpers.get_track_status_icon(self._complet(None), set()) == "⚠️"
