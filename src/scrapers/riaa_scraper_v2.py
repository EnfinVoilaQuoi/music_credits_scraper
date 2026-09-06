"""Scraper RIAA (gold-platinum) via patchright — remplace l'ancien Selenium.

Pourquoi patchright : cohérence projet (Genius/BRMA), maintenance plus simple,
et l'ancien **headless Selenium ne passait pas** (RIAA est derrière Cloudflare).
patchright (Chromium undetected, profil persistant ou CDP) passe le CF.

**Site REFAIT (constaté le 2026-09-06)** — thème Tailwind, préfixes `tw-`. La
version précédente de ce fichier lisait l'ancien DOM et rendait **0 ligne pour
toute requête** depuis ~2026-07-12, sans que rien ne le signale. Ce qui a changé :

  - le mode de date n'est plus `date_option=certification|release` (paramètre
    IGNORÉ) mais un interrupteur **`release_date_toggle`** : absent = recherche
    par date de SORTIE, `=1` = par date de CERTIFICATION. C'est très exactement
    le symptôme rapporté (« la recherche se fait par date de sortie ») ;
  - la cellule artiste est passée de `td.artists_cell` à `td.tw-artists_cell`
    (l'artiste ressortait vide → `_parse_main` rendait `None` → toutes les
    lignes tombaient) ;
  - l'ordre des `others_cell` a changé (titre, label, format, date). On ne s'y
    fie donc PLUS : la date est reconnue à sa FORME, le format vient de
    `td.format_cell`, le label est ce qui reste — insensible à un prochain
    réordonnancement ;
  - la pagination `#loadmore` est devenue `button.tw-gnp-show-more`
    (`loadMoreSearch`), qui **se retire lui-même** quand il n'y a plus de page :
    condition d'arrêt franche, au lieu du compteur d'échecs d'avant ;
  - MORE DETAILS (`showDefaultDetail`) est devenu `showTimeline(id)`, qui POSTe
    `load_detail_from_recent_timeline` à `admin-ajax.php` et injecte le résultat
    dans une modale UNIQUE. On ne peut donc plus déclencher toutes les lignes et
    lire la page : `_trigger_details` appelle l'AJAX ligne par ligne et dépose
    chaque réponse dans son propre `div.riaa-timeline[data-award-id]`. Le HTML
    rendu redevient auto-suffisant — condition pour que les fixtures offline
    (`tests/test_riaa_fixtures.py`) gardent un sens.

Le NIVEAU reste encodé dans le badge, mais l'`alt` porte désormais la FAMILLE
d'award : `alt="badge DI level 2"`, `alt="badge LA level 61"`. La famille compte :
`LA` = awards **latins**, dont le vocabulaire est Oro/Platino/Diamante et dont
les seuils d'unités n'ont rien à voir avec les Gold/Platinum US. L'ancienne regex
`(\\d+)_big` lisait `la_61_big.png` comme « 61x Platinum » : un niveau inventé.

Deux entrées :
  - scrape_by_date_range(from, to)  → bulk, ligne principale.
  - scrape_by_artist(artist)        → complet (timeline = historique des paliers).
"""

from __future__ import annotations

import os
import re
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from bs4 import BeautifulSoup

from src.concurrency import async_loop
from src.observability import source_usage
from src.observability.issues import IssueKind
from src.utils.cert_normalize import (
    FAMILLE_LATINE,
    PROGRAMME_LATIN,
    PROGRAMME_US,
    programme_riaa,
    riaa_units,
)
from src.utils.logger import get_logger

# patchright est une dépendance OPTIONNELLE (installée avec crawl4ai), importée en
# lazy. On expose sa classe d'erreur de base — DISTINCTE de celle de playwright —
# pour cibler les `except` ; sentinelle jamais levée si la lib est absente.
try:
    from patchright.async_api import Error as PatchrightError
except ImportError:  # pragma: no cover

    class PatchrightError(Exception):
        """Sentinelle quand patchright est absent (jamais levée)."""


logger = get_logger(__name__)

#: Clé de `source_health.SOURCES` sous laquelle cet usage est compté.
_SOURCE = "riaa"

_BASE = "https://www.riaa.com/gold-platinum/"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Infra anti-Cloudflare partagée avec le reste du projet
_CDP_URL = os.getenv("GENIUS_CDP_URL")
_BROWSER_CHANNEL = os.getenv("SCRAPER_BROWSER_CHANNEL")

#: Vocabulaire des paliers par famille d'award. La famille latine a les mêmes
#: numéros de badge, d'autres noms — et une AUTRE échelle d'unités (Platino
#: 60 000, pas 1 000 000), portée par `cert_normalize.riaa_units`. Les codes de
#: famille eux-mêmes viennent de `cert_normalize` : ils sont lus ici (badge) et
#: interprétés là-bas (seuils), donc une seule définition.
_LEVEL_WORDS = {
    FAMILLE_LATINE: ("Oro", "Platino", "Diamante"),
    "": ("Gold", "Platinum", "Diamond"),
}

#: Onglets du site (`movetoTab`) : les deux programmes sont servis séparément.
_ONGLET_CLASSIQUE = "default-award"
_ONGLET_LATIN = "platinum-latin"

_BADGE_ALT_RE = re.compile(r"badge\s+([A-Za-z]+)\s+level\s+(\d+)", re.I)
_BADGE_SRC_RE = re.compile(r"(?:([A-Za-z]+)_)?(\d+)_big", re.I)
#: Libellé de palier tel qu'écrit dans la timeline : « 15X PLATINUM », « ORO »…
_LABEL_RE = re.compile(r"^(?:(\d+)\s*X\s*)?([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\- ]*)$")
_LABEL_WORDS = {
    "GOLD": "Gold",
    "PLATINUM": "Platinum",
    "DIAMOND": "Diamond",
    "ORO": "Oro",
    "PLATINO": "Platino",
    "DIAMANTE": "Diamante",
}


def _profile_dir() -> str:
    base = str(Path.home() / ".music_credits_scraper" / "cf_profile")
    return f"{base}_{_BROWSER_CHANNEL}" if _BROWSER_CHANNEL else base


def _level_from_number(n: int, family: str = "") -> str:
    """Numéro de badge + famille d'award → palier (multiplicateur inclus)."""
    gold, platinum, diamond = _LEVEL_WORDS.get((family or "").upper(), _LEVEL_WORDS[""])
    if n == 0:
        return gold
    if n == 1:
        return platinum
    if n == 10:
        return diamond
    return f"{n}x {platinum}"


def _badge_infos(src: str, alt: str = "") -> tuple[str, str]:
    """Badge → (niveau, famille d'award).

    L'`alt` (« badge DI level 2 ») est prioritaire : il porte la FAMILLE d'award,
    que le nom de fichier ne donne que par un préfixe (`la_61_big.png`). Le nom
    de fichier reste le repli — c'est lui seul que portaient les vieilles pages.
    """
    m = _BADGE_ALT_RE.search(alt or "")
    if m:
        famille = m.group(1).upper()
        return _level_from_number(int(m.group(2)), famille), famille
    m = _BADGE_SRC_RE.search(src or "")
    if not m:
        return "", ""
    famille = (m.group(1) or "").upper()
    return _level_from_number(int(m.group(2)), famille), famille


def _level_from_img(src: str, alt: str = "") -> str:
    """Badge → niveau RIAA (multiplicateur inclus)."""
    return _badge_infos(src, alt)[0]


def _level_from_label(text: str) -> str:
    """« 15X PLATINUM » → « 15x Platinum » ; « ORO » → « Oro ».

    Les libellés de la timeline sont en capitales et séparés par une espace
    insécable. Un mot inconnu est conservé tel quel (en capitales d'initiale) :
    inventer une correspondance serait pire que de recopier la source.
    """
    t = (text or "").replace("\xa0", " ").strip()
    t = re.sub(r"\s+", " ", t)
    if not t:
        return ""
    m = _LABEL_RE.match(t)
    if not m:
        return t.title()
    mult, word = m.group(1), m.group(2).strip().upper()
    canon = _LABEL_WORDS.get(word, word.title())
    return f"{mult}x {canon}" if mult else canon


def _units_for(level: str) -> int | None:
    """Unités du palier, sur l'échelle de SON programme (cf. `cert_normalize`)."""
    return riaa_units(level)


def _to_iso(date_str: str) -> str:
    """« April 10, 2026 » → « 2026-04-10 ». Laisse tel quel si déjà ISO/inconnu."""
    s = (date_str or "").strip()
    if not s:
        return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s


def _is_date(text: str) -> bool:
    """La cellule se lit-elle comme une date ? (sert à repérer la colonne)."""
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", _to_iso(text)))


# Endpoint AJAX WordPress du site (constaté le 2026-09-06). La page le publie
# dans la globale `DATA_AJAX_URL`, mais patchright évalue le JS dans un monde
# ISOLÉ où les globales de la page n'existent pas : on tente la globale, et on
# retombe sur cette constante — c'est elle qui sert en pratique.
_AJAX_URL = "https://www.riaa.com/wp-admin/admin-ajax.php"

# JS injecté pour l'historique : `showTimeline` remplit une modale UNIQUE, donc
# cliquer chaque ligne n'accumule rien dans le DOM. On rejoue la requête qu'elle
# émet et on dépose chaque réponse dans son propre conteneur, ce qui rend le
# HTML final auto-suffisant (indispensable pour les fixtures rejouées offline).
# `fetch` plutôt que jQuery : disponible dans TOUS les mondes d'exécution.
_TIMELINE_JS = """
async ({ ids, ajaxUrl }) => {
  const url =
    (typeof DATA_AJAX_URL !== 'undefined' && DATA_AJAX_URL) || ajaxUrl;
  const box = document.createElement('div');
  box.id = 'riaa-timelines';
  box.style.display = 'none';
  document.body.appendChild(box);
  for (const id of ids) {
    try {
      const resp = await fetch(url, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
          'X-Requested-With': 'XMLHttpRequest',
        },
        body: new URLSearchParams({
          action: 'load_detail_from_recent_timeline',
          id: id,
        }),
      });
      const data = await resp.json();
      const item = document.createElement('div');
      item.className = 'riaa-timeline';
      item.setAttribute('data-award-id', id);
      item.innerHTML = (data && data.body) || '';
      box.appendChild(item);
    } catch (e) {
      /* une ligne sans détail ne doit pas emporter les autres */
    }
  }
  return box.querySelectorAll('.riaa-timeline').length;
}
"""


class RIAAScraperV2:
    """Scraper RIAA patchright. API compatible avec l'updater existant."""

    def __init__(self, headless: bool = True):
        self.headless = headless

    # ------------------------------------------------------------------ public
    @staticmethod
    def _search_url(
        *,
        artist: str = "",
        start: str = "",
        end: str = "",
        by_certification_date: bool = True,
        programme: str = PROGRAMME_US,
    ) -> str:
        """URL de recherche du NOUVEAU formulaire (`form_gnp`).

        `release_date_toggle=1` = recherche par date de CERTIFICATION ; absent =
        par date de sortie. Les anciens paramètres (`date_option`, `award`,
        `type`, `category`, `adv`) ne sont plus lus par le site : les envoyer
        n'aidait pas, mais les garder aurait entretenu l'illusion qu'ils pilotent
        quelque chose.

        `programme` choisit l'ONGLET : le site sépare le programme classique
        (`default-award`) du programme latin (`platinum-latin`, « RIAA Premios de
        Oro y Platino »). C'est ce qui permet de connaître le programme d'une
        certification par la MESURE plutôt que par le vocabulaire de son
        libellé — lequel ne suffit pas, un Oro latin ayant longtemps été écrit
        « Gold » par les collectes qui ignoraient la distinction.
        """
        onglet = _ONGLET_LATIN if programme == PROGRAMME_LATIN else _ONGLET_CLASSIQUE
        url = (
            f"{_BASE}?tab_active={onglet}&ar={quote(artist)}&se=&ti=&lab=" f"&from={start}&to={end}"
        )
        if by_certification_date:
            url += "&release_date_toggle=1"
        return url + "#search_section"

    def scrape_by_date_range(
        self,
        start_date: str,
        end_date: str,
        date_option: str = "certification",
        get_details: bool = False,
    ) -> list[dict]:
        """Bulk par plage de dates (ligne principale par défaut).

        Compté dans l'usage des sources — il ne l'était PAS avant, si bien qu'un
        parseur mort en bulk restait invisible du panneau de santé, alors même
        que c'est le flux qui alimente la base.
        """
        start, end = _norm_date(start_date), _norm_date(end_date)
        url = self._search_url(start=start, end=end, by_certification_date=date_option != "release")
        logger.info(f"RIAA dates {start}→{end} (détails={get_details})")
        with source_usage.observe(_SOURCE, label=f"{start}→{end}") as obs:
            html = self._render(url, load_all=True, get_details=get_details)
            if not html:
                obs.fail(IssueKind.UNREACHABLE, "page non rendue")
                return []
            resultats = self._parse_verifie(html, get_details, obs)
            if resultats and date_option != "release":
                _verifier_fenetre(resultats, start, end, obs)
            return resultats

    def scrape_by_artist(self, artist: str, get_details: bool = True) -> list[dict]:
        """Par artiste (avec la timeline = historique des paliers).

        **UNE seule requête, sur l'onglet par défaut.** Le site affiche deux
        onglets — classique et « Premios de Oro y Platino » — et il est tentant
        d'en déduire qu'une recherche ne rend que l'onglet demandé, donc qu'il
        faut interroger les deux pour un artiste hispanophone. C'est faux, et
        mesuré le 2026-09-06 sur deux artistes : `tab_active` pilote
        l'AFFICHAGE, pas la recherche. L'onglet par défaut rend TOUT, latin
        compris (Luis Fonsi 14 lignes dont 13 latines ; Bad Bunny 93 dont 90),
        et l'onglet latin en est un sous-ensemble STRICT — sa différence avec
        l'autre est vide dans les deux cas.

        Une seconde requête ne rapporterait donc rien tout en doublant le coût :
        avec `get_details`, ce sont 90 allers-retours AJAX de plus pour Bad
        Bunny. `_search_url` garde son paramètre `programme` — il documente la
        mécanique du site et sert à la mesure — mais l'appeler ici serait payer
        pour un sous-ensemble de ce qu'on a déjà.

        Le programme de chaque ligne est de toute façon relevé sur son BADGE
        (cf. `_parse_main`), jamais déduit de l'onglet : c'est ce qui rend
        l'onglet inutile à la classification.
        """
        url = self._search_url(artist=artist)
        logger.info(f"RIAA artiste '{artist}' (détails={get_details})")
        with source_usage.observe(_SOURCE, label=f"artiste {artist}") as obs:
            html = self._render(url, load_all=True, get_details=get_details)
            if not html:
                obs.fail(IssueKind.UNREACHABLE, "page non rendue")
                return []
            return self._parse_verifie(html, get_details, obs)

    @staticmethod
    def _parse_verifie(html: str, get_details: bool, obs) -> list[dict]:
        """Parse en distinguant « rien à dire » de « on ne sait plus lire ».

        Des lignes `tr.table_award_row` dans le HTML mais aucune extraite = le
        parseur ne comprend plus la page : c'est un échec de communication
        (`parse`), jamais un `absent` — le seul verdict exclu du numérateur est
        aussi le seul capable de rendre une panne muette, et c'est précisément ce
        qui s'est produit ici pendant deux mois.
        """
        resultats = _parse_results(html, get_details)
        if resultats:
            return resultats
        lignes_vues = _count_award_rows(html)
        if lignes_vues:
            logger.error(
                f"RIAA : {lignes_vues} ligne(s) dans la page, AUCUNE extraite — "
                "le gabarit du site a changé (re-capturer les fixtures)."
            )
            obs.fail(IssueKind.PARSE, f"{lignes_vues} lignes non parsées")
        else:
            obs.absent("aucune certification")
        return []

    # Compat ancienne API
    def init_driver(self):  # no-op : patchright gère le navigateur à la volée
        pass

    def close_driver(self):
        pass

    # ------------------------------------------------------------------ rendu
    def _render(self, url: str, load_all: bool, get_details: bool) -> str | None:
        # Pont F4 : rendu sur LA boucle applicative (plus d'asyncio.run par appel).
        try:
            return async_loop.run_sync(self._render_async(url, load_all, get_details))
        except (PatchrightError, RuntimeError, OSError) as e:
            logger.error(f"RIAA: rendu patchright échoué : {e}")
            return None

    @asynccontextmanager
    async def _page_ouverte(self, pw):
        """Ouvre une page RIAA. **SEUL endroit où les deux routes divergent.**

        Deux routes coexistent :
          - **CDP** — celle que la GUI emprunte RÉELLEMENT (elle pose
            `GENIUS_CDP_URL` dans l'environnement du sous-processus) ;
          - **profil persistant** — celle des scripts, des captures de fixtures
            et des essais en ligne de commande.

        Tout ce qui suit (navigation, pagination, timelines, parsing) est
        COMMUN, et doit le rester : c'est la seule façon qu'une route ne rouille
        pas pendant qu'on corrige l'autre. Après la refonte du site le
        2026-09-06, le rattrapage avait été validé en headless alors que la GUI
        passe par le CDP — les deux ont dû être vérifiées séparément, ce qui
        n'aurait pas été nécessaire si la divergence avait déjà été confinée
        ici. Ne PAS remettre de logique de site dans l'une des deux branches.
        """
        if _CDP_URL:
            browser = await pw.chromium.connect_over_cdp(_CDP_URL)
            ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = await ctx.new_page()
            logger.info(f"RIAA : connecté via CDP {_CDP_URL}")
            try:
                yield page
            finally:
                await page.close()
                await browser.close()
        else:
            os.makedirs(_profile_dir(), exist_ok=True)
            launch = dict(
                headless=self.headless,
                user_agent=_USER_AGENT,
                viewport={"width": 1366, "height": 900},
            )
            if _BROWSER_CHANNEL:
                launch["channel"] = _BROWSER_CHANNEL
            ctx = await pw.chromium.launch_persistent_context(_profile_dir(), **launch)
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            try:
                yield page
            finally:
                await ctx.close()

    async def _render_async(self, url: str, load_all: bool, get_details: bool) -> str | None:
        try:
            from patchright.async_api import async_playwright
        except ImportError as e:
            logger.error(
                f"patchright indisponible : {e} — `pip install -U crawl4ai && crawl4ai-setup`"
            )
            return None

        async with async_playwright() as pw, self._page_ouverte(pw) as page:
            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            try:
                await page.wait_for_selector("tr.table_award_row", timeout=20_000)
            except PatchrightError:
                logger.warning("RIAA : aucune ligne (page vide / Cloudflare ?)")
                return await page.content()

            if load_all:
                await self._click_load_more(page)
            if get_details:
                await self._trigger_details(page)

            return await page.content()

    async def _click_load_more(self, page) -> None:
        """Clique « Show More » jusqu'à épuisement.

        Le bouton se SUPPRIME lui-même quand le serveur répond `has_more: false`
        (cf. `loadMoreSearch`) : sa disparition est la condition d'arrêt franche.
        On garde tout de même une garde d'absence de progression — un AJAX qui
        échoue laisse le bouton en place et ferait boucler à l'infini.
        """
        clicks = 0
        while clicks < _MAX_LOAD_MORE:
            btn = await page.query_selector("button.tw-gnp-show-more") or await page.query_selector(
                "#loadmore"
            )
            if not btn or not await btn.is_visible():
                break
            before = len(await page.query_selector_all("tr.table_award_row"))
            try:
                await btn.scroll_into_view_if_needed()
                await btn.click()
            except PatchrightError:
                await page.evaluate(
                    "var b=document.querySelector('button.tw-gnp-show-more')"
                    "||document.getElementById('loadmore'); if(b){b.click();}"
                )
            clicks += 1
            if not await _attendre_croissance(page, before):
                break
        if clicks >= _MAX_LOAD_MORE:
            logger.warning(f"RIAA : plafond de {_MAX_LOAD_MORE} pages atteint (résultat tronqué ?)")
        total = len(await page.query_selector_all("tr.table_award_row"))
        logger.info(f"RIAA : {clicks} page(s) supplémentaire(s), {total} ligne(s) au total")

    async def _trigger_details(self, page) -> None:
        """Charge la timeline (historique des paliers) de chaque ligne.

        Une requête AJAX par ligne, séquentielle, exactement celle que le bouton
        déclenche — pas de contournement, juste la modale court-circuitée parce
        qu'elle est unique et écraserait le contenu précédent.
        """
        ids = await page.eval_on_selector_all(
            "tr.table_award_row",
            "rows => rows.map(r => (r.id || '').replace('default_', '')).filter(Boolean)",
        )
        if not ids:
            return
        logger.info(f"RIAA : chargement de la timeline de {len(ids)} ligne(s)…")
        try:
            n = await page.evaluate(_TIMELINE_JS, {"ids": ids, "ajaxUrl": _AJAX_URL})
        except PatchrightError as e:
            logger.warning(f"RIAA : timelines non chargées ({e})")
            return
        if not n:
            logger.warning(
                "RIAA : aucune timeline récupérée — endpoint AJAX déplacé ? "
                "(l'historique des paliers manquera, la ligne principale reste bonne)"
            )
        else:
            logger.info(f"RIAA : {n} timeline(s) récupérée(s)")


#: Garde-fou de boucle : au-delà, on soupçonne un bouton qui ne disparaît jamais.
_MAX_LOAD_MORE = 200


async def _attendre_croissance(page, before: int, tentatives: int = 4) -> bool:
    """Attend que le tableau grandisse après un clic. False si rien ne bouge."""
    for _ in range(tentatives):
        await page.wait_for_timeout(1200)
        if len(await page.query_selector_all("tr.table_award_row")) > before:
            return True
    return False


# ---------------------------------------------------------------------- parsing
def _norm_date(d: str) -> str:
    """Accepte MM/DD/YYYY ou YYYY-MM-DD → renvoie YYYY-MM-DD."""
    d = (d or "").strip()
    if "/" in d:
        p = d.split("/")
        if len(p) == 3:
            return f"{p[2]}-{int(p[0]):02d}-{int(p[1]):02d}"
    return d


def _txt(node, sel) -> str:
    el = node.select_one(sel)
    return el.get_text(strip=True) if el else ""


def _verifier_fenetre(resultats: list[dict], start: str, end: str, obs) -> None:
    """La fenêtre demandée est-elle celle qu'on a obtenue ?

    C'est le contrôle qui manquait : l'interrupteur de date a changé de nom sans
    que rien ne s'en aperçoive, parce qu'aucun invariant ne reliait la requête au
    résultat. Un débordement partiel se signale (bornes incluses ou non, on ne
    tranche pas) ; TOUTES les lignes hors fenêtre, en revanche, ne s'explique que
    par un filtre qui ne filtre pas ce qu'on croit.
    """
    if not (start and end):
        return
    dates = [r["certification_date"] for r in resultats if r.get("certification_date")]
    if not dates:
        return
    hors = [d for d in dates if d < start or d > end]
    if not hors:
        return
    exemples = ", ".join(sorted(hors)[:3])
    if len(hors) == len(dates):
        logger.error(
            f"RIAA : les {len(hors)} certifications rendues sont HORS de la fenêtre "
            f"{start}→{end} (ex. {exemples}) — le filtre de date ne porte pas sur "
            "la date de certification."
        )
        obs.fail(IssueKind.PARSE, "fenêtre de dates non respectée")
    else:
        logger.warning(
            f"RIAA : {len(hors)}/{len(dates)} certification(s) hors fenêtre "
            f"{start}→{end} (ex. {exemples})"
        )


def _parse_main(row) -> dict | None:
    """Ligne principale du tableau.

    Les colonnes sont reconnues à leur NATURE et non à leur rang : le site a déjà
    réordonné `others_cell` une fois (titre, date, label → titre, label, format,
    date), et une lecture positionnelle rendrait alors un label en guise de date
    sans que rien ne proteste.
    """
    artist = _txt(row, "td.tw-artists_cell") or _txt(row, "td.artists_cell")
    fmt = _txt(row, "td.format_cell").replace("MORE DETAILS", "").strip()
    cells = [c.get_text(strip=True) for c in row.select("td.others_cell")]
    cells = [c for c in cells if c]
    if not cells:
        return None

    title = cells[0]
    reste = cells[1:]
    cert_date = next((c for c in reste if _is_date(c)), "")
    label = next((c for c in reste if c != cert_date and c != fmt), "")

    img = row.select_one("img.tw-atom-badge") or row.select_one("img.award")
    level, famille = _badge_infos(img.get("src", ""), img.get("alt", "")) if img else ("", "")
    if not (artist and title):
        return None
    return {
        "artist": artist,
        "title": title,
        "certification_date": _to_iso(cert_date),
        "release_date": "",
        "label": label,
        "format": fmt,
        "award_level": level,
        "certification_level": level,
        # Programme relevé À LA SOURCE (famille du badge) plutôt que déduit du
        # libellé : c'est la seule façon de le savoir pour une ligne dont le
        # niveau serait écrit dans l'autre vocabulaire, comme l'a fait l'ancien
        # code pendant des années sur les awards latins.
        "award_programme": (
            PROGRAMME_LATIN if famille == FAMILLE_LATINE else programme_riaa(level)
        ),
        # Famille VERBATIM (ST/DI/LA). Le programme s'en déduit, mais pas
        # l'inverse : c'est elle qui distingue un single PHYSIQUE d'un single
        # NUMÉRIQUE — le libellé de format dit « SINGLE » dans les deux cas —
        # et cette distinction décide du seuil applicable avant août 2006.
        "award_family": famille,
        "units": _units_for(level),
    }


def _parse_timeline_details(det) -> dict:
    """Bloc « Label / Format / Genre / Released on » de la timeline."""
    infos = {}
    for bloc in det.select(".tw-molecule-timeline-details > div"):
        champs = [d.get_text(strip=True) for d in bloc.select("div")]
        if len(champs) >= 2:
            infos[champs[0].lower()] = champs[1]
    return infos


def _parse_details(soup, rid: str, base: dict) -> list[dict]:
    """Historique des paliers depuis la timeline (une entrée par palier)."""
    if not rid:
        return []
    det = soup.select_one(f'.riaa-timeline[data-award-id="{rid}"]') or soup.select_one(
        f"#recent_{rid}_detail"
    )
    if not det:
        return []

    infos = _parse_timeline_details(det)
    release = _to_iso(infos.get("released on", ""))
    genre = infos.get("genre", "")

    history = []
    for etape in det.select('div[class*="tw-time-date-"]'):
        level = _level_from_label(_txt(etape, "p.tw-tag-label"))
        cdate = _txt(etape, "p.tw-tag-date")
        if not level:
            continue
        history.append(
            {
                "certification_level": level,
                "certification_date": _to_iso(cdate) or base.get("certification_date", ""),
                "release_date": release,
                "genre": genre,
                "units": _units_for(level),
            }
        )
    if history:
        return history

    # Repli : ancien gabarit (pages enregistrées avant la refonte du site).
    return _parse_details_legacy(det, base)


def _parse_details_legacy(det, base: dict) -> list[dict]:
    """Ancien bloc `tr.content_recent_table` — gardé pour les vieilles fixtures."""
    history = []
    for cr in det.select("tr.content_recent_table"):
        cells = [c.get_text(strip=True) for c in cr.select("td")]
        lvl_cell = next((c for c in cells if "|" in c), "")
        if lvl_cell:
            parts = lvl_cell.split("|", 1)
            level = parts[0].strip()
            cdate = parts[1].strip() if len(parts) > 1 else base.get("certification_date", "")
        elif len(cells) >= 2:
            level, cdate = cells[1].strip(), base.get("certification_date", "")
        else:
            continue
        if not level:
            continue
        history.append(
            {
                "certification_level": level,
                "certification_date": _to_iso(cdate),
                "release_date": _to_iso(cells[0]) if cells else "",
                "units": _units_for(level),
            }
        )
    return history


def _count_award_rows(html: str) -> int:
    """Nombre de lignes de résultat PRÉSENTES dans la page.

    Confronté au nombre d'extractions, il distingue « la recherche ne rend
    rien » de « on ne sait plus lire la page » — deux situations qui se
    ressemblent comme deux gouttes d'eau dans un log et n'ont rien à voir. Ne
    sert que dans la branche à zéro résultat : pas de second parsing en régime
    nominal.
    """
    return len(BeautifulSoup(html, "html.parser").select("tr.table_award_row"))


def _parse_results(html: str, get_details: bool) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    lignes = soup.select("tr.table_award_row")
    out = []
    for row in lignes:
        data = _parse_main(row)
        if not data:
            continue
        if get_details:
            rid = (row.get("id") or "").replace("default_", "")
            data["history"] = _parse_details(soup, rid, data)
        out.append(data)
    logger.info(f"RIAA : {len(out)} certification(s) extraite(s) sur {len(lignes)} ligne(s)")
    return out
