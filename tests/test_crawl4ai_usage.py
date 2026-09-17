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
    monkeypatch.delenv("GENIUS_CDP_URL", raising=False)  # échelle headless → visible
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


def test_le_pont_sync_rejoue_les_passes_dans_l_observation_de_l_appelant():
    """Le test qui discrimine : `run_sync` n'est PAS simulé.

    `update_brma._lire_page` tient une observation `brma` sur son thread pour y
    poser le verdict de PARSE ; le rendu tourne sur la boucle. Quand le pont
    ouvrait sa propre observation côté boucle, l'appelant restait sans tentative
    — run BRMA du 2026-09-17 : 78 pages `ok` ET 78 `indeterminate`. Ici : UN
    verdict, portant les deux passes, sur le thread de l'appelant.
    """
    import threading

    vu = {}
    scraper = _scraper([(None, True), ("<html>ok</html>", False)], health_key="brma")
    interne = scraper._patchright_fetch

    async def faux_fetch(*args, **kwargs):
        vu["thread"] = threading.current_thread().name
        return await interne(*args, **kwargs)

    scraper._patchright_fetch = faux_fetch
    with su.observe("brma", label="page") as obs:
        _, html = scraper._crawl_page("https://www.ultratop.be/x")
        obs.ok()  # le verdict de parse de l'appelant
    assert html == "<html>ok</html>"
    assert vu["thread"] == "asyncio-loop", "le rendu doit bien changer de thread"
    (verdict,) = su.flush()
    assert verdict.issue == IssueKind.OK
    assert verdict.attempts == 2 and verdict.expected_blocked == 1


def test_le_pont_sync_sans_appelant_observant_ouvre_la_sienne():
    """Genius sync : aucun `observe` chez l'appelant — le pont en ouvre une,
    comme avant, et le verdict porte les passes."""
    scraper = _scraper([("<html>ok</html>", False)])
    scraper._crawl_page("https://genius.com/x")
    (verdict,) = su.flush()
    assert verdict.issue == IssueKind.OK and verdict.attempts == 1
