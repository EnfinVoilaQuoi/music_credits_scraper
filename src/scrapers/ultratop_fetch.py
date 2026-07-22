"""Fetch des pages de certifications Ultratop (BRMA) à travers l'infra
anti-Cloudflare du projet.

Depuis sa refonte, ultratop.be est passé derrière **Cloudflare** et bloque les
requêtes `requests` non-navigateur (403). On réutilise donc `CrawlAIScraperBase`
(patchright + profil PERSISTANT, déjà en place pour Genius) : on attend
l'apparition de `.chart_title` (les entrées de certif) au lieu du conteneur
paroles Genius, puis on récupère le HTML rendu.

⚠️ PREMIÈRE UTILISATION : le profil persistant a un cookie `cf_clearance` pour
genius.com mais PAS pour ultratop.be. La 1ʳᵉ page ouvrira donc une fenêtre
VISIBLE pour résoudre le challenge Cloudflare d'ultratop.be UNE fois ; le cookie
est ensuite mémorisé et tout repasse en headless (même mécanisme que Genius).

URLs (inchangées par la refonte) : /fr/or-platine/{année}/{singles|albums}.
Le DOM aussi est inchangé : .chart_title = <B>Artiste</B><BR>Titre,
.company = "JJ/MM/AAAA: Niveau [JJ/MM/AAAA: Niveau ...]".
"""

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
        _scraper = CrawlAIScraperBase(headless=True)
    return _scraper


def fetch_ultratop_html(year, category: str) -> str | None:
    """Récupère le HTML d'une page certif Ultratop via le navigateur anti-CF.

    `category` = 'singles' ou 'albums'. Retourne le HTML ou None si la page est
    vide / bloquée par Cloudflare (le challenge non résolu).
    """
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
