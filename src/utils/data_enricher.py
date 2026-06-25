"""
Module d'enrichissement des données tracks
VERSION CORRIGÉE: Empêche la duplication des Spotify IDs + Intégration Spotify_ID scraper + GetSongBPM API
"""
from typing import List, Dict, Optional
from src.models import Track
from src.scrapers.songbpm_scraper_v2 import SongBPMScraper
from src.scrapers.spotify_id_scraper_v2 import SpotifyIDScraper
from src.api.reccobeats_api import ReccoBeatsIntegratedClient
from src.api.getsongbpm_api import GetSongBPMFetcher
from src.api.deezer_api import DeezerAPI
from src.api.discogs_api import DiscogsClient
from src.utils.logger import get_logger
import os

logger = get_logger(__name__)


class DataEnricher:
    """Enrichissement des données des morceaux"""
    
    def __init__(self, headless_reccobeats: bool = False, headless_songbpm: bool = True, headless_spotify_scraper: bool = True):
        """
        Initialise les scrapers et APIs
        
        Args:
            headless_reccobeats: Si True, lance ReccoBeats en mode headless
            headless_songbpm: Si True, lance SongBPM en mode headless (True par défaut)
        """
        self.apis_available = {
            'spotify_id': False,  # 1. Scraper Spotify_ID
            'reccobeats': False,  # 2. ReccoBeats
            'getsongbpm': False,  # 3. GetSongBPM API
            'songbpm': False,     # 4. SongBPM scraper
            'deezer': False,      # 5. Deezer API
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

        # Initialiser GetSongBPM API
        self.getsongbpm_fetcher = None
        try:
            self.getsongbpm_fetcher = GetSongBPMFetcher()
            self.apis_available['getsongbpm'] = True
            logger.info("✅ GetSongBPM API initialisée")
        except Exception as e:
            logger.warning(f"⚠️ GetSongBPM non disponible: {e}")

        # Initialiser SongBPM scraper
        self.songbpm_scraper = None
        try:
            self.songbpm_scraper = SongBPMScraper(headless=headless_songbpm)
            self.apis_available['songbpm'] = True
            logger.info("✅ SongBPM scraper initialisé (Selenium)")
        except Exception as e:
            logger.error(f"❌ Erreur init SongBPM: {e}")
        
        # Initialiser Spotify ID scraper
        self.spotify_id_scraper = None
        try:
            self.spotify_id_scraper = SpotifyIDScraper(headless=headless_spotify_scraper)
            self.apis_available['spotify_id'] = True
            logger.info("✅ Spotify ID scraper initialisé")
        except Exception as e:
            logger.error(f"❌ Erreur init Spotify ID scraper: {e}")

        # Initialiser Deezer API
        self.deezer_client = None
        try:
            self.deezer_client = DeezerAPI()
            self.apis_available['deezer'] = True
            logger.info("✅ Deezer API initialisée")
        except Exception as e:
            logger.error(f"❌ Erreur init Deezer API: {e}")

        # Initialiser Discogs API
        self.discogs_client = None
        try:
            # Chercher le token Discogs dans les variables d'environnement
            discogs_token = os.getenv('DISCOGS_TOKEN') or os.getenv('DISCOGS_USER_TOKEN')
            if discogs_token:
                self.discogs_client = DiscogsClient(user_token=discogs_token)
                self.apis_available['discogs'] = True
                logger.info("✅ Discogs API initialisée avec token (60 req/min)")
            else:
                # Initialiser quand même sans token (limité à 25 req/min)
                self.discogs_client = DiscogsClient()
                self.apis_available['discogs'] = True
                logger.info("✅ Discogs API initialisée sans token (25 req/min)")
        except Exception as e:
            logger.warning(f"⚠️ Discogs API non disponible: {e}")
            self.apis_available['discogs'] = False

        # Client Genius (pour les feats : media/album/relations avant ReccoBeats)
        self.genius_client = None
        try:
            from src.api.genius_api import GeniusAPI
            self.genius_client = GeniusAPI()
            logger.info("✅ Genius API initialisée (media feats)")
        except Exception as e:
            logger.debug(f"Genius API non disponible dans l'enricher: {e}")

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
    
    def validate_spotify_id_unique(self, spotify_id: str, current_track: Track, 
                                   artist_tracks: List[Track]) -> bool:
        """
        Valide qu'un Spotify ID n'est pas utilisé par un AUTRE titre
        VERSION AMÉLIORÉE: Accepte plusieurs IDs pour le MÊME titre
        """
        if not spotify_id or not artist_tracks:
            return True
        
        current_title_normalized = self._normalize_title(current_track.title)
        
        for track in artist_tracks:
            # Récupérer tous les IDs de ce track
            if hasattr(track, 'get_all_spotify_ids'):
                track_ids = track.get_all_spotify_ids()
            elif hasattr(track, 'spotify_id') and track.spotify_id:
                track_ids = [track.spotify_id]
            else:
                track_ids = []
            
            # Vérifier si cet ID est déjà utilisé
            if spotify_id in track_ids:
                track_title_normalized = self._normalize_title(track.title)
                
                # ✅ C'est le MÊME morceau : OK
                if track_title_normalized == current_title_normalized:
                    logger.info(f"✅ ID déjà utilisé par le même titre (version alternative)")
                    return True
                
                # ❌ C'est un AUTRE morceau : REJET
                else:
                    logger.warning(f"❌ ID déjà utilisé par un autre titre: '{track.title}'")
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
    
    def _normalize_title(self, title: str) -> str:
        """
        Normalise un titre pour comparaison
        """
        import unicodedata
        import re
        
        title = title.lower()
        
        # Supprimer les accents
        title = ''.join(
            c for c in unicodedata.normalize('NFD', title)
            if unicodedata.category(c) != 'Mn'
        )
        
        # Supprimer feat., parenthèses, etc.
        title = re.sub(r'\(.*?\)', '', title)
        title = re.sub(r'\[.*?\]', '', title)
        title = re.sub(r'feat\..*$', '', title, flags=re.IGNORECASE)
        title = re.sub(r'ft\..*$', '', title, flags=re.IGNORECASE)
        
        # Supprimer la ponctuation
        title = re.sub(r'[^\w\s]', '', title)
        
        # Supprimer les espaces multiples
        title = ' '.join(title.split())
        
        return title.strip()

    # ================================================================
    
    def get_available_sources(self) -> List[str]:
        """Retourne la liste des sources disponibles"""
        return [k for k, v in self.apis_available.items() if v]
    
    def enrich_track(self, track: Track, sources: Optional[List[str]] = None,
                 force_update: bool = False, artist_tracks: Optional[List[Track]] = None,
                 clear_on_failure: bool = True) -> Dict[str, bool]:
        """
        Enrichit un morceau avec les sources spécifiées
        VERSION CORRIGÉE: Avec validation Spotify ID + logs détaillés + fallback SongBPM + Deezer + GetSongBPM + Discogs
        ORDRE: 1. Spotify ID, 2. ReccoBeats, 3. GetSongBPM, 4. SongBPM, 5. Deezer, 6. Discogs
        """
        if sources is None:
            sources = ['spotify_id', 'reccobeats', 'getsongbpm', 'songbpm', 'deezer', 'discogs']
        
        results = {}
        
        logger.info(f"🔍 Enrichissement: track='{track.title}', sources={sources}, force_update={force_update}")
        logger.info(f"🔍 État actuel: spotify_id={getattr(track, 'spotify_id', None)}, bpm={getattr(track, 'bpm', None)}")
        
        # Sauvegarder l'état initial
        initial_spotify_id = getattr(track, 'spotify_id', None)
        initial_bpm = getattr(track, 'bpm', None)

        # Candidats BPM collectés par chaque source → vote final (_finalize_bpm)
        track._bpm_candidates = []

        # ========================================
        # FEATS : media/album/relations via API Genius AVANT ReccoBeats
        # (le Spotify ID Genius fiabilise la chaîne ; 1 appel/feat espace les requêtes).
        # Les primaires ont déjà été traités à l'import (_prefill_via_song_api).
        # ========================================
        if (getattr(track, 'is_featuring', False) and self.genius_client
                and getattr(track, 'genius_id', None)
                and (not getattr(track, 'spotify_id', None)
                     or not getattr(track, 'relationships', None))):
            try:
                if self.genius_client.apply_song_metadata(track):
                    logger.info(
                        f"🎫 Genius (feat) '{track.title}' : Spotify={getattr(track, 'spotify_id', None)}, "
                        f"relations={len(getattr(track, 'relationships', []) or [])}"
                    )
            except Exception as e:
                logger.debug(f"Genius media feat échec: {e}")

        # ========================================
        # VOIE ISRC PRIORITAIRE (avant tout scrape Playwright)
        # Si l'ISRC fournit BPM/Key via ReccoBeats, on évite le scraper Spotify.
        # ========================================
        isrc_ok = False
        if 'reccobeats' in sources and self.apis_available.get('reccobeats'):
            try:
                isrc_ok = self._try_reccobeats_by_isrc(track)
                if isrc_ok:
                    logger.info(f"⚡ ISRC a fourni les données audio pour '{track.title}' → scrape Spotify évité")
            except Exception as e:
                logger.debug(f"Voie ISRC échec: {e}")

        # ========================================
        # 0. SCRAPER SPOTIFY ID
        # ========================================
        if 'spotify_id' in sources and self.apis_available.get('spotify_id'):
            # Ne skip que si on a déjà un ID valide ET que force_update=False
            has_valid_id = (
                hasattr(track, 'spotify_id') and
                track.spotify_id and
                (not artist_tracks or self.validate_spotify_id_unique(track.spotify_id, track, artist_tracks))
            )

            # Si l'ISRC a déjà fourni les données audio, inutile de scraper Spotify
            should_use_spotify_scraper = (force_update or not has_valid_id) and not isrc_ok
            
            if should_use_spotify_scraper:
                logger.info(f"🎯 Appel du scraper Spotify ID pour '{track.title}' (force_update={force_update}, has_valid_id={has_valid_id})")
                try:
                    spotify_id = self.get_unique_spotify_id(track, artist_tracks or [], force_scraper=True)
                    if spotify_id:
                        track.spotify_id = spotify_id
                        logger.info(f"✅ Spotify ID attribué via scraper: {spotify_id}")
                        results['spotify_id'] = True

                        # Récupérer le titre de la page Spotify pour vérification
                        if self.spotify_id_scraper:
                            try:
                                page_title = self.spotify_id_scraper.get_spotify_page_title(spotify_id)
                                if page_title:
                                    track.spotify_page_title = page_title
                                    logger.info(f"📄 Titre de page Spotify: {page_title[:50]}...")
                            except Exception as e:
                                logger.debug(f"Impossible de récupérer le titre de page: {e}")
                    else:
                        logger.warning(f"❌ Échec récupération Spotify ID via scraper")
                        results['spotify_id'] = False
                except Exception as e:
                    logger.error(f"Erreur Spotify ID scraper pour {track.title}: {e}")
                    results['spotify_id'] = False
            else:
                logger.info(f"⏭️ Scraper Spotify ID non nécessaire (ID déjà présent et valide: {track.spotify_id})")
                results['spotify_id'] = 'not_needed'
        
        # ========================================
        # 1. RECCOBEATS
        # ========================================
        reccobeats_success = False
        if 'reccobeats' in sources and self.apis_available.get('reccobeats'):
            if isrc_ok:
                # Déjà satisfait par la voie ISRC en amont : pas de second appel
                reccobeats_success = True
                results['reccobeats'] = True
                logger.info(f"✅ ReccoBeats déjà satisfait via ISRC (BPM={getattr(track, 'bpm', 'N/A')})")
            else:
                logger.info(f"🎵 Appel de ReccoBeats pour '{track.title}'")
                try:
                    # skip_isrc=True : voie ISRC déjà tentée en amont.
                    # allow_spotify_scrape=False si l'étape 0 (source 'spotify_id') a déjà
                    # scrapé → évite un double scrape Playwright par morceau.
                    reccobeats_success = self._enrich_with_reccobeats(
                        track, artist_tracks,
                        skip_isrc=True,
                        allow_spotify_scrape=('spotify_id' not in sources)
                    )
                    results['reccobeats'] = reccobeats_success

                    if reccobeats_success:
                        logger.info(f"✅ ReccoBeats SUCCÈS: BPM={getattr(track, 'bpm', 'N/A')}, Spotify ID={getattr(track, 'spotify_id', 'N/A')}")
                    else:
                        logger.warning(f"❌ ReccoBeats ÉCHEC pour '{track.title}' - On tentera GetSongBPM en fallback")
                except Exception as e:
                    logger.error(f"❌ Erreur ReccoBeats pour {track.title}: {e}")
                    results['reccobeats'] = False
                    reccobeats_success = False

        # ========================================
        # 2. GETSONGBPM API
        # ========================================
        getsongbpm_success = False
        if 'getsongbpm' in sources and self.apis_available.get('getsongbpm'):
            # Utiliser GetSongBPM si :
            # - force_update OU
            # - pas de BPM OU
            # - ReccoBeats a échoué OU
            # - Données manquantes (key, mode)

            missing_bpm = not hasattr(track, 'bpm') or not track.bpm
            missing_key = not hasattr(track, 'key') or track.key is None
            missing_mode = not hasattr(track, 'mode') or track.mode is None

            has_missing_data = missing_bpm or missing_key or missing_mode

            # §8.3 : GetSongBPM est une API gratuite/rapide → on l'appelle TOUJOURS
            # pour fournir un 2ᵉ vote BPM (même si ReccoBeats a déjà répondu).
            should_use_getsongbpm = True

            if should_use_getsongbpm:
                # Construire le message de raison pour le log
                reasons = []
                if force_update:
                    reasons.append("force_update=True")
                if missing_bpm:
                    reasons.append("no_bpm")
                if not reccobeats_success and 'reccobeats' in sources:
                    reasons.append("reccobeats_failed")
                if has_missing_data and not missing_bpm:
                    missing_items = []
                    if missing_key:
                        missing_items.append("key")
                    if missing_mode:
                        missing_items.append("mode")
                    reasons.append(f"missing_data={','.join(missing_items)}")

                reason_str = ", ".join(reasons)
                logger.info(f"🎼 Appel de GetSongBPM pour '{track.title}' (raison: {reason_str})")

                try:
                    getsongbpm_success = self._enrich_with_getsongbpm(track, force_update=force_update)
                    results['getsongbpm'] = getsongbpm_success

                    if getsongbpm_success:
                        logger.info(f"✅ GetSongBPM SUCCÈS: BPM={getattr(track, 'bpm', 'N/A')}, Key={getattr(track, 'key', 'N/A')}, Mode={getattr(track, 'mode', 'N/A')}")
                    else:
                        logger.warning(f"❌ GetSongBPM ÉCHEC pour '{track.title}' - On tentera SongBPM scraper")
                except Exception as e:
                    logger.error(f"❌ Erreur GetSongBPM pour {track.title}: {e}")
                    results['getsongbpm'] = False
                    getsongbpm_success = False
            else:
                logger.info(f"⏭️ GetSongBPM non appelé (données déjà présentes: BPM={getattr(track, 'bpm', 'N/A')}, Key={getattr(track, 'key', 'N/A')}, Mode={getattr(track, 'mode', 'N/A')})")
                results['getsongbpm'] = 'not_needed'

        # ========================================
        # 3. SONGBPM SCRAPER (avec amélioration de la logique)
        # ========================================
        if 'songbpm' in sources and self.apis_available.get('songbpm'):
            # ⭐ LOGIQUE AMÉLIORÉE : Utiliser SongBPM si :
            # - force_update OU
            # - pas de BPM OU
            # - ReccoBeats ET GetSongBPM ont échoué OU
            # - Données manquantes (key, mode, duration)

            # Vérifier si des données sont manquantes
            missing_key = not hasattr(track, 'key') or track.key is None
            missing_mode = not hasattr(track, 'mode') or track.mode is None
            missing_duration = not hasattr(track, 'duration') or not track.duration

            # §8.3 : SongBPM (scrape) = DÉPARTAGE. On l'ouvre seulement si les APIs
            # ne donnent pas de consensus BPM, ou s'il manque key/mode/duration.
            bpm_consensus = self._bpm_consensus_reached(track)

            should_use_songbpm = (
                force_update or
                not bpm_consensus or
                missing_key or missing_mode or missing_duration
            )

            if should_use_songbpm:
                # Construire le message de raison pour le log
                reasons = []
                if force_update:
                    reasons.append("force_update=True")
                if not bpm_consensus:
                    reasons.append("pas_de_consensus_bpm")
                missing_items = []
                if missing_key:
                    missing_items.append("key")
                if missing_mode:
                    missing_items.append("mode")
                if missing_duration:
                    missing_items.append("duration")
                if missing_items:
                    reasons.append(f"missing_data={','.join(missing_items)}")

                reason_str = ", ".join(reasons)
                logger.info(f"🎼 Appel de SongBPM (départage) pour '{track.title}' (raison: {reason_str})")
                
                try:
                    songbpm_success = self._enrich_with_songbpm(track, force_update=force_update, artist_tracks=artist_tracks)
                    results['songbpm'] = songbpm_success

                    if songbpm_success:
                        logger.info(f"✅ SongBPM SUCCÈS: BPM={track.bpm}, Key={getattr(track, 'key', 'N/A')}, Mode={getattr(track, 'mode', 'N/A')}, Duration={getattr(track, 'duration', 'N/A')}")
                    else:
                        logger.warning(f"❌ SongBPM ÉCHEC pour '{track.title}'")
                except Exception as e:
                    logger.error(f"❌ Erreur/Timeout SongBPM pour {track.title}: {e}")
                    # Utiliser None pour indiquer un crash/timeout (différent de False = pas de données)
                    results['songbpm'] = None
            else:
                logger.info(f"⏭️ SongBPM non appelé (toutes les données déjà présentes: BPM={track.bpm}, Key={getattr(track, 'key', 'N/A')}, Mode={getattr(track, 'mode', 'N/A')}, Duration={getattr(track, 'duration', 'N/A')})")
                results['songbpm'] = 'not_needed'

        # ========================================
        # 4. DEEZER API
        # ========================================
        if 'deezer' in sources and self.apis_available.get('deezer'):
            # Appeler Deezer pour vérification et enrichissement complémentaire
            logger.info(f"🎵 Appel de Deezer API pour '{track.title}'")
            try:
                deezer_success = self._enrich_with_deezer(track, force_update=force_update)
                results['deezer'] = deezer_success

                if deezer_success:
                    logger.info(f"✅ Deezer SUCCÈS pour '{track.title}'")
                else:
                    logger.warning(f"❌ Deezer ÉCHEC pour '{track.title}'")
            except Exception as e:
                logger.error(f"❌ Erreur Deezer pour {track.title}: {e}")
                results['deezer'] = False

        # ========================================
        # VOTE BPM : réconciliation de tous les candidats (§8.3)
        # ========================================
        self._finalize_bpm(track)

        # ========================================
        # 5. DISCOGS API (CRÉDITS COMPLÉMENTAIRES)
        # ========================================
        if 'discogs' in sources and self.apis_available.get('discogs'):
            logger.info(f"💿 Appel de Discogs API pour '{track.title}'")
            try:
                discogs_success = self.discogs_client.enrich_track_data(track, force_update=force_update)
                results['discogs'] = discogs_success

                if discogs_success:
                    logger.info(f"✅ Discogs SUCCÈS pour '{track.title}' - {len(track.credits)} crédits au total")
                else:
                    logger.warning(f"❌ Discogs ÉCHEC pour '{track.title}'")
            except Exception as e:
                logger.error(f"❌ Erreur Discogs pour {track.title}: {e}")
                results['discogs'] = False

        # ========================================
        # 6. NETTOYAGE SI ÉCHEC COMPLET
        # ========================================
        if clear_on_failure and force_update:
            # Vérifier si toutes les sources ont échoué
            all_failed = all(
                result is False 
                for result in results.values() 
                if result not in ['skipped', 'not_needed']
            )
            
            # Vérifier si aucune nouvelle donnée n'a été trouvée
            new_spotify_id = getattr(track, 'spotify_id', None)
            new_bpm = getattr(track, 'bpm', None)
            
            no_new_data = (
                (new_spotify_id == initial_spotify_id or new_spotify_id is None) and
                (new_bpm == initial_bpm or new_bpm is None)
            )
            
            if all_failed or no_new_data:
                if force_update and initial_bpm is not None:
                    # Vérifications de sécurité
                    if not hasattr(track, 'title') or not track.title:
                        logger.error(f"❌ ERREUR: Track sans titre, annulation du nettoyage")
                        return results
                    
                    if not hasattr(track, 'artist'):
                        logger.error(f"❌ ERREUR: Track '{track.title}' sans artiste, annulation du nettoyage")
                        return results
                    
                    logger.warning(f"⚠️ NETTOYAGE: Aucune source n'a trouvé de données pour '{track.title}'")
                    logger.warning(f"⚠️ Effacement des anciennes valeurs potentiellement erronées...")
                    
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
                    
                    # Vérification post-nettoyage
                    if not hasattr(track, 'title') or not track.title:
                        logger.error(f"❌ ERREUR CRITIQUE: Le titre a disparu après nettoyage!")
                    elif not hasattr(track, 'artist') or not track.artist:
                        logger.error(f"❌ ERREUR CRITIQUE: L'artiste a disparu après nettoyage!")
                    else:
                        logger.info(f"✅ Données erronées nettoyées pour '{track.title}'")
                        results['cleaned'] = True
        
        # ========================================
        # RÉSUMÉ FINAL
        # ========================================
        logger.info(f"📊 RÉSUMÉ enrichissement '{track.title}':")
        logger.info(f"   • Résultats: {results}")
        logger.info(f"   • Spotify ID: {getattr(track, 'spotify_id', 'N/A')}")
        logger.info(f"   • BPM: {getattr(track, 'bpm', 'N/A')}")
        logger.info(f"   • Key: {getattr(track, 'key', 'N/A')}, Mode: {getattr(track, 'mode', 'N/A')}")
        logger.info(f"   • Musical Key: {getattr(track, 'musical_key', 'N/A')}")
        logger.info(f"   • Duration: {getattr(track, 'duration', 'N/A')}")
        logger.info(f"   • Release Date: {getattr(track, 'release_date', 'N/A')}")
        logger.info(f"   • Deezer ID: {getattr(track, 'deezer_id', 'N/A')}")
        logger.info(f"   • Discogs ID: {getattr(track, 'discogs_id', 'N/A')}")
        logger.info(f"   • Crédits totaux: {len(track.credits) if hasattr(track, 'credits') else 0}")

        return results
    
    # ──────────────────────────────────────────────────────────────────────
    # Réconciliation BPM (§8.2 borne unique + §8.3 vote demi/double)
    # ──────────────────────────────────────────────────────────────────────
    _BPM_SOURCE_RANK = {'reccobeats': 3, 'getsongbpm': 2, 'songbpm': 1, 'deezer': 0}

    @staticmethod
    def _sanitize_bpm(value):
        """Cast en int + borne unique 40–220. None si invalide/hors borne."""
        try:
            v = int(round(float(value)))
        except (ValueError, TypeError):
            return None
        return v if 40 <= v <= 220 else None

    def _add_bpm_candidate(self, track, source: str, raw):
        """Enregistre un candidat BPM (sanitizé) pour le vote final."""
        v = self._sanitize_bpm(raw)
        if v is None:
            return
        if not getattr(track, '_bpm_candidates', None):
            track._bpm_candidates = []
        track._bpm_candidates.append((source, v))
        logger.debug(f"🎚️ Candidat BPM: {source}={v}")

    @staticmethod
    def _bpm_agree(a: int, b: int, tol: int = 3) -> bool:
        """Concordance à la tolérance près, demi/double inclus (71 ≡ 142)."""
        return abs(a - b) <= tol or abs(a - 2 * b) <= tol or abs(2 * a - b) <= tol

    # Seuil sous lequel un BPM ISOLÉ (1 seule source) est considéré half-time
    # et remonté en double-time (logique prod rap & co.). N'agit PAS quand
    # plusieurs sources concordent ou qu'une source confirme déjà le double.
    _BPM_HALFTIME_THRESHOLD = 90

    def _reconcile_bpm(self, candidates):
        """
        (bpm_real, bpm_alt, 'src1+src2', confidence) à partir des candidats.
        bpm_real = octave retenue (double-time à l'export) ; bpm_alt = autre octave.

        Résolution demi/double par ÉVIDENCE (pas de seuil aveugle) :
          1. Deux octaves dans le cluster (74 + 145) → une source confirme le
             double → on garde la valeur HAUTE réellement mesurée.
          2. Une seule octave, ≥2 sources d'accord (88 + 88) → consensus = vrai
             tempo → on NE double PAS.
          3. Une seule octave, 1 source sous le seuil (71 seul) → aucune preuve
             → convention rap : on double (71 → 142).
        """
        if not candidates:
            return (None, None, None, 0)
        clusters = []
        for cand in candidates:
            for cl in clusters:
                if any(self._bpm_agree(cand[1], m[1]) for m in cl):
                    cl.append(cand)
                    break
            else:
                clusters.append([cand])
        # Meilleur cluster : d'abord la taille (vote), puis la source la plus fiable
        def rank(cl):
            return (len(cl), max(self._BPM_SOURCE_RANK.get(s, 0) for s, _ in cl))
        best = max(clusters, key=rank)
        conf = len(best)
        srcs = sorted({s for s, _ in best}, key=lambda s: -self._BPM_SOURCE_RANK.get(s, 0))
        th = self._BPM_HALFTIME_THRESHOLD

        vals = [v for _, v in best]
        lo, hi = min(vals), max(vals)

        if hi >= lo * 1.5:
            # (1) Deux octaves : double confirmé → valeur haute la plus fiable
            high = [(s, v) for s, v in best if v >= lo * 1.5]
            bpm_real = max(high, key=lambda sb: self._BPM_SOURCE_RANK.get(sb[0], 0))[1]
            bpm_alt = lo
        else:
            # Une seule octave : valeur de la source la plus fiable
            V = max(best, key=lambda sb: self._BPM_SOURCE_RANK.get(sb[0], 0))[1]
            if conf < 2 and V < th and V * 2 <= 220:
                # (3) half-time isolé, aucune confirmation → on double
                bpm_real, bpm_alt = V * 2, V
            else:
                # (2) consensus, ou déjà bande haute → on garde V
                bpm_real = V
                if V < th and V * 2 <= 220:
                    bpm_alt = V * 2           # ex. 88 (consensus) → alt 176
                else:
                    half = V // 2
                    bpm_alt = half if half >= 55 else None
        return (bpm_real, bpm_alt, "+".join(srcs), conf)

    def _bpm_consensus_reached(self, track) -> bool:
        """True si ≥2 candidats concordent déjà (→ pas besoin du scrape SongBPM)."""
        cands = getattr(track, '_bpm_candidates', None) or []
        if len(cands) < 2:
            return False
        return self._reconcile_bpm(cands)[3] >= 2

    def _finalize_bpm(self, track):
        """Pose le BPM final : bpm (octave réelle) + bpm_alt + source + confiance."""
        cands = getattr(track, '_bpm_candidates', None) or []
        bpm, bpm_alt, src, conf = self._reconcile_bpm(cands)
        if bpm is not None:
            track.bpm = bpm
            track.bpm_alt = bpm_alt
            track.bpm_source = src
            track.bpm_confidence = conf
            alt_str = f" (alt half-time: {bpm_alt})" if bpm_alt else ""
            logger.info(f"🧮 BPM réconcilié: {bpm}{alt_str} (source(s): {src}, confiance: {conf} | candidats: {cands})")
        track._bpm_candidates = []

    def _apply_reccobeats_result(self, track: Track, track_info: Dict, resolution: Optional[str] = None) -> bool:
        """
        Applique au track les données d'un result ReccoBeats (bpm/key/mode/
        musical_key/duration), de façon non destructive pour la durée.

        Args:
            resolution: 'isrc' ou 'spotify_id' — voie de résolution (debug).

        Returns:
            bool: True si au moins le BPM ou la Key a été posé.
        """
        applied = False
        if resolution:
            track.reccobeats_resolution = resolution

        # BPM → candidat pour le vote (+ pose provisoire si manquant)
        bpm = track_info.get('bpm')
        if bpm is None and isinstance(track_info.get('audio_features'), dict):
            bpm = track_info['audio_features'].get('tempo')
        sbpm = self._sanitize_bpm(bpm)
        if sbpm is not None:
            self._add_bpm_candidate(track, 'reccobeats', sbpm)
            if not getattr(track, 'bpm', None):
                track.bpm = sbpm
            applied = True

        # Key / Mode
        if track_info.get('key') is not None:
            track.key = track_info['key']
            track.key_mode_source = 'reccobeats'
            applied = True
        if track_info.get('mode') is not None:
            track.mode = track_info['mode']
            track.key_mode_source = 'reccobeats'

        if getattr(track, 'key', None) is not None and getattr(track, 'mode', None) is not None:
            try:
                from src.utils.music_theory import key_mode_to_french
                track.musical_key = key_mode_to_french(track.key, track.mode)
            except Exception as e:
                logger.warning(f"⚠️ Erreur conversion musical_key: {e}")

        # Durée (ne pas écraser une durée déjà présente)
        dur = track_info.get('duration')
        if isinstance(dur, (int, float)) and dur > 0 and not getattr(track, 'duration', None):
            track.duration = int(dur)

        return applied

    def _try_reccobeats_by_isrc(self, track: Track, artist_name: Optional[str] = None) -> bool:
        """
        Voie ISRC : récupère l'ISRC (track.isrc ou Deezer) puis interroge
        ReccoBeats SANS scraper de Spotify ID. Applique BPM/Key/Mode au track.

        Returns:
            bool: True si des audio features ont été appliquées (BPM/Key).
        """
        if not self.reccobeats_client:
            return False

        if artist_name is None:
            if getattr(track, 'is_featuring', False) and getattr(track, 'primary_artist_name', None):
                artist_name = track.primary_artist_name
            else:
                artist_name = track.artist.name if hasattr(track.artist, 'name') else str(track.artist)

        isrc = getattr(track, 'isrc', None)
        if not isrc and self.deezer_client:
            try:
                isrc = self.deezer_client.get_isrc(artist_name, track.title)
                if isrc:
                    track.isrc = isrc
                    logger.info(f"🔑 ISRC récupéré via Deezer: {isrc}")
            except Exception as e:
                logger.debug(f"Deezer get_isrc échec: {e}")

        if not isrc:
            return False

        try:
            info = self.reccobeats_client.get_track_info_by_isrc(isrc)
        except Exception as e:
            logger.error(f"❌ ReccoBeats ISRC API: {e}")
            info = None

        if info and info.get('success') and self._apply_reccobeats_result(track, info, resolution='isrc'):
            logger.info(f"ReccoBeats: ✅ SUCCÈS via ISRC pour '{track.title}' (scrape Spotify évité)")
            return True

        logger.info(f"ReccoBeats: ISRC sans audio-features pour '{track.title}' → fallback Spotify ID")
        return False

    def _enrich_with_reccobeats(self, track: Track, artist_tracks: Optional[List[Track]] = None,
                                skip_isrc: bool = False, allow_spotify_scrape: bool = True) -> bool:
        """
        Enrichit avec ReccoBeats
        VERSION SÉPARÉE: ISRC (prioritaire) → SpotifyIDScraper → ReccoBeatsAPI
        skip_isrc=True quand la voie ISRC a déjà été tentée en amont (enrich_track).
        allow_spotify_scrape=False quand l'étape 0 d'enrich_track a déjà scrapé le
        Spotify ID (évite un DOUBLE scrape Playwright par morceau).
        """
        try:
            if not self.reccobeats_client:
                logger.error("ReccoBeats client non initialisé")
                return False

            # Déterminer l'artiste (gestion featurings)
            if hasattr(track, 'is_featuring') and track.is_featuring:
                if hasattr(track, 'primary_artist_name') and track.primary_artist_name:
                    artist_name = track.primary_artist_name
                    logger.info(f"🎤 Featuring détecté, artiste principal: {artist_name}")
                else:
                    artist_name = track.artist.name if hasattr(track.artist, 'name') else str(track.artist)
            else:
                artist_name = track.artist.name if hasattr(track.artist, 'name') else str(track.artist)

            logger.info(f"ReccoBeats: DÉBUT traitement '{artist_name}' - '{track.title}'")

            # ============================================================
            # VOIE PRIORITAIRE : ISRC (sauf si déjà tentée en amont par enrich_track)
            # ============================================================
            if not skip_isrc and self._try_reccobeats_by_isrc(track, artist_name):
                return True

            # ============================================================
            # ÉTAPE 1: RÉCUPÉRER LE SPOTIFY ID (fallback si pas d'ISRC exploitable)
            # ============================================================
            spotify_id = None

            # 1a. Vérifier si le track a déjà un Spotify ID validé
            if hasattr(track, 'spotify_id') and track.spotify_id:
                # Valider l'unicité
                if artist_tracks and self.validate_spotify_id_unique(track.spotify_id, track, artist_tracks):
                    spotify_id = track.spotify_id
                    logger.info(f"✅ Spotify ID existant validé: {spotify_id}")
                else:
                    logger.warning(f"⚠️ Spotify ID existant est un duplicata, il sera ignoré")
                    track.spotify_id = None

            # 1b. Si pas d'ID, utiliser SpotifyIDScraper (sauf si l'étape 0 l'a déjà fait)
            if not spotify_id and not allow_spotify_scrape:
                logger.info(f"⏭️ ReccoBeats: scrape Spotify déjà tenté à l'étape 0 → pas de second scrape")
            if not spotify_id and allow_spotify_scrape and self.spotify_id_scraper:
                logger.info(f"🔍 Appel SpotifyIDScraper pour '{artist_name}' - '{track.title}'")
                try:
                    spotify_id = self.spotify_id_scraper.get_spotify_id(artist_name, track.title)

                    if spotify_id:
                        # Valider l'unicité
                        if artist_tracks and not self.validate_spotify_id_unique(spotify_id, track, artist_tracks):
                            logger.error(f"❌ REJET: Spotify ID du scraper déjà utilisé: {spotify_id}")
                            spotify_id = None
                        else:
                            logger.info(f"✅ Spotify ID trouvé par le scraper: {spotify_id}")
                            track.spotify_id = spotify_id

                            # Récupérer le titre de la page Spotify pour vérification
                            try:
                                page_title = self.spotify_id_scraper.get_spotify_page_title(spotify_id)
                                if page_title:
                                    track.spotify_page_title = page_title
                                    logger.info(f"📄 Titre de page Spotify: {page_title[:50]}...")
                            except Exception as e:
                                logger.debug(f"Impossible de récupérer le titre de page: {e}")
                    else:
                        logger.warning(f"❌ SpotifyIDScraper n'a pas trouvé d'ID")

                except Exception as e:
                    logger.error(f"❌ Erreur SpotifyIDScraper: {e}")
                    spotify_id = None

            # 1c. Si toujours pas d'ID, échec
            if not spotify_id:
                logger.warning(f"ReccoBeats: ❌ Aucun Spotify ID disponible pour '{track.title}'")
                return False

            # ============================================================
            # ÉTAPE 2: APPELER RECCOBEATS AVEC L'ID
            # ============================================================
            logger.info(f"🎵 Appel ReccoBeats API avec Spotify ID: {spotify_id}")

            try:
                track_info = self.reccobeats_client.get_track_info(spotify_id)
            except Exception as e:
                logger.error(f"❌ Erreur ReccoBeats API: {e}")
                return False

            if not track_info or not track_info.get('success'):
                logger.warning(f"ReccoBeats: ❌ Pas de données pour ID {spotify_id}")
                return False

            logger.debug(f"ReccoBeats: ✅ Données récupérées")
            track.reccobeats_resolution = 'spotify_id'

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
            
            sbpm = self._sanitize_bpm(bpm)
            if sbpm is not None:
                self._add_bpm_candidate(track, 'reccobeats', sbpm)
                if not getattr(track, 'bpm', None):
                    track.bpm = sbpm
                logger.info(f"ReccoBeats: ✅ BPM: {sbpm}")

            # Stocker Key et Mode
            if 'key' in track_info and track_info['key'] is not None:
                track.key = track_info['key']
                track.key_mode_source = 'reccobeats'
                logger.info(f"ReccoBeats: ✅ Key: {track.key}")

            if 'mode' in track_info and track_info['mode'] is not None:
                track.mode = track_info['mode']
                track.key_mode_source = 'reccobeats'
                logger.info(f"ReccoBeats: ✅ Mode: {track.mode}")

            if hasattr(track, 'key') and hasattr(track, 'mode') and track.key is not None and track.mode is not None:
                try:
                    from src.utils.music_theory import key_mode_to_french
                    track.musical_key = key_mode_to_french(track.key, track.mode)
                    logger.info(f"ReccoBeats: ✅ Musical Key: {track.musical_key}")
                except Exception as e:
                    logger.warning(f"ReccoBeats: ⚠️ Erreur conversion musical_key: {e}")
            
            # Stocker la Durée
            if 'duration' in track_info and track_info['duration'] is not None:
                duration_value = track_info['duration']
                if isinstance(duration_value, (int, float)) and duration_value > 0:
                    track.duration = int(duration_value)
                    logger.info(f"ReccoBeats: ✅ Duration: {track.duration}s")
                else:
                    logger.warning(f"ReccoBeats: ⚠️ Duration invalide: {duration_value}")

            # Mise à jour de la logique de succès
            has_spotify_id = hasattr(track, 'spotify_id') and track.spotify_id
            has_bpm = hasattr(track, 'bpm') and track.bpm
            has_duration = hasattr(track, 'duration') and track.duration  # ⭐ NOUVEAU

            if has_spotify_id and has_bpm:
                logger.info(f"ReccoBeats: ✅ SUCCÈS COMPLET '{track.title}'")
                if has_duration:
                    logger.info(f"ReccoBeats: ✅ Duration également récupérée: {track.duration}s")
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

    def _enrich_with_getsongbpm(self, track: Track, force_update: bool = False) -> bool:
        """
        Enrichit avec GetSongBPM API
        Récupère: BPM, Key, Mode, Time Signature, Danceability, Acousticness
        """
        try:
            if not self.getsongbpm_fetcher:
                logger.debug("GetSongBPM API non disponible")
                return False

            # Déterminer l'artiste (gestion featurings)
            if hasattr(track, 'is_featuring') and track.is_featuring:
                if hasattr(track, 'primary_artist_name') and track.primary_artist_name:
                    artist_name = track.primary_artist_name
                    logger.info(f"🎤 Featuring détecté, artiste principal: {artist_name}")
                else:
                    artist_name = track.artist.name if hasattr(track.artist, 'name') else str(track.artist)
            else:
                artist_name = track.artist.name if hasattr(track.artist, 'name') else str(track.artist)

            logger.info(f"GetSongBPM: DÉBUT traitement '{artist_name}' - '{track.title}'")

            # Appeler l'API
            try:
                song_data = self.getsongbpm_fetcher.fetch_track_bpm(artist_name, track.title)
            except Exception as api_error:
                logger.error(f"GetSongBPM: ❌ Exception API: {api_error}")
                return False

            if song_data.error:
                logger.warning(f"GetSongBPM: ❌ {song_data.error}")
                return False

            # Enrichir le track avec les données GetSongBPM
            updated = False

            # BPM → candidat pour le vote (+ pose provisoire si manquant)
            sbpm = self._sanitize_bpm(song_data.bpm)
            if sbpm is not None:
                self._add_bpm_candidate(track, 'getsongbpm', sbpm)
                if force_update or not track.bpm:
                    track.bpm = sbpm
                    logger.info(f"GetSongBPM: ✅ BPM: {sbpm}")
                    updated = True

            # Key (seulement si pas déjà présent ou force_update)
            if song_data.key and (force_update or not track.key):
                # Convertir la notation anglaise (ex: "F#m") en notation numérique
                try:
                    from src.utils.music_theory import convert_key_to_numeric
                    track.key = convert_key_to_numeric(song_data.key)
                    track.key_mode_source = 'getsongbpm'
                    logger.info(f"GetSongBPM: ✅ Key: {song_data.key} → {track.key}")
                except:
                    logger.debug(f"GetSongBPM: Key brute stockée: {song_data.key}")
                updated = True

            # Mode (seulement si pas déjà présent ou force_update)
            if song_data.mode and (force_update or not track.mode):
                # Convertir "major"/"minor" en 1/0
                track.mode = 1 if song_data.mode == "major" else 0
                track.key_mode_source = 'getsongbpm'
                logger.info(f"GetSongBPM: ✅ Mode: {song_data.mode}")
                updated = True

            # Musical key (calculé depuis Key + Mode)
            if hasattr(track, 'key') and hasattr(track, 'mode') and track.key is not None and track.mode is not None:
                try:
                    from src.utils.music_theory import key_mode_to_french
                    track.musical_key = key_mode_to_french(track.key, track.mode)
                    logger.info(f"GetSongBPM: ✅ Musical Key: {track.musical_key}")
                except Exception as e:
                    logger.debug(f"GetSongBPM: Erreur conversion musical_key: {e}")

            # Time Signature (optionnel)
            if song_data.time_signature:
                track.time_signature = song_data.time_signature
                logger.info(f"GetSongBPM: ✅ Time Signature: {track.time_signature}")
                updated = True

            if updated:
                logger.info(f"GetSongBPM: ✅ SUCCÈS '{track.title}'")
                return True
            else:
                logger.warning(f"GetSongBPM: ⚠️ Aucune donnée nouvelle pour '{track.title}'")
                return False

        except Exception as e:
            logger.error(f"GetSongBPM: ❌ Erreur: {e}")
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
            
            # MODIFIÉ: Timeout réduit à 30 secondes avec arrêt forcé du driver
            import signal
            import platform
            import threading

            is_windows = platform.system() == 'Windows'
            timeout_seconds = 30  # ← Réduit à 30s

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
                # Windows: utiliser threading.Timer avec arrêt forcé du driver
                timer_expired = {'value': False}

                def timeout_func():
                    timer_expired['value'] = True
                    logger.error(f"⏰ SongBPM timeout après {timeout_seconds}s")
                    # ⭐ FORCER la fermeture du driver pour interrompre les requêtes HTTP bloquées
                    try:
                        if self.songbpm_scraper and self.songbpm_scraper.driver:
                            logger.warning("⚠️ Fermeture forcée du driver SongBPM après timeout")
                            self.songbpm_scraper.driver.quit()
                            self.songbpm_scraper.driver = None
                            self.songbpm_scraper.wait = None
                    except Exception as e:
                        logger.debug(f"Erreur fermeture driver: {e}")

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
                    # Le driver a déjà été fermé par timeout_func
                    return False
            
            if not track_data:
                return False
            
            updated = False
            
            # BPM → candidat pour le vote (+ pose provisoire si manquant)
            sbpm = self._sanitize_bpm(track_data.get('bpm'))
            if sbpm is not None:
                self._add_bpm_candidate(track, 'songbpm', sbpm)
                if force_update or not track.bpm:
                    track.bpm = sbpm
                    logger.info(f"📊 BPM ajouté depuis SongBPM: {sbpm} pour {track.title}")
                    updated = True
            
            # Key et Mode
            key_value = track_data.get('key')
            mode_value = track_data.get('mode')
            
            if key_value and mode_value:
                if force_update or not hasattr(track, 'key') or not track.key:
                    track.key = key_value
                    track.key_mode_source = 'songbpm'
                    logger.info(f"🎵 Key ajoutée depuis SongBPM: {track.key} pour {track.title}")
                    updated = True

                if force_update or not hasattr(track, 'mode') or not track.mode:
                    track.mode = mode_value
                    track.key_mode_source = 'songbpm'
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

    def _enrich_with_deezer(self, track: Track, force_update: bool = False) -> bool:
        """
        Enrichit avec Deezer API (4ème enrichisseur)
        Vérifie la cohérence des données avec les enrichissements précédents

        Args:
            track: Le track à enrichir
            force_update: Si True, force la mise à jour même si les données existent

        Returns:
            bool: True si des données ont été enrichies avec succès
        """
        if not self.deezer_client:
            logger.warning("❌ Deezer API non disponible")
            return False

        try:
            # Déterminer l'artiste (gestion des featurings)
            if hasattr(track, 'is_featuring') and track.is_featuring:
                if hasattr(track, 'primary_artist_name') and track.primary_artist_name:
                    artist_name = track.primary_artist_name
                    logger.info(f"🎤 Featuring détecté, utilisation de l'artiste principal: {artist_name}")
                else:
                    artist_name = track.artist.name if hasattr(track.artist, 'name') else str(track.artist)
            else:
                artist_name = track.artist.name if hasattr(track.artist, 'name') else str(track.artist)

            logger.info(f"🎵 Deezer: Recherche pour '{artist_name}' - '{track.title}'")

            # Récupérer les données existantes pour vérification
            previous_duration = getattr(track, 'duration', None)
            scraped_release_date = getattr(track, 'release_date', None)

            # Appeler l'API Deezer avec vérifications
            result = self.deezer_client.enrich_track(
                artist=artist_name,
                title=track.title,
                previous_duration=previous_duration,
                scraped_release_date=scraped_release_date
            )

            if not result['success']:
                logger.warning(f"❌ Deezer: {result.get('error', 'Erreur inconnue')}")
                return False

            data = result['data']
            verifications = result['verifications']
            updated = False

            # Logs des vérifications
            if verifications:
                logger.info("🔍 Deezer: Vérifications:")

                if 'duration' in verifications:
                    dur_check = verifications['duration']
                    if dur_check['is_valid']:
                        if dur_check.get('difference') is not None:
                            logger.info(f"   ✅ Duration cohérente (diff: {dur_check['difference']}s)")
                        else:
                            logger.info(f"   ℹ️ {dur_check['message']}")
                    else:
                        logger.warning(f"   ⚠️ Duration incohérente! {dur_check['message']}")

                if 'release_date' in verifications:
                    date_check = verifications['release_date']
                    if date_check['is_valid']:
                        if date_check.get('dates_match') is True:
                            logger.info(f"   ✅ Release date cohérente")
                        elif date_check.get('dates_match') is False:
                            logger.warning(f"   ⚠️ Release dates différentes: Deezer={date_check.get('deezer_date')} vs Scraping={date_check.get('scraped_date')}")
                        else:
                            logger.info(f"   ℹ️ {date_check['message']}")
                    else:
                        logger.warning(f"   ⚠️ {date_check['message']}")

            # Stocker la Duration si elle est cohérente ou si on force la mise à jour
            if data.get('deezer_duration'):
                duration_check = verifications.get('duration', {})
                should_update_duration = (
                    force_update or
                    not previous_duration or
                    duration_check.get('is_valid', False)
                )

                if should_update_duration:
                    track.duration = data['deezer_duration']
                    logger.info(f"   ✅ Duration mise à jour: {track.duration}s")
                    updated = True
                else:
                    logger.warning(f"   ⚠️ Duration Deezer ignorée (incohérente)")

            # Stocker la Release Date si elle est cohérente ou si on force la mise à jour
            if data.get('deezer_release_date'):
                date_check = verifications.get('release_date', {})
                should_update_date = (
                    force_update or
                    not scraped_release_date or
                    date_check.get('dates_match', False)
                )

                if should_update_date:
                    # Convertir au format utilisé dans la base de données
                    track.release_date = data['deezer_release_date']
                    logger.info(f"   ✅ Release date mise à jour: {track.release_date}")
                    updated = True
                elif date_check.get('dates_match') is False:
                    logger.warning(f"   ⚠️ Release date Deezer ignorée (différente du scraping)")

            # Stocker les métadonnées supplémentaires (toujours, pas de vérification nécessaire)
            if data.get('deezer_track_id'):
                if not hasattr(track, 'deezer_id') or force_update or not track.deezer_id:
                    track.deezer_id = data['deezer_track_id']
                    logger.info(f"   ✅ Deezer ID: {track.deezer_id}")
                    updated = True

            # ISRC : pivot inter-sources (non destructif). Alimente ReccoBeats.
            if data.get('deezer_isrc'):
                if not getattr(track, 'isrc', None) or force_update:
                    track.isrc = data['deezer_isrc']
                    logger.info(f"   ✅ ISRC: {track.isrc}")
                    updated = True

            # BPM Deezer : candidat (souvent absent/0) — vote arbitré par _finalize_bpm
            sbpm = self._sanitize_bpm(data.get('deezer_bpm'))
            if sbpm is not None:
                self._add_bpm_candidate(track, 'deezer', sbpm)
                if not getattr(track, 'bpm', None):
                    track.bpm = sbpm
                    logger.info(f"   ✅ BPM (Deezer, opportuniste): {sbpm}")
                    updated = True

            if data.get('deezer_link'):
                if not hasattr(track, 'deezer_url') or force_update or not track.deezer_url:
                    track.deezer_url = data['deezer_link']
                    logger.info(f"   ✅ Deezer URL: {track.deezer_url}")
                    updated = True

            if data.get('deezer_explicit_lyrics') is not None:
                if not hasattr(track, 'explicit_lyrics') or force_update or track.explicit_lyrics is None:
                    track.explicit_lyrics = data['deezer_explicit_lyrics']
                    logger.info(f"   ✅ Explicit lyrics: {track.explicit_lyrics}")
                    updated = True

            if data.get('deezer_picture'):
                if not hasattr(track, 'deezer_picture_url') or force_update or not track.deezer_picture_url:
                    track.deezer_picture_url = data['deezer_picture']
                    logger.info(f"   ✅ Deezer picture URL stockée")
                    updated = True

            if updated:
                logger.info(f"✅ Deezer: Enrichissement réussi pour '{track.title}'")
            else:
                logger.info(f"ℹ️ Deezer: Aucune nouvelle donnée pour '{track.title}'")

            return updated

        except Exception as e:
            logger.error(f"❌ Deezer: Erreur pour '{track.title}': {e}")
            return False