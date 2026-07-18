"""Scraper SongBPM — variante ASYNC (Phase F3c), vivant dans la boucle asyncio.

Sous-classe de `SongBPMScraper` : la logique PURE est héritée (matching
artiste/titre, normalisations, extraction regex+LLM des détails) ; les méthodes
touchant Playwright sont réécrites en async (mêmes sélecteurs, mêmes timeouts,
même séquence de recherche : accueil → champ query → résultats → détails).
Le fallback LLM (Ollama, bloquant) passe par `asyncio.to_thread`. Le browser
naît, travaille et meurt DANS la boucle, fermé par `aclose()` en fin de batch.
Le timeout de garde de 30 s vit chez le PROVIDER (`asyncio.timeout`, F3c).
"""

import asyncio
import logging
import re
from typing import Any

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from src.scrapers.playwright_manager import get_playwright_async
from src.scrapers.songbpm_scraper_v2 import SongBPMScraper
from src.utils.logger import log_api

logger = logging.getLogger(__name__)


class SongBPMScraperAsync(SongBPMScraper):
    """Variante async du scraper SongBPM (mêmes sélecteurs, même matching)."""

    def __init__(self, headless: bool = False):
        super().__init__(headless=headless)
        logger.info(f"SongBPMScraperAsync initialisé (headless={headless}, driver lazy)")

    # ── Garde-fous : pas d'API sync sur l'instance async ────────────────────

    def _ensure_driver(self):
        raise RuntimeError("Instance async : utiliser les méthodes *_async")

    def search_track(self, *args, **kwargs):
        raise RuntimeError("Instance async : utiliser search_track_async")

    def close(self):
        raise RuntimeError("Instance async : utiliser aclose()")

    # ── Init / cleanup (miroirs async) ──────────────────────────────────────

    async def _ensure_driver_async(self):
        if self.page is not None:
            return
        await self._init_driver_async()

    async def _init_driver_async(self):
        try:
            logger.info(f"🌐 Initialisation Playwright async SongBPM (headless={self.headless})...")
            self._playwright = await get_playwright_async()
            self.browser = await self._playwright.chromium.launch(
                headless=self.headless,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-webgl",
                    "--disable-webgl2",
                ],
            )
            self.context = await self.browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            )
            # Bloquer les images pour accélérer
            await self.context.route(
                "**/*.{png,jpg,jpeg,gif,webp,svg,ico}", lambda route: route.abort()
            )
            self.page = await self.context.new_page()
            await self.page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            logger.info("✅ SongBPM async: Playwright initialisé avec succès")
        except Exception as e:
            logger.error(f"❌ Erreur initialisation Playwright async SongBPM: {e}")
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
        logger.info("✅ SongBPM async: Browser fermé")

    async def _reset_browser_on_error_async(self):
        logger.warning("⚠️ Réinitialisation du browser après erreur")
        await self._cleanup_resources_async()

    # ── Cookies ─────────────────────────────────────────────────────────────

    async def _handle_cookies_async(self):
        try:
            agree_selectors = [
                ".qc-cmp2-summary-buttons button[mode='primary']",
                "button.css-47sehv",
                "button:has-text('AGREE')",
                "button:has-text('Accept all')",
                "button:has-text('Accept')",
                "button:has-text('I understand')",
            ]
            for selector in agree_selectors:
                try:
                    btn = await self.page.query_selector(selector)
                    if btn and await btn.is_visible():
                        await btn.click()
                        logger.info(f"✅ Popup cookies fermé via: {selector}")
                        return
                except Exception:
                    continue
        except Exception as e:
            logger.debug(f"Gestion cookies (non bloquant): {e}")

    # ── Détails (miroir async ; texte → détails = logique pure héritée) ─────

    async def _extract_track_details_async(self, detail_url: str, timeout: int = 30) -> dict:
        details: dict[str, Any] = {}
        try:
            # Les liens du site sont relatifs (/@artiste/titre) → URL absolue requise
            if detail_url.startswith("/"):
                detail_url = "https://songbpm.com" + detail_url
            logger.info(f"📄 Navigation détails: {detail_url}")
            await self.page.goto(detail_url, wait_until="domcontentloaded", timeout=timeout * 1000)

            content_selectors = [
                "div.lg\\:prose-xl",
                "div[class*='prose']",
                "main",
                "article",
            ]
            full_text = None
            for selector in content_selectors:
                el = await self.page.query_selector(selector)
                if el and await el.is_visible():
                    full_text = await el.inner_text()
                    break

            if not full_text:
                body = await self.page.query_selector("body")
                full_text = await body.inner_text() if body else ""

            clean_text = re.sub(r"\s+", " ", full_text).replace("\xa0", " ")
            # Regex rapides + éventuel LLM bloquant (Ollama) → hors boucle
            details = await asyncio.to_thread(self._details_from_text, clean_text)
            logger.info(f"✅ Détails extraits: {details}")
        except PlaywrightTimeoutError:
            logger.warning(f"⏰ Timeout ({timeout}s) lors de la récupération des détails")
        except Exception as e:
            logger.error(f"❌ Erreur extraction détails: {e}")
        return details

    # ── Résultats de recherche (miroir async du DOM-walking) ────────────────

    async def _get_search_results_async(self) -> list[dict[str, Any]]:
        results = []
        try:
            containers = await self.page.query_selector_all("div.bg-card")
            logger.debug(f"📋 {len(containers)} conteneurs trouvés")

            for container in containers:
                try:
                    result = {}

                    track_links = await container.query_selector_all("a[href*='/@']")
                    if not track_links:
                        continue

                    track_link = track_links[0]
                    href = await track_link.get_attribute("href") or ""
                    if "/@" not in href:
                        continue
                    path = href.split("songbpm.com")[-1] if "songbpm.com" in href else href
                    if path.count("/") < 2:
                        continue
                    if any(
                        s in href.lower()
                        for s in ["/apple-music", "/spotify", "/amazon", "/youtube"]
                    ):
                        continue

                    result["detail_url"] = href

                    # Titre et artiste
                    info_divs = await track_link.query_selector_all("div.flex-1")
                    for info_div in info_divs:
                        paragraphs = await info_div.query_selector_all("p")
                        if len(paragraphs) >= 2:
                            artist_class = await paragraphs[0].get_attribute("class") or ""
                            title_class = await paragraphs[1].get_attribute("class") or ""
                            if "text-sm" in artist_class and (
                                "text-lg" in title_class or "text-2xl" in title_class
                            ):
                                result["artist"] = (await paragraphs[0].inner_text()).strip()
                                result["title"] = (await paragraphs[1].inner_text()).strip()
                                break

                    if "title" not in result or "artist" not in result:
                        continue

                    # BPM, Key, Duration
                    metrics = await track_link.query_selector_all(
                        "div.flex.flex-1.flex-col.items-center"
                    )
                    for metric in metrics:
                        spans = await metric.query_selector_all("span")
                        if len(spans) >= 2:
                            label = (await spans[0].inner_text()).strip().upper()
                            value = (await spans[1].inner_text()).strip()
                            if label == "BPM":
                                try:
                                    result["bpm"] = int(value)
                                except ValueError:
                                    pass
                            elif label == "KEY":
                                result["key"] = value
                            elif label == "DURATION":
                                result["duration"] = value

                    # Spotify ID
                    spotify_links = await container.query_selector_all(
                        "a[href*='spotify.com/track/']"
                    )
                    if spotify_links:
                        spotify_url = await spotify_links[0].get_attribute("href") or ""
                        sid = self._extract_spotify_id_from_url(spotify_url)
                        if sid:
                            result["spotify_id"] = sid
                            result["spotify_url"] = spotify_url

                    if "title" in result and "artist" in result and "detail_url" in result:
                        results.append(result)
                        logger.info(f"✅ Résultat: {result['artist']} - {result['title']}")

                except Exception as e:
                    logger.debug(f"Erreur conteneur: {e}")
                    continue

        except Exception as e:
            logger.error(f"❌ Erreur extraction résultats: {e}")
        return results

    # ── Recherche (miroirs async) ───────────────────────────────────────────

    async def _perform_search_async(
        self,
        track_title: str,
        artist_name: str,
        spotify_id: str | None = None,
        max_results_to_check: int = 5,
        fetch_details: bool = True,
        reload_homepage: bool = True,
    ) -> dict[str, Any] | None:
        try:
            if reload_homepage:
                logger.info("🌐 Chargement page d'accueil SongBPM...")
                await self.page.goto(self.base_url, wait_until="domcontentloaded", timeout=30_000)
                await self._handle_cookies_async()

            search_selector = "input[name='query'][placeholder='type a song, get a bpm']"
            try:
                await self.page.wait_for_selector(search_selector, timeout=10_000)
            except PlaywrightTimeoutError:
                logger.error("⏰ Champ de recherche introuvable")
                return None

            search_query = f"{artist_name} {track_title}"
            logger.info(f"🔍 Recherche: '{search_query}'")
            await self.page.fill(search_selector, search_query)
            await self.page.press(search_selector, "Enter")

            # Attendre les résultats
            try:
                await self.page.wait_for_selector("div.bg-card", timeout=10_000)
            except PlaywrightTimeoutError:
                logger.warning(f"❌ Aucun résultat pour '{track_title}'")
                return None

            results = await self._get_search_results_async()
            if not results:
                return None

            for i, result in enumerate(results[:max_results_to_check], 1):
                if self._match_track(
                    result["title"],
                    result["artist"],
                    track_title,
                    artist_name,
                    result_spotify_id=result.get("spotify_id"),
                    search_spotify_id=spotify_id,
                ):
                    logger.info(f"✅ Correspondance trouvée (résultat #{i})")
                    if fetch_details and result.get("detail_url"):
                        try:
                            details = await self._extract_track_details_async(result["detail_url"])
                            result.update(details)
                        except Exception as e:
                            logger.warning(f"⚠️ Détails inaccessibles: {e}")
                    log_api("SongBPM", f"search/{track_title}", True)
                    return result

            logger.warning(
                f"❌ Aucune correspondance parmi {min(len(results), max_results_to_check)} résultat(s)"
            )
            return None

        except PlaywrightTimeoutError:
            logger.error("❌ SongBPM: Timeout Playwright")
            await self._reset_browser_on_error_async()
            return None
        except Exception as e:
            logger.error(f"❌ SongBPM: Erreur recherche: {e}")
            await self._reset_browser_on_error_async()
            return None

    async def search_track_async(
        self,
        track_title: str,
        artist_name: str,
        spotify_id: str | None = None,
        max_results_to_check: int = 5,
        fetch_details: bool = True,
    ) -> dict[str, Any] | None:
        await self._ensure_driver_async()
        if not self.page:
            logger.error("❌ SongBPM: Browser non initialisé")
            return None

        logger.info(f"🔍 SongBPM: '{track_title}' par {artist_name}")
        result = await self._perform_search_async(
            track_title=track_title,
            artist_name=artist_name,
            spotify_id=spotify_id,
            max_results_to_check=max_results_to_check,
            fetch_details=fetch_details,
        )
        if result:
            log_api("SongBPM", f"search/{track_title}", True)
            return result

        # Fallback sans parenthèses
        if re.search(r"[\(\)\[\]]", track_title):
            cleaned = self._remove_parentheses_and_brackets(track_title)
            if cleaned and cleaned != track_title:
                logger.info(f"🔄 Nouvelle tentative: '{cleaned}'")
                result = await self._perform_search_async(
                    track_title=cleaned,
                    artist_name=artist_name,
                    spotify_id=spotify_id,
                    max_results_to_check=max_results_to_check,
                    fetch_details=fetch_details,
                    reload_homepage=False,
                )
                if result:
                    log_api("SongBPM", f"search/{track_title}", True)
                    return result

        log_api("SongBPM", f"search/{track_title}", False)
        return None
