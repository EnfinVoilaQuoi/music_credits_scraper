"""Tests du pont sync→boucle `async_loop.run_sync` (Phase F4)."""

import threading

import pytest

from src.concurrency import async_loop


@pytest.fixture(autouse=True)
def _clean_app_loop():
    yield
    async_loop.shutdown(timeout=2.0)


def test_run_sync_retourne_le_resultat_et_demarre_la_boucle():
    async def coro():
        return threading.current_thread().name

    # Exécuté sur le thread de LA boucle applicative, pas le thread appelant
    assert async_loop.run_sync(coro()) == "asyncio-loop"
    assert async_loop.is_running()


def test_run_sync_propage_les_exceptions():
    async def boom():
        raise ValueError("boum")

    with pytest.raises(ValueError, match="boum"):
        async_loop.run_sync(boom())


def test_run_sync_refuse_depuis_la_boucle():
    """Depuis une coroutine, run_sync doit lever (deadlock sinon) — on await."""

    async def inner():
        return 1

    async def outer():
        try:
            async_loop.run_sync(inner())
        except RuntimeError:
            return "raised"
        return "not raised"

    assert async_loop.run_sync(outer()) == "raised"


def test_crawl_page_route_par_la_boucle():
    """Le pont sync des scrapers crawl4ai exécute `acrawl_page` sur la boucle app."""
    from src.scrapers.crawl4ai_scraper_base import CrawlAIScraperBase

    class _Stub(CrawlAIScraperBase):
        def __init__(self):
            super().__init__(headless=True)
            self.seen_thread = None

        async def acrawl_page(self, url, **kwargs):
            self.seen_thread = threading.current_thread().name
            return ("md", "<html>")

    stub = _Stub()
    assert stub._crawl_page("https://exemple.test") == ("md", "<html>")
    assert stub.seen_thread == "asyncio-loop"
