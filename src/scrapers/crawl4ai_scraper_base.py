"""
Base class pour les scrapers utilisant Crawl4AI.
Fournit le bridge synchrone→asynchrone et la config navigateur commune.
Les sous-classes n'ont jamais à toucher à asyncio directement.
"""
import asyncio
from typing import Optional, Tuple

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

from src.utils.logger import get_logger

logger = get_logger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class CrawlAIScraperBase:
    """
    Base class pour tous les scrapers Crawl4AI du projet.

    Usage dans une sous-classe :
        markdown, html = self._crawl_page(url, js_before_wait=JS, wait_for="js:...")
    """

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._browser_config = BrowserConfig(
            headless=headless,
            browser_type="chromium",
            user_agent=_USER_AGENT,
            verbose=False,
        )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def close(self) -> None:
        """Compatibilité API avec les scrapers Playwright/Selenium."""
        logger.debug(f"{self.__class__.__name__}: close() — aucune ressource persistante")

    # -------------------------------------------------------------------------
    # API protégée pour les sous-classes
    # -------------------------------------------------------------------------

    def _crawl_page(
        self,
        url: str,
        js_before_wait: Optional[str] = None,
        wait_for: Optional[str] = None,
        wait_timeout: int = 15_000,
        page_timeout: int = 30_000,
        delay_before_return: float = 1.5,
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Crawl synchrone d'une page. Retourne (markdown, html_brut).
        Retourne (None, None) si le crawl échoue.

        Paramètres :
            js_before_wait   : JS exécuté avant la condition d'attente (ex: clic expand)
            wait_for         : condition CSS ou JS à attendre (ex: "css:.credits", "js:()=>...")
            wait_timeout     : ms avant abandon de la condition wait_for
            page_timeout     : ms avant abandon du chargement de page
            delay_before_return : secondes d'attente supplémentaires après le wait_for
        """
        try:
            return asyncio.run(
                self._async_crawl_page(
                    url=url,
                    js_before_wait=js_before_wait,
                    wait_for=wait_for,
                    wait_timeout=wait_timeout,
                    page_timeout=page_timeout,
                    delay_before_return=delay_before_return,
                )
            )
        except Exception as e:
            logger.error(f"{self.__class__.__name__}: erreur _crawl_page({url}): {e}")
            return None, None

    async def _async_crawl_page(
        self,
        url: str,
        js_before_wait: Optional[str],
        wait_for: Optional[str],
        wait_timeout: int,
        page_timeout: int,
        delay_before_return: float,
    ) -> Tuple[Optional[str], Optional[str]]:
        """Coroutine interne — ne pas appeler directement depuis les sous-classes."""
        run_config = CrawlerRunConfig(
            js_code=js_before_wait,
            wait_for=wait_for,
            wait_for_timeout=wait_timeout,
            page_timeout=page_timeout,
            delay_before_return_html=delay_before_return,
            remove_consent_popups=True,
            remove_overlay_elements=True,
            markdown_generator=DefaultMarkdownGenerator(
                options={"ignore_links": True}
            ),
            cache_mode=CacheMode.BYPASS,
            verbose=False,
        )

        try:
            async with AsyncWebCrawler(config=self._browser_config) as crawler:
                result = await crawler.arun(url=url, config=run_config)

                if not result.success:
                    logger.warning(
                        f"{self.__class__.__name__}: crawl échoué pour {url} — "
                        f"{result.error_message}"
                    )
                    return None, None

                markdown = str(result.markdown) if result.markdown else None
                # HTML BRUT de préférence : cleaned_html supprime les attributs
                # data-* (ex: data-lyrics-container) nécessaires aux extracteurs
                html = result.html or result.cleaned_html or None
                logger.debug(
                    f"{self.__class__.__name__}: crawl OK — "
                    f"markdown={len(markdown or '')} chars, html={len(html or '')} chars"
                )
                return markdown, html

        except Exception as e:
            logger.error(f"{self.__class__.__name__}: exception AsyncWebCrawler: {e}")
            return None, None
