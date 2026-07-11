"""
Gestionnaire d'instance Playwright partagée (par thread).

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
"""

import logging
import threading

from playwright.sync_api import sync_playwright, Playwright

logger = logging.getLogger(__name__)

_local = threading.local()


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
        except Exception:
            pass
        _local.playwright = None
