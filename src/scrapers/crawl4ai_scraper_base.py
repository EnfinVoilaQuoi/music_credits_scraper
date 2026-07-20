"""
Base class pour les scrapers utilisant Crawl4AI.
Fournit le bridge synchrone→asynchrone et la config navigateur commune.
Les sous-classes n'ont jamais à toucher à asyncio directement.

Phase F4 : plus d'`asyncio.run` par appel (une boucle jetable par crawl dans le
thread appelant) — le cœur est la coroutine `acrawl_page`, exécutée sur LA
boucle applicative ; `_crawl_page` n'est plus qu'un pont bloquant
(`async_loop.run_sync`) pour les workers sync, qui await-eront directement en F5.
"""

import inspect
import os
from pathlib import Path

from crawl4ai import BrowserConfig

from src.concurrency import async_loop
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _supported_kwargs(cls, **kwargs) -> dict:
    """Ne garde que les kwargs réellement acceptés par cls.__init__
    (compatibilité entre versions de crawl4ai : stealth/magic varient)."""
    try:
        params = inspect.signature(cls.__init__).parameters
        return {k: v for k, v in kwargs.items() if k in params}
    except (ValueError, TypeError):
        return {}


_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Profil navigateur PERSISTANT : conserve le cookie cf_clearance entre les
# morceaux ET entre les lancements → le challenge Cloudflare n'est résolu
# qu'UNE fois (manuellement), ensuite tout passe (même en headless).
_PROFILE_DIR = str(Path.home() / ".music_credits_scraper" / "cf_profile")

# Optionnel : se connecter à un navigateur Chromium DÉJÀ OUVERT (ex. Brave sur IP
# résidentielle) via CDP, au lieu de lancer Chrome for Testing. Lance ton Brave avec
# `--remote-debugging-port=9222` puis `set GENIUS_CDP_URL=http://localhost:9222`.
_CDP_URL = os.getenv("GENIUS_CDP_URL")

# Optionnel : forcer patchright à lancer le VRAI navigateur installé
# (channel="chrome", "msedge", "brave"…) au lieu du Chromium bundlé. Certains
# Cloudflare (ex: ultratop.be) détectent et bouclent le Chromium d'automation,
# alors que le vrai Chrome — sur IP résidentielle — passe le challenge comme une
# session normale → évite la danse CDP. Profil dédié PAR canal (cookies séparés).
_BROWSER_CHANNEL = os.getenv("SCRAPER_BROWSER_CHANNEL")


def _profile_dir() -> str:
    """Dossier de profil persistant ; suffixé par le canal s'il est forcé, pour
    ne pas mélanger les cookies du Chromium bundlé et du vrai Chrome."""
    base = str(Path.home() / ".music_credits_scraper" / "cf_profile")
    return f"{base}_{_BROWSER_CHANNEL}" if _BROWSER_CHANNEL else base


class CrawlAIScraperBase:
    """
    Base class pour tous les scrapers Crawl4AI du projet.

    Usage dans une sous-classe :
        markdown, html = self._crawl_page(url, js_before_wait=JS, wait_for="js:...")
    """

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._browser_config = self._make_browser_config(headless)

    @staticmethod
    def _make_browser_config(headless: bool) -> BrowserConfig:
        """Config navigateur + stealth + profil PERSISTANT (cookie CF réutilisé)."""
        os.makedirs(_PROFILE_DIR, exist_ok=True)
        extras = _supported_kwargs(
            BrowserConfig,
            enable_stealth=True,
            use_persistent_context=True,
            user_data_dir=_PROFILE_DIR,
        )
        return BrowserConfig(
            headless=headless,
            browser_type="chromium",
            user_agent=_USER_AGENT,
            verbose=False,
            **extras,
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
        js_before_wait: str | None = None,
        wait_for: str | None = None,
        wait_timeout: int = 15_000,
        page_timeout: int = 30_000,
        delay_before_return: float = 1.5,
    ) -> tuple[str | None, str | None]:
        """
        Crawl synchrone d'une page. Retourne (markdown, html_brut).
        Retourne (None, None) si le crawl échoue.

        Pont F4 : exécute `acrawl_page` sur LA boucle applicative (bloquant).
        À appeler depuis un thread sync uniquement — une coroutine await
        directement `acrawl_page`.
        """
        return async_loop.run_sync(
            self.acrawl_page(
                url,
                js_before_wait=js_before_wait,
                wait_for=wait_for,
                wait_timeout=wait_timeout,
                page_timeout=page_timeout,
                delay_before_return=delay_before_return,
            )
        )

    async def acrawl_page(
        self,
        url: str,
        *,
        js_before_wait: str | None = None,
        wait_for: str | None = None,
        wait_timeout: int = 15_000,
        page_timeout: int = 30_000,
        delay_before_return: float = 1.5,
    ) -> tuple[str | None, str | None]:
        """
        Coroutine native (F4) — même séquence que le pont sync historique :
        CDP → essai headless (profil persistant) → fenêtre VISIBLE (challenge).
        Retourne (markdown, html_brut), (None, None) si le crawl échoue.

        Paramètres :
            js_before_wait   : JS exécuté avant la condition d'attente (ex: clic expand)
            wait_for         : condition CSS ou JS à attendre (ex: "css:.credits", "js:()=>...")
            wait_timeout     : ms avant abandon de la condition wait_for
            page_timeout     : ms avant abandon du chargement de page
            delay_before_return : secondes d'attente supplémentaires après le wait_for
        """
        # 0) Mode CDP : on lit via un navigateur déjà ouvert (Brave, IP résidentielle)
        if _CDP_URL:
            try:
                _, html, blocked = await self._patchright_fetch(
                    url,
                    js_before_wait,
                    wait_for,
                    wait_timeout,
                    page_timeout,
                    delay_before_return,
                    headless=True,
                )
                if blocked:
                    logger.warning(
                        f"{self.__class__.__name__}: via CDP, page bloquée — ouvre d'abord "
                        f"genius.com dans ton Brave (résidentiel) pour lever le challenge."
                    )
                return None, html
            except Exception as e:
                logger.error(f"{self.__class__.__name__}: CDP {url}: {e}")
                return None, None

        # 1) Essai HEADLESS rapide (patchright + profil persistant) : passe seul
        #    dès que le cookie cf_clearance est dans le profil.
        if self.headless:
            try:
                _, html, blocked = await self._patchright_fetch(
                    url,
                    js_before_wait,
                    wait_for,
                    wait_timeout,
                    page_timeout,
                    delay_before_return,
                    headless=True,
                )
            except Exception as e:
                logger.error(f"{self.__class__.__name__}: patchright headless {url}: {e}")
                html, blocked = None, True
            if not blocked:
                return None, html
            logger.info(
                f"{self.__class__.__name__}: Cloudflare → fenêtre VISIBLE pour {url}. "
                f"⚠️ Résous le challenge UNE fois ; le cookie est mémorisé (profil persistant), "
                f"ensuite tout repasse en headless."
            )

        # 2) Mode VISIBLE + undetected + fenêtre longue : tu résous, on capture la
        #    vraie page (on attend le conteneur paroles, pas d'abandon prématuré).
        try:
            _, html, _ = await self._patchright_fetch(
                url,
                js_before_wait,
                wait_for,
                max(wait_timeout, 120_000),
                max(page_timeout, 120_000),
                max(delay_before_return, 2.0),
                headless=False,
            )
            return None, html
        except Exception as e:
            logger.error(f"{self.__class__.__name__}: patchright visible {url}: {e}")
            return None, None

    async def _patchright_fetch(
        self,
        url: str,
        js_before_wait: str | None,
        wait_for: str | None,
        wait_timeout: int,
        page_timeout: int,
        delay_before_return: float,
        headless: bool,
    ) -> tuple[str | None, str | None, bool]:
        """
        Fetch via patchright (Chromium undetected) en CONTEXTE PERSISTANT.
        On contrôle nous-mêmes l'attente : la fenêtre reste ouverte jusqu'à ce que
        la vraie page apparaisse (conteneur paroles) → laisse le temps de résoudre
        Cloudflare, puis capture le HTML. Le cookie cf_clearance persiste dans le profil.
        """
        try:
            from patchright.async_api import async_playwright
        except Exception as e:
            logger.error(
                f"patchright indisponible: {e} — `pip install -U crawl4ai && crawl4ai-setup`"
            )
            return None, None, True

        profile_dir = _profile_dir()
        os.makedirs(profile_dir, exist_ok=True)
        # Sélecteur « vraie page chargée » : conteneur paroles (présent sur toute page Genius)
        sel = (
            wait_for[4:]
            if (wait_for or "").startswith("css:")
            else "[data-lyrics-container='true']"
        )

        try:
            async with async_playwright() as pw:
                cdp_mode = bool(_CDP_URL)
                if cdp_mode:
                    # Attache à un Chromium déjà ouvert (Brave, IP résidentielle, vraie session)
                    browser = await pw.chromium.connect_over_cdp(_CDP_URL)
                    ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
                    page = await ctx.new_page()
                    logger.info(f"{self.__class__.__name__}: connecté via CDP à {_CDP_URL}")
                else:
                    launch_kwargs = dict(
                        headless=headless,
                        user_agent=_USER_AGENT,
                        viewport={"width": 1280, "height": 900},
                    )
                    # channel="chrome" → utilise le vrai Chrome installé (passe
                    # mieux certains Cloudflare que le Chromium bundlé)
                    if _BROWSER_CHANNEL:
                        launch_kwargs["channel"] = _BROWSER_CHANNEL
                    ctx = await pw.chromium.launch_persistent_context(profile_dir, **launch_kwargs)
                    page = ctx.pages[0] if ctx.pages else await ctx.new_page()

                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=page_timeout)
                    # Attend la vraie page (conteneur paroles). En mode VISIBLE : timeout=0
                    # (infini) → AUCUN timer, la fenêtre reste tant que tu n'as pas fini la
                    # boucle de challenge (ou que tu fermes l'onglet toi-même).
                    sel_timeout = 0 if (not headless and not cdp_mode) else wait_timeout
                    content_found = False
                    try:
                        await page.wait_for_selector(sel, timeout=sel_timeout)
                        content_found = True
                    except Exception:
                        pass  # pas trouvé : on récupère quand même le HTML pour diagnostic
                    # JS d'expansion (crédits) sur la vraie page
                    if js_before_wait:
                        try:
                            await page.evaluate(js_before_wait)
                            await page.wait_for_timeout(800)
                        except Exception:
                            pass
                    if delay_before_return:
                        await page.wait_for_timeout(int(delay_before_return * 1000))
                    html = await page.content()
                finally:
                    if cdp_mode:
                        await page.close()  # on ne ferme QUE notre onglet
                        await browser.close()  # détache (ne tue pas ton Brave)
                    else:
                        await ctx.close()

            # Si la vraie page a chargé (conteneur trouvé), on n'est PAS bloqué même
            # si des scripts Cloudflare résiduels traînent dans le HTML.
            blocked = (not content_found) and self._looks_blocked(None, html)
            return None, html, blocked
        except Exception as e:
            logger.error(f"{self.__class__.__name__}: patchright fetch {url}: {e}")
            return None, None, True

    @staticmethod
    def _looks_blocked(error_message: str | None, html: str | None) -> bool:
        """Détecte un blocage anti-bot (Cloudflare) sur échec OU page-défi 200."""
        err = (error_message or "").lower()
        if any(k in err for k in ("cloudflare", "anti-bot", "challenge", "403", "forbidden")):
            return True
        h = (html or "").lower()
        # NE PAS tester "challenge-platform"/"cf-chl" nus : Cloudflare injecte le
        # beacon /cdn-cgi/challenge-platform/... sur les pages DÉJÀ PASSÉES → faux
        # positif systématique (fenêtre VISIBLE à chaque page). On ne matche que
        # l'interstitiel réel (« Just a moment », objet de défi _cf_chl_opt, form).
        return any(
            k in h
            for k in (
                "just a moment",
                "checking your browser",
                "checking if the site connection is secure",
                "_cf_chl_opt",
                'id="challenge-form"',
            )
        )
