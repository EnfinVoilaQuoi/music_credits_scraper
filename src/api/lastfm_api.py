"""Interface avec l'API Last.fm"""
import time
from typing import Optional, Dict, Any, List
import pylast

from src.config import LAST_FM_API_KEY, DELAY_BETWEEN_REQUESTS
from src.models import Track, Artist
from src.utils.logger import get_logger, log_api


logger = get_logger(__name__)


class LastFmAPI:
    """Gère les interactions avec l'API Last.fm"""
    
    def __init__(self):
        if not LAST_FM_API_KEY:
            raise ValueError("LAST_FM_API_KEY non configurée")
        
        self.network = pylast.LastFMNetwork(
            api_key=LAST_FM_API_KEY,
            api_secret=None,  # Pas nécessaire pour les lectures seules
        )
        
        logger.info("API Last.fm initialisée")
    
    def search_track(self, track_title: str, artist_name: str) -> Optional[Dict[str, Any]]:
        """Recherche un morceau sur Last.fm"""
        try:
            # Récupérer le track
            track = self.network.get_track(artist_name, track_title)
            
            if track:
                track_data = {
                    'title': track.get_title(),
                    'artist': track.get_artist().get_name(),
                    'url': track.get_url(),
                    'duration': None,
                    'playcount': None,
                    'tags': []
                }
                
                # Récupérer la durée
                try:
                    duration = track.get_duration()
                    if duration:
                        track_data['duration'] = duration // 1000  # Convertir en secondes
                except:
                    pass
                
                # Récupérer le playcount
                try:
                    playcount = track.get_playcount()
                    if playcount:
                        track_data['playcount'] = int(playcount)
                except:
                    pass
                
                # Récupérer les tags
                try:
                    tags = track.get_top_tags(limit=5)
                    track_data['tags'] = [tag.item.get_name() for tag in tags]
                except:
                    pass
                
                log_api("Last.fm", f"track/{artist_name}/{track_title}", True)
                return track_data
            
            logger.warning(f"Morceau non trouvé sur Last.fm: {track_title} - {artist_name}")
            return None
            
        except pylast.WSError as e:
            if "Track not found" in str(e):
                logger.debug(f"Track non trouvé: {track_title} - {artist_name}")
            else:
                logger.error(f"Erreur Last.fm: {e}")
            log_api("Last.fm", f"track/{artist_name}/{track_title}", False)
            return None
        except Exception as e:
            logger.error(f"Erreur lors de la recherche Last.fm: {e}")
            return None
    
    def get_album_info(self, artist_name: str, album_name: str) -> Optional[Dict[str, Any]]:
        """Récupère les informations d'un album"""
        try:
            album = self.network.get_album(artist_name, album_name)
            
            if album:
                album_data = {
                    'title': album.get_title(),
                    'artist': album.get_artist().get_name(),
                    'url': album.get_url(),
                    'playcount': None,
                    'tags': []
                }
                
                # Récupérer le playcount
                try:
                    playcount = album.get_playcount()
                    if playcount:
                        album_data['playcount'] = int(playcount)
                except:
                    pass
                
                # Récupérer les tags
                try:
                    tags = album.get_top_tags(limit=5)
                    album_data['tags'] = [tag.item.get_name() for tag in tags]
                except:
                    pass
                
                log_api("Last.fm", f"album/{artist_name}/{album_name}", True)
                return album_data
            
            return None
            
        except Exception as e:
            logger.error(f"Erreur lors de la récupération de l'album: {e}")
            return None
    
    def enrich_track_data(self, track: Track) -> bool:
        """Enrichit les données d'un morceau avec les infos Last.fm"""
        try:
            # Rechercher le morceau
            lastfm_data = self.search_track(track.title, track.artist.name)
            
            if not lastfm_data:
                return False
            
            # Ajouter la durée si manquante
            if not track.duration and lastfm_data.get('duration'):
                track.duration = lastfm_data['duration']
            
            # Ajouter les genres depuis les tags si manquant
            if not track.genre and lastfm_data.get('tags'):
                # Filtrer les tags pour garder ceux qui ressemblent à des genres
                genre_tags = [tag for tag in lastfm_data['tags'] 
                             if not any(skip in tag.lower() 
                                       for skip in ['seen live', 'favorite', 'love', 'awesome'])]
                if genre_tags:
                    track.genre = ', '.join(genre_tags[:2])
            
            # Si on a l'album, essayer de récupérer plus d'infos
            if track.album and not track.release_date:
                album_data = self.get_album_info(track.artist.name, track.album)
                # Last.fm n'a pas toujours les dates de sortie dans l'API gratuite
            
            # Respecter le rate limit
            time.sleep(DELAY_BETWEEN_REQUESTS)
            
            logger.info(f"Données Last.fm ajoutées pour {track.title}")
            return True
            
        except Exception as e:
            logger.error(f"Erreur lors de l'enrichissement Last.fm: {e}")
            return False
    
    def search_artist(self, artist_name: str) -> Optional[Dict[str, Any]]:
        """Recherche un artiste sur Last.fm"""
        try:
            artist = self.network.get_artist(artist_name)
            
            if artist:
                artist_data = {
                    'name': artist.get_name(),
                    'url': artist.get_url(),
                    'playcount': None,
                    'listeners': None,
                    'bio': None,
                    'tags': [],
                    'similar': []
                }
                
                # Récupérer les stats
                try:
                    playcount = artist.get_playcount()
                    if playcount:
                        artist_data['playcount'] = int(playcount)
                except:
                    pass
                
                try:
                    listeners = artist.get_listener_count()
                    if listeners:
                        artist_data['listeners'] = int(listeners)
                except:
                    pass
                
                # Récupérer la bio
                try:
                    bio = artist.get_bio_summary()
                    if bio:
                        artist_data['bio'] = bio
                except:
                    pass
                
                # Récupérer les tags
                try:
                    tags = artist.get_top_tags(limit=5)
                    artist_data['tags'] = [tag.item.get_name() for tag in tags]
                except:
                    pass
                
                # Récupérer les artistes similaires
                try:
                    similar = artist.get_similar(limit=5)
                    artist_data['similar'] = [s.item.get_name() for s in similar]
                except:
                    pass
                
                log_api("Last.fm", f"artist/{artist_name}", True)
                return artist_data
            
            return None
            
        except Exception as e:
            logger.error(f"Erreur lors de la recherche d'artiste Last.fm: {e}")
            log_api("Last.fm", f"artist/{artist_name}", False)
            return None
    
    def enrich_multiple_tracks(self, tracks: List[Track], progress_callback=None) -> Dict[str, int]:
        """Enrichit plusieurs morceaux avec les données Last.fm"""
        results = {
            'enriched': 0,
            'failed': 0
        }
        
        total = len(tracks)
        
        for i, track in enumerate(tracks):
            # Last.fm est utilisé principalement pour les genres et durées manquantes
            if not track.genre or not track.duration:
                if self.enrich_track_data(track):
                    results['enriched'] += 1
                else:
                    results['failed'] += 1
            
            if progress_callback:
                progress_callback(i + 1, total, f"Last.fm: {track.title}")
        
        logger.info(f"Enrichissement Last.fm terminé: {results['enriched']} réussis, {results['failed']} échoués")
        return results
    
    def test_connection(self) -> bool:
        """Teste la connexion à l'API"""
        try:
            # Faire une recherche simple
            artist = self.network.get_artist("The Beatles")
            artist.get_name()
            return True
        except Exception as e:
            logger.error(f"Erreur de connexion à Last.fm: {e}")
            return False
