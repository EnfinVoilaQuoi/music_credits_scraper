"""Provider GetSongBPM — BPM (candidat), Key, Mode, Time Signature via l'API.

Corps historique de `DataEnricher._enrich_with_getsongbpm`, déplacé sans
changement de logique. API gratuite/rapide : appelée systématiquement pour
fournir un 2ᵉ vote BPM (§8.3). Backlink getsongbpm.com obligatoire côté client.
"""

from src.enrichment.audio_normalize import key_mode_observations
from src.enrichment.base import Capability, LazyResource
from src.enrichment.context import EnrichmentContext
from src.models import Track
from src.utils.bpm_vote import sanitize_bpm
from src.utils.logger import get_logger

logger = get_logger(__name__)


class GetSongBpmProvider:
    """Enrichissement via l'API GetSongBPM (source `getsongbpm`)."""

    name = "getsongbpm"
    capabilities = {Capability.BPM}  # E7b structurel (non consommé)
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
        # « Manquant » = ni valeur PERSISTÉE (track.X) ni frais ce run (vote au
        # ballot pour le BPM, observation pour key/mode) — les poses provisoires
        # des providers amont ont été retirées (E7). Gate purement informatif
        # (jamais de skip) → sert à un log honnête. getattr : attrs dynamiques.
        missing_bpm = not track.bpm and not ctx.bpm_ballot.candidates
        missing_key = getattr(track, "key", None) is None and not ctx.has_observation("key")
        missing_mode = getattr(track, "mode", None) is None and not ctx.has_observation("mode")

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

        # Appel SYSTÉMATIQUE (2ᵉ vote BPM §8.3) : quand l'ISRC a déjà tout posé,
        # aucune raison ci-dessus ne s'applique → motif explicite plutôt qu'un
        # « (raison: ) » vide.
        motif = ", ".join(reasons) if reasons else "2e_vote_bpm"
        logger.info(f"🎼 Appel de GetSongBPM pour '{track.title}' (raison: {motif})")
        return None

    @staticmethod
    def _artist_name(track: Track) -> str:
        """Artiste de recherche : artiste principal si featuring (commun sync/async)."""
        if track.is_featuring and track.primary_artist_name:
            logger.info(f"🎤 Featuring détecté, artiste principal: {track.primary_artist_name}")
            return track.primary_artist_name
        return track.artist.name if hasattr(track.artist, "name") else str(track.artist)

    def enrich(self, track: Track, ctx: EnrichmentContext) -> bool:
        """Récupère BPM/Key/Mode/Time Signature ; le BPM rejoint le scrutin."""
        try:
            fetcher = self._resource.get()
            if not fetcher:
                logger.debug("GetSongBPM API non disponible")
                return False

            artist_name = self._artist_name(track)
            logger.info(f"GetSongBPM: DÉBUT traitement '{artist_name}' - '{track.title}'")

            # Appeler l'API
            try:
                song_data = fetcher.fetch_track_bpm(artist_name, track.title)
            except Exception as api_error:
                logger.error(f"GetSongBPM: ❌ Exception API: {api_error}")
                return False

            return self._apply_song_data(track, ctx, song_data)

        except Exception as e:
            logger.error(f"GetSongBPM: ❌ Erreur: {e}")
            return False

    async def enrich_async(self, track: Track, ctx: EnrichmentContext) -> bool:
        """Voie async (F2) : appel API via la session httpx partagée du contexte,
        application des résultats strictement identique à la voie sync."""
        try:
            fetcher = self._resource.get()
            if not fetcher:
                logger.debug("GetSongBPM API non disponible")
                return False

            artist_name = self._artist_name(track)
            logger.info(f"GetSongBPM: DÉBUT traitement '{artist_name}' - '{track.title}'")

            try:
                song_data = await fetcher.fetch_track_bpm_async(ctx.http, artist_name, track.title)
            except Exception as api_error:
                logger.error(f"GetSongBPM: ❌ Exception API: {api_error}")
                return False

            return self._apply_song_data(track, ctx, song_data)

        except Exception as e:
            logger.error(f"GetSongBPM: ❌ Erreur: {e}")
            return False

    def _apply_song_data(self, track: Track, ctx: EnrichmentContext, song_data) -> bool:
        """Émet les observations GetSongBPM (commun sync/async).

        E7 : plus de pose legacy directe sur `track` — le BPM va au scrutin, key/
        mode/time_signature en observations PAR SOURCE. `apply_resolutions` repose
        ensuite bpm/key/mode/key_mode_source/musical_key/time_signature en fin de
        run. `updated` (→ succès) reflète « la source a MESURÉ un champ », non une
        pose (sinon faux ÉCHEC → nettoyage, cf. data_enricher._clear...).
        """
        if song_data.error:
            logger.warning(f"GetSongBPM: ❌ {song_data.error}")
            return False

        updated = False

        # BPM → candidat pour le scrutin (§8.3).
        sbpm = sanitize_bpm(song_data.bpm)
        if sbpm is not None:
            ctx.bpm_ballot.add("getsongbpm", sbpm)
            logger.info(f"GetSongBPM: ✅ BPM: {sbpm}")
            updated = True

        # Observations key/mode PAR SOURCE (normalisées : lettre/mot → pc/0-1).
        ctx.observations.extend(
            key_mode_observations("getsongbpm", key=song_data.key, mode=song_data.mode)
        )
        if song_data.key:
            logger.info(f"GetSongBPM: ✅ Key: {song_data.key}")
            updated = True
        if song_data.mode:
            logger.info(f"GetSongBPM: ✅ Mode: {song_data.mode}")
            updated = True

        # Time Signature → observation PAR SOURCE (colonne droppée E7-D2 ;
        # apply_resolutions repose track.time_signature depuis l'observation).
        if song_data.time_signature:
            from src.enrichment.observation import Observation

            ctx.observations.append(
                Observation("time_signature", song_data.time_signature, "getsongbpm")
            )
            logger.info(f"GetSongBPM: ✅ Time Signature: {song_data.time_signature}")
            updated = True

        if updated:
            logger.info(f"GetSongBPM: ✅ SUCCÈS '{track.title}'")
            return True
        else:
            logger.warning(f"GetSongBPM: ⚠️ Aucune donnée nouvelle pour '{track.title}'")
            return False
