"""Interface avec l'API AcousticBrainz"""
import time
import requests
from typing import Optional, Dict, Any, List
import musicbrainzngs

from src.config import DELAY_BETWEEN_REQUESTS
from src.models import Track
from src.utils.logger import get_logger, log_api


logger = get_logger(__name__)


class AcousticBrainzAPI:
    """Gère les interactions avec l'API AcousticBrainz"""
    
    def __init__(self):
        self.base_url = "https://acousticbrainz.org"
        
        # Configurer MusicBrainz pour les recherches
        musicbrainzngs.set_useragent(
            "MusicCreditsScraper",
            "1.0",
            "https://github.com/user/music-credits-scraper"
        )
        
        logger.info("API AcousticBrainz initialisée")
    
    def search_track(self, track_title: str, artist_name: str) -> Optional[Dict[str, Any]]:
        """Recherche un morceau sur AcousticBrainz via MusicBrainz"""
        try:
            # Étape 1: Rechercher sur MusicBrainz pour obtenir le MBID
            mbid = self._get_musicbrainz_id(track_title, artist_name)
            
            if not mbid:
                return None
            
            # Étape 2: Récupérer les données acoustiques depuis AcousticBrainz
            acoustic_data = self._get_acoustic_data(mbid)
            
            if acoustic_data:
                log_api("AcousticBrainz", f"search/{track_title}", True)
                return acoustic_data
            else:
                log_api("AcousticBrainz", f"search/{track_title}", False)
                return None
                
        except Exception as e:
            logger.error(f"Erreur lors de la recherche AcousticBrainz: {e}")
            log_api("AcousticBrainz", f"search/{track_title}", False)
            return None
    
    def _get_musicbrainz_id(self, track_title: str, artist_name: str) -> Optional[str]:
        """Récupère l'ID MusicBrainz d'un morceau"""
        try:
            # Rechercher l'enregistrement sur MusicBrainz
            query = f'recording:"{track_title}" AND artist:"{artist_name}"'
            
            result = musicbrainzngs.search_recordings(
                query=query,
                limit=10,
                offset=0
            )
            
            recordings = result.get('recording-list', [])
            
            for recording in recordings:
                # Vérifier la correspondance de l'artiste
                artist_credits = recording.get('artist-credit', [])
                for credit in artist_credits:
                    if isinstance(credit, dict):
                        credit_name = credit.get('artist', {}).get('name', '')
                        if artist_name.lower() in credit_name.lower() or credit_name.lower() in artist_name.lower():
                            return recording.get('id')
            
            # Si pas de correspondance exacte, prendre le premier
            if recordings:
                return recordings[0].get('id')
            
            return None
            
        except Exception as e:
            logger.debug(f"Erreur recherche MusicBrainz: {e}")
            return None
    
    def _get_acoustic_data(self, mbid: str) -> Optional[Dict[str, Any]]:
        """Récupère les données acoustiques depuis AcousticBrainz"""
        try:
            # URL de l'API AcousticBrainz
            url = f"{self.base_url}/{mbid}/low-level"
            
            headers = {
                'User-Agent': 'MusicCreditsScraper/1.0',
                'Accept': 'application/json'
            }
            
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code == 404:
                logger.debug(f"Pas de données acoustiques pour MBID: {mbid}")
                return None
            
            response.raise_for_status()
            data = response.json()
            
            return self._extract_acoustic_features(data)
            
        except Exception as e:
            logger.debug(f"Erreur récupération données acoustiques: {e}")
            return None
    
    def _extract_acoustic_features(self, acoustic_data: Dict) -> Dict[str, Any]:
        """Extrait les caractéristiques acoustiques pertinentes"""
        try:
            # Naviguer dans la structure des données AcousticBrainz
            rhythm = acoustic_data.get('rhythm', {})
            tonal = acoustic_data.get('tonal', {})
            
            data = {
                'source': 'acousticbrainz'
            }
            
            # Extraire le BPM
            bpm = rhythm.get('bpm')
            if bpm:
                try:
                    data['bpm'] = int(float(bpm))
                except (ValueError, TypeError):
                    pass
            
            # Extraire la clé musicale
            key_key = tonal.get('key_key')
            key_scale = tonal.get('key_scale')
            if key_key and key_scale:
                data['musical_key'] = f"{key_key} {key_scale}"
            
            # Extraire d'autres métadonnées utiles
            if 'lowlevel' in acoustic_data:
                lowlevel = acoustic_data['lowlevel']
                
                # Durée
                duration = lowlevel.get('duration', {}).get('value')
                if duration:
                    data['duration'] = int(float(duration))
                
                # Caractéristiques spectrales (pour info)
                spectral_centroid = lowlevel.get('spectral_centroid', {}).get('mean')
                if spectral_centroid:
                    data['spectral_brightness'] = float(spectral_centroid)
            
            # Confiance des données
            if 'rhythm' in acoustic_data and 'bpm_confidence' in rhythm:
                data['bpm_confidence'] = float(rhythm['bpm_confidence'])
            
            return data
            
        except Exception as e:
            logger.error(f"Erreur extraction caractéristiques acoustiques: {e}")
            return {'source': 'acousticbrainz'}
    
    def enrich_track_data(self, track: Track) -> bool:
        """Enrichit les données d'un morceau avec les infos AcousticBrainz"""
        try:
            # Rechercher le morceau
            acoustic_data = self.search_track(track.title, track.artist.name)
            
            if not acoustic_data:
                return False
            
            # Mettre à jour les données du track (uniquement si manquantes)
            if not track.bpm and acoustic_data.get('bpm'):
                # Vérifier la confiance si disponible
                confidence = acoustic_data.get('bpm_confidence', 1.0)
                if confidence > 0.7:  # Seuil de confiance minimum
                    track.bpm = acoustic_data['bpm']
                    logger.info(f"BPM ajouté depuis AcousticBrainz: {track.bpm} (confiance: {confidence:.2f}) pour {track.title}")
                else:
                    logger.debug(f"BPM AcousticBrainz ignoré (confiance faible: {confidence:.2f})")
            
            # Ajouter la clé musicale si manquante
            if not hasattr(track, 'musical_key') and acoustic_data.get('musical_key'):
                track.musical_key = acoustic_data['musical_key']
                logger.debug(f"Clé musicale ajoutée: {track.musical_key}")
            
            # Ajouter la durée si manquante
            if not track.duration and acoustic_data.get('duration'):
                track.duration = acoustic_data['duration']
                logger.debug(f"Durée ajoutée: {track.duration}s")
            
            # Respecter le rate limit (plus généreux pour AcousticBrainz)
            time.sleep(max(DELAY_BETWEEN_REQUESTS, 0.5))
            
            return True
            
        except Exception as e:
            logger.error(f"Erreur lors de l'enrichissement AcousticBrainz: {e}")
            return False
    
    def test_connection(self) -> bool:
        """Teste la connexion à l'API"""
        try:
            # Tester avec un MBID connu
            test_url = f"{self.base_url}/7e4ac5e9-7d33-4e8c-9309-c82e7b0b2a1d/low-level"
            response = requests.get(test_url, timeout=5)
            return response.status_code in [200, 404]  # 404 est OK, signifie que l'API répond
        except Exception as e:
            logger.error(f"Erreur de connexion à AcousticBrainz: {e}")
            return False