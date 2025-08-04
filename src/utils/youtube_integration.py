"""Intégration YouTube simplifiée pour main_window"""
from typing import Optional, Dict
import webbrowser
from urllib.parse import quote

from src.youtube.youtube_searcher import YouTubeSearcher
from src.youtube.track_classifier import TrackClassifier, TrackType
from src.config import YOUTUBE_AUTO_SELECT_ALBUM_TRACKS, YOUTUBE_CONFIDENCE_THRESHOLD
from src.utils.logger import get_logger

logger = get_logger(__name__)


class YouTubeIntegration:
    """Interface simplifiée pour l'intégration YouTube dans l'interface"""
    
    def __init__(self):
        self.searcher = YouTubeSearcher()
        self.classifier = TrackClassifier()
    
    def get_youtube_link_for_track(self, artist: str, title: str, album: str = None, 
                                  release_year: int = None) -> Dict[str, str]:
        """
        Retourne le lien YouTube approprié pour un morceau
        
        Returns:
            Dict avec 'url', 'type' ('direct' ou 'search'), 'confidence', 'method'
        """
        
        try:
            # Étape 1: Classification du morceau
            track_type = self.classifier.classify_track(
                title, album=album, release_year=release_year
            )
            
            # Étape 2: Décider de la stratégie
            should_auto = (YOUTUBE_AUTO_SELECT_ALBUM_TRACKS and 
                          self.classifier.should_auto_select(track_type))
            
            if should_auto:
                # Tentative de sélection automatique
                auto_result = self._try_auto_selection(artist, title, track_type)
                if auto_result:
                    return auto_result
            
            # Fallback: URL de recherche
            return self._generate_search_url(artist, title, track_type)
            
        except Exception as e:
            logger.error(f"Erreur YouTube pour {artist} - {title}: {e}")
            return self._generate_fallback_search_url(artist, title)
    
    def _try_auto_selection(self, artist: str, title: str, track_type: TrackType) -> Optional[Dict]:
        """Tentative de sélection automatique"""
        
        try:
            # Rechercher les candidats
            results = self.searcher.search_track(artist, title, max_results=10)
            
            if not results:
                logger.debug(f"Aucun résultat pour {artist} - {title}")
                return None
            
            best_result = results[0]
            confidence = best_result.get('relevance_score', 0)
            threshold = self.classifier.get_confidence_threshold(track_type)
            
            # Vérifier le seuil de confiance
            if confidence >= threshold and not best_result.get('is_search_url', False):
                logger.info(f"Auto-sélection: {best_result['url']} (confiance: {confidence:.2f})")
                
                return {
                    'url': best_result['url'],
                    'type': 'direct',
                    'confidence': confidence,
                    'method': 'auto_selected',
                    'track_type': track_type.value,
                    'title': best_result.get('title', title),
                    'channel': best_result.get('channel_title', 'Inconnu')
                }
            else:
                logger.debug(f"Confiance insuffisante: {confidence:.2f} < {threshold}")
                return None
                
        except Exception as e:
            logger.debug(f"Erreur auto-sélection: {e}")
            return None
    
    def _generate_search_url(self, artist: str, title: str, track_type: TrackType) -> Dict:
        """Génère une URL de recherche optimisée selon le type"""
        
        strategy = self.classifier.get_search_strategy(track_type)
        
        # Construire la requête selon la stratégie
        primary_query = strategy['primary_query'].format(artist=artist, title=title)
        search_url = f"https://www.youtube.com/results?search_query={quote(primary_query)}"
        
        return {
            'url': search_url,
            'type': 'search',
            'confidence': 0.0,
            'method': 'optimized_search',
            'track_type': track_type.value,
            'query': primary_query
        }
    
    def _generate_fallback_search_url(self, artist: str, title: str) -> Dict:
        """Génère une URL de recherche basique en cas d'erreur"""
        
        search_term = f"{artist} {title} audio"
        search_url = f"https://www.youtube.com/results?search_query={quote(search_term)}"
        
        return {
            'url': search_url,
            'type': 'search',
            'confidence': 0.0,
            'method': 'fallback_search',
            'track_type': 'unknown'
        }
    
    def open_youtube_link(self, youtube_result: Dict) -> bool:
        """Ouvre le lien YouTube dans le navigateur"""
        
        try:
            url = youtube_result.get('url')
            if url:
                webbrowser.open(url)
                
                # Log selon le type
                if youtube_result.get('type') == 'direct':
                    logger.info(f"Ouverture directe: {url}")
                else:
                    logger.info(f"Ouverture recherche: {youtube_result.get('query', 'N/A')}")
                
                return True
        except Exception as e:
            logger.error(f"Erreur ouverture YouTube: {e}")
        
        return False


# Instance globale pour faciliter l'import
youtube_integration = YouTubeIntegration()