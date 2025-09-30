"""
Module d'enrichissement des données tracks
VERSION CORRIGÉE: Empêche la duplication des Spotify IDs + Intégration Spotify_ID scraper
"""
from typing import List, Dict, Optional, Any
from datetime import datetime
from src.models import Track
from src.scrapers.songbpm_scraper import SongBPMScraper
from src.scrapers.spotify_id_scraper import SpotifyIDScraper
from src.api.reccobeats_api import ReccoBeatsIntegratedClient
from src.utils.logger import get_logger

logger = get_logger(__name__)


class DataEnricher:
    """Enrichissement des données des morceaux"""
    
    def __init__(self, headless_reccobeats: bool = False, headless_songbpm: bool = True):
        """
        Initialise les scrapers et APIs
        
        Args:
            headless_reccobeats: Si True, lance ReccoBeats en mode headless
            headless_songbpm: Si True, lance SongBPM en mode headless (True par défaut)
        """
        self.apis_available = {
            'reccobeats': False,
            'songbpm': False,
            'spotify_id': False,  # NOUVEAU: Ajout du scraper Spotify_ID
            'discogs': False
        }
        
        # Initialiser ReccoBeats (pas de clé API nécessaire)
        self.reccobeats_client = None
        try:
            self.reccobeats_client = ReccoBeatsIntegratedClient(headless=headless_reccobeats)
            # Vider le cache des erreurs précédentes si nécessaire
            if hasattr(self.reccobeats_client, 'clear_old_errors'):
                self.reccobeats_client.clear_old_errors()
            self.apis_available['reccobeats'] = True
            logger.info("✅ ReccoBeats client initialisé")
        except Exception as e:
            logger.error(f"❌ Erreur init ReccoBeats: {e}")
        
        # Initialiser SongBPM scraper
        self.songbpm_scraper = None
        try:
            self.songbpm_scraper = SongBPMScraper(headless=headless_songbpm)
            self.apis_available['songbpm'] = True
            logger.info("✅ SongBPM scraper initialisé (Selenium)")
        except Exception as e:
            logger.error(f"❌ Erreur init SongBPM: {e}")
        
        # NOUVEAU: Initialiser Spotify ID scraper
        self.spotify_id_scraper = None
        try:
            self.spotify_id_scraper = SpotifyIDScraper()
            self.apis_available['spotify_id'] = True
            logger.info("✅ Spotify ID scraper initialisé")
        except Exception as e:
            logger.error(f"❌ Erreur init Spotify ID scraper: {e}")
        
        # Discogs n'est pas implémenté
        self.apis_available['discogs'] = False
        
        logger.info(f"Sources disponibles: {[k for k, v in self.apis_available.items() if v]}")
    
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
                self.songbpm_scraper.close()
                logger.info("SongBPM scraper fermé")
            except Exception as e:
                logger.error(f"Erreur fermeture SongBPM: {e}")
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    def __del__(self):
        """Cleanup des ressources"""
        try:
            self.close()
        except:
            pass
    
    # ================== NOUVEAU: VALIDATION SPOTIFY ID ==================
    
    def validate_spotify_id_unique(self, spotify_id: str, current_track: Track, artist_tracks: List[Track]) -> bool:
        """
        Vérifie qu'un Spotify ID n'est pas déjà utilisé par un autre track
        
        Args:
            spotify_id: L'ID Spotify à vérifier
            current_track: Le track actuel (pour l'exclure de la vérification)
            artist_tracks: Liste de tous les tracks de l'artiste
            
        Returns:
            bool: True si l'ID est unique, False s'il est déjà utilisé
        """
        if not spotify_id or not artist_tracks:
            return True
        
        for track in artist_tracks:
            # Ignorer le track actuel
            if track.title == current_track.title:
                continue
            
            # Vérifier si un autre track utilise déjà cet ID
            if hasattr(track, 'spotify_id') and track.spotify_id == spotify_id:
                logger.warning(f"⚠️ SPOTIFY ID DÉJÀ UTILISÉ: {spotify_id}")
                logger.warning(f"   ❌ Déjà attribué à: '{track.title}'")
                logger.warning(f"   🚫 Refus attribution à: '{current_track.title}'")
                return False
        
        return True
    
    def get_unique_spotify_id(self, track: Track, artist_tracks: List[Track], force_scraper: bool = False) -> Optional[str]:
        """
        Récupère un Spotify ID unique pour un track
        Utilise le scraper Spotify_ID pour obtenir le bon ID
        
        Args:
            track: Le track pour lequel trouver un Spotify ID
            artist_tracks: Liste de tous les tracks de l'artiste (pour validation)
            force_scraper: Si True, force l'utilisation du scraper même si un ID existe
            
        Returns:
            Optional[str]: Le Spotify ID unique trouvé, ou None
        """
        if not self.spotify_id_scraper:
            logger.warning("❌ Spotify ID scraper non disponible")
            return None
        
        artist_name = track.artist.name if hasattr(track.artist, 'name') else str(track.artist)
        
        # Si le track a déjà un Spotify ID et qu'on ne force pas, le vérifier
        if not force_scraper and hasattr(track, 'spotify_id') and track.spotify_id:
            if self.validate_spotify_id_unique(track.spotify_id, track, artist_tracks):
                logger.info(f"✅ Spotify ID existant validé: {track.spotify_id}")
                return track.spotify_id
            else:
                logger.warning(f"⚠️ Spotify ID existant invalide (dupliqué), recherche d'un nouveau...")
        
        # Utiliser le scraper Spotify_ID pour obtenir le bon ID
        logger.info(f"🔍 Recherche Spotify ID via scraper pour: '{artist_name}' - '{track.title}'")
        spotify_id = self.spotify_id_scraper.get_spotify_id(artist_name, track.title)
        
        if not spotify_id:
            logger.warning(f"❌ Aucun Spotify ID trouvé via scraper pour '{track.title}'")
            return None
        
        # Valider l'unicité de l'ID trouvé
        if not self.validate_spotify_id_unique(spotify_id, track, artist_tracks):
            logger.error(f"❌ ERREUR: Spotify ID trouvé par scraper est déjà utilisé: {spotify_id}")
            logger.error(f"   Cela ne devrait pas arriver. Vérifiez la base de données.")
            return None
        
        logger.info(f"✅ Spotify ID unique trouvé via scraper: {spotify_id}")
        return spotify_id
    
    # ================== NOUVEAU: NETTOYAGE DES DONNÉES ERRONÉES ==================
    
    def clear_track_data(self, track: Track, clear_spotify_id: bool = False) -> bool:
        """
        Efface les données musicales d'un track (BPM, Key, Mode, Duration, etc.)
        Utile pour nettoyer les données erronées avant un nouvel enrichissement
        
        IMPORTANT: Ne touche QUE aux données musicales, JAMAIS à artist, album, title, etc.
        
        Args:
            track: Le track à nettoyer
            clear_spotify_id: Si True, efface aussi le Spotify ID
            
        Returns:
            bool: True si des données ont été effacées
        """
        cleaned = False
        
        logger.info(f"🗑️ Nettoyage des données MUSICALES UNIQUEMENT pour '{track.title}'")
        
        # VÉRIFICATION DE SÉCURITÉ: S'assurer que les données essentielles existent
        if not hasattr(track, 'title') or not track.title:
            logger.error(f"❌ ERREUR CRITIQUE: Track sans titre détecté! Annulation du nettoyage.")
            return False
        
        if not hasattr(track, 'artist'):
            logger.error(f"❌ ERREUR CRITIQUE: Track '{track.title}' sans artiste! Annulation du nettoyage.")
            return False
        
        # Effacer BPM
        if hasattr(track, 'bpm') and track.bpm is not None:
            old_value = track.bpm
            track.bpm = None
            logger.info(f"   ✅ BPM effacé: {old_value} → None")
            cleaned = True
        
        # Effacer Key
        if hasattr(track, 'key') and track.key is not None:
            old_value = track.key
            track.key = None
            logger.info(f"   ✅ Key effacée: {old_value} → None")
            cleaned = True
        
        # Effacer Mode
        if hasattr(track, 'mode') and track.mode is not None:
            old_value = track.mode
            track.mode = None
            logger.info(f"   ✅ Mode effacé: {old_value} → None")
            cleaned = True
        
        # Effacer Duration
        if hasattr(track, 'duration') and track.duration is not None:
            old_value = track.duration
            track.duration = None
            logger.info(f"   ✅ Duration effacée: {old_value} → None")
            cleaned = True
        
        # Effacer Musical Key (format français)
        if hasattr(track, 'musical_key') and track.musical_key is not None:
            old_value = track.musical_key
            track.musical_key = None
            logger.info(f"   ✅ Musical Key effacée: {old_value} → None")
            cleaned = True
        
        # Effacer Spotify ID (optionnel)
        if clear_spotify_id and hasattr(track, 'spotify_id') and track.spotify_id is not None:
            old_value = track.spotify_id
            track.spotify_id = None
            logger.info(f"   ✅ Spotify ID effacé: {old_value} → None")
            cleaned = True
        
        # VÉRIFICATION POST-NETTOYAGE: S'assurer que les données essentielles sont toujours là
        if not hasattr(track, 'title') or not track.title:
            logger.error(f"❌ ERREUR CRITIQUE POST-NETTOYAGE: Le titre a disparu!")
            return False
        
        if not hasattr(track, 'artist') or not track.artist:
            logger.error(f"❌ ERREUR CRITIQUE POST-NETTOYAGE: L'artiste a disparu!")
            return False
        
        if cleaned:
            logger.info(f"✅ Nettoyage terminé pour '{track.title}' - Artiste intact: {track.artist}")
        else:
            logger.info(f"ℹ️ Aucune donnée à nettoyer pour '{track.title}'")
        
        return cleaned
    
    def clear_multiple_tracks_data(self, tracks: List[Track], clear_spotify_id: bool = False) -> int:
        """
        Nettoie les données de plusieurs tracks
        
        Args:
            tracks: Liste des tracks à nettoyer
            clear_spotify_id: Si True, efface aussi les Spotify IDs
            
        Returns:
            int: Nombre de tracks nettoyés
        """
        cleaned_count = 0
        
        logger.info(f"🗑️ Nettoyage de {len(tracks)} track(s)")
        
        for track in tracks:
            if self.clear_track_data(track, clear_spotify_id=clear_spotify_id):
                cleaned_count += 1
        
        logger.info(f"✅ {cleaned_count}/{len(tracks)} track(s) nettoyé(s)")
        return cleaned_count
    
    # ================================================================
    
    def get_available_sources(self) -> List[str]:
        """Retourne la liste des sources disponibles"""
        return [k for k, v in self.apis_available.items() if v]
    
    def enrich_track(self, track: Track, sources: Optional[List[str]] = None, 
                     force_update: bool = False, artist_tracks: Optional[List[Track]] = None,
                     clear_on_failure: bool = True) -> Dict[str, bool]:
        """
        Enrichit un morceau avec les sources spécifiées
        VERSION CORRIGÉE: Avec validation Spotify ID + nettoyage des données erronées
        
        Args:
            track: Le morceau à enrichir
            sources: Liste des sources à utiliser
            force_update: Si True, met à jour même si les données existent déjà
            artist_tracks: NOUVEAU - Liste de tous les tracks de l'artiste (pour validation Spotify ID)
            clear_on_failure: NOUVEAU - Si True, efface les données erronées quand toutes les sources échouent
            
        Returns:
            Dict avec le statut de chaque source
        """
        if sources is None:
            sources = ['spotify_id', 'reccobeats', 'songbpm']  # MODIFIÉ: spotify_id en premier
        
        results = {}
        
        logger.info(f"🔍 Enrichissement: track='{track.title}', sources={sources}, force_update={force_update}")
        logger.info(f"🔍 État actuel: spotify_id={getattr(track, 'spotify_id', None)}, bpm={getattr(track, 'bpm', None)}")
        
        # NOUVEAU: Sauvegarder l'état initial pour détecter si des données ont été trouvées
        initial_spotify_id = getattr(track, 'spotify_id', None)
        initial_bpm = getattr(track, 'bpm', None)
        
        # NOUVEAU: 0. Spotify ID scraper en PREMIER (si pas de Spotify ID ou si force_update)
        if 'spotify_id' in sources and self.apis_available.get('spotify_id'):
            # Utiliser le scraper si:
            # - Pas de Spotify ID existant OU
            # - Force update demandé OU
            # - Spotify ID existant mais invalide (dupliqué)
            should_use_spotify_scraper = (
                force_update or 
                not hasattr(track, 'spotify_id') or 
                not track.spotify_id or
                (artist_tracks and not self.validate_spotify_id_unique(
                    getattr(track, 'spotify_id', None), track, artist_tracks
                ))
            )
            
            # NOUVEAU: Skip le scraper Spotify_ID si ReccoBeats est disponible et sélectionné
            # ReccoBeats a son propre système de recherche Spotify ID plus fiable
            if should_use_spotify_scraper and 'reccobeats' not in sources:
                try:
                    spotify_id = self.get_unique_spotify_id(track, artist_tracks or [], force_scraper=True)
                    if spotify_id:
                        track.spotify_id = spotify_id
                        logger.info(f"✅ Spotify ID attribué via scraper: {spotify_id}")
                        results['spotify_id'] = True
                    else:
                        logger.warning(f"❌ Échec récupération Spotify ID via scraper")
                        results['spotify_id'] = False
                except Exception as e:
                    logger.error(f"Erreur Spotify ID scraper pour {track.title}: {e}")
                    results['spotify_id'] = False
            elif should_use_spotify_scraper and 'reccobeats' in sources:
                logger.info(f"⏭️ Skip Spotify ID scraper (ReccoBeats le fera)")
                results['spotify_id'] = 'skipped'
        
        # 1. ReccoBeats pour BPM et features audio
        if 'reccobeats' in sources and self.apis_available.get('reccobeats'):
            try:
                # MODIFIÉ: Passer artist_tracks pour validation
                success = self._enrich_with_reccobeats(track, artist_tracks)
                results['reccobeats'] = success
                if success:
                    logger.debug(f"✅ ReccoBeats: BPM={track.bpm}")
            except Exception as e:
                logger.error(f"Erreur ReccoBeats pour {track.title}: {e}")
                results['reccobeats'] = False
        
        # 2. SongBPM scraper
        if 'songbpm' in sources and self.apis_available.get('songbpm'):
            should_use_songbpm = force_update or not track.bpm
            
            if should_use_songbpm:
                try:
                    # MODIFIÉ: Passer artist_tracks pour validation
                    success = self._enrich_with_songbpm(track, force_update=force_update, artist_tracks=artist_tracks)
                    results['songbpm'] = success
                    if success:
                        logger.debug(f"✅ SongBPM: BPM={track.bpm}, Mode={getattr(track, 'mode', 'N/A')}")
                except Exception as e:
                    logger.error(f"❌ Erreur SongBPM pour {track.title}: {e}")
                    results['songbpm'] = False
        
        # NOUVEAU: Nettoyage des données erronées si toutes les sources ont échoué
        if clear_on_failure and force_update:
            all_failed = all(not success for success in results.values())
            
            # Vérifier si aucune nouvelle donnée n'a été trouvée
            new_spotify_id = getattr(track, 'spotify_id', None)
            new_bpm = getattr(track, 'bpm', None)
            
            no_new_data = (
                (new_spotify_id == initial_spotify_id or new_spotify_id is None) and
                (new_bpm == initial_bpm)
            )
            
            if all_failed or no_new_data:
                # Si force_update et aucune source n'a trouvé de nouvelles données,
                # effacer les anciennes données potentiellement erronées
                if force_update and initial_bpm is not None:
                    # VÉRIFICATION DE SÉCURITÉ: Ne jamais toucher aux données essentielles
                    if not hasattr(track, 'title') or not track.title:
                        logger.error(f"❌ ERREUR: Track sans titre, annulation du nettoyage")
                        return results
                    
                    if not hasattr(track, 'artist'):
                        logger.error(f"❌ ERREUR: Track '{track.title}' sans artiste, annulation du nettoyage")
                        return results
                    
                    logger.warning(f"⚠️ NETTOYAGE: Aucune source n'a trouvé de données pour '{track.title}'")
                    logger.warning(f"⚠️ Effacement des anciennes valeurs potentiellement erronées...")
                    logger.info(f"   ℹ️ PROTECTION: Title et Artist seront préservés")
                    
                    # Effacer UNIQUEMENT les données musicales
                    if hasattr(track, 'bpm'):
                        old_bpm = track.bpm
                        track.bpm = None
                        logger.info(f"   🗑️ BPM effacé: {old_bpm} → None")
                    
                    if hasattr(track, 'key'):
                        old_key = track.key
                        track.key = None
                        logger.info(f"   🗑️ Key effacée: {old_key} → None")
                    
                    if hasattr(track, 'mode'):
                        old_mode = track.mode
                        track.mode = None
                        logger.info(f"   🗑️ Mode effacé: {old_mode} → None")
                    
                    if hasattr(track, 'duration'):
                        old_duration = track.duration
                        track.duration = None
                        logger.info(f"   🗑️ Duration effacée: {old_duration} → None")
                    
                    if hasattr(track, 'musical_key'):
                        old_musical_key = track.musical_key
                        track.musical_key = None
                        logger.info(f"   🗑️ Musical Key effacée: {old_musical_key} → None")
                    
                    # VÉRIFICATION POST-NETTOYAGE
                    if not hasattr(track, 'title') or not track.title:
                        logger.error(f"❌ ERREUR CRITIQUE: Le titre a disparu après nettoyage!")
                    elif not hasattr(track, 'artist') or not track.artist:
                        logger.error(f"❌ ERREUR CRITIQUE: L'artiste a disparu après nettoyage!")
                    else:
                        logger.info(f"✅ Données erronées nettoyées pour '{track.title}'")
                        logger.info(f"   ✅ VÉRIFICATION: Artiste intact = {track.artist}")
                        results['cleaned'] = True
        
        return results
    
    def _enrich_with_reccobeats(self, track: Track, artist_tracks: Optional[List[Track]] = None) -> bool:
        """
        Enrichit avec ReccoBeats
        VERSION CORRIGÉE: Avec validation Spotify ID + Compatible Windows
        """
        try:
            if not self.reccobeats_client:
                logger.error("ReccoBeats client non initialisé")
                return False
            
            artist_name = track.artist.name if hasattr(track.artist, 'name') else str(track.artist)
            logger.info(f"ReccoBeats: DÉBUT traitement '{artist_name}' - '{track.title}'")
            
            # Vérifier si le track a déjà un spotify_id
            existing_spotify_id = None
            if hasattr(track, 'spotify_id') and track.spotify_id:
                existing_spotify_id = track.spotify_id
                
                # Valider l'unicité
                if artist_tracks and not self.validate_spotify_id_unique(existing_spotify_id, track, artist_tracks):
                    logger.warning(f"⚠️ Spotify ID existant est un duplicata, il sera ignoré")
                    existing_spotify_id = None
                    track.spotify_id = None
                else:
                    logger.info(f"✅ Spotify ID existant validé: {existing_spotify_id}")
            
            # MODIFIÉ: Timeout compatible Windows
            import platform
            is_windows = platform.system() == 'Windows'
            
            if not is_windows:
                # Sur Unix/Linux, utiliser signal.alarm
                import signal
                
                def timeout_handler(signum, frame):
                    raise TimeoutError("ReccoBeats timeout")
                
                try:
                    signal.signal(signal.SIGALRM, timeout_handler)
                    signal.alarm(60)
                    
                    # Appeler ReccoBeats
                    track_info = self.reccobeats_client.get_track_info(artist_name, track.title)
                    signal.alarm(0)
                    
                except TimeoutError as e:
                    logger.error(f"ReccoBeats: ⏰ TIMEOUT pour '{track.title}': {e}")
                    signal.alarm(0)
                    return False
            else:
                # Sur Windows, pas de timeout (ou utiliser threading.Timer si nécessaire)
                logger.debug("Windows détecté, pas de timeout signal")
                track_info = self.reccobeats_client.get_track_info(artist_name, track.title)
            
            if not track_info or not track_info.get('spotify_id'):
                logger.warning(f"ReccoBeats: ❌ Pas de données pour '{track.title}'")
                return False
            
            logger.debug(f"ReccoBeats: ✅ Données récupérées")
            
            # Stocker l'ID Spotify (avec validation)
            if 'spotify_id' in track_info:
                new_spotify_id = track_info['spotify_id']
                
                # Valider avant de stocker
                if artist_tracks and not self.validate_spotify_id_unique(new_spotify_id, track, artist_tracks):
                    logger.error(f"❌ REJET: Spotify ID de ReccoBeats déjà utilisé: {new_spotify_id}")
                    return False
                
                track.spotify_id = new_spotify_id
                logger.info(f"✅ ID Spotify stocké: {track.spotify_id}")
            
            # Stocker le BPM
            bpm = None
            if 'bpm' in track_info:
                bpm = track_info['bpm']
            elif 'tempo' in track_info:
                bpm = track_info['tempo']
            elif 'audio_features' in track_info:
                features = track_info['audio_features']
                if isinstance(features, dict) and 'tempo' in features:
                    bpm = features['tempo']
            
            if bpm and isinstance(bpm, (int, float)) and 50 <= bpm <= 200:
                track.bpm = round(float(bpm))
                logger.info(f"ReccoBeats: ✅ BPM: {track.bpm}")
            
            # Stocker Key et Mode
            if 'key' in track_info and track_info['key'] is not None:
                track.key = track_info['key']
                logger.info(f"ReccoBeats: ✅ Key: {track.key}")
            
            if 'mode' in track_info and track_info['mode'] is not None:
                track.mode = track_info['mode']
                logger.info(f"ReccoBeats: ✅ Mode: {track.mode}")
            
            has_spotify_id = hasattr(track, 'spotify_id') and track.spotify_id
            has_bpm = hasattr(track, 'bpm') and track.bpm
            
            if has_spotify_id and has_bpm:
                logger.info(f"ReccoBeats: ✅ SUCCÈS COMPLET '{track.title}'")
                return True
            elif has_spotify_id:
                logger.info(f"ReccoBeats: ⚠️ SUCCÈS PARTIEL '{track.title}' - ID mais pas BPM")
                return True
            else:
                logger.warning(f"ReccoBeats: ❌ ÉCHEC '{track.title}'")
                return False
            
        except Exception as e:
            logger.error(f"ReccoBeats: ❌ Erreur générale: {e}")
            return False
    
    def _enrich_with_songbpm(self, track: Track, force_update: bool = False, 
                            artist_tracks: Optional[List[Track]] = None) -> bool:
        """
        Enrichit avec SongBPM scraper
        VERSION CORRIGÉE: Avec validation Spotify ID + Timeout + Featuring
        """
        if not self.songbpm_scraper:
            return False
        
        try:
            # NOUVEAU: Utiliser le bon artiste selon si c'est un featuring
            if hasattr(track, 'is_featuring') and track.is_featuring:
                # Si c'est un featuring, utiliser l'artiste principal
                if hasattr(track, 'primary_artist_name') and track.primary_artist_name:
                    artist_name = track.primary_artist_name
                    logger.info(f"🎤 Featuring détecté, utilisation de l'artiste principal: {artist_name}")
                else:
                    artist_name = track.artist.name if hasattr(track.artist, 'name') else str(track.artist)
            else:
                artist_name = track.artist.name if hasattr(track.artist, 'name') else str(track.artist)
            
            # Extraire le Spotify ID si disponible (et validé)
            spotify_id = getattr(track, 'spotify_id', None)
            
            # Valider l'unicité si un ID existe
            if spotify_id and artist_tracks:
                if not self.validate_spotify_id_unique(spotify_id, track, artist_tracks):
                    logger.warning(f"⚠️ Spotify ID du track est un duplicata, ignoré pour la recherche SongBPM")
                    spotify_id = None
            
            # MODIFIÉ: Timeout réduit à 60 secondes (au lieu de 120)
            import signal
            import platform
            
            is_windows = platform.system() == 'Windows'
            timeout_seconds = 60  # ← Réduit de 120 à 60
            
            track_data = None
            
            if not is_windows:
                # Unix/Linux: utiliser signal.alarm
                def signal_handler(signum, frame):
                    raise TimeoutError(f"SongBPM timeout après {timeout_seconds}s")
                
                old_handler = signal.signal(signal.SIGALRM, signal_handler)
                signal.alarm(timeout_seconds)
                
                try:
                    track_data = self.songbpm_scraper.search_track(
                        track.title, artist_name, spotify_id=spotify_id, fetch_details=True
                    )
                    signal.alarm(0)
                finally:
                    signal.signal(signal.SIGALRM, old_handler)
            else:
                # Windows: utiliser threading.Timer
                import threading
                
                timer_expired = {'value': False}
                
                def timeout_func():
                    timer_expired['value'] = True
                    logger.error(f"⏰ SongBPM timeout après {timeout_seconds}s")
                
                timer = threading.Timer(timeout_seconds, timeout_func)
                timer.start()
                
                try:
                    track_data = self.songbpm_scraper.search_track(
                        track.title, artist_name, spotify_id=spotify_id, fetch_details=True
                    )
                finally:
                    timer.cancel()
                    
                if timer_expired['value']:
                    logger.error(f"❌ SongBPM: Timeout expiré pour '{track.title}'")
                    return False
            
            if not track_data:
                return False
            
            updated = False
            
            # BPM
            if (force_update or not track.bpm) and track_data.get('bpm'):
                track.bpm = track_data['bpm']
                logger.info(f"📊 BPM ajouté depuis SongBPM: {track.bpm} pour {track.title}")
                updated = True
            
            # Key et Mode
            key_value = track_data.get('key')
            mode_value = track_data.get('mode')
            
            if key_value and mode_value:
                if force_update or not hasattr(track, 'key') or not track.key:
                    track.key = key_value
                    logger.info(f"🎵 Key ajoutée depuis SongBPM: {track.key} pour {track.title}")
                    updated = True
                
                if force_update or not hasattr(track, 'mode') or not track.mode:
                    track.mode = mode_value
                    logger.info(f"🎼 Mode ajouté depuis SongBPM: {track.mode} pour {track.title}")
                    updated = True
            
            # Spotify ID depuis SongBPM (avec validation stricte)
            songbpm_spotify_id = track_data.get('spotify_id')
            if songbpm_spotify_id:
                if not hasattr(track, 'spotify_id') or not track.spotify_id:
                    # Valider l'unicité
                    if artist_tracks and self.validate_spotify_id_unique(songbpm_spotify_id, track, artist_tracks):
                        track.spotify_id = songbpm_spotify_id
                        logger.info(f"🎵 Spotify ID ajouté depuis SongBPM: {track.spotify_id}")
                        updated = True
                    else:
                        logger.warning(f"⚠️ REJET: Spotify ID de SongBPM déjà utilisé: {songbpm_spotify_id}")
            
            # Duration
            if (force_update or not hasattr(track, 'duration') or not track.duration) and track_data.get('duration'):
                track.duration = track_data['duration']
                logger.info(f"⏱️ Duration ajoutée depuis SongBPM: {track.duration} pour {track.title}")
                updated = True
            
            return updated
            
        except TimeoutError as e:
            logger.error(f"⏰ SongBPM timeout pour {track.title}: {e}")
            return False
        except Exception as e:
            logger.error(f"Erreur SongBPM pour {track.title}: {e}")
            return False