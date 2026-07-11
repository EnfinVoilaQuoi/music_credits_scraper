"""
Scraper Kworb v2 — Streams Spotify par morceau et par album
Pages 100 % statiques, sans Cloudflare : requests + BS4 suffisent.

Structure des pages artiste (kworb.net/spotify/artist/{id}_{songs|albums}.html) :
  - <title> : "{Nom artiste} - Spotify Top Songs|Albums"  → validation d'identité
  - "Last updated: YYYY/MM/DD"                            → fraîcheur par artiste
  - Table 1 (page songs seulement, sans classe) : récap Total/As lead/Solo/As feature
  - Table 2 (class="addpos sortable") : lignes <td class="text"><div>[* ]<a href=
    "https://open.spotify.com/track/{ID}">Titre</a></div></td><td>streams</td><td>daily</td>
    · "*" avant le lien = artiste en featuring (co-primaires comptés lead, sans *)
    · href → Spotify track/album ID (matching exact possible)
    · daily parfois vide
"""

import re
import logging
from datetime import datetime
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup

from src.utils.llm_extractor import get_shared_extractor, build_streams_table_prompt

logger = logging.getLogger("KworbScraper")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}
_TIMEOUT = 20  # secondes

_SPOTIFY_URL_RE = re.compile(r"open\.spotify\.com/(track|album)/([A-Za-z0-9]+)")


class KworbScraper:
    """Récupère les données de streaming Spotify depuis kworb.net"""

    BASE_URL = "https://kworb.net/spotify/artist/{artist_id}_{type}.html"

    def scrape_songs(self, spotify_artist_id: str) -> Optional[Dict]:
        """Page songs d'un artiste.

        Returns:
            {
              'artist_name': str,          # depuis <title> — À VALIDER par l'appelant
              'last_updated': datetime|None,
              'summary': {'streams': {...}, 'daily': {...}, 'tracks': {...}}|None,
              'entries': [{'title', 'streams', 'daily_streams',
                           'spotify_id', 'is_feature'}]
            }
            ou None si la page n'existe pas (404) / erreur réseau.
        """
        url = self.BASE_URL.format(artist_id=spotify_artist_id, type="songs")
        return self._fetch_and_parse(url)

    def scrape_albums(self, spotify_artist_id: str) -> Optional[Dict]:
        """Page albums d'un artiste. Même forme que scrape_songs (summary=None).

        Les 'spotify_id' sont des IDs d'ALBUM. Un même titre peut apparaître
        plusieurs fois (éditions distinctes) — l'appelant doit agréger.
        Les streams album = somme des morceaux de L'ARTISTE sur l'album
        (les albums où il n'est qu'invité apparaissent aussi).
        """
        url = self.BASE_URL.format(artist_id=spotify_artist_id, type="albums")
        return self._fetch_and_parse(url)

    # ── Fetch + parse ──────────────────────────────────────────────────────────

    def _fetch_and_parse(self, url: str) -> Optional[Dict]:
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
            if resp.status_code == 404:
                logger.warning(f"Page Kworb introuvable (404): {url}")
                return None
            resp.raise_for_status()
            # Kworb sert de l'UTF-8 sans header charset → requests retombe en
            # latin-1 et mojibake les titres accentués ("FlÃ»tes recyclables")
            resp.encoding = "utf-8"
        except requests.RequestException as e:
            logger.error(f"Erreur HTTP Kworb ({url}): {e}")
            return None

        try:
            soup = BeautifulSoup(resp.text, "html.parser")
            page = {
                "artist_name": self._parse_artist_name(soup),
                "last_updated": self._parse_last_updated(soup),
                "summary": self._parse_summary(soup),
                "entries": self._parse_entries(soup),
            }

            if not page["entries"]:
                # Fallback LLM si la structure de la page a changé
                page["entries"] = self._extract_with_llm(soup.get_text(separator="\n", strip=True))

            logger.info(
                f"Kworb: {len(page['entries'])} entrées pour "
                f"'{page['artist_name']}' (maj {page['last_updated']}) — {url}"
            )
            return page

        except Exception as e:
            logger.error(f"Erreur parsing Kworb ({url}): {e}")
            return None

    @staticmethod
    def _parse_artist_name(soup) -> Optional[str]:
        """'ISHA - Spotify Top Songs' → 'ISHA'."""
        title = soup.title.string if soup.title else None
        if not title:
            return None
        return re.split(r"\s+-\s+Spotify Top\b", title)[0].strip() or None

    @staticmethod
    def _parse_last_updated(soup) -> Optional[datetime]:
        m = re.search(r"Last updated:\s*(\d{4})/(\d{2})/(\d{2})", soup.get_text())
        if not m:
            return None
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None

    def _parse_summary(self, soup) -> Optional[Dict]:
        """Table récap (page songs) : lignes Streams/Daily/Tracks ×
        colonnes Total/As lead/Solo/As feature."""
        for table in soup.find_all("table"):
            if "addpos" in (table.get("class") or []):
                continue
            headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
            if "total" not in headers or "as lead" not in headers:
                continue
            summary = {}
            for row in table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) != 5:
                    continue
                label = cells[0].get_text(strip=True).lower()  # streams/daily/tracks
                values = [self._parse_number(c.get_text(strip=True)) for c in cells[1:]]
                summary[label] = {
                    "total": values[0],
                    "as_lead": values[1],
                    "solo": values[2],
                    "as_feature": values[3],
                }
            return summary or None
        return None

    def _parse_entries(self, soup) -> List[Dict]:
        """Table class='addpos' : une ligne par morceau/album."""
        table = soup.find("table", class_="addpos")
        if not table:
            return []

        results = []
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 3:
                continue
            link = cells[0].find("a")
            if not link:
                continue
            title = link.get_text(strip=True)
            # '*' avant le lien = featuring (le texte de la cellule commence par *)
            is_feature = cells[0].get_text(strip=True).startswith("*")

            spotify_id = None
            m = _SPOTIFY_URL_RE.search(link.get("href") or "")
            if m:
                spotify_id = m.group(2)

            streams = self._parse_number(cells[1].get_text(strip=True))
            daily = self._parse_number(cells[2].get_text(strip=True))
            if title and streams is not None:
                results.append(
                    {
                        "title": title,
                        "streams": streams,
                        "daily_streams": daily or 0,
                        "spotify_id": spotify_id,
                        "is_feature": is_feature,
                    }
                )
        return results

    # ── Fallback LLM (structure de page changée) ───────────────────────────────

    def _extract_with_llm(self, page_text: str) -> List[Dict]:
        """
        Fallback LLM : extrait les lignes titre/streams du texte de la page.
        Anti-hallucination : chaque nombre doit exister tel quel dans la page.
        Pas d'IDs Spotify ni de flag feature par cette voie (texte brut).
        """
        llm = get_shared_extractor()
        if not llm or not page_text:
            return []

        logger.info("🤖 Kworb: parsing HTML sans résultat, fallback LLM")
        data = llm.extract_json(build_streams_table_prompt(page_text[:5500]))
        if not data or not isinstance(data.get("tracks"), list):
            return []

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
            if str(streams) not in page_numbers:
                logger.debug(f"Kworb LLM: streams {streams} absent de la page — rejeté")
                continue
            if not (isinstance(daily, int) and str(daily) in page_numbers):
                daily = 0
            results.append(
                {
                    "title": title.lstrip("* ").strip(),
                    "streams": streams,
                    "daily_streams": daily,
                    "spotify_id": None,
                    "is_feature": title.startswith("*"),
                }
            )

        logger.info(f"🤖 Kworb LLM: {len(results)} entrée(s) validée(s)")
        return results

    @staticmethod
    def _parse_number(text: str) -> Optional[int]:
        """Convertit '20,706,079' en 20706079. Retourne None si non parseable."""
        cleaned = re.sub(r"[^\d]", "", text or "")
        return int(cleaned) if cleaned else None
