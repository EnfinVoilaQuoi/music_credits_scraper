"""Provider ReccoBeats — BPM/Key/Mode/Duration via ISRC (prioritaire) ou Spotify ID.

Corps historique de `DataEnricher._apply_reccobeats_result` /
`_try_reccobeats_by_isrc` / `_enrich_with_reccobeats`, déplacé sans changement de
logique. Deux points d'entrée pour l'orchestrateur :
  - `try_by_isrc(track, ctx)` : voie ISRC (appelée AVANT le scrape Spotify pour
    l'éviter si l'ISRC suffit) ;
  - `enrich(track, ctx)` : voie Spotify ID (ISRC déjà tentée en amont).
Le BPM alimente le scrutin partagé (§8.3) ; l'unicité d'ID passe par le contexte.
"""

from src.enrichment.audio_normalize import key_mode_observations
from src.enrichment.base import Capability, LazyResource
from src.enrichment.context import EnrichmentContext
from src.models import Track
from src.utils.bpm_vote import sanitize_bpm
from src.utils.logger import get_logger

logger = get_logger(__name__)


class ReccoBeatsProvider:
    """Enrichissement via ReccoBeats (source `reccobeats`)."""

    name = "reccobeats"
    capabilities = {Capability.BPM}  # E7b structurel (non consommé)
    error_result = False

    def __init__(
        self, client=None, deezer_client=None, spotify_scraper_getter=None, client_factory=None
    ):
        # PROPRIÉTAIRE de son client HTTP (créé lazy, fermé par close()).
        self._resource = LazyResource(client, client_factory, label="client ReccoBeats")
        # Client Deezer PARTAGÉ (résolution ISRC) : possédé par DataEnricher,
        # sans ressource à fermer.
        self._deezer = deezer_client
        # EMPRUNT : le scraper Spotify appartient à SpotifyIdProvider — jamais
        # stocké, jamais fermé ici ; le getter est appelé au moment de l'usage.
        self._spotify_scraper_getter = spotify_scraper_getter

    def is_available(self) -> bool:
        return self._resource.available()

    def close(self) -> None:
        """Ferme SON client s'il l'a créé ; jamais le scraper Spotify emprunté."""
        self._resource.close()

    def gate(self, track: Track, ctx: EnrichmentContext) -> bool | None:
        """Skip (résultat True) si la voie ISRC en pré-étape a déjà satisfait
        la source — pas de second appel."""
        if ctx.isrc_satisfied:
            logger.info(f"✅ ReccoBeats déjà satisfait via ISRC (BPM={track.bpm})")
            return True
        logger.info(f"🎵 Appel de ReccoBeats pour '{track.title}'")
        return None

    @staticmethod
    def _artist_name(track: Track) -> str:
        if track.is_featuring and track.primary_artist_name:
            return track.primary_artist_name
        return track.artist.name if hasattr(track.artist, "name") else str(track.artist)

    def _apply_result(
        self, track: Track, track_info: dict, ctx: EnrichmentContext, resolution: str | None = None
    ) -> bool:
        """Applique bpm/key/mode/musical_key/duration (non destructif pour la durée).

        Renvoie True si au moins le BPM ou la Key a été posé.
        """
        applied = False
        if resolution:
            track.reccobeats_resolution = resolution

        # BPM → candidat pour le vote (+ pose provisoire si manquant)
        bpm = track_info.get("bpm")
        if bpm is None and isinstance(track_info.get("audio_features"), dict):
            bpm = track_info["audio_features"].get("tempo")
        sbpm = sanitize_bpm(bpm)
        if sbpm is not None:
            ctx.bpm_ballot.add("reccobeats", sbpm)
            if not track.bpm:
                track.bpm = sbpm
            applied = True

        # Observation key/mode PAR SOURCE (normalisée) — voie ISRC (E5c-2b).
        ctx.observations.extend(
            key_mode_observations(
                "reccobeats", key=track_info.get("key"), mode=track_info.get("mode")
            )
        )

        # Key / Mode
        if track_info.get("key") is not None:
            track.key = track_info["key"]
            track.key_mode_source = "reccobeats"
            applied = True
        if track_info.get("mode") is not None:
            track.mode = track_info["mode"]
            track.key_mode_source = "reccobeats"

        if getattr(track, "key", None) is not None and getattr(track, "mode", None) is not None:
            try:
                from src.utils.music_theory import key_mode_to_french

                track.musical_key = key_mode_to_french(track.key, track.mode)
            except Exception as e:
                logger.warning(f"⚠️ Erreur conversion musical_key: {e}")

        # Durée (ne pas écraser une durée déjà présente)
        dur = track_info.get("duration")
        if isinstance(dur, (int, float)) and dur > 0 and not track.duration:
            track.duration = int(dur)

        return applied

    def try_by_isrc(
        self, track: Track, ctx: EnrichmentContext, artist_name: str | None = None
    ) -> bool:
        """Voie ISRC : ISRC (track ou Deezer) → ReccoBeats SANS scraper de Spotify ID."""
        client = self._resource.get()
        if not client:
            return False

        if artist_name is None:
            artist_name = self._artist_name(track)

        isrc = track.isrc
        if not isrc and self._deezer:
            try:
                isrc = self._deezer.get_isrc(artist_name, track.title)
                if isrc:
                    track.isrc = isrc
                    logger.info(f"🔑 ISRC récupéré via Deezer: {isrc}")
            except Exception as e:
                logger.debug(f"Deezer get_isrc échec: {e}")

        if not isrc:
            return False

        try:
            info = client.get_track_info_by_isrc(isrc)
        except Exception as e:
            logger.error(f"❌ ReccoBeats ISRC API: {e}")
            info = None

        if info and info.get("success") and self._apply_result(track, info, ctx, resolution="isrc"):
            logger.info(
                f"ReccoBeats: ✅ SUCCÈS via ISRC pour '{track.title}' (scrape Spotify évité)"
            )
            return True

        logger.info(
            f"ReccoBeats: ISRC sans audio-features pour '{track.title}' → fallback Spotify ID"
        )
        return False

    async def try_by_isrc_async(
        self, track: Track, ctx: EnrichmentContext, artist_name: str | None = None
    ) -> bool:
        """Voie async (F2) de `try_by_isrc` : Deezer + ReccoBeats via httpx partagé."""
        client = self._resource.get()
        if not client:
            return False

        if artist_name is None:
            artist_name = self._artist_name(track)

        isrc = track.isrc
        if not isrc and self._deezer:
            try:
                isrc = await self._deezer.get_isrc_async(ctx.http, artist_name, track.title)
                if isrc:
                    track.isrc = isrc
                    logger.info(f"🔑 ISRC récupéré via Deezer: {isrc}")
            except Exception as e:
                logger.debug(f"Deezer get_isrc échec: {e}")

        if not isrc:
            return False

        try:
            info = await client.get_track_info_by_isrc_async(ctx.http, isrc)
        except Exception as e:
            logger.error(f"❌ ReccoBeats ISRC API: {e}")
            info = None

        if info and info.get("success") and self._apply_result(track, info, ctx, resolution="isrc"):
            logger.info(
                f"ReccoBeats: ✅ SUCCÈS via ISRC pour '{track.title}' (scrape Spotify évité)"
            )
            return True

        logger.info(
            f"ReccoBeats: ISRC sans audio-features pour '{track.title}' → fallback Spotify ID"
        )
        return False

    def _existing_spotify_id(self, track: Track, ctx: EnrichmentContext) -> str | None:
        """Étape 1a (commun sync/async) : ID existant validé, sinon None.

        Comportement historique : sans artist_tracks ou si l'ID est un
        duplicata, on l'ignore et on le re-scrape (l'ID dupliqué est effacé).
        """
        if not track.spotify_id:
            return None
        validate = ctx.validate_spotify_id_unique
        if ctx.artist_tracks and validate and validate(track.spotify_id, track, ctx.artist_tracks):
            logger.info(f"✅ Spotify ID existant validé: {track.spotify_id}")
            return track.spotify_id
        logger.warning("⚠️ Spotify ID existant est un duplicata, il sera ignoré")
        track.spotify_id = None
        return None

    def _spotify_id_via_scraper(
        self, track: Track, ctx: EnrichmentContext, artist_name: str
    ) -> str | None:
        """Étape 1b (commun sync/async) : fallback scraper Spotify EMPRUNTÉ.

        Sur la voie async, ce bloc entier tourne sur le thread sync dédié du
        run — le scraper Playwright y naît et y travaille (affinité).
        """
        if not ctx.allow_spotify_scrape:
            logger.info(
                "⏭️ ReccoBeats: scrape Spotify déjà tenté à l'étape 0 → pas de second scrape"
            )
            return None
        spotify_scraper = self._spotify_scraper_getter() if self._spotify_scraper_getter else None
        if not spotify_scraper:
            return None

        artist_tracks = ctx.artist_tracks
        logger.info(f"🔍 Appel SpotifyIDScraper pour '{artist_name}' - '{track.title}'")
        try:
            spotify_id = spotify_scraper.get_spotify_id(artist_name, track.title)

            if spotify_id:
                # Valider l'unicité
                if (
                    artist_tracks
                    and ctx.validate_spotify_id_unique
                    and not ctx.validate_spotify_id_unique(spotify_id, track, artist_tracks)
                ):
                    logger.error(f"❌ REJET: Spotify ID du scraper déjà utilisé: {spotify_id}")
                    spotify_id = None
                else:
                    logger.info(f"✅ Spotify ID trouvé par le scraper: {spotify_id}")
                    track.spotify_id = spotify_id

                    # Récupérer le titre de la page Spotify pour vérification
                    try:
                        page_title = spotify_scraper.get_spotify_page_title(spotify_id)
                        if page_title:
                            track.spotify_page_title = page_title
                            logger.info(f"📄 Titre de page Spotify: {page_title[:50]}...")
                    except Exception as e:
                        logger.debug(f"Impossible de récupérer le titre de page: {e}")
            else:
                logger.warning("❌ SpotifyIDScraper n'a pas trouvé d'ID")

            return spotify_id

        except Exception as e:
            logger.error(f"❌ Erreur SpotifyIDScraper: {e}")
            return None

    def enrich(self, track: Track, ctx: EnrichmentContext) -> bool:
        """Voie Spotify ID (ISRC déjà tentée en amont par l'orchestrateur)."""
        try:
            client = self._resource.get()
            if not client:
                logger.error("ReccoBeats client non initialisé")
                return False

            artist_name = self._artist_name(track)
            logger.info(f"ReccoBeats: DÉBUT traitement '{artist_name}' - '{track.title}'")

            # La voie ISRC a déjà été tentée en amont (enrich_track) : on ne la
            # rejoue pas ici (équivalent skip_isrc=True de l'historique).

            # ÉTAPE 1 : Spotify ID — existant validé, sinon fallback scraper
            spotify_id = self._existing_spotify_id(track, ctx)
            if not spotify_id:
                spotify_id = self._spotify_id_via_scraper(track, ctx, artist_name)

            # 1c. Si toujours pas d'ID, échec
            if not spotify_id:
                logger.warning(f"ReccoBeats: ❌ Aucun Spotify ID disponible pour '{track.title}'")
                return False

            # ÉTAPE 2 : appeler ReccoBeats avec l'ID
            logger.info(f"🎵 Appel ReccoBeats API avec Spotify ID: {spotify_id}")

            try:
                track_info = client.get_track_info(spotify_id)
            except Exception as e:
                logger.error(f"❌ Erreur ReccoBeats API: {e}")
                return False

            return self._apply_spotify_info(track, track_info, ctx, spotify_id)

        except Exception as e:
            logger.error(f"ReccoBeats: ❌ Erreur générale: {e}")
            return False

    async def enrich_async(self, track: Track, ctx: EnrichmentContext) -> bool:
        """Voie async (F2) : API via httpx partagé, fallback scraper sur le
        thread sync dédié du run, application identique à la voie sync."""
        try:
            client = self._resource.get()
            if not client:
                logger.error("ReccoBeats client non initialisé")
                return False

            artist_name = self._artist_name(track)
            logger.info(f"ReccoBeats: DÉBUT traitement '{artist_name}' - '{track.title}'")

            spotify_id = self._existing_spotify_id(track, ctx)
            if not spotify_id:
                spotify_id = await ctx.sync_runner.run(
                    self._spotify_id_via_scraper, track, ctx, artist_name
                )

            if not spotify_id:
                logger.warning(f"ReccoBeats: ❌ Aucun Spotify ID disponible pour '{track.title}'")
                return False

            logger.info(f"🎵 Appel ReccoBeats API avec Spotify ID: {spotify_id}")

            try:
                track_info = await client.get_track_info_async(ctx.http, spotify_id)
            except Exception as e:
                logger.error(f"❌ Erreur ReccoBeats API: {e}")
                return False

            return self._apply_spotify_info(track, track_info, ctx, spotify_id)

        except Exception as e:
            logger.error(f"ReccoBeats: ❌ Erreur générale: {e}")
            return False

    def _apply_spotify_info(
        self, track: Track, track_info: dict | None, ctx: EnrichmentContext, spotify_id: str
    ) -> bool:
        """Étape 2 (commun sync/async) : application des données ReccoBeats."""
        if not track_info or not track_info.get("success"):
            logger.warning(f"ReccoBeats: ❌ Pas de données pour ID {spotify_id}")
            return False

        logger.debug("ReccoBeats: ✅ Données récupérées")
        track.reccobeats_resolution = "spotify_id"

        # Stocker le BPM
        bpm = None
        if "bpm" in track_info:
            bpm = track_info["bpm"]
        elif "tempo" in track_info:
            bpm = track_info["tempo"]
        elif "audio_features" in track_info:
            features = track_info["audio_features"]
            if isinstance(features, dict) and "tempo" in features:
                bpm = features["tempo"]

        sbpm = sanitize_bpm(bpm)
        if sbpm is not None:
            ctx.bpm_ballot.add("reccobeats", sbpm)
            if not track.bpm:
                track.bpm = sbpm
            logger.info(f"ReccoBeats: ✅ BPM: {sbpm}")

        # Observation key/mode PAR SOURCE (normalisée) — voie Spotify ID (E5c-2b).
        ctx.observations.extend(
            key_mode_observations(
                "reccobeats", key=track_info.get("key"), mode=track_info.get("mode")
            )
        )

        # Stocker Key et Mode
        if "key" in track_info and track_info["key"] is not None:
            track.key = track_info["key"]
            track.key_mode_source = "reccobeats"
            logger.info(f"ReccoBeats: ✅ Key: {track.key}")

        if "mode" in track_info and track_info["mode"] is not None:
            track.mode = track_info["mode"]
            track.key_mode_source = "reccobeats"
            logger.info(f"ReccoBeats: ✅ Mode: {track.mode}")

        if (
            hasattr(track, "key")
            and hasattr(track, "mode")
            and track.key is not None
            and track.mode is not None
        ):
            try:
                from src.utils.music_theory import key_mode_to_french

                track.musical_key = key_mode_to_french(track.key, track.mode)
                logger.info(f"ReccoBeats: ✅ Musical Key: {track.musical_key}")
            except Exception as e:
                logger.warning(f"ReccoBeats: ⚠️ Erreur conversion musical_key: {e}")

        # Stocker la Durée
        if "duration" in track_info and track_info["duration"] is not None:
            duration_value = track_info["duration"]
            if isinstance(duration_value, (int, float)) and duration_value > 0:
                track.duration = int(duration_value)
                logger.info(f"ReccoBeats: ✅ Duration: {track.duration}s")
            else:
                logger.warning(f"ReccoBeats: ⚠️ Duration invalide: {duration_value}")

        # Mise à jour de la logique de succès
        has_spotify_id = track.spotify_id
        has_bpm = track.bpm
        has_duration = track.duration  # ⭐ NOUVEAU

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
