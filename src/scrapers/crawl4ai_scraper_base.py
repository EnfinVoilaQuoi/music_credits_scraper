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
import re
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from crawl4ai import BrowserConfig

from src.concurrency import async_loop
from src.config import DATA_PATH, settings
from src.observability import source_usage
from src.observability.issues import IssueKind, classify
from src.utils.logger import get_logger

# patchright (undetected Chromium) est importé en lazy dans _patchright_fetch : on
# expose sa classe d'erreur — DISTINCTE de celle de playwright — pour cibler les
# `except` ; sentinelle jamais levée si la lib est absente.
try:
    from patchright.async_api import Error as PatchrightError
except ImportError:  # pragma: no cover

    class PatchrightError(Exception):
        """Sentinelle quand patchright est absent (jamais levée)."""


logger = get_logger(__name__)


@dataclass
class _Passes:
    """Les passes d'un crawl (CDP, headless, fenêtre visible), relevées pendant le
    rendu et REJOUÉES dans l'observation de l'appelant.

    Une tentative se rattache à l'observation ouverte sur le carrier COURANT (la
    Task asyncio, sinon le thread). Le rendu tourne sur la boucle applicative ;
    le pont sync `_crawl_page` et son appelant, sur un thread. Notée côté
    boucle, une passe partirait en verdict isolé et l'observation de
    l'appelant resterait `indeterminate` — même mécanique que
    `riaa_scraper_v2._Tentatives`.
    """

    relevees: list[tuple[IssueKind, str, bool]] = field(default_factory=list)

    def note(self, kind: IssueKind, detail: str = "", *, expected: bool = False) -> None:
        self.relevees.append((kind, detail, expected))

    def rejouer(self, source_key: str) -> None:
        for kind, detail, expected in self.relevees:
            source_usage.record_attempt(source_key, kind, detail=detail, expected=expected)
        self.relevees.clear()


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


def _cdp_url() -> str | None:
    """Optionnel : se connecter à un navigateur Chromium DÉJÀ OUVERT (ex. Brave sur
    IP résidentielle) via CDP, au lieu de lancer Chrome for Testing. Lance ton Brave
    avec `--remote-debugging-port=9222` puis `set GENIUS_CDP_URL=http://localhost:9222`.

    Lue À L'APPEL, pas à l'import : cette base est importée au chargement de
    `update_brma`, si bien qu'une constante de module figeait la route AVANT que
    le script ait pu préparer son Chrome (`ultratop_fetch.preparer_route_cdp`) —
    il fallait poser la variable dans le shell, d'où l'ex-`run_brma.ps1`.
    """
    return os.getenv("GENIUS_CDP_URL")


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


def type_de_page(url: str) -> str:
    """PUR. Le type d'une page de session, d'après le premier segment de son
    chemin hors locale (`/intl-fr/track/…` → `track`)."""
    from urllib.parse import urlsplit

    segments = [s for s in urlsplit(url).path.split("/") if s and not s.startswith("intl-")]
    return segments[0] if segments else "?"


@dataclass
class ChronoPages:
    """Temps passé par TYPE de page et par PHASE d'une navigation de session
    (2026-10-31) : la latence moyenne des pages Spotify est passée de ~7 s
    (26/10) à ~25 s (31/10) sans que rien ne dise où. Phases : `goto`
    (navigation jusqu'au DOM), `attente` (sélecteur ; `delais` compte ceux qui
    ont expiré), `script`, `pause` (délai fixe), `html`, `brut`."""

    phases: dict = field(default_factory=dict)  # {type: {phase: [n, total, max]}}
    pages: dict = field(default_factory=dict)  # {type: n}
    delais: dict = field(default_factory=dict)  # {type: n} sélecteurs expirés

    def noter(self, type_: str, phase: str, secondes: float) -> None:
        n_total_max = self.phases.setdefault(type_, {}).setdefault(phase, [0, 0.0, 0.0])
        n_total_max[0] += 1
        n_total_max[1] += secondes
        n_total_max[2] = max(n_total_max[2], secondes)

    def resume(self) -> list[str]:
        """Une ligne par type de page : moyenne par phase, et le max."""
        lignes = []
        for type_, n in sorted(self.pages.items()):
            phases = self.phases.get(type_, {})
            total = sum(v[1] for v in phases.values())
            detail = ", ".join(
                f"{p} {v[1] / v[0]:.1f} s (max {v[2]:.0f})" for p, v in phases.items() if v[0]
            )
            expires = self.delais.get(type_, 0)
            lignes.append(
                f"{type_} : {n} page(s), {total / n:.1f} s/page — {detail}"
                + (f" ; {expires} attente(s) expirée(s)" if expires else "")
            )
        return lignes


class CrawlAIScraperBase:
    """
    Base class pour tous les scrapers Crawl4AI du projet.

    Usage dans une sous-classe :
        html = self._crawl_page(url, js_before_wait=JS, wait_for="js:...")
    """

    #: Clé de `source_health.SOURCES` sous laquelle l'usage est compté. La base
    #: sert Genius par défaut ; `ultratop_fetch` l'instancie avec sa propre clé.
    HEALTH_KEY = "genius_scrape"

    def __init__(
        self,
        headless: bool = True,
        health_key: str | None = None,
        profile_dir: str | None = None,
        locale: str | None = None,
    ):
        self.headless = headless
        self.health_key = health_key or self.HEALTH_KEY
        # Profil PROPRE à ce scraper. Par défaut, le profil anti-Cloudflare partagé
        # (Genius/RIAA) — mais une source SANS Cloudflare n'a rien à y faire : y
        # mêler sa session encombrerait le profil qui porte le cf_clearance.
        self.profile_dir = profile_dir
        # Locale du contexte. Spotify choisit sa langue sur `navigator.language` et
        # REDIRIGE l'URL en conséquence (`/intl-fr/…`) : sans épinglage, le libellé
        # sur lequel s'accrochent les parseurs change avec la machine.
        self.locale = locale
        self.chrono = ChronoPages()
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
    ) -> str | None:
        """
        Crawl synchrone d'une page. Retourne le HTML brut, ou None si le crawl échoue.

        Pont F4 : exécute le crawl sur LA boucle applicative (bloquant).
        À appeler depuis un thread sync uniquement — une coroutine await
        directement `acrawl_page`.

        L'observation est ouverte ICI, sur le thread de l'appelant, et les
        passes relevées sur la boucle y sont REJOUÉES (`_Passes`) : un capteur
        posé de l'autre côté du pont ne trouve pas son observation (règle apprise
        sur RIAA le 2026-09-08). Quand ce pont ouvrait sa propre observation
        côté boucle, un appelant qui en tenait une sur son thread —
        `update_brma._lire_page`, pour y poser le verdict de PARSE — la voyait
        rester sans tentative : le run BRMA du 2026-09-17 comptait 78 pages `ok`
        ET 78 `indeterminate`. `observe()` étant réentrant par source, l'appelant
        qui en tient une la réutilise ; sinon on en ouvre une, comme avant.
        """
        passes = _Passes()
        with source_usage.observe(self.health_key, label=url) as obs:
            try:
                html = async_loop.run_sync(
                    self._acrawl_page_body(
                        url,
                        js_before_wait,
                        wait_for,
                        wait_timeout,
                        page_timeout,
                        delay_before_return,
                        passes,
                    )
                )
            finally:
                passes.rejouer(self.health_key)
            self._conclure(obs, html)
            return html

    async def acrawl_page(
        self,
        url: str,
        *,
        js_before_wait: str | None = None,
        wait_for: str | None = None,
        wait_timeout: int = 15_000,
        page_timeout: int = 30_000,
        delay_before_return: float = 1.5,
    ) -> str | None:
        """
        Coroutine native (F4) — même séquence que le pont sync historique :
        CDP → essai headless (profil persistant) → fenêtre VISIBLE (challenge).
        Retourne le HTML brut, ou None si le crawl échoue.

        Paramètres :
            js_before_wait   : JS exécuté avant la condition d'attente (ex: clic expand)
            wait_for         : condition CSS ou JS à attendre (ex: "css:.credits", "js:()=>...")
            wait_timeout     : ms avant abandon de la condition wait_for
            page_timeout     : ms avant abandon du chargement de page
            delay_before_return : secondes d'attente supplémentaires après le wait_for
        """
        passes = _Passes()
        with source_usage.observe(self.health_key, label=url) as obs:
            try:
                html = await self._acrawl_page_body(
                    url,
                    js_before_wait,
                    wait_for,
                    wait_timeout,
                    page_timeout,
                    delay_before_return,
                    passes,
                )
            finally:
                passes.rejouer(self.health_key)
            self._conclure(obs, html)
            return html

    @staticmethod
    def _conclure(obs, html: str | None) -> None:
        if html is None:
            # Les passes ont échoué sans exception : la page n'est pas venue. Si
            # une tentative a déjà dit pourquoi (bloquée, timeout), c'est elle qui
            # parle — sinon on nomme l'inaccessibilité.
            obs.fail(IssueKind.UNREACHABLE, "aucune passe n'a rendu de HTML")

    async def _acrawl_page_body(
        self,
        url: str,
        js_before_wait: str | None,
        wait_for: str | None,
        wait_timeout: int,
        page_timeout: int,
        delay_before_return: float,
        passes: "_Passes",
    ) -> str | None:
        """Point de passage des pages (chantier E) : en REJEU, la page gardée
        (jamais le navigateur) ; sinon le crawl, et le HTML d'une source
        activée est gardé brut."""
        from src.persistence import brut

        if brut.rejeu_actif():
            lu = brut.servir(self.health_key, url)
            return lu[1].decode("utf-8") if lu else None
        html = await self._acrawl_page_reseau(
            url,
            js_before_wait,
            wait_for,
            wait_timeout,
            page_timeout,
            delay_before_return,
            passes,
        )
        if html:
            brut.consigner(self.health_key, url, 200, html.encode("utf-8"))
        return html

    async def _acrawl_page_reseau(
        self,
        url: str,
        js_before_wait: str | None,
        wait_for: str | None,
        wait_timeout: int,
        page_timeout: int,
        delay_before_return: float,
        passes: "_Passes",
    ) -> str | None:
        """L'échelle CDP → headless → fenêtre visible. Chaque passe est une
        TENTATIVE relevée dans `passes` — rejouée par l'appelant dans SON
        observation —, l'appel reste unique : bloqué en headless puis servi en
        fenêtre visible vaut UN succès, pas deux échecs."""
        # 0) Mode CDP : on lit via un navigateur déjà ouvert (Brave, IP résidentielle)
        if _cdp_url():
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
                self._note_passe(passes, blocked, html, "CDP")
                if blocked:
                    logger.warning(
                        f"{self.__class__.__name__}: via CDP, page bloquée — ouvre d'abord "
                        f"genius.com dans ton Brave (résidentiel) pour lever le challenge."
                    )
                return html
            except PatchrightError as e:
                passes.note(classify(exc=e), f"CDP : {e}")
                logger.error(f"{self.__class__.__name__}: CDP {url}: {e}")
                return None

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
                self._note_passe(passes, blocked, html, "headless")
            except PatchrightError as e:
                passes.note(classify(exc=e), f"headless : {e}")
                logger.error(f"{self.__class__.__name__}: patchright headless {url}: {e}")
                # Une coupure réseau n'est pas un défi : la fenêtre visible n'y ferait
                # rien (197 fenêtres ouvertes pour rien le 2026-11-08, chez Kaaris).
                html, blocked = None, not erreur_reseau(e)
            if not blocked:
                return html
            marqueur = _marqueur_de_defi(html) or "aucun, erreur du navigateur"
            logger.info(
                f"{self.__class__.__name__}: Cloudflare → fenêtre VISIBLE pour {url} "
                f"(marqueur : {marqueur}). "
                f"⚠️ Résous le challenge UNE fois ; le cookie est mémorisé (profil persistant), "
                f"ensuite tout repasse en headless."
            )

        # 2) Mode VISIBLE + undetected + fenêtre longue : tu résous, on capture la
        #    vraie page (on attend le conteneur paroles, pas d'abandon prématuré).
        try:
            _, html, blocked = await self._patchright_fetch(
                url,
                js_before_wait,
                wait_for,
                max(wait_timeout, 120_000),
                max(page_timeout, 120_000),
                max(delay_before_return, 2.0),
                headless=False,
            )
            self._note_passe(passes, blocked, html, "fenêtre visible")
            if blocked:
                # Le défi n'est pas levé (limite d'un lot) : ce HTML n'est pas la page.
                _garder_le_defi(url, html)
                return None
            return html
        except PatchrightError as e:
            passes.note(classify(exc=e), f"fenêtre visible : {e}")
            logger.error(f"{self.__class__.__name__}: patchright visible {url}: {e}")
            return None

    @staticmethod
    def _note_passe(passes: "_Passes", blocked: bool, html: str | None, passe: str) -> None:
        """Une passe de l'échelle = une tentative.

        Un blocage anti-bot est ATTENDU sur ces sources (`tolerate_403`) : il est
        marqué comme tel, ce qui l'exclut du taux d'échec ET du calcul du pire,
        tout en restant compté — si les contournements grimpent pendant que les
        succès s'effondrent, c'est le signe que Cloudflare a durci.
        """
        if blocked:
            passes.note(IssueKind.BLOCKED, f"{passe} : anti-bot", expected=True)
        elif html:
            passes.note(IssueKind.OK, passe)
        else:
            passes.note(IssueKind.UNREACHABLE, f"{passe} : page vide")

    def _launch_kwargs(self, headless: bool, *, hors_ecran: bool = False) -> dict:
        """Arguments de lancement du contexte persistant, partagés par
        `_patchright_fetch` (une page) et `session` (N pages). `hors_ecran` : la
        fenêtre visible d'un défi, qui ne vient devant que s'il faut un humain."""
        kwargs = dict(
            headless=headless,
            user_agent=_USER_AGENT,
            viewport={"width": 1280, "height": 900},
        )
        # channel="chrome" → utilise le vrai Chrome installé (passe mieux certains
        # Cloudflare que le Chromium bundlé)
        if hors_ecran and not headless:
            # Ouverte HORS de l'écran : un défi se lève seul en quelques secondes
            # (197 / 197 en ≤ 5 s dans les journaux du 2026-11), la fenêtre ne vient
            # devant que s'il faut un humain (`_au_premier_plan`). Rendue même cachée.
            kwargs["args"] = [_POSITION_HORS_ECRAN, "--disable-backgrounding-occluded-windows"]
        if _BROWSER_CHANNEL:
            kwargs["channel"] = _BROWSER_CHANNEL
        if self.locale:
            kwargs["locale"] = self.locale
        return kwargs

    @asynccontextmanager
    async def session(self, headless: bool | None = None):
        """Ouvre UNE session navigateur pour enchaîner N pages.

        `acrawl_page` ouvre — et referme — un contexte à CHAQUE appel : sur trois
        cents morceaux, cela fait trois cents lancements de navigateur. Cette
        session garde une page ouverte, donc le cache JS reste chaud et la session
        du site continue d'un appel au suivant.

        Elle NE reprend PAS l'échelle CDP → headless → fenêtre visible : cette
        escalade n'a de sens que face à un challenge Cloudflare, et une source qui
        n'en a pas n'a aucune raison d'en payer la complexité. `acrawl_page` reste
        le chemin Cloudflare, inchangé.

        Usage :
            async with scraper.session() as sess:
                html = await sess.fetch(url, wait_for="css:…")
        """
        from src.persistence import brut

        if brut.rejeu_actif():
            # Rejeu (chantier E) : les pages viennent du brut, aucun navigateur.
            yield _BrowserSession(self, None)
            return
        from patchright.async_api import async_playwright

        profile_dir = self.profile_dir or _profile_dir()
        os.makedirs(profile_dir, exist_ok=True)
        launch = self._launch_kwargs(self.headless if headless is None else headless)
        async with async_playwright() as pw:
            ctx = await pw.chromium.launch_persistent_context(profile_dir, **launch)
            try:
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                yield _BrowserSession(self, page)
            finally:
                await ctx.close()

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
        except ImportError as e:
            logger.error(
                f"patchright indisponible: {e} — `pip install -U crawl4ai && crawl4ai-setup`"
            )
            return None, None, True

        profile_dir = self.profile_dir or _profile_dir()
        os.makedirs(profile_dir, exist_ok=True)
        # Sélecteur « vraie page chargée » : conteneur paroles (présent sur toute page Genius)
        sel = (
            wait_for[4:]
            if (wait_for or "").startswith("css:")
            else "[data-lyrics-container='true']"
        )

        try:
            async with async_playwright() as pw:
                cdp_url = _cdp_url()
                cdp_mode = bool(cdp_url)
                if cdp_mode:
                    # Attache à un Chromium déjà ouvert (Brave, IP résidentielle, vraie session)
                    browser = await pw.chromium.connect_over_cdp(cdp_url)
                    ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
                    page = await ctx.new_page()
                    logger.info(f"{self.__class__.__name__}: connecté via CDP à {cdp_url}")
                else:
                    ctx = await pw.chromium.launch_persistent_context(
                        profile_dir, **self._launch_kwargs(headless, hors_ecran=True)
                    )
                    page = ctx.pages[0] if ctx.pages else await ctx.new_page()

                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=page_timeout)
                    # Attend la vraie page (conteneur paroles). En mode VISIBLE : tant que
                    # le défi est là, ou que tu fermes l'onglet toi-même.
                    content_found = False
                    if not headless and not cdp_mode:
                        content_found = await self._attendre_fin_du_defi(page, sel, url)
                    else:
                        try:
                            await page.wait_for_selector(sel, timeout=wait_timeout)
                            content_found = True
                        except PatchrightError:
                            pass  # pas trouvé : on récupère quand même le HTML pour diagnostic
                    # JS d'expansion (crédits) sur la vraie page
                    if js_before_wait:
                        try:
                            await page.evaluate(js_before_wait)
                            await page.wait_for_timeout(800)
                        except PatchrightError:
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
        except PatchrightError as e:
            logger.error(f"{self.__class__.__name__}: patchright fetch {url}: {e}")
            # Une coupure réseau n'est pas un blocage : sans elle, la passe headless
            # montait en fenêtre visible à chaque page (2026-11-08, ERR_NAME_NOT_RESOLVED).
            return None, None, not erreur_reseau(e)

    async def _attendre_fin_du_defi(self, page, sel: str, url: str) -> bool:
        """Fenêtre visible : `True` si le conteneur attendu est là.

        L'attente suit le DÉFI, pas le conteneur : attendre celui-ci sans limite
        gardait ouverte pour toujours une page sans paroles, défi levé ou non
        (« Souvenir Du Bled » d'Aketo, pas encore transcrite ; l'instrumental de Talib
        Kweli, 10 h perdues le 2026-10-21). Sans personne devant l'écran,
        `fenetre_visible_max_s` borne l'attente (lots) ; 0 = sans limite.
        """
        nom = self.__class__.__name__
        limite_s = settings.fenetre_visible_max_s
        debut = time.monotonic()
        devant = False

        def duree() -> int:
            return round(time.monotonic() - debut)

        while not limite_s or time.monotonic() - debut < limite_s:
            if not devant and time.monotonic() - debut >= _PREMIER_PLAN_APRES_S:
                # Le défi ne s'est pas levé seul : il faut l'humain, la fenêtre vient devant.
                devant = True
                await self._au_premier_plan(page, url)
            try:
                if await page.query_selector(sel):
                    logger.info(f"{nom}: défi levé après {duree()} s, contenu attendu : {url}")
                    return True
                if not self._looks_blocked(None, await page.content()):
                    # La durée du défi, sans la grâce qui suit.
                    leve_apres = duree()
                    try:
                        await page.wait_for_selector(sel, timeout=_GRACE_APRES_DEFI_MS)
                        logger.info(
                            f"{nom}: défi levé après {leve_apres} s, contenu attendu : {url}"
                        )
                        return True
                    except PatchrightError:
                        # Un défi peut revenir pendant la grâce : on reste alors à l'attendre.
                        if not self._looks_blocked(None, await page.content()):
                            logger.info(
                                f"{nom}: défi levé après {leve_apres} s, "
                                f"page sans le contenu attendu : {url}"
                            )
                            return False
            except PatchrightError:
                # Contexte détruit le temps d'une navigation du défi : on regarde à nouveau.
                if page.is_closed():
                    logger.info(f"{nom}: fenêtre fermée après {duree()} s : {url}")
                    raise
            await page.wait_for_timeout(_REGARD_DEFI_MS)
        logger.warning(
            f"{nom}: défi non levé après {limite_s} s, "
            f"page laissée pour le prochain passage : {url}"
        )
        return False

    async def _au_premier_plan(self, page, url: str) -> None:
        """La fenêtre du défi, ouverte hors de l'écran, revient à l'écran et passe
        devant : seul un humain peut encore lever le défi. Un échec ne coûte que la
        fenêtre restée cachée (l'attente continue jusqu'à sa limite)."""
        logger.warning(
            f"{self.__class__.__name__}: défi non levé seul après {_PREMIER_PLAN_APRES_S} s — "
            f"fenêtre au premier plan, résous-le : {url}"
        )
        try:
            cdp = await page.context.new_cdp_session(page)
            fenetre = await cdp.send("Browser.getWindowForTarget")
            await cdp.send(
                "Browser.setWindowBounds",
                {"windowId": fenetre["windowId"], "bounds": {"left": 80, "top": 60}},
            )
            await page.bring_to_front()
        except PatchrightError as e:
            logger.info(f"{self.__class__.__name__}: fenêtre non ramenée à l'écran ({e})")

    @staticmethod
    def _looks_blocked(error_message: str | None, html: str | None) -> bool:
        """Détecte un blocage anti-bot (Cloudflare) sur échec OU page-défi 200."""
        err = (error_message or "").lower()
        if any(k in err for k in ("cloudflare", "anti-bot", "challenge", "403", "forbidden")):
            return True
        return _marqueur_de_defi(html) is not None


# NE PAS tester "challenge-platform"/"cf-chl" nus : Cloudflare injecte le
# beacon /cdn-cgi/challenge-platform/... sur les pages DÉJÀ PASSÉES → faux
# positif systématique (fenêtre VISIBLE à chaque page). On ne matche que
# l'interstitiel réel (« Just a moment », objet de défi _cf_chl_opt, form).
_MARQUEURS_DE_DEFI = (
    "just a moment",
    "checking your browser",
    "checking if the site connection is secure",
    "_cf_chl_opt",
    'id="challenge-form"',
)


def _marqueur_de_defi(html: str | None) -> str | None:
    """PUR. Ce qui fait prendre ce HTML pour un défi : dit par le journal, pour qu'une
    fausse alerte se voie (le contrôle ne porte que sur les pages sans conteneur)."""
    h = (html or "").lower()
    return next((m for m in _MARQUEURS_DE_DEFI if m in h), None)


def _dossier_des_defis() -> Path:
    return Path(DATA_PATH) / "diagnostics" / "defis_cloudflare"


def _garder_le_defi(url: str, html: str | None) -> None:
    """Ce que Cloudflare a servi à une fenêtre restée bloquée : il n'en resterait rien,
    le brut ne gardant que les pages. Une pièce à relire, jamais une raison d'échouer."""
    if not html:
        return
    fin_d_adresse = re.sub(r"[^\w-]+", "_", url.rstrip("/").rsplit("/", 1)[-1])[:80]
    fichier = _dossier_des_defis() / f"{datetime.now():%Y%m%d_%H%M%S}_{fin_d_adresse}.html"
    try:
        fichier.parent.mkdir(parents=True, exist_ok=True)
        fichier.write_text(f"<!-- {url} -->\n{html}", encoding="utf-8")
        logger.info(f"HTML du défi gardé : {fichier}")
    except OSError as e:
        logger.warning(f"HTML du défi non gardé ({fichier}) : {e}")


#: Le défi levé, le temps laissé au conteneur attendu d'apparaître : une page sans
#: paroles (pas encore transcrites, instrumental) ne l'aura jamais.
_GRACE_APRES_DEFI_MS = 15_000
#: Entre deux regards sur une fenêtre visible.
_REGARD_DEFI_MS = 2_000
#: Un défi que le navigateur n'a pas levé seul dans ce délai attend un humain : la
#: fenêtre, ouverte hors de l'écran, vient alors devant (197 / 197 levés en ≤ 5 s).
_PREMIER_PLAN_APRES_S = 15
#: Hors de tout écran, même à gauche d'un écran principal (coordonnées négatives).
_POSITION_HORS_ECRAN = "--window-position=-10000,-10000"

#: Les erreurs du navigateur qui disent une coupure du RÉSEAU, jamais un défi :
#: le DNS, la carte, la connexion. Un délai dépassé n'en est pas (un défi peut
#: faire attendre la page).
_ERREURS_RESEAU = (
    "net::ERR_NAME_NOT_RESOLVED",
    "net::ERR_INTERNET_DISCONNECTED",
    "net::ERR_NETWORK_CHANGED",
    "net::ERR_NETWORK_IO_SUSPENDED",
    "net::ERR_ADDRESS_UNREACHABLE",
    "net::ERR_CONNECTION_REFUSED",
    "net::ERR_CONNECTION_RESET",
    "net::ERR_CONNECTION_CLOSED",
    "net::ERR_CONNECTION_ABORTED",
    "net::ERR_CONNECTION_FAILED",
    "net::ERR_PROXY_CONNECTION_FAILED",
)


def erreur_reseau(exc: BaseException) -> bool:
    """PUR. L'erreur du navigateur dit une coupure réseau : la page n'est pas venue,
    aucune fenêtre visible n'y changera rien."""
    texte = str(exc)
    return any(code in texte for code in _ERREURS_RESEAU)


class _BrowserSession:
    """Une page ouverte, N navigations. Rendue par `CrawlAIScraperBase.session`.

    ⚠️ Cette classe n'ouvre **aucune observation** : elle n'enregistre que des
    TENTATIVES (un aller-retour réseau). L'observation — l'appel LOGIQUE, qui
    porte le verdict — appartient à l'appelant, parce que lui seul sait où finit
    l'unité de travail : récupérer la page d'un morceau PUIS la parser, c'est un
    seul verdict, pas deux. Ouvrir un verdict ici le trancherait trop tôt.
    """

    def __init__(self, scraper: CrawlAIScraperBase, page):
        self._scraper = scraper
        self._page = page

    @property
    def page(self):
        """La page patchright, pour les interactions que `fetch` ne couvre pas."""
        return self._page

    async def fetch(
        self,
        url: str,
        *,
        wait_for: str | None = None,
        js_before_wait: str | None = None,
        wait_timeout: int = 15_000,
        page_timeout: int = 30_000,
        delay_before_return: float = 0.0,
        viewport: dict | None = None,
    ) -> str | None:
        """Navigue et rend le HTML rendu, ou `None` si la page n'est pas venue.

        `viewport` : taille de fenêtre le temps de CETTE page, rétablie après
        (une liste virtualisée ne rend que ce qui tient à l'écran).

        Point de passage des pages de session (chantier E) : en REJEU, la page
        gardée sous son URL (jamais le navigateur) ; sinon le HTML rendu d'une
        source activée est gardé brut, avec le statut de la navigation."""
        from src.persistence import brut

        key = self._scraper.health_key
        if brut.rejeu_actif():
            lu = brut.servir(key, url)
            return lu[1].decode("utf-8") if lu else None
        avant = None
        if viewport:
            avant = self._page.viewport_size
            try:
                await self._page.set_viewport_size(viewport)
            except PatchrightError as e:
                logger.debug(f"{url}: taille de fenêtre ignorée ({e})")
                avant = None
        try:
            return await self._fetch(
                url, key, wait_for, js_before_wait, wait_timeout, page_timeout, delay_before_return
            )
        finally:
            if avant:
                try:
                    await self._page.set_viewport_size(avant)
                except PatchrightError:
                    pass

    async def _fetch(
        self, url, key, wait_for, js_before_wait, wait_timeout, page_timeout, delay_before_return
    ) -> str | None:
        from src.persistence import brut

        chrono = self._scraper.chrono
        type_ = type_de_page(url)
        chrono.pages[type_] = chrono.pages.get(type_, 0) + 1
        top = time.monotonic()

        def phase(nom: str) -> None:
            nonlocal top
            maintenant = time.monotonic()
            chrono.noter(type_, nom, maintenant - top)
            top = maintenant

        try:
            reponse = await self._page.goto(
                url, wait_until="domcontentloaded", timeout=page_timeout
            )
            phase("goto")
            if wait_for and wait_for.startswith("css:"):
                try:
                    await self._page.wait_for_selector(wait_for[4:], timeout=wait_timeout)
                except PatchrightError:
                    # Sélecteur absent : on rend quand même le HTML. C'est à
                    # l'appelant de dire si c'est une donnée absente ou une
                    # structure changée — lui seul sait ce qu'il cherchait.
                    logger.debug(f"{url}: sélecteur {wait_for[4:]} non apparu")
                    chrono.delais[type_] = chrono.delais.get(type_, 0) + 1
                phase("attente")
            if js_before_wait:
                try:
                    await self._page.evaluate(js_before_wait)
                except PatchrightError as e:
                    logger.debug(f"{url}: js_before_wait ignoré ({e})")
                phase("script")
            if delay_before_return:
                await self._page.wait_for_timeout(int(delay_before_return * 1000))
                phase("pause")
            html = await self._page.content()
            phase("html")
        except PatchrightError as e:
            source_usage.note_failure(key, e, detail=f"session : {e}")
            logger.error(f"{type(self._scraper).__name__}: session {url}: {e}")
            return None

        if not html:
            source_usage.record_attempt(key, IssueKind.UNREACHABLE, detail="session : page vide")
            return None
        source_usage.record_attempt(key, IssueKind.OK, detail="session")
        brut.consigner(key, url, reponse.status if reponse else None, html.encode("utf-8"))
        phase("brut")
        return html
