"""Client pour l'API Discogs - Enrichissement des crédits et métadonnées"""

import time
from typing import Any

import discogs_client
from discogs_client.exceptions import HTTPError

from src.models import Credit, CreditRole, Track
from src.utils.logger import get_logger, log_api

logger = get_logger(__name__)


class DiscogsClient:
    """Client pour interagir avec l'API Discogs"""

    def __init__(self, user_token: str | None = None):
        """
        Initialise le client Discogs

        Args:
            user_token: Token personnel Discogs (optionnel mais recommandé pour + de requêtes/min)
        """
        self.client = None
        self.rate_limit_remaining = 60
        self.rate_limit_used = 0

        try:
            if user_token:
                # Authentification avec token personnel (60 req/min)
                self.client = discogs_client.Client(
                    "MusicCreditsScraper/1.0", user_token=user_token
                )
                logger.info("✅ Discogs API initialisée avec token personnel (60 req/min)")
            else:
                # Sans authentification (25 req/min)
                self.client = discogs_client.Client("MusicCreditsScraper/1.0")
                logger.warning("⚠️ Discogs API initialisée SANS token (25 req/min - limité)")

        except Exception as e:
            logger.error(f"❌ Erreur initialisation Discogs API: {e}")
            raise

    def _check_rate_limit(self):
        """Vérifie et gère le rate limit de l'API Discogs"""
        try:
            # L'API Discogs retourne les headers de rate limit après chaque requête
            # On peut vérifier via self.client._fetcher.last_request si besoin
            if hasattr(self.client, "_fetcher") and hasattr(
                self.client._fetcher, "rate_limit_remaining"
            ):
                self.rate_limit_remaining = self.client._fetcher.rate_limit_remaining
                self.rate_limit_used = self.client._fetcher.rate_limit_used

                if self.rate_limit_remaining < 5:
                    logger.warning(
                        f"⚠️ Rate limit Discogs faible: {self.rate_limit_remaining} requêtes restantes"
                    )
                    # Attendre 60 secondes pour reset
                    logger.info("⏸️ Pause de 60s pour reset du rate limit...")
                    time.sleep(60)

        except Exception as e:
            logger.debug(f"Impossible de vérifier le rate limit: {e}")

    def search_track(
        self, track_title: str, artist_name: str, album_name: str | None = None
    ) -> dict[str, Any] | None:
        """
        Recherche un morceau sur Discogs

        Args:
            track_title: Titre du morceau
            artist_name: Nom de l'artiste
            album_name: Nom de l'album (optionnel mais améliore la précision)

        Returns:
            Dictionnaire avec les infos du morceau ou None
        """
        if not self.client:
            logger.error("❌ Client Discogs non initialisé")
            return None

        try:
            self._check_rate_limit()

            # Construire la requête de recherche
            # Format: "artist - title" ou "artist - album - title"
            if album_name:
                query = f"{artist_name} {album_name} {track_title}"
                logger.info(
                    f"🔍 Discogs: Recherche '{track_title}' de {artist_name} (album: {album_name})"
                )
            else:
                query = f"{artist_name} {track_title}"
                logger.info(f"🔍 Discogs: Recherche '{track_title}' de {artist_name}")

            # Rechercher des releases (albums/singles) contenant ce track
            results = self.client.search(query, type="release", artist=artist_name)

            if not results:
                logger.warning(f"❌ Aucun résultat Discogs pour '{track_title}'")
                log_api("Discogs", f"search/{track_title}", False)
                return None

            # Discogs results is a paginated object, not a list
            # We can't use len() or slice directly
            logger.info("📊 Discogs: Résultats trouvés, analyse des premiers...")

            # Analyser les premiers résultats (max 5)
            checked_count = 0
            for i, release in enumerate(results, 1):
                if checked_count >= 5:
                    break
                checked_count += 1
                try:
                    logger.debug(f"Vérification résultat #{i}: {release.title}")

                    # Vérifier si le release contient le track recherché
                    track_data = self._extract_track_from_release(release, track_title, artist_name)

                    if track_data:
                        logger.info(f"✅ Discogs: Correspondance trouvée (résultat #{i})")
                        log_api("Discogs", f"search/{track_title}", True)
                        return track_data

                except Exception as e:
                    logger.debug(f"Erreur analyse résultat #{i}: {e}")
                    continue

            logger.warning(
                f"❌ Aucune correspondance exacte trouvée sur Discogs pour '{track_title}'"
            )
            log_api("Discogs", f"search/{track_title}", False)
            return None

        except HTTPError as e:
            if e.status_code == 429:
                logger.error("⏰ Rate limit Discogs atteint, pause de 60s...")
                time.sleep(60)
            else:
                logger.error(f"❌ Erreur HTTP Discogs: {e}")
            log_api("Discogs", f"search/{track_title}", False)
            return None

        except Exception as e:
            logger.error(f"❌ Erreur recherche Discogs: {e}")
            log_api("Discogs", f"search/{track_title}", False)
            return None

    def _extract_track_from_release(
        self, release, track_title: str, artist_name: str
    ) -> dict[str, Any] | None:
        """
        Extrait les données d'un track depuis un release Discogs

        Args:
            release: Objet Release de discogs_client
            track_title: Titre du track recherché
            artist_name: Nom de l'artiste

        Returns:
            Dictionnaire avec les données du track ou None
        """
        try:
            # Récupérer la tracklist
            if not hasattr(release, "tracklist") or not release.tracklist:
                return None

            # Normaliser le titre recherché pour comparaison
            normalized_search_title = self._normalize_string(track_title)

            # Chercher le track dans la tracklist
            for track in release.tracklist:
                track_name = track.title if hasattr(track, "title") else str(track)
                normalized_track_name = self._normalize_string(track_name)

                if normalized_track_name == normalized_search_title:
                    logger.info(f"✅ Track trouvé: '{track_name}' dans release '{release.title}'")

                    # Extraire les données
                    track_data = {
                        "title": track_title,
                        "artist": artist_name,
                        "album": release.title if hasattr(release, "title") else None,
                        "discogs_id": release.id if hasattr(release, "id") else None,
                        "discogs_url": release.url if hasattr(release, "url") else None,
                        "position": track.position if hasattr(track, "position") else None,
                        "duration": track.duration if hasattr(track, "duration") else None,
                    }

                    # Extraire les métadonnées du release
                    if hasattr(release, "genres") and release.genres:
                        track_data["genres"] = release.genres

                    if hasattr(release, "styles") and release.styles:
                        track_data["styles"] = release.styles

                    if hasattr(release, "year") and release.year:
                        track_data["year"] = release.year

                    if hasattr(release, "labels") and release.labels:
                        labels = [label.name for label in release.labels if hasattr(label, "name")]
                        track_data["labels"] = labels

                    # Extraire les crédits
                    credits = self._extract_credits_from_release(release)
                    if credits:
                        track_data["credits"] = credits

                    return track_data

            return None

        except Exception as e:
            logger.debug(f"Erreur extraction track depuis release: {e}")
            return None

    def _extract_credits_from_release(self, release) -> list[dict[str, str]]:
        """
        Extrait tous les crédits depuis un release Discogs

        Args:
            release: Objet Release de discogs_client

        Returns:
            Liste de dictionnaires avec les crédits
        """
        credits = []

        try:
            # Les crédits sont dans release.credits ou release.extraartists
            credit_sources = []

            if hasattr(release, "credits") and release.credits:
                credit_sources.extend(release.credits)

            if hasattr(release, "extraartists") and release.extraartists:
                credit_sources.extend(release.extraartists)

            for credit in credit_sources:
                try:
                    # Credits are Artist objects with a 'data' dictionary containing the actual credit info
                    name = credit.name if hasattr(credit, "name") else str(credit)

                    # Extract role from data dictionary
                    role = "Unknown"
                    role_detail = None

                    if hasattr(credit, "data") and isinstance(credit.data, dict):
                        # Role is in credit.data['role']
                        role = credit.data.get("role", "Unknown")
                        # Tracks info (like "A1, B4") is in credit.data['tracks']
                        role_detail = credit.data.get("tracks")

                    credit_dict = {"name": name, "role": role, "role_detail": role_detail}

                    credits.append(credit_dict)
                    logger.debug(f"Crédit Discogs: {name} - {role}")

                except Exception as e:
                    logger.debug(f"Erreur extraction crédit: {e}")
                    continue

            logger.info(f"✅ {len(credits)} crédit(s) extrait(s) de Discogs")
            return credits

        except Exception as e:
            logger.warning(f"⚠️ Erreur extraction crédits Discogs: {e}")
            return []

    def _normalize_string(self, s: str) -> str:
        """Normalise une chaîne pour la comparaison"""
        import re

        # Unifier les apostrophes typographiques (' ' ` ´) → apostrophe droite
        for apo in ("’", "‘", "`", "´"):
            s = s.replace(apo, "'")
        # Minuscules, sans accents, sans caractères spéciaux
        s = s.lower().strip()
        # Retirer les parenthèses/crochets
        s = re.sub(r"\s*[\(\)\[\]].*?[\(\)\[\]]", "", s)
        s = re.sub(r"\s*[\(\)\[\]]", "", s)
        # Retirer feat/ft
        s = re.sub(r"\s*\(?f(ea)?t\.?\s+.*", "", s)
        return " ".join(s.split())

    def _map_discogs_role_to_enum(self, role: str) -> CreditRole:
        """
        Mappe un rôle Discogs vers un CreditRole

        Args:
            role: Rôle Discogs (ex: "Producer", "Mixed By", etc.)

        Returns:
            CreditRole correspondant
        """
        role_lower = role.lower().strip()

        # Mapping des rôles Discogs vers CreditRole
        role_mapping = {
            # Production
            "producer": CreditRole.PRODUCER,
            "co-producer": CreditRole.CO_PRODUCER,
            "executive producer": CreditRole.EXECUTIVE_PRODUCER,
            "vocal producer": CreditRole.VOCAL_PRODUCER,
            "programmed by": CreditRole.PROGRAMMER,
            "arranged by": CreditRole.ARRANGER,
            # Engineering
            "mixed by": CreditRole.MIXING_ENGINEER,
            "mastered by": CreditRole.MASTERING_ENGINEER,
            "recorded by": CreditRole.RECORDING_ENGINEER,
            "engineer": CreditRole.ENGINEER,
            "assistant engineer": CreditRole.ASSISTANT_ENGINEER,
            # Writing
            "written by": CreditRole.WRITER,
            "composed by": CreditRole.COMPOSER,
            "lyrics by": CreditRole.LYRICIST,
            # Performance
            "vocals": CreditRole.VOCALS,
            "lead vocals": CreditRole.LEAD_VOCALS,
            "backing vocals": CreditRole.BACKGROUND_VOCALS,
            "choir": CreditRole.CHOIR,
            # Instruments
            "guitar": CreditRole.GUITAR,
            "bass": CreditRole.BASS,
            "drums": CreditRole.DRUMS,
            "piano": CreditRole.PIANO,
            "keyboards": CreditRole.KEYBOARD,
            "saxophone": CreditRole.SAXOPHONE,
            # Artwork
            "artwork": CreditRole.ARTWORK,
            "design": CreditRole.GRAPHIC_DESIGN,
            "photography": CreditRole.PHOTOGRAPHY,
        }

        # Chercher une correspondance
        for discogs_role, credit_role in role_mapping.items():
            if discogs_role in role_lower:
                return credit_role

        # Pas de correspondance → OTHER
        return CreditRole.OTHER

    def enrich_track_data(self, track: Track, force_update: bool = False) -> bool:
        """
        Enrichit un track avec les données depuis Discogs

        Args:
            track: Objet Track à enrichir
            force_update: Si True, écrase les données existantes

        Returns:
            True si des données ont été ajoutées, False sinon
        """
        try:
            artist_name = track.artist.name if hasattr(track.artist, "name") else str(track.artist)
            album_name = track.album

            # Rechercher le track sur Discogs
            track_data = self.search_track(track.title, artist_name, album_name)

            if not track_data:
                logger.warning(f"⚠️ Aucune donnée Discogs pour '{track.title}'")
                return False

            updated = False

            # Discogs ID
            if track_data.get("discogs_id") and (force_update or not track.discogs_id):
                track.discogs_id = track_data["discogs_id"]
                logger.info(f"💿 Discogs ID ajouté: {track.discogs_id}")
                updated = True

            # Genre (si pas déjà présent)
            if track_data.get("genres") and (force_update or not track.genre):
                # Joindre genres + styles
                genres = track_data["genres"]
                if track_data.get("styles"):
                    genres = genres + track_data["styles"]
                track.genre = ", ".join(genres[:3])  # Max 3 genres
                logger.info(f"🎵 Genre ajouté depuis Discogs: {track.genre}")
                updated = True

            # Labels
            if track_data.get("labels"):
                labels_str = ", ".join(track_data["labels"])
                logger.info(f"🏷️ Labels Discogs: {labels_str}")
                # Peut être stocké dans un champ custom ou ignoré

            # Crédits
            if track_data.get("credits"):
                credits_added = 0
                for credit_dict in track_data["credits"]:
                    try:
                        role_enum = self._map_discogs_role_to_enum(credit_dict["role"])

                        credit = Credit(
                            name=credit_dict["name"],
                            role=role_enum,
                            role_detail=credit_dict.get("role_detail")
                            or (credit_dict["role"] if role_enum == CreditRole.OTHER else None),
                            source="discogs",
                        )

                        track.add_credit(credit)
                        credits_added += 1

                    except Exception as e:
                        logger.debug(f"Erreur ajout crédit: {e}")
                        continue

                if credits_added > 0:
                    logger.info(f"✅ {credits_added} crédit(s) Discogs ajouté(s) à '{track.title}'")
                    updated = True

            return updated

        except Exception as e:
            logger.error(f"❌ Erreur enrichissement Discogs pour '{track.title}': {e}")
            return False
