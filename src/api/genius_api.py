"""Interface avec l'API Genius - Version corrigée pour les erreurs de clés"""

import re
import time
import requests
from typing import List, Optional, Dict, Any
from datetime import datetime
from lyricsgenius import Genius

from src.config import (
    GENIUS_API_KEY,
    DELAY_BETWEEN_REQUESTS,
    GENIUS_TIMEOUT,
    GENIUS_RETRIES,
    GENIUS_SLEEP_TIME,
)
from src.models import Artist, Track
from src.utils.logger import get_logger, log_api

logger = get_logger(__name__)


class GeniusAPI:
    """Gère les interactions avec l'API Genius"""

    def __init__(self):
        if not GENIUS_API_KEY:
            raise ValueError("GENIUS_API_KEY non configurée")

        # Configuration avec timeout augmenté pour les requêtes lourdes
        self.genius = Genius(
            GENIUS_API_KEY,
            timeout=GENIUS_TIMEOUT,  # ✅ Configurable via .env ou config.py (défaut: 30s)
            sleep_time=GENIUS_SLEEP_TIME,  # Délai entre requêtes (rate limiting)
            retries=GENIUS_RETRIES,  # ✅ Nombre de tentatives en cas d'échec
        )
        self.genius.verbose = False  # Désactiver les prints de lyricsgenius
        self.genius.remove_section_headers = True
        self.genius.skip_non_songs = True
        self.genius.excluded_terms = ["(Remix)", "(Live)"]  # Optionnel

        logger.info(
            f"API Genius initialisée (timeout: {GENIUS_TIMEOUT}s, retries: {GENIUS_RETRIES}, sleep: {GENIUS_SLEEP_TIME}s)"
        )

    def search_artist(self, artist_name: str) -> Optional[Artist]:
        """Wrapper simple : retourne le premier candidat exact, ou le premier candidat disponible."""
        candidates = self.search_artist_candidates(artist_name, max_candidates=1)
        if not candidates:
            return None
        return candidates[0]

    def _search_artist_legacy(self, artist_name: str) -> Optional[Artist]:
        """Remplacé par search_artist_candidates. Conservé temporairement, à supprimer."""
        try:
            logger.info(f"🔍 Recherche API Genius pour: '{artist_name}'")

            # ✅ CORRECTION 1: Vérifier d'abord dans la base de données locale
            try:
                # Importer ici pour éviter la dépendance circulaire
                from src.utils.data_manager import DataManager

                data_manager = DataManager()
                existing_artist = data_manager.get_artist_by_name(artist_name)

                if existing_artist:
                    logger.info(f"✅ Artiste trouvé en base: {existing_artist.name}")
                    return existing_artist

            except Exception as db_error:
                logger.warning(f"Erreur lors de la vérification en base: {db_error}")
                # Continuer avec la recherche API

            # ✅ CORRECTION 2: Recherche API avec gestion d'erreurs robuste
            # Note: genius.search() utilise l'API publique (genius.com/api/search) qui retourne 403
            # On utilise search_songs() qui passe par l'API authentifiée (api.genius.com/search)
            search_response = self.genius.search_songs(artist_name)
            logger.debug(f"📦 Réponse API reçue: {type(search_response)}")

            # ✅ CORRECTION 3: Vérifications strictes de la structure
            if not search_response:
                logger.warning(f"Réponse API vide pour '{artist_name}'")
                return None

            if not isinstance(search_response, dict):
                logger.warning(f"Réponse API n'est pas un dict: {type(search_response)}")
                return None

            if "hits" not in search_response:
                logger.warning(f"Clé 'hits' manquante dans la réponse API")
                return None

            hits = search_response["hits"]
            if not isinstance(hits, list) or len(hits) == 0:
                logger.warning(f"Aucun résultat dans 'hits' pour '{artist_name}'")
                return None

            logger.info(f"🎯 {len(hits)} résultats trouvés")

            # ✅ CORRECTION 4: Parsing sécurisé des résultats
            for i, hit in enumerate(hits):
                try:
                    # Vérifier la structure du hit
                    if not isinstance(hit, dict):
                        logger.debug(f"Hit {i} n'est pas un dict: {type(hit)}")
                        continue

                    if "result" not in hit:
                        logger.debug(f"Hit {i} n'a pas de clé 'result'")
                        continue

                    result = hit["result"]
                    if not isinstance(result, dict):
                        logger.debug(f"Hit {i} result n'est pas un dict: {type(result)}")
                        continue

                    # Vérifier primary_artist
                    if "primary_artist" not in result:
                        logger.debug(f"Hit {i} n'a pas de 'primary_artist'")
                        continue

                    primary_artist = result["primary_artist"]
                    if not isinstance(primary_artist, dict):
                        logger.debug(
                            f"Hit {i} primary_artist n'est pas un dict: {type(primary_artist)}"
                        )
                        continue

                    # Vérifier les champs requis
                    if "name" not in primary_artist or "id" not in primary_artist:
                        logger.debug(f"Hit {i} primary_artist manque name ou id")
                        continue

                    artist_found_name = primary_artist["name"]
                    artist_found_id = primary_artist["id"]

                    # Vérification de correspondance (insensible à la casse)
                    if artist_found_name.lower() == artist_name.lower():
                        artist = Artist(name=artist_found_name, genius_id=artist_found_id)
                        log_api("Genius", f"artist/{artist.genius_id}", True)
                        logger.info(
                            f"✅ Artiste trouvé (correspondance exacte): {artist.name} (ID: {artist.genius_id})"
                        )
                        return artist

                except Exception as hit_error:
                    logger.debug(f"Erreur lors du traitement du hit {i}: {hit_error}")
                    continue

            # ✅ CORRECTION 5: Correspondance partielle en dernier recours (stricte)
            logger.debug("Recherche de correspondance partielle stricte...")
            seen_artist_ids = set()
            for i, hit in enumerate(hits):
                try:
                    if (
                        isinstance(hit, dict)
                        and "result" in hit
                        and isinstance(hit["result"], dict)
                        and "primary_artist" in hit["result"]
                        and isinstance(hit["result"]["primary_artist"], dict)
                    ):

                        primary_artist = hit["result"]["primary_artist"]
                        if "name" in primary_artist and "id" in primary_artist:
                            artist_found_name = primary_artist["name"]
                            artist_found_id = primary_artist["id"]

                            # Dédupliquer les artistes déjà vérifiés
                            if artist_found_id in seen_artist_ids:
                                continue
                            seen_artist_ids.add(artist_found_id)

                            artist_name_lower = artist_name.lower()
                            artist_found_lower = artist_found_name.lower()

                            # Word boundary : "isha" ne matche PAS "ishaan" (\b entre mot/non-mot)
                            import re as _re

                            name_word_match = bool(
                                _re.search(
                                    r"\b" + _re.escape(artist_name_lower) + r"\b",
                                    artist_found_lower,
                                )
                            )
                            length_ratio = len(artist_found_lower) / max(len(artist_name_lower), 1)
                            # Ratio plus permissif si le nom cherché est le premier mot du résultat
                            after_idx = len(artist_name_lower)
                            starts_as_first_word = artist_found_lower[
                                :after_idx
                            ] == artist_name_lower and (
                                after_idx >= len(artist_found_lower)
                                or not artist_found_lower[after_idx].isalnum()
                            )
                            max_ratio = 3.0 if starts_as_first_word else 1.4

                            if name_word_match and length_ratio <= max_ratio:
                                artist = Artist(name=artist_found_name, genius_id=artist_found_id)
                                log_api("Genius", f"artist/{artist.genius_id}", True)
                                logger.info(
                                    f"✅ Artiste trouvé (correspondance partielle): {artist.name} (ID: {artist.genius_id})"
                                )
                                return artist

                except Exception as partial_error:
                    logger.debug(f"Erreur lors de la correspondance partielle {i}: {partial_error}")
                    continue

            # Fallback: endpoint authentifié api.genius.com (différent du public genius.com qui retourne 403)
            logger.debug("Fallback: recherche via api.genius.com/search?type=artist...")
            try:
                resp = requests.get(
                    "https://api.genius.com/search",
                    params={"q": artist_name, "type": "artist"},
                    headers={"Authorization": f"Bearer {GENIUS_API_KEY}"},
                    timeout=10,
                )
                resp.raise_for_status()
                sections = resp.json().get("response", {}).get("sections", [])
                for section in sections:
                    for hit in section.get("hits", []):
                        if hit.get("type") != "artist":
                            continue
                        result = hit.get("result", {})
                        found_name = result.get("name", "")
                        found_id = result.get("id")
                        if not found_name or not found_id:
                            continue
                        artist_name_lower = artist_name.lower()
                        found_lower = found_name.lower()
                        length_ratio = len(found_lower) / max(len(artist_name_lower), 1)
                        import re as _re

                        name_word_match = bool(
                            _re.search(r"\b" + _re.escape(artist_name_lower) + r"\b", found_lower)
                        )
                        after_idx = len(artist_name_lower)
                        starts_as_first_word = found_lower[:after_idx] == artist_name_lower and (
                            after_idx >= len(found_lower) or not found_lower[after_idx].isalnum()
                        )
                        max_ratio = 3.0 if starts_as_first_word else 1.4
                        if found_lower == artist_name_lower or (
                            name_word_match and length_ratio <= max_ratio
                        ):
                            artist = Artist(name=found_name, genius_id=found_id)
                            log_api("Genius", f"artist/{artist.genius_id}", True)
                            logger.info(
                                f"✅ Artiste trouvé (search type=artist): {artist.name} (ID: {artist.genius_id})"
                            )
                            return artist
            except Exception as sa_error:
                logger.debug(f"Fallback type=artist échoué: {sa_error}")

            logger.warning(f"Aucun artiste correspondant trouvé pour '{artist_name}'")
            return None

        except Exception as e:
            logger.error(f"Erreur lors de la recherche d'artiste: {e}")
            logger.error(f"Type d'erreur: {type(e).__name__}")
            # Log plus de détails pour debug
            import traceback

            logger.debug(f"Traceback complet: {traceback.format_exc()}")
            log_api("Genius", f"search/artist/{artist_name}", False)
            return None

    def search_artist_candidates(self, artist_name: str, max_candidates: int = 6) -> List[Artist]:
        """
        Retourne les artistes candidats pour une recherche par nom.

        L'API Genius ne dispose pas d'endpoint de recherche d'artistes par nom.
        GET /search retourne uniquement des chansons (hits de type "song").
        On en extrait les primary_artist pour construire la liste de candidats.
        """
        candidates: List[Artist] = []
        seen_ids: set = set()
        artist_name_lower = artist_name.lower()

        try:
            resp = requests.get(
                "https://api.genius.com/search",
                params={"q": artist_name},
                headers={"Authorization": f"Bearer {GENIUS_API_KEY}"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json().get("response", {})
            hits = data.get("hits", [])
            logger.debug(f"Réponse API: {len(hits)} hits, clés response: {list(data.keys())}")

            for hit in hits:
                result = hit.get("result", {})
                primary = result.get("primary_artist", {})
                artist_id = primary.get("id")
                found_name = primary.get("name", "")
                if artist_id and found_name and artist_id not in seen_ids:
                    seen_ids.add(artist_id)
                    candidates.append(Artist(name=found_name, genius_id=artist_id))
        except Exception as e:
            logger.warning(f"Recherche Genius échouée: {e}")

        # Trier : correspondances exactes en premier, puis par proximité
        def _sort_key(a: Artist) -> int:
            n = a.name.lower()
            if n == artist_name_lower:
                return 0
            if n.startswith(artist_name_lower):
                return 1
            if artist_name_lower in n:
                return 2
            return 3

        candidates.sort(key=_sort_key)
        logger.info(f"🔍 {len(candidates)} candidats trouvés pour '{artist_name}'")
        return candidates[:max_candidates]

    def get_artist_songs(
        self,
        artist: Artist,
        max_songs: int = 200,
        include_features: bool = False,
        prefill: bool = True,
        known_genius_ids: Optional[set] = None,
        include_secondary: bool = False,
    ) -> List[Track]:
        """
        Récupère la liste des morceaux d'un artiste

        Args:
            artist: L'artiste dont récupérer les morceaux
            max_songs: Nombre maximum de morceaux à récupérer
            include_features: Si True, inclut les morceaux où l'artiste est en featuring
            prefill: Si True, appelle l'API détail (album + Spotify/YouTube media + relations).
            known_genius_ids: genius_id à exclure du prefill (mode MàJ). L'appelant
                y met les titres dont les données media (album/Spotify/YouTube) sont
                déjà complètes en base — les connus mais incomplets sont re-tentés.
        """
        tracks = []

        try:
            logger.info(
                f"Récupération des morceaux de {artist.name} (include_features={include_features})"
            )

            if not artist.genius_id:
                logger.error(f"Pas d'ID Genius pour {artist.name}")
                return tracks

            # API officielle /artists/{id}/songs (l'ancien search_artist de
            # lyricsgenius passait par genius.com/api, bloqué en 403 depuis 2025)
            tracks = self._get_artist_songs_manual(
                artist,
                max_songs,
                include_features=include_features,
                include_secondary=include_secondary,
            )
            log_api("Genius", f"artist/{artist.genius_id}/songs", True)

            # Pré-remplissage via l'endpoint détail : album + Spotify ID + YouTube
            if prefill:
                self._prefill_via_song_api(tracks, known_genius_ids=known_genius_ids)
            else:
                logger.info("⏭️ Prefill API (album/media) désactivé pour cette récupération")

            logger.info(f"{len(tracks)} morceaux récupérés pour {artist.name}")
            return tracks

        except Exception as e:
            logger.error(f"Erreur lors de la récupération des morceaux: {e}")
            log_api("Genius", f"artist/songs", False)
            return tracks

    def _create_track_from_genius_song(self, song, artist: Artist) -> Optional[Track]:
        """Crée un objet Track depuis un song object de lyricsgenius - VERSION CORRIGÉE"""
        try:
            track_data = {
                "title": song.title,
                "artist": artist,
                "genius_id": song.id,
                "genius_url": song.url,
            }

            # Déterminer si c'est un featuring
            is_featuring = False
            primary_artist_name = None

            # Vérifier si l'artiste principal du morceau est différent de l'artiste recherché
            if hasattr(song, "primary_artist") and song.primary_artist:
                primary_artist_id = getattr(song.primary_artist, "id", None)
                primary_artist_name = getattr(song.primary_artist, "name", None)

                if primary_artist_id and primary_artist_id != artist.genius_id:
                    is_featuring = True
                    track_data["primary_artist_name"] = primary_artist_name
                    logger.debug(
                        f"Featuring détecté: {song.title} (artiste principal: {primary_artist_name})"
                    )

            track_data["is_featuring"] = is_featuring

            # Album depuis lyricsgenius + marquage
            if hasattr(song, "album") and song.album:
                album_name = song.album
                # Nettoyer le nom d'album s'il s'agit d'un objet
                if hasattr(album_name, "name"):
                    album_name = album_name.name
                elif isinstance(album_name, dict) and "name" in album_name:
                    album_name = album_name["name"]

                if album_name:
                    track_data["album"] = str(album_name)
                    track_data["_album_from_api"] = True
                    logger.debug(f"Album depuis API: {album_name}")

            # Date de sortie depuis lyricsgenius - créer un track temporaire pour utiliser update_release_date
            if hasattr(song, "year") and song.year:
                try:
                    year_date = datetime(int(song.year), 1, 1)
                    track_data["release_date"] = year_date
                    track_data["_release_date_from_api"] = True
                    logger.debug(f"Date depuis API: {song.year}")
                except (ValueError, TypeError):
                    pass

            # Récupérer des métadonnées supplémentaires depuis l'API raw
            if hasattr(song, "_body") and song._body:
                additional_data = self._extract_additional_metadata_from_raw(song._body)
                # Fusionner sans écraser les données existantes, SAUF pour release_date
                for key, value in additional_data.items():
                    if key == "release_date" and value:
                        # Pour les dates, utiliser la logique intelligente après création du track
                        continue
                    if key not in track_data and value:
                        track_data[key] = value

            track = Track(**track_data)

            # Appliquer la date depuis raw_data avec la logique intelligente
            if hasattr(song, "_body") and song._body:
                additional_data = self._extract_additional_metadata_from_raw(song._body)
                if "release_date" in additional_data and additional_data["release_date"]:
                    track.update_release_date(additional_data["release_date"], source="api")

            # Log pour debug
            status = "featuring" if is_featuring else "principal"
            album_source = "API" if track_data.get("_album_from_api") else "N/A"
            logger.debug(
                f"Track créé ({status}): {track.title} | Album: {track.album or 'N/A'} ({album_source})"
            )

            return track

        except Exception as e:
            logger.error(f"Erreur lors de la création du track: {e}")
            return None

    def _extract_additional_metadata_from_raw(self, raw_data: dict) -> dict:
        """Extrait des métadonnées supplémentaires depuis les données brutes - VERSION CORRIGÉE"""
        metadata = {}

        try:
            # Album depuis les données brutes si pas déjà présent
            if raw_data.get("album"):
                album_data = raw_data["album"]
                if isinstance(album_data, dict) and album_data.get("name"):
                    metadata["album"] = album_data["name"]
                    metadata["_album_from_api"] = True

            # Date de sortie plus précise
            release_components = raw_data.get("release_date_components")
            if release_components:
                year = release_components.get("year")
                month = release_components.get("month", 1)
                day = release_components.get("day", 1)

                if year:
                    try:
                        metadata["release_date"] = datetime(year, month, day)
                        metadata["_release_date_from_api"] = True
                        logger.debug(f"Date complète depuis API: {year}-{month:02d}-{day:02d}")
                    except (ValueError, TypeError):
                        try:
                            metadata["release_date"] = datetime(year, 1, 1)
                            metadata["_release_date_from_api"] = True
                        except (ValueError, TypeError):
                            pass

            # Artiste principal pour les features
            primary_artist = raw_data.get("primary_artist")
            if primary_artist and isinstance(primary_artist, dict):
                metadata["primary_artist_name"] = primary_artist.get("name")

            # Artistes en featuring
            featured_artists = raw_data.get("featured_artists", [])
            if featured_artists:
                features = [artist.get("name") for artist in featured_artists if artist.get("name")]
                if features:
                    metadata["featured_artists"] = ", ".join(features)

            # Popularité
            stats = raw_data.get("stats", {})
            if "pageviews" in stats:
                metadata["popularity"] = stats["pageviews"]

            # URL de l'artwork
            if raw_data.get("song_art_image_url"):
                metadata["artwork_url"] = raw_data["song_art_image_url"]

        except Exception as e:
            logger.debug(f"Erreur lors de l'extraction des métadonnées: {e}")

        return metadata

    @staticmethod
    def _collect_artist_ids(lst) -> set:
        """Set des ids (int) d'une liste d'artistes Genius, robuste aux types."""
        out = set()
        for a in lst or []:
            if isinstance(a, dict) and a.get("id") is not None:
                try:
                    out.add(int(a["id"]))
                except (TypeError, ValueError):
                    pass
        return out

    @staticmethod
    def _primary_is_collab_with(primary_name: str, artist_name: str) -> bool:
        """
        True si la page artiste principale est une collaboration incluant
        l'artiste avec son nom EXACT (ex: "Limsa d'Aulnay & Isha" → True pour
        Isha, mais "Vasjan & ISHA!" → False car "ISHA!" ≠ "Isha").
        """
        if not primary_name or not artist_name:
            return False
        # Découper sur les séparateurs de collaboration
        parts = re.split(
            r"\s*(?:&|,|\+|\bet\b|\bx\b|\bfeat\.?\b|\bft\.?\b)\s*",
            primary_name,
            flags=re.IGNORECASE,
        )
        if len(parts) < 2:
            return False
        target = artist_name.strip().lower()
        return any(p.strip().lower() == target for p in parts)

    def _verify_artist_credit(self, song_id, artist_id):
        """
        Vérifie au détail (`genius.song`) si `artist_id` (id EXACT) est crédité
        sur le morceau, et renvoie la nature du crédit :
            ('primary', None) | ('feat', None) | ('secondary', '<rôle>') | None
        None = id absent des crédits (ex: 'ISHA!' ≠ notre Isha → à jeter).
        Le détail est autoritaire : il rattrape les feats sous-déclarés par la liste,
        et expose les rôles fins via `custom_performances` (Additional Vocals…).
        """
        if not song_id:
            return None
        try:
            data = self.genius.song(song_id)
        except Exception as e:
            logger.debug(f"verify credit échec song {song_id}: {e}")
            return None
        song = (data or {}).get("song") or {}
        try:
            aid = int(artist_id)
        except (TypeError, ValueError):
            aid = artist_id
        if aid in self._collect_artist_ids(song.get("primary_artists")):
            return ("primary", None)
        if aid in self._collect_artist_ids(song.get("featured_artists")):
            return ("feat", None)
        # Rôles fins (chant additionnel, chœurs, etc.)
        for perf in song.get("custom_performances") or []:
            if isinstance(perf, dict) and aid in self._collect_artist_ids(perf.get("artists")):
                return ("secondary", perf.get("label") or "Contribution")
        if aid in self._collect_artist_ids(song.get("producer_artists")):
            return ("secondary", "Producer")
        if aid in self._collect_artist_ids(song.get("writer_artists")):
            return ("secondary", "Writer")
        return None

    def _get_artist_songs_manual(
        self,
        artist: Artist,
        max_songs: int,
        include_features: bool = False,
        include_secondary: bool = False,
    ) -> List[Track]:
        """Méthode manuelle de récupération (fallback) — gère aussi les featurings.

        include_secondary: si True, les morceaux où l'artiste n'est ni primary ni
        feat sont VÉRIFIÉS au détail (`genius.song`) ; gardés seulement si son id
        exact y est crédité, avec son rôle (`secondary_role`). Sinon ils sont jetés.
        """
        tracks = []

        try:
            page = 1
            per_page = 50

            while len(tracks) < max_songs:
                response = self.genius.artist_songs(
                    artist.genius_id, sort="popularity", per_page=per_page, page=page
                )

                if not response or "songs" not in response:
                    break

                songs = response["songs"]
                if not songs:
                    break

                for song in songs:
                    primary = song.get("primary_artist") or {}
                    is_feat = primary.get("id") != artist.genius_id
                    if is_feat and not include_features:
                        continue

                    secondary_role = None  # rempli si contribution secondaire (Additional Voices…)

                    if is_feat:
                        # L'API renvoie aussi les morceaux où l'artiste a un rôle
                        # secondaire (writer, producer...) ou est mal tagué.
                        # Garder : 1) VRAIS feats (id dans featured_artists),
                        # 2) co-primaires (id dans primary_artists), 3) collab par nom
                        # ("Limsa d'Aulnay & Isha" — mais PAS "Vasjan & ISHA!").
                        featured_ids = self._collect_artist_ids(song.get("featured_artists"))
                        # primary_artists (pluriel) = co-artistes principaux (collab) ;
                        # un co-primaire n'est PAS dans featured_artists → test par ID indispensable.
                        primary_ids = self._collect_artist_ids(song.get("primary_artists"))
                        try:
                            aid = int(artist.genius_id)
                        except (TypeError, ValueError):
                            aid = artist.genius_id
                        is_collab_page = self._primary_is_collab_with(
                            primary.get("name", ""), artist.name
                        )
                        if (
                            aid not in featured_ids
                            and aid not in primary_ids
                            and not is_collab_page
                        ):
                            if not include_secondary:
                                logger.debug(
                                    f"Ignoré (rôle secondaire/tag douteux): "
                                    f"{song.get('title')} — primary='{primary.get('name')}' "
                                    f"feat_ids={featured_ids} prim_ids={primary_ids} aid={aid}"
                                )
                                continue
                            # Mode rôles secondaires : on VÉRIFIE au détail que c'est
                            # bien NOTRE artiste (id exact) et on récupère son rôle.
                            verdict = self._verify_artist_credit(song.get("id"), artist.genius_id)
                            time.sleep(DELAY_BETWEEN_REQUESTS)
                            if verdict is None:
                                logger.debug(
                                    f"Ignoré (non crédité au détail / id ≠): {song.get('title')} "
                                    f"— primary='{primary.get('name')}'"
                                )
                                continue
                            kind, role = verdict
                            if kind == "primary":
                                is_feat = False  # liste sous-déclarée → vrai primaire
                            elif kind == "feat":
                                is_feat = True  # vrai feat sous-déclaré par la liste
                            else:  # 'secondary'
                                is_feat = True
                                secondary_role = role
                                logger.info(
                                    f"🎙️ Rôle secondaire gardé: {song.get('title')} "
                                    f"— {artist.name} = {role}"
                                )

                    track = Track(
                        title=song.get("title", ""),
                        artist=artist,
                        genius_id=song.get("id"),
                        genius_url=song.get("url"),
                        album=self._extract_album_from_song(song),
                        release_date=self._extract_release_date_from_song(song),
                        is_featuring=is_feat,
                    )
                    track.secondary_role = secondary_role
                    if is_feat and primary.get("name"):
                        track.primary_artist_name = primary["name"]
                        logger.debug(
                            f"Featuring (fallback): {track.title} "
                            f"(artiste principal: {primary['name']})"
                        )

                    tracks.append(track)

                    if len(tracks) >= max_songs:
                        break

                    time.sleep(DELAY_BETWEEN_REQUESTS)

                page += 1

                if len(songs) < per_page:
                    break

        except Exception as e:
            logger.error(f"Erreur dans la méthode manuelle: {e}")

        return tracks

    def _prefill_via_song_api(
        self, tracks: List[Track], known_genius_ids: Optional[set] = None
    ) -> None:
        """
        Pré-remplit via l'endpoint détail `GET /songs/{id}` (la liste ne fournit
        ni album ni media) : album + **Spotify ID + URL YouTube** (depuis `media`).
        PRIMAIRES seulement, et seulement si une de ces données manque (peu
        d'appels en ré-import). Le scrape rattrape le reste.
        Le Spotify ID Genius fiabilise la chaîne audio (le scraper Google devient
        un vrai fallback).

        known_genius_ids : si fourni (mode MàJ), ces titres (déjà complets en base)
        sont exclus → l'API media/album n'est appelée que pour les nouveaux titres
        et les connus incomplets.
        """
        known = known_genius_ids or set()
        # Feats INCLUS depuis 2026-07-02 : leur media (Spotify ID / lien YouTube)
        # est nécessaire aux streams (étape feats YTM) et à l'affichage — les
        # attendre jusqu'à l'enrichissement laissait p.ex. Bitume Caviar 2 sans
        # lien YouTube. L'exclusion des "déjà complets" (mode MàJ) limite le coût.
        targets = [t for t in tracks if self._needs_song_api(t) and t.genius_id not in known]
        if not targets:
            if known:
                logger.info("🎫 Genius API : aucun nouveau morceau à enrichir (MàJ)")
            return
        n_feats = sum(1 for t in targets if getattr(t, "is_featuring", False))
        logger.info(
            f"🎫 Genius API (album/Spotify/YouTube/relations) : "
            f"{len(targets)} morceau(x) dont {n_feats} feat(s)…"
        )
        n = 0
        for t in targets:
            if self.apply_song_metadata(t):
                n += 1
            time.sleep(DELAY_BETWEEN_REQUESTS)
        logger.info(f"🎫 Genius API : {n}/{len(targets)} morceau(x) enrichi(s)")

    @staticmethod
    def _needs_song_api(track: "Track") -> bool:
        """True si album/Spotify/YouTube/relations manquent (→ un appel détail utile).

        Un lien YouTube 'search_auto' (recherche persistée) compte comme manquant :
        Genius est prioritaire et doit pouvoir le remplacer par le lien officiel.
        """
        return bool(track.genius_id) and (
            (not getattr(track, "album", None) and not getattr(track, "album_override", None))
            or not getattr(track, "spotify_id", None)
            or not getattr(track, "youtube_url", None)
            or getattr(track, "youtube_url_source", None) == "search_auto"
            or not getattr(track, "relationships", None)
        )

    # Types de relation AMONT à conserver (ce qui a inspiré le morceau)
    _REL_UPSTREAM = {"samples", "interpolates", "cover_of", "remix_of"}

    @staticmethod
    def _extract_relationships(song: dict) -> List[Dict[str, Any]]:
        """
        Relations « inspiré de » depuis `song_relationships` : on garde l'AMONT
        (samples/interpolates/cover_of/remix_of) + les traductions FR. On jette
        l'aval (sampled_in, *_by : ce que le morceau a engendré).
        """
        out = []
        for rel in song.get("song_relationships") or []:
            rtype = rel.get("relationship_type") or rel.get("type")
            songs = rel.get("songs") or []
            keep_fr = rtype == "translations"
            if rtype not in GeniusAPI._REL_UPSTREAM and not keep_fr:
                continue
            for s in songs:
                if not isinstance(s, dict):
                    continue
                if keep_fr and (s.get("language") or "").lower() != "fr":
                    continue
                out.append(
                    {
                        "type": "translation_fr" if keep_fr else rtype,
                        "title": s.get("title") or s.get("full_title"),
                        "artist": (s.get("primary_artist") or {}).get("name"),
                        "url": s.get("url"),
                    }
                )
        return out

    def apply_song_metadata(self, track: "Track") -> bool:
        """
        UN appel `GET /songs/{id}` → pose album + Spotify ID + YouTube (depuis
        `media`) + relations amont (si manquants). Réutilisé pour les primaires
        (import) ET les feats (avant ReccoBeats, à l'enrichissement).
        Returns True si quelque chose a été posé.
        """
        if not track.genius_id:
            return False
        try:
            data = self.genius.song(track.genius_id)
        except Exception as e:
            logger.debug(f"genius.song échec '{track.title}': {e}")
            return False
        song = (data or {}).get("song") or {}
        changed = False

        album = song.get("album")
        name = album.get("name") if isinstance(album, dict) else None
        if (
            name
            and not getattr(track, "album", None)
            and not getattr(track, "album_override", None)
        ):  # édition manuelle respectée
            track.album = str(name)
            track._album_from_api = True
            changed = True

        sid, yt = self._extract_media(song)
        if sid and not getattr(track, "spotify_id", None):
            track.spotify_id = sid
            track._spotify_from_api = True
            changed = True
        # Genius pose le lien si absent, et remplace un 'search_auto'. MAIS
        # respecte un lien 'manual' (choix explicite de l'utilisateur, priorité max).
        _yt_src = getattr(track, "youtube_url_source", None)
        if (
            yt
            and _yt_src != "manual"
            and (not getattr(track, "youtube_url", None) or _yt_src != "genius_media")
        ):
            track.youtube_url = yt
            track.youtube_url_source = "genius_media"
            track._youtube_from_api = True
            changed = True

        rels = self._extract_relationships(song)
        if rels and not getattr(track, "relationships", None):
            track.relationships = rels
            changed = True

        return changed

    # ── Import d'album complet via l'API WEB genius.com ───────────────────────
    # /artists/{id}/songs OMET les morceaux aux paroles 'incomplete' (cas
    # "Vas-y chante" : 2/14 récupérés). La page album les liste tous →
    # genius.com/api/albums/{id}/tracks. Résolution URL→id : recherche d'album
    # + match d'URL exact (ne PAS regexer albums/\d+ dans le HTML de la page :
    # le premier id venu appartient aux albums recommandés).

    _WEB_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }

    def get_album_tracks_from_url(self, album_url: str) -> Optional[Dict[str, Any]]:
        """Tracklist COMPLÈTE d'un album Genius depuis son URL publique.

        Returns:
            {'album': {id, name, artist, release_date, url},
             'tracks': [{genius_id, title, track_number, primary_artist, url,
                         lyrics_state}]}
            ou None si introuvable.
        """
        url = (album_url or "").split("?")[0].strip().rstrip("/")
        if "/albums/" not in url:
            logger.error(f"URL d'album Genius invalide: {album_url!r}")
            return None

        # 1. URL → album id, via la recherche d'albums (match d'URL exact)
        slug_query = url.rsplit("/", 1)[-1].replace("-", " ")
        try:
            resp = requests.get(
                "https://genius.com/api/search/album",
                params={"q": slug_query},
                headers=self._WEB_HEADERS,
                timeout=20,
            )
            resp.raise_for_status()
            sections = (resp.json().get("response") or {}).get("sections") or []
            hits = sections[0].get("hits", []) if sections else []
        except Exception as e:
            logger.error(f"Recherche album Genius échouée ({slug_query!r}): {e}")
            return None

        album_id = None
        wanted = url.lower()
        for h in hits:
            res = h.get("result") or {}
            if (res.get("url") or "").split("?")[0].rstrip("/").lower() == wanted:
                album_id = res.get("id")
                break
        if not album_id and len(hits) == 1:
            album_id = (hits[0].get("result") or {}).get("id")
        if not album_id:
            logger.error(
                f"Album introuvable via la recherche: {url} — candidats: "
                f"{[(h.get('result') or {}).get('url') for h in hits[:5]]}"
            )
            return None

        # 2. Métadonnées + tracklist
        try:
            alb_resp = requests.get(
                f"https://genius.com/api/albums/{album_id}", headers=self._WEB_HEADERS, timeout=20
            )
            album = (alb_resp.json().get("response") or {}).get("album") or {}
            tr_resp = requests.get(
                f"https://genius.com/api/albums/{album_id}/tracks",
                headers=self._WEB_HEADERS,
                timeout=20,
            )
            raw_tracks = (tr_resp.json().get("response") or {}).get("tracks") or []
        except Exception as e:
            logger.error(f"Tracklist album Genius {album_id} échouée: {e}")
            return None

        tracks = []
        for t in raw_tracks:
            song = t.get("song") or {}
            if not song.get("id"):
                continue
            tracks.append(
                {
                    "genius_id": song["id"],
                    "title": song.get("title"),
                    "track_number": t.get("number"),
                    "primary_artist": (song.get("primary_artist") or {}).get("name"),
                    "url": song.get("url"),
                    "lyrics_state": song.get("lyrics_state"),
                }
            )

        logger.info(
            f"🎼 Album Genius '{album.get('name')}' (#{album_id}) : "
            f"{len(tracks)} morceau(x), dont "
            f"{sum(1 for t in tracks if t['lyrics_state'] != 'complete')} sans paroles complètes"
        )
        return {
            "album": {
                "id": album_id,
                "name": album.get("name"),
                "artist": (album.get("artist") or {}).get("name"),
                "release_date": album.get("release_date"),
                "url": url,
            },
            "tracks": tracks,
        }

    @staticmethod
    def _extract_media(song: dict):
        """(spotify_id, youtube_url) depuis le tableau `media` d'un song Genius."""
        sid = yt = None
        for m in song.get("media") or []:
            if not isinstance(m, dict):
                continue
            prov = (m.get("provider") or "").lower()
            url = m.get("url") or ""
            if prov == "spotify" and not sid:
                uri = m.get("native_uri") or ""
                if "spotify:track:" in uri:
                    sid = uri.split("spotify:track:")[1].split("?")[0].strip()
                elif "open.spotify.com/track/" in url:
                    sid = url.split("/track/")[1].split("?")[0].split("/")[0].strip()
            elif prov == "youtube" and not yt:
                yt = url or None
        return sid, yt

    def _extract_album_from_song(self, song: dict) -> Optional[str]:
        """Extrait l'album depuis les données de l'API"""
        try:
            album_data = song.get("album")
            if album_data and isinstance(album_data, dict):
                album_name = album_data.get("name")
                if album_name:
                    return album_name
        except Exception as e:
            logger.debug(f"Erreur lors de l'extraction de l'album: {e}")
        return None

    def _extract_release_date_from_song(self, song: dict) -> Optional[datetime]:
        """Extrait la date de sortie depuis les données de l'API"""
        try:
            release_components = song.get("release_date_components")
            if release_components:
                year = release_components.get("year")
                month = release_components.get("month")
                day = release_components.get("day")

                if year:
                    if month and day:
                        return datetime(year, month, day)
                    else:
                        return datetime(year, 1, 1)

        except Exception as e:
            logger.debug(f"Erreur lors de l'extraction de la date: {e}")

        return None

    def get_song_details(self, track: Track) -> Dict[str, Any]:
        """Récupère les détails d'un morceau (pour le scraping)"""
        if not track.genius_id:
            logger.warning(f"Pas d'ID Genius pour {track.title}")
            return {}

        try:
            logger.debug(f"Récupération des détails de {track.title}")

            song = self.genius.song(track.genius_id)

            if song:
                details = {
                    "id": song["song"]["id"],
                    "title": song["song"]["title"],
                    "url": song["song"]["url"],
                    "album": song["song"]["album"]["name"] if song["song"].get("album") else None,
                    "release_date": song["song"].get("release_date_for_display"),
                    "producers": [],
                    "writers": [],
                    "features": [],
                }

                # Extraire les producteurs et auteurs des relations
                for relation in song["song"].get("producer_artists", []):
                    details["producers"].append(relation["name"])

                for relation in song["song"].get("writer_artists", []):
                    details["writers"].append(relation["name"])

                # Features
                for relation in song["song"].get("featured_artists", []):
                    details["features"].append(relation["name"])

                log_api("Genius", f"songs/{track.genius_id}", True)
                return details

        except Exception as e:
            logger.error(f"Erreur lors de la récupération des détails: {e}")
            log_api("Genius", f"songs/{track.genius_id}", False)

        return {}

    def search_song(self, title: str, artist_name: str) -> Optional[Dict[str, Any]]:
        """Recherche un morceau spécifique"""
        try:
            query = f"{title} {artist_name}"
            logger.debug(f"Recherche du morceau: {query}")

            search_results = self.genius.search_songs(query)

            if search_results and "hits" in search_results:
                for hit in search_results["hits"]:
                    result = hit["result"]
                    # Vérifier que c'est le bon artiste
                    if artist_name.lower() in result["primary_artist"]["name"].lower():
                        return {
                            "genius_id": result["id"],
                            "title": result["title"],
                            "url": result["url"],
                            "artist": result["primary_artist"]["name"],
                        }

            logger.warning(f"Morceau non trouvé: {query}")
            return None

        except Exception as e:
            logger.error(f"Erreur lors de la recherche de morceau: {e}")
            return None

    def test_connection(self) -> bool:
        """Teste la connexion à l'API"""
        try:
            result = self.genius.search_songs("test", per_page=1)
            return result is not None
        except Exception as e:
            logger.error(f"Erreur de connexion à Genius: {e}")
            return False
