"""
ReccoBeats API Client - Version modulaire propre
Responsabilité UNIQUE : Récupérer les données musicales (BPM, Key, Mode) depuis ReccoBeats
Le scraping Spotify ID est géré par SpotifyIDScraper (module séparé)
"""

import json
import logging
import time
from typing import TYPE_CHECKING

import requests

if TYPE_CHECKING:
    from src.api.async_http import AsyncHttpSession

logger = logging.getLogger("ReccoBeatsAPI")

# En-têtes par requête pour la voie async : l'AsyncHttpSession est PARTAGÉE
# (UA httpx par défaut) → on repasse l'UA/Accept du client sync par requête
# (cf. self.recco_session.headers). Sans effet observé, alignement préventif.
_ASYNC_HEADERS = {"Accept": "application/json", "User-Agent": "ReccoBeats-Python-Client/3.0"}


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
        self.recco_session.headers.update(
            {"Accept": "application/json", "User-Agent": "ReccoBeats-Python-Client/3.0"}
        )

        logger.info("ReccoBeats client initialisé")

    def _load_cache(self) -> dict:
        """Charge le cache depuis le fichier"""
        try:
            with open(self.cache_file, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_cache(self):
        """Sauvegarde le cache"""
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Erreur sauvegarde cache: {e}")

    def _get_cache_key(self, spotify_id: str) -> str:
        """Génère une clé de cache basée sur l'ID Spotify"""
        return f"spotify_id::{spotify_id}"

    @staticmethod
    def _pick_track_from_response(data) -> dict | None:
        """Extrait le track d'une réponse 200 aux formats variables (commun sync/async)."""
        track = None

        if isinstance(data, list) and len(data) > 0:
            track = data[0]
            logger.info(f"✅ Track trouvé (liste): {track.get('trackTitle', 'N/A')}")
        elif isinstance(data, dict):
            if "content" in data:
                content = data["content"]
                if isinstance(content, list) and len(content) > 0:
                    track = content[0]
                    logger.info(
                        f"✅ Track trouvé (dict.content[0]): {track.get('trackTitle', 'N/A')}"
                    )
                elif isinstance(content, dict):
                    if "id" in content or "trackTitle" in content:
                        track = content
                        logger.info(
                            f"✅ Track trouvé (dict.content dict): {track.get('trackTitle', 'N/A')}"
                        )
            elif "id" in data or "trackTitle" in data:
                track = data
                logger.info(f"✅ Track trouvé (dict direct): {track.get('trackTitle', 'N/A')}")

        return track

    def get_track_from_reccobeats(self, spotify_id: str) -> dict | None:
        """
        Récupère les données d'un track depuis ReccoBeats

        Args:
            spotify_id: L'ID Spotify du track

        Returns:
            Dictionnaire avec les données du track ou None
        """
        try:
            url = f"{self.recco_base_url}/track"
            params = {"ids": spotify_id}

            logger.info(f"🎵 ReccoBeats: Requête pour ID {spotify_id}")

            response = self.recco_session.get(url, params=params, timeout=15)

            logger.debug(f"📡 Response: Status {response.status_code}")

            if response.status_code == 200:
                return self._pick_track_from_response(response.json())

            elif response.status_code == 404:
                logger.warning(f"❌ Track {spotify_id} non trouvé (404)")
            elif response.status_code == 429:
                logger.warning("⏰ Rate limit atteint")
            else:
                logger.error(f"❌ Erreur {response.status_code}: {response.text[:200]}")

        except Exception as e:
            logger.error(f"❌ Exception ReccoBeats: {e}")

        return None

    async def get_track_from_reccobeats_async(
        self, http: "AsyncHttpSession", spotify_id: str
    ) -> dict | None:
        """Jumeau async de `get_track_from_reccobeats`."""
        try:
            url = f"{self.recco_base_url}/track"
            logger.info(f"🎵 ReccoBeats: Requête pour ID {spotify_id}")
            response = await http.get(
                url, params={"ids": spotify_id}, headers=_ASYNC_HEADERS, timeout=15
            )
            logger.debug(f"📡 Response: Status {response.status_code}")

            if response.status_code == 200:
                return self._pick_track_from_response(response.json())
            elif response.status_code == 404:
                logger.warning(f"❌ Track {spotify_id} non trouvé (404)")
            elif response.status_code == 429:
                logger.warning("⏰ Rate limit atteint")
            else:
                logger.error(f"❌ Erreur {response.status_code}: {response.text[:200]}")
        except Exception as e:
            logger.error(f"❌ Exception ReccoBeats: {e}")

        return None

    def get_track_audio_features(self, reccobeats_id: str) -> dict | None:
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

    async def get_track_audio_features_async(
        self, http: "AsyncHttpSession", reccobeats_id: str
    ) -> dict | None:
        """Jumeau async de `get_track_audio_features`."""
        try:
            url = f"{self.recco_base_url}/track/{reccobeats_id}/audio-features"
            logger.debug(f"🎼 Audio features: {url}")
            response = await http.get(url, headers=_ASYNC_HEADERS, timeout=15)

            if response.status_code == 200:
                features = response.json()
                logger.info(f"✅ BPM récupéré: {features.get('tempo', 'N/A')}")
                return features
            else:
                logger.warning(f"❌ Audio features erreur {response.status_code}")
        except Exception as e:
            logger.error(f"❌ Exception audio features: {e}")

        return None

    def get_track_info(
        self, spotify_id: str, use_cache: bool = True, force_refresh: bool = False
    ) -> dict | None:
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
            cached = self._cached_spotify_info(cache_key, spotify_id, use_cache, force_refresh)
            if cached is not None:
                return cached

            # Étape 1: Récupérer les données du track
            track_data = self.get_track_from_reccobeats(spotify_id)

            if not track_data:
                self._cache_not_found(cache_key, f"Spotify ID {spotify_id}")
                return None

            # Durée + audio features (BPM/Key/Mode...) via helper partagé
            result = self._enrich_result_with_features(
                self._base_spotify_result(spotify_id, track_data), track_data
            )

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

    async def get_track_info_async(
        self,
        http: "AsyncHttpSession",
        spotify_id: str,
        use_cache: bool = True,
        force_refresh: bool = False,
    ) -> dict | None:
        """Jumeau async de `get_track_info` (même cache, même forme de retour)."""
        logger.info(f"🎵 get_track_info pour Spotify ID: {spotify_id}")

        try:
            cache_key = self._get_cache_key(spotify_id)
            cached = self._cached_spotify_info(cache_key, spotify_id, use_cache, force_refresh)
            if cached is not None:
                return cached

            track_data = await self.get_track_from_reccobeats_async(http, spotify_id)

            if not track_data:
                self._cache_not_found(cache_key, f"Spotify ID {spotify_id}")
                return None

            result = await self._enrich_result_with_features_async(
                http, self._base_spotify_result(spotify_id, track_data), track_data
            )

            self.cache[cache_key] = result
            self._save_cache()

            logger.info(f"✅ Succès complet pour Spotify ID: {spotify_id}")
            return result

        except Exception as e:
            logger.error(f"❌ Erreur générale get_track_info: {e}")
            import traceback

            logger.error(traceback.format_exc())
            return None

    def _cached_spotify_info(
        self, cache_key: str, spotify_id: str, use_cache: bool, force_refresh: bool
    ) -> dict | None:
        """Gestion du cache de `get_track_info` (commun sync/async)."""
        # Force refresh = nettoyer le cache
        if force_refresh and cache_key in self.cache:
            del self.cache[cache_key]
            logger.info(f"Force refresh pour: {spotify_id}")

        # Vérifier le cache
        if use_cache and not force_refresh and cache_key in self.cache:
            cached = self.cache[cache_key]
            if isinstance(cached, dict):
                has_bpm = cached.get("bpm") is not None or cached.get("tempo") is not None
                has_audio_features = cached.get("audio_features") is not None

                if has_bpm or has_audio_features:
                    logger.info("✅ Données complètes trouvées dans le cache")
                    return cached
        return None

    def _cache_not_found(self, cache_key: str, label: str) -> None:
        """Mémorise un échec « not_found » (commun sync/async)."""
        logger.warning(f"❌ Aucune donnée ReccoBeats pour {label}")
        self.cache[cache_key] = {"error": "not_found", "timestamp": time.time()}
        self._save_cache()

    @staticmethod
    def _base_spotify_result(spotify_id: str, track_data: dict) -> dict:
        """Réponse de base de la voie Spotify ID (commun sync/async)."""
        return {
            "spotify_id": spotify_id,
            "source": "reccobeats",
            "success": True,
            "timestamp": time.time(),
            **track_data,
        }

    @staticmethod
    def _apply_duration(result: dict, track_data: dict) -> None:
        """Durée depuis `durationMs` (commun sync/async)."""
        if "durationMs" in track_data:
            duration_ms = track_data["durationMs"]
            result["duration"] = int(duration_ms / 1000) if duration_ms else None
            logger.info(f"⏱️ Duration: {result['duration']}s ({duration_ms}ms)")

    @staticmethod
    def _apply_audio_features(result: dict, audio_features: dict | None) -> None:
        """Applique les audio features + musical_key (commun sync/async)."""
        if not audio_features:
            return
        result["audio_features"] = audio_features
        result["bpm"] = audio_features.get("tempo")
        result["key"] = audio_features.get("key")
        result["mode"] = audio_features.get("mode")
        result["energy"] = audio_features.get("energy")
        result["danceability"] = audio_features.get("danceability")
        result["valence"] = audio_features.get("valence")
        if result.get("key") is not None and result.get("mode") is not None:
            try:
                from src.utils.music_theory import key_mode_to_french

                result["musical_key"] = key_mode_to_french(result["key"], result["mode"])
                logger.info(f"✅ Musical key: {result['musical_key']}")
            except Exception as e:
                logger.warning(f"⚠️ Erreur conversion musical_key: {e}")

    def _enrich_result_with_features(self, result: dict, track_data: dict) -> dict:
        """
        Complète un result de base avec la durée et les audio features
        (BPM, Key, Mode, energy, danceability, valence, musical_key).
        Partagé entre la voie Spotify ID et la voie ISRC.
        """
        self._apply_duration(result, track_data)

        reccobeats_id = track_data.get("id")
        if reccobeats_id:
            logger.debug(f"🎼 Récupération audio features pour ID: {reccobeats_id}")
            self._apply_audio_features(result, self.get_track_audio_features(reccobeats_id))
        return result

    async def _enrich_result_with_features_async(
        self, http: "AsyncHttpSession", result: dict, track_data: dict
    ) -> dict:
        """Jumeau async de `_enrich_result_with_features`."""
        self._apply_duration(result, track_data)

        reccobeats_id = track_data.get("id")
        if reccobeats_id:
            logger.debug(f"🎼 Récupération audio features pour ID: {reccobeats_id}")
            self._apply_audio_features(
                result, await self.get_track_audio_features_async(http, reccobeats_id)
            )
        return result

    def get_track_by_isrc(self, isrc: str) -> dict | None:
        """
        Récupère le track ReccoBeats correspondant à un ISRC.
        L'endpoint /track?ids=<isrc> peut renvoyer PLUSIEURS entrées (pressings
        Spotify distincts) -> on garde la plus populaire.
        """
        try:
            url = f"{self.recco_base_url}/track"
            response = self.recco_session.get(url, params={"ids": isrc}, timeout=15)
            logger.info(f"🎵 ReccoBeats: requête ISRC {isrc} (status {response.status_code})")

            if response.status_code != 200:
                if response.status_code == 429:
                    logger.warning("⏰ Rate limit ReccoBeats")
                return None

            return self._best_isrc_hit(response.json().get("content", []))
        except Exception as e:
            logger.error(f"❌ Exception ReccoBeats ISRC: {e}")
            return None

    @staticmethod
    def _best_isrc_hit(content: list) -> dict | None:
        """Entrée la plus populaire parmi les pressings d'un ISRC (commun sync/async)."""
        if not content:
            return None
        best = sorted(content, key=lambda t: t.get("popularity", 0), reverse=True)[0]
        logger.info(
            f"✅ Track ISRC trouvé: {best.get('trackTitle', 'N/A')} (pop={best.get('popularity')})"
        )
        return best

    async def get_track_by_isrc_async(self, http: "AsyncHttpSession", isrc: str) -> dict | None:
        """Jumeau async de `get_track_by_isrc`."""
        try:
            url = f"{self.recco_base_url}/track"
            response = await http.get(url, params={"ids": isrc}, headers=_ASYNC_HEADERS, timeout=15)
            logger.info(f"🎵 ReccoBeats: requête ISRC {isrc} (status {response.status_code})")

            if response.status_code != 200:
                if response.status_code == 429:
                    logger.warning("⏰ Rate limit ReccoBeats")
                return None

            return self._best_isrc_hit(response.json().get("content", []))
        except Exception as e:
            logger.error(f"❌ Exception ReccoBeats ISRC: {e}")
            return None

    def get_track_info_by_isrc(
        self, isrc: str, use_cache: bool = True, force_refresh: bool = False
    ) -> dict | None:
        """
        Équivalent de get_track_info mais à partir d'un ISRC (pas de Spotify ID).
        Même forme de retour (success, bpm, key, mode, musical_key, duration...).
        """
        if not isrc:
            return None

        logger.info(f"🎵 get_track_info_by_isrc pour ISRC: {isrc}")
        try:
            cache_key = f"isrc::{isrc}"
            cached = self._cached_isrc_info(cache_key, use_cache, force_refresh)
            if cached is not None:
                return cached

            track_data = self.get_track_by_isrc(isrc)
            if not track_data:
                self._cache_not_found(cache_key, f"ISRC {isrc}")
                return None

            result = self._enrich_result_with_features(
                self._base_isrc_result(isrc, track_data), track_data
            )

            self.cache[cache_key] = result
            self._save_cache()
            logger.info(f"✅ Succès complet pour ISRC: {isrc}")
            return result
        except Exception as e:
            logger.error(f"❌ Erreur get_track_info_by_isrc: {e}")
            return None

    async def get_track_info_by_isrc_async(
        self,
        http: "AsyncHttpSession",
        isrc: str,
        use_cache: bool = True,
        force_refresh: bool = False,
    ) -> dict | None:
        """Jumeau async de `get_track_info_by_isrc` (même cache, même retour)."""
        if not isrc:
            return None

        logger.info(f"🎵 get_track_info_by_isrc pour ISRC: {isrc}")
        try:
            cache_key = f"isrc::{isrc}"
            cached = self._cached_isrc_info(cache_key, use_cache, force_refresh)
            if cached is not None:
                return cached

            track_data = await self.get_track_by_isrc_async(http, isrc)
            if not track_data:
                self._cache_not_found(cache_key, f"ISRC {isrc}")
                return None

            result = await self._enrich_result_with_features_async(
                http, self._base_isrc_result(isrc, track_data), track_data
            )

            self.cache[cache_key] = result
            self._save_cache()
            logger.info(f"✅ Succès complet pour ISRC: {isrc}")
            return result
        except Exception as e:
            logger.error(f"❌ Erreur get_track_info_by_isrc: {e}")
            return None

    def _cached_isrc_info(
        self, cache_key: str, use_cache: bool, force_refresh: bool
    ) -> dict | None:
        """Gestion du cache de la voie ISRC (commun sync/async)."""
        if force_refresh and cache_key in self.cache:
            del self.cache[cache_key]

        if use_cache and not force_refresh and cache_key in self.cache:
            cached = self.cache[cache_key]
            if isinstance(cached, dict) and (
                cached.get("bpm") is not None or cached.get("audio_features") is not None
            ):
                logger.info("✅ Données ISRC trouvées dans le cache")
                return cached
        return None

    @staticmethod
    def _base_isrc_result(isrc: str, track_data: dict) -> dict:
        """Réponse de base de la voie ISRC (commun sync/async)."""
        return {
            "isrc": isrc,
            "spotify_id": None,
            "source": "reccobeats_isrc",
            "success": True,
            "timestamp": time.time(),
            **track_data,
        }

    def get_audio_features_batch(self, reccobeats_ids: list[str]) -> dict[str, dict]:
        """
        Récupère les audio features de plusieurs tracks EN UN SEUL appel
        (GET /audio-features?ids=...), au lieu de N appels /track/{id}/audio-features.

        Args:
            reccobeats_ids: liste d'IDs ReccoBeats (max 40)

        Returns:
            Mapping { reccobeats_id: features }
        """
        result: dict[str, dict] = {}
        if not reccobeats_ids:
            return result
        try:
            url = f"{self.recco_base_url}/audio-features"
            params = {"ids": ",".join(reccobeats_ids[:40])}
            response = self.recco_session.get(url, params=params, timeout=30)
            if response.status_code != 200:
                logger.warning(f"❌ audio-features batch: status {response.status_code}")
                return result
            content = response.json().get("content", [])
            for feat in content:
                fid = feat.get("id")
                if fid:
                    result[fid] = feat
        except Exception as e:
            logger.error(f"❌ audio-features batch error: {e}")
        return result

    def get_multiple_tracks_with_bpm(self, ids: list[str]) -> list[dict]:
        """
        Récupère plusieurs tracks + audio features en batch.
        `ids` accepte Spotify IDs, ReccoBeats IDs ou ISRC (max 40 — limite API).

        Returns:
            Liste de tracks (chacun enrichi de 'bpm' + 'audio_features' si dispo).
        """
        try:
            # Doc ReccoBeats : 1 à 40 valeurs par requête /track?ids=
            ids = ids[:40]

            url = f"{self.recco_base_url}/track"
            params = {"ids": ",".join(ids)}

            logger.info(f"🎵 Batch request pour {len(ids)} tracks")

            response = self.recco_session.get(url, params=params, timeout=30)

            if response.status_code != 200:
                logger.error(f"❌ Batch request failed: {response.status_code}")
                return []

            # La réponse est wrappée dans {'content': [...]}
            tracks = response.json().get("content", [])
            if not tracks:
                return []

            # Audio features en UN appel batch, puis mapping par ID ReccoBeats
            reccobeats_ids = [t["id"] for t in tracks if t.get("id")]
            features_map = self.get_audio_features_batch(reccobeats_ids)

            for track in tracks:
                feats = features_map.get(track.get("id"))
                if feats:
                    track["bpm"] = feats.get("tempo")
                    track["audio_features"] = feats

            logger.info(f"✅ {len(tracks)} tracks enrichis (audio-features en 1 appel)")
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
            if isinstance(value, dict) and "error" in value:
                keys_to_remove.append(key)

        for key in keys_to_remove:
            del self.cache[key]
            errors_removed += 1

        if errors_removed > 0:
            logger.info(f"{errors_removed} entrées d'erreur supprimées du cache")
            self._save_cache()

        return errors_removed > 0

    def get_cache_stats(self) -> dict:
        """Statistiques du cache"""
        total = len(self.cache)
        errors = len([v for v in self.cache.values() if isinstance(v, dict) and "error" in v])
        success = len([v for v in self.cache.values() if isinstance(v, dict) and v.get("success")])

        return {
            "total_entries": total,
            "successful_entries": success,
            "error_entries": errors,
            "cache_file": self.cache_file,
        }

    def close(self):
        """Ferme les connexions"""
        try:
            if hasattr(self, "recco_session"):
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
            print("\n✅ Succès!")
            print(f"  - Titre: {result.get('trackTitle', 'N/A')}")
            print(f"  - Artiste: {result.get('artistName', 'N/A')}")
            print(f"  - BPM: {result.get('bpm', 'N/A')}")
            print(f"  - Key: {result.get('key', 'N/A')}")
            print(f"  - Mode: {result.get('mode', 'N/A')}")
            print(f"  - Musical Key: {result.get('musical_key', 'N/A')}")
            print(f"  - Duration: {result.get('duration', 'N/A')}s")
        else:
            print("\n❌ Échec")

        # Stats du cache
        print("\n=== Stats du cache ===")
        stats = client.get_cache_stats()
        for key, value in stats.items():
            print(f"  {key}: {value}")

    finally:
        client.close()
