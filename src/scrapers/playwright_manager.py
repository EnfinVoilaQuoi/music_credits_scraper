"""
Gestionnaire d'instance Playwright partagée (par thread — et par boucle en async).

Playwright Sync API n'autorise qu'UNE instance sync_playwright() par thread :
en démarrer une seconde lève "Playwright Sync API inside the asyncio loop".
Les scrapers (SongBPM, SpotifyID, ...) doivent donc partager la même instance
et lancer chacun leur propre browser dessus.

Usage dans un scraper :
    from src.scrapers.playwright_manager import get_playwright

    self._playwright = get_playwright()          # instance partagée
    self.browser = self._playwright.chromium.launch(...)
    # Dans close()/_cleanup : fermer page/context/browser,
    # mais NE PAS appeler self._playwright.stop().

Phase F3 : les scrapers async partagent de la même façon UNE instance
`async_playwright()` liée à LA boucle applicative (`src.concurrency.async_loop`
— une seule boucle dans l'app, un singleton module suffit) :
    pw = await get_playwright_async()
    self.browser = await pw.chromium.launch(...)
    # fermer page/context/browser, mais NE PAS stopper l'instance partagée.
`stop_playwright_async()` s'appelle en fin de batch, DANS la boucle, APRÈS la
fermeture des browsers (garde-fou : annuler les tasks avant de fermer
Playwright async).
"""

import logging
import threading

from playwright.async_api import Playwright as AsyncPlaywright
from playwright.async_api import async_playwright
from playwright.sync_api import Playwright, sync_playwright

logger = logging.getLogger(__name__)

_local = threading.local()
_async_instance: AsyncPlaywright | None = None


def get_playwright() -> Playwright:
    """Retourne l'instance Playwright partagée du thread courant (créée au besoin)."""
    pw = getattr(_local, "playwright", None)
    if pw is None:
        logger.debug("Démarrage d'une instance Playwright partagée pour ce thread")
        pw = sync_playwright().start()
        _local.playwright = pw
    return pw


def stop_playwright() -> None:
    """Arrête l'instance partagée du thread courant (à appeler à la fermeture de l'app)."""
    pw = getattr(_local, "playwright", None)
    if pw is not None:
        try:
            pw.stop()
        except Exception:  # noqa: BLE001 — arrêt best-effort de l'instance Playwright
            pass
        _local.playwright = None


async def get_playwright_async() -> AsyncPlaywright:
    """Instance Playwright ASYNC partagée de la boucle applicative (créée au besoin)."""
    global _async_instance
    if _async_instance is None:
        logger.debug("Démarrage de l'instance Playwright async partagée")
        _async_instance = await async_playwright().start()
    return _async_instance


async def stop_playwright_async() -> None:
    """Arrête l'instance async partagée (fin de batch, browsers déjà fermés)."""
    global _async_instance
    if _async_instance is not None:
        try:
            await _async_instance.stop()
        except Exception:  # noqa: BLE001 — arrêt best-effort de l'instance Playwright async
            pass
        _async_instance = None
