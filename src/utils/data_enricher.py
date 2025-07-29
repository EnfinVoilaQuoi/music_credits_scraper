"""Orchestrateur pour l'enrichissement des données depuis toutes les sources"""
from typing import List, Dict, Any, Optional
import concurrent.futures
from datetime import datetime

from src.api.spotify_api import SpotifyAPI
from src.api.discogs_api import DiscogsAPI
from src.api.lastfm_api import LastFmAPI
from src.scrapers.rapedia_scraper import RapediaScraper
from src.models import Track, Artist
from src.utils.logger import get_logger
from src.config import SPOTIFY_CLIENT_ID, DISCOGS_TOKEN, LAST_FM_API_KEY


logger = get_logger(__name__)


class DataEnricher:
    """Orchestre l'enrichissement des données depuis toutes les sources"""
    
    def __init__(self):
        self.apis_available = {}
        self._init_apis()
    
    def _init_apis(self):
        """Initialise les APIs disponibles"""
        # Spotify
        if SPOTIFY_CLIENT_ID:
            try:
                self.spotify_api = SpotifyAPI()
                self.apis_available['spotify'] = True
                logger.info("API Spotify disponible")
            except Exception as e:
                logger.warning(f"API Spotify non disponible: {e}")
                self.apis_available['spotify'] = False
        else:
            self.apis_available['spotify'] = False
        
        # Discogs
        if DISCOGS_TOKEN:
            try:
                self.discogs_api = DiscogsAPI()
                self.apis_available['discogs'] = True
                logger.info("API Discogs disponible")
            except Exception as e:
                logger.warning(f"API Discogs non disponible: {e}")
                self.apis_available['discogs'] = False
        else:
            self.apis_available['discogs'] = False
        
        # Last.fm
        if LAST_FM_API_KEY:
            try:
                self.lastfm_api = LastFmAPI()
                self.apis_available['lastfm'] = True
                logger.info("API Last.fm disponible")
            except Exception as e:
                logger.warning(f"API Last.fm non disponible: {e}")
                self.apis_available['lastfm'] = False
        else:
            self.apis_available['lastfm'] = False
        
        # Rapedia est toujours disponible (scraping)
        self.rapedia_scraper = RapediaScraper(use_selenium=False)
        self.apis_available['rapedia'] = True
        logger.info("Scraper Rapedia disponible")
    
    def enrich_track(self, track: Track, sources: Optional[List[str]] = None) -> Dict[str, bool]:
        """
        Enrichit un morceau avec les données de toutes les sources disponibles
        
        Args:
            track: Le morceau à enrichir
            sources: Liste des sources à utiliser (None = toutes)
        
        Returns:
            Dict indiquant le succès pour chaque source
        """
        if sources is None:
            sources = ['rapedia', 'spotify', 'discogs', 'lastfm']
        
        results = {}
        
        # 1. Rapedia en premier (prioritaire pour les BPM)
        if 'rapedia' in sources and self.apis_available['rapedia']:
            try:
                success = self.rapedia_scraper.enrich_track_data(track)
                results['rapedia'] = success
                if success and track.bpm:
                    logger.info(f"BPM trouvé sur Rapedia: {track.bpm}")
            except Exception as e:
                logger.error(f"Erreur Rapedia: {e}")
                results['rapedia'] = False
        
        # 2. Spotify (seulement si pas de BPM depuis Rapedia)
        if 'spotify' in sources and self.apis_available['spotify']:
            try:
                # Spotify enrichit seulement si pas de BPM
                success = self.spotify_api.enrich_track_data(track)
                results['spotify'] = success
            except Exception as e:
                logger.error(f"Erreur Spotify: {e}")
                results['spotify'] = False
        
        # 3. Discogs pour les crédits supplémentaires
        if 'discogs' in sources and self.apis_available['discogs']:
            try:
                success = self.discogs_api.enrich_track_data(track)
                results['discogs'] = success
            except Exception as e:
                logger.error(f"Erreur Discogs: {e}")
                results['discogs'] = False
        
        # 4. Last.fm pour les genres et métadonnées
        if 'lastfm' in sources and self.apis_available['lastfm']:
            try:
                success = self.lastfm_api.enrich_track_data(track)
                results['lastfm'] = success
            except Exception as e:
                logger.error(f"Erreur Last.fm: {e}")
                results['lastfm'] = False
        
        return results
    
    def enrich_tracks(self, tracks: List[Track], sources: Optional[List[str]] = None,
                     progress_callback=None, use_threading: bool = False) -> Dict[str, Any]:
        """
        Enrichit plusieurs morceaux
        
        Args:
            tracks: Liste des morceaux à enrichir
            sources: Sources à utiliser
            progress_callback: Fonction de callback pour la progression
            use_threading: Utiliser le multi-threading (plus rapide mais plus de requêtes)
        
        Returns:
            Statistiques d'enrichissement
        """
        if sources is None:
            sources = ['rapedia', 'spotify', 'discogs', 'lastfm']
        
        stats = {
            'total': len(tracks),
            'processed': 0,
            'by_source': {source: {'success': 0, 'failed': 0} for source in sources},
            'tracks_with_bpm': 0,
            'tracks_with_complete_data': 0
        }
        
        start_time = datetime.now()
        
        if use_threading and len(tracks) > 10:
            # Utiliser le threading pour les grandes listes
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                futures = []
                for i, track in enumerate(tracks):
                    future = executor.submit(self.enrich_track, track, sources)
                    futures.append((i, track, future))
                
                for i, track, future in futures:
                    try:
                        results = future.result(timeout=30)
                        self._update_stats(stats, results, track)
                        stats['processed'] += 1
                        
                        if progress_callback:
                            progress_callback(stats['processed'], stats['total'], 
                                            f"Enrichissement: {track.title}")
                    except Exception as e:
                        logger.error(f"Erreur lors de l'enrichissement de {track.title}: {e}")
        else:
            # Mode séquentiel
            for i, track in enumerate(tracks):
                results = self.enrich_track(track, sources)
                self._update_stats(stats, results, track)
                stats['processed'] += 1
                
                if progress_callback:
                    progress_callback(stats['processed'], stats['total'], 
                                    f"Enrichissement: {track.title}")
        
        # Calculer le temps total
        elapsed = (datetime.now() - start_time).total_seconds()
        stats['duration_seconds'] = elapsed
        stats['average_time_per_track'] = elapsed / len(tracks) if tracks else 0
        
        logger.info(f"Enrichissement terminé: {stats['processed']} morceaux en {elapsed:.1f}s")
        logger.info(f"Morceaux avec BPM: {stats['tracks_with_bpm']}")
        
        return stats
    
    def _update_stats(self, stats: Dict, results: Dict[str, bool], track: Track):
        """Met à jour les statistiques"""
        for source, success in results.items():
            if success:
                stats['by_source'][source]['success'] += 1
            else:
                stats['by_source'][source]['failed'] += 1
        
        if track.bpm:
            stats['tracks_with_bpm'] += 1
        
        # Vérifier si le track a des données complètes
        if track.bpm and track.genre and len(track.credits) > 5:
            stats['tracks_with_complete_data'] += 1
    
    def get_available_sources(self) -> List[str]:
        """Retourne la liste des sources disponibles"""
        return [source for source, available in self.apis_available.items() if available]
    
    def test_all_connections(self) -> Dict[str, bool]:
        """Teste toutes les connexions aux APIs"""
        results = {}
        
        # Test Spotify
        if self.apis_available['spotify']:
            results['spotify'] = self.spotify_api.test_connection()
        
        # Test Discogs
        if self.apis_available['discogs']:
            results['discogs'] = self.discogs_api.test_connection()
        
        # Test Last.fm
        if self.apis_available['lastfm']:
            results['lastfm'] = self.lastfm_api.test_connection()
        
        # Rapedia est toujours OK si on arrive ici
        results['rapedia'] = True
        
        return results
    
    def close(self):
        """Ferme les connexions si nécessaire"""
        # Fermer Rapedia si utilise Selenium
        if hasattr(self.rapedia_scraper, 'close'):
            self.rapedia_scraper.close()
