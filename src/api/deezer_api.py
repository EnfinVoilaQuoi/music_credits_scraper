"""
Module d'enrichissement des données musicales via l'API Deezer
Troisième enrichisseur dans la chaîne : Reccobeats -> SongBPM -> Deezer
"""

import requests
import time
from typing import Dict, Optional, Any
from datetime import datetime
import logging

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
            t for t in self.request_times 
            if current_time - t < self.RATE_LIMIT_WINDOW
        ]
        
        # Si on atteint la limite, attendre
        if len(self.request_times) >= self.RATE_LIMIT:
            sleep_time = self.RATE_LIMIT_WINDOW - (current_time - self.request_times[0])
            if sleep_time > 0:
                logger.warning(f"Rate limit atteint. Attente de {sleep_time:.2f}s")
                time.sleep(sleep_time)
                self.request_times = []
        
        self.request_times.append(current_time)
    
    def _make_request(self, endpoint: str, params: Optional[Dict] = None) -> Optional[Dict]:
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
            data = response.json()
            
            # Vérifier les erreurs de l'API Deezer
            if "error" in data:
                error_type = data["error"].get("type", "Unknown")
                error_message = data["error"].get("message", "Unknown error")
                logger.error(f"Erreur API Deezer: {error_type} - {error_message}")
                return None
            
            return data
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Erreur de requête: {e}")
            return None
        except ValueError as e:
            logger.error(f"Erreur de parsing JSON: {e}")
            return None
    
    def search_track(self, artist: str, title: str, strict: bool = False) -> Optional[Dict]:
        """
        Recherche un track par artiste et titre
        
        Args:
            artist: Nom de l'artiste
            title: Titre de la chanson
            strict: Active le mode strict (désactive le fuzzy matching)
            
        Returns:
            Données du premier résultat ou None
        """
        # Construction de la requête de recherche avancée
        query = f'artist:"{artist}" track:"{title}"'
        
        params = {
            "q": query,
            "limit": 5  # Récupérer plusieurs résultats pour choisir le meilleur
        }
        
        if strict:
            params["strict"] = "on"
        
        data = self._make_request("search", params)
        
        if not data or "data" not in data or not data["data"]:
            logger.warning(f"Aucun résultat trouvé pour: {artist} - {title}")
            return None
        
        # Retourner le premier résultat (meilleur match)
        return data["data"][0]
    
    def get_track_by_id(self, track_id: int) -> Optional[Dict]:
        """
        Récupère les informations d'un track par son ID
        
        Args:
            track_id: ID Deezer du track
            
        Returns:
            Données complètes du track ou None
        """
        data = self._make_request(f"track/{track_id}")
        return data
    
    def extract_enrichment_data(self, track_data: Dict) -> Dict[str, Any]:
        """
        Extrait les données d'enrichissement depuis les données Deezer
        
        Args:
            track_data: Données brutes du track depuis l'API
            
        Returns:
            Dictionnaire avec les champs d'enrichissement
        """
        enriched_data = {
            "deezer_track_id": track_data.get("id"),
            "deezer_duration": track_data.get("duration"),  # en secondes
            "deezer_explicit_lyrics": track_data.get("explicit_lyrics", False),
            "deezer_readable": track_data.get("readable", False),
            "deezer_release_date": track_data.get("release_date"),
            "deezer_picture": None,
            "deezer_rank": track_data.get("rank"),
            "deezer_link": track_data.get("link"),
        }
        
        # Récupérer l'image depuis l'album
        if "album" in track_data and track_data["album"]:
            album = track_data["album"]
            enriched_data["deezer_picture"] = album.get("cover_medium") or album.get("cover")
        
        # Si pas d'image d'album, essayer l'artiste
        if not enriched_data["deezer_picture"] and "artist" in track_data:
            artist = track_data["artist"]
            enriched_data["deezer_picture"] = artist.get("picture_medium") or artist.get("picture")
        
        return enriched_data
    
    def verify_duration(self, deezer_duration: int, previous_duration: Optional[int],
                       tolerance: int = 2) -> Dict[str, Any]:
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
                "difference": None
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
                    "difference": None
                }

        difference = abs(deezer_duration - previous_duration)
        is_valid = difference <= tolerance
        
        return {
            "is_valid": is_valid,
            "difference": difference,
            "deezer_duration": deezer_duration,
            "previous_duration": previous_duration,
            "message": f"Différence de {difference}s" if not is_valid else "Durée cohérente"
        }
    
    def verify_release_date(self, deezer_release_date: str, 
                           scraped_release_date: Optional[str]) -> Dict[str, Any]:
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
                "dates_match": None
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
                    "dates_match": False
                }
            
            # Comparer les dates
            dates_match = deezer_date.date() == scraped_date.date()
            
            return {
                "is_valid": True,
                "dates_match": dates_match,
                "deezer_date": deezer_release_date,
                "scraped_date": scraped_release_date,
                "message": "Dates identiques" if dates_match else "Dates différentes"
            }
            
        except ValueError as e:
            return {
                "is_valid": False,
                "message": f"Erreur de parsing: {e}",
                "dates_match": False
            }
    
    def enrich_track(self, artist: str, title: str, 
                    previous_duration: Optional[int] = None,
                    scraped_release_date: Optional[str] = None) -> Dict[str, Any]:
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
        # Rechercher le track
        track_data = self.search_track(artist, title)
        
        if not track_data:
            return {
                "success": False,
                "error": "Track non trouvé sur Deezer",
                "data": None,
                "verifications": None
            }
        
        # Extraire les données d'enrichissement
        enriched_data = self.extract_enrichment_data(track_data)
        
        # Effectuer les vérifications
        verifications = {}
        
        if enriched_data["deezer_duration"] is not None:
            verifications["duration"] = self.verify_duration(
                enriched_data["deezer_duration"],
                previous_duration
            )
        
        if enriched_data["deezer_release_date"] is not None:
            verifications["release_date"] = self.verify_release_date(
                enriched_data["deezer_release_date"],
                scraped_release_date
            )
        
        return {
            "success": True,
            "data": enriched_data,
            "verifications": verifications,
            "raw_data": track_data  # Pour debug si nécessaire
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
        scraped_release_date="2002-10-28"  # Date depuis le scraping
    )
    
    if result["success"]:
        print(f"\n✓ Track trouvé!")
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
