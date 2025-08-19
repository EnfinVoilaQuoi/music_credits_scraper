"""Interface avec l'API Discogs"""
import time
from typing import Optional, Dict, Any, List
import discogs_client

from src.config import DISCOGS_TOKEN, DELAY_BETWEEN_REQUESTS
from src.models import Track, Artist, Credit, CreditRole
from src.utils.logger import get_logger, log_api


logger = get_logger(__name__)


class DiscogsAPI:
    """Gère les interactions avec l'API Discogs"""
    
    def __init__(self):
        if not DISCOGS_TOKEN:
            raise ValueError("DISCOGS_TOKEN non configuré")
        
        self.client = discogs_client.Client(
            'MusicCreditsScraper/1.0',
            user_token=DISCOGS_TOKEN
        )
        
        logger.info("API Discogs initialisée")
    
    def search_release(self, track_title: str, artist_name: str) -> Optional[Dict[str, Any]]:
        """Recherche une release sur Discogs"""
        try:
            # Rechercher d'abord par artiste et titre
            results = self.client.search(
                track=track_title,
                artist=artist_name,
                type='release'
            )
            
            if not results:
                # Essayer une recherche plus générale
                results = self.client.search(
                    q=f"{artist_name} {track_title}",
                    type='release'
                )
            
            # Convertir en liste et limiter
            results_list = list(results)
            for i, result in enumerate(results_list):
                if i >= 5:  # Limiter aux 5 premiers résultats
                    break
                try:
                    # Vérifier que c'est le bon artiste
                    if any(artist_name.lower() in a.name.lower() for a in result.artists):
                        release_data = self._extract_release_data(result)
                        log_api("Discogs", f"search/release/{track_title}", True)
                        return release_data
                except Exception as e:
                    logger.debug(f"Erreur sur un résultat Discogs: {e}")
                    continue
            
            logger.warning(f"Release non trouvée sur Discogs: {track_title} - {artist_name}")
            return None
            
        except Exception as e:
            logger.error(f"Erreur lors de la recherche Discogs: {e}")
            log_api("Discogs", f"search/release/{track_title}", False)
            return None
    
    def _extract_release_data(self, release) -> Dict[str, Any]:
        """Extrait les données d'une release Discogs"""
        try:
            data = {
                'id': release.id,
                'title': release.title,
                'artists': [a.name for a in release.artists],
                'year': getattr(release, 'year', None),
                'labels': [],
                'genres': getattr(release, 'genres', []),
                'styles': getattr(release, 'styles', []),
                'credits': []
            }
            
            # Récupérer les labels
            if hasattr(release, 'labels'):
                data['labels'] = [label.name for label in release.labels]
            
            # Récupérer les crédits détaillés si disponibles
            if hasattr(release, 'credits'):
                for credit in release.credits:
                    data['credits'].append({
                        'name': credit.name,
                        'role': credit.role
                    })
            
            # Récupérer la tracklist pour plus d'infos
            if hasattr(release, 'tracklist'):
                for track in release.tracklist:
                    if hasattr(track, 'extraartists'):
                        for artist in track.extraartists:
                            data['credits'].append({
                                'name': artist.name,
                                'role': artist.role,
                                'track': track.title
                            })
            
            return data
            
        except Exception as e:
            logger.error(f"Erreur lors de l'extraction des données Discogs: {e}")
            return {}
    
    def enrich_track_data(self, track: Track) -> bool:
        """Enrichit les données d'un morceau avec les infos Discogs"""
        try:
            # Rechercher la release
            release_data = self.search_release(track.title, track.artist.name)
            
            if not release_data:
                return False
            
            # Ajouter l'ID Discogs
            track.discogs_id = release_data.get('id')
            
            # Ajouter les genres/styles si manquants
            if not track.genre and release_data.get('genres'):
                track.genre = ', '.join(release_data['genres'][:2])  # Prendre les 2 premiers
            
            # Ajouter les labels
            for label_name in release_data.get('labels', []):
                label_credit = Credit(
                    name=label_name,
                    role=CreditRole.LABEL,
                    source="discogs"
                )
                track.add_credit(label_credit)
            
            # Ajouter les crédits supplémentaires
            role_mapping = {
                'Producer': CreditRole.PRODUCER,
                'Co-producer': CreditRole.CO_PRODUCER,
                'Executive Producer': CreditRole.EXECUTIVE_PRODUCER,
                'Mixed By': CreditRole.MIXING_ENGINEER,
                'Mastered By': CreditRole.MASTERING_ENGINEER,
                'Engineer': CreditRole.ENGINEER,
                'Vocals': CreditRole.VOCALS,
                'Written-By': CreditRole.WRITER,
                'Composed By': CreditRole.COMPOSER,
                'Arranged By': CreditRole.ARRANGER,
                'Piano': CreditRole.PIANO,
                'Guitar': CreditRole.GUITAR,
                'Bass': CreditRole.BASS,
                'Drums': CreditRole.DRUMS,
                'Saxophone': CreditRole.SAXOPHONE,
                'Trumpet': CreditRole.TRUMPET,
                'Design': CreditRole.GRAPHIC_DESIGN,
                'Photography': CreditRole.PHOTOGRAPHY,
                'Artwork': CreditRole.ARTWORK
            }
            
            for credit_data in release_data.get('credits', []):
                role_str = credit_data.get('role', '')
                
                # Mapper le rôle Discogs vers notre enum
                matched_role = None
                for discogs_role, our_role in role_mapping.items():
                    if discogs_role.lower() in role_str.lower():
                        matched_role = our_role
                        break
                
                if matched_role:
                    credit = Credit(
                        name=credit_data['name'],
                        role=matched_role,
                        source="discogs"
                    )
                    track.add_credit(credit)
                else:
                    # Si rôle non reconnu, l'ajouter comme "OTHER"
                    credit = Credit(
                        name=credit_data['name'],
                        role=CreditRole.OTHER,
                        role_detail=role_str,
                        source="discogs"
                    )
                    track.add_credit(credit)
            
            # Respecter le rate limit
            time.sleep(DELAY_BETWEEN_REQUESTS)
            
            logger.info(f"Données Discogs ajoutées pour {track.title}")
            return True
            
        except Exception as e:
            logger.error(f"Erreur lors de l'enrichissement Discogs: {e}")
            return False
    
    def search_artist(self, artist_name: str) -> Optional[Dict[str, Any]]:
        """Recherche un artiste sur Discogs"""
        try:
            results = self.client.search(artist_name, type='artist')
            
            if results:
                artist = results[0]
                
                log_api("Discogs", f"search/artist/{artist_name}", True)
                return {
                    'id': artist.id,
                    'name': artist.name,
                    'profile': getattr(artist, 'profile', ''),
                    'urls': getattr(artist, 'urls', []),
                    'namevariations': getattr(artist, 'namevariations', [])
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Erreur lors de la recherche d'artiste Discogs: {e}")
            log_api("Discogs", f"search/artist/{artist_name}", False)
            return None
    
    def enrich_multiple_tracks(self, tracks: List[Track], progress_callback=None) -> Dict[str, int]:
        """Enrichit plusieurs morceaux avec les données Discogs"""
        results = {
            'enriched': 0,
            'failed': 0
        }
        
        total = len(tracks)
        
        for i, track in enumerate(tracks):
            if self.enrich_track_data(track):
                results['enriched'] += 1
            else:
                results['failed'] += 1
            
            if progress_callback:
                progress_callback(i + 1, total, f"Discogs: {track.title}")
        
        logger.info(f"Enrichissement Discogs terminé: {results['enriched']} réussis, {results['failed']} échoués")
        return results
    
    def test_connection(self) -> bool:
        """Teste la connexion à l'API"""
        try:
            # Faire une recherche simple
            result = self.client.search("test", type="release", per_page=1)
            return True
        except Exception as e:
            logger.error(f"Erreur de connexion à Discogs: {e}")
            return False