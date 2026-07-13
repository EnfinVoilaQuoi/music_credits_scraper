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
from src.utils.bpm_vote import BpmBallot, sanitize_bpm
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
            # Récupérer tous les IDs de ce track
            if hasattr(track, "get_all_spotify_ids"):
                track_ids = track.get_all_spotify_ids()
            elif hasattr(track, "spotify_id") and track.spotify_id:
                track_ids = [track.spotify_id]
            else:
                track_ids = []

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
        if not hasattr(track, "title") or not track.title:
            logger.error("❌ ERREUR CRITIQUE: Track sans titre détecté! Annulation du nettoyage.")
            return False

        if not hasattr(track, "artist"):
            logger.error(
                f"❌ ERREUR CRITIQUE: Track '{track.title}' sans artiste! Annulation du nettoyage."
            )
            return False

        # Effacer BPM
        if hasattr(track, "bpm") and track.bpm is not None:
            old_value = track.bpm
            track.bpm = None
            logger.info(f"   ✅ BPM effacé: {old_value} → None")
            cleaned = True

        # Effacer Key
        if hasattr(track, "key") and track.key is not None:
            old_value = track.key
            track.key = None
            logger.info(f"   ✅ Key effacée: {old_value} → None")
            cleaned = True

        # Effacer Mode
        if hasattr(track, "mode") and track.mode is not None:
            old_value = track.mode
            track.mode = None
            logger.info(f"   ✅ Mode effacé: {old_value} → None")
            cleaned = True

        # Effacer Duration
        if hasattr(track, "duration") and track.duration is not None:
            old_value = track.duration
            track.duration = None
            logger.info(f"   ✅ Duration effacée: {old_value} → None")
            cleaned = True

        # Effacer Musical Key (format français)
        if hasattr(track, "musical_key") and track.musical_key is not None:
            old_value = track.musical_key
            track.musical_key = None
            logger.info(f"   ✅ Musical Key effacée: {old_value} → None")
            cleaned = True

        # Effacer Spotify ID (optionnel)
        if clear_spotify_id and hasattr(track, "spotify_id") and track.spotify_id is not None:
            old_value = track.spotify_id
            track.spotify_id = None
            logger.info(f"   ✅ Spotify ID effacé: {old_value} → None")
            cleaned = True

        # VÉRIFICATION POST-NETTOYAGE: S'assurer que les données essentielles sont toujours là
        if not hasattr(track, "title") or not track.title:
            logger.error("❌ ERREUR CRITIQUE POST-NETTOYAGE: Le titre a disparu!")
            return False

        if not hasattr(track, "artist") or not track.artist:
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
        Enrichit un morceau avec les sources spécifiées
        VERSION CORRIGÉE: Avec validation Spotify ID + logs détaillés + fallback SongBPM + Deezer + GetSongBPM + Discogs
        ORDRE: 1. Spotify ID, 2. ReccoBeats, 3. GetSongBPM, 4. SongBPM, 5. Deezer, 6. Discogs
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
        logger.info(
            f"🔍 État actuel: spotify_id={getattr(track, 'spotify_id', None)}, bpm={getattr(track, 'bpm', None)}"
        )

        # Sauvegarder l'état initial (pour la logique force_update du BPM)
        initial_bpm = getattr(track, "bpm", None)

        from src.enrichment.context import EnrichmentContext

        # Scrutin BPM partagé par toutes les sources du run (vote final : _finalize_bpm).
        # Migration provider en cours : les sources déjà migrées lisent le scrutin
        # via le contexte, les autres via track._bpm_ballot — MÊME instance.
        ballot = BpmBallot()
        track._bpm_ballot = ballot
        ctx = EnrichmentContext(
            force_update=force_update,
            artist_tracks=artist_tracks or [],
            bpm_ballot=ballot,
            clear_on_failure=clear_on_failure,
            validate_spotify_id_unique=self.validate_spotify_id_unique,
            # ReccoBeats ne re-scrape pas le Spotify ID si l'étape spotify_id le fait déjà
            allow_spotify_scrape=("spotify_id" not in sources),
        )

        # ========================================
        # FEATS : media/album/relations via API Genius AVANT ReccoBeats
        # (le Spotify ID Genius fiabilise la chaîne ; 1 appel/feat espace les requêtes).
        # Les primaires ont déjà été traités à l'import (_prefill_via_song_api).
        # ========================================
        if (
            getattr(track, "is_featuring", False)
            and self.genius_client
            and getattr(track, "genius_id", None)
            and (
                not getattr(track, "spotify_id", None) or not getattr(track, "relationships", None)
            )
        ):
            try:
                if self.genius_client.apply_song_metadata(track):
                    logger.info(
                        f"🎫 Genius (feat) '{track.title}' : Spotify={getattr(track, 'spotify_id', None)}, "
                        f"relations={len(getattr(track, 'relationships', []) or [])}"
                    )
            except Exception as e:
                logger.debug(f"Genius media feat échec: {e}")

        # ========================================
        # VOIE ISRC PRIORITAIRE (avant tout scrape Playwright)
        # Si l'ISRC fournit BPM/Key via ReccoBeats, on évite le scraper Spotify.
        # ========================================
        isrc_ok = False
        if "reccobeats" in sources and self.apis_available.get("reccobeats"):
            try:
                isrc_ok = self._reccobeats_provider.try_by_isrc(track, ctx)
                if isrc_ok:
                    logger.info(
                        f"⚡ ISRC a fourni les données audio pour '{track.title}' → scrape Spotify évité"
                    )
            except Exception as e:
                logger.debug(f"Voie ISRC échec: {e}")

        # ========================================
        # 0. SCRAPER SPOTIFY ID
        # ========================================
        if "spotify_id" in sources and self.apis_available.get("spotify_id"):
            # Ne skip que si on a déjà un ID valide ET que force_update=False
            has_valid_id = (
                hasattr(track, "spotify_id")
                and track.spotify_id
                and (
                    not artist_tracks
                    or self.validate_spotify_id_unique(track.spotify_id, track, artist_tracks)
                )
            )

            # Si l'ISRC a déjà fourni les données audio, inutile de scraper Spotify
            should_use_spotify_scraper = (force_update or not has_valid_id) and not isrc_ok

            if should_use_spotify_scraper:
                logger.info(
                    f"🎯 Appel du scraper Spotify ID pour '{track.title}' (force_update={force_update}, has_valid_id={has_valid_id})"
                )
                try:
                    results["spotify_id"] = self._spotify_id_provider.enrich(track, ctx)
                except Exception as e:
                    logger.error(f"Erreur Spotify ID scraper pour {track.title}: {e}")
                    results["spotify_id"] = False
            else:
                logger.info(
                    f"⏭️ Scraper Spotify ID non nécessaire (ID déjà présent et valide: {track.spotify_id})"
                )
                results["spotify_id"] = "not_needed"

        # ========================================
        # 1. RECCOBEATS
        # ========================================
        reccobeats_success = False
        if "reccobeats" in sources and self.apis_available.get("reccobeats"):
            if isrc_ok:
                # Déjà satisfait par la voie ISRC en amont : pas de second appel
                reccobeats_success = True
                results["reccobeats"] = True
                logger.info(
                    f"✅ ReccoBeats déjà satisfait via ISRC (BPM={getattr(track, 'bpm', 'N/A')})"
                )
            else:
                logger.info(f"🎵 Appel de ReccoBeats pour '{track.title}'")
                try:
                    # Voie ISRC déjà tentée en amont ; ctx.allow_spotify_scrape gère
                    # le double scrape Playwright (False si l'étape spotify_id l'a fait).
                    reccobeats_success = self._reccobeats_provider.enrich(track, ctx)
                    results["reccobeats"] = reccobeats_success

                    if reccobeats_success:
                        logger.info(
                            f"✅ ReccoBeats SUCCÈS: BPM={getattr(track, 'bpm', 'N/A')}, Spotify ID={getattr(track, 'spotify_id', 'N/A')}"
                        )
                    else:
                        logger.warning(
                            f"❌ ReccoBeats ÉCHEC pour '{track.title}' - On tentera GetSongBPM en fallback"
                        )
                except Exception as e:
                    logger.error(f"❌ Erreur ReccoBeats pour {track.title}: {e}")
                    results["reccobeats"] = False
                    reccobeats_success = False

        # ========================================
        # 2. GETSONGBPM API
        # ========================================
        getsongbpm_success = False
        if "getsongbpm" in sources and self.apis_available.get("getsongbpm"):
            # Utiliser GetSongBPM si :
            # - force_update OU
            # - pas de BPM OU
            # - ReccoBeats a échoué OU
            # - Données manquantes (key, mode)

            missing_bpm = not hasattr(track, "bpm") or not track.bpm
            missing_key = not hasattr(track, "key") or track.key is None
            missing_mode = not hasattr(track, "mode") or track.mode is None

            has_missing_data = missing_bpm or missing_key or missing_mode

            # §8.3 : GetSongBPM est une API gratuite/rapide → on l'appelle TOUJOURS
            # pour fournir un 2ᵉ vote BPM (même si ReccoBeats a déjà répondu).
            should_use_getsongbpm = True

            if should_use_getsongbpm:
                # Construire le message de raison pour le log
                reasons = []
                if force_update:
                    reasons.append("force_update=True")
                if missing_bpm:
                    reasons.append("no_bpm")
                if not reccobeats_success and "reccobeats" in sources:
                    reasons.append("reccobeats_failed")
                if has_missing_data and not missing_bpm:
                    missing_items = []
                    if missing_key:
                        missing_items.append("key")
                    if missing_mode:
                        missing_items.append("mode")
                    reasons.append(f"missing_data={','.join(missing_items)}")

                reason_str = ", ".join(reasons)
                logger.info(f"🎼 Appel de GetSongBPM pour '{track.title}' (raison: {reason_str})")

                try:
                    getsongbpm_success = self._getsongbpm_provider.enrich(track, ctx)
                    results["getsongbpm"] = getsongbpm_success

                    if getsongbpm_success:
                        logger.info(
                            f"✅ GetSongBPM SUCCÈS: BPM={getattr(track, 'bpm', 'N/A')}, Key={getattr(track, 'key', 'N/A')}, Mode={getattr(track, 'mode', 'N/A')}"
                        )
                    else:
                        logger.warning(
                            f"❌ GetSongBPM ÉCHEC pour '{track.title}' - On tentera SongBPM scraper"
                        )
                except Exception as e:
                    logger.error(f"❌ Erreur GetSongBPM pour {track.title}: {e}")
                    results["getsongbpm"] = False
                    getsongbpm_success = False
            else:
                logger.info(
                    f"⏭️ GetSongBPM non appelé (données déjà présentes: BPM={getattr(track, 'bpm', 'N/A')}, Key={getattr(track, 'key', 'N/A')}, Mode={getattr(track, 'mode', 'N/A')})"
                )
                results["getsongbpm"] = "not_needed"

        # ========================================
        # 3. SONGBPM SCRAPER (avec amélioration de la logique)
        # ========================================
        if "songbpm" in sources and self.apis_available.get("songbpm"):
            # ⭐ LOGIQUE AMÉLIORÉE : Utiliser SongBPM si :
            # - force_update OU
            # - pas de BPM OU
            # - ReccoBeats ET GetSongBPM ont échoué OU
            # - Données manquantes (key, mode, duration)

            # Vérifier si des données sont manquantes
            missing_key = not hasattr(track, "key") or track.key is None
            missing_mode = not hasattr(track, "mode") or track.mode is None
            missing_duration = not hasattr(track, "duration") or not track.duration

            # §8.3 : SongBPM (scrape) = DÉPARTAGE. On l'ouvre seulement si les APIs
            # ne donnent pas de consensus BPM, ou s'il manque key/mode/duration.
            bpm_consensus = self._bpm_consensus_reached(track)

            should_use_songbpm = (
                force_update or not bpm_consensus or missing_key or missing_mode or missing_duration
            )

            if should_use_songbpm:
                # Construire le message de raison pour le log
                reasons = []
                if force_update:
                    reasons.append("force_update=True")
                if not bpm_consensus:
                    reasons.append("pas_de_consensus_bpm")
                missing_items = []
                if missing_key:
                    missing_items.append("key")
                if missing_mode:
                    missing_items.append("mode")
                if missing_duration:
                    missing_items.append("duration")
                if missing_items:
                    reasons.append(f"missing_data={','.join(missing_items)}")

                reason_str = ", ".join(reasons)
                logger.info(
                    f"🎼 Appel de SongBPM (départage) pour '{track.title}' (raison: {reason_str})"
                )

                try:
                    songbpm_success = self._songbpm_provider.enrich(track, ctx)
                    results["songbpm"] = songbpm_success

                    if songbpm_success:
                        logger.info(
                            f"✅ SongBPM SUCCÈS: BPM={track.bpm}, Key={getattr(track, 'key', 'N/A')}, Mode={getattr(track, 'mode', 'N/A')}, Duration={getattr(track, 'duration', 'N/A')}"
                        )
                    else:
                        logger.warning(f"❌ SongBPM ÉCHEC pour '{track.title}'")
                except Exception as e:
                    logger.error(f"❌ Erreur/Timeout SongBPM pour {track.title}: {e}")
                    # Utiliser None pour indiquer un crash/timeout (différent de False = pas de données)
                    results["songbpm"] = None
            else:
                logger.info(
                    f"⏭️ SongBPM non appelé (toutes les données déjà présentes: BPM={track.bpm}, Key={getattr(track, 'key', 'N/A')}, Mode={getattr(track, 'mode', 'N/A')}, Duration={getattr(track, 'duration', 'N/A')})"
                )
                results["songbpm"] = "not_needed"

        # ========================================
        # 3bis. BPM FINDER (audioaidynamics) — DERNIER RECOURS via lien YouTube
        # Pour les morceaux hors de portée de ReccoBeats/GetSongBPM/SongBPM
        # (pas de Spotify ID, absents des bases BPM) mais présents sur YouTube.
        # Comble les manques ; en force_update, ré-analyse et ÉCRASE.
        # ========================================
        if "bpmfinder" in sources and self.apis_available.get("bpmfinder"):
            # Tout le comportement (disjoncteur, recherche de lien YouTube, analyse
            # et écriture) vit dans le provider ; il renvoie la valeur de résultat
            # historique ("skipped"/"not_needed"/bool/None).
            results["bpmfinder"] = self._bpmfinder_provider.enrich(track, ctx)

        # ========================================
        # 4. DEEZER API
        # ========================================
        if "deezer" in sources and self.apis_available.get("deezer"):
            # Appeler Deezer pour vérification et enrichissement complémentaire
            logger.info(f"🎵 Appel de Deezer API pour '{track.title}'")
            try:
                deezer_success = self._deezer_provider.enrich(track, ctx)
                results["deezer"] = deezer_success

                if deezer_success:
                    logger.info(f"✅ Deezer SUCCÈS pour '{track.title}'")
                else:
                    logger.warning(f"❌ Deezer ÉCHEC pour '{track.title}'")
            except Exception as e:
                logger.error(f"❌ Erreur Deezer pour {track.title}: {e}")
                results["deezer"] = False

        # ========================================
        # VOTE BPM : réconciliation de tous les candidats (§8.3)
        # ========================================
        self._finalize_bpm(track)

        # ========================================
        # 5. DISCOGS API (CRÉDITS COMPLÉMENTAIRES)
        # ========================================
        if "discogs" in sources and self.apis_available.get("discogs"):
            logger.info(f"💿 Appel de Discogs API pour '{track.title}'")
            try:
                discogs_success = self._discogs_provider.enrich(track, ctx)
                results["discogs"] = discogs_success

                if discogs_success:
                    logger.info(
                        f"✅ Discogs SUCCÈS pour '{track.title}' - {len(track.credits)} crédits au total"
                    )
                else:
                    logger.warning(f"❌ Discogs ÉCHEC pour '{track.title}'")
            except Exception as e:
                logger.error(f"❌ Erreur Discogs pour {track.title}: {e}")
                results["discogs"] = False

        # ========================================
        # 6. NETTOYAGE SI ÉCHEC COMPLET
        # ========================================
        if clear_on_failure and force_update:
            # Sources ayant RÉELLEMENT tenté (ni skipped ni not_needed).
            # ⚠️ all([]) == True en Python : si TOUTES les sources sont
            # 'not_needed'/'skipped' (aucune n'a tourné), il ne faut PAS conclure
            # « tout a échoué » et effacer des données valides (bug ayant vidé
            # TOTAL 90 : 100 BPM/Do majeur légitimes). On n'efface QUE si au
            # moins une source a tenté ET que toutes les tentatives ont échoué.
            attempted = [r for r in results.values() if r not in ("skipped", "not_needed")]
            all_failed = bool(attempted) and all(r is False for r in attempted)

            if all_failed and force_update and initial_bpm is not None:
                # Vérifications de sécurité
                if not hasattr(track, "title") or not track.title:
                    logger.error("❌ ERREUR: Track sans titre, annulation du nettoyage")
                    return results

                if not hasattr(track, "artist"):
                    logger.error(
                        f"❌ ERREUR: Track '{track.title}' sans artiste, annulation du nettoyage"
                    )
                    return results

                logger.warning(
                    f"⚠️ NETTOYAGE: Aucune source n'a trouvé de données pour '{track.title}'"
                )
                logger.warning("⚠️ Effacement des anciennes valeurs potentiellement erronées...")

                # Effacer UNIQUEMENT les données musicales
                if hasattr(track, "bpm"):
                    old_bpm = track.bpm
                    track.bpm = None
                    logger.info(f"   🗑️ BPM effacé: {old_bpm} → None")

                if hasattr(track, "key"):
                    old_key = track.key
                    track.key = None
                    logger.info(f"   🗑️ Key effacée: {old_key} → None")

                if hasattr(track, "mode"):
                    old_mode = track.mode
                    track.mode = None
                    logger.info(f"   🗑️ Mode effacé: {old_mode} → None")

                if hasattr(track, "duration"):
                    old_duration = track.duration
                    track.duration = None
                    logger.info(f"   🗑️ Duration effacée: {old_duration} → None")

                if hasattr(track, "musical_key"):
                    old_musical_key = track.musical_key
                    track.musical_key = None
                    logger.info(f"   🗑️ Musical Key effacée: {old_musical_key} → None")

                # Vérification post-nettoyage
                if not hasattr(track, "title") or not track.title:
                    logger.error("❌ ERREUR CRITIQUE: Le titre a disparu après nettoyage!")
                elif not hasattr(track, "artist") or not track.artist:
                    logger.error("❌ ERREUR CRITIQUE: L'artiste a disparu après nettoyage!")
                else:
                    logger.info(f"✅ Données erronées nettoyées pour '{track.title}'")
                    results["cleaned"] = True

        # ========================================
        # RÉSUMÉ FINAL
        # ========================================
        logger.info(f"📊 RÉSUMÉ enrichissement '{track.title}':")
        logger.info(f"   • Résultats: {results}")
        logger.info(f"   • Spotify ID: {getattr(track, 'spotify_id', 'N/A')}")
        logger.info(f"   • BPM: {getattr(track, 'bpm', 'N/A')}")
        logger.info(
            f"   • Key: {getattr(track, 'key', 'N/A')}, Mode: {getattr(track, 'mode', 'N/A')}"
        )
        logger.info(f"   • Musical Key: {getattr(track, 'musical_key', 'N/A')}")
        logger.info(f"   • Duration: {getattr(track, 'duration', 'N/A')}")
        logger.info(f"   • Release Date: {getattr(track, 'release_date', 'N/A')}")
        logger.info(f"   • Deezer ID: {getattr(track, 'deezer_id', 'N/A')}")
        logger.info(f"   • Discogs ID: {getattr(track, 'discogs_id', 'N/A')}")
        logger.info(
            f"   • Crédits totaux: {len(track.credits) if hasattr(track, 'credits') else 0}"
        )

        return results

    # ──────────────────────────────────────────────────────────────────────
    # Réconciliation BPM — logique pure dans src/utils/bpm_vote.py (§8.2/§8.3).
    # Ici : la couture avec le scrutin porté par le track le temps du run.
    # ──────────────────────────────────────────────────────────────────────
    @staticmethod
    def _sanitize_bpm(value):
        """Cast en int + borne unique 40–220. None si invalide (délègue bpm_vote)."""
        return sanitize_bpm(value)

    @staticmethod
    def _get_ballot(track) -> BpmBallot:
        """Scrutin BPM du run, créé à la volée si absent."""
        ballot = getattr(track, "_bpm_ballot", None)
        if ballot is None:
            ballot = track._bpm_ballot = BpmBallot()
        return ballot

    def _add_bpm_candidate(self, track, source: str, raw):
        """Enregistre un candidat BPM (sanitizé) pour le vote final."""
        self._get_ballot(track).add(source, raw)

    def _bpm_consensus_reached(self, track) -> bool:
        """True si ≥2 candidats concordent déjà (→ pas besoin du scrape SongBPM)."""
        return self._get_ballot(track).consensus_reached()

    def _finalize_bpm(self, track):
        """Pose le BPM final : bpm (octave réelle) + bpm_alt + source + confiance."""
        self._get_ballot(track).finalize(track)
