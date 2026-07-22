"""
Module d'enrichissement des données musicales via l'API Deezer
Troisième enrichisseur dans la chaîne : Reccobeats -> SongBPM -> Deezer

Phase F2 : chaque méthode réseau a un jumeau async (`*_async`) alimenté par
l'`AsyncHttpSession` partagée (httpx + rate-limit par domaine) ; la logique
pure (extraction, vérifications) est commune aux deux voies.
"""

import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

import httpx
import requests

if TYPE_CHECKING:
    from src.api.async_http import AsyncHttpSession

# Configuration du logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class DeezerAPI:
    """
    Client pour l'API Deezer permettant d'enrichir les données musicales
    """

    BASE_URL = "https://api.deezer.com"
    RATE_LIMIT = 50  # 50 requêtes par 5 secondes
    RATE_LIMIT_WINDOW = 5  # secondes

    def __init__(self):
        """Initialise le client Deezer API"""
        self.session = requests.Session()
        self.request_times = []

    def _check_rate_limit(self):
        """
        Vérifie et applique le rate limiting (50 requêtes / 5 secondes)
        """
        current_time = time.time()

        # Nettoyer les requêtes hors de la fenêtre de temps
        self.request_times = [
            t for t in self.request_times if current_time - t < self.RATE_LIMIT_WINDOW
        ]

        # Si on atteint la limite, attendre
        if len(self.request_times) >= self.RATE_LIMIT:
            sleep_time = self.RATE_LIMIT_WINDOW - (current_time - self.request_times[0])
            if sleep_time > 0:
                logger.warning(f"Rate limit atteint. Attente de {sleep_time:.2f}s")
                time.sleep(sleep_time)
                self.request_times = []

        self.request_times.append(current_time)

    @staticmethod
    def _payload_or_none(data: dict) -> dict | None:
        """Filtre les erreurs applicatives de l'API Deezer (commun sync/async)."""
        if "error" in data:
            error_type = data["error"].get("type", "Unknown")
            error_message = data["error"].get("message", "Unknown error")
            logger.error(f"Erreur API Deezer: {error_type} - {error_message}")
            return None
        return data

    def _make_request(self, endpoint: str, params: dict | None = None) -> dict | None:
        """
        Effectue une requête à l'API Deezer avec gestion du rate limiting

        Args:
            endpoint: Point de terminaison de l'API
            params: Paramètres de la requête

        Returns:
            Réponse JSON ou None en cas d'erreur
        """
        self._check_rate_limit()

        url = f"{self.BASE_URL}/{endpoint}"

        try:
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()
            return self._payload_or_none(response.json())

        except requests.exceptions.RequestException as e:
            logger.error(f"Erreur de requête: {e}")
            return None
        except ValueError as e:
            logger.error(f"Erreur de parsing JSON: {e}")
            return None

    async def _make_request_async(
        self, http: "AsyncHttpSession", endpoint: str, params: dict | None = None
    ) -> dict | None:
        """Jumeau async de `_make_request` — le rate-limit par fenêtre (50/5 s)
        est remplacé par le limiteur par domaine de la session partagée."""
        url = f"{self.BASE_URL}/{endpoint}"
        try:
            response = await http.get(url, params=params, timeout=10)
            response.raise_for_status()
            return self._payload_or_none(response.json())
        except httpx.HTTPError as e:
            logger.error(f"Erreur de requête: {e}")
            return None
        except ValueError as e:
            logger.error(f"Erreur de parsing JSON: {e}")
            return None

    def search_track(self, artist: str, title: str, strict: bool = False) -> dict | None:
        """
        Recherche un track par artiste et titre

        Args:
            artist: Nom de l'artiste
            title: Titre de la chanson
            strict: Active le mode strict (désactive le fuzzy matching)

        Returns:
            Données du premier résultat ou None
        """
        data = self._make_request("search", self._search_params(artist, title, strict))
        return self._first_search_hit(data, artist, title)

    async def search_track_async(
        self, http: "AsyncHttpSession", artist: str, title: str, strict: bool = False
    ) -> dict | None:
        """Jumeau async de `search_track` (mêmes params, même sélection)."""
        data = await self._make_request_async(
            http, "search", self._search_params(artist, title, strict)
        )
        return self._first_search_hit(data, artist, title)

    @staticmethod
    def _search_params(artist: str, title: str, strict: bool) -> dict:
        """Requête de recherche avancée (commun sync/async)."""
        params = {"q": f'artist:"{artist}" track:"{title}"', "limit": 5}
        if strict:
            params["strict"] = "on"
        return params

    @staticmethod
    def _first_search_hit(data: dict | None, artist: str, title: str) -> dict | None:
        """Premier résultat (meilleur match) ou None (commun sync/async)."""
        if not data or "data" not in data or not data["data"]:
            logger.warning(f"Aucun résultat trouvé pour: {artist} - {title}")
            return None
        return data["data"][0]

    def get_isrc(self, artist: str, title: str) -> str | None:
        """
        Récupère rapidement l'ISRC d'un morceau (recherche Deezer).
        Utilisé pour alimenter ReccoBeats sans scraper de Spotify ID.

        Returns:
            L'ISRC (str) ou None si non trouvé.
        """
        # Le réseau/JSON est déjà géré par _make_request (retourne None) : ce garde
        # ne couvre plus qu'un accès inattendu sur le hit → warning, pas de silence.
        try:
            track_data = self.search_track(artist, title)
            if track_data and track_data.get("isrc"):
                return track_data["isrc"]
        except (AttributeError, TypeError) as e:
            logger.warning(f"get_isrc échec pour {artist} - {title}: {e}")
        return None

    async def get_isrc_async(self, http: "AsyncHttpSession", artist: str, title: str) -> str | None:
        """Jumeau async de `get_isrc`."""
        try:
            track_data = await self.search_track_async(http, artist, title)
            if track_data and track_data.get("isrc"):
                return track_data["isrc"]
        except (AttributeError, TypeError) as e:
            logger.warning(f"get_isrc échec pour {artist} - {title}: {e}")
        return None

    def get_track_by_id(self, track_id: int) -> dict | None:
        """
        Récupère les informations d'un track par son ID

        Args:
            track_id: ID Deezer du track

        Returns:
            Données complètes du track ou None
        """
        data = self._make_request(f"track/{track_id}")
        return data

    def search_artist(self, name: str) -> dict | None:
        """Recherche un artiste par nom (`GET /search/artist`).

        Chantier « Media » : expose ``id`` et ``picture_xl`` (photo de profil
        haute résolution). Renvoie le premier résultat (meilleur match) ou None.
        """
        data = self._make_request("search/artist", {"q": name, "limit": 5})
        if not data or not data.get("data"):
            logger.warning(f"Aucun artiste Deezer trouvé pour: {name}")
            return None
        return data["data"][0]

    def get_album(self, album_id: int) -> dict | None:
        """Récupère un album par son ID (`GET /album/{id}`).

        Chantier « Media » : expose ``cover_xl`` (pochette haute résolution).
        """
        return self._make_request(f"album/{album_id}")

    def extract_enrichment_data(self, track_data: dict) -> dict[str, Any]:
        """
        Extrait les données d'enrichissement depuis les données Deezer

        Args:
            track_data: Données brutes du track depuis l'API

        Returns:
            Dictionnaire avec les champs d'enrichissement
        """
        # bpm : Deezer renvoie souvent 0 (rap surtout) -> ne garder que les valeurs utiles
        raw_bpm = track_data.get("bpm")
        deezer_bpm = (
            raw_bpm if isinstance(raw_bpm, (int, float)) and raw_bpm and raw_bpm > 0 else None
        )

        # release_date : Deezer renvoie parfois "0000-00-00" (date inconnue) -> None
        raw_date = track_data.get("release_date")
        deezer_date = raw_date if (raw_date and not str(raw_date).startswith("0000")) else None

        enriched_data = {
            "deezer_track_id": track_data.get("id"),
            "deezer_isrc": track_data.get("isrc"),  # pivot inter-sources (présent dès la recherche)
            "deezer_bpm": deezer_bpm,  # opportuniste (souvent absent)
            "deezer_duration": track_data.get("duration"),  # en secondes
            "deezer_explicit_lyrics": track_data.get("explicit_lyrics", False),
            "deezer_readable": track_data.get("readable", False),
            "deezer_release_date": deezer_date,
            "deezer_picture": None,
            "deezer_rank": track_data.get("rank"),
            "deezer_link": track_data.get("link"),
            # Chantier « Media » : ids + covers haute résolution (déjà présents
            # dans track_data, jamais lus jusqu'ici). Consommés par media_enricher.
            "deezer_album_id": None,
            "deezer_artist_id": None,
            "deezer_cover_xl": None,
            "deezer_picture_xl": None,
        }

        # Album : image medium (historique, compat provider) + id/cover_xl (Media)
        album = track_data.get("album")
        if isinstance(album, dict) and album:
            enriched_data["deezer_picture"] = album.get("cover_medium") or album.get("cover")
            enriched_data["deezer_album_id"] = album.get("id")
            enriched_data["deezer_cover_xl"] = album.get("cover_xl") or album.get("cover_big")

        # Artiste : id + picture_xl (Media) + fallback deezer_picture si pas d'album
        artist = track_data.get("artist")
        if isinstance(artist, dict) and artist:
            enriched_data["deezer_artist_id"] = artist.get("id")
            enriched_data["deezer_picture_xl"] = artist.get("picture_xl") or artist.get(
                "picture_big"
            )
            if not enriched_data["deezer_picture"]:
                enriched_data["deezer_picture"] = artist.get("picture_medium") or artist.get(
                    "picture"
                )

        return enriched_data

    def verify_duration(
        self, deezer_duration: int, previous_duration: int | None, tolerance: int = 2
    ) -> dict[str, Any]:
        """
        Vérifie la cohérence de la durée avec les enrichissements précédents

        Args:
            deezer_duration: Durée depuis Deezer (en secondes)
            previous_duration: Durée des enrichissements précédents (en secondes ou format "MM:SS")
            tolerance: Tolérance en secondes pour la différence

        Returns:
            Résultat de la vérification
        """
        if previous_duration is None:
            return {
                "is_valid": True,
                "message": "Aucune durée précédente à comparer",
                "difference": None,
            }

        # Convertir previous_duration si c'est une string au format "MM:SS"
        if isinstance(previous_duration, str):
            try:
                if ":" in previous_duration:
                    parts = previous_duration.split(":")
                    previous_duration = int(parts[0]) * 60 + int(parts[1])
                else:
                    previous_duration = int(previous_duration)
            except (ValueError, IndexError) as e:
                logger.warning(f"Impossible de convertir la durée '{previous_duration}': {e}")
                return {
                    "is_valid": False,
                    "message": f"Format de durée invalide: {previous_duration}",
                    "difference": None,
                }

        difference = abs(deezer_duration - previous_duration)
        is_valid = difference <= tolerance

        return {
            "is_valid": is_valid,
            "difference": difference,
            "deezer_duration": deezer_duration,
            "previous_duration": previous_duration,
            "message": f"Différence de {difference}s" if not is_valid else "Durée cohérente",
        }

    def verify_release_date(
        self, deezer_release_date: str, scraped_release_date: str | None
    ) -> dict[str, Any]:
        """
        Vérifie la cohérence de la date de sortie avec les données scrapées

        Args:
            deezer_release_date: Date de sortie depuis Deezer (format: YYYY-MM-DD)
            scraped_release_date: Date de sortie depuis le scraping

        Returns:
            Résultat de la vérification
        """
        if not scraped_release_date:
            return {
                "is_valid": True,
                "message": "Aucune date scrapée à comparer",
                "dates_match": None,
            }

        try:
            # Parser les dates
            deezer_date = datetime.strptime(deezer_release_date, "%Y-%m-%d")

            # Essayer différents formats pour la date scrapée
            scraped_date = None
            for fmt in ["%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y"]:
                try:
                    scraped_date = datetime.strptime(scraped_release_date, fmt)
                    break
                except ValueError:
                    continue

            if scraped_date is None:
                return {
                    "is_valid": False,
                    "message": "Format de date scrapée non reconnu",
                    "dates_match": False,
                }

            # Comparer les dates
            dates_match = deezer_date.date() == scraped_date.date()

            return {
                "is_valid": True,
                "dates_match": dates_match,
                "deezer_date": deezer_release_date,
                "scraped_date": scraped_release_date,
                "message": "Dates identiques" if dates_match else "Dates différentes",
            }

        except ValueError as e:
            return {"is_valid": False, "message": f"Erreur de parsing: {e}", "dates_match": False}

    def enrich_track(
        self,
        artist: str,
        title: str,
        previous_duration: int | None = None,
        scraped_release_date: str | None = None,
    ) -> dict[str, Any]:
        """
        Enrichit les données d'un track avec vérifications

        Args:
            artist: Nom de l'artiste
            title: Titre de la chanson
            previous_duration: Durée depuis les enrichissements précédents
            scraped_release_date: Date de sortie depuis le scraping

        Returns:
            Données enrichies avec vérifications
        """
        track_data = self.search_track(artist, title)
        return self._build_enrichment_result(track_data, previous_duration, scraped_release_date)

    async def enrich_track_async(
        self,
        http: "AsyncHttpSession",
        artist: str,
        title: str,
        previous_duration: int | None = None,
        scraped_release_date: str | None = None,
    ) -> dict[str, Any]:
        """Jumeau async d'`enrich_track` (mêmes vérifications, même forme de retour)."""
        track_data = await self.search_track_async(http, artist, title)
        return self._build_enrichment_result(track_data, previous_duration, scraped_release_date)

    def _build_enrichment_result(
        self,
        track_data: dict | None,
        previous_duration: int | None,
        scraped_release_date: str | None,
    ) -> dict[str, Any]:
        """Extraction + vérifications sur un hit de recherche (commun sync/async)."""
        if not track_data:
            return {
                "success": False,
                "error": "Track non trouvé sur Deezer",
                "data": None,
                "verifications": None,
            }

        # Extraire les données d'enrichissement
        enriched_data = self.extract_enrichment_data(track_data)

        # Effectuer les vérifications
        verifications = {}

        if enriched_data["deezer_duration"] is not None:
            verifications["duration"] = self.verify_duration(
                enriched_data["deezer_duration"], previous_duration
            )

        if enriched_data["deezer_release_date"] is not None:
            verifications["release_date"] = self.verify_release_date(
                enriched_data["deezer_release_date"], scraped_release_date
            )

        return {
            "success": True,
            "data": enriched_data,
            "verifications": verifications,
            "raw_data": track_data,  # Pour debug si nécessaire
        }


# Exemple d'utilisation
if __name__ == "__main__":
    # Initialiser le client
    deezer = DeezerAPI()

    # Exemple 1: Enrichir un track avec vérifications
    print("=== Exemple 1: Enrichissement avec vérifications ===")
    result = deezer.enrich_track(
        artist="Eminem",
        title="Lose Yourself",
        previous_duration=326,  # Durée depuis SongBPM/Reccobeats
        scraped_release_date="2002-10-28",  # Date depuis le scraping
    )

    if result["success"]:
        print("\n✓ Track trouvé!")
        print(f"  ID Deezer: {result['data']['deezer_track_id']}")
        print(f"  Durée: {result['data']['deezer_duration']}s")
        print(f"  Explicit: {result['data']['deezer_explicit_lyrics']}")
        print(f"  Readable: {result['data']['deezer_readable']}")
        print(f"  Date de sortie: {result['data']['deezer_release_date']}")
        print(f"  Image: {result['data']['deezer_picture'][:50]}...")

        print("\n--- Vérifications ---")
        if "duration" in result["verifications"]:
            dur_check = result["verifications"]["duration"]
            print(f"  Durée: {dur_check['message']}")

        if "release_date" in result["verifications"]:
            date_check = result["verifications"]["release_date"]
            print(f"  Date: {date_check['message']}")
    else:
        print(f"✗ Erreur: {result['error']}")

    # Exemple 2: Recherche simple
    print("\n\n=== Exemple 2: Recherche simple ===")
    track = deezer.search_track("Aloe Blacc", "I Need a Dollar")
    if track:
        print(f"✓ Trouvé: {track['title']} - {track['artist']['name']}")
        print(f"  Durée: {track['duration']}s")
        print(f"  ID: {track['id']}")

    # Exemple 3: Récupération par ID
    print("\n\n=== Exemple 3: Récupération par ID ===")
    track_by_id = deezer.get_track_by_id(3135556)
    if track_by_id:
        print(f"✓ Track: {track_by_id['title']}")
        print(f"  Artiste: {track_by_id['artist']['name']}")
        print(f"  Album: {track_by_id['album']['title']}")
