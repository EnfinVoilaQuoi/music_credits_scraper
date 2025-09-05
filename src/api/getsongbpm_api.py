"""Interface avec l'API GetSongBPM"""
import time
import requests
from typing import Optional, Dict, Any
from urllib.parse import quote

from src.config import DELAY_BETWEEN_REQUESTS
from src.models import Track
from src.utils.logger import get_logger, log_api


logger = get_logger(__name__)


class GetSongBPMAPI:
    """Gère les interactions avec l'API GetSongBPM"""
    
    def __init__(self, api_key: str = None):
        # GetSongBPM API est gratuite mais nécessite parfois une clé
        self.api_key = api_key
        self.base_url = "https://api.getsongbpm.com"
        logger.info("API GetSongBPM initialisée")
    
    def search_track(self, track_title: str, artist_name: str) -> Optional[Dict[str, Any]]:
        """Recherche un morceau sur GetSongBPM"""
        try:
            # Nettoyer les caractères spéciaux
            clean_title = track_title.replace("!", "").replace("?", "").strip()
            clean_artist = artist_name.replace("!", "").replace("?", "").strip()
            
            # URL de l'API avec le bon format
            url = f"{self.base_url}/search/"
            
            params = {
                'type': 'song',
                'lookup': f"song:{clean_artist} {clean_title}",
                'api_key': self.api_key  # ✅ Toujours inclure la clé
            }
            
            # Vérifier que la clé API est présente
            if not self.api_key:
                logger.warning("GetSongBPM: Clé API manquante")
                return None
            
            logger.debug(f"Recherche GetSongBPM: {clean_artist} {clean_title}")
            
            headers = {
                'User-Agent': 'MusicCreditsScraper/1.0',
                'Accept': 'application/json'
            }
            
            response = requests.get(url, params=params, headers=headers, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            # Vérifier le format de réponse
            if data.get('search') and len(data['search']) > 0:
                # Prendre le premier résultat le plus pertinent
                best_match = None
                best_score = 0
                
                for result in data['search'][:5]:  # Examiner les 5 premiers
                    # Calculer un score de correspondance simple
                    result_artist = result.get('artist', {}).get('name', '').lower()
                    result_title = result.get('song', {}).get('title', '').lower()
                    
                    artist_match = artist_name.lower() in result_artist or result_artist in artist_name.lower()
                    title_match = track_title.lower() in result_title or result_title in track_title.lower()
                    
                    if artist_match and title_match:
                        score = 10
                    elif artist_match or title_match:
                        score = 5
                    else:
                        score = 1
                    
                    if score > best_score:
                        best_score = score
                        best_match = result
                
                if best_match and best_score >= 5:  # Seuil minimum de confiance
                    track_data = self._extract_track_data(best_match)
                    log_api("GetSongBPM", f"search/{query}", True)
                    return track_data
            
            logger.warning(f"Morceau non trouvé sur GetSongBPM: {clean_title} - {clean_artist}")
            log_api("GetSongBPM", f"search/{clean_title}", False)
            return None
            
        except Exception as e:
            logger.error(f"Erreur lors de la recherche GetSongBPM: {e}")
            log_api("GetSongBPM", f"search/{track_title}", False)
            return None
    
    def _extract_track_data(self, song_data: Dict) -> Dict[str, Any]:
        """Extrait les données pertinentes d'un résultat GetSongBPM"""
        try:
            song_info = song_data.get('song', {})
            artist_info = song_data.get('artist', {})
            
            # Extraire les données principales
            data = {
                'title': song_info.get('title', ''),
                'artist': artist_info.get('name', ''),
                'bpm': None,
                'key': song_info.get('key_of', ''),
                'time_signature': song_info.get('time_sig', ''),
                'danceability': song_info.get('danceability', 0),
                'source': 'getsongbpm'
            }
            
            # Traiter le BPM
            bpm_raw = song_info.get('bpm')
            if bpm_raw:
                try:
                    data['bpm'] = int(float(bpm_raw))
                except (ValueError, TypeError):
                    pass
            
            return data
            
        except Exception as e:
            logger.error(f"Erreur lors de l'extraction des données GetSongBPM: {e}")
            return {}
    
    def enrich_track_data(self, track: Track) -> bool:
        """Enrichit les données d'un morceau avec les infos GetSongBPM"""
        try:
            # Rechercher le morceau
            track_data = self.search_track(track.title, track.artist.name)
            
            if not track_data:
                return False
            
            # Mettre à jour les données du track (uniquement si manquantes)
            if not track.bpm and track_data.get('bpm'):
                track.bpm = track_data['bpm']
                logger.info(f"BPM ajouté depuis GetSongBPM: {track.bpm} pour {track.title}")
            
            # Ajouter la clé musicale si manquante
            if not hasattr(track, 'musical_key') and track_data.get('key'):
                track.musical_key = track_data['key']
                logger.debug(f"Clé musicale ajoutée: {track.musical_key}")
            
            # Respecter le rate limit
            time.sleep(DELAY_BETWEEN_REQUESTS)
            
            return True
            
        except Exception as e:
            logger.error(f"Erreur lors de l'enrichissement GetSongBPM: {e}")
            return False
    
    def test_connection(self) -> bool:
        """Teste la connexion à l'API"""
        try:
            # Faire une recherche simple
            result = self.search_track("test", "test")
            return True  # Si pas d'exception, l'API répond
        except Exception as e:
            logger.error(f"Erreur de connexion à GetSongBPM: {e}")
            return False