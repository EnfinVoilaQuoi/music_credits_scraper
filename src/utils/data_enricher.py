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
    
    def __init__(self, headless_songbpm: bool = False):
        """
        Initialise l'enrichisseur de données
        
        Args:
            headless_songbpm: Si True, lance SongBPM en mode headless (sans interface)
        """
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
            self.songbpm_scraper = SongBPMScraper(headless=headless_songbpm)
            self.apis_available['songbpm'] = True
            self.apis_available['getsongbpm'] = True  # Compatibilité ancien nom
            logger.info("✅ SongBPM scraper initialisé (Selenium)")
        except Exception as e:
            logger.error(f"❌ Erreur initialisation SongBPM: {e}")
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
        
        # 2. SongBPM scraper (fallback pour BPM/Key/Duration)
        # N'utiliser que si ReccoBeats n'a pas trouvé le BPM
        if 'songbpm' in sources and self.apis_available.get('songbpm') and not track.bpm:
            try:
                success = self._enrich_with_songbpm(track)
                results['songbpm'] = success
                if success:
                    logger.debug(f"✅ SongBPM: BPM={track.bpm}, Mode={getattr(track, 'mode', 'N/A')}")
            except Exception as e:
                logger.error(f"❌ Erreur SongBPM pour {track.title}: {e}")
                results['songbpm'] = False
        
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
        Enrichit avec ReccoBeats + TIMEOUTS STRICTS pour éviter blocages
        VERSION COMPLÈTE CORRIGÉE - Appelle vraiment ReccoBeats même si spotify_id existe
        """
        try:
            if not self.reccobeats_client:
                logger.error("ReccoBeats client non initialisé")
                return False
            
            artist_name = track.artist.name if hasattr(track.artist, 'name') else str(track.artist)
            
            logger.info(f"ReccoBeats: DÉBUT traitement '{artist_name}' - '{track.title}'")
            
            # =====================================================
            # ÉTAPE 1 : Vérifier si le track a déjà un spotify_id
            # =====================================================
            existing_spotify_id = None
            if hasattr(track, 'spotify_id') and track.spotify_id:
                existing_spotify_id = track.spotify_id
                logger.info(f"✅ Track a déjà un spotify_id: {existing_spotify_id}")
            else:
                logger.info(f"🔍 Pas de spotify_id, recherche nécessaire")
            
            # NOUVEAU: Timeout strict de 60 secondes maximum par track
            import signal
            
            def timeout_handler(signum, frame):
                raise TimeoutError("Timeout ReccoBeats - plus de 60 secondes")
            
            # Configurer le timeout (seulement sur Unix/Linux, pas Windows)
            try:
                signal.signal(signal.SIGALRM, timeout_handler)
                signal.alarm(60)  # 60 secondes max
            except AttributeError:
                # Windows ne supporte pas signal.alarm
                logger.debug("Windows détecté, pas de timeout signal")
            
            try:
                # Nettoyer le cache d'erreur
                self.reccobeats_client.clear_error_cache(artist_name, track.title)
                
                # =====================================================
                # ÉTAPE 2 : TOUJOURS APPELER get_track_info
                # Passer le spotify_id s'il existe pour éviter le scraping
                # =====================================================
                logger.info(f"🎵 Appel ReccoBeats API pour '{track.title}'")
                track_info = self.reccobeats_client.get_track_info(
                    artist=artist_name,
                    title=track.title,
                    use_cache=True,
                    force_refresh=False,
                    spotify_id=existing_spotify_id  # ← Passe le spotify_id s'il existe
                )
                logger.info(f"🔍 DEBUG track_info keys: {list(track_info.keys()) if track_info else None}")
                logger.info(f"🔍 DEBUG track_info key={track_info.get('key') if track_info else None}, mode={track_info.get('mode') if track_info else None}")
                
                # Désactiver l'alarme après succès
                try:
                    signal.alarm(0)
                except AttributeError:
                    pass
                
                # =====================================================
                # ÉTAPE 3 : VÉRIFIER que track_info contient vraiment des données
                # =====================================================
                if not track_info:
                    logger.warning(f"ReccoBeats: ❌ Aucune donnée retournée pour '{track.title}'")
                    return False
                
                # Vérifier qu'on a au moins un spotify_id (minimum requis)
                if not track_info.get('spotify_id'):
                    logger.warning(f"ReccoBeats: ❌ Pas de spotify_id dans la réponse pour '{track.title}'")
                    return False
                
                logger.debug(f"ReccoBeats: ✅ Données récupérées pour '{track.title}'")
                
                # =====================================================
                # ÉTAPE 4 : STOCKER les données dans le track
                # =====================================================
                
                # 4.1 - Stocker l'ID Spotify
                if 'spotify_id' in track_info:
                    track.spotify_id = track_info['spotify_id']
                    logger.info(f"✅ ID Spotify stocké: {track.spotify_id}")
                
                # 4.2 - Extraire et stocker le BPM
                bpm = None
                if 'bpm' in track_info:
                    bpm = track_info['bpm']
                    logger.debug(f"BPM trouvé dans 'bpm': {bpm}")
                elif 'tempo' in track_info:
                    bpm = track_info['tempo']
                    logger.debug(f"BPM trouvé dans 'tempo': {bpm}")
                elif 'audio_features' in track_info:
                    features = track_info['audio_features']
                    if isinstance(features, dict) and 'tempo' in features:
                        bpm = features['tempo']
                        logger.debug(f"BPM trouvé dans 'audio_features.tempo': {bpm}")
                
                # Valider et appliquer le BPM
                if bpm and isinstance(bpm, (int, float)) and 50 <= bpm <= 200:
                    track.bpm = round(float(bpm))
                    logger.info(f"ReccoBeats: ✅ BPM mis à jour: {track.bpm}")
                else:
                    if bpm:
                        logger.warning(f"ReccoBeats: ⚠️ BPM invalide: {bpm} (hors plage 50-200)")
                    else:
                        logger.warning(f"ReccoBeats: ⚠️ Aucun BPM trouvé dans la réponse")
                
                # Stocker Key et Mode 
                if 'key' in track_info and track_info['key'] is not None:
                    key_value = track_info['key']
                    mode_value = track_info.get('mode', 1)  # Par défaut majeur si absent
                    
                    # Stocker dans audio_features pour référence
                    if not hasattr(track, 'audio_features') or track.audio_features is None:
                        track.audio_features = {}
                    track.audio_features['key'] = key_value
                    track.audio_features['mode'] = mode_value
                    
                    # Convertir en notation française
                    from src.utils.music_theory import key_mode_to_french
                    track.musical_key = key_mode_to_french(key_value, mode_value)
                    logger.info(f"ReccoBeats: Tonalité: {track.musical_key}")

                # Stocker time_signature si disponible
                if 'time_signature' in track_info:
                    track.time_signature = str(track_info['time_signature'])
                    logger.debug(f"ReccoBeats: Time signature: {track.time_signature}")
                
                # Stocker la durée si disponible (durationMs -> secondes)
                if 'durationMs' in track_info and track_info['durationMs']:
                    try:
                        duration_ms = track_info['durationMs']
                        track.duration = round(duration_ms / 1000)  # Convertir ms en secondes
                        logger.debug(f"ReccoBeats: Durée: {track.duration}s")
                    except (ValueError, TypeError) as e:
                        logger.warning(f"ReccoBeats: Erreur conversion durée: {e}")

                # =====================================================
                # ÉTAPE 5 : DÉTERMINER le succès
                # On considère un succès si on a au moins un spotify_id
                # Un succès COMPLET si on a aussi le BPM
                # =====================================================
                has_spotify_id = hasattr(track, 'spotify_id') and track.spotify_id
                has_bpm = hasattr(track, 'bpm') and track.bpm
                
                if has_bpm:
                    logger.info(f"ReccoBeats: ✅ FIN SUCCÈS COMPLET '{track.title}' - ID: {track.spotify_id}, BPM: {track.bpm}")
                    return True
                elif has_spotify_id:
                    logger.info(f"ReccoBeats: ⚠️ FIN SUCCÈS PARTIEL '{track.title}' - ID: {track.spotify_id}, Pas de BPM")
                    return True  # On considère quand même comme succès si on a l'ID
                else:
                    logger.warning(f"ReccoBeats: ❌ FIN ÉCHEC '{track.title}'")
                    return False
                    
            except TimeoutError as e:
                logger.error(f"ReccoBeats: ⏰ TIMEOUT pour '{track.title}': {e}")
                try:
                    signal.alarm(0)
                except AttributeError:
                    pass
                return False
                
            except Exception as e:
                logger.error(f"ReccoBeats: ❌ Exception pour '{track.title}': {e}")
                import traceback
                logger.error(traceback.format_exc())
                try:
                    signal.alarm(0)
                except AttributeError:
                    pass
                return False
            
        except Exception as e:
            logger.error(f"ReccoBeats: ❌ Erreur générale pour '{getattr(track, 'title', 'unknown')}': {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
        
        finally:
            # S'assurer que l'alarme est désactivée
            try:
                signal.alarm(0)
            except AttributeError:
                pass
            
            logger.info(f"ReccoBeats: 🏁 FIN traitement '{getattr(track, 'title', 'unknown')}'")
    
    def _enrich_with_songbpm(self, track: Track) -> bool:
        """
        Enrichit un morceau avec SongBPM (scraper Selenium - fallback)
        
        Args:
            track: Le track à enrichir
            
        Returns:
            True si l'enrichissement a réussi
        """
        try:
            if not self.songbpm_scraper:
                logger.debug("SongBPM scraper non disponible")
                return False
            
            # Utiliser la méthode enrich_track_data du scraper
            success = self.songbpm_scraper.enrich_track_data(track)
            
            if success:
                logger.info(f"✅ SongBPM: Données enrichies pour '{track.title}'")
            else:
                logger.debug(f"❌ SongBPM: Aucune donnée trouvée pour '{track.title}'")
            
            return success
            
        except Exception as e:
            logger.error(f"❌ Erreur SongBPM pour '{track.title}': {e}")
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
            except Exception as e:
                logger.error(f"Erreur fermeture ReccoBeats: {e}")
        
        if self.songbpm_scraper:
            try:
                self.songbpm_scraper.close()  # Ferme le driver Selenium
                logger.info("SongBPM scraper fermé")
            except Exception as e:
                logger.error(f"Erreur fermeture SongBPM: {e}")
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()