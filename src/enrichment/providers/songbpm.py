"""Provider SongBPM (scrape) — DÉPARTAGE du vote BPM + Key/Mode/Duration/Spotify ID.

Corps historique de `DataEnricher._enrich_with_songbpm`, déplacé sans changement
de logique. Le timeout de garde (threading.Timer 30 s) déménage tel quel ; il
sera remplacé par asyncio.timeout en phase F. Le BPM alimente le scrutin (§8.3),
le Spotify ID trouvé est validé par la fonction d'unicité fournie via le contexte.
"""

from src.enrichment.audio_normalize import key_mode_observations
from src.enrichment.base import Capability, LazyResource
from src.enrichment.context import EnrichmentContext
from src.models import Track
from src.utils.bpm_vote import sanitize_bpm
from src.utils.logger import get_logger

logger = get_logger(__name__)


class SongBpmProvider:
    """Enrichissement via le scraper SongBPM (source `songbpm`)."""

    name = "songbpm"
    capabilities = {Capability.BPM}  # E7b structurel (non consommé)
    # None = crash/timeout ≠ False (« pas de données ») : n'entre pas dans le
    # « tout a échoué » qui déclenche le nettoyage de l'orchestrateur.
    error_result = None

    def __init__(self, scraper=None, scraper_factory=None):
        # PROPRIÉTAIRE de son scraper (créé lazy, fermé par close()).
        self._resource = LazyResource(scraper, scraper_factory, label="scraper SongBPM")

    def is_available(self) -> bool:
        return self._resource.available()

    def close(self) -> None:
        """Ferme le scraper si ce provider l'a créé (recréé au run suivant)."""
        self._resource.close()

    def gate(self, track: Track, ctx: EnrichmentContext) -> str | None:
        """DÉPARTAGE (§8.3) : scrape seulement si les APIs n'ont pas de consensus
        BPM, ou s'il manque key/mode/duration (force_update court-circuite)."""
        # key/mode : attributs dynamiques du mapper → getattr requis
        missing_key = getattr(track, "key", None) is None
        missing_mode = getattr(track, "mode", None) is None
        missing_duration = not track.duration
        bpm_consensus = ctx.bpm_ballot.consensus_reached()

        should_run = (
            ctx.force_update or not bpm_consensus or missing_key or missing_mode or missing_duration
        )
        if not should_run:
            logger.info(
                f"⏭️ SongBPM non appelé (toutes les données déjà présentes: BPM={track.bpm}, "
                f"Key={getattr(track, 'key', 'N/A')}, Mode={getattr(track, 'mode', 'N/A')}, "
                f"Duration={track.duration})"
            )
            return "not_needed"

        reasons = []
        if ctx.force_update:
            reasons.append("force_update=True")
        if not bpm_consensus:
            reasons.append("pas_de_consensus_bpm")
        missing_items = [
            name
            for name, missing in (
                ("key", missing_key),
                ("mode", missing_mode),
                ("duration", missing_duration),
            )
            if missing
        ]
        if missing_items:
            reasons.append(f"missing_data={','.join(missing_items)}")

        logger.info(
            f"🎼 Appel de SongBPM (départage) pour '{track.title}' (raison: {', '.join(reasons)})"
        )
        return None

    def enrich(self, track: Track, ctx: EnrichmentContext) -> bool:
        force_update = ctx.force_update
        artist_tracks = ctx.artist_tracks

        scraper = self._resource.get()
        if not scraper:
            return False

        try:
            # NOUVEAU: Utiliser le bon artiste selon si c'est un featuring
            if track.is_featuring:
                # Si c'est un featuring, utiliser l'artiste principal
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

            # Extraire le Spotify ID si disponible (et validé)
            spotify_id = track.spotify_id

            # Valider l'unicité si un ID existe
            if (
                spotify_id
                and artist_tracks
                and ctx.validate_spotify_id_unique
                and not ctx.validate_spotify_id_unique(spotify_id, track, artist_tracks)
            ):
                logger.warning(
                    "⚠️ Spotify ID du track est un duplicata, ignoré pour la recherche SongBPM"
                )
                spotify_id = None

            # Timeout de garde (30 s) : un Timer invalide un résultat trop tardif.
            # NOTE (AUDIT.md §3.6) : l'ancien code tentait de fermer un attribut
            # Selenium `.driver` qui n'existe plus depuis la migration Playwright
            # (no-op silencieux). On ne force PAS la fermeture depuis le thread du
            # Timer : l'API sync de Playwright n'est pas thread-safe, et le scraper
            # applique déjà ses propres timeouts de navigation (goto timeout=30s).
            # (Branche Unix `signal.alarm` supprimée le 2026-07-11 — app Windows-only.)
            import threading

            timeout_seconds = 30

            track_data = None
            timer_expired = {"value": False}

            def timeout_func():
                timer_expired["value"] = True
                logger.error(
                    f"⏰ SongBPM timeout après {timeout_seconds}s — le résultat sera ignoré"
                )

            timer = threading.Timer(timeout_seconds, timeout_func)
            timer.start()

            try:
                track_data = scraper.search_track(
                    track.title, artist_name, spotify_id=spotify_id, fetch_details=True
                )
            finally:
                timer.cancel()

            if timer_expired["value"]:
                logger.error(f"❌ SongBPM: Timeout expiré pour '{track.title}'")
                return False

            if not track_data:
                return False

            updated = False

            # BPM → candidat pour le vote (+ pose provisoire si manquant)
            sbpm = sanitize_bpm(track_data.get("bpm"))
            if sbpm is not None:
                ctx.bpm_ballot.add("songbpm", sbpm)
                # Un candidat valide fourni au vote = SongBPM a RÉUSSI, même si un
                # BPM était déjà présent (2ᵉ vote concordant). Sans ça, le run était
                # loggé « ÉCHEC » à tort et pouvait déclencher le nettoyage.
                updated = True
                if force_update or not track.bpm:
                    track.bpm = sbpm
                    logger.info(f"📊 BPM ajouté depuis SongBPM: {sbpm} pour {track.title}")

            # Key et Mode
            key_value = track_data.get("key")
            mode_value = track_data.get("mode")

            # Observation key/mode PAR SOURCE (normalisée : "minor"→0, lettre→pc)
            # — indépendante du last-writer legacy, corrige le bug WIP mode="minor"
            # côté observations (la colonne legacy suivra en E5c-2b-ii).
            ctx.observations.extend(
                key_mode_observations("songbpm", key=key_value, mode=mode_value)
            )

            if key_value and mode_value:
                if force_update or not hasattr(track, "key") or not track.key:
                    track.key = key_value
                    track.key_mode_source = "songbpm"
                    logger.info(f"🎵 Key ajoutée depuis SongBPM: {track.key} pour {track.title}")
                    updated = True

                if force_update or not hasattr(track, "mode") or not track.mode:
                    track.mode = mode_value
                    track.key_mode_source = "songbpm"
                    logger.info(f"🎼 Mode ajouté depuis SongBPM: {track.mode} pour {track.title}")
                    updated = True

            # Spotify ID depuis SongBPM (avec validation stricte)
            songbpm_spotify_id = track_data.get("spotify_id")
            if songbpm_spotify_id and (not track.spotify_id):
                # Valider l'unicité
                if (
                    artist_tracks
                    and ctx.validate_spotify_id_unique
                    and ctx.validate_spotify_id_unique(songbpm_spotify_id, track, artist_tracks)
                ):
                    track.spotify_id = songbpm_spotify_id
                    logger.info(f"🎵 Spotify ID ajouté depuis SongBPM: {track.spotify_id}")
                    updated = True
                else:
                    logger.warning(
                        f"⚠️ REJET: Spotify ID de SongBPM déjà utilisé: {songbpm_spotify_id}"
                    )

            # Duration
            if (force_update or not track.duration) and track_data.get("duration"):
                track.duration = track_data["duration"]
                logger.info(
                    f"⏱️ Duration ajoutée depuis SongBPM: {track.duration} pour {track.title}"
                )
                updated = True

            return updated

        except TimeoutError as e:
            logger.error(f"⏰ SongBPM timeout pour {track.title}: {e}")
            return False
        except Exception as e:
            logger.error(f"Erreur SongBPM pour {track.title}: {e}")
            return False
