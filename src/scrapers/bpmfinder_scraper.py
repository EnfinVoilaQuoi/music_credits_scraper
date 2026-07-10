"""
Scraper BPM Finder — audioaidynamics.com/music-analyzer
BPM / Key / Camelot (+ moods) depuis un lien YouTube. Compte requis pour lancer
une analyse (email/mot de passe : BPMFINDER_EMAIL / BPMFINDER_PASSWORD).

Session Playwright PERSISTÉE (storage_state → data/.bpmfinder_session.json,
cookies + localStorage/JWT) : le login ne rejoue que quand la session expire.
Bootstrap manuel possible : `python scripts/bpmfinder_login.py` (fenêtre visible).

Backend observé (session Chrome 2026-07-03) : POST api.audioaidynamics.com/api/yt
(~8 s), résultats en cartes ("Key: C minor / BPM: 87 / Camelot: 5A"). On pilote
l'UI (pas d'API documentée) et on parse les cartes par diff avant/après.
"""
import json
import re
import time
import logging
import threading
from pathlib import Path
from typing import Dict, Optional, Set, Tuple

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from src.config import (
    BPMFINDER_EMAIL, BPMFINDER_PASSWORD, BPMFINDER_SESSION_FILE, DATA_DIR,
)
from src.scrapers.playwright_manager import get_playwright
from src.utils.music_theory import note_to_pitch_class, parse_mode

logger = logging.getLogger('BPMFinderScraper')

ANALYZER_URL = "https://audioaidynamics.com/music-analyzer"
_CACHE_FILE = DATA_DIR / "bpmfinder_cache.json"

# "Key: C minor … BPM: 87 … Camelot: 5A" (l'ordre des cartes suit le DOM)
_CARD_RE = re.compile(
    r'Key:\s*([A-G][#b♯♭]?)\s*(minor|major)'
    r'.{0,80}?BPM:\s*(\d{2,3})'
    r'(?:.{0,80}?Camelot:\s*(\d{1,2}[AB]))?',
    re.S | re.I,
)


class BPMFinderScraper:
    """Analyse BPM/Key via audioaidynamics (Playwright, session persistée)."""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self._thread_id = None  # thread propriétaire du driver (Playwright sync = thread-affine)
        self.cache = self._load_cache()

    # ── Disponibilité ──────────────────────────────────────────────────────────

    @staticmethod
    def credentials_or_session_available() -> bool:
        return bool((BPMFINDER_EMAIL and BPMFINDER_PASSWORD)
                    or Path(BPMFINDER_SESSION_FILE).exists())

    # ── Cache (par videoId : une analyse suffit, le résultat ne change pas) ───

    def _load_cache(self) -> dict:
        try:
            return json.loads(Path(_CACHE_FILE).read_text(encoding='utf-8'))
        except Exception:
            return {}

    def _save_cache(self):
        try:
            Path(_CACHE_FILE).write_text(
                json.dumps(self.cache, ensure_ascii=False, indent=1), encoding='utf-8')
        except Exception as e:
            logger.debug(f"Cache BPM Finder non sauvegardé: {e}")

    # ── Driver / session ───────────────────────────────────────────────────────

    # Consentement cookies ESSENTIELS SEULEMENT (décline analytics/pubs) —
    # pré-installé avant le chargement pour que la bannière bloquante
    # (« To continue using this website, please accept cookies », overlay
    # plein écran qui intercepte le clic Upload) n'apparaisse jamais.
    _CONSENT_INIT = "try{localStorage.setItem('cookie-comply', JSON.stringify(['session']));}catch(e){}"

    def _drop_driver_refs(self):
        """Abandonne les objets Playwright sans les fermer (leur thread est mort :
        toute opération dessus relèverait l'erreur thread-affine)."""
        for attr in ('page', 'context', 'browser'):
            try:
                obj = getattr(self, attr, None)
                if obj:
                    obj.close()
            except Exception:
                pass
            setattr(self, attr, None)

    def _ensure_driver(self):
        cur = threading.get_ident()
        # Playwright sync est THREAD-AFFINE : un driver créé dans un thread mort
        # (enrichissement traité thread par thread) casse « cannot switch to a
        # different thread ». Si le thread a changé, on reconstruit.
        if self.page is not None and self._thread_id == cur:
            return
        if self.page is not None:
            logger.info("BPM Finder: thread changé → recréation du driver")
            self._drop_driver_refs()

        self._playwright = get_playwright()
        self.browser = self._playwright.chromium.launch(headless=self.headless)
        storage = str(BPMFINDER_SESSION_FILE) if Path(BPMFINDER_SESSION_FILE).exists() else None
        self.context = self.browser.new_context(storage_state=storage)
        self.context.add_init_script(self._CONSENT_INIT)
        self.context.on("response", self._log_api_error)
        self.page = self.context.new_page()
        self._thread_id = cur
        logger.info(f"🌐 BPM Finder: Playwright initialisé "
                    f"(session {'reprise' if storage else 'neuve'})")

    @staticmethod
    def _log_api_error(resp):
        """Listener réseau : rend visibles les erreurs backend (sans lui, un refus
        de l'API se manifeste par un « pas de résultat en 90s » muet). Propriétés
        sync uniquement — PAS de resp.text() ici (appel bloquant interdit dans un
        handler Playwright sync)."""
        try:
            if 'audioaidynamics.com/api' in resp.url and resp.status >= 400:
                logger.warning(f"BPM Finder API: HTTP {resp.status} sur {resp.url}")
        except Exception:
            pass

    def _dump_debug_state(self, label: str):
        """Capture d'état (screenshot + texte de la page) dans data/diagnostics/,
        UNE fois par run pour ne pas spammer. Évite de rediagnostiquer à l'aveugle
        (cf. panne 2026-07-10 : overlay pubs invisible dans les logs)."""
        if getattr(self, '_debug_dumped', False):
            return
        self._debug_dumped = True
        try:
            diag = DATA_DIR / "diagnostics"
            diag.mkdir(parents=True, exist_ok=True)
            safe = re.sub(r'[^\w.-]', '_', label)[:60]
            stamp = time.strftime("%Y%m%d_%H%M%S")
            self.page.screenshot(path=str(diag / f"bpmfinder_{stamp}_{safe}.png"))
            (diag / f"bpmfinder_{stamp}_{safe}.txt").write_text(
                self.page.inner_text('body') or '', encoding='utf-8')
            logger.warning(f"BPM Finder: état de la page capturé dans {diag}")
        except Exception:
            pass

    def _dismiss_cookie_overlay(self):
        """Fallback runtime : neutralise les overlays qui interceptent les clics.

        1) Dialogue Google Funding Choices (« Unlock more content »,
           `.fc-message-root`/`.fc-dialog-overlay`) + pubs AdSense ancrées
           (`ins.adsbygoogle`) — apparus sur le site ~2026-07-10 : le clic
           Upload était intercepté → aucun POST /api/yt → « pas de résultat
           en 90s » en série. On clique un bouton de refus/fermeture si
           présent, puis on retire le dialogue et les pubs du DOM.
        2) Ancien voile cookies maison (« To continue using this website… »).
        """
        try:
            self.page.evaluate("""
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
        except Exception:
            pass

    def _save_session(self):
        try:
            Path(BPMFINDER_SESSION_FILE).parent.mkdir(parents=True, exist_ok=True)
            self.context.storage_state(path=str(BPMFINDER_SESSION_FILE))
            logger.info("💾 Session BPM Finder sauvegardée")
        except Exception as e:
            logger.warning(f"Session BPM Finder non sauvegardée: {e}")

    def _goto_analyzer(self, force: bool = False):
        if force or not (self.page.url or '').startswith(ANALYZER_URL):
            self.page.goto(ANALYZER_URL, wait_until="domcontentloaded", timeout=45_000)
            try:
                self.page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeoutError:
                pass
            self.page.wait_for_timeout(1_500)
            self._dismiss_cookie_overlay()

    def _find_youtube_input(self, timeout_ms: int = 15_000):
        """Champ URL YouTube (placeholder « Or Enter YouTube URL »). Robuste :
        get_by_placeholder puis fallbacks CSS. None si introuvable."""
        try:
            loc = self.page.get_by_placeholder("YouTube", exact=False)
            loc.wait_for(state="visible", timeout=timeout_ms)
            return loc.first
        except Exception:
            pass
        for sel in ("input[placeholder*='YouTube' i]",
                    "input[placeholder*='youtube' i]",
                    "input[type='text']"):
            try:
                el = self.page.wait_for_selector(sel, state="visible", timeout=3_000)
                if el:
                    return el
            except Exception:
                continue
        return None

    # ── Login ──────────────────────────────────────────────────────────────────

    def _is_logged_in(self) -> bool:
        """Connecté ⇔ cookie `access_token` présent (fiable, indépendant du
        rendu DOM — vérifié en live : connecté = cookies access_token/refresh_token,
        aucun span « LOGIN »). Évite le re-login inutile à chaque run."""
        try:
            return any(c.get('name') == 'access_token' for c in self.context.cookies())
        except Exception:
            return False

    def _looks_logged_out(self) -> bool:
        """Fallback DOM (après login) : présence du span « LOGIN » de la sidebar."""
        if self._is_logged_in():
            return False
        try:
            return self.page.locator("span:text-is('LOGIN')").count() > 0
        except Exception:
            return False

    def _try_login(self) -> bool:
        """Login email/mot de passe via le MODAL (pas de page /login) :
        clic sur « LOGIN » dans la sidebar → input[type=email] +
        input[type=password] + bouton « Login ». Structure vérifiée en live.
        Si l'UI change : bootstrap manuel via scripts/bpmfinder_login.py."""
        if not (BPMFINDER_EMAIL and BPMFINDER_PASSWORD):
            logger.error("BPM Finder: identifiants absents (BPMFINDER_EMAIL/PASSWORD) "
                         "et pas de session — lancer scripts/bpmfinder_login.py")
            return False
        try:
            # Ouvrir le modal si le formulaire n'est pas déjà affiché
            pwd_sel = "input[type='password']"
            if not self.page.query_selector(pwd_sel):
                self.page.locator("span:text-is('LOGIN')").first.click()
                self.page.wait_for_selector(pwd_sel, timeout=10_000)

            self.page.fill("input[type='email']", BPMFINDER_EMAIL)
            self.page.fill(pwd_sel, BPMFINDER_PASSWORD)
            self.page.click("button:has-text('Login')")
            self.page.wait_for_timeout(4_000)

            # Rechargement PROPRE : évite l'état DOM laissé par le modal
            # (sinon le champ YouTube n'est pas retrouvé juste après le login).
            self._goto_analyzer(force=True)
            if self._looks_logged_out():
                logger.error("BPM Finder: login refusé (vérifier identifiants) — "
                             "ou UI de login inattendue : scripts/bpmfinder_login.py")
                return False
            self._save_session()
            logger.info("✅ BPM Finder: connecté")
            return True
        except Exception as e:
            logger.error(f"BPM Finder: login échoué: {e} — "
                         "bootstrap manuel : scripts/bpmfinder_login.py")
            return False

    # ── Analyse ────────────────────────────────────────────────────────────────

    @staticmethod
    def _video_id(url: str) -> Optional[str]:
        m = re.search(r'(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})', url or '')
        return m.group(1) if m else None

    def _cards(self) -> Set[Tuple[str, str, str, str]]:
        """Cartes de résultats présentes : {(note, mode, bpm, camelot)}."""
        try:
            text = self.page.inner_text('body') or ''
        except Exception:
            return set()
        return {m.groups() for m in _CARD_RE.finditer(text)}

    def analyze(self, youtube_url: str, timeout_s: int = 90) -> Optional[Dict]:
        """Analyse un lien YouTube.

        Returns:
            {'bpm': int, 'key': 0-11, 'mode': 0|1, 'key_name': 'C minor',
             'camelot': '5A'|None} ou None.
        """
        vid = self._video_id(youtube_url)
        if not vid:
            logger.warning(f"BPM Finder: lien YouTube invalide: {youtube_url!r}")
            return None
        if vid in self.cache:
            logger.debug(f"BPM Finder: cache hit {vid}")
            return self.cache[vid]

        self._ensure_driver()
        self._goto_analyzer()

        # Login SEULEMENT si la session reprise n'est pas déjà valide
        # (cookie access_token) — évite le re-login systématique.
        if not self._is_logged_in():
            if not self._try_login():
                return None
        else:
            logger.debug("BPM Finder: session valide (access_token) — pas de login")

        before = self._cards()
        url_input = self._find_youtube_input()
        if not url_input:
            # Dernier essai : reload propre puis re-cherche
            self._goto_analyzer(force=True)
            url_input = self._find_youtube_input()
        if not url_input:
            try:
                dbg = str(DATA_DIR / "bpmfinder_debug.png")
                self.page.screenshot(path=dbg)
                logger.error(f"BPM Finder: champ YouTube introuvable — capture: {dbg}")
            except Exception:
                logger.error("BPM Finder: champ YouTube introuvable (UI changée ?)")
            return None
        try:
            url_input.fill(youtube_url)
            self._dismiss_cookie_overlay()  # au cas où l'overlay surgit après la saisie
            try:
                self.page.click("button:has-text('Upload')", timeout=15_000)
            except Exception:
                # Les overlays pubs (fc-dialog/AdSense) peuvent réapparaître entre
                # le dismiss et le clic : re-nettoyer et retenter UNE fois.
                self._dismiss_cookie_overlay()
                self.page.click("button:has-text('Upload')", timeout=10_000)
        except Exception as e:
            logger.error(f"BPM Finder: saisie/Upload échoué: {e}")
            self._dump_debug_state(f"upload_{vid}")
            return None

        result = self._await_and_parse(before, timeout_s, label=vid)
        if result:
            self.cache[vid] = result
            self._save_cache()
            self._save_session()  # prolonge la session (cookies rafraîchis)
        return result

    def analyze_file(self, filepath: str, timeout_s: int = 120) -> Optional[Dict]:
        """Analyse un FICHIER audio/vidéo LOCAL (wav/mp3/ogg/flac/mp4…).

        Pour les morceaux absents de YouTube. Même retour que analyze().
        Pas de cache (pas d'identifiant stable comme le videoId).
        """
        p = Path(filepath)
        if not p.exists():
            logger.error(f"BPM Finder: fichier introuvable: {filepath}")
            return None

        self._ensure_driver()
        self._goto_analyzer()
        if not self._is_logged_in() and not self._try_login():
            return None

        before = self._cards()
        try:
            self._dismiss_cookie_overlay()
            self.page.set_input_files("input#fileInput", str(p))
        except Exception as e:
            logger.error(f"BPM Finder: envoi du fichier échoué: {e}")
            return None

        result = self._await_and_parse(before, timeout_s, label=p.name)
        if result:
            self._save_session()
        return result

    def _await_and_parse(self, before, timeout_s: int, label: str) -> Optional[Dict]:
        """Attend la NOUVELLE carte (diff avant/après) et la parse en dict."""
        deadline = time.time() + timeout_s
        new_card = None
        while time.time() < deadline:
            self.page.wait_for_timeout(2_000)
            fresh = self._cards() - before
            if fresh:
                new_card = sorted(fresh)[0]
                break
        if not new_card:
            logger.warning(f"BPM Finder: pas de résultat en {timeout_s}s pour {label}")
            self._dump_debug_state(f"timeout_{label}")
            return None

        note, mode_name, bpm, camelot = new_card
        result = {
            'bpm': int(bpm),
            'key': note_to_pitch_class(note),
            'mode': parse_mode(mode_name),
            'key_name': f"{note} {mode_name.lower()}",
            'camelot': camelot or None,
        }
        if result['key'] is None or result['mode'] is None:
            logger.warning(f"BPM Finder: tonalité non parsée: {note!r} {mode_name!r}")
        logger.info(f"✅ BPM Finder {label}: {result['bpm']} BPM, {result['key_name']}"
                    + (f", Camelot {result['camelot']}" if result['camelot'] else ""))
        return result

    # ── Cycle de vie ───────────────────────────────────────────────────────────

    def close(self):
        for attr in ('page', 'context', 'browser'):
            obj = getattr(self, attr, None)
            if obj:
                try:
                    obj.close()
                except Exception:
                    pass
                setattr(self, attr, None)
        # NB: ne pas stopper self._playwright — instance partagée (playwright_manager)
        self._playwright = None
        logger.info("✅ BPMFinderScraper fermé")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
