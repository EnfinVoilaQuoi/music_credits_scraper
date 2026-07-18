"""Tests du singleton Playwright async (Phase F3a) — vrai Chromium, zéro réseau."""

import pytest

from src.concurrency.async_loop import AsyncLoopThread
from src.scrapers import playwright_manager


@pytest.fixture()
def loop_thread():
    lt = AsyncLoopThread(name="test-pw-loop")
    lt.start()
    yield lt
    lt.shutdown(timeout=5.0)


def test_singleton_partage_et_browser(loop_thread):
    """Deux get successifs → MÊME instance ; un browser se lance et se ferme."""

    async def scenario():
        pw1 = await playwright_manager.get_playwright_async()
        pw2 = await playwright_manager.get_playwright_async()
        assert pw1 is pw2

        browser = await pw1.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page()
        await page.goto("about:blank")
        title = await page.title()
        await browser.close()

        await playwright_manager.stop_playwright_async()
        return title

    assert loop_thread.submit(scenario()).result(timeout=60.0) == ""


def test_stop_puis_get_recree(loop_thread):
    async def scenario():
        pw1 = await playwright_manager.get_playwright_async()
        await playwright_manager.stop_playwright_async()
        await playwright_manager.stop_playwright_async()  # idempotent
        pw2 = await playwright_manager.get_playwright_async()
        recreated = pw2 is not pw1
        await playwright_manager.stop_playwright_async()
        return recreated

    assert loop_thread.submit(scenario()).result(timeout=60.0) is True
