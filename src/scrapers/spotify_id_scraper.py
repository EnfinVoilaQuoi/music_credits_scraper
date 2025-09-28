"""
Scraper pour récupérer les IDs Spotify sans utiliser l'API Spotify
Recherche sur le web et extrait les IDs depuis les URLs Spotify
"""
import requests
import re
import time
import urllib.parse
from typing import Optional, List
from bs4 import BeautifulSoup
import logging

logger = logging.getLogger('SpotifyIDScraper')

class SpotifyIDScraper:
    """Scraper pour récupérer les IDs Spotify via recherche web"""
    
    def __init__(self, cache_file: str = "spotify_ids_cache.json"):
        self.cache_file = cache_file
        self.cache = self._load_cache()
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })
        
        # Pattern pour extraire les IDs Spotify depuis les URLs
        self.spotify_id_patterns = [
            r'open\.spotify\.com/(?:intl-[a-z]{2}/)?track/([a-zA-Z0-9]{22})',
            r'spotify:track:([a-zA-Z0-9]{22})',
        ]
    
    def _load_cache(self):
        """Charge le cache depuis le fichier"""
        try:
            import json
            with open(self.cache_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
    
    def _save_cache(self):
        """Sauvegarde le cache"""
        try:
            import json
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.cache, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Erreur sauvegarde cache: {e}")
    
    def _get_cache_key(self, artist: str, title: str) -> str:
        return f"{artist.lower().strip()}::{title.lower().strip()}"
    
    def extract_spotify_id_from_url(self, url: str) -> Optional[str]:
        """Extrait l'ID Spotify depuis une URL"""
        for pattern in self.spotify_id_patterns:
            match = re.search(pattern, url)
            if match:
                spotify_id = match.group(1)
                # Vérifier que c'est bien un ID Spotify valide (22 caractères alphanumériques)
                if len(spotify_id) == 22 and spotify_id.isalnum():
                    logger.debug(f"ID Spotify extrait: {spotify_id}")
                    return spotify_id
        return None
    
    def search_google_for_spotify_track(self, artist: str, title: str) -> Optional[str]:
        """Recherche un track sur Google et extrait l'ID Spotify"""
        query = f'"{artist}" "{title}" site:open.spotify.com'
        encoded_query = urllib.parse.quote_plus(query)
        
        google_url = f"https://www.google.com/search?q={encoded_query}"
        
        try:
            logger.debug(f"Recherche Google: {query}")
            response = self.session.get(google_url, timeout=10)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Chercher tous les liens vers Spotify
                links = soup.find_all('a', href=True)
                
                for link in links:
                    href = link.get('href', '')
                    
                    # Nettoyer l'URL (Google encode souvent les URLs)
                    if 'open.spotify.com' in href:
                        # Extraire l'URL réelle depuis les redirections Google
                        if href.startswith('/url?q='):
                            actual_url = urllib.parse.unquote(href.split('/url?q=')[1].split('&')[0])
                        else:
                            actual_url = href
                        
                        spotify_id = self.extract_spotify_id_from_url(actual_url)
                        if spotify_id:
                            logger.info(f"✅ ID Spotify trouvé via Google: {spotify_id}")
                            return spotify_id
                
                logger.warning("Aucun lien Spotify trouvé dans les résultats Google")
            else:
                logger.warning(f"Erreur recherche Google: {response.status_code}")
                
        except Exception as e:
            logger.error(f"Erreur recherche Google: {e}")
        
        return None
    
    def search_duckduckgo_for_spotify_track(self, artist: str, title: str) -> Optional[str]:
        """Recherche alternative avec DuckDuckGo"""
        query = f'"{artist}" "{title}" site:open.spotify.com'
        encoded_query = urllib.parse.quote_plus(query)
        
        ddg_url = f"https://html.duckduckgo.com/html/?q={encoded_query}"
        
        try:
            logger.debug(f"Recherche DuckDuckGo: {query}")
            response = self.session.get(ddg_url, timeout=10)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Chercher les liens vers Spotify
                links = soup.find_all('a', href=True)
                
                for link in links:
                    href = link.get('href', '')
                    
                    if 'open.spotify.com' in href:
                        spotify_id = self.extract_spotify_id_from_url(href)
                        if spotify_id:
                            logger.info(f"✅ ID Spotify trouvé via DuckDuckGo: {spotify_id}")
                            return spotify_id
                
                logger.warning("Aucun lien Spotify trouvé dans DuckDuckGo")
            else:
                logger.warning(f"Erreur recherche DuckDuckGo: {response.status_code}")
                
        except Exception as e:
            logger.error(f"Erreur recherche DuckDuckGo: {e}")
        
        return None
    
    def search_direct_spotify_web(self, artist: str, title: str) -> Optional[str]:
        """Tentative de recherche directe sur Spotify Web (plus risqué)"""
        try:
            # Construire une URL de recherche Spotify
            query = f"{artist} {title}"
            encoded_query = urllib.parse.quote_plus(query)
            spotify_search_url = f"https://open.spotify.com/search/{encoded_query}"
            
            logger.debug(f"Recherche directe Spotify: {spotify_search_url}")
            
            # Headers pour imiter un navigateur
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive',
            }
            
            response = self.session.get(spotify_search_url, headers=headers, timeout=15)
            
            if response.status_code == 200:
                # Chercher les IDs Spotify dans le HTML de la page
                spotify_ids = []
                for pattern in self.spotify_id_patterns:
                    matches = re.findall(pattern, response.text)
                    spotify_ids.extend(matches)
                
                if spotify_ids:
                    # Prendre le premier ID trouvé (probablement le plus pertinent)
                    spotify_id = spotify_ids[0]
                    logger.info(f"✅ ID Spotify trouvé directement: {spotify_id}")
                    return spotify_id
                else:
                    logger.warning("Aucun ID Spotify trouvé dans la page")
            else:
                logger.warning(f"Erreur recherche directe Spotify: {response.status_code}")
                
        except Exception as e:
            logger.error(f"Erreur recherche directe Spotify: {e}")
        
        return None
    
    def get_spotify_id(self, artist: str, title: str) -> Optional[str]:
        """
        Méthode principale pour récupérer l'ID Spotify d'un track
        Utilise plusieurs stratégies en cascade
        """
        logger.info(f"Recherche ID Spotify pour: '{artist}' - '{title}'")
        
        # Vérifier le cache d'abord
        cache_key = self._get_cache_key(artist, title)
        if cache_key in self.cache:
            cached_id = self.cache[cache_key]
            if cached_id != 'not_found':
                logger.debug(f"ID trouvé en cache: {cached_id}")
                return cached_id
            else:
                logger.debug("Échec précédent en cache")
                return None
        
        # Stratégie 1: Recherche Google
        spotify_id = self.search_google_for_spotify_track(artist, title)
        
        # Stratégie 2: Recherche DuckDuckGo (si Google échoue)
        if not spotify_id:
            time.sleep(2)  # Délai entre les recherches
            spotify_id = self.search_duckduckgo_for_spotify_track(artist, title)
        
        # Stratégie 3: Recherche directe Spotify (en dernier recours)
        if not spotify_id:
            time.sleep(2)
            spotify_id = self.search_direct_spotify_web(artist, title)
        
        # Mettre en cache le résultat
        if spotify_id:
            self.cache[cache_key] = spotify_id
            logger.info(f"✅ Succès: ID Spotify {spotify_id} pour '{title}'")
        else:
            self.cache[cache_key] = 'not_found'
            logger.warning(f"❌ Aucun ID Spotify trouvé pour '{title}'")
        
        self._save_cache()
        
        # Délai respectueux entre les requêtes
        time.sleep(1)
        
        return spotify_id
    
    def get_multiple_spotify_ids(self, tracks: List[tuple]) -> dict:
        """
        Récupère les IDs Spotify pour plusieurs tracks
        tracks: Liste de tuples (artist, title)
        """
        logger.info(f"Récupération de {len(tracks)} IDs Spotify")
        
        results = {}
        
        for i, (artist, title) in enumerate(tracks):
            logger.debug(f"Traitement {i+1}/{len(tracks)}: '{artist}' - '{title}'")
            
            spotify_id = self.get_spotify_id(artist, title)
            results[f"{artist}::{title}"] = spotify_id
            
            # Délai plus long entre les tracks pour éviter le rate limiting
            if i < len(tracks) - 1:  # Pas de délai après le dernier
                time.sleep(3)
        
        success_count = len([v for v in results.values() if v])
        logger.info(f"Résultats: {success_count}/{len(tracks)} IDs Spotify récupérés")
        
        return results
    
    def test_extraction(self, test_url: str = "https://open.spotify.com/intl-fr/track/4EVMhVr6GslvST0uLx8VIJ"):
        """Teste l'extraction d'ID depuis une URL"""
        spotify_id = self.extract_spotify_id_from_url(test_url)
        logger.info(f"Test extraction - URL: {test_url}")
        logger.info(f"ID extrait: {spotify_id}")
        return spotify_id
