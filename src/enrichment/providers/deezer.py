"""Provider Deezer — duration / release date / ISRC / BPM (candidat) / métadonnées.

Corps historique de `DataEnricher._enrich_with_deezer`, déplacé sans changement
de logique. Le BPM Deezer (souvent absent/0) n'est qu'un CANDIDAT : il rejoint
le scrutin du contexte, arbitré en fin de parcours (§8.3). L'ISRC alimente
ReccoBeats (pivot inter-sources, non destructif).
"""

from src.api.deezer_api import DeezerAPI
from src.enrichment.base import Capability
from src.enrichment.context import EnrichmentContext
from src.enrichment.observation import Observation
from src.models import Track
from src.utils.bpm_vote import sanitize_bpm
from src.utils.logger import get_logger

logger = get_logger(__name__)


class DeezerProvider:
    """Enrichissement via l'API Deezer (source `deezer`)."""

    name = "deezer"
    capabilities = {Capability.BPM}  # E7b structurel (non consommé)
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

    @staticmethod
    def _artist_name(track: Track) -> str:
        """Artiste de recherche : artiste principal si featuring (commun sync/async)."""
        if track.is_featuring and track.primary_artist_name:
            logger.info(
                f"🎤 Featuring détecté, utilisation de l'artiste principal: "
                f"{track.primary_artist_name}"
            )
            return track.primary_artist_name
        return track.artist.name if hasattr(track.artist, "name") else str(track.artist)

    def enrich(self, track: Track, ctx: EnrichmentContext) -> bool:
        """Vérifie la cohérence des données Deezer avec l'existant et enrichit."""
        if not self._client:
            logger.warning("❌ Deezer API non disponible")
            return False

        try:
            artist_name = self._artist_name(track)
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
            return self._apply_result(track, ctx, result, previous_duration, scraped_release_date)

        except Exception:
            # Dernier ressort : DeezerAPI.enrich_track gère déjà le réseau/JSON ;
            # ce qui remonte est un bug d'_apply_result → trace complète, ÉCHEC.
            logger.exception(f"❌ Deezer: Erreur pour '{track.title}'")
            return False

    async def enrich_async(self, track: Track, ctx: EnrichmentContext) -> bool:
        """Voie async (F2) : recherche via la session httpx partagée du contexte,
        application des résultats strictement identique à la voie sync."""
        if not self._client:
            logger.warning("❌ Deezer API non disponible")
            return False

        try:
            artist_name = self._artist_name(track)
            logger.info(f"🎵 Deezer: Recherche pour '{artist_name}' - '{track.title}'")

            previous_duration = track.duration
            scraped_release_date = track.release_date

            result = await self._client.enrich_track_async(
                ctx.http,
                artist=artist_name,
                title=track.title,
                previous_duration=previous_duration,
                scraped_release_date=scraped_release_date,
            )
            return self._apply_result(track, ctx, result, previous_duration, scraped_release_date)

        except Exception:
            # Dernier ressort : DeezerAPI.enrich_track gère déjà le réseau/JSON ;
            # ce qui remonte est un bug d'_apply_result → trace complète, ÉCHEC.
            logger.exception(f"❌ Deezer: Erreur pour '{track.title}'")
            return False

    def _apply_result(
        self,
        track: Track,
        ctx: EnrichmentContext,
        result: dict,
        previous_duration,
        scraped_release_date,
    ) -> bool:
        """Vérifications + application des données Deezer (commun sync/async)."""
        force_update = ctx.force_update

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
                        logger.info(f"   ✅ Duration cohérente (diff: {dur_check['difference']}s)")
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

            # L'observation n'est émise QUE si la mesure est RETENUE — correction
            # du 2026-09-08, quelques heures après le lot B. « Déclarer même ce
            # qu'on ne suit pas » semblait être l'esprit de la provenance ;
            # c'était un contresens. Une observation n'est pas une note de bas de
            # page, c'est un BULLETIN : Deezer étant en TÊTE de l'ordre de
            # priorité, la valeur écartée ici pour incohérence regagnait la
            # colonne à la relecture (mesuré : refusée à 999, relue à 999). La
            # vérification juge de la VALIDITÉ de la mesure — un mauvais
            # rapprochement Deezer —, pas d'une préférence : ce qu'on juge
            # invalide n'entre pas au vote, comme un ID Spotify refusé n'est pas
            # enregistré « pour mémoire ».
            if should_update_duration:
                ctx.observations.append(Observation("duration", data["deezer_duration"], self.name))
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
                # Même règle que pour la durée : une date écartée parce qu'elle
                # contredit le scrape ne doit pas revenir par l'observation.
                ctx.observations.append(
                    Observation("release_date", data["deezer_release_date"], self.name)
                )
                # Convertir au format utilisé dans la base de données
                track.release_date = data["deezer_release_date"]
                logger.info(f"   ✅ Release date mise à jour: {track.release_date}")
                updated = True
            elif date_check.get("dates_match") is False:
                logger.warning("   ⚠️ Release date Deezer ignorée (différente du scraping)")

        # Stocker les métadonnées supplémentaires (toujours, pas de vérification nécessaire).
        # e19 : ce sont désormais de VRAIES colonnes — jusque-là ces valeurs étaient
        # posées sur l'objet puis perdues en fin de run, faute de place en base.
        if data.get("deezer_track_id") and (force_update or not track.deezer_id):
            track.deezer_id = data["deezer_track_id"]
            logger.info(f"   ✅ Deezer ID: {track.deezer_id}")
            updated = True

        # ISRC : pivot inter-sources (non destructif). Alimente ReccoBeats.
        if data.get("deezer_isrc"):
            ctx.observations.append(Observation("isrc", data["deezer_isrc"], self.name))
        if data.get("deezer_isrc") and (not track.isrc or force_update):
            track.isrc = data["deezer_isrc"]
            logger.info(f"   ✅ ISRC: {track.isrc}")
            updated = True

        # BPM Deezer : candidat (souvent absent/0) — vote arbitré par le scrutin.
        # E7 : plus de pose legacy directe, apply_resolutions pose track.audio.bpm en
        # fin de run ; updated rattaché au candidat fourni (pas à la pose).
        sbpm = sanitize_bpm(data.get("deezer_bpm"))
        if sbpm is not None:
            ctx.bpm_ballot.add("deezer", sbpm)
            logger.info(f"   ✅ BPM (Deezer, opportuniste, candidat): {sbpm}")
            updated = True

        if data.get("deezer_link") and (force_update or not track.deezer_url):
            track.deezer_url = data["deezer_link"]
            logger.info(f"   ✅ Deezer URL: {track.deezer_url}")
            updated = True

        # Drapeau « parental advisory » (booléen, pas un texte). `is not None`
        # des deux côtés : False est une MESURE (« Deezer dit que non »), None
        # une absence de mesure — les confondre est l'erreur qu'e17 a réparée
        # pour la résolution d'ID Spotify.
        if data.get("deezer_explicit_lyrics") is not None and (
            force_update or track.lyrics.explicit is None
        ):
            track.lyrics.explicit = data["deezer_explicit_lyrics"]
            logger.info(f"   ✅ Paroles explicites: {track.lyrics.explicit}")
            updated = True

        # `deezer_picture` (cover_medium) n'est PAS repris ici : le chantier
        # Media récupère la même image en haute résolution (`deezer_cover_xl`,
        # `deezer_picture_xl`) et la RANGE sur le disque via `media_enricher`,
        # qui utilise d'ailleurs `deezer_picture` comme repli. Poser l'URL
        # medium sur le Track était un doublon dégradé, et il ne survivait pas
        # au run.

        if updated:
            logger.info(f"✅ Deezer: Enrichissement réussi pour '{track.title}'")
        else:
            logger.info(f"ℹ️ Deezer: Aucune nouvelle donnée pour '{track.title}'")

        return updated
