"""L'échelle CDP → headless → fenêtre visible vaut UN appel logique.

`_patchright_fetch` est monkeypatché : aucun navigateur n'est lancé.
"""

import asyncio

import pytest

from src.observability import source_usage as su
from src.observability.issues import IssueKind
from src.scrapers import crawl4ai_scraper_base as base
from src.scrapers.crawl4ai_scraper_base import CrawlAIScraperBase


@pytest.fixture(autouse=True)
def _propre(monkeypatch):
    su.reset()
    su.set_sink(None)
    monkeypatch.setattr(base, "_CDP_URL", None)  # échelle headless → visible
    yield
    su.reset()
    su.set_sink(None)


def _scraper(passes, health_key="genius_scrape"):
    """Scraper dont chaque passe rend le prochain (html, blocked) de `passes`."""
    scraper = CrawlAIScraperBase(headless=True, health_key=health_key)
    restantes = list(passes)

    async def faux_fetch(*args, **kwargs):
        html, blocked = restantes.pop(0)
        if isinstance(html, Exception):
            raise html
        return None, html, blocked

    scraper._patchright_fetch = faux_fetch
    return scraper


def _jouer(scraper, url="https://genius.com/x"):
    html = asyncio.run(scraper.acrawl_page(url))[1]
    return html, su.flush()


def test_headless_bloque_puis_fenetre_visible_vaut_un_succes():
    """Le cas nominal derrière Cloudflare : deux passes, UN verdict `ok`.
    Compter deux échecs ici afficherait la source en rouge alors qu'elle sert."""
    html, verdicts = _jouer(_scraper([(None, True), ("<html>paroles</html>", False)]))
    assert html == "<html>paroles</html>"
    assert len(verdicts) == 1
    assert verdicts[0].issue == IssueKind.OK
    assert verdicts[0].attempts == 2
    assert verdicts[0].expected_blocked == 1, "le contournement doit rester visible"


def test_headless_suffisant_vaut_un_succes_sans_blocage():
    _, verdicts = _jouer(_scraper([("<html>ok</html>", False)]))
    assert verdicts[0].issue == IssueKind.OK
    assert verdicts[0].expected_blocked == 0


def test_bloque_partout_donne_un_seul_echec():
    html, verdicts = _jouer(_scraper([(None, True), (None, True)]))
    assert html is None
    assert len(verdicts) == 1
    assert verdicts[0].issue == IssueKind.UNREACHABLE


def test_une_erreur_patchright_est_classee():
    """Piège du dépôt : patchright a ses PROPRES classes d'exception."""
    boum = base.PatchrightError("Target closed")
    _, verdicts = _jouer(_scraper([(boum, True), (boum, True)]))
    assert len(verdicts) == 1
    assert verdicts[0].issue in {IssueKind.CRASH, IssueKind.TIMEOUT, IssueKind.UNREACHABLE}


def test_la_cle_de_source_suit_le_scraper():
    """Ultratop passe par la même base mais compte pour BRMA."""
    _, verdicts = _jouer(
        _scraper([("<html>certifs</html>", False)], health_key="brma"),
        url="https://www.ultratop.be/fr/or-platine/2024/singles",
    )
    assert verdicts[0].source_key == "brma"
