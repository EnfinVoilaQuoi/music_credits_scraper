"""Scraper BPM Finder — variante ASYNC (Phase F3d), vivant dans la boucle asyncio.

⚠️ PRÉPARÉ, NON VÉRIFIÉ EN CONDITIONS RÉELLES (décision « préparer sans vérifier »,
2026-07-20) : ce jumeau n'est PAS activé par défaut (aucune `async_scraper_factory`
fournie au `BpmFinderProvider` dans `DataEnricher.__init__`) → `enrich_async`
retombe sur le pont sync F2. Il exige une passe de vérification avec le compte
BPM Finder (login modal, session persistée, quota) ET le backend audioaidynamics
rétabli (HTTP 500/401 observés le 2026-07-20).

Sous-classe de `BPMFinderScraper` : la logique PURE est héritée (cache, extraction
du videoId, parsing des cartes `_cards_from_text` / `_card_to_result`, listener
d'erreurs backend `_log_api_error`, disponibilité, consentement cookies). Les
méthodes touchant Playwright sont réécrites en async (MÊMES sélecteurs, MÊMES
timeouts, MÊME séquence : analyzer → login modal → champ YouTube → Upload →
polling des cartes par diff). Le browser naît, travaille et meurt DANS la boucle
(`get_playwright_async`), fermé par `aclose()`.

ACTIVATION : fournir `async_scraper_factory=lambda: BPMFinderScraperAsync(headless=True)`
au `BpmFinderProvider` (montage `DataEnricher`) et ajouter le provider à
`DataEnricher.aclose_async_scrapers` (fermeture des browsers de la boucle).
"""

import logging
import re
import time
from pathlib import Path

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from src.config import (
    BPMFINDER_EMAIL,
    BPMFINDER_PASSWORD,
    BPMFINDER_SESSION_FILE,
    DATA_DIR,
)
from src.scrapers.bpmfinder_scraper import ANALYZER_URL, BPMFinderScraper
from src.scrapers.playwright_manager import get_playwright_async

logger = logging.getLogger("BPMFinderScraper")


class BPMFinderScraperAsync(BPMFinderScraper):
    """Variante async du scraper BPM Finder (mêmes sélecteurs, même séquence)."""

    def __init__(self, headless: bool = True):
        super().__init__(headless=headless)
        logger.info(f"BPMFinderScraperAsync initialisé (headless={headless}, driver lazy)")

    # ── Garde-fous : pas d'API sync sur l'instance async ────────────────────

    def _ensure_driver(self):
        raise RuntimeError("Instance async : utiliser les méthodes *_async")

    def analyze(self, *args, **kwargs):
        raise RuntimeError("Instance async : utiliser analyze_async")

    def analyze_file(self, *args, **kwargs):
        raise RuntimeError("Instance async : analyze_file non porté en async")

    def close(self):
        raise RuntimeError("Instance async : utiliser aclose()")

    # ── Driver / session (miroirs async) ────────────────────────────────────

    async def _ensure_driver_async(self):
        """Crée le browser/context/page dans la boucle (pas d'affinité thread à
        gérer — un seul thread de boucle, contrairement à la voie sync)."""
        if self.page is not None:
            return
        self._playwright = await get_playwright_async()
        self.browser = await self._playwright.chromium.launch(headless=self.headless)
        storage = str(BPMFINDER_SESSION_FILE) if Path(BPMFINDER_SESSION_FILE).exists() else None
        self.context = await self.browser.new_context(storage_state=storage)
        await self.context.add_init_script(self._CONSENT_INIT)
        self.context.on("response", self._log_api_error)  # handler sync (lit des propriétés)
        self.page = await self.context.new_page()
        logger.info(
            f"🌐 BPM Finder: Playwright async initialisé "
            f"(session {'reprise' if storage else 'neuve'})"
        )

    async def _cleanup_resources_async(self):
        # NB: ne pas stopper self._playwright — instance partagée (playwright_manager)
        for attr in ("page", "context", "browser"):
            obj = getattr(self, attr, None)
            if obj:
                try:
                    await obj.close()
                except PlaywrightError:
                    pass
                setattr(self, attr, None)
        self._playwright = None

    async def aclose(self):
        await self._cleanup_resources_async()
        logger.info("✅ BPMFinderScraper async fermé")

    async def _save_session_async(self):
        try:
            Path(BPMFINDER_SESSION_FILE).parent.mkdir(parents=True, exist_ok=True)
            await self.context.storage_state(path=str(BPMFINDER_SESSION_FILE))
            logger.info("💾 Session BPM Finder sauvegardée")
        except (PlaywrightError, OSError) as e:
            logger.warning(f"Session BPM Finder non sauvegardée: {e}")

    async def _dismiss_cookie_overlay_async(self):
        """Miroir async de `_dismiss_cookie_overlay` (même JS injecté)."""
        try:
            await self.page.evaluate("""
                () => {
                  const fc = document.querySelector('.fc-message-root');
                  if (fc) {
                    const noBtn = [...fc.querySelectorAll('button, a, .fc-button')].find(
                      b => /do not consent|reject|refuser|no thanks|dismiss|close/i.test(b.textContent));
                    if (noBtn) noBtn.click();
                    document.querySelectorAll('.fc-message-root, .fc-dialog-overlay').forEach(e => e.remove());
                    document.documentElement.style.overflow = '';
                    document.body.style.overflow = '';
                  }
                  document.querySelectorAll(
                    'ins.adsbygoogle-noablate, ins.adsbygoogle[data-anchor-status]'
                  ).forEach(e => e.remove());
                  const btn = [...document.querySelectorAll('button, a')].find(
                    b => /essential|necessary|reject|decline|refuser|only|accept|continue/i.test(b.textContent));
                  if (btn) { btn.click(); return; }
                  document.querySelectorAll('div').forEach(d => {
                    const s = getComputedStyle(d);
                    if (s.position === 'fixed' && (d.className||'').includes('h-[100vh]')
                        && /cookie|continue using/i.test(d.textContent)) d.remove();
                  });
                }
            """)
        except PlaywrightError:
            pass

    async def _goto_analyzer_async(self, force: bool = False):
        if force or not (self.page.url or "").startswith(ANALYZER_URL):
            await self.page.goto(ANALYZER_URL, wait_until="domcontentloaded", timeout=45_000)
            try:
                await self.page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeoutError:
                pass
            await self.page.wait_for_timeout(1_500)
            await self._dismiss_cookie_overlay_async()

    async def _find_youtube_input_async(self, timeout_ms: int = 15_000):
        """Champ URL YouTube (placeholder « Or Enter YouTube URL »). None si absent."""
        try:
            loc = self.page.get_by_placeholder("YouTube", exact=False)
            await loc.wait_for(state="visible", timeout=timeout_ms)
            return loc.first
        except PlaywrightError:
            pass
        for sel in (
            "input[placeholder*='YouTube' i]",
            "input[placeholder*='youtube' i]",
            "input[type='text']",
        ):
            try:
                el = await self.page.wait_for_selector(sel, state="visible", timeout=3_000)
                if el:
                    return el
            except PlaywrightError:
                continue
        return None

    # ── Login (miroirs async) ───────────────────────────────────────────────

    async def _is_logged_in_async(self) -> bool:
        try:
            cookies = await self.context.cookies()
            return any(c.get("name") == "access_token" for c in cookies)
        except PlaywrightError:
            return False

    async def _looks_logged_out_async(self) -> bool:
        if await self._is_logged_in_async():
            return False
        try:
            return await self.page.locator("span:text-is('LOGIN')").count() > 0
        except PlaywrightError:
            return False

    async def _try_login_async(self) -> bool:
        if not (BPMFINDER_EMAIL and BPMFINDER_PASSWORD):
            logger.error(
                "BPM Finder: identifiants absents (BPMFINDER_EMAIL/PASSWORD) "
                "et pas de session — lancer scripts/bpmfinder_login.py"
            )
            return False
        try:
            pwd_sel = "input[type='password']"
            if not await self.page.query_selector(pwd_sel):
                await self.page.locator("span:text-is('LOGIN')").first.click()
                await self.page.wait_for_selector(pwd_sel, timeout=10_000)

            await self.page.fill("input[type='email']", BPMFINDER_EMAIL)
            await self.page.fill(pwd_sel, BPMFINDER_PASSWORD)
            await self.page.click("button:has-text('Login')")
            await self.page.wait_for_timeout(4_000)

            await self._goto_analyzer_async(force=True)
            if await self._looks_logged_out_async():
                logger.error(
                    "BPM Finder: login refusé (vérifier identifiants) — "
                    "ou UI de login inattendue : scripts/bpmfinder_login.py"
                )
                return False
            await self._save_session_async()
            logger.info("✅ BPM Finder: connecté")
            return True
        except (PlaywrightError, OSError) as e:
            logger.error(
                f"BPM Finder: login échoué: {e} — bootstrap manuel : scripts/bpmfinder_login.py"
            )
            return False

    # ── Analyse (miroir async) ──────────────────────────────────────────────

    async def _cards_async(self) -> set[tuple[str, str, str, str]]:
        try:
            text = await self.page.inner_text("body") or ""
        except PlaywrightError:
            return set()
        return self._cards_from_text(text)

    async def _dump_debug_state_async(self, label: str):
        if getattr(self, "_debug_dumped", False):
            return
        self._debug_dumped = True
        try:
            diag = DATA_DIR / "diagnostics"
            diag.mkdir(parents=True, exist_ok=True)
            safe = re.sub(r"[^\w.-]", "_", label)[:60]
            stamp = time.strftime("%Y%m%d_%H%M%S")
            await self.page.screenshot(path=str(diag / f"bpmfinder_{stamp}_{safe}.png"))
            (diag / f"bpmfinder_{stamp}_{safe}.txt").write_text(
                await self.page.inner_text("body") or "", encoding="utf-8"
            )
            logger.warning(f"BPM Finder: état de la page capturé dans {diag}")
        except (PlaywrightError, OSError):
            pass

    async def _await_and_parse_async(self, before, timeout_s: int, label: str) -> dict | None:
        """Attend la NOUVELLE carte (diff avant/après) et la parse en dict."""
        deadline = time.time() + timeout_s
        new_card = None
        while time.time() < deadline:
            await self.page.wait_for_timeout(2_000)
            fresh = (await self._cards_async()) - before
            if fresh:
                new_card = sorted(fresh)[0]
                break
            if self._last_api_error is not None:
                logger.warning(
                    f"BPM Finder: backend HTTP {self._last_api_error} → abandon rapide pour {label}"
                )
                return None
        if not new_card:
            logger.warning(f"BPM Finder: pas de résultat en {timeout_s}s pour {label}")
            await self._dump_debug_state_async(f"timeout_{label}")
            return None
        return self._card_to_result(new_card, label)

    async def analyze_async(self, youtube_url: str, timeout_s: int = 90) -> dict | None:
        """Miroir async de `analyze` — mêmes retours ({...} | None) et mêmes
        `last_failure_reason` ('timeout'|'backend'|'ui'|'login'|None)."""
        vid = self._video_id(youtube_url)
        if not vid:
            logger.warning(f"BPM Finder: lien YouTube invalide: {youtube_url!r}")
            return None
        if vid in self.cache:
            logger.debug(f"BPM Finder: cache hit {vid}")
            return self.cache[vid]

        await self._ensure_driver_async()
        await self._goto_analyzer_async()

        if not await self._is_logged_in_async():
            if not await self._try_login_async():
                self.last_failure_reason = "login"
                return None
        else:
            logger.debug("BPM Finder: session valide (access_token) — pas de login")

        self.last_failure_reason = None
        for attempt in range(2):
            if attempt:
                logger.info(f"BPM Finder: nouvel essai ({attempt + 1}/2) pour {vid}")
                await self._goto_analyzer_async(force=True)
            self._last_api_error = None  # armé pour cette tentative

            before = await self._cards_async()
            url_input = await self._find_youtube_input_async()
            if not url_input:
                await self._goto_analyzer_async(force=True)
                url_input = await self._find_youtube_input_async()
            if not url_input:
                try:
                    dbg = str(DATA_DIR / "bpmfinder_debug.png")
                    await self.page.screenshot(path=dbg)
                    logger.error(f"BPM Finder: champ YouTube introuvable — capture: {dbg}")
                except (PlaywrightError, OSError):
                    logger.error("BPM Finder: champ YouTube introuvable (UI changée ?)")
                self.last_failure_reason = "ui"
                return None
            try:
                await url_input.fill(youtube_url)
                await self._dismiss_cookie_overlay_async()
                try:
                    await self.page.click("button:has-text('Upload')", timeout=15_000)
                except PlaywrightError:
                    await self._dismiss_cookie_overlay_async()
                    await self.page.click("button:has-text('Upload')", timeout=10_000)
            except PlaywrightError as e:
                logger.error(f"BPM Finder: saisie/Upload échoué: {e}")
                await self._dump_debug_state_async(f"upload_{vid}")
                self.last_failure_reason = "ui"
                return None

            result = await self._await_and_parse_async(before, timeout_s, label=vid)
            if result:
                self.cache[vid] = result
                self._save_cache()
                await self._save_session_async()  # prolonge la session (cookies rafraîchis)
                return result

            status = self._last_api_error
            if status is None:
                self.last_failure_reason = "timeout"
                return None
            if 500 <= status < 600 and attempt == 0:
                continue  # 5xx : on retente une fois
            self.last_failure_reason = "backend"
            return None

        self.last_failure_reason = "backend"
        return None
