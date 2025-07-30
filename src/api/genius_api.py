"""Interface avec l'API Genius - Version avec support des features"""
import time
from typing import List, Optional, Dict, Any
from datetime import datetime
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
            
            # Utiliser la méthode search() qui fonctionne
            search_response = self.genius.search(artist_name)
            
            if search_response and 'hits' in search_response:
                # Chercher l'artiste dans les résultats
                for hit in search_response['hits']:
                    result = hit['result']
                    primary_artist = result.get('primary_artist', {})
                    
                    # Vérifier si c'est le bon artiste (comparaison insensible à la casse)
                    if primary_artist.get('name', '').lower() == artist_name.lower():
                        artist = Artist(
                            name=primary_artist['name'],
                            genius_id=primary_artist['id']
                        )
                        log_api("Genius", f"artist/{artist.genius_id}", True)
                        logger.info(f"Artiste trouvé: {artist.name} (ID: {artist.genius_id})")
                        return artist
                
                # Si pas de correspondance exacte, prendre le premier artiste qui contient le nom
                for hit in search_response['hits']:
                    result = hit['result']
                    primary_artist = result.get('primary_artist', {})
                    artist_name_lower = artist_name.lower()
                    
                    if (artist_name_lower in primary_artist.get('name', '').lower() or 
                        primary_artist.get('name', '').lower() in artist_name_lower):
                        artist = Artist(
                            name=primary_artist['name'],
                            genius_id=primary_artist['id']
                        )
                        log_api("Genius", f"artist/{artist.genius_id}", True)
                        logger.info(f"Artiste trouvé (correspondance partielle): {artist.name} (ID: {artist.genius_id})")
                        return artist
            
            logger.warning(f"Artiste non trouvé: {artist_name}")
            return None
                
        except Exception as e:
            logger.error(f"Erreur lors de la recherche d'artiste: {e}")
            log_api("Genius", f"search/artist/{artist_name}", False)
            return None
    
    def get_artist_songs(self, artist: Artist, max_songs: int = 200, include_features: bool = False) -> List[Track]:
        """
        Récupère la liste des morceaux d'un artiste
        
        Args:
            artist: L'artiste dont récupérer les morceaux
            max_songs: Nombre maximum de morceaux à récupérer
            include_features: Si True, inclut les morceaux où l'artiste est en featuring
        """
        tracks = []
        
        try:
            logger.info(f"Récupération des morceaux de {artist.name} (include_features={include_features})")
            
            if not artist.genius_id:
                logger.error(f"Pas d'ID Genius pour {artist.name}")
                return tracks
            
            # NOUVEAU : Utiliser la méthode search_artist de lyricsgenius avec include_features
            try:
                # Rechercher l'artiste avec lyricsgenius pour avoir accès à include_features
                genius_artist = self.genius.search_artist(
                    artist.name,
                    max_songs=max_songs,
                    sort='popularity',
                    get_full_info=True,  # Pour avoir plus de métadonnées
                    include_features=include_features  # ✨ NOUVEAU : Inclure les features
                )
                
                if genius_artist and genius_artist.songs:
                    logger.info(f"Trouvé {len(genius_artist.songs)} morceaux via search_artist")
                    
                    for song in genius_artist.songs:
                        # Extraire les données du song object de lyricsgenius
                        track = self._create_track_from_genius_song(song, artist)
                        if track:
                            tracks.append(track)
                            
                            # Marquer si c'est un featuring
                            if hasattr(song, 'primary_artist') and song.primary_artist.id != artist.genius_id:
                                track.is_featuring = True
                                logger.debug(f"Featuring détecté: {track.title} (artiste principal: {song.primary_artist.name})")
                            else:
                                track.is_featuring = False
                
                log_api("Genius", f"artist/{artist.genius_id}/songs_with_features", True)
                
            except Exception as e:
                logger.warning(f"Erreur avec search_artist: {e}, fallback sur artist_songs")
                # Fallback sur la méthode manuelle si search_artist échoue
                tracks = self._get_artist_songs_manual(artist, max_songs)
                
            logger.info(f"{len(tracks)} morceaux récupérés pour {artist.name}")
            return tracks
            
        except Exception as e:
            logger.error(f"Erreur lors de la récupération des morceaux: {e}")
            log_api("Genius", f"artist/songs", False)
            return tracks
    
    def _create_track_from_genius_song(self, song, artist: Artist) -> Optional[Track]:
        """Crée un objet Track depuis un song object de lyricsgenius - AVEC MARQUAGE"""
        try:
            track_data = {
                'title': song.title,
                'artist': artist,
                'genius_id': song.id,
                'genius_url': song.url,
            }
            
            # Album depuis lyricsgenius + marquage
            if hasattr(song, 'album') and song.album:
                track_data['album'] = song.album
                track_data['_album_from_api'] = True  # ✅ Marquer la source
            
            # Date de sortie depuis lyricsgenius + marquage
            if hasattr(song, 'year') and song.year:
                try:
                    track_data['release_date'] = datetime(int(song.year), 1, 1)
                    track_data['_release_date_from_api'] = True  # ✅ Marquer la source
                except (ValueError, TypeError):
                    pass
            
            # Récupérer des métadonnées supplémentaires depuis l'API raw
            if hasattr(song, '_body') and song._body:
                additional_data = self._extract_additional_metadata_from_raw(song._body)
                track_data.update(additional_data)
            
            track = Track(**track_data)
            logger.debug(f"Track créé: {track.title} (Album: {track.album or 'N/A'} depuis {'API' if track_data.get('_album_from_api') else 'N/A'})")
            return track
            
        except Exception as e:
            logger.error(f"Erreur lors de la création du track: {e}")
            return None
    
    def _extract_additional_metadata_from_raw(self, raw_data: dict) -> dict:
        """Extrait des métadonnées supplémentaires depuis les données brutes"""
        metadata = {}
        
        try:
            # Album depuis les données brutes si pas déjà présent
            if not metadata.get('album') and raw_data.get('album'):
                album_data = raw_data['album']
                if isinstance(album_data, dict) and album_data.get('name'):
                    metadata['album'] = album_data['name']
            
            # Date de sortie plus précise
            release_components = raw_data.get('release_date_components')
            if release_components:
                year = release_components.get('year')
                month = release_components.get('month', 1)
                day = release_components.get('day', 1)
                
                if year:
                    try:
                        metadata['release_date'] = datetime(year, month, day)
                    except (ValueError, TypeError):
                        try:
                            metadata['release_date'] = datetime(year, 1, 1)
                        except (ValueError, TypeError):
                            pass
            
            # Artistes en featuring
            featured_artists = raw_data.get('featured_artists', [])
            if featured_artists:
                features = [artist.get('name') for artist in featured_artists if artist.get('name')]
                if features:
                    metadata['featured_artists'] = ', '.join(features)
            
            # Popularité
            stats = raw_data.get('stats', {})
            if 'pageviews' in stats:
                metadata['popularity'] = stats['pageviews']
            
            # URL de l'artwork
            if raw_data.get('song_art_image_url'):
                metadata['artwork_url'] = raw_data['song_art_image_url']
                
        except Exception as e:
            logger.debug(f"Erreur lors de l'extraction des métadonnées: {e}")
        
        return metadata
    
    def _get_artist_songs_manual(self, artist: Artist, max_songs: int) -> List[Track]:
        """Méthode manuelle de récupération (fallback)"""
        tracks = []
        
        try:
            page = 1
            per_page = 50
            
            while len(tracks) < max_songs:
                response = self.genius.artist_songs(
                    artist.genius_id, 
                    sort='popularity', 
                    per_page=per_page,
                    page=page
                )
                
                if not response or 'songs' not in response:
                    break
                
                songs = response['songs']
                if not songs:
                    break
                
                for song in songs:
                    if song['primary_artist']['id'] != artist.genius_id:
                        continue
                    
                    track = Track(
                        title=song.get('title', ''),
                        artist=artist,
                        genius_id=song.get('id'),
                        genius_url=song.get('url'),
                        album=self._extract_album_from_song(song),
                        release_date=self._extract_release_date_from_song(song),
                        is_featuring=False  # Méthode manuelle = pas de features
                    )
                    
                    tracks.append(track)
                    
                    if len(tracks) >= max_songs:
                        break
                    
                    time.sleep(DELAY_BETWEEN_REQUESTS)
                
                page += 1
                
                if len(songs) < per_page:
                    break
                    
        except Exception as e:
            logger.error(f"Erreur dans la méthode manuelle: {e}")
        
        return tracks
    
    def _extract_album_from_song(self, song: dict) -> Optional[str]:
        """Extrait l'album depuis les données de l'API"""
        try:
            album_data = song.get('album')
            if album_data and isinstance(album_data, dict):
                album_name = album_data.get('name')
                if album_name:
                    return album_name
        except Exception as e:
            logger.debug(f"Erreur lors de l'extraction de l'album: {e}")
        return None
    
    def _extract_release_date_from_song(self, song: dict) -> Optional[datetime]:
        """Extrait la date de sortie depuis les données de l'API"""
        try:
            release_components = song.get('release_date_components')
            if release_components:
                year = release_components.get('year')
                month = release_components.get('month')
                day = release_components.get('day')
                
                if year:
                    if month and day:
                        return datetime(year, month, day)
                    else:
                        return datetime(year, 1, 1)
                        
        except Exception as e:
            logger.debug(f"Erreur lors de l'extraction de la date: {e}")
        
        return None
    
    def get_song_details(self, track: Track) -> Dict[str, Any]:
        """Récupère les détails d'un morceau (pour le scraping)"""
        if not track.genius_id:
            logger.warning(f"Pas d'ID Genius pour {track.title}")
            return {}
        
        try:
            logger.debug(f"Récupération des détails de {track.title}")
            
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
                
                # Features
                for relation in song['song'].get('featured_artists', []):
                    details['features'].append(relation['name'])
                
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
            result = self.genius.search_songs("test", per_page=1)
            return result is not None
        except Exception as e:
            logger.error(f"Erreur de connexion à Genius: {e}")
            return False