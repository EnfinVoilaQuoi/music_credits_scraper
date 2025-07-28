"""Interface avec l'API Genius"""
import time
from typing import List, Optional, Dict, Any
from lyricsgenius import Genius

from src.config import GENIUS_API_KEY, DELAY_BETWEEN_REQUESTS, MAX_RETRIES
from src.models import Artist, Track
from src.utils.logger import get_logger, log_api


logger = get_logger(__name__)


class GeniusAPI:
    """Gère les interactions avec l'API Genius"""
    
    def __init__(self):
        if not GENIUS_API_KEY:
            raise ValueError("GENIUS_API_KEY non configurée")
        
        self.genius = Genius(GENIUS_API_KEY)
        self.genius.verbose = False  # Désactiver les prints de lyricsgenius
        self.genius.remove_section_headers = True
        self.genius.skip_non_songs = True
        self.genius.excluded_terms = ["(Remix)", "(Live)"]  # Optionnel
        
        logger.info("API Genius initialisée")
    
    def search_artist(self, artist_name: str) -> Optional[Artist]:
        """Recherche un artiste sur Genius"""
        try:
            logger.info(f"Recherche de l'artiste: {artist_name}")
            genius_artist = self.genius.search_artist(
                artist_name, 
                max_songs=0,  # On ne veut que l'info de l'artiste
                get_full_info=False
            )
            
            if genius_artist:
                artist = Artist(
                    name=genius_artist.name,
                    genius_id=genius_artist.id
                )
                log_api("Genius", f"artist/{genius_artist.id}", True)
                logger.info(f"Artiste trouvé: {artist.name} (ID: {artist.genius_id})")
                return artist
            else:
                logger.warning(f"Artiste non trouvé: {artist_name}")
                return None
                
        except Exception as e:
            logger.error(f"Erreur lors de la recherche d'artiste: {e}")
            log_api("Genius", f"search/artist/{artist_name}", False)
            return None
    
    def get_artist_songs(self, artist: Artist, max_songs: int = 200) -> List[Track]:
        """Récupère la liste des morceaux d'un artiste"""
        tracks = []
        
        try:
            logger.info(f"Récupération des morceaux de {artist.name}")
            
            # Rechercher l'artiste avec ses morceaux
            genius_artist = self.genius.search_artist(
                artist.name,
                max_songs=max_songs,
                sort="popularity",
                get_full_info=False
            )
            
            if not genius_artist:
                logger.error(f"Impossible de récupérer les morceaux de {artist.name}")
                return tracks
            
            # Mettre à jour l'ID Genius si nécessaire
            if not artist.genius_id:
                artist.genius_id = genius_artist.id
            
            # Parcourir les morceaux
            for genius_song in genius_artist.songs:
                try:
                    # Vérifier que c'est bien une chanson de l'artiste principal
                    if genius_song.artist.lower() != artist.name.lower():
                        continue
                    
                    track = Track(
                        title=genius_song.title,
                        artist=artist,
                        genius_id=genius_song.id,
                        genius_url=genius_song.url,
                        album=genius_song.album,
                        release_date=genius_song.release_date_for_display
                    )
                    
                    tracks.append(track)
                    logger.debug(f"Morceau ajouté: {track.title}")
                    
                    # Respecter le rate limit
                    time.sleep(DELAY_BETWEEN_REQUESTS)
                    
                except Exception as e:
                    logger.error(f"Erreur sur le morceau {genius_song.title}: {e}")
                    continue
            
            logger.info(f"{len(tracks)} morceaux récupérés pour {artist.name}")
            log_api("Genius", f"artist/{artist.genius_id}/songs", True)
            return tracks
            
        except Exception as e:
            logger.error(f"Erreur lors de la récupération des morceaux: {e}")
            log_api("Genius", f"artist/songs", False)
            return tracks
    
    def get_song_details(self, track: Track) -> Dict[str, Any]:
        """Récupère les détails d'un morceau (pour le scraping)"""
        if not track.genius_id:
            logger.warning(f"Pas d'ID Genius pour {track.title}")
            return {}
        
        try:
            logger.debug(f"Récupération des détails de {track.title}")
            
            # Utiliser l'API pour récupérer les infos de base
            song = self.genius.song(track.genius_id)
            
            if song:
                details = {
                    'id': song['song']['id'],
                    'title': song['song']['title'],
                    'url': song['song']['url'],
                    'album': song['song']['album']['name'] if song['song'].get('album') else None,
                    'release_date': song['song'].get('release_date_for_display'),
                    'producers': [],
                    'writers': [],
                    'features': []
                }
                
                # Extraire les producteurs et auteurs des relations
                for relation in song['song'].get('producer_artists', []):
                    details['producers'].append(relation['name'])
                
                for relation in song['song'].get('writer_artists', []):
                    details['writers'].append(relation['name'])
                
                # Les features sont dans le titre généralement
                if 'feat.' in track.title or 'ft.' in track.title:
                    # Le scraping sera plus précis pour ça
                    pass
                
                log_api("Genius", f"songs/{track.genius_id}", True)
                return details
            
        except Exception as e:
            logger.error(f"Erreur lors de la récupération des détails: {e}")
            log_api("Genius", f"songs/{track.genius_id}", False)
        
        return {}
    
    def search_song(self, title: str, artist_name: str) -> Optional[Dict[str, Any]]:
        """Recherche un morceau spécifique"""
        try:
            query = f"{title} {artist_name}"
            logger.debug(f"Recherche du morceau: {query}")
            
            search_results = self.genius.search_songs(query)
            
            if search_results and 'hits' in search_results:
                for hit in search_results['hits']:
                    result = hit['result']
                    # Vérifier que c'est le bon artiste
                    if artist_name.lower() in result['primary_artist']['name'].lower():
                        return {
                            'genius_id': result['id'],
                            'title': result['title'],
                            'url': result['url'],
                            'artist': result['primary_artist']['name']
                        }
            
            logger.warning(f"Morceau non trouvé: {query}")
            return None
            
        except Exception as e:
            logger.error(f"Erreur lors de la recherche de morceau: {e}")
            return None
    
    def test_connection(self) -> bool:
        """Teste la connexion à l'API"""
        try:
            # Faire une recherche simple
            result = self.genius.search_songs("test", per_page=1)
            return result is not None
        except Exception as e:
            logger.error(f"Erreur de connexion à Genius: {e}")
            return False