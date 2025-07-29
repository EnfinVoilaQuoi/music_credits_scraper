"""Interface avec l'API Spotify"""
import time
from typing import Optional, Dict, Any, List
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

from src.config import SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, DELAY_BETWEEN_REQUESTS
from src.models import Track, Artist
from src.utils.logger import get_logger, log_api


logger = get_logger(__name__)


class SpotifyAPI:
    """Gère les interactions avec l'API Spotify"""
    
    def __init__(self):
        if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
            raise ValueError("SPOTIFY_CLIENT_ID et SPOTIFY_CLIENT_SECRET doivent être configurés")
        
        # Authentification
        auth_manager = SpotifyClientCredentials(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET
        )
        
        self.sp = spotipy.Spotify(auth_manager=auth_manager)
        logger.info("API Spotify initialisée")
    
    def search_track(self, track_title: str, artist_name: str) -> Optional[Dict[str, Any]]:
        """Recherche un morceau sur Spotify"""
        try:
            # Construire la requête
            query = f"track:{track_title} artist:{artist_name}"
            logger.debug(f"Recherche Spotify: {query}")
            
            # Rechercher
            results = self.sp.search(q=query, type='track', limit=10)
            
            if results['tracks']['items']:
                # Parcourir les résultats pour trouver la meilleure correspondance
                for item in results['tracks']['items']:
                    # Vérifier que c'est le bon artiste
                    artists = [a['name'].lower() for a in item['artists']]
                    if artist_name.lower() in ' '.join(artists):
                        track_data = self._extract_track_data(item)
                        log_api("Spotify", f"search/track/{track_title}", True)
                        return track_data
                
                # Si pas de correspondance exacte, prendre le premier résultat
                track_data = self._extract_track_data(results['tracks']['items'][0])
                log_api("Spotify", f"search/track/{track_title}", True)
                return track_data
            
            logger.warning(f"Morceau non trouvé sur Spotify: {track_title} - {artist_name}")
            return None
            
        except Exception as e:
            logger.error(f"Erreur lors de la recherche Spotify: {e}")
            log_api("Spotify", f"search/track/{track_title}", False)
            return None
    
    def get_track_audio_features(self, spotify_id: str) -> Optional[Dict[str, Any]]:
        """Récupère les caractéristiques audio d'un morceau (BPM, etc.)"""
        try:
            features = self.sp.audio_features(spotify_id)[0]
            
            if features:
                log_api("Spotify", f"audio-features/{spotify_id}", True)
                return {
                    'bpm': int(features['tempo']) if features['tempo'] else None,
                    'key': features['key'],
                    'mode': features['mode'],
                    'time_signature': features['time_signature'],
                    'duration_ms': features['duration_ms'],
                    'energy': features['energy'],
                    'danceability': features['danceability'],
                    'valence': features['valence']
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Erreur lors de la récupération des features: {e}")
            log_api("Spotify", f"audio-features/{spotify_id}", False)
            return None
    
    def enrich_track_data(self, track: Track) -> bool:
        """Enrichit les données d'un morceau avec les infos Spotify"""
        try:
            # Rechercher le morceau
            spotify_data = self.search_track(track.title, track.artist.name)
            
            if not spotify_data:
                return False
            
            # Mettre à jour les données du track
            track.spotify_id = spotify_data['id']
            track.spotify_url = spotify_data['url']
            
            # Si pas d'album, prendre celui de Spotify
            if not track.album and spotify_data.get('album'):
                track.album = spotify_data['album']
            
            # Récupérer les features audio
            if spotify_data['id']:
                features = self.get_track_audio_features(spotify_data['id'])
                if features:
                    # Ne mettre à jour le BPM que s'il n'existe pas déjà
                    # (Rapedia est prioritaire)
                    if not track.bpm and features.get('bpm'):
                        track.bpm = features['bpm']
                        logger.info(f"BPM ajouté depuis Spotify: {track.bpm} pour {track.title}")
                    
                    # Durée
                    if not track.duration and features.get('duration_ms'):
                        track.duration = features['duration_ms'] // 1000  # Convertir en secondes
            
            # Respecter le rate limit
            time.sleep(DELAY_BETWEEN_REQUESTS)
            
            return True
            
        except Exception as e:
            logger.error(f"Erreur lors de l'enrichissement du track: {e}")
            return False
    
    def _extract_track_data(self, spotify_track: Dict) -> Dict[str, Any]:
        """Extrait les données pertinentes d'un track Spotify"""
        return {
            'id': spotify_track['id'],
            'title': spotify_track['name'],
            'artists': [a['name'] for a in spotify_track['artists']],
            'album': spotify_track['album']['name'] if spotify_track.get('album') else None,
            'release_date': spotify_track['album'].get('release_date') if spotify_track.get('album') else None,
            'url': spotify_track['external_urls']['spotify'],
            'popularity': spotify_track.get('popularity', 0)
        }
    
    def search_artist(self, artist_name: str) -> Optional[Dict[str, Any]]:
        """Recherche un artiste sur Spotify"""
        try:
            results = self.sp.search(q=artist_name, type='artist', limit=5)
            
            if results['artists']['items']:
                # Prendre l'artiste le plus populaire
                artist = max(results['artists']['items'], key=lambda x: x['popularity'])
                
                log_api("Spotify", f"search/artist/{artist_name}", True)
                return {
                    'id': artist['id'],
                    'name': artist['name'],
                    'genres': artist.get('genres', []),
                    'popularity': artist.get('popularity', 0),
                    'url': artist['external_urls']['spotify']
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Erreur lors de la recherche d'artiste: {e}")
            log_api("Spotify", f"search/artist/{artist_name}", False)
            return None
    
    def enrich_multiple_tracks(self, tracks: List[Track], progress_callback=None) -> Dict[str, int]:
        """Enrichit plusieurs morceaux avec les données Spotify"""
        results = {
            'enriched': 0,
            'failed': 0
        }
        
        total = len(tracks)
        
        for i, track in enumerate(tracks):
            # Ne traiter que si pas déjà de BPM (priorité à Rapedia)
            if not track.bpm:
                if self.enrich_track_data(track):
                    results['enriched'] += 1
                else:
                    results['failed'] += 1
            
            if progress_callback:
                progress_callback(i + 1, total, f"Spotify: {track.title}")
        
        logger.info(f"Enrichissement Spotify terminé: {results['enriched']} réussis, {results['failed']} échoués")
        return results
    
    def test_connection(self) -> bool:
        """Teste la connexion à l'API"""
        try:
            # Faire une recherche simple
            result = self.sp.search(q="test", type="track", limit=1)
            return result is not None
        except Exception as e:
            logger.error(f"Erreur de connexion à Spotify: {e}")
            return False
