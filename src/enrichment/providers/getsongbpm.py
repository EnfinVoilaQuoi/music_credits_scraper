"""Provider GetSongBPM — BPM (candidat), Key, Mode, Time Signature via l'API.

Corps historique de `DataEnricher._enrich_with_getsongbpm`, déplacé sans
changement de logique. API gratuite/rapide : appelée systématiquement pour
fournir un 2ᵉ vote BPM (§8.3). Backlink getsongbpm.com obligatoire côté client.
"""

from src.enrichment.base import LazyResource
from src.enrichment.context import EnrichmentContext
from src.models import Track
from src.utils.bpm_vote import sanitize_bpm
from src.utils.logger import get_logger

logger = get_logger(__name__)


class GetSongBpmProvider:
    """Enrichissement via l'API GetSongBPM (source `getsongbpm`)."""

    name = "getsongbpm"
    error_result = False

    def __init__(self, fetcher=None, fetcher_factory=None):
        # Fetcher créé lazy (son ctor lève sans GETSONGBPM_API_KEY).
        self._resource = LazyResource(fetcher, fetcher_factory, label="fetcher GetSongBPM")

    def is_available(self) -> bool:
        return self._resource.available()

    def close(self) -> None:
        """L'API GetSongBPM (HTTP sans état) n'a aucune ressource à libérer."""

    def gate(self, track: Track, ctx: EnrichmentContext) -> None:
        """Jamais de skip : API gratuite/rapide, appelée SYSTÉMATIQUEMENT pour
        fournir un 2ᵉ vote BPM (§8.3). Ne sert qu'à logger la raison."""
        # key/mode : attributs dynamiques du mapper → getattr requis
        missing_bpm = not track.bpm
        missing_key = getattr(track, "key", None) is None
        missing_mode = getattr(track, "mode", None) is None

        reasons = []
        if ctx.force_update:
            reasons.append("force_update=True")
        if missing_bpm:
            reasons.append("no_bpm")
        if ctx.results.get("reccobeats") is False:
            reasons.append("reccobeats_failed")
        if (missing_key or missing_mode) and not missing_bpm:
            missing_items = [
                name for name, missing in (("key", missing_key), ("mode", missing_mode)) if missing
            ]
            reasons.append(f"missing_data={','.join(missing_items)}")

        logger.info(f"🎼 Appel de GetSongBPM pour '{track.title}' (raison: {', '.join(reasons)})")
        return None

    def enrich(self, track: Track, ctx: EnrichmentContext) -> bool:
        """Récupère BPM/Key/Mode/Time Signature ; le BPM rejoint le scrutin."""
        force_update = ctx.force_update
        try:
            fetcher = self._resource.get()
            if not fetcher:
                logger.debug("GetSongBPM API non disponible")
                return False

            # Déterminer l'artiste (gestion featurings)
            if track.is_featuring:
                if track.primary_artist_name:
                    artist_name = track.primary_artist_name
                    logger.info(f"🎤 Featuring détecté, artiste principal: {artist_name}")
                else:
                    artist_name = (
                        track.artist.name if hasattr(track.artist, "name") else str(track.artist)
                    )
            else:
                artist_name = (
                    track.artist.name if hasattr(track.artist, "name") else str(track.artist)
                )

            logger.info(f"GetSongBPM: DÉBUT traitement '{artist_name}' - '{track.title}'")

            # Appeler l'API
            try:
                song_data = fetcher.fetch_track_bpm(artist_name, track.title)
            except Exception as api_error:
                logger.error(f"GetSongBPM: ❌ Exception API: {api_error}")
                return False

            if song_data.error:
                logger.warning(f"GetSongBPM: ❌ {song_data.error}")
                return False

            # Enrichir le track avec les données GetSongBPM
            updated = False

            # BPM → candidat pour le vote (+ pose provisoire si manquant)
            sbpm = sanitize_bpm(song_data.bpm)
            if sbpm is not None:
                ctx.bpm_ballot.add("getsongbpm", sbpm)
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
                    track.key_mode_source = "getsongbpm"
                    logger.info(f"GetSongBPM: ✅ Key: {song_data.key} → {track.key}")
                except Exception:
                    logger.debug(f"GetSongBPM: Key brute stockée: {song_data.key}")
                updated = True

            # Mode (seulement si pas déjà présent ou force_update)
            if song_data.mode and (force_update or not track.mode):
                # Convertir "major"/"minor" en 1/0
                track.mode = 1 if song_data.mode == "major" else 0
                track.key_mode_source = "getsongbpm"
                logger.info(f"GetSongBPM: ✅ Mode: {song_data.mode}")
                updated = True

            # Musical key (calculé depuis Key + Mode)
            if (
                hasattr(track, "key")
                and hasattr(track, "mode")
                and track.key is not None
                and track.mode is not None
            ):
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
