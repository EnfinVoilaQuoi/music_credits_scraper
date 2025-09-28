"""Enrichissement des données de morceaux avec diverses sources - VERSION NETTOYÉE"""
import concurrent.futures
from datetime import datetime
from typing import Dict, List, Optional, Any
import time

from src.models import Track
from src.utils.logger import get_logger
from src.scrapers.songbpm_scraper import SongBPMScraper
from src.api.reccobeats_api import ReccoBeatsIntegratedClient

logger = get_logger(__name__)


class DataEnricher:
    """Enrichit les données de morceaux avec diverses sources"""
    
    def __init__(self):
        """Initialise l'enrichisseur de données"""
        self.apis_available = {}
        
        # Initialiser ReccoBeats
        try:
            self.reccobeats_client = ReccoBeatsIntegratedClient()
            self.apis_available['reccobeats'] = True
            logger.info("✅ ReccoBeats client initialisé")
        except Exception as e:
            logger.error(f"Erreur initialisation ReccoBeats: {e}")
            self.apis_available['reccobeats'] = False
            self.reccobeats_client = None
        
        # Initialiser SongBPM scraper
        try:
            self.songbpm_scraper = SongBPMScraper()
            self.apis_available['getsongbpm'] = True
            logger.info("✅ GetSongBPM scraper initialisé")
        except Exception as e:
            logger.error(f"Erreur initialisation GetSongBPM: {e}")
            self.apis_available['getsongbpm'] = False
            self.getsongbpm_scraper = None
        
        logger.info(f"Sources disponibles: {list(self.apis_available.keys())}")
    
    def enrich_track(self, track: Track, sources: Optional[List[str]] = None) -> Dict[str, bool]:
        """
        Enrichit un morceau avec les sources spécifiées
        
        Args:
            track: Le morceau à enrichir
            sources: Liste des sources à utiliser
            
        Returns:
            Dict avec le statut de chaque source
        """
        if sources is None:
            sources = ['reccobeats', 'getsongbpm']
        
        results = {}
        
        logger.debug(f"Enrichissement de '{track.title}' avec sources: {sources}")
        
        # 1. ReccoBeats pour BPM et features audio
        if 'reccobeats' in sources and self.apis_available['reccobeats']:
            try:
                success = self._enrich_with_reccobeats(track)
                results['reccobeats'] = success
                if success:
                    logger.debug(f"✅ ReccoBeats: BPM={track.bpm}")
            except Exception as e:
                logger.error(f"Erreur ReccoBeats pour {track.title}: {e}")
                results['reccobeats'] = False
        
        # 2. GetSongBPM scraper (fallback pour BPM)
        if 'getsongbpm' in sources and self.apis_available['getsongbpm'] and not track.bpm:
            try:
                success = self._enrich_with_getsongbpm(track)
                results['getsongbpm'] = success
                if success:
                    logger.debug(f"✅ GetSongBPM: BPM={track.bpm}")
            except Exception as e:
                logger.error(f"Erreur GetSongBPM pour {track.title}: {e}")
                results['getsongbpm'] = False
        
        return results
    
    def _enrich_with_reccobeats(self, track: Track) -> bool:
        """Enrichit un morceau avec ReccoBeats"""
        try:
            if not self.reccobeats_client:
                logger.error("ReccoBeats client non initialisé")
                return False
            
            logger.info(f"ReccoBeats: Recherche pour '{track.artist.name if hasattr(track.artist, 'name') else str(track.artist)}' - '{track.title}'")
            
            # Rechercher les features pour le morceau
            features_list = self.reccobeats_client.fetch_discography(
                track.artist.name if hasattr(track.artist, 'name') else str(track.artist), 
                [track.title]
            )
            
            logger.debug(f"ReccoBeats: Réponse brute - {features_list}")
            
            if features_list and len(features_list) > 0:
                features = features_list[0]
                logger.debug(f"ReccoBeats: Features extraites - {features}")
                
                # Vérifier si on a des données valides
                if 'error' not in features:
                    # Enrichir avec les données audio
                    if features.get('tempo'):
                        track.bpm = int(round(features['tempo']))
                        logger.info(f"ReccoBeats: BPM trouvé {track.bpm} pour '{track.title}'")
                    else:
                        logger.warning(f"ReccoBeats: Pas de tempo dans la réponse pour '{track.title}'")
                    
                    # Ajouter d'autres features audio si disponibles
                    if not hasattr(track, 'audio_features') or track.audio_features is None:
                        track.audio_features = {}
                        
                    track.audio_features.update({
                        'energy': features.get('energy'),
                        'danceability': features.get('danceability'),
                        'acousticness': features.get('acousticness'),
                        'instrumentalness': features.get('instrumentalness'),
                        'liveness': features.get('liveness'),
                        'loudness': features.get('loudness'),
                        'speechiness': features.get('speechiness'),
                        'valence': features.get('valence'),
                        'key': features.get('key'),
                        'mode': features.get('mode'),
                        'time_signature': features.get('time_signature'),
                        'duration_ms': features.get('duration_ms'),
                    })
                    
                    logger.info(f"ReccoBeats: Succès pour '{track.title}' - BPM: {track.bpm}")
                    return True
                else:
                    error_msg = features.get('error', 'Erreur inconnue')
                    logger.warning(f"ReccoBeats: Erreur pour '{track.title}': {error_msg}")
                    return False
            else:
                logger.warning(f"ReccoBeats: Aucune réponse pour '{track.title}'")
                return False
            
        except Exception as e:
            logger.error(f"ReccoBeats: Exception pour '{track.title}': {e}", exc_info=True)
            return False
    
    def _enrich_with_getsongbpm(self, track: Track) -> bool:
        """Enrichit un morceau avec GetSongBPM (fallback)"""
        try:
            if not self.getsongbpm_scraper:
                return False
            
            bpm = self.getsongbpm_scraper.get_bpm(track.artist, track.title)
            if bpm:
                track.bpm = bpm
                logger.debug(f"GetSongBPM: trouvé BPM {bpm} pour {track.title}")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Erreur GetSongBPM: {e}")
            return False
    
    def enrich_tracks(self, tracks: List[Track], sources: Optional[List[str]] = None,
                     progress_callback=None, use_threading: bool = False) -> Dict[str, Any]:
        """
        Enrichit plusieurs morceaux
        
        Args:
            tracks: Liste des morceaux à enrichir
            sources: Sources à utiliser
            progress_callback: Fonction de callback pour la progression
            use_threading: Utiliser le multi-threading (attention aux rate limits)
        
        Returns:
            Statistiques d'enrichissement
        """
        if sources is None:
            sources = ['reccobeats', 'getsongbpm']
        
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
            # Attention: ReccoBeats a des rate limits!
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
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
            # Mode séquentiel (recommandé pour éviter les rate limits)
            for i, track in enumerate(tracks):
                results = self.enrich_track(track, sources)
                self._update_stats(stats, results, track)
                stats['processed'] += 1
                
                if progress_callback:
                    progress_callback(stats['processed'], stats['total'], 
                                    f"Enrichissement: {track.title}")
                
                # Délai entre les requêtes pour respecter les rate limits
                if 'reccobeats' in sources and self.apis_available['reccobeats']:
                    time.sleep(0.1)  # Petit délai pour ReccoBeats
        
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
        """Retourne la liste des sources d'enrichissement disponibles"""
        available = []
        for source, is_available in self.apis_available.items():
            if is_available:
                available.append(source)
        return available
    
    def test_all_connections(self) -> Dict[str, bool]:
        """Teste toutes les connexions aux APIs"""
        results = {}
        
        # Test ReccoBeats
        if self.apis_available['reccobeats']:
            try:
                # Test simple avec un artiste connu
                test_result = self.reccobeats_client.fetch_discography("Daft Punk", ["Get Lucky"])
                results['reccobeats'] = bool(test_result)
            except Exception as e:
                logger.error(f"Test ReccoBeats échoué: {e}")
                results['reccobeats'] = False
        
        # SongBPM est toujours OK si initialisé
        if self.apis_available['songbpm']:
            results['songbpm'] = True
        
        return results
    
    def close(self):
        """Ferme les connexions si nécessaire"""
        if hasattr(self.getsongbpm_scraper, 'close'):
            self.getsongbpm_scraper.close()
        
        # ReccoBeats utilise requests.Session, pas besoin de fermeture explicite
        logger.info("Connexions fermées")