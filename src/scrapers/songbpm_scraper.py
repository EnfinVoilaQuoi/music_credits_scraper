"""Scraper pour récupérer le BPM depuis songbpm.com"""
import time
import requests
from bs4 import BeautifulSoup
from typing import Optional, Dict, Any
from urllib.parse import quote_plus

from src.models import Track
from src.utils.logger import get_logger, log_api
from src.config import DELAY_BETWEEN_REQUESTS

logger = get_logger(__name__)


class SongBPMScraper:
    """Scrape songbpm.com pour obtenir le BPM"""

    def __init__(self):
        self.base_url = "https://songbpm.com"

    def search_track(self, track_title: str, artist_name: str) -> Optional[Dict[str, Any]]:
        """Scrape la page du morceau pour extraire le BPM"""
        try:
            # Construire l’URL : https://songbpm.com/@artist/slug-title
            artist_slug = artist_name.lower().replace(" ", "-")
            title_slug = track_title.lower().replace(" ", "-")
            url = f"{self.base_url}/@{artist_slug}/{quote_plus(title_slug)}"

            headers = {"User-Agent": "MusicCreditsScraper/1.0"}
            logger.debug(f"Scraping SongBPM URL: {url}")
            response = requests.get(url, headers=headers, timeout=10)

            if response.status_code != 200:
                logger.warning(f"SongBPM: Page non trouvée ({response.status_code})")
                log_api("SongBPM", f"search/{track_title}", False)
                return None

            soup = BeautifulSoup(response.text, "html.parser")

            # Chercher un texte contenant "BPM"
            bpm_tag = soup.find(string=lambda s: s and "BPM" in s)
            if bpm_tag:
                bpm_str = "".join([c for c in bpm_tag if c.isdigit()])
                if bpm_str:
                    bpm_val = int(bpm_str)
                    data = {
                        "title": track_title,
                        "artist": artist_name,
                        "bpm": bpm_val,
                        "source": "songbpm_scraper",
                    }
                    log_api("SongBPM", f"search/{track_title}", True)
                    return data

            log_api("SongBPM", f"search/{track_title}", False)
            return None

        except Exception as e:
            logger.error(f"Erreur scraping SongBPM: {e}")
            log_api("SongBPM", f"search/{track_title}", False)
            return None

    def enrich_track_data(self, track: Track) -> bool:
        """Enrichit un track avec le BPM depuis SongBPM"""
        try:
            track_data = self.search_track(track.title, track.artist.name)
            if not track_data:
                return False

            if not track.bpm and track_data.get("bpm"):
                track.bpm = track_data["bpm"]
                logger.info(f"BPM ajouté depuis SongBPM: {track.bpm} pour {track.title}")

            time.sleep(DELAY_BETWEEN_REQUESTS)
            return True

        except Exception as e:
            logger.error(f"Erreur enrichissement SongBPM: {e}")
            return False
