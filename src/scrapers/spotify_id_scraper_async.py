"""Scraper Spotify ID — variante ASYNC (Phase F3b), vivant dans la boucle asyncio.

Sous-classe de `SpotifyIDScraper` : toute la logique PURE est héritée (patterns
d'ID, cache, scoring de pertinence, parsing embed, choix LLM) ; seules les
méthodes touchant Playwright sont réécrites en async (API `playwright.async_api`,
mêmes sélecteurs, mêmes timeouts, même séquence). Le browser naît, travaille et
meurt DANS la boucle (`get_playwright_async`), fermé par `aclose()` en fin de
batch. Le LLM (Ollama, bloquant ~secondes) passe par `asyncio.to_thread`.

Le périmètre couvre le flux d'enrichissement (get_spotify_id + page title) ;
les méthodes de vote d'ID artiste (streams/Kworb) restent sync et migreront
avec leurs workers (F5). Les méthodes sync héritées touchant Playwright sont
neutralisées (RuntimeError) pour éviter tout usage accidentel.
"""

import asyncio
import logging
import urllib.parse

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from src.scrapers.playwright_manager import get_playwright_async
from src.scrapers.spotify_id_scraper_v2 import SpotifyIDScraper

logger = logging.getLogger("SpotifyIDScraper")


class SpotifyIDScraperAsync(SpotifyIDScraper):
    """Variante async du scraper Spotify ID (mêmes sélecteurs, mêmes caches)."""

    def __init__(self, cache_file: str = "spotify_ids_cache.json", headless: bool = True):
        super().__init__(cache_file=cache_file, headless=headless)
        logger.info(f"SpotifyIDScraperAsync initialisé (headless={headless}, driver lazy)")

    # ── Garde-fous : pas d'API sync sur l'instance async ────────────────────

    def _ensure_driver(self):
        raise RuntimeError("Instance async : utiliser les méthodes *_async")

    def get_spotify_id(self, artist: str, title: str) -> str | None:
        raise RuntimeError("Instance async : utiliser get_spotify_id_async")

    def get_spotify_page_title(self, spotify_id: str) -> str | None:
        raise RuntimeError("Instance async : utiliser get_spotify_page_title_async")

    def close(self):
        raise RuntimeError("Instance async : utiliser aclose()")

    # ── Init / cleanup (miroirs async de la voie sync) ──────────────────────

    async def _ensure_driver_async(self):
        if self.page is not None:
            return
        await self._init_driver_async()

    async def _init_driver_async(self):
        try:
            logger.info(
                f"🌐 Initialisation Playwright async SpotifyID (headless={self.headless})..."
            )
            self._playwright = await get_playwright_async()
            self.browser = await self._playwright.chromium.launch(
                headless=self.headless,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            )
            self.context = await self.browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            )
            await self.context.route(
                "**/*.{png,jpg,jpeg,gif,webp,svg,ico}", lambda route: route.abort()
            )
            self.page = await self.context.new_page()
            await self.page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            logger.info("✅ SpotifyIDScraperAsync: Playwright initialisé")
        except Exception as e:
            logger.error(f"❌ Erreur initialisation Playwright async SpotifyID: {e}")
            await self._cleanup_resources_async()
            raise

    async def _cleanup_resources_async(self):
        # NB: ne pas stopper self._playwright — instance partagée (playwright_manager)
        for attr in ("page", "context", "browser"):
            obj = getattr(self, attr, None)
            if obj:
                try:
                    await obj.close()
                except Exception:
                    pass
                setattr(self, attr, None)
        self._playwright = None

    async def aclose(self):
        await self._cleanup_resources_async()
        logger.info("✅ SpotifyIDScraperAsync fermé")

    # ── Cookies ─────────────────────────────────────────────────────────────

    async def _handle_cookies_async(self):
        cookie_selectors = [
            "button[id='onetrust-accept-btn-handler']",
            "button[data-testid='accept-all-cookies']",
            "button[class*='accept-all']",
            "#onetrust-accept-btn-handler",
        ]
        for selector in cookie_selectors:
            try:
                btn = await self.page.query_selector(selector)
                if btn and await btn.is_visible():
                    await btn.click()
                    logger.info(f"✅ Cookies acceptés via: {selector}")
                    return
            except Exception:
                continue

    # ── Recherche principale (miroir async de get_spotify_id) ───────────────

    async def get_spotify_id_async(self, artist: str, title: str) -> str | None:
        logger.info(f"🔍 Recherche ID Spotify pour: '{artist}' - '{title}'")

        cache_key = self._get_cache_key(artist, title)
        if cache_key in self.cache:
            cached = self.cache[cache_key]
            if cached and cached != "not_found":
                logger.info(f"✅ ID trouvé en cache: {cached}")
                return cached
            # 'not_found' n'est PAS définitif → on retente la recherche

        try:
            await self._ensure_driver_async()
        except Exception:
            logger.error("❌ Browser non disponible")
            return None

        # Apostrophes droites dans les requêtes (la recherche Spotify gère mal ')
        artist_q = self._normalize_apostrophes(artist)
        title_q = self._normalize_apostrophes(title)
        search_queries = [
            f"{artist_q} {title_q}",
            f'"{artist_q}" "{title_q}"',
            f"{title_q} {artist_q}",
        ]

        found_tracks = []
        had_errors = False

        for query_idx, query in enumerate(search_queries):
            logger.info(f"📝 Essai {query_idx + 1}/{len(search_queries)}: '{query}'")
            try:
                spotify_url = f"https://open.spotify.com/search/{urllib.parse.quote(query)}"
                await self.page.goto(spotify_url, wait_until="domcontentloaded", timeout=30_000)
                await self._handle_cookies_async()

                # Attendre qu'un lien track apparaisse
                try:
                    await self.page.wait_for_selector("a[href*='/track/']", timeout=self._timeout)
                except PlaywrightTimeoutError:
                    logger.warning(f"⏰ Timeout pour: {query}")
                    continue

                track_selectors = [
                    "a[href*='/track/'][data-testid]",
                    "div[data-testid*='track'] a[href*='/track/']",
                    "[role='row'] a[href*='/track/']",
                    "a[href*='/track/']",
                ]

                for selector in track_selectors:
                    links = await self.page.query_selector_all(selector)
                    for link in links[:10]:
                        try:
                            href = await link.get_attribute("href") or ""
                            if "/track/" not in href:
                                continue
                            sid = self.extract_spotify_id_from_url(href)
                            if not sid or sid in [t["id"] for t in found_tracks]:
                                continue
                            try:
                                link_text = (await link.inner_text()).lower()
                                parent = await link.query_selector("..")
                                parent_text = (await parent.inner_text()).lower() if parent else ""
                                combined = f"{link_text} {parent_text}"
                                relevance = self._calculate_relevance(artist, title, combined)
                            except Exception:
                                combined, relevance = "", 0.5
                            found_tracks.append(
                                {"id": sid, "text": combined, "relevance": relevance, "href": href}
                            )
                        except Exception:
                            continue

                if found_tracks:
                    break

            except Exception as e:
                had_errors = True
                logger.error(f"❌ Erreur requête '{query}': {e}")
                # Driver mort (page/context fermé) → re-création et on continue
                err_str = str(e).lower()
                if any(s in err_str for s in ("thread", "greenlet", "closed", "crashed")):
                    try:
                        await self._cleanup_resources_async()
                        await self._init_driver_async()
                    except Exception:
                        logger.error("❌ Impossible de réinitialiser le driver SpotifyID")
                        break
                continue

        if found_tracks:
            found_tracks.sort(key=lambda x: x["relevance"], reverse=True)
            best = found_tracks[0]

            # Fallback LLM (Ollama bloquant → hors boucle) si choix ambigu
            if len(found_tracks) > 1 and best["relevance"] < 0.8:
                llm_choice = await asyncio.to_thread(
                    self._select_track_with_llm, artist, title, found_tracks
                )
                if llm_choice is not None:
                    best = llm_choice
                    logger.info(f"🤖 SpotifyID LLM: choix affiné → {best['id']}")

            sid = best["id"]
            logger.info(f"✅ SÉLECTIONNÉ: {sid} (relevance: {best['relevance']:.2f})")
            self.cache[cache_key] = sid
            self._save_cache()
            return sid
        else:
            logger.warning(f"❌ Aucun ID Spotify trouvé pour '{title}'")
            # Ne pas cacher l'échec si des erreurs techniques ont eu lieu
            if not had_errors:
                self.cache[cache_key] = "not_found"
                self._save_cache()
            return None

    # ── Titre de page (miroir async) ────────────────────────────────────────

    async def get_spotify_page_title_async(self, spotify_id: str) -> str | None:
        try:
            await self._ensure_driver_async()
        except Exception:
            return None
        try:
            spotify_url = f"https://open.spotify.com/track/{spotify_id}"
            await self.page.goto(spotify_url, wait_until="domcontentloaded", timeout=30_000)
            return self._clean_page_title(await self.page.title())
        except Exception as e:
            logger.error(f"❌ Erreur récupération titre: {e}")
        return None
