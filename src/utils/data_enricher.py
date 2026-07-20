"""
Module d'enrichissement des données tracks
VERSION CORRIGÉE: Empêche la duplication des Spotify IDs + Intégration Spotify_ID scraper + GetSongBPM API
"""

import os

from src.api.async_http import AsyncHttpSession
from src.api.deezer_api import DeezerAPI
from src.api.discogs_api import DiscogsClient
from src.api.getsongbpm_api import GetSongBPMFetcher
from src.api.reccobeats_api import ReccoBeatsIntegratedClient
from src.concurrency.serial_worker import SerialWorker
from src.models import Track
from src.scrapers.songbpm_scraper_v2 import SongBPMScraper
from src.scrapers.spotify_id_scraper_v2 import SpotifyIDScraper
from src.utils.bpm_vote import BpmBallot
from src.utils.logger import get_logger

# NB : les modules de src.enrichment sont importés LOCALEMENT (dans __init__ /
# enrich_track), pas au niveau module : src/utils/__init__ charge ce fichier,
# et src.enrichment ré-importe src.utils → un import module-niveau ici bouclerait.

logger = get_logger(__name__)


class DataEnricher:
    """Enrichissement des données des morceaux"""

    def __init__(
        self,
        headless_reccobeats: bool = False,
        headless_songbpm: bool = True,
        headless_spotify_scraper: bool = True,
    ):
        """
        Compose les providers d'enrichissement (ownership Refacto Phase 3.5).

        Les ressources (browsers Playwright, clients HTTP des sources) sont
        créées LAZY au premier usage — dans le thread du batch — et fermées par
        close() ; chaque provider possède et ferme les siennes (« qui crée
        ferme »). Ici ne vivent que les clients racine PARTAGÉS sans ressource
        à fermer (Deezer, Genius).

        Args:
            headless_reccobeats: conservé pour compatibilité (client HTTP)
            headless_songbpm: Si True, lance SongBPM en mode headless
            headless_spotify_scraper: Si True, lance le scraper Spotify en headless
        """
        # NB : imports providers LOCAUX (règle anti-boucle src.utils ↔ src.enrichment).
        from src.enrichment.providers.bpmfinder import BpmFinderProvider
        from src.enrichment.providers.deezer import DeezerProvider
        from src.enrichment.providers.discogs import DiscogsProvider
        from src.enrichment.providers.getsongbpm import GetSongBpmProvider
        from src.enrichment.providers.reccobeats import ReccoBeatsProvider
        from src.enrichment.providers.songbpm import SongBpmProvider
        from src.enrichment.providers.spotify_id import SpotifyIdProvider
        from src.scrapers.bpmfinder_scraper import BPMFinderScraper

        # Clients racine partagés : Deezer (DeezerProvider + résolution ISRC de
        # ReccoBeats), Genius (pré-étape media des feats).
        self.deezer_client = None
        try:
            self.deezer_client = DeezerAPI()
            logger.info("✅ Deezer API initialisée")
        except Exception as e:
            logger.error(f"❌ Erreur init Deezer API: {e}")

        self.genius_client = None
        try:
            from src.api.genius_api import GeniusAPI

            self.genius_client = GeniusAPI()
            logger.info("✅ Genius API initialisée (media feats)")
        except Exception as e:
            logger.debug(f"Genius API non disponible dans l'enricher: {e}")

        from src.scrapers.spotify_id_scraper_async import SpotifyIDScraperAsync

        self._spotify_id_provider = SpotifyIdProvider(
            scraper_factory=lambda: SpotifyIDScraper(headless=headless_spotify_scraper),
            # Variante ASYNC (F3b) : vit dans la boucle, fermée par aclose().
            async_scraper_factory=lambda: SpotifyIDScraperAsync(headless=headless_spotify_scraper),
        )
        self._reccobeats_provider = ReccoBeatsProvider(
            client_factory=lambda: ReccoBeatsIntegratedClient(headless=headless_reccobeats),
            deezer_client=self.deezer_client,
            # EMPRUNT : le scraper Spotify appartient à SpotifyIdProvider (qui le
            # ferme) ; ReccoBeats l'utilise au moment du fallback, sans le posséder.
            spotify_scraper_getter=lambda: self._spotify_id_provider.scraper,
            spotify_scraper_async_getter=lambda: self._spotify_id_provider.async_scraper,
        )
        # Factory seulement si la clé API est présente (le ctor lève sans elle) :
        # même visibilité qu'avant dans la liste de sources de la GUI.
        self._getsongbpm_provider = GetSongBpmProvider(
            fetcher_factory=GetSongBPMFetcher if os.getenv("GETSONGBPM_API_KEY") else None
        )
        from src.scrapers.songbpm_scraper_async import SongBPMScraperAsync

        self._songbpm_provider = SongBpmProvider(
            scraper_factory=lambda: SongBPMScraper(headless=headless_songbpm),
            # Variante ASYNC (F3c) : vit dans la boucle, fermée par aclose().
            async_scraper_factory=lambda: SongBPMScraperAsync(headless=headless_songbpm),
        )
        # Factory seulement si identifiants/session présents (même règle qu'avant).
        # F3d : le jumeau ASYNC (BPMFinderScraperAsync) est PRÉPARÉ mais NON activé
        # — enrich_async retombe sur le pont sync. Activation = ajouter
        # `async_scraper_factory=lambda: BPMFinderScraperAsync(headless=True)` ici
        # + le provider dans aclose_async_scrapers, après un run réel (login/quota,
        # backend audioaidynamics rétabli). Cf. src/scrapers/bpmfinder_scraper_async.py.
        if BPMFinderScraper.credentials_or_session_available():
            self._bpmfinder_provider = BpmFinderProvider(
                scraper_factory=lambda: BPMFinderScraper(headless=True)
            )
        else:
            self._bpmfinder_provider = BpmFinderProvider()
            logger.info("⏭️ BPM Finder non configuré (BPMFINDER_EMAIL/PASSWORD ou session absents)")
        self._deezer_provider = DeezerProvider(self.deezer_client)
        discogs_token = os.getenv("DISCOGS_TOKEN") or os.getenv("DISCOGS_USER_TOKEN")
        self._discogs_provider = DiscogsProvider(
            client_factory=lambda: (
                DiscogsClient(user_token=discogs_token) if discogs_token else DiscogsClient()
            )
        )

        # Voie async (Phase F2) : session httpx PARTAGÉE (créée lazy dans la
        # boucle, fermée par aclose_http en fin de batch) + thread sync dédié
        # du flux (affinité Playwright — les scrapers y naissent et y meurent).
        self._http = AsyncHttpSession()
        self.sync_runner = SerialWorker("enrich-sync")

        self.apis_available = {
            p.name: p.is_available() for p in [*self._pipeline, self._discogs_provider]
        }
        logger.info(f"Sources disponibles: {[k for k, v in self.apis_available.items() if v]}")

    @property
    def bpmfinder_scraper(self):
        """Accès GUI direct (manual_entry ✏️) au scraper BPM Finder — créé à la
        demande, None si la source n'est pas configurée."""
        return self._bpmfinder_provider.scraper

    def close(self):
        """Ferme les ressources de toutes les sources (idempotent, ré-ouvrable :
        les ressources possédées sont recréées à la demande au run suivant).

        À appeler dans le thread qui a fait tourner le batch (browsers
        Playwright thread-affines) : finally du worker d'enrichissement, et
        _on_closing de la GUI pour le reliquat du main thread — sans fermeture
        explicite, un browser orphelin ne mourait qu'au shutdown → EPIPE du
        driver Node.
        """
        for provider in [*self._pipeline, self._discogs_provider]:
            try:
                provider.close()
            except Exception as e:
                logger.warning(f"⚠️ Fermeture provider {provider.name}: {e}")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    # ================== NOUVEAU: VALIDATION SPOTIFY ID ==================

    def validate_spotify_id_unique(
        self, spotify_id: str, current_track: Track, artist_tracks: list[Track]
    ) -> bool:
        """
        Valide qu'un Spotify ID n'est pas utilisé par un AUTRE titre
        VERSION AMÉLIORÉE: Accepte plusieurs IDs pour le MÊME titre
        """
        if not spotify_id or not artist_tracks:
            return True

        current_title_normalized = self._normalize_title(current_track.title)

        for track in artist_tracks:
            # Tous les IDs de ce track (méthode du modèle : [] si aucun)
            track_ids = track.get_all_spotify_ids()

            # Vérifier si cet ID est déjà utilisé
            if spotify_id in track_ids:
                track_title_normalized = self._normalize_title(track.title)

                # ✅ C'est le MÊME morceau : OK
                if track_title_normalized == current_title_normalized:
                    logger.info("✅ ID déjà utilisé par le même titre (version alternative)")
                    return True

                # ❌ C'est un AUTRE morceau : REJET
                else:
                    logger.warning(f"❌ ID déjà utilisé par un autre titre: '{track.title}'")
                    return False

        return True

    # get_unique_spotify_id : déplacé dans src/enrichment/providers/spotify_id.py
    # (SpotifyIdProvider.get_unique_spotify_id). L'unicité d'ID reste
    # validate_spotify_id_unique ci-dessus (partagée, injectée via le contexte).

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
        if not track.title:
            logger.error("❌ ERREUR CRITIQUE: Track sans titre détecté! Annulation du nettoyage.")
            return False

        # Effacer BPM
        if track.audio.bpm is not None:
            old_value = track.audio.bpm
            track.audio.bpm = None
            logger.info(f"   ✅ BPM effacé: {old_value} → None")
            cleaned = True

        # Effacer Key (champ du sous-objet audio — Phase 5)
        if track.audio.key is not None:
            old_value = track.audio.key
            track.audio.key = None
            logger.info(f"   ✅ Key effacée: {old_value} → None")
            cleaned = True

        # Effacer Mode (champ du sous-objet audio — Phase 5)
        if track.audio.mode is not None:
            old_value = track.audio.mode
            track.audio.mode = None
            logger.info(f"   ✅ Mode effacé: {old_value} → None")
            cleaned = True

        # Effacer Duration
        if track.duration is not None:
            old_value = track.duration
            track.duration = None
            logger.info(f"   ✅ Duration effacée: {old_value} → None")
            cleaned = True

        # Effacer Musical Key (format français)
        if track.audio.musical_key is not None:
            old_value = track.audio.musical_key
            track.audio.musical_key = None
            logger.info(f"   ✅ Musical Key effacée: {old_value} → None")
            cleaned = True

        # Effacer Spotify ID (optionnel)
        if clear_spotify_id and track.spotify_id is not None:
            old_value = track.spotify_id
            track.spotify_id = None
            logger.info(f"   ✅ Spotify ID effacé: {old_value} → None")
            cleaned = True

        # VÉRIFICATION POST-NETTOYAGE: S'assurer que les données essentielles sont toujours là
        if not track.title:
            logger.error("❌ ERREUR CRITIQUE POST-NETTOYAGE: Le titre a disparu!")
            return False

        if not track.artist:
            logger.error("❌ ERREUR CRITIQUE POST-NETTOYAGE: L'artiste a disparu!")
            return False

        if cleaned:
            # E7-D1 : demander la suppression des observations audio au prochain
            # save (sinon résurrection à la lecture via la réconciliation).
            track.clear_audio_observations = True
            logger.info(
                f"✅ Nettoyage terminé pour '{track.title}' - Artiste intact: {track.artist}"
            )
        else:
            logger.info(f"ℹ️ Aucune donnée à nettoyer pour '{track.title}'")

        return cleaned

    def _normalize_title(self, title: str) -> str:
        """
        Normalise un titre pour comparaison
        """
        import re
        import unicodedata

        title = title.lower()

        # Supprimer les accents
        title = "".join(
            c for c in unicodedata.normalize("NFD", title) if unicodedata.category(c) != "Mn"
        )

        # Supprimer feat., parenthèses, etc.
        title = re.sub(r"\(.*?\)", "", title)
        title = re.sub(r"\[.*?\]", "", title)
        title = re.sub(r"feat\..*$", "", title, flags=re.IGNORECASE)
        title = re.sub(r"ft\..*$", "", title, flags=re.IGNORECASE)

        # Supprimer la ponctuation
        title = re.sub(r"[^\w\s]", "", title)

        # Supprimer les espaces multiples
        title = " ".join(title.split())

        return title.strip()

    # ================================================================

    def get_available_sources(self) -> list[str]:
        """Retourne la liste des sources disponibles"""
        return [k for k, v in self.apis_available.items() if v]

    def reset_bpmfinder_breaker(self):
        """Ré-arme le disjoncteur BPM Finder — à appeler en début de run
        d'enrichissement (l'instance DataEnricher vit toute la session GUI,
        « pour le reste du run » ne doit pas déborder sur le run suivant)."""
        self._bpmfinder_provider.reset_breaker()

    def enrich_track(
        self,
        track: Track,
        sources: list[str] | None = None,
        force_update: bool = False,
        artist_tracks: list[Track] | None = None,
        clear_on_failure: bool = True,
    ) -> dict[str, bool]:
        """
        Enrichit un morceau : boucle ordonnée de providers (gate → enrich).

        Pré-étapes d'orchestrateur : Genius media (feats) puis voie ISRC (évite
        le scrape Spotify si l'ISRC suffit — lue par les gates spotify_id et
        reccobeats). Le vote BPM est arbitré entre Deezer et Discogs (position
        historique). Le gating et la valeur d'échec de chaque source vivent
        dans son provider (gate() / error_result).
        """
        sources, ctx, ballot, results, initial_bpm = self._start_run(
            track, sources, force_update, artist_tracks, clear_on_failure
        )

        self._apply_genius_feat_metadata(track)

        # VOIE ISRC PRIORITAIRE (avant tout scrape Playwright) : si l'ISRC
        # fournit BPM/Key via ReccoBeats, les gates spotify_id/reccobeats skippent.
        if "reccobeats" in sources and self.apis_available.get("reccobeats"):
            try:
                ctx.isrc_satisfied = self._reccobeats_provider.try_by_isrc(track, ctx)
                if ctx.isrc_satisfied:
                    logger.info(
                        f"⚡ ISRC a fourni les données audio pour '{track.title}' → scrape Spotify évité"
                    )
            except Exception as e:
                logger.debug(f"Voie ISRC échec: {e}")

        # Boucle ordonnée : gate() décide (skip → valeur posée telle quelle),
        # enrich() tourne derrière la frontière d'exception de _run_step.
        for provider in self._pipeline:
            self._run_step(provider, track, ctx, sources, results)

        # Réconciliation (§8.3) — le MOTEUR pilote les colonnes legacy, AVANT Discogs
        self._finalize_run(track, ballot, ctx)

        self._run_step(self._discogs_provider, track, ctx, sources, results)

        if clear_on_failure and force_update:
            self._clear_after_total_failure(track, results, initial_bpm)

        self._log_run_summary(track, results)
        return results

    async def enrich_track_async(
        self,
        track: Track,
        sources: list[str] | None = None,
        force_update: bool = False,
        artist_tracks: list[Track] | None = None,
        clear_on_failure: bool = True,
    ) -> dict[str, bool]:
        """Jumeau async d'`enrich_track` (Phase F2) — même orchestration, mêmes
        gates, mêmes valeurs de résultat.

        Providers API purs (deezer, getsongbpm, reccobeats) via la session
        httpx partagée ; scrapers sync (spotify_id, songbpm, bpmfinder,
        discogs, Genius) sur le thread sync dédié du run (affinité Playwright).
        """
        sources, ctx, ballot, results, initial_bpm = self._start_run(
            track, sources, force_update, artist_tracks, clear_on_failure
        )
        ctx.http = self._http
        ctx.sync_runner = self.sync_runner

        await ctx.sync_runner.run(self._apply_genius_feat_metadata, track)

        # VOIE ISRC PRIORITAIRE (même gating que la voie sync)
        if "reccobeats" in sources and self.apis_available.get("reccobeats"):
            try:
                ctx.isrc_satisfied = await self._reccobeats_provider.try_by_isrc_async(track, ctx)
                if ctx.isrc_satisfied:
                    logger.info(
                        f"⚡ ISRC a fourni les données audio pour '{track.title}' → scrape Spotify évité"
                    )
            except Exception as e:
                logger.debug(f"Voie ISRC échec: {e}")

        for provider in self._pipeline:
            await self._run_step_async(provider, track, ctx, sources, results)

        # Réconciliation (§8.3) — le MOTEUR pilote les colonnes legacy, AVANT Discogs
        self._finalize_run(track, ballot, ctx)

        await self._run_step_async(self._discogs_provider, track, ctx, sources, results)

        if clear_on_failure and force_update:
            self._clear_after_total_failure(track, results, initial_bpm)

        self._log_run_summary(track, results)
        return results

    def _start_run(self, track, sources, force_update, artist_tracks, clear_on_failure):
        """Sources par défaut + contexte + scrutin d'un run (commun sync/async)."""
        if sources is None:
            sources = [
                "spotify_id",
                "reccobeats",
                "getsongbpm",
                "songbpm",
                "bpmfinder",
                "deezer",
                "discogs",
            ]

        results = {}

        logger.info(
            f"🔍 Enrichissement: track='{track.title}', sources={sources}, force_update={force_update}"
        )
        logger.info(f"🔍 État actuel: spotify_id={track.spotify_id}, bpm={track.audio.bpm}")

        # Sauvegarder l'état initial (pour la logique force_update du BPM)
        initial_bpm = track.audio.bpm

        from src.enrichment.context import EnrichmentContext

        # Scrutin BPM partagé par toutes les sources du run, arbitré entre
        # Deezer et Discogs. `results` est partagé avec les gates (raisons).
        ballot = BpmBallot()
        ctx = EnrichmentContext(
            force_update=force_update,
            artist_tracks=artist_tracks or [],
            bpm_ballot=ballot,
            clear_on_failure=clear_on_failure,
            validate_spotify_id_unique=self.validate_spotify_id_unique,
            # ReccoBeats ne re-scrape pas le Spotify ID si l'étape spotify_id le fait déjà
            allow_spotify_scrape=("spotify_id" not in sources),
            results=results,
        )
        return sources, ctx, ballot, results, initial_bpm

    def _finalize_run(self, track, ballot, ctx) -> None:
        """Réconciliation du run (commun sync/async).

        Observations PAR SOURCE du run (BPM du scrutin + key/mode normalisés
        émis par les providers), puis reconcile() pilote bpm/bpm_alt/source/
        confidence + key/mode/musical_key — remplace BpmBallot.finalize. BPM iso
        par construction (même reconcile_bpm) ; key/mode normalisés + appariés
        (corrige mode="minor" côté legacy). Fresh UNIQUEMENT en 2b-ii : l'union
        persisté ∪ frais (vote inter-runs, point-2) = étape ultérieure isolée.
        save_track persiste track.observations dans SA transaction (E5c-1).
        """
        from src.enrichment.reconcile import apply_resolutions, reconcile

        track.observations = self._collect_run_observations(ballot.candidates, ctx.observations)
        apply_resolutions(track, reconcile(track.observations, track_duration=track.duration))
        logger.info(
            f"🧮 Réconciliation: BPM={track.audio.bpm} (alt={track.audio.bpm_alt}, "
            f"source={track.audio.bpm_source}, conf={track.audio.bpm_confidence})"
        )

    def _log_run_summary(self, track, results: dict) -> None:
        """Résumé final d'un run (commun sync/async)."""
        logger.info(f"📊 RÉSUMÉ enrichissement '{track.title}':")
        logger.info(f"   • Résultats: {results}")
        logger.info(f"   • Spotify ID: {track.spotify_id}")
        logger.info(f"   • BPM: {track.audio.bpm}")
        # key/mode/deezer_id = attributs dynamiques (mapper/providers) → getattr
        logger.info(
            f"   • Key: {getattr(track, 'key', 'N/A')}, Mode: {getattr(track, 'mode', 'N/A')}"
        )
        logger.info(f"   • Musical Key: {track.audio.musical_key}")
        logger.info(f"   • Duration: {track.duration}")
        logger.info(f"   • Release Date: {track.release_date}")
        logger.info(f"   • Deezer ID: {getattr(track, 'deezer_id', 'N/A')}")
        logger.info(f"   • Discogs ID: {track.discogs_id}")
        logger.info(f"   • Crédits totaux: {len(track.credits)}")

    # ──────────────────────────────────────────────────────────────────────
    # Orchestration (Refacto Phase 3.4) — l'ordre d'appel des sources est
    # encodé dans _pipeline, le gating dans les gate() des providers.
    # ──────────────────────────────────────────────────────────────────────

    @property
    def _pipeline(self):
        """Ordre d'appel historique des sources AVANT le vote BPM (Discogs
        vient après le vote). Relu à chaque accès : les tests substituent les
        providers par attribut."""
        return [
            self._spotify_id_provider,
            self._reccobeats_provider,
            self._getsongbpm_provider,
            self._songbpm_provider,
            self._bpmfinder_provider,
            self._deezer_provider,
        ]

    def _run_step(self, provider, track, ctx, sources: list[str], results: dict) -> None:
        """Exécute une source si demandée et disponible : gate() puis enrich().

        Frontière d'exception du batch : un provider qui lève ne stoppe pas le
        run, la valeur d'échec posée est déclarée par le provider
        (`error_result` : False = pas de données, None = crash/timeout).
        """
        name = provider.name
        if name not in sources or not self.apis_available.get(name):
            return

        verdict = provider.gate(track, ctx)
        if verdict is not None:
            results[name] = verdict
            return

        try:
            outcome = provider.enrich(track, ctx)
            results[name] = outcome
            if outcome is True:
                logger.info(f"✅ {name} SUCCÈS pour '{track.title}'")
            elif outcome is False:
                logger.warning(f"❌ {name} ÉCHEC pour '{track.title}'")
        except Exception as e:
            logger.error(f"❌ Erreur {name} pour {track.title}: {e}")
            results[name] = provider.error_result

    async def _run_step_async(
        self, provider, track, ctx, sources: list[str], results: dict
    ) -> None:
        """Jumeau async de `_run_step` : mêmes gates, même frontière d'exception."""
        name = provider.name
        if name not in sources or not self.apis_available.get(name):
            return

        verdict = provider.gate(track, ctx)
        if verdict is not None:
            results[name] = verdict
            return

        try:
            outcome = await provider.enrich_async(track, ctx)
            results[name] = outcome
            if outcome is True:
                logger.info(f"✅ {name} SUCCÈS pour '{track.title}'")
            elif outcome is False:
                logger.warning(f"❌ {name} ÉCHEC pour '{track.title}'")
        except Exception as e:
            logger.error(f"❌ Erreur {name} pour {track.title}: {e}")
            results[name] = provider.error_result

    async def aclose_http(self) -> None:
        """Ferme la session httpx partagée (fin de batch async) ; rouverte à la
        demande au batch suivant. À appeler DANS la boucle asyncio."""
        await self._http.aclose()

    async def aclose_async_scrapers(self) -> None:
        """Ferme les scrapers Playwright ASYNC des providers (F3) — browsers de
        la boucle, recréés à la demande au batch suivant. À appeler DANS la
        boucle, AVANT stop_playwright_async."""
        for provider in (self._spotify_id_provider, self._songbpm_provider):
            try:
                await provider.aclose()
            except Exception as e:
                logger.warning(f"⚠️ Fermeture async provider {provider.name}: {e}")

    def _apply_genius_feat_metadata(self, track) -> None:
        """FEATS : media/album/relations via API Genius AVANT ReccoBeats
        (le Spotify ID Genius fiabilise la chaîne ; 1 appel/feat espace les
        requêtes). Les primaires ont déjà été traités à l'import
        (_prefill_via_song_api)."""
        if not (
            track.is_featuring
            and self.genius_client
            and track.genius_id
            and (not track.spotify_id or not track.relationships)
        ):
            return
        try:
            if self.genius_client.apply_song_metadata(track):
                logger.info(
                    f"🎫 Genius (feat) '{track.title}' : Spotify={track.spotify_id}, "
                    f"relations={len(track.relationships or [])}"
                )
        except Exception as e:
            logger.debug(f"Genius media feat échec: {e}")

    def _clear_after_total_failure(self, track, results: dict, initial_bpm) -> None:
        """Efface les données musicales si TOUTES les sources ayant tenté ont échoué.

        Sources ayant RÉELLEMENT tenté = ni 'skipped' ni 'not_needed'.
        ⚠️ all([]) == True en Python : si TOUTES les sources sont
        'not_needed'/'skipped' (aucune n'a tourné), il ne faut PAS conclure
        « tout a échoué » et effacer des données valides (bug ayant vidé
        TOTAL 90 : 100 BPM/Do majeur légitimes). None (crash/timeout) n'est
        pas non plus un échec de données : il bloque aussi le nettoyage.
        """
        attempted = [r for r in results.values() if r not in ("skipped", "not_needed")]
        all_failed = bool(attempted) and all(r is False for r in attempted)
        if not (all_failed and initial_bpm is not None):
            return

        # Vérification de sécurité
        if not track.title:
            logger.error("❌ ERREUR: Track sans titre, annulation du nettoyage")
            return

        logger.warning(f"⚠️ NETTOYAGE: Aucune source n'a trouvé de données pour '{track.title}'")
        logger.warning("⚠️ Effacement des anciennes valeurs potentiellement erronées...")

        # Effacer UNIQUEMENT les données musicales
        old_bpm = track.audio.bpm
        track.audio.bpm = None
        logger.info(f"   🗑️ BPM effacé: {old_bpm} → None")

        # key/mode : champs du sous-objet audio (Phase 5), toujours présents
        old_key = track.audio.key
        track.audio.key = None
        logger.info(f"   🗑️ Key effacée: {old_key} → None")

        old_mode = track.audio.mode
        track.audio.mode = None
        logger.info(f"   🗑️ Mode effacé: {old_mode} → None")

        old_duration = track.duration
        track.duration = None
        logger.info(f"   🗑️ Duration effacée: {old_duration} → None")

        old_musical_key = track.audio.musical_key
        track.audio.musical_key = None
        logger.info(f"   🗑️ Musical Key effacée: {old_musical_key} → None")

        # Vérification post-nettoyage
        if not track.title:
            logger.error("❌ ERREUR CRITIQUE: Le titre a disparu après nettoyage!")
        elif not track.artist:
            logger.error("❌ ERREUR CRITIQUE: L'artiste a disparu après nettoyage!")
        else:
            logger.info(f"✅ Données erronées nettoyées pour '{track.title}'")
            results["cleaned"] = True
            # E7-D1 : ne rien upserter ET demander la SUPPRESSION des observations
            # audio persistées (sinon la réconciliation ressusciterait les valeurs
            # effacées à la lecture — la vérité vit dans `observations`).
            track.observations = []
            track.clear_audio_observations = True

    def _collect_run_observations(self, bpm_candidates, key_mode_observations):
        """Observations PAR SOURCE de ce run (phase E5c-2b-i).

        - **BPM** : une observation par source ayant voté (candidats bruts du
          scrutin, avant réconciliation ; `confidence` None → le moteur la
          recalcule au vote). Cohabite sans dommage avec la ligne backfill
          « source combinée » (`reccobeats+songbpm`) : la réconciliation ne tape
          que les observations FRAÎCHES du run, pas l'union persistée — le
          nettoyage des lignes combinées accompagnera l'introduction de l'union
          (vote inter-runs, étape ultérieure).
        - **key/mode** : observations PAR SOURCE normalisées, émises par les
          providers dans `ctx.observations` (chaque source qui a mesuré, pas
          seulement le last-writer legacy).

        Sans effet de bord : renvoie la liste, l'orchestrateur la pose sur
        `track.observations` (drainé par `save_track`).
        """
        from src.enrichment.observation import Observation

        observations = [Observation("bpm", value, source) for source, value in bpm_candidates]
        observations.extend(key_mode_observations)
        return observations
