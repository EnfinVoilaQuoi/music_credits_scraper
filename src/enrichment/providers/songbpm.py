"""Provider SongBPM (scrape) — DÉPARTAGE du vote BPM + Key/Mode/Duration/Spotify ID.

Corps historique de `DataEnricher._enrich_with_songbpm`, déplacé sans changement
de logique. Le timeout de garde (threading.Timer 30 s) déménage tel quel ; il
sera remplacé par asyncio.timeout en phase F. Le BPM alimente le scrutin (§8.3),
le Spotify ID trouvé est validé par la fonction d'unicité fournie via le contexte.
"""

import asyncio

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

    def __init__(self, scraper=None, scraper_factory=None, async_scraper_factory=None):
        # PROPRIÉTAIRE de son scraper (créé lazy, fermé par close()).
        self._resource = LazyResource(scraper, scraper_factory, label="scraper SongBPM")
        # Variante ASYNC (F3c) : vit dans la boucle, fermée par aclose().
        self._async_resource = LazyResource(
            None, async_scraper_factory, label="scraper SongBPM async"
        )

    def is_available(self) -> bool:
        return self._resource.available()

    def close(self) -> None:
        """Ferme le scraper si ce provider l'a créé (recréé au run suivant)."""
        self._resource.close()

    async def aclose(self) -> None:
        """Ferme le scraper ASYNC s'il l'a créé (browsers de la boucle, F3c)."""
        await self._async_resource.aclose()

    def gate(self, track: Track, ctx: EnrichmentContext) -> str | None:
        """DÉPARTAGE (§8.3) : scrape seulement si les APIs n'ont pas de consensus
        BPM, ou s'il manque key/mode/duration (force_update court-circuite)."""
        # « Manquant » = ni valeur PERSISTÉE (track.audio.key/mode, relue via
        # observations au chargement) ni observation FRAÎCHE d'une source amont ce
        # run (E7 : les poses provisoires des providers ayant été retirées, on ne
        # peut plus se fier au seul `track.audio.key`). Phase 5 : key/mode sont des
        # champs du sous-objet audio (fin des attributs dynamiques → plus de getattr).
        missing_key = track.audio.key is None and not ctx.has_observation("key")
        missing_mode = track.audio.mode is None and not ctx.has_observation("mode")
        missing_duration = not track.duration
        bpm_consensus = ctx.bpm_ballot.consensus_reached()

        should_run = (
            ctx.force_update or not bpm_consensus or missing_key or missing_mode or missing_duration
        )
        if not should_run:
            logger.info(
                f"⏭️ SongBPM non appelé (toutes les données déjà présentes: BPM={track.audio.bpm}, "
                f"Key={track.audio.key}, Mode={track.audio.mode}, "
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

    def _validated_spotify_id(self, track: Track, ctx: EnrichmentContext) -> str | None:
        """Spotify ID du track, ignoré s'il est un duplicata (commun sync/async)."""
        spotify_id = track.spotify_id
        if (
            spotify_id
            and ctx.artist_tracks
            and ctx.validate_spotify_id_unique
            and not ctx.validate_spotify_id_unique(spotify_id, track, ctx.artist_tracks)
        ):
            logger.warning(
                "⚠️ Spotify ID du track est un duplicata, ignoré pour la recherche SongBPM"
            )
            return None
        return spotify_id

    def enrich(self, track: Track, ctx: EnrichmentContext) -> bool:
        scraper = self._resource.get()
        if not scraper:
            return False

        try:
            artist_name = self._artist_name(track)
            spotify_id = self._validated_spotify_id(track, ctx)

            # Timeout de garde (30 s) : un Timer invalide un résultat trop tardif.
            # NOTE (AUDIT.md §3.6) : l'ancien code tentait de fermer un attribut
            # Selenium `.driver` qui n'existe plus depuis la migration Playwright
            # (no-op silencieux). On ne force PAS la fermeture depuis le thread du
            # Timer : l'API sync de Playwright n'est pas thread-safe, et le scraper
            # applique déjà ses propres timeouts de navigation (goto timeout=30s).
            # (Branche Unix `signal.alarm` supprimée le 2026-07-11 — app Windows-only.)
            # Voie ASYNC : remplacé par asyncio.timeout (cf. enrich_async, F3c).
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

            return self._apply_track_data(track, ctx, track_data)

        except TimeoutError as e:
            logger.error(f"⏰ SongBPM timeout pour {track.title}: {e}")
            return False
        except Exception as e:
            logger.error(f"Erreur SongBPM pour {track.title}: {e}")
            return False

    def _apply_track_data(self, track: Track, ctx: EnrichmentContext, track_data: dict) -> bool:
        """Application des données SongBPM au track (commun sync/async)."""
        force_update = ctx.force_update
        artist_tracks = ctx.artist_tracks
        updated = False

        # BPM → candidat pour le vote (§8.3). E7 : plus de pose legacy directe —
        # apply_resolutions pose track.bpm en fin de run. Un candidat valide au
        # vote = SongBPM a contribué (même 2ᵉ vote concordant) → succès, sinon
        # faux « ÉCHEC » → nettoyage.
        sbpm = sanitize_bpm(track_data.get("bpm"))
        if sbpm is not None:
            ctx.bpm_ballot.add("songbpm", sbpm)
            logger.info(f"📊 BPM SongBPM (candidat): {sbpm} pour {track.title}")
            updated = True

        # Key et Mode
        key_value = track_data.get("key")
        mode_value = track_data.get("mode")

        # Observations key/mode PAR SOURCE (normalisées : "minor"→0, lettre→pc) —
        # corrige le bug WIP mode="minor". apply_resolutions repose key/mode/
        # key_mode_source en fin de run (plus de pose legacy directe, E7).
        ctx.observations.extend(key_mode_observations("songbpm", key=key_value, mode=mode_value))
        if key_value or mode_value:
            logger.info(f"🎵 Key/Mode SongBPM: {key_value}/{mode_value} pour {track.title}")
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
            logger.info(f"⏱️ Duration ajoutée depuis SongBPM: {track.duration} pour {track.title}")
            updated = True

        return updated

    async def enrich_async(self, track: Track, ctx: EnrichmentContext) -> bool:
        """Voie async (F3c) : scraper Playwright ASYNC natif dans la boucle ;
        `asyncio.timeout(30)` remplace le threading.Timer de garde — même
        budget, mais l'annulation INTERROMPT la recherche (le Timer laissait
        finir et jetait le résultat) et le driver est recyclé pour repartir
        sain. Sans variante async configurée, repli sur le pont sync F2."""
        scraper = self._async_resource.get()
        if scraper is None:
            return await ctx.sync_runner.run(self.enrich, track, ctx)

        try:
            artist_name = self._artist_name(track)
            spotify_id = self._validated_spotify_id(track, ctx)

            timeout_seconds = 30
            try:
                async with asyncio.timeout(timeout_seconds):
                    track_data = await scraper.search_track_async(
                        track.title, artist_name, spotify_id=spotify_id, fetch_details=True
                    )
            except TimeoutError:
                logger.error(f"⏰ SongBPM timeout après {timeout_seconds}s — recherche annulée")
                logger.error(f"❌ SongBPM: Timeout expiré pour '{track.title}'")
                await scraper.aclose()  # recyclé : recréé au prochain usage
                return False

            if not track_data:
                return False

            return self._apply_track_data(track, ctx, track_data)

        except Exception as e:
            logger.error(f"Erreur SongBPM pour {track.title}: {e}")
            return False
