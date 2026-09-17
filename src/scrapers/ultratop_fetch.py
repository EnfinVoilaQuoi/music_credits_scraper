"""Fetch des pages de certifications Ultratop (BRMA) à travers l'infra
anti-Cloudflare du projet.

Depuis sa refonte, ultratop.be est passé derrière **Cloudflare** et bloque les
requêtes `requests` non-navigateur (403). On réutilise donc `CrawlAIScraperBase`
(patchright + profil PERSISTANT, déjà en place pour Genius) : on attend
l'apparition de `.chart_title` (les entrées de certif) au lieu du conteneur
paroles Genius, puis on récupère le HTML rendu.

⚠️ ROUTE CDP OBLIGATOIRE — ce Cloudflare-là est STRICT (≠ Genius, ≠ RIAA) : il
fait boucler tout navigateur LANCÉ par de l'automation, headless comme fenêtre
visible, même le vrai Chrome via `channel="chrome"` (JOURNAL 2026-06-29). La
seule voie qui passe : un Chrome démarré NORMALEMENT avec son port de debug,
auquel patchright s'ATTACHE (`connect_over_cdp`). `preparer_route_cdp` est le
point UNIQUE qui pose cette route ; il était écrit à trois endroits au-dessus
de ce module (GUI, `run_brma.ps1`, `capture_fixtures`) et nulle part dedans, si
bien que `python -m src.utils.update_brma` partait boucler en headless.

URLs (inchangées par la refonte) : /fr/or-platine/{année}/{singles|albums}.
Le DOM aussi est inchangé : .chart_title = <B>Artiste</B><BR>Titre,
.company = "JJ/MM/AAAA: Niveau [JJ/MM/AAAA: Niveau ...]".
"""

import os

from bs4 import BeautifulSoup

from src.scrapers.crawl4ai_scraper_base import CrawlAIScraperBase
from src.utils.logger import get_logger

logger = get_logger(__name__)

ULTRATOP_BASE = "https://www.ultratop.be/fr/or-platine"

# Singleton : le profil patchright est partagé (cookie CF réutilisé)
_scraper: CrawlAIScraperBase | None = None


def _get_scraper() -> CrawlAIScraperBase:
    global _scraper
    if _scraper is None:
        # `health_key` : ces pages comptent pour BRMA, pas pour le scrape Genius.
        _scraper = CrawlAIScraperBase(headless=True, health_key="brma")
    return _scraper


def preparer_route_cdp() -> str | None:
    """Garantit la route CDP et rend son URL ; None si Chrome est introuvable.

    `GENIUS_CDP_URL` déjà posée (GUI, `.env`, Brave lancé à la main) → respectée
    telle quelle. Sinon on lance — ou on retrouve — le Chrome de debug et on POSE
    la variable, que la base lit à l'appel (`crawl4ai_scraper_base._cdp_url`).

    Sans route, l'appelant doit REFUSER de partir : l'échelle headless → fenêtre
    visible ne rend jamais une page d'Ultratop, elle boucle sur le challenge
    (et `fetch_page_with_retry` y ajouterait 10 + 20 s d'attente PAR page).
    """
    url = os.getenv("GENIUS_CDP_URL")
    if url:
        return url
    from src.scrapers.cdp_chrome import ensure_cdp_chrome

    url = ensure_cdp_chrome()
    if url:
        os.environ["GENIUS_CDP_URL"] = url
        logger.info(f"Ultratop : route CDP posée ({url})")
    else:
        logger.error(
            "Ultratop : Chrome de debug introuvable — route CDP obligatoire "
            "(installe Google Chrome ou définis CHROME_PATH)"
        )
    return url


def fetch_ultratop_html(year, category: str) -> str | None:
    """Récupère le HTML d'une page certif Ultratop via le navigateur anti-CF.

    `category` = 'singles' ou 'albums'. Retourne le HTML ou None si la page est
    vide / bloquée par Cloudflare (le challenge non résolu), ou si aucune route
    CDP n'a pu être posée — on ne tente PAS le headless, il boucle.
    """
    if not preparer_route_cdp():
        return None
    url = f"{ULTRATOP_BASE}/{year}/{category}"
    scraper = _get_scraper()
    try:
        _, html = scraper._crawl_page(
            url,
            wait_for="css:.chart_title",  # attend les entrées de certif
            wait_timeout=15_000,
            page_timeout=45_000,
            delay_before_return=1.0,
        )
    except Exception:
        # Frontière crawl (crawl4ai + patchright + boucle) : surface large → trace.
        logger.exception(f"Ultratop {year}/{category} : échec fetch CF")
        return None

    if not html or "chart_title" not in html:
        logger.warning(
            f"Ultratop {year}/{category} : HTML vide ou sans entrée "
            f"(Cloudflare non résolu ? page sans certif ?)"
        )
        return None
    return html


def fetch_ultratop_soup(year, category: str) -> BeautifulSoup | None:
    """Idem `fetch_ultratop_html` mais retourne un BeautifulSoup prêt à parser."""
    html = fetch_ultratop_html(year, category)
    return BeautifulSoup(html, "html.parser") if html else None
