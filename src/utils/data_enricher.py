"""Enrichissement des données de morceaux avec diverses sources - VERSION CORRIGÉE"""
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
        
        # Initialiser ReccoBeats avec la nouvelle API
        try:
            # Important: mode headless pour l'utilisation en production
            self.reccobeats_client = ReccoBeatsIntegratedClient(headless=False)
            # Vider le cache des erreurs précédentes si nécessaire
            if hasattr(self.reccobeats_client, 'clear_old_errors'):
                self.reccobeats_client.clear_old_errors()
            self.apis_available['reccobeats'] = True
            logger.info("✅ ReccoBeats client initialisé")
        except Exception as e:
            logger.error(f"Erreur initialisation ReccoBeats: {e}")
            self.apis_available['reccobeats'] = False
            self.reccobeats_client = None
        
        # Initialiser SongBPM scraper
        try:
            self.songbpm_scraper = SongBPMScraper()
            self.apis_available['songbpm'] = True  # Utiliser 'songbpm' pour correspondre à l'interface
            self.apis_available['getsongbpm'] = True  # Garder aussi l'ancien nom pour compatibilité
            logger.info("✅ GetSongBPM scraper initialisé")
        except Exception as e:
            logger.error(f"Erreur initialisation GetSongBPM: {e}")
            self.apis_available['songbpm'] = False
            self.apis_available['getsongbpm'] = False
            self.songbpm_scraper = None
        
        # Discogs n'est pas implémenté mais on le marque comme non disponible
        self.apis_available['discogs'] = False
        
        logger.info(f"Sources disponibles: {[k for k, v in self.apis_available.items() if v]}")
    
    def get_available_sources(self) -> List[str]:
        """Retourne la liste des sources disponibles"""
        return [k for k, v in self.apis_available.items() if v]
    
    def get_available_sources(self) -> List[str]:
        """Retourne la liste des sources disponibles"""
        return [k for k, v in self.apis_available.items() if v]
    
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
            sources = ['reccobeats', 'songbpm']
        
        results = {}
        
        logger.debug(f"Enrichissement de '{track.title}' avec sources: {sources}")
        
        # 1. ReccoBeats pour BPM et features audio
        if 'reccobeats' in sources and self.apis_available.get('reccobeats'):
            try:
                success = self._enrich_with_reccobeats(track)
                results['reccobeats'] = success
                if success:
                    logger.debug(f"✅ ReccoBeats: BPM={track.bpm}")
            except Exception as e:
                logger.error(f"Erreur ReccoBeats pour {track.title}: {e}")
                results['reccobeats'] = False
        
        # 2. GetSongBPM scraper (fallback pour BPM) - accepter 'songbpm' ou 'getsongbpm'
        if ('songbpm' in sources or 'getsongbpm' in sources) and \
           self.apis_available.get('songbpm') and not track.bpm:
            try:
                success = self._enrich_with_getsongbpm(track)
                # Utiliser le nom de la source qui était dans la liste
                source_name = 'songbpm' if 'songbpm' in sources else 'getsongbpm'
                results[source_name] = success
                if success:
                    logger.debug(f"✅ GetSongBPM: BPM={track.bpm}")
            except Exception as e:
                logger.error(f"Erreur GetSongBPM pour {track.title}: {e}")
                source_name = 'songbpm' if 'songbpm' in sources else 'getsongbpm'
                results[source_name] = False
        
        # 3. Discogs (non implémenté pour l'instant)
        if 'discogs' in sources:
            logger.debug("Discogs n'est pas encore implémenté")
            results['discogs'] = False
        
        # Marquer comme enrichi si au moins une source a réussi
        if any(results.values()):
            track.enriched = True
            track.enrichment_date = datetime.now()
        
        return results
    
    def _enrich_with_reccobeats(self, track: Track) -> bool:
        """
        Enrichit un morceau avec ReccoBeats en utilisant la NOUVELLE API
        """
        try:
            if not self.reccobeats_client:
                logger.error("ReccoBeats client non initialisé")
                return False
            
            # Extraire le nom de l'artiste correctement
            artist_name = track.artist.name if hasattr(track.artist, 'name') else str(track.artist)
            
            logger.info(f"ReccoBeats: Recherche pour '{artist_name}' - '{track.title}'")
            
            # IMPORTANT: Utiliser la nouvelle méthode get_track_info() au lieu de fetch_discography()
            track_info = self.reccobeats_client.get_track_info(
                artist=artist_name,
                title=track.title,
                use_cache=True  # Utiliser le cache mais éviter les erreurs en cache
            )
            
            if track_info and track_info.get('success'):
                logger.debug(f"ReccoBeats: Données récupérées - {track_info.get('spotify_id')}")
                
                # Extraire le BPM
                bpm = None
                
                # Chercher le BPM dans différents endroits possibles
                if 'bpm' in track_info:
                    bpm = track_info['bpm']
                elif 'audio_features' in track_info:
                    features = track_info['audio_features']
                    if isinstance(features, dict) and 'tempo' in features:
                        bpm = features['tempo']
                
                # Appliquer le BPM si trouvé
                if bpm and isinstance(bpm, (int, float)) and bpm > 0:
                    track.bpm = int(round(bpm))
                    logger.info(f"ReccoBeats: BPM trouvé {track.bpm} pour '{track.title}'")
                else:
                    logger.warning(f"ReccoBeats: Pas de BPM valide dans la réponse pour '{track.title}'")
                
                # Enrichir avec les autres features audio si disponibles
                if not hasattr(track, 'audio_features') or track.audio_features is None:
                    track.audio_features = {}
                
                # Extraire toutes les features audio disponibles
                if 'audio_features' in track_info:
                    features = track_info['audio_features']
                    if isinstance(features, dict):
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
                
                # Ajouter également les infos directement au niveau du track_info
                for key in ['energy', 'danceability', 'key', 'mode', 'valence']:
                    if key in track_info:
                        track.audio_features[key] = track_info[key]
                
                # Stocker l'ID Spotify si disponible
                if 'spotify_id' in track_info:
                    if not hasattr(track, 'spotify_id'):
                        track.spotify_id = track_info['spotify_id']
                
                logger.info(f"ReccoBeats: ✅ Succès pour '{track.title}' - BPM: {track.bpm}")
                return True
                
            else:
                # Gérer les différents types d'erreur
                if track_info and 'error' in track_info:
                    error_type = track_info['error']
                    if error_type == 'spotify_id_not_found':
                        logger.warning(f"ReccoBeats: ID Spotify non trouvé pour '{track.title}'")
                    elif error_type == 'reccobeats_not_found':
                        logger.warning(f"ReccoBeats: Données non disponibles dans ReccoBeats pour '{track.title}'")
                    else:
                        logger.warning(f"ReccoBeats: Erreur {error_type} pour '{track.title}'")
                else:
                    logger.warning(f"ReccoBeats: Aucune donnée retournée pour '{track.title}'")
                
                return False
                
        except AttributeError as e:
            logger.error(f"ReccoBeats: Erreur d'attribut pour '{track.title}': {e}")
            return False
        except Exception as e:
            logger.error(f"ReccoBeats: Exception pour '{track.title}': {e}", exc_info=True)
            return False
    
    def _enrich_with_getsongbpm(self, track: Track) -> bool:
        """Enrichit un morceau avec GetSongBPM (fallback)"""
        try:
            if not self.songbpm_scraper:
                return False
            
            # Extraire le nom de l'artiste
            artist_name = track.artist.name if hasattr(track.artist, 'name') else str(track.artist)
            
            bpm = self.songbpm_scraper.get_bpm(artist_name, track.title)
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
            sources = ['reccobeats', 'songbpm']
        
        stats = {
            'total': len(tracks),
            'processed': 0,
            'by_source': {source: {'success': 0, 'failed': 0} for source in sources},
            'tracks_with_bpm': 0,
            'tracks_with_complete_data': 0,
            'start_time': datetime.now()
        }
        
        logger.info(f"Début enrichissement de {len(tracks)} morceaux avec sources: {sources}")
        
        for i, track in enumerate(tracks, 1):
            try:
                # Callback de progression
                if progress_callback:
                    progress_info = f"Traitement: {track.title[:30]}..."
                    progress_callback(i, len(tracks), progress_info)
                
                # Enrichir le morceau
                results = self.enrich_track(track, sources)
                
                # Mettre à jour les stats
                stats['processed'] += 1
                for source, success in results.items():
                    if success:
                        stats['by_source'][source]['success'] += 1
                    else:
                        stats['by_source'][source]['failed'] += 1
                
                # Compter les morceaux avec BPM
                if hasattr(track, 'bpm') and track.bpm:
                    stats['tracks_with_bpm'] += 1
                
                # Compter les morceaux avec données complètes
                if (hasattr(track, 'bpm') and track.bpm and 
                    hasattr(track, 'audio_features') and track.audio_features):
                    stats['tracks_with_complete_data'] += 1
                
                # Délai entre les requêtes pour éviter le rate limiting
                if i < len(tracks):
                    time.sleep(0.5)  # Délai de 500ms entre chaque requête
                    
            except Exception as e:
                logger.error(f"Erreur lors de l'enrichissement du morceau {track.title}: {e}")
                stats['processed'] += 1
        
        # Durée totale
        stats['duration'] = (datetime.now() - stats['start_time']).total_seconds()
        
        # Logs finaux
        logger.info(f"Enrichissement terminé en {stats['duration']:.1f}s")
        logger.info(f"Morceaux traités: {stats['processed']}/{stats['total']}")
        logger.info(f"Morceaux avec BPM: {stats['tracks_with_bpm']}")
        logger.info(f"Morceaux avec données complètes: {stats['tracks_with_complete_data']}")
        
        for source in sources:
            if source in stats['by_source']:
                source_stats = stats['by_source'][source]
                logger.info(f"{source}: {source_stats['success']} succès, {source_stats['failed']} échecs")
        
        return stats
    
    def close(self):
        """Ferme toutes les connexions"""
        if self.reccobeats_client:
            try:
                self.reccobeats_client.close()
                logger.info("ReccoBeats client fermé")
            except:
                pass
        
        if self.songbpm_scraper:
            try:
                if hasattr(self.songbpm_scraper, 'close'):
                    self.songbpm_scraper.close()
                logger.info("GetSongBPM scraper fermé")
            except:
                pass
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()