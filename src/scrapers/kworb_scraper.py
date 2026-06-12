"""
Scraper Kworb — Streams Spotify par morceau et par album
Pages statiques HTML, pas besoin de Playwright.
"""
import re
import logging
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup

from src.utils.llm_extractor import get_shared_extractor, build_streams_table_prompt

logger = logging.getLogger('KworbScraper')

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}
_TIMEOUT = 20  # secondes


class KworbScraper:
    """Récupère les données de streaming Spotify depuis kworb.net"""

    BASE_URL = "https://kworb.net/spotify/artist/{artist_id}_{type}.html"

    def scrape_songs(self, spotify_artist_id: str) -> List[Dict]:
        """Retourne la liste des morceaux avec leurs streams Spotify.

        Returns:
            [{'title': str, 'streams': int, 'daily_streams': int}]
        """
        url = self.BASE_URL.format(artist_id=spotify_artist_id, type="songs")
        return self._fetch_and_parse(url)

    def scrape_albums(self, spotify_artist_id: str) -> List[Dict]:
        """Retourne la liste des albums avec leurs streams Spotify.

        Returns:
            [{'title': str, 'streams': int, 'daily_streams': int}]
        """
        url = self.BASE_URL.format(artist_id=spotify_artist_id, type="albums")
        return self._fetch_and_parse(url)

    def _fetch_and_parse(self, url: str) -> List[Dict]:
        """Télécharge la page et parse la table HTML à 3 colonnes."""
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
            if resp.status_code == 404:
                logger.warning(f"Page Kworb introuvable (404): {url}")
                return []
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"Erreur HTTP Kworb ({url}): {e}")
            return []

        try:
            soup = BeautifulSoup(resp.text, "html.parser")
            table = soup.find("table")
            if not table:
                logger.warning(f"Aucune table trouvée sur: {url}")
                return []

            # Labels du tableau récapitulatif kworb (pas des morceaux)
            junk_titles = {"streams", "daily", "tracks", "total",
                           "as lead", "solo", "as feature"}

            results = []
            for row in table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) < 3:
                    continue
                title = cells[0].get_text(strip=True)
                if title.lower() in junk_titles:
                    continue
                streams = self._parse_number(cells[1].get_text(strip=True))
                daily = self._parse_number(cells[2].get_text(strip=True))
                if title and streams is not None:
                    results.append({
                        "title": title,
                        "streams": streams,
                        "daily_streams": daily or 0,
                    })

            if not results:
                # Fallback LLM si la structure de la page a changé
                results = self._extract_with_llm(soup.get_text(separator="\n", strip=True))

            logger.info(f"Kworb: {len(results)} entrées récupérées depuis {url}")
            return results

        except Exception as e:
            logger.error(f"Erreur parsing Kworb ({url}): {e}")
            return []

    def _extract_with_llm(self, page_text: str) -> List[Dict]:
        """
        Fallback LLM : extrait les lignes titre/streams du texte de la page.
        Anti-hallucination : chaque nombre doit exister tel quel dans la page.
        """
        llm = get_shared_extractor()
        if not llm or not page_text:
            return []

        logger.info("🤖 Kworb: parsing HTML sans résultat, fallback LLM")
        data = llm.extract_json(build_streams_table_prompt(page_text[:5500]))
        if not data or not isinstance(data.get("tracks"), list):
            return []

        # Ensemble des nombres réellement présents dans la page (sans séparateurs)
        page_numbers = set(re.findall(r"\d[\d,.\s]*\d|\d", page_text))
        page_numbers = {re.sub(r"[^\d]", "", n) for n in page_numbers}

        results = []
        for entry in data["tracks"]:
            if not isinstance(entry, dict):
                continue
            title = str(entry.get("title", "")).strip()
            streams = entry.get("streams")
            daily = entry.get("daily")
            if not title or not isinstance(streams, int):
                continue
            # Validation stricte : le nombre doit exister dans la page
            if str(streams) not in page_numbers:
                logger.debug(f"Kworb LLM: streams {streams} absent de la page — rejeté")
                continue
            if not (isinstance(daily, int) and str(daily) in page_numbers):
                daily = 0
            results.append({"title": title, "streams": streams, "daily_streams": daily})

        logger.info(f"🤖 Kworb LLM: {len(results)} entrée(s) validée(s)")
        return results

    @staticmethod
    def _parse_number(text: str) -> Optional[int]:
        """Convertit '20,706,079' en 20706079. Retourne None si non parseable."""
        cleaned = re.sub(r"[^\d]", "", text)
        return int(cleaned) if cleaned else None
