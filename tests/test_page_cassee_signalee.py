"""Une vraie page Kworb ou Ultratop CASSÉE est signalée, jamais maquillée.

Les replis LLM de ces deux sources sont retirés (décision utilisateur
2026-10-12) : testés en réel sur ces pages cassées, ils ne sauvaient rien
(réponse du modèle coupée). Reste ce qui compte — que la panne soit VUE.
Chaque test prend la VRAIE page capturée (`tests/fixtures/`), la casse comme le
ferait une refonte du site, la fait passer par le chemin réel du scraper, et
vérifie : panne déclarée à l'observation, rien d'inventé. La page saine, elle,
ne déclare rien.
"""

from contextlib import contextmanager

import pytest
from bs4 import BeautifulSoup

from tests.conftest import load_fixture


class ObsEspion:
    """Observation de source qui note tout ce qu'on lui déclare."""

    def __init__(self):
        self.journal = []

    def __getattr__(self, nom):
        def noter(*args, **kwargs):
            self.journal.append((nom, args))

        return noter

    def pannes(self):
        return [args for nom, args in self.journal if nom == "parse_error"]


# ── Kworb ─────────────────────────────────────────────────────────────────────


class _Reponse:
    status_code = 200
    encoding = None

    def __init__(self, texte):
        self.text = texte

    def raise_for_status(self):
        pass


def _kworb(monkeypatch, html):
    from src.scrapers import kworb_scraper
    from src.scrapers.kworb_scraper import KworbScraper

    monkeypatch.setattr(kworb_scraper.source_usage, "requests_get", lambda *a, **k: _Reponse(html))
    obs = ObsEspion()
    page = KworbScraper()._fetch_and_parse_body("https://kworb.net/spotify/artist/x.html", obs)
    return page, obs


def test_kworb_page_saine_rien_a_signaler(monkeypatch):
    page, obs = _kworb(monkeypatch, load_fixture("kworb/artist_songs.html"))
    assert len(page["entries"]) > 100
    assert obs.pannes() == []


def test_kworb_page_cassee_signalee_et_vide(monkeypatch):
    html = load_fixture("kworb/artist_songs.html")
    # Une refonte : les cellules ne sont plus des <td>. 0 ligne lue.
    cassee = html.replace("<td", "<span").replace("</td>", "</span>")
    page, obs = _kworb(monkeypatch, cassee)
    assert page["entries"] == []
    assert len(obs.pannes()) == 1


# ── BRMA (Ultratop) ───────────────────────────────────────────────────────────


@pytest.fixture
def updater(tmp_path):
    from src.utils.update_brma import UltratopUpdater

    return UltratopUpdater(
        database_path=str(tmp_path / "certif_brma.csv"), output_dir=str(tmp_path)
    )


def _brma(monkeypatch, updater, html):
    from src.utils import update_brma

    obs = ObsEspion()

    @contextmanager
    def observer(*a, **k):
        yield obs

    monkeypatch.setattr(update_brma.source_usage, "observe", observer)
    monkeypatch.setattr(
        updater, "fetch_page", lambda year, category: BeautifulSoup(html, "html.parser")
    )
    return updater._lire_page(2021, "singles"), obs


def test_brma_page_saine_rien_a_signaler(monkeypatch, updater):
    certifs, obs = _brma(monkeypatch, updater, load_fixture("brma/ultratop_2021_singles.html"))
    assert certifs
    assert obs.pannes() == []
    assert updater.pages_muettes == 0


def test_brma_page_cassee_signalee_et_muette(monkeypatch, updater):
    html = load_fixture("brma/ultratop_2021_singles.html")
    # Une refonte : la classe des titres change. 0 ligne candidate sur 2021.
    cassee = html.replace('class="chart_title"', 'class="titre_renomme"')
    certifs, obs = _brma(monkeypatch, updater, cassee)
    assert certifs == []
    assert len(obs.pannes()) == 1
    assert updater.pages_muettes == 1
