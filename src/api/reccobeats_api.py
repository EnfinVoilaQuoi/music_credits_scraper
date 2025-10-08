"""
ReccoBeats API Client - Version modulaire propre
Responsabilité UNIQUE : Récupérer les données musicales (BPM, Key, Mode) depuis ReccoBeats
Le scraping Spotify ID est géré par SpotifyIDScraper (module séparé)
"""
import requests
import json
import time
import logging
from typing import Dict, List, Optional

logger = logging.getLogger('ReccoBeatsAPI')


class ReccoBeatsIntegratedClient:
    """Client ReccoBeats pour récupération BPM/Key/Mode/Audio Features"""

    def __init__(self, cache_file: str = "reccobeats_cache.json", headless: bool = False):
        """
        Initialise le client ReccoBeats

        Args:
            cache_file: Fichier de cache JSON
            headless: Paramètre conservé pour compatibilité (non utilisé)
        """
        self.cache_file = cache_file
        self.cache = self._load_cache()

        # Configuration ReccoBeats API
        self.recco_base_url = "https://api.reccobeats.com/v1"
        self.recco_session = requests.Session()
        self.recco_session.headers.update({
            'Accept': 'application/json',
            'User-Agent': 'ReccoBeats-Python-Client/3.0'
        })

        logger.info(f"ReccoBeats client initialisé")

    def _load_cache(self) -> Dict:
        """Charge le cache depuis le fichier"""
        try:
            with open(self.cache_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_cache(self):
        """Sauvegarde le cache"""
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.cache, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Erreur sauvegarde cache: {e}")

    def _get_cache_key(self, spotify_id: str) -> str:
        """Génère une clé de cache basée sur l'ID Spotify"""
        return f"spotify_id::{spotify_id}"

    def get_track_from_reccobeats(self, spotify_id: str) -> Optional[Dict]:
        """
        Récupère les données d'un track depuis ReccoBeats

        Args:
            spotify_id: L'ID Spotify du track

        Returns:
            Dictionnaire avec les données du track ou None
        """
        try:
            url = f"{self.recco_base_url}/track"
            params = {'ids': spotify_id}

            logger.info(f"🎵 ReccoBeats: Requête pour ID {spotify_id}")

            response = self.recco_session.get(url, params=params, timeout=15)

            logger.debug(f"📡 Response: Status {response.status_code}")

            if response.status_code == 200:
                data = response.json()

                # Gérer différents formats de réponse
                track = None

                if isinstance(data, list) and len(data) > 0:
                    track = data[0]
                    logger.info(f"✅ Track trouvé (liste): {track.get('trackTitle', 'N/A')}")
                elif isinstance(data, dict):
                    if 'content' in data:
                        content = data['content']
                        if isinstance(content, list) and len(content) > 0:
                            track = content[0]
                            logger.info(f"✅ Track trouvé (dict.content[0]): {track.get('trackTitle', 'N/A')}")
                        elif isinstance(content, dict):
                            if 'id' in content or 'trackTitle' in content:
                                track = content
                                logger.info(f"✅ Track trouvé (dict.content dict): {track.get('trackTitle', 'N/A')}")
                    elif 'id' in data or 'trackTitle' in data:
                        track = data
                        logger.info(f"✅ Track trouvé (dict direct): {track.get('trackTitle', 'N/A')}")

                return track

            elif response.status_code == 404:
                logger.warning(f"❌ Track {spotify_id} non trouvé (404)")
            elif response.status_code == 429:
                logger.warning("⏰ Rate limit atteint")
            else:
                logger.error(f"❌ Erreur {response.status_code}: {response.text[:200]}")

        except Exception as e:
            logger.error(f"❌ Exception ReccoBeats: {e}")

        return None

    def get_track_audio_features(self, reccobeats_id: str) -> Optional[Dict]:
        """
        Récupère les audio features (BPM, Key, Mode, etc.)

        Args:
            reccobeats_id: L'ID ReccoBeats du track (différent de l'ID Spotify)

        Returns:
            Dictionnaire avec les audio features ou None
        """
        try:
            url = f"{self.recco_base_url}/track/{reccobeats_id}/audio-features"

            logger.debug(f"🎼 Audio features: {url}")

            response = self.recco_session.get(url, timeout=15)

            if response.status_code == 200:
                features = response.json()
                logger.info(f"✅ BPM récupéré: {features.get('tempo', 'N/A')}")
                return features
            else:
                logger.warning(f"❌ Audio features erreur {response.status_code}")

        except Exception as e:
            logger.error(f"❌ Exception audio features: {e}")

        return None

    def get_track_info(self, spotify_id: str, use_cache: bool = True, force_refresh: bool = False) -> Optional[Dict]:
        """
        Récupère les informations complètes d'un track (données + audio features)

        Args:
            spotify_id: L'ID Spotify du track
            use_cache: Utiliser le cache
            force_refresh: Forcer un rafraîchissement

        Returns:
            Dictionnaire avec toutes les données ou None
        """
        logger.info(f"🎵 get_track_info pour Spotify ID: {spotify_id}")

        try:
            cache_key = self._get_cache_key(spotify_id)

            # Force refresh = nettoyer le cache
            if force_refresh and cache_key in self.cache:
                del self.cache[cache_key]
                logger.info(f"Force refresh pour: {spotify_id}")

            # Vérifier le cache
            if use_cache and not force_refresh and cache_key in self.cache:
                cached = self.cache[cache_key]
                if isinstance(cached, dict):
                    has_bpm = cached.get('bpm') is not None or cached.get('tempo') is not None
                    has_audio_features = cached.get('audio_features') is not None

                    if has_bpm or has_audio_features:
                        logger.info(f"✅ Données complètes trouvées dans le cache")
                        return cached

            # Étape 1: Récupérer les données du track
            track_data = self.get_track_from_reccobeats(spotify_id)

            if not track_data:
                logger.warning(f"❌ Aucune donnée ReccoBeats pour {spotify_id}")
                self.cache[cache_key] = {'error': 'not_found', 'timestamp': time.time()}
                self._save_cache()
                return None

            # Réponse de base
            result = {
                'spotify_id': spotify_id,
                'source': 'reccobeats',
                'success': True,
                'timestamp': time.time(),
                **track_data
            }

            # Extraire la durée si disponible
            if 'durationMs' in track_data:
                duration_ms = track_data['durationMs']
                result['duration'] = int(duration_ms / 1000) if duration_ms else None
                logger.info(f"⏱️ Duration: {result['duration']}s ({duration_ms}ms)")

            # Étape 2: Récupérer les audio features
            reccobeats_id = track_data.get('id')
            if reccobeats_id:
                logger.debug(f"🎼 Récupération audio features pour ID: {reccobeats_id}")
                audio_features = self.get_track_audio_features(reccobeats_id)

                if audio_features:
                    result['audio_features'] = audio_features

                    # Extraire BPM, Key et Mode
                    result['bpm'] = audio_features.get('tempo')
                    result['key'] = audio_features.get('key')
                    result['mode'] = audio_features.get('mode')
                    result['energy'] = audio_features.get('energy')
                    result['danceability'] = audio_features.get('danceability')
                    result['valence'] = audio_features.get('valence')

                    # Convertir en musical_key français si possible
                    if result.get('key') is not None and result.get('mode') is not None:
                        try:
                            from src.utils.music_theory import key_mode_to_french
                            result['musical_key'] = key_mode_to_french(
                                result['key'],
                                result['mode']
                            )
                            logger.info(f"✅ Musical key: {result['musical_key']}")
                        except Exception as e:
                            logger.warning(f"⚠️ Erreur conversion musical_key: {e}")

            # Sauvegarder en cache
            self.cache[cache_key] = result
            self._save_cache()

            logger.info(f"✅ Succès complet pour Spotify ID: {spotify_id}")
            return result

        except Exception as e:
            logger.error(f"❌ Erreur générale get_track_info: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None

    def get_multiple_tracks_with_bpm(self, spotify_ids: List[str]) -> List[Dict]:
        """
        Récupère plusieurs tracks + BPM en batch (max 50 IDs)

        Args:
            spotify_ids: Liste d'IDs Spotify

        Returns:
            Liste de dictionnaires avec les données
        """
        try:
            # Limiter à 50 IDs par requête
            spotify_ids = spotify_ids[:50]

            url = f"{self.recco_base_url}/track"
            params = {'ids': ','.join(spotify_ids)}

            logger.info(f"🎵 Batch request pour {len(spotify_ids)} tracks")

            response = self.recco_session.get(url, params=params, timeout=30)

            if response.status_code != 200:
                logger.error(f"❌ Batch request failed: {response.status_code}")
                return []

            tracks = response.json()

            # Enrichir avec les audio features
            for track in tracks:
                reccobeats_id = track.get('id')
                if reccobeats_id:
                    features = self.get_track_audio_features(reccobeats_id)
                    if features:
                        track['bpm'] = features.get('tempo')
                        track['audio_features'] = features

            logger.info(f"✅ {len(tracks)} tracks enrichis")
            return tracks

        except Exception as e:
            logger.error(f"❌ Batch processing error: {e}")
            return []

    def clear_cache(self):
        """Vide le cache"""
        self.cache.clear()
        self._save_cache()
        logger.info("Cache vidé")

    def clear_error_cache(self):
        """Nettoie les entrées d'erreur du cache"""
        errors_removed = 0
        keys_to_remove = []

        for key, value in self.cache.items():
            if isinstance(value, dict) and 'error' in value:
                keys_to_remove.append(key)

        for key in keys_to_remove:
            del self.cache[key]
            errors_removed += 1

        if errors_removed > 0:
            logger.info(f"{errors_removed} entrées d'erreur supprimées du cache")
            self._save_cache()

        return errors_removed > 0

    def get_cache_stats(self) -> Dict:
        """Statistiques du cache"""
        total = len(self.cache)
        errors = len([v for v in self.cache.values() if isinstance(v, dict) and 'error' in v])
        success = len([v for v in self.cache.values() if isinstance(v, dict) and v.get('success')])

        return {
            'total_entries': total,
            'successful_entries': success,
            'error_entries': errors,
            'cache_file': self.cache_file
        }

    def close(self):
        """Ferme les connexions"""
        try:
            if hasattr(self, 'recco_session'):
                self.recco_session.close()

            logger.info("✅ ReccoBeats client fermé")

        except Exception as e:
            logger.error(f"Erreur fermeture ReccoBeats client: {e}")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# Test simple
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    client = ReccoBeatsIntegratedClient()

    try:
        # Test avec un ID Spotify connu
        test_spotify_id = "3KkXRkHbMCARz0aVfEt68P"  # PNL - Au DD

        print("\n=== Test ReccoBeats ===")
        print(f"Spotify ID: {test_spotify_id}")

        result = client.get_track_info(test_spotify_id)

        if result:
            print(f"\n✅ Succès!")
            print(f"  - Titre: {result.get('trackTitle', 'N/A')}")
            print(f"  - Artiste: {result.get('artistName', 'N/A')}")
            print(f"  - BPM: {result.get('bpm', 'N/A')}")
            print(f"  - Key: {result.get('key', 'N/A')}")
            print(f"  - Mode: {result.get('mode', 'N/A')}")
            print(f"  - Musical Key: {result.get('musical_key', 'N/A')}")
            print(f"  - Duration: {result.get('duration', 'N/A')}s")
        else:
            print(f"\n❌ Échec")

        # Stats du cache
        print("\n=== Stats du cache ===")
        stats = client.get_cache_stats()
        for key, value in stats.items():
            print(f"  {key}: {value}")

    finally:
        client.close()
