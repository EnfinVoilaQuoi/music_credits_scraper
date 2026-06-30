"""Scraper RIAA (gold-platinum) via patchright — remplace l'ancien Selenium.

Pourquoi patchright : cohérence projet (Genius/BRMA), maintenance plus simple,
et l'ancien **headless Selenium ne passait pas** (RIAA est derrière Cloudflare).
patchright (Chromium undetected, profil persistant ou CDP) passe le CF.

Faits du site (inspectés en live) :
  - Résultats RENDUS CÔTÉ SERVEUR (1ʳᵉ page), pagination par bouton `#loadmore`
    (id désormais en minuscule — l'ancien scraper cherchait `loadMore`, d'où sa
    panne : il ne récupérait que la 1ʳᵉ page).
  - Le NIVEAU est encodé dans le nom de l'image `img.award` = `{N}_big.png` :
    0=Gold, 1=Platinum, 2-9=Nx Platinum, 10=Diamond (multiplicateur INCLUS).
  - MORE DETAILS (historique des paliers) = AJAX au clic (`showDefaultDetail`).
    → mode bulk = ligne principale (rapide) ; mode par-artiste = avec détails.

Deux entrées :
  - scrape_by_date_range(from, to)  → bulk, ligne principale.
  - scrape_by_artist(artist)        → complet (MORE DETAILS).
"""
from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional
from urllib.parse import quote

from bs4 import BeautifulSoup

from src.utils.logger import get_logger

logger = get_logger(__name__)

_BASE = "https://www.riaa.com/gold-platinum/"
_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# Infra anti-Cloudflare partagée avec le reste du projet
_CDP_URL = os.getenv("GENIUS_CDP_URL")
_BROWSER_CHANNEL = os.getenv("SCRAPER_BROWSER_CHANNEL")

BASE_UNITS = {"gold": 500_000, "platinum": 1_000_000, "diamond": 10_000_000}


def _profile_dir() -> str:
    base = str(Path.home() / ".music_credits_scraper" / "cf_profile")
    return f"{base}_{_BROWSER_CHANNEL}" if _BROWSER_CHANNEL else base


def _level_from_img(src: str) -> str:
    """`{N}_big.png` → niveau RIAA (multiplicateur inclus)."""
    m = re.search(r'(\d+)_big', src or '')
    if not m:
        return ""
    n = int(m.group(1))
    if n == 0:
        return "Gold"
    if n == 1:
        return "Platinum"
    if n == 10:
        return "Diamond"
    return f"{n}x Platinum"


def _units_for(level: str) -> Optional[int]:
    l = (level or "").lower()
    if "diamond" in l:
        return BASE_UNITS["diamond"]
    m = re.match(r'(\d+)\s*x', l)
    mult = int(m.group(1)) if m else 1
    if "platinum" in l:
        return BASE_UNITS["platinum"] * mult
    if "gold" in l:
        return BASE_UNITS["gold"]
    return None


def _to_iso(date_str: str) -> str:
    """« April 10, 2026 » → « 2026-04-10 ». Laisse tel quel si déjà ISO/inconnu."""
    s = (date_str or "").strip()
    if not s:
        return ""
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', s):
        return s
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s


class RIAAScraperV2:
    """Scraper RIAA patchright. API compatible avec l'updater existant."""

    def __init__(self, headless: bool = True):
        self.headless = headless

    # ------------------------------------------------------------------ public
    def scrape_by_date_range(self, start_date: str, end_date: str,
                             date_option: str = "certification",
                             get_details: bool = False) -> List[Dict]:
        """Bulk par plage de dates (ligne principale par défaut)."""
        start, end = _norm_date(start_date), _norm_date(end_date)
        url = (f"{_BASE}?tab_active=default-award&ar=&ti=&lab=&genre=&format="
               f"&date_option={date_option}&from={start}&to={end}"
               f"&award=&type=&category=&adv=SEARCH#search_section")
        logger.info(f"RIAA dates {start}→{end} (détails={get_details})")
        html = self._render(url, load_all=True, get_details=get_details)
        return _parse_results(html, get_details) if html else []

    def scrape_by_artist(self, artist: str, get_details: bool = True) -> List[Dict]:
        """Par artiste (avec MORE DETAILS = historique des paliers)."""
        url = (f"{_BASE}?tab_active=default-award&ar={quote(artist)}&ti=&lab="
               f"&genre=&format=&date_option=&from=&to="
               f"&award=&type=&category=&adv=SEARCH#search_section")
        logger.info(f"RIAA artiste '{artist}' (détails={get_details})")
        html = self._render(url, load_all=True, get_details=get_details)
        return _parse_results(html, get_details) if html else []

    # Compat ancienne API
    def init_driver(self):   # no-op : patchright gère le navigateur à la volée
        pass

    def close_driver(self):
        pass

    # ------------------------------------------------------------------ rendu
    def _render(self, url: str, load_all: bool, get_details: bool) -> Optional[str]:
        try:
            return asyncio.run(self._render_async(url, load_all, get_details))
        except Exception as e:
            logger.error(f"RIAA: rendu patchright échoué : {e}")
            return None

    async def _render_async(self, url: str, load_all: bool, get_details: bool) -> Optional[str]:
        try:
            from patchright.async_api import async_playwright
        except Exception as e:
            logger.error(f"patchright indisponible : {e} — `pip install -U crawl4ai && crawl4ai-setup`")
            return None

        async with async_playwright() as pw:
            if _CDP_URL:
                browser = await pw.chromium.connect_over_cdp(_CDP_URL)
                ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
                page = await ctx.new_page()
                logger.info(f"RIAA : connecté via CDP {_CDP_URL}")
            else:
                os.makedirs(_profile_dir(), exist_ok=True)
                launch = dict(headless=self.headless, user_agent=_USER_AGENT,
                              viewport={"width": 1366, "height": 900})
                if _BROWSER_CHANNEL:
                    launch["channel"] = _BROWSER_CHANNEL
                ctx = await pw.chromium.launch_persistent_context(_profile_dir(), **launch)
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                browser = None

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                try:
                    await page.wait_for_selector("tr.table_award_row", timeout=20_000)
                except Exception:
                    logger.warning("RIAA : aucune ligne (page vide / Cloudflare ?)")
                    return await page.content()

                if load_all:
                    await self._click_load_more(page)
                if get_details:
                    await self._trigger_details(page)

                return await page.content()
            finally:
                if _CDP_URL:
                    await page.close()
                    if browser:
                        await browser.close()
                else:
                    await ctx.close()

    async def _click_load_more(self, page) -> None:
        """Clique `#loadmore` jusqu'à épuisement (id minuscule = nouveau site)."""
        fails = 0
        clicks = 0
        while fails < 3:
            lm = await page.query_selector("#loadmore")
            if not lm or not await lm.is_visible():
                break
            before = len(await page.query_selector_all("tr.table_award_row"))
            try:
                await lm.scroll_into_view_if_needed()
                await lm.click()
            except Exception:
                await page.evaluate("var l=document.getElementById('loadmore'); if(l){l.click();}")
            clicks += 1
            await page.wait_for_timeout(2500)
            after = len(await page.query_selector_all("tr.table_award_row"))
            if after == before:
                await page.wait_for_timeout(2000)
                after = len(await page.query_selector_all("tr.table_award_row"))
                fails = fails + 1 if after == before else 0
            else:
                fails = 0
        logger.info(f"RIAA : {clicks} clic(s) LOAD MORE")

    async def _trigger_details(self, page) -> None:
        """Déclenche le MORE DETAILS (AJAX) de chaque ligne pour charger l'historique."""
        rows = await page.query_selector_all("tr.table_award_row")
        logger.info(f"RIAA : chargement des détails de {len(rows)} ligne(s)…")
        for row in rows:
            rid = await row.get_attribute("id")
            if rid and rid.startswith("default_"):
                num = rid[len("default_"):]
                try:
                    await page.evaluate(f"showDefaultDetail('{num}','DI');")
                except Exception:
                    pass
                await page.wait_for_timeout(220)
        await page.wait_for_timeout(1500)


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


def _parse_main(row) -> Optional[Dict]:
    artist = _txt(row, "td.artists_cell")
    others = row.select("td.others_cell")
    title = others[0].get_text(strip=True) if others else ""
    cert_date = others[1].get_text(strip=True) if len(others) > 1 else ""
    label = others[2].get_text(strip=True) if len(others) > 2 else ""
    fmt = _txt(row, "td.format_cell").replace("MORE DETAILS", "").strip()
    img = row.select_one("img.award")
    level = _level_from_img(img.get("src", "")) if img else ""
    if not (artist and title):
        return None
    return {
        "artist": artist, "title": title,
        "certification_date": _to_iso(cert_date),
        "release_date": "", "label": label, "format": fmt,
        "award_level": level, "certification_level": level,
        "units": _units_for(level),
    }


def _parse_details(soup, rid: str, base: Dict) -> List[Dict]:
    """Historique des paliers depuis le détail (content_recent_table)."""
    det = soup.select_one(f"#recent_{rid}_detail") if rid else None
    history = []
    if not det:
        return history
    for cr in det.select("tr.content_recent_table"):
        cells = [c.get_text(strip=True) for c in cr.select("td")]
        # cellule "Niveau | Date" = celle qui contient un '|'
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
        history.append({
            "certification_level": level,
            "certification_date": _to_iso(cdate),
            "release_date": _to_iso(cells[0]) if cells else "",
            "units": _units_for(level),
        })
    return history


def _parse_results(html: str, get_details: bool) -> List[Dict]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for row in soup.select("tr.table_award_row"):
        data = _parse_main(row)
        if not data:
            continue
        if get_details:
            rid = (row.get("id") or "").replace("default_", "")
            data["history"] = _parse_details(soup, rid, data)
        out.append(data)
    logger.info(f"RIAA : {len(out)} certification(s) extraite(s)")
    return out
