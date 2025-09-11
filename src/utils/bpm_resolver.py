"""
Script utilitaire pour obtenir le BPM d'un morceau
en utilisant GetSongBPM, puis AcousticBrainz, puis SongBPM (scraping).
"""

from typing import Optional, Dict
from src.models import Track, Artist
from src.api.getsongbpm_api import GetSongBPMAPI
from src.api.acousticbrainz_api import AcousticBrainzAPI

import requests
from bs4 import BeautifulSoup


class BPMResolver:
    def __init__(self, getsongbpm_api_key: str = None):
        self.getsongbpm = GetSongBPMAPI(api_key=getsongbpm_api_key)
        self.acousticbrainz = AcousticBrainzAPI()

    def get_bpm(self, title: str, artist: str) -> Optional[int]:
        """
        Tente d'obtenir le BPM d'un morceau via différentes sources.
        """
        track = Track(title=title, artist=Artist(name=artist))

        # 1. Essayer GetSongBPM
        if self.getsongbpm.enrich_track_data(track) and track.bpm:
            return track.bpm

        # 2. Essayer AcousticBrainz
        if self.acousticbrainz.enrich_track_data(track) and track.bpm:
            return track.bpm

        # 3. Essayer le scraping de songbpm.com
        bpm = self._scrape_songbpm(title, artist)
        if bpm:
            return bpm

        return None

    def _scrape_songbpm(self, title: str, artist: str) -> Optional[int]:
        """
        Scraping de https://songbpm.com/ pour obtenir le BPM.
        """
        try:
            search_url = f"https://songbpm.com/{artist.replace(' ', '-')}/{title.replace(' ', '-')}"
            headers = {"User-Agent": "Mozilla/5.0"}
            resp = requests.get(search_url, headers=headers, timeout=10)

            if resp.status_code != 200:
                return None

            soup = BeautifulSoup(resp.text, "html.parser")

            # SongBPM affiche le BPM dans une balise contenant "BPM"
            bpm_tag = soup.find(string=lambda s: s and "BPM" in s)
            if bpm_tag:
                bpm_str = "".join([c for c in bpm_tag if c.isdigit()])
                return int(bpm_str) if bpm_str else None

        except Exception as e:
            print(f"Erreur scraping SongBPM: {e}")
            return None


if __name__ == "__main__":
    resolver = BPMResolver(getsongbpm_api_key="TON_API_KEY_ICI")
    bpm = resolver.get_bpm("1 peu trop court", "IAM")
    print(f"BPM trouvé: {bpm}")
