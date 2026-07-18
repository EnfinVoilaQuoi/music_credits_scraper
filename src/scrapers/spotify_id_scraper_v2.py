"""
Scraper Spotify ID — version Playwright
Recherche directe sur open.spotify.com/search
"""

import json
import logging
import re
import threading
import urllib.parse

import requests
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

from src.scrapers.playwright_manager import get_playwright
from src.utils.llm_extractor import build_spotify_match_prompt, get_shared_extractor

logger = logging.getLogger("SpotifyIDScraper")


class SpotifyIDScraper:
    """Scraper pour récupérer les IDs Spotify via recherche directe (Playwright)"""

    def __init__(self, cache_file: str = "spotify_ids_cache.json", headless: bool = True):
        self.cache_file = cache_file
        self.cache = self._load_cache()
        self.headless = headless
        self._playwright: PlaywrightInstance | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self._timeout = 20_000  # ms

        self.spotify_id_patterns = [
            r"open\.spotify\.com/(?:intl-[a-z]{2}/)?track/([a-zA-Z0-9]{22})",
            r"spotify\.com/track/([a-zA-Z0-9]{22})",
            r"spotify:track:([a-zA-Z0-9]{22})",
            r"/track/([a-zA-Z0-9]{22})(?:\?|$|/)",
        ]

        self.spotify_artist_id_patterns = [
            r"open\.spotify\.com/(?:intl-[a-z]{2}/)?artist/([a-zA-Z0-9]{22})",
            r"spotify\.com/artist/([a-zA-Z0-9]{22})",
            r"spotify:artist:([a-zA-Z0-9]{22})",
            r"/artist/([a-zA-Z0-9]{22})(?:\?|$|/)",
        ]

        self._owner_thread: int | None = None
        # Init PARESSEUSE : le driver est créé au premier usage, dans le thread
        # qui l'utilise (Playwright Sync est lié au thread de création)
        logger.info(f"SpotifyIDScraper initialisé (headless={headless}, driver lazy)")

    # ──────────────────────────────────────────────────────────────────────────
    # Init / cleanup
    # ──────────────────────────────────────────────────────────────────────────

    def _ensure_driver(self):
        """Crée (ou re-crée) le driver si absent ou créé dans un autre thread."""
        if self.page is not None and self._owner_thread == threading.get_ident():
            return
        if self.page is not None:
            logger.info("♻️ SpotifyID: changement de thread détecté — réinitialisation du driver")
        self._cleanup_resources()
        self._init_driver()

    def _init_driver(self):
        try:
            logger.info(f"🌐 Initialisation Playwright SpotifyID (headless={self.headless})...")
            self._playwright = get_playwright()
            self._owner_thread = threading.get_ident()
            self.browser = self._playwright.chromium.launch(
                headless=self.headless,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            )
            self.context = self.browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            )
            self.context.route("**/*.{png,jpg,jpeg,gif,webp,svg,ico}", lambda route: route.abort())
            self.page = self.context.new_page()
            self.page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            logger.info("✅ SpotifyIDScraper: Playwright initialisé")
        except Exception as e:
            logger.error(f"❌ Erreur initialisation Playwright SpotifyID: {e}")
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
        self._playwright = None

    # ──────────────────────────────────────────────────────────────────────────
    # Cache
    # ──────────────────────────────────────────────────────────────────────────

    def _load_cache(self):
        try:
            with open(self.cache_file, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_cache(self):
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Erreur sauvegarde cache: {e}")

    def _get_cache_key(self, artist: str, title: str) -> str:
        return f"{artist.lower().strip()}::{title.lower().strip()}"

    # ──────────────────────────────────────────────────────────────────────────
    # Logique pure (inchangée)
    # ──────────────────────────────────────────────────────────────────────────

    def extract_artist_id_from_url(self, url: str) -> str | None:
        # Drift site 2026-07-18 : la recherche Spotify sert des hrefs RELATIFS
        # (`/artist/<id>`) — l'ancien garde « 'spotify' dans l'URL » les rejetait
        # tous (source muette). Les chemins relatifs /artist/ sont donc admis.
        if not url or ("spotify" not in url.lower() and not url.startswith("/artist/")):
            return None
        for pattern in self.spotify_artist_id_patterns:
            match = re.search(pattern, url)
            if match:
                aid = match.group(1)
                if len(aid) == 22 and re.match(r"^[a-zA-Z0-9_-]+$", aid):
                    return aid
        return None

    def extract_spotify_id_from_url(self, url: str) -> str | None:
        # Drift site 2026-07-18 : hrefs RELATIFS `/track/<id>` (cf. artist ci-dessus).
        if not url or ("spotify" not in url.lower() and not url.startswith("/track/")):
            return None
        for pattern in self.spotify_id_patterns:
            match = re.search(pattern, url)
            if match:
                sid = match.group(1)
                if len(sid) == 22 and re.match(r"^[a-zA-Z0-9_-]+$", sid):
                    return sid
        return None

    @staticmethod
    def _normalize_apostrophes(s: str) -> str:
        """Unifie les apostrophes typographiques (' ' ` ´) → apostrophe droite."""
        for apo in ("’", "‘", "`", "´"):
            s = s.replace(apo, "'")
        return s

    def _calculate_relevance(self, artist: str, title: str, text: str) -> float:
        if not text:
            return 0.5
        score = 0.0
        artist_lower = self._normalize_apostrophes(artist).lower()
        title_lower = self._normalize_apostrophes(title).lower()
        text_lower = self._normalize_apostrophes(text).lower()
        if artist_lower in text_lower:
            score += 0.4
        if title_lower in text_lower:
            score += 0.4
        for word in artist_lower.split():
            if len(word) > 2 and word in text_lower:
                score += 0.1
        for word in title_lower.split():
            if len(word) > 2 and word in text_lower:
                score += 0.1
        return min(score, 1.0)

    # ──────────────────────────────────────────────────────────────────────────
    # Cookies
    # ──────────────────────────────────────────────────────────────────────────

    def _handle_cookies(self):
        cookie_selectors = [
            "button[id='onetrust-accept-btn-handler']",
            "button[data-testid='accept-all-cookies']",
            "button[class*='accept-all']",
            "#onetrust-accept-btn-handler",
        ]
        for selector in cookie_selectors:
            try:
                btn = self.page.query_selector(selector)
                if btn and btn.is_visible():
                    btn.click()
                    logger.info(f"✅ Cookies acceptés via: {selector}")
                    return
            except Exception:
                continue

    # ──────────────────────────────────────────────────────────────────────────
    # Recherche principale
    # ──────────────────────────────────────────────────────────────────────────

    def get_spotify_id(self, artist: str, title: str) -> str | None:
        logger.info(f"🔍 Recherche ID Spotify pour: '{artist}' - '{title}'")

        cache_key = self._get_cache_key(artist, title)
        if cache_key in self.cache:
            cached = self.cache[cache_key]
            if cached and cached != "not_found":
                logger.info(f"✅ ID trouvé en cache: {cached}")
                return cached
            # 'not_found' n'est PAS définitif (a pu être causé par une erreur
            # passagère, ex: driver mort) → on retente la recherche

        try:
            self._ensure_driver()
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
                self.page.goto(spotify_url, wait_until="domcontentloaded", timeout=30_000)
                self._handle_cookies()

                # Attendre qu'un lien track apparaisse
                try:
                    self.page.wait_for_selector("a[href*='/track/']", timeout=self._timeout)
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
                    links = self.page.query_selector_all(selector)
                    for link in links[:10]:
                        try:
                            href = link.get_attribute("href") or ""
                            if "/track/" not in href:
                                continue
                            sid = self.extract_spotify_id_from_url(href)
                            if not sid or sid in [t["id"] for t in found_tracks]:
                                continue
                            try:
                                link_text = link.inner_text().lower()
                                parent = link.query_selector("..")
                                parent_text = parent.inner_text().lower() if parent else ""
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
                # Driver mort (thread/greenlet/page fermée) → re-création et on continue
                err_str = str(e).lower()
                if any(s in err_str for s in ("thread", "greenlet", "closed", "crashed")):
                    try:
                        self._cleanup_resources()
                        self._init_driver()
                    except Exception:
                        logger.error("❌ Impossible de réinitialiser le driver SpotifyID")
                        break
                continue

        if found_tracks:
            found_tracks.sort(key=lambda x: x["relevance"], reverse=True)
            best = found_tracks[0]

            # Fallback LLM : si le choix heuristique est ambigu, demander au LLM
            if len(found_tracks) > 1 and best["relevance"] < 0.8:
                llm_choice = self._select_track_with_llm(artist, title, found_tracks)
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

    def _select_track_with_llm(self, artist: str, title: str, found_tracks: list) -> dict | None:
        """
        Fallback LLM : choisit le bon résultat parmi les candidats ambigus.
        Retourne le candidat choisi, ou None si LLM indisponible/sans réponse valide.
        L'ID retourné vient toujours d'un candidat réel (jamais généré par le LLM).
        """
        llm = get_shared_extractor()
        if not llm:
            return None

        candidates = [
            {"index": i, "text": (t.get("text") or "")[:150].strip() or t["id"]}
            for i, t in enumerate(found_tracks[:8])
        ]
        data = llm.extract_json(
            build_spotify_match_prompt(artist, title, candidates), max_tokens=32
        )
        if not data:
            return None

        idx = data.get("best_index")
        if isinstance(idx, int) and 0 <= idx < len(found_tracks[:8]):
            return found_tracks[idx]
        return None

    # ── Artistes d'un track via la page EMBED (server-rendered, fiable) ───────
    #
    # La page track normale est une app JS : son HTML brut est une coquille dont
    # le premier `spotify:artist:` venu appartient souvent aux recommandations
    # (bug historique : le vote d'ID artiste élisait Limsa d'Aulnay pour des
    # morceaux crédités ISHA seul). La page /embed/track/{id} contient un JSON
    # __NEXT_DATA__ avec les crédits EXACTS du morceau : [{name, uri}].

    @staticmethod
    def _parse_embed_artists(html: str) -> list[dict[str, str]]:
        """Extrait [{'name', 'id'}] du __NEXT_DATA__ d'une page embed."""
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html or "", re.S)
        if not m:
            return []
        try:
            data = json.loads(m.group(1))
            entity = (
                (((data.get("props") or {}).get("pageProps") or {}).get("state") or {}).get("data")
                or {}
            ).get("entity") or {}
            out = []
            for a in entity.get("artists") or []:
                uri = a.get("uri") or ""
                if uri.startswith("spotify:artist:"):
                    out.append(
                        {"name": (a.get("name") or "").strip(), "id": uri.rsplit(":", 1)[-1]}
                    )
            return out
        except (ValueError, AttributeError, TypeError) as e:
            logger.debug(f"Parse __NEXT_DATA__ embed échoué: {e}")
            return []

    def get_track_artists(self, track_spotify_id: str) -> list[dict[str, str]]:
        """Artistes crédités sur un track : [{'name', 'id'}], ordre Spotify.

        Voie 1 : requests sur la page embed (léger, server-rendered).
        Voie 2 : Playwright sur la même page si requests échoue.
        """
        url = f"https://open.spotify.com/embed/track/{track_spotify_id}"

        try:
            resp = requests.get(
                url,
                timeout=15,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    )
                },
            )
            if resp.ok:
                resp.encoding = "utf-8"  # noms d'artistes accentués (anti-mojibake)
                artists = self._parse_embed_artists(resp.text)
                if artists:
                    return artists
        except requests.RequestException as e:
            logger.debug(f"Embed via requests échoué ({track_spotify_id}): {e}")

        try:
            self._ensure_driver()
            self.page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            return self._parse_embed_artists(self.page.content())
        except Exception as e:
            logger.error(f"❌ Embed via Playwright échoué ({track_spotify_id}): {e}")
            return []

    def get_artist_id_from_track(
        self, track_spotify_id: str, expected_name: str = None
    ) -> str | None:
        """
        Déduit l'ID Spotify de l'ARTISTE depuis un de ses morceaux (page embed).

        expected_name : si fourni, retourne l'ID de l'artiste crédité portant CE
        nom — et None si aucun crédité ne matche (le morceau ne vote pas).
        Indispensable pour les projets communs : sans ça, le premier artiste
        crédité (ex. Limsa d'Aulnay sur les albums Isha × Limsa) rafle le vote.
        """
        artists = self.get_track_artists(track_spotify_id)
        if not artists:
            logger.warning(f"❌ Aucun artiste crédité lisible pour le track {track_spotify_id}")
            return None

        if expected_name:
            exp = self._normalize_apostrophes(expected_name).lower().strip()
            for a in artists:
                name = self._normalize_apostrophes(a["name"]).lower().strip()
                if name and (name == exp or name in exp or exp in name):
                    logger.info(f"✅ ID artiste (crédité '{a['name']}'): {a['id']}")
                    return a["id"]
            logger.info(
                f"⏭️ '{expected_name}' absent des crédités du track "
                f"{track_spotify_id} ({[a['name'] for a in artists]}) — pas de vote"
            )
            return None

        logger.info(f"✅ ID artiste (1er crédité '{artists[0]['name']}'): {artists[0]['id']}")
        return artists[0]["id"]

    def get_spotify_page_title(self, spotify_id: str) -> str | None:
        try:
            self._ensure_driver()
        except Exception:
            return None
        try:
            spotify_url = f"https://open.spotify.com/track/{spotify_id}"
            self.page.goto(spotify_url, wait_until="domcontentloaded", timeout=30_000)
            title = self.page.title()
            if title:
                return title.replace(" | Spotify", "").strip()
        except Exception as e:
            logger.error(f"❌ Erreur récupération titre: {e}")
        return None

    def get_artist_spotify_id(self, artist_name: str) -> str | None:
        """Récupère l'ID Spotify (22 chars) d'un artiste depuis la page de recherche Spotify."""
        logger.info(f"🔍 Recherche ID Spotify artiste pour: '{artist_name}'")

        cache_key = f"artist::{artist_name.lower().strip()}"
        if cache_key in self.cache:
            cached = self.cache[cache_key]
            if cached and cached != "not_found":
                logger.info(f"✅ ID artiste trouvé en cache: {cached}")
                return cached
            # 'not_found' non définitif → on retente

        try:
            self._ensure_driver()
        except Exception:
            logger.error("❌ Browser non disponible")
            return None

        try:
            search_url = (
                f"https://open.spotify.com/search/{urllib.parse.quote(artist_name)}/artists"
            )
            self.page.goto(search_url, wait_until="domcontentloaded", timeout=30_000)
            self._handle_cookies()

            try:
                self.page.wait_for_selector("a[href*='/artist/']", timeout=self._timeout)
            except PlaywrightTimeoutError:
                logger.warning(f"⏰ Timeout recherche artiste: {artist_name}")
                self.cache[cache_key] = "not_found"
                self._save_cache()
                return None

            links = self.page.query_selector_all("a[href*='/artist/']")
            artist_lower = artist_name.lower()

            for link in links[:20]:
                try:
                    href = link.get_attribute("href") or ""
                    aid = self.extract_artist_id_from_url(href)
                    if not aid:
                        continue
                    link_text = (link.inner_text() or "").lower()
                    parent = link.query_selector("..")
                    parent_text = (parent.inner_text() if parent else "").lower()
                    combined = f"{link_text} {parent_text}"
                    if artist_lower in combined:
                        logger.info(f"✅ ID artiste Spotify trouvé: {aid}")
                        self.cache[cache_key] = aid
                        self._save_cache()
                        return aid
                except Exception:
                    continue

            # Fallback : premier lien artiste sans vérification de nom
            for link in links[:5]:
                try:
                    href = link.get_attribute("href") or ""
                    aid = self.extract_artist_id_from_url(href)
                    if aid:
                        logger.warning(f"⚠️ ID artiste Spotify (fallback, sans vérif nom): {aid}")
                        self.cache[cache_key] = aid
                        self._save_cache()
                        return aid
                except Exception:
                    continue

        except Exception as e:
            logger.error(f"❌ Erreur recherche ID artiste '{artist_name}': {e}")

        self.cache[cache_key] = "not_found"
        self._save_cache()
        return None

    def get_spotify_id_for_track(self, track) -> str | None:
        if hasattr(track, "is_featuring") and track.is_featuring:
            artist_name = (
                track.primary_artist_name
                if hasattr(track, "primary_artist_name") and track.primary_artist_name
                else (track.artist.name if hasattr(track.artist, "name") else str(track.artist))
            )
        else:
            artist_name = track.artist.name if hasattr(track.artist, "name") else str(track.artist)
        return self.get_spotify_id(artist_name, track.title)

    def close(self):
        self._cleanup_resources()
        logger.info("✅ SpotifyIDScraper fermé")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
