"""Scraper pour récupérer le BPM depuis songbpm.com — version Playwright"""

import re
import threading
from typing import Any

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
)
from playwright.sync_api import (
    Playwright as PlaywrightInstance,
)
from playwright.sync_api import (
    TimeoutError as PlaywrightTimeoutError,
)

from src.config import SELENIUM_TIMEOUT
from src.models import Track
from src.scrapers.playwright_manager import get_playwright
from src.utils.llm_extractor import build_songbpm_prompt, get_shared_extractor
from src.utils.logger import get_logger, log_api

logger = get_logger(__name__)

PW_TIMEOUT = SELENIUM_TIMEOUT * 1000  # Playwright attend des ms


class SongBPMScraper:
    """Scrape songbpm.com pour obtenir le BPM, Key et Duration (Playwright)"""

    def __init__(self, headless: bool = False):
        self.base_url = "https://songbpm.com/"
        self.headless = headless
        self._playwright: PlaywrightInstance | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.spotify_id_pattern = re.compile(
            r"spotify\.com/(?:intl-[a-z]{2}/)?track/([a-zA-Z0-9]{22})"
        )
        self._owner_thread: int | None = None
        # Init PARESSEUSE : driver créé au premier usage, dans le thread utilisateur
        logger.info(f"SongBPMScraper initialisé (headless={self.headless}, driver lazy)")

    def _init_driver(self):
        try:
            logger.info(f"🌐 Initialisation Playwright SongBPM (headless={self.headless})...")
            self._playwright = get_playwright()
            self._owner_thread = threading.get_ident()
            self.browser = self._playwright.chromium.launch(
                headless=self.headless,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-webgl",
                    "--disable-webgl2",
                ],
            )
            self.context = self.browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            )
            # Bloquer les images pour accélérer
            self.context.route("**/*.{png,jpg,jpeg,gif,webp,svg,ico}", lambda route: route.abort())
            self.page = self.context.new_page()
            self.page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            logger.info("✅ SongBPM: Playwright initialisé avec succès")
        except Exception as e:
            logger.error(f"❌ Erreur initialisation Playwright SongBPM: {e}")
            self._cleanup_resources()
            raise

    def _cleanup_resources(self):
        # NB: ne pas stopper self._playwright — instance partagée (playwright_manager)
        for attr in ("page", "context", "browser"):
            obj = getattr(self, attr, None)
            if obj:
                try:
                    obj.close()
                except Exception:
                    pass
                setattr(self, attr, None)

    def _ensure_driver(self):
        """Crée (ou re-crée) le driver si absent ou créé dans un autre thread."""
        if self.page is not None and self._owner_thread == threading.get_ident():
            return
        if self.page is not None:
            logger.info("♻️ SongBPM: changement de thread détecté — réinitialisation du driver")
        self._cleanup_resources()
        self._init_driver()

    # ──────────────────────────────────────────────────────────────────────────
    # Méthodes de logique pure (inchangées par rapport à v1)
    # ──────────────────────────────────────────────────────────────────────────

    def _extract_spotify_id_from_url(self, url: str) -> str | None:
        if not url:
            return None
        match = self.spotify_id_pattern.search(url)
        return match.group(1) if match else None

    def _normalize_string(self, s: str) -> str:
        # Unifier les apostrophes typographiques (' ' ` ´) → apostrophe droite
        for apo in ("’", "‘", "`", "´"):
            s = s.replace(apo, "'")
        return " ".join(s.lower().strip().split())

    def _normalize_title_for_matching(self, title: str) -> str:
        patterns_to_remove = [
            r"\s*\(feat\.?\s+[^)]+\)",
            r"\s*\(ft\.?\s+[^)]+\)",
            r"\s*feat\.?\s+.+$",
            r"\s*ft\.?\s+.+$",
            r"\s*\[feat\.?\s+[^\]]+\]",
            r"\s*\[ft\.?\s+[^\]]+\]",
        ]
        normalized = title
        for pattern in patterns_to_remove:
            normalized = re.sub(pattern, "", normalized, flags=re.IGNORECASE)
        return normalized.strip()

    def _remove_parentheses_and_brackets(self, title: str) -> str:
        cleaned = re.sub(r"\s*\([^)]*\)", "", title)
        cleaned = re.sub(r"\s*\[[^\]]*\]", "", cleaned)
        return cleaned.strip()

    def _title_key(self, title: str) -> str:
        """
        Clé de titre robuste pour le matching : retire le featuring et les
        (...)/[...], puis remplace la ponctuation/tirets par des espaces.
        Permet de rapprocher 'Booska Pogo' et 'FREESTYLE BOOSKA-POGO'.
        """
        t = self._normalize_title_for_matching(title)
        t = self._remove_parentheses_and_brackets(t)
        t = self._normalize_string(t)
        t = re.sub(r"[\-_/.,:;!?\"'’]+", " ", t)
        return " ".join(t.split())

    def _match_track(
        self,
        result_title: str,
        result_artist: str,
        search_title: str,
        search_artist: str,
        result_spotify_id: str | None = None,
        search_spotify_id: str | None = None,
    ) -> bool:
        if result_spotify_id and search_spotify_id:
            match = result_spotify_id == search_spotify_id
            if match:
                logger.info(f"✅ MATCH PARFAIT via Spotify ID: {search_spotify_id}")
            else:
                logger.info("❌ REJET: Spotify IDs différents")
            return match

        # 1) Artiste = ancre STRICTE (évite les faux positifs)
        if self._normalize_string(result_artist) != self._normalize_string(search_artist):
            logger.info("❌ REJET: artiste différent")
            return False

        # 2) Titre = comparaison assouplie (égalité / inclusion / similarité)
        rt = self._title_key(result_title)
        st = self._title_key(search_title)
        if not rt or not st:
            logger.info("❌ REJET: titre vide après normalisation")
            return False

        from difflib import SequenceMatcher

        ratio = SequenceMatcher(None, rt, st).ratio()

        if rt == st:
            logger.info("✅ Match titre exact + artiste")
            return True
        # Inclusion (ex. 'booska pogo' ⊂ 'freestyle booska pogo'), garde-fou longueur
        if (st in rt or rt in st) and min(len(st), len(rt)) >= 4:
            logger.info(f"✅ Match titre par inclusion + artiste ('{st}' ↔ '{rt}')")
            return True
        if ratio >= 0.85:
            logger.info(f"✅ Match titre par similarité {ratio:.2f} + artiste")
            return True

        logger.info(f"❌ REJET: titre trop différent (sim {ratio:.2f}: '{st}' vs '{rt}')")
        return False

    # ──────────────────────────────────────────────────────────────────────────
    # Gestion cookies (Playwright)
    # ──────────────────────────────────────────────────────────────────────────

    def _handle_cookies(self):
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
                    btn = self.page.query_selector(selector)
                    if btn and btn.is_visible():
                        btn.click()
                        logger.info(f"✅ Popup cookies fermé via: {selector}")
                        return
                except Exception:
                    continue
        except Exception as e:
            logger.debug(f"Gestion cookies (non bloquant): {e}")

    # ──────────────────────────────────────────────────────────────────────────
    # Extraction des détails (page de détail d'un morceau)
    # ──────────────────────────────────────────────────────────────────────────

    def _extract_track_details(self, detail_url: str, timeout: int = 30) -> dict[str, Any]:
        details = {}
        try:
            # Les liens du site sont relatifs (/@artiste/titre) → URL absolue requise
            if detail_url.startswith("/"):
                detail_url = "https://songbpm.com" + detail_url
            logger.info(f"📄 Navigation détails: {detail_url}")
            self.page.goto(detail_url, wait_until="domcontentloaded", timeout=timeout * 1000)

            content_selectors = [
                "div.lg\\:prose-xl",
                "div[class*='prose']",
                "main",
                "article",
            ]
            full_text = None
            for selector in content_selectors:
                el = self.page.query_selector(selector)
                if el and el.is_visible():
                    full_text = el.inner_text()
                    break

            if not full_text:
                body = self.page.query_selector("body")
                full_text = body.inner_text() if body else ""

            clean_text = re.sub(r"\s+", " ", full_text).replace("\xa0", " ")
            details = self._details_from_text(clean_text)
            logger.info(f"✅ Détails extraits: {details}")
        except PlaywrightTimeoutError:
            logger.warning(f"⏰ Timeout ({timeout}s) lors de la récupération des détails")
        except Exception as e:
            logger.error(f"❌ Erreur extraction détails: {e}")
        return details

    def _details_from_text(self, clean_text: str) -> dict[str, Any]:
        """Mode/key/time_signature depuis le texte de la page de détail
        (regex + fallback LLM) — logique PURE, commune sync/async (F3c)."""
        details: dict[str, Any] = {}

        mode_match = re.search(r"key\s+and\s+a\s+(major|minor)\s+mode", clean_text, re.IGNORECASE)
        if not mode_match:
            mode_match = re.search(r"\b(major|minor)\s+mode\b", clean_text, re.IGNORECASE)
        if mode_match:
            details["mode"] = mode_match.group(1).lower()

        key_match = re.search(r"with\s+a\s+([A-G][\#b♯♭/]+)\s+key", clean_text, re.IGNORECASE)
        if key_match:
            details["key_from_paragraph"] = key_match.group(1).strip()

        time_sig_match = re.search(
            r"time\s+signature\s+of\s+(\d+)\s+beats?\s+per\s+bar", clean_text, re.IGNORECASE
        )
        if time_sig_match:
            details["time_signature"] = int(time_sig_match.group(1))

        # Fallback LLM : complète les champs que les regex n'ont pas trouvés
        missing = {"mode", "key_from_paragraph", "time_signature"} - set(details)
        if missing:
            llm_details = self._extract_details_with_llm(clean_text)
            for key, value in llm_details.items():
                if key in missing:
                    details[key] = value
                    logger.info(f"🤖 SongBPM LLM: {key} = {value}")

        return details

    def _extract_details_with_llm(self, clean_text: str) -> dict[str, Any]:
        """
        Fallback LLM : extrait mode/key/time_signature du texte de la page
        quand les regex échouent. Valeurs validées strictement (pas d'hallucination).
        """
        details: dict[str, Any] = {}
        llm = get_shared_extractor()
        if not llm or not clean_text:
            return details

        data = llm.extract_json(build_songbpm_prompt(clean_text[:4000]), max_tokens=128)
        if not data:
            return details

        mode = data.get("mode")
        if isinstance(mode, str) and mode.lower() in ("major", "minor"):
            details["mode"] = mode.lower()

        key = data.get("key")
        if isinstance(key, str) and re.fullmatch(r"[A-G][#b♯♭]?(?:/[A-G][#b♯♭]?)?", key.strip()):
            details["key_from_paragraph"] = key.strip()

        ts = data.get("time_signature")
        if isinstance(ts, int) and 2 <= ts <= 12:
            details["time_signature"] = ts

        return details

    # ──────────────────────────────────────────────────────────────────────────
    # Recherche sur SongBPM
    # ──────────────────────────────────────────────────────────────────────────

    def _get_search_results(self) -> list[dict[str, Any]]:
        results = []
        try:
            containers = self.page.query_selector_all("div.bg-card")
            logger.debug(f"📋 {len(containers)} conteneurs trouvés")

            for container in containers:
                try:
                    result = {}

                    track_links = container.query_selector_all("a[href*='/@']")
                    if not track_links:
                        continue

                    track_link = track_links[0]
                    href = track_link.get_attribute("href") or ""
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
                    info_divs = track_link.query_selector_all("div.flex-1")
                    for info_div in info_divs:
                        paragraphs = info_div.query_selector_all("p")
                        if len(paragraphs) >= 2:
                            artist_class = paragraphs[0].get_attribute("class") or ""
                            title_class = paragraphs[1].get_attribute("class") or ""
                            if "text-sm" in artist_class and (
                                "text-lg" in title_class or "text-2xl" in title_class
                            ):
                                result["artist"] = paragraphs[0].inner_text().strip()
                                result["title"] = paragraphs[1].inner_text().strip()
                                break

                    if "title" not in result or "artist" not in result:
                        continue

                    # BPM, Key, Duration
                    metrics = track_link.query_selector_all("div.flex.flex-1.flex-col.items-center")
                    for metric in metrics:
                        spans = metric.query_selector_all("span")
                        if len(spans) >= 2:
                            label = spans[0].inner_text().strip().upper()
                            value = spans[1].inner_text().strip()
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
                    spotify_links = container.query_selector_all("a[href*='spotify.com/track/']")
                    if spotify_links:
                        spotify_url = spotify_links[0].get_attribute("href") or ""
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

    def _perform_search(
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
                self.page.goto(self.base_url, wait_until="domcontentloaded", timeout=30_000)
                self._handle_cookies()

            search_selector = "input[name='query'][placeholder='type a song, get a bpm']"
            try:
                self.page.wait_for_selector(search_selector, timeout=10_000)
            except PlaywrightTimeoutError:
                logger.error("⏰ Champ de recherche introuvable")
                return None

            search_query = f"{artist_name} {track_title}"
            logger.info(f"🔍 Recherche: '{search_query}'")
            self.page.fill(search_selector, search_query)
            self.page.press(search_selector, "Enter")

            # Attendre les résultats
            try:
                self.page.wait_for_selector("div.bg-card", timeout=10_000)
            except PlaywrightTimeoutError:
                logger.warning(f"❌ Aucun résultat pour '{track_title}'")
                return None

            results = self._get_search_results()
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
                            details = self._extract_track_details(result["detail_url"])
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
            self._reset_browser_on_error()
            return None
        except Exception as e:
            logger.error(f"❌ SongBPM: Erreur recherche: {e}")
            self._reset_browser_on_error()
            return None

    def search_track(
        self,
        track_title: str,
        artist_name: str,
        spotify_id: str | None = None,
        max_results_to_check: int = 5,
        fetch_details: bool = True,
    ) -> dict[str, Any] | None:
        self._ensure_driver()
        if not self.page:
            logger.error("❌ SongBPM: Browser non initialisé")
            return None

        logger.info(f"🔍 SongBPM: '{track_title}' par {artist_name}")
        result = self._perform_search(
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
                result = self._perform_search(
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

    def enrich_track_data(
        self, track: Track, force_update: bool = False, artist_tracks: list[Track] | None = None
    ) -> bool:
        try:
            self._ensure_driver()
            artist_name = track.artist.name if hasattr(track.artist, "name") else str(track.artist)
            spotify_id = getattr(track, "spotify_id", None)

            track_data = self.search_track(
                track.title, artist_name, spotify_id=spotify_id, fetch_details=False
            )
            if not track_data:
                return False

            updated = False

            if (force_update or not track.bpm) and track_data.get("bpm"):
                track.bpm = track_data["bpm"]
                updated = True

            key_value = track_data.get("key")
            if key_value and (force_update or not getattr(track, "key", None)):
                track.key = key_value
                updated = True

            songbpm_sid = track_data.get("spotify_id")
            if songbpm_sid and (force_update or not getattr(track, "spotify_id", None)):
                track.spotify_id = songbpm_sid
                updated = True

            if (force_update or not getattr(track, "duration", None)) and track_data.get(
                "duration"
            ):
                duration_str = track_data["duration"]
                try:
                    if isinstance(duration_str, str) and ":" in duration_str:
                        parts = duration_str.split(":")
                        track.duration = int(parts[0]) * 60 + int(parts[1])
                        updated = True
                    elif isinstance(duration_str, (int, float)):
                        track.duration = int(duration_str)
                        updated = True
                except ValueError:
                    pass

            detail_url = track_data.get("detail_url")
            if detail_url and key_value:
                try:
                    details = self._extract_track_details(detail_url, timeout=30)
                    if details.get("mode") and (force_update or not getattr(track, "mode", None)):
                        track.mode = details["mode"]
                        updated = True
                    if details.get("key_from_paragraph") and (
                        force_update or not getattr(track, "key", None)
                    ):
                        track.key = details["key_from_paragraph"]
                        updated = True
                    final_key = getattr(track, "key", None)
                    final_mode = getattr(track, "mode", None)
                    if (
                        final_key
                        and final_mode
                        and (force_update or not getattr(track, "musical_key", None))
                    ):
                        try:
                            from src.utils.music_theory import key_mode_to_french_from_string

                            _mk = key_mode_to_french_from_string(final_key, final_mode)
                            if _mk:  # None si key/mode non interprétables
                                track.musical_key = _mk
                                updated = True
                        except Exception:
                            pass
                except Exception as e:
                    logger.warning(f"⚠️ Erreur mode pour '{track.title}': {e}")

            return updated

        except Exception as e:
            logger.error(f"❌ SongBPM ERREUR pour {track.title}: {e}")
            return False

    def _reset_browser_on_error(self):
        logger.warning("⚠️ Réinitialisation du browser après erreur")
        self._cleanup_resources()

    def close(self):
        self._cleanup_resources()
        logger.info("✅ SongBPM: Browser fermé")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
