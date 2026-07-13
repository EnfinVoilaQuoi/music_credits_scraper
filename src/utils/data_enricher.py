"""
Module d'enrichissement des données tracks
VERSION CORRIGÉE: Empêche la duplication des Spotify IDs + Intégration Spotify_ID scraper + GetSongBPM API
"""

import os

from src.api.deezer_api import DeezerAPI
from src.api.discogs_api import DiscogsClient
from src.api.getsongbpm_api import GetSongBPMFetcher
from src.api.reccobeats_api import ReccoBeatsIntegratedClient
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
        Initialise les scrapers et APIs

        Args:
            headless_reccobeats: Si True, lance ReccoBeats en mode headless
            headless_songbpm: Si True, lance SongBPM en mode headless (True par défaut)
        """
        self.apis_available = {
            "spotify_id": False,  # 1. Scraper Spotify_ID
            "reccobeats": False,  # 2. ReccoBeats
            "getsongbpm": False,  # 3. GetSongBPM API
            "songbpm": False,  # 4. SongBPM scraper
            "deezer": False,  # 5. Deezer API
            "discogs": False,
        }

        # Initialiser ReccoBeats (pas de clé API nécessaire)
        self.reccobeats_client = None
        try:
            self.reccobeats_client = ReccoBeatsIntegratedClient(headless=headless_reccobeats)
            # Vider le cache des erreurs précédentes si nécessaire
            if hasattr(self.reccobeats_client, "clear_old_errors"):
                self.reccobeats_client.clear_old_errors()
            self.apis_available["reccobeats"] = True
            logger.info("✅ ReccoBeats client initialisé")
        except Exception as e:
            logger.error(f"❌ Erreur init ReccoBeats: {e}")

        # Initialiser GetSongBPM API
        self.getsongbpm_fetcher = None
        try:
            self.getsongbpm_fetcher = GetSongBPMFetcher()
            self.apis_available["getsongbpm"] = True
            logger.info("✅ GetSongBPM API initialisée")
        except Exception as e:
            logger.warning(f"⚠️ GetSongBPM non disponible: {e}")

        # Provider GetSongBPM (pattern provider) — enveloppe le fetcher
        from src.enrichment.providers.getsongbpm import GetSongBpmProvider

        self._getsongbpm_provider = GetSongBpmProvider(self.getsongbpm_fetcher)

        # Initialiser SongBPM scraper
        self.songbpm_scraper = None
        try:
            self.songbpm_scraper = SongBPMScraper(headless=headless_songbpm)
            self.apis_available["songbpm"] = True
            logger.info("✅ SongBPM scraper initialisé (Selenium)")
        except Exception as e:
            logger.error(f"❌ Erreur init SongBPM: {e}")

        # Provider SongBPM (pattern provider) — enveloppe le scraper
        from src.enrichment.providers.songbpm import SongBpmProvider

        self._songbpm_provider = SongBpmProvider(self.songbpm_scraper)

        # Initialiser Spotify ID scraper
        self.spotify_id_scraper = None
        try:
            self.spotify_id_scraper = SpotifyIDScraper(headless=headless_spotify_scraper)
            self.apis_available["spotify_id"] = True
            logger.info("✅ Spotify ID scraper initialisé")
        except Exception as e:
            logger.error(f"❌ Erreur init Spotify ID scraper: {e}")

        # Provider Spotify ID (pattern provider) — enveloppe le scraper
        from src.enrichment.providers.spotify_id import SpotifyIdProvider

        self._spotify_id_provider = SpotifyIdProvider(self.spotify_id_scraper)

        # Initialiser Deezer API
        self.deezer_client = None
        try:
            self.deezer_client = DeezerAPI()
            self.apis_available["deezer"] = True
            logger.info("✅ Deezer API initialisée")
        except Exception as e:
            logger.error(f"❌ Erreur init Deezer API: {e}")

        # Provider Deezer (pattern provider, REFACTORING §3) — enveloppe le client
        from src.enrichment.providers.deezer import DeezerProvider

        self._deezer_provider = DeezerProvider(self.deezer_client)

        # Provider ReccoBeats — a besoin des clients Deezer (ISRC) et Spotify (scrape),
        # créés au-dessus : instancié ici une fois les trois disponibles.
        from src.enrichment.providers.reccobeats import ReccoBeatsProvider

        self._reccobeats_provider = ReccoBeatsProvider(
            self.reccobeats_client, self.deezer_client, self.spotify_id_scraper
        )

        # Initialiser Discogs API
        self.discogs_client = None
        try:
            # Chercher le token Discogs dans les variables d'environnement
            discogs_token = os.getenv("DISCOGS_TOKEN") or os.getenv("DISCOGS_USER_TOKEN")
            if discogs_token:
                self.discogs_client = DiscogsClient(user_token=discogs_token)
                self.apis_available["discogs"] = True
                logger.info("✅ Discogs API initialisée avec token (60 req/min)")
            else:
                # Initialiser quand même sans token (limité à 25 req/min)
                self.discogs_client = DiscogsClient()
                self.apis_available["discogs"] = True
                logger.info("✅ Discogs API initialisée sans token (25 req/min)")
        except Exception as e:
            logger.warning(f"⚠️ Discogs API non disponible: {e}")
            self.apis_available["discogs"] = False

        # Provider Discogs (pattern provider) — enveloppe le client
        from src.enrichment.providers.discogs import DiscogsProvider

        self._discogs_provider = DiscogsProvider(self.discogs_client)

        # BPM Finder (audioaidynamics) — dernier recours BPM/Key via lien YouTube
        # Nécessite BPMFINDER_EMAIL/PASSWORD (env/.env) ou une session sauvegardée.
        # L'attribut reste sur DataEnricher : la GUI y accède directement
        # (manual_entry / workers) ; le provider (état disjoncteur) l'enveloppe.
        self.bpmfinder_scraper = None
        try:
            from src.scrapers.bpmfinder_scraper import BPMFinderScraper

            if BPMFinderScraper.credentials_or_session_available():
                self.bpmfinder_scraper = BPMFinderScraper(headless=True)
                self.apis_available["bpmfinder"] = True
                logger.info("✅ BPM Finder scraper initialisé (audioaidynamics)")
            else:
                logger.info(
                    "⏭️ BPM Finder non configuré (BPMFINDER_EMAIL/PASSWORD ou session absents)"
                )
        except Exception as e:
            logger.warning(f"⚠️ BPM Finder non disponible: {e}")

        # Provider BPM Finder (pattern provider) — porte l'état du disjoncteur
        from src.enrichment.providers.bpmfinder import BpmFinderProvider

        self._bpmfinder_provider = BpmFinderProvider(self.bpmfinder_scraper)

        # Client Genius (pour les feats : media/album/relations avant ReccoBeats)
        self.genius_client = None
        try:
            from src.api.genius_api import GeniusAPI

            self.genius_client = GeniusAPI()
            logger.info("✅ Genius API initialisée (media feats)")
        except Exception as e:
            logger.debug(f"Genius API non disponible dans l'enricher: {e}")

        logger.info(f"Sources disponibles: {[k for k, v in self.apis_available.items() if v]}")

    def close(self):
        """Ferme toutes les connexions"""
        if getattr(self, "bpmfinder_scraper", None):
            try:
                self.bpmfinder_scraper.close()
            except Exception:
                pass
        if self.reccobeats_client:
            try:
                self.reccobeats_client.close()
                logger.info("ReccoBeats client fermé")
            except Exception as e:
                logger.error(f"Erreur fermeture ReccoBeats: {e}")

        if self.songbpm_scraper:
            try:
                self.songbpm_scraper.close()
                logger.info("SongBPM scraper fermé")
            except Exception as e:
                logger.error(f"Erreur fermeture SongBPM: {e}")

        # Playwright lui aussi : sans fermeture explicite il ne mourait qu'au
        # __del__ pendant l'arrêt de l'interpréteur → browser orphelin → EPIPE
        # du driver Node à la fermeture de l'app.
        if getattr(self, "spotify_id_scraper", None):
            try:
                self.spotify_id_scraper.close()
                logger.info("Spotify ID scraper fermé")
            except Exception as e:
                logger.error(f"Erreur fermeture Spotify ID scraper: {e}")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __del__(self):
        """Cleanup des ressources"""
        try:
            self.close()
        except Exception:
            pass

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
        if track.bpm is not None:
            old_value = track.bpm
            track.bpm = None
            logger.info(f"   ✅ BPM effacé: {old_value} → None")
            cleaned = True

        # Effacer Key (attribut dynamique du mapper → hasattr requis)
        if hasattr(track, "key") and track.key is not None:
            old_value = track.key
            track.key = None
            logger.info(f"   ✅ Key effacée: {old_value} → None")
            cleaned = True

        # Effacer Mode (attribut dynamique du mapper → hasattr requis)
        if hasattr(track, "mode") and track.mode is not None:
            old_value = track.mode
            track.mode = None
            logger.info(f"   ✅ Mode effacé: {old_value} → None")
            cleaned = True

        # Effacer Duration
        if track.duration is not None:
            old_value = track.duration
            track.duration = None
            logger.info(f"   ✅ Duration effacée: {old_value} → None")
            cleaned = True

        # Effacer Musical Key (format français)
        if track.musical_key is not None:
            old_value = track.musical_key
            track.musical_key = None
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
            logger.info(
                f"✅ Nettoyage terminé pour '{track.title}' - Artiste intact: {track.artist}"
            )
        else:
            logger.info(f"ℹ️ Aucune donnée à nettoyer pour '{track.title}'")

        return cleaned

    def clear_multiple_tracks_data(
        self, tracks: list[Track], clear_spotify_id: bool = False
    ) -> int:
        """
        Nettoie les données de plusieurs tracks

        Args:
            tracks: Liste des tracks à nettoyer
            clear_spotify_id: Si True, efface aussi les Spotify IDs

        Returns:
            int: Nombre de tracks nettoyés
        """
        cleaned_count = 0

        logger.info(f"🗑️ Nettoyage de {len(tracks)} track(s)")

        for track in tracks:
            if self.clear_track_data(track, clear_spotify_id=clear_spotify_id):
                cleaned_count += 1

        logger.info(f"✅ {cleaned_count}/{len(tracks)} track(s) nettoyé(s)")
        return cleaned_count

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
        logger.info(f"🔍 État actuel: spotify_id={track.spotify_id}, bpm={track.bpm}")

        # Sauvegarder l'état initial (pour la logique force_update du BPM)
        initial_bpm = track.bpm

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

        # ========================================
        # VOTE BPM : réconciliation de tous les candidats (§8.3), AVANT Discogs
        # ========================================
        ballot.finalize(track)

        self._run_step(self._discogs_provider, track, ctx, sources, results)

        if clear_on_failure and force_update:
            self._clear_after_total_failure(track, results, initial_bpm)

        # ========================================
        # RÉSUMÉ FINAL
        # ========================================
        logger.info(f"📊 RÉSUMÉ enrichissement '{track.title}':")
        logger.info(f"   • Résultats: {results}")
        logger.info(f"   • Spotify ID: {track.spotify_id}")
        logger.info(f"   • BPM: {track.bpm}")
        # key/mode/deezer_id = attributs dynamiques (mapper/providers) → getattr
        logger.info(
            f"   • Key: {getattr(track, 'key', 'N/A')}, Mode: {getattr(track, 'mode', 'N/A')}"
        )
        logger.info(f"   • Musical Key: {track.musical_key}")
        logger.info(f"   • Duration: {track.duration}")
        logger.info(f"   • Release Date: {track.release_date}")
        logger.info(f"   • Deezer ID: {getattr(track, 'deezer_id', 'N/A')}")
        logger.info(f"   • Discogs ID: {track.discogs_id}")
        logger.info(f"   • Crédits totaux: {len(track.credits)}")

        return results

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
        old_bpm = track.bpm
        track.bpm = None
        logger.info(f"   🗑️ BPM effacé: {old_bpm} → None")

        # key/mode : attributs dynamiques du mapper → hasattr requis
        if hasattr(track, "key"):
            old_key = track.key
            track.key = None
            logger.info(f"   🗑️ Key effacée: {old_key} → None")

        if hasattr(track, "mode"):
            old_mode = track.mode
            track.mode = None
            logger.info(f"   🗑️ Mode effacé: {old_mode} → None")

        old_duration = track.duration
        track.duration = None
        logger.info(f"   🗑️ Duration effacée: {old_duration} → None")

        old_musical_key = track.musical_key
        track.musical_key = None
        logger.info(f"   🗑️ Musical Key effacée: {old_musical_key} → None")

        # Vérification post-nettoyage
        if not track.title:
            logger.error("❌ ERREUR CRITIQUE: Le titre a disparu après nettoyage!")
        elif not track.artist:
            logger.error("❌ ERREUR CRITIQUE: L'artiste a disparu après nettoyage!")
        else:
            logger.info(f"✅ Données erronées nettoyées pour '{track.title}'")
            results["cleaned"] = True
