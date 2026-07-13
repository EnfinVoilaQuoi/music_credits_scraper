"""Provider Deezer — duration / release date / ISRC / BPM (candidat) / métadonnées.

Corps historique de `DataEnricher._enrich_with_deezer`, déplacé sans changement
de logique. Le BPM Deezer (souvent absent/0) n'est qu'un CANDIDAT : il rejoint
le scrutin du contexte, arbitré en fin de parcours (§8.3). L'ISRC alimente
ReccoBeats (pivot inter-sources, non destructif).
"""

from src.api.deezer_api import DeezerAPI
from src.enrichment.context import EnrichmentContext
from src.models import Track
from src.utils.bpm_vote import sanitize_bpm
from src.utils.logger import get_logger

logger = get_logger(__name__)


class DeezerProvider:
    """Enrichissement via l'API Deezer (source `deezer`)."""

    name = "deezer"
    error_result = False

    def __init__(self, client: DeezerAPI | None = None):
        self._client = client

    def is_available(self) -> bool:
        return self._client is not None

    def close(self) -> None:
        """L'API Deezer (HTTP sans état) n'a aucune ressource à libérer."""

    def gate(self, track: Track, ctx: EnrichmentContext) -> None:
        """Jamais de skip : vérification de cohérence + enrichissement complémentaire."""
        logger.info(f"🎵 Appel de Deezer API pour '{track.title}'")
        return None

    def enrich(self, track: Track, ctx: EnrichmentContext) -> bool:
        """Vérifie la cohérence des données Deezer avec l'existant et enrichit."""
        force_update = ctx.force_update

        if not self._client:
            logger.warning("❌ Deezer API non disponible")
            return False

        try:
            # Déterminer l'artiste (gestion des featurings)
            if track.is_featuring:
                if track.primary_artist_name:
                    artist_name = track.primary_artist_name
                    logger.info(
                        f"🎤 Featuring détecté, utilisation de l'artiste principal: {artist_name}"
                    )
                else:
                    artist_name = (
                        track.artist.name if hasattr(track.artist, "name") else str(track.artist)
                    )
            else:
                artist_name = (
                    track.artist.name if hasattr(track.artist, "name") else str(track.artist)
                )

            logger.info(f"🎵 Deezer: Recherche pour '{artist_name}' - '{track.title}'")

            # Récupérer les données existantes pour vérification
            previous_duration = track.duration
            scraped_release_date = track.release_date

            # Appeler l'API Deezer avec vérifications
            result = self._client.enrich_track(
                artist=artist_name,
                title=track.title,
                previous_duration=previous_duration,
                scraped_release_date=scraped_release_date,
            )

            if not result["success"]:
                logger.warning(f"❌ Deezer: {result.get('error', 'Erreur inconnue')}")
                return False

            data = result["data"]
            verifications = result["verifications"]
            updated = False

            # Logs des vérifications
            if verifications:
                logger.info("🔍 Deezer: Vérifications:")

                if "duration" in verifications:
                    dur_check = verifications["duration"]
                    if dur_check["is_valid"]:
                        if dur_check.get("difference") is not None:
                            logger.info(
                                f"   ✅ Duration cohérente (diff: {dur_check['difference']}s)"
                            )
                        else:
                            logger.info(f"   ℹ️ {dur_check['message']}")
                    else:
                        logger.warning(f"   ⚠️ Duration incohérente! {dur_check['message']}")

                if "release_date" in verifications:
                    date_check = verifications["release_date"]
                    if date_check["is_valid"]:
                        if date_check.get("dates_match") is True:
                            logger.info("   ✅ Release date cohérente")
                        elif date_check.get("dates_match") is False:
                            logger.warning(
                                f"   ⚠️ Release dates différentes: Deezer={date_check.get('deezer_date')} vs Scraping={date_check.get('scraped_date')}"
                            )
                        else:
                            logger.info(f"   ℹ️ {date_check['message']}")
                    else:
                        logger.warning(f"   ⚠️ {date_check['message']}")

            # Stocker la Duration si elle est cohérente ou si on force la mise à jour
            if data.get("deezer_duration"):
                duration_check = verifications.get("duration", {})
                should_update_duration = (
                    force_update or not previous_duration or duration_check.get("is_valid", False)
                )

                if should_update_duration:
                    track.duration = data["deezer_duration"]
                    logger.info(f"   ✅ Duration mise à jour: {track.duration}s")
                    updated = True
                else:
                    logger.warning("   ⚠️ Duration Deezer ignorée (incohérente)")

            # Stocker la Release Date si elle est cohérente ou si on force la mise à jour
            if data.get("deezer_release_date"):
                date_check = verifications.get("release_date", {})
                should_update_date = (
                    force_update or not scraped_release_date or date_check.get("dates_match", False)
                )

                if should_update_date:
                    # Convertir au format utilisé dans la base de données
                    track.release_date = data["deezer_release_date"]
                    logger.info(f"   ✅ Release date mise à jour: {track.release_date}")
                    updated = True
                elif date_check.get("dates_match") is False:
                    logger.warning("   ⚠️ Release date Deezer ignorée (différente du scraping)")

            # Stocker les métadonnées supplémentaires (toujours, pas de vérification nécessaire)
            if data.get("deezer_track_id") and (
                not hasattr(track, "deezer_id") or force_update or not track.deezer_id
            ):
                track.deezer_id = data["deezer_track_id"]
                logger.info(f"   ✅ Deezer ID: {track.deezer_id}")
                updated = True

            # ISRC : pivot inter-sources (non destructif). Alimente ReccoBeats.
            if data.get("deezer_isrc") and (not track.isrc or force_update):
                track.isrc = data["deezer_isrc"]
                logger.info(f"   ✅ ISRC: {track.isrc}")
                updated = True

            # BPM Deezer : candidat (souvent absent/0) — vote arbitré par le scrutin
            sbpm = sanitize_bpm(data.get("deezer_bpm"))
            if sbpm is not None:
                ctx.bpm_ballot.add("deezer", sbpm)
                if not track.bpm:
                    track.bpm = sbpm
                    logger.info(f"   ✅ BPM (Deezer, opportuniste): {sbpm}")
                    updated = True

            if data.get("deezer_link") and (
                not hasattr(track, "deezer_url") or force_update or not track.deezer_url
            ):
                track.deezer_url = data["deezer_link"]
                logger.info(f"   ✅ Deezer URL: {track.deezer_url}")
                updated = True

            if data.get("deezer_explicit_lyrics") is not None and (
                not hasattr(track, "explicit_lyrics")
                or force_update
                or track.explicit_lyrics is None
            ):
                track.explicit_lyrics = data["deezer_explicit_lyrics"]
                logger.info(f"   ✅ Explicit lyrics: {track.explicit_lyrics}")
                updated = True

            if data.get("deezer_picture") and (
                not hasattr(track, "deezer_picture_url")
                or force_update
                or not track.deezer_picture_url
            ):
                track.deezer_picture_url = data["deezer_picture"]
                logger.info("   ✅ Deezer picture URL stockée")
                updated = True

            if updated:
                logger.info(f"✅ Deezer: Enrichissement réussi pour '{track.title}'")
            else:
                logger.info(f"ℹ️ Deezer: Aucune nouvelle donnée pour '{track.title}'")

            return updated

        except Exception as e:
            logger.error(f"❌ Deezer: Erreur pour '{track.title}': {e}")
            return False
