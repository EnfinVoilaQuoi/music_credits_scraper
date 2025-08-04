"""Recherche YouTube avec fallbacks et cache"""
import requests
import sqlite3
import pickle
import difflib
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from pathlib import Path

from src.config import DATA_DIR, YOUTUBE_CACHE_TTL_HOURS
from src.utils.logger import get_logger

logger = get_logger(__name__)


class YouTubeSearcher:
    """Recherche YouTube avec ytmusicapi et fallbacks"""
    
    def __init__(self):
        self.cache_db = DATA_DIR / "youtube_cache.db"
        self._init_cache()
        
        # Tenter d'initialiser ytmusicapi
        try:
            from ytmusicapi import YTMusic
            self.ytmusic = YTMusic()
            self.ytmusic_available = True
            logger.info("YTMusic initialisé avec succès")
        except ImportError:
            logger.warning("ytmusicapi non disponible, utilisation fallback requests")
            self.ytmusic = None
            self.ytmusic_available = False
        except Exception as e:
            logger.warning(f"Erreur YTMusic: {e}, utilisation fallback")
            self.ytmusic = None
            self.ytmusic_available = False
    
    def _init_cache(self):
        """Initialise la base de données de cache"""
        self.cache_db.parent.mkdir(parents=True, exist_ok=True)
        
        conn = sqlite3.connect(self.cache_db)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS youtube_search_cache (
                query_hash TEXT PRIMARY KEY,
                results BLOB,
                cached_at TIMESTAMP,
                expires_at TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()
    
    def search_track(self, artist: str, title: str, max_results: int = 25) -> List[Dict]:
        """Recherche principale avec cache et fallbacks"""
        
        # Vérifier le cache d'abord
        cache_key = f"{artist}::{title}".replace(" ", "_").lower()
        cached_result = self._get_cached_result(cache_key)
        if cached_result:
            logger.debug(f"Cache hit pour {artist} - {title}")
            return cached_result
        
        results = []
        
        # Méthode 1: ytmusicapi (recommandée)
        if self.ytmusic_available:
            try:
                results = self._search_with_ytmusic(artist, title, max_results)
                logger.info(f"ytmusicapi: {len(results)} résultats pour {artist} - {title}")
            except Exception as e:
                logger.warning(f"Erreur ytmusicapi: {e}")
        
        # Méthode 2: Fallback requests simple (si ytmusicapi échoue)
        if not results:
            try:
                results = self._search_with_requests_fallback(artist, title, max_results)
                logger.info(f"Fallback: {len(results)} résultats pour {artist} - {title}")
            except Exception as e:
                logger.error(f"Erreur fallback: {e}")
        
        # Trier par pertinence
        if results:
            results = sorted(results, key=lambda x: x.get('relevance_score', 0), reverse=True)
            # Mettre en cache
            self._cache_result(cache_key, results)
        
        return results
    
    def _search_with_ytmusic(self, artist: str, title: str, max_results: int) -> List[Dict]:
        """Recherche avec ytmusicapi"""
        
        search_queries = [
            f"{artist} {title}",
            f'"{artist}" "{title}"',
            f"{artist} {title} official"
        ]
        
        all_results = []
        seen_video_ids = set()
        
        for query in search_queries:
            try:
                results = self.ytmusic.search(query, filter="songs", limit=15)
                
                for result in results:
                    video_id = result.get('videoId')
                    if video_id and video_id not in seen_video_ids:
                        seen_video_ids.add(video_id)
                        
                        # Calculer score de pertinence
                        score = self._calculate_relevance_score(result, artist, title)
                        
                        # Extraire les artistes
                        artists = []
                        for art in result.get('artists', []):
                            if isinstance(art, dict):
                                artists.append(art.get('name', ''))
                            else:
                                artists.append(str(art))
                        
                        formatted_result = {
                            'video_id': video_id,
                            'title': result.get('title', ''),
                            'channel_title': artists[0] if artists else 'Inconnu',
                            'channel_id': None,  # ytmusicapi ne fournit pas toujours l'ID
                            'duration': result.get('duration', ''),
                            'thumbnail_url': self._get_best_thumbnail(result.get('thumbnails', [])),
                            'relevance_score': score,
                            'source': 'ytmusicapi',
                            'url': f"https://youtube.com/watch?v={video_id}"
                        }
                        
                        all_results.append(formatted_result)
                        
                        if len(all_results) >= max_results:
                            break
                
            except Exception as e:
                logger.debug(f"Erreur recherche ytmusicapi pour '{query}': {e}")
                continue
        
        return all_results[:max_results]
    
    def _search_with_requests_fallback(self, artist: str, title: str, max_results: int) -> List[Dict]:
        """Fallback avec requests basique (génère juste des liens de recherche)"""
        
        # Cette méthode génère des résultats "fictifs" pour tests
        # En production, vous pourriez utiliser yt-dlp ou autre
        
        search_queries = [
            f"{artist} {title}",
            f"{artist} {title} official",
            f"{artist} {title} lyrics"
        ]
        
        results = []
        
        for i, query in enumerate(search_queries):
            # Simuler un résultat de recherche
            from urllib.parse import quote
            search_url = f"https://www.youtube.com/results?search_query={quote(query)}"
            
            result = {
                'video_id': f"fallback_{i}",
                'title': f"{title} - {artist}",
                'channel_title': artist,
                'channel_id': None,
                'duration': "3:30",
                'thumbnail_url': None,
                'relevance_score': 0.8 - (i * 0.2),  # Score décroissant
                'source': 'fallback_search',
                'url': search_url,  # URL de recherche au lieu de vidéo directe
                'is_search_url': True  # Marquer comme URL de recherche
            }
            
            results.append(result)
        
        return results
    
    def _calculate_relevance_score(self, result: Dict, target_artist: str, target_title: str) -> float:
        """Calcule un score de pertinence"""
        
        result_title = result.get('title', '').lower()
        
        # Récupérer les artistes
        result_artists = []
        for artist in result.get('artists', []):
            if isinstance(artist, dict):
                result_artists.append(artist.get('name', '').lower())
            else:
                result_artists.append(str(artist).lower())
        
        # Similarité du titre
        title_similarity = difflib.SequenceMatcher(
            None, target_title.lower(), result_title
        ).ratio()
        
        # Similarité de l'artiste
        artist_similarity = 0
        target_artist_lower = target_artist.lower()
        for result_artist in result_artists:
            similarity = difflib.SequenceMatcher(
                None, target_artist_lower, result_artist
            ).ratio()
            artist_similarity = max(artist_similarity, similarity)
        
        # Score composite (titre 60%, artiste 40%)
        return (title_similarity * 0.6) + (artist_similarity * 0.4)
    
    def _get_best_thumbnail(self, thumbnails: List[Dict]) -> Optional[str]:
        """Récupère la meilleure thumbnail disponible"""
        if not thumbnails:
            return None
        
        # Prendre la thumbnail de meilleure qualité
        best_thumb = max(thumbnails, key=lambda x: x.get('width', 0) * x.get('height', 0))
        return best_thumb.get('url')
    
    def _get_cached_result(self, cache_key: str) -> Optional[List[Dict]]:
        """Récupération depuis le cache"""
        try:
            conn = sqlite3.connect(self.cache_db)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT results FROM youtube_search_cache WHERE query_hash = ? AND expires_at > ?",
                (cache_key, datetime.now())
            )
            result = cursor.fetchone()
            conn.close()
            
            if result:
                return pickle.loads(result[0])
        except Exception as e:
            logger.debug(f"Erreur cache: {e}")
        
        return None
    
    def _cache_result(self, cache_key: str, results: List[Dict]):
        """Mise en cache des résultats"""
        try:
            conn = sqlite3.connect(self.cache_db)
            cursor = conn.cursor()
            expires_at = datetime.now() + timedelta(hours=YOUTUBE_CACHE_TTL_HOURS)
            cursor.execute(
                "INSERT OR REPLACE INTO youtube_search_cache (query_hash, results, cached_at, expires_at) VALUES (?, ?, ?, ?)",
                (cache_key, pickle.dumps(results), datetime.now(), expires_at)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.debug(f"Erreur mise en cache: {e}")
    
    def get_direct_youtube_url(self, artist: str, title: str) -> Optional[str]:
        """Retourne directement le meilleur lien YouTube trouvé"""
        results = self.search_track(artist, title, max_results=5)
        
        if results and not results[0].get('is_search_url', False):
            best_result = results[0]
            logger.info(f"Lien automatique sélectionné: {best_result['url']} (score: {best_result['relevance_score']:.2f})")
            return best_result['url']
        
        return None