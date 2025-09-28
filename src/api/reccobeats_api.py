"""
ReccoBeats API intégré avec scraper Spotify ID amélioré
Version robuste avec Selenium et webdriver_manager
"""
import requests
import json
import time
import logging
import re
import urllib.parse
import random
from typing import Dict, List, Optional
from pathlib import Path
from bs4 import BeautifulSoup

# Imports Selenium
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.common.exceptions import TimeoutException, WebDriverException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager

logger = logging.getLogger('ReccoBeatsIntegrated')

class ReccoBeatsIntegratedClient:
    """Client ReccoBeats avec scraper Spotify ID intégré + Selenium amélioré"""
    
    def __init__(self, cache_file: str = "reccobeats_integrated_cache.json", headless: bool = False):
        self.cache_file = cache_file
        self.cache = self._load_cache()
        self.headless = headless
        
        # Configuration ReccoBeats
        self.recco_base_url = "https://api.reccobeats.com/v1"
        self.recco_session = requests.Session()
        self.recco_session.headers.update({
            'Accept': 'application/json',
            'User-Agent': 'ReccoBeats-Python-Client/3.0'
        })
        
        # Configuration Scraper classique
        self.scraper_session = requests.Session()
        self.scraper_session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })
        
        # Configuration Selenium
        self.driver = None
        self.wait = None
        self.use_selenium = True
        self.selenium_timeout = 30
        
        # Patterns améliorés pour extraire les IDs Spotify
        self.spotify_id_patterns = [
            r'open\.spotify\.com/(?:intl-[a-z]{2}/)?track/([a-zA-Z0-9]{22})',
            r'spotify\.com/track/([a-zA-Z0-9]{22})',
            r'spotify:track:([a-zA-Z0-9]{22})',
            r'/track/([a-zA-Z0-9]{22})(?:\?|$|/)',
        ]
        
        logger.info(f"ReccoBeats client initialisé (headless={headless})")

    def _load_cache(self) -> Dict:
        """Charge le cache depuis le fichier"""
        try:
            with open(self.cache_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_cache(self):
        """Sauvegarde le cache"""
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.cache, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Erreur sauvegarde cache: {e}")

    def _get_cache_key(self, artist: str, title: str) -> str:
        return f"{artist.lower().strip()}::{title.lower().strip()}"

    def extract_spotify_id_from_url(self, url: str) -> Optional[str]:
        """Extrait l'ID Spotify depuis une URL avec validation améliorée"""
        if not url or 'spotify' not in url.lower():
            return None
            
        # Nettoyer l'URL
        url = url.strip()
        
        for pattern in self.spotify_id_patterns:
            match = re.search(pattern, url)
            if match:
                spotify_id = match.group(1)
                # Valider l'ID (22 caractères alphanumériques avec - et _)
                if len(spotify_id) == 22 and re.match(r'^[a-zA-Z0-9_-]+$', spotify_id):
                    return spotify_id
        return None

    # ========== MÉTHODES SELENIUM AMÉLIORÉES ==========

    def _init_selenium_driver(self):
        """Initialise le driver Selenium avec webdriver_manager"""
        if self.driver:
            return  # Déjà initialisé
        
        logger.info(f"Initialisation du driver Selenium (headless={self.headless})...")
        
        try:
            options = Options()
            
            # Mode headless ou visible selon configuration
            if self.headless:
                options.add_argument('--headless=new')
            
            # Options standards pour éviter la détection
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-gpu')
            options.add_argument('--window-size=1920,1080')
            options.add_argument('--disable-blink-features=AutomationControlled')
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option('useAutomationExtension', False)
            
            # User-Agent réaliste
            options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
            
            # Réduire les logs
            options.add_argument('--log-level=3')
            options.add_argument('--silent')
            options.add_experimental_option('excludeSwitches', ['enable-logging'])
            
            # Désactiver les images pour accélérer (optionnel)
            prefs = {
                "profile.managed_default_content_settings.images": 2,
                "profile.default_content_setting_values.notifications": 2,
                "profile.default_content_settings.popups": 0
            }
            options.add_experimental_option("prefs", prefs)
            
            # Utiliser webdriver_manager pour gérer ChromeDriver automatiquement
            service = ChromeService(ChromeDriverManager().install())
            
            self.driver = webdriver.Chrome(service=service, options=options)
            self.wait = WebDriverWait(self.driver, self.selenium_timeout)
            
            # Script pour masquer les signes d'automation
            self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            logger.info(f"✅ Driver Selenium initialisé avec succès")
            
        except Exception as e:
            logger.error(f"❌ Erreur initialisation Selenium: {e}")
            self.driver = None
            self.wait = None
            raise

    def _close_selenium_driver(self):
        """Ferme le driver Selenium"""
        if self.driver:
            try:
                self.driver.quit()
                logger.info("Driver Selenium fermé")
            except:
                pass
            finally:
                self.driver = None
                self.wait = None

    def _handle_cookies(self):
        """Gère les popups de cookies"""
        try:
            # Essayer plusieurs sélecteurs courants pour les boutons de cookies
            cookie_selectors = [
                "button[id*='accept']",
                "button[class*='accept']",
                "button[class*='consent']",
                "button[class*='agree']",
                "button[aria-label*='accept']",
                "button[aria-label*='consent']"
            ]
            
            for selector in cookie_selectors:
                try:
                    cookie_button = self.driver.find_element(By.CSS_SELECTOR, selector)
                    if cookie_button.is_displayed():
                        cookie_button.click()
                        time.sleep(1)
                        logger.debug("Cookies acceptés")
                        break
                except NoSuchElementException:
                    continue
        except Exception:
            pass  # Pas grave si pas de cookies

    def _search_google_selenium(self, artist: str, title: str) -> Optional[str]:
        """Recherche Google améliorée avec Selenium"""
        logger.info(f"Recherche Google Selenium pour: {artist} - {title}")
        
        try:
            if not self.driver:
                self._init_selenium_driver()
            
            if not self.driver:
                logger.error("Driver Selenium non disponible")
                return None
            
            # Construire plusieurs variantes de requêtes
            queries = [
                f'"{artist}" "{title}" site:open.spotify.com',
                f'{artist} {title} spotify',
                f'spotify track {artist} {title}'
            ]
            
            for query in queries:
                logger.debug(f"Tentative avec requête: {query}")
                
                try:
                    # Naviguer vers Google
                    google_url = f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}"
                    self.driver.get(google_url)
                    
                    # Gérer les cookies
                    self._handle_cookies()
                    
                    # Attendre le chargement
                    time.sleep(2)
                    
                    # Vérifier s'il y a un CAPTCHA
                    page_source = self.driver.page_source.lower()
                    if any(sign in page_source for sign in ['captcha', 'unusual traffic', 'verify']):
                        logger.warning("CAPTCHA détecté sur Google")
                        if not self.headless:
                            input("Résolvez le CAPTCHA et appuyez sur Entrée...")
                        else:
                            continue
                    
                    # Chercher les liens Spotify de plusieurs façons
                    spotify_links = []
                    
                    # Méthode 1: Liens directs
                    try:
                        links = self.driver.find_elements(By.PARTIAL_LINK_TEXT, "Spotify")
                        spotify_links.extend([l.get_attribute('href') for l in links if l.get_attribute('href')])
                    except:
                        pass
                    
                    # Méthode 2: Par sélecteur CSS
                    try:
                        links = self.driver.find_elements(By.CSS_SELECTOR, "a[href*='spotify.com']")
                        spotify_links.extend([l.get_attribute('href') for l in links if l.get_attribute('href')])
                    except:
                        pass
                    
                    # Méthode 3: Par XPath
                    try:
                        links = self.driver.find_elements(By.XPATH, "//a[contains(@href, 'spotify.com/track')]")
                        spotify_links.extend([l.get_attribute('href') for l in links if l.get_attribute('href')])
                    except:
                        pass
                    
                    # Méthode 4: Chercher dans tout le HTML
                    try:
                        soup = BeautifulSoup(self.driver.page_source, 'html.parser')
                        for link in soup.find_all('a', href=True):
                            href = link['href']
                            if 'spotify.com' in href:
                                # Nettoyer les URLs Google
                                if '/url?q=' in href:
                                    href = urllib.parse.unquote(href.split('/url?q=')[1].split('&')[0])
                                spotify_links.append(href)
                    except:
                        pass
                    
                    # Traiter les liens trouvés
                    spotify_links = list(set(spotify_links))  # Dédupliquer
                    logger.debug(f"Liens Spotify trouvés: {len(spotify_links)}")
                    
                    for link in spotify_links:
                        if link:
                            spotify_id = self.extract_spotify_id_from_url(link)
                            if spotify_id:
                                logger.info(f"✅ ID Spotify trouvé via Google Selenium: {spotify_id}")
                                return spotify_id
                    
                except Exception as e:
                    logger.debug(f"Erreur avec la requête {query}: {e}")
                    continue
                
                # Petit délai entre les requêtes
                time.sleep(random.uniform(2, 4))
            
        except Exception as e:
            logger.error(f"Erreur Google Selenium: {e}")
        
        return None

    def _search_spotify_direct_selenium(self, artist: str, title: str) -> Optional[str]:
        """Recherche directe sur Spotify Web avec Selenium"""
        logger.info(f"Recherche directe Spotify pour: {artist} - {title}")
        
        try:
            if not self.driver:
                self._init_selenium_driver()
            
            if not self.driver:
                return None
            
            # URL de recherche Spotify
            query = f"{artist} {title}"
            spotify_url = f"https://open.spotify.com/search/{urllib.parse.quote(query)}"
            
            logger.debug(f"Navigation vers: {spotify_url}")
            self.driver.get(spotify_url)
            
            # Attendre le chargement
            time.sleep(3)
            
            # Chercher le premier résultat de type "track"
            try:
                # Attendre que les résultats se chargent
                self.wait.until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "[data-testid*='track']"))
                )
                
                # Récupérer l'URL actuelle après navigation
                current_url = self.driver.current_url
                
                # Chercher les liens vers des tracks
                track_links = self.driver.find_elements(By.CSS_SELECTOR, "a[href*='/track/']")
                
                for link in track_links[:5]:  # Vérifier les 5 premiers
                    href = link.get_attribute('href')
                    if href:
                        spotify_id = self.extract_spotify_id_from_url(href)
                        if spotify_id:
                            # Vérifier si le titre correspond approximativement
                            try:
                                link_text = link.text.lower()
                                if title.lower() in link_text or artist.lower() in link_text:
                                    logger.info(f"✅ ID Spotify trouvé via recherche directe: {spotify_id}")
                                    return spotify_id
                            except:
                                # Retourner le premier ID trouvé si on ne peut pas vérifier
                                logger.info(f"✅ ID Spotify trouvé (premier résultat): {spotify_id}")
                                return spotify_id
                
            except TimeoutException:
                logger.warning("Timeout lors de la recherche Spotify directe")
            
        except Exception as e:
            logger.error(f"Erreur recherche directe Spotify: {e}")
        
        return None

    # ========== MÉTHODES SCRAPING CLASSIQUE (SANS SELENIUM) ==========

    def _search_google_requests(self, artist: str, title: str) -> Optional[str]:
        """Recherche sur Google avec requests (fallback)"""
        logger.info(f"Recherche Google classique pour: {artist} - {title}")
        
        try:
            # Délai anti-bot
            time.sleep(random.uniform(2, 4))
            
            # Requête Google
            query = f'"{artist}" "{title}" site:open.spotify.com'
            google_url = "https://www.google.com/search"
            params = {'q': query}
            
            response = self.scraper_session.get(google_url, params=params, timeout=10)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Chercher tous les liens
                for link in soup.find_all('a', href=True):
                    href = link.get('href', '')
                    
                    if 'spotify.com' in href:
                        # Nettoyer l'URL Google
                        if '/url?q=' in href:
                            actual_url = urllib.parse.unquote(href.split('/url?q=')[1].split('&')[0])
                        else:
                            actual_url = href
                        
                        spotify_id = self.extract_spotify_id_from_url(actual_url)
                        if spotify_id:
                            logger.info(f"✅ ID trouvé via Google requests: {spotify_id}")
                            return spotify_id
                
            elif response.status_code == 429:
                logger.warning("Rate limit Google")
            
        except Exception as e:
            logger.error(f"Erreur Google requests: {e}")
        
        return None

    def _search_duckduckgo(self, artist: str, title: str) -> Optional[str]:
        """Recherche sur DuckDuckGo (alternative)"""
        logger.info(f"Recherche DuckDuckGo pour: {artist} - {title}")
        
        try:
            time.sleep(random.uniform(1, 3))
            
            query = f'"{artist}" "{title}" site:open.spotify.com'
            ddg_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(query)}"
            
            response = self.scraper_session.get(ddg_url, timeout=10)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                for link in soup.find_all('a', href=True):
                    href = link.get('href', '')
                    
                    if 'spotify.com' in href:
                        spotify_id = self.extract_spotify_id_from_url(href)
                        if spotify_id:
                            logger.info(f"✅ ID trouvé via DuckDuckGo: {spotify_id}")
                            return spotify_id
            
        except Exception as e:
            logger.error(f"Erreur DuckDuckGo: {e}")
        
        return None

    # ========== MÉTHODE PRINCIPALE DE RECHERCHE ==========

    def search_spotify_id(self, artist: str, title: str, force_selenium: bool = False) -> Optional[str]:
        """
        Recherche l'ID Spotify d'un track avec stratégie multiple
        
        Args:
            artist: Nom de l'artiste
            title: Titre du morceau
            force_selenium: Force l'utilisation de Selenium même si désactivé
            
        Returns:
            L'ID Spotify du track ou None
        """
        # Vérifier le cache
        cache_key = self._get_cache_key(artist, title)
        if cache_key in self.cache:
            cached = self.cache[cache_key]
            if isinstance(cached, dict) and 'spotify_id' in cached:
                logger.info(f"ID trouvé dans le cache: {cached['spotify_id']}")
                return cached['spotify_id']
        
        spotify_id = None
        
        # Stratégie de recherche
        if self.use_selenium or force_selenium:
            # 1. Essayer Google avec Selenium
            spotify_id = self._search_google_selenium(artist, title)
            
            # 2. Si échec, essayer la recherche directe Spotify
            if not spotify_id:
                spotify_id = self._search_spotify_direct_selenium(artist, title)
        
        # 3. Si Selenium échoue ou est désactivé, utiliser les méthodes classiques
        if not spotify_id:
            spotify_id = self._search_google_requests(artist, title)
        
        # 4. Dernière tentative avec DuckDuckGo
        if not spotify_id:
            spotify_id = self._search_duckduckgo(artist, title)
        
        # Sauvegarder en cache
        if spotify_id:
            self.cache[cache_key] = {'spotify_id': spotify_id, 'timestamp': time.time()}
            self._save_cache()
            logger.info(f"✅ ID Spotify final: {spotify_id}")
        else:
            logger.warning(f"❌ Aucun ID trouvé pour: {artist} - {title}")
            self.cache[cache_key] = {'error': 'not_found', 'timestamp': time.time()}
            self._save_cache()
        
        return spotify_id

    # ========== MÉTHODES RECCOBEATS API ==========

    def get_track_from_reccobeats(self, spotify_id: str) -> Optional[Dict]:
        """Récupère les données d'un track depuis ReccoBeats via son ID Spotify"""
        try:
            url = f"{self.recco_base_url}/track"
            params = {'ids': spotify_id}
            
            response = self.recco_session.get(url, params=params, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                
                # Gérer les différentes structures de réponse ReccoBeats
                if isinstance(data, dict) and 'content' in data:
                    content = data['content']
                    if isinstance(content, list) and len(content) > 0:
                        return content[0]
                elif isinstance(data, list) and len(data) > 0:
                    return data[0]
                elif isinstance(data, dict):
                    return data
                
                logger.warning("Format de réponse ReccoBeats inattendu")
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Erreur API ReccoBeats: {e}")
        except json.JSONDecodeError as e:
            logger.error(f"Erreur parsing JSON ReccoBeats: {e}")
        
        return None

    def get_track_audio_features(self, reccobeats_id: str) -> Optional[Dict]:
        """Récupère les audio features depuis ReccoBeats"""
        try:
            url = f"{self.recco_base_url}/audio-features"
            params = {'ids': reccobeats_id}
            
            response = self.recco_session.get(url, params=params, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                
                if isinstance(data, list) and len(data) > 0:
                    return data[0]
                elif isinstance(data, dict):
                    return data
                
        except Exception as e:
            logger.error(f"Erreur récupération audio features: {e}")
        
        return None

    # ========== MÉTHODE PRINCIPALE INTÉGRÉE ==========

    def get_track_info(self, artist: str, title: str, use_cache: bool = True) -> Optional[Dict]:
        """
        Méthode principale : récupère toutes les infos d'un track
        
        Args:
            artist: Nom de l'artiste
            title: Titre du morceau
            use_cache: Utiliser le cache si disponible
            
        Returns:
            Dict avec toutes les infos (Spotify ID, features ReccoBeats, etc.)
        """
        # Vérifier le cache complet
        cache_key = self._get_cache_key(artist, title)
        if use_cache and cache_key in self.cache:
            cached = self.cache[cache_key]
            if isinstance(cached, dict) and 'success' in cached and cached['success']:
                logger.info(f"Données complètes trouvées dans le cache pour: {artist} - {title}")
                return cached
        
        # Étape 1: Rechercher l'ID Spotify
        logger.info(f"Recherche de l'ID Spotify pour: {artist} - {title}")
        spotify_id = self.search_spotify_id(artist, title)
        
        if not spotify_id:
            logger.warning(f"Aucun ID Spotify trouvé pour: {artist} - {title}")
            self.cache[cache_key] = {'error': 'spotify_id_not_found', 'timestamp': time.time()}
            self._save_cache()
            return None
        
        # Étape 2: Récupérer les données ReccoBeats
        logger.info(f"Récupération des données ReccoBeats pour ID: {spotify_id}")
        track_data = self.get_track_from_reccobeats(spotify_id)
        
        if not track_data:
            logger.warning(f"Données ReccoBeats non trouvées pour ID: {spotify_id}")
            self.cache[cache_key] = {
                'error': 'reccobeats_not_found',
                'spotify_id': spotify_id,
                'timestamp': time.time()
            }
            self._save_cache()
            return None
        
        # Étape 3: Enrichir avec audio features si possible
        reccobeats_id = track_data.get('id')
        if reccobeats_id:
            logger.debug(f"Récupération des audio features pour ID ReccoBeats: {reccobeats_id}")
            audio_features = self.get_track_audio_features(reccobeats_id)
            if audio_features:
                track_data['audio_features'] = audio_features
                logger.info("Audio features ajoutées aux données")
        
        # Construire la réponse complète
        enriched_data = {
            'search_artist': artist,
            'search_title': title,
            'spotify_id': spotify_id,
            'source': 'reccobeats_integrated',
            'success': True,
            'timestamp': time.time(),
            **track_data
        }
        
        # Extraire les infos importantes si disponibles
        if 'audio_features' in enriched_data:
            features = enriched_data['audio_features']
            enriched_data['bpm'] = features.get('tempo')
            enriched_data['key'] = features.get('key')
            enriched_data['mode'] = features.get('mode')
            enriched_data['energy'] = features.get('energy')
            enriched_data['danceability'] = features.get('danceability')
            enriched_data['valence'] = features.get('valence')
        
        # Sauvegarder en cache
        self.cache[cache_key] = enriched_data
        self._save_cache()
        
        logger.info(f"✅ Données complètes récupérées pour: {artist} - {title}")
        return enriched_data

    def process_multiple_tracks(self, track_list: List[Dict[str, str]]) -> List[Dict]:
        """
        Traite plusieurs tracks en batch
        
        Args:
            track_list: Liste de dicts avec 'artist' et 'title'
            
        Returns:
            Liste des résultats
        """
        results = []
        total = len(track_list)
        
        for i, track in enumerate(track_list, 1):
            artist = track.get('artist', '')
            title = track.get('title', '')
            
            logger.info(f"Traitement {i}/{total}: {artist} - {title}")
            
            result = self.get_track_info(artist, title)
            if result:
                results.append(result)
            
            # Délai entre les requêtes
            if i < total:
                delay = random.uniform(2, 5)
                logger.debug(f"Attente de {delay:.1f}s avant la prochaine requête...")
                time.sleep(delay)
        
        logger.info(f"Traitement terminé: {len(results)}/{total} tracks traités avec succès")
        return results

    # ========== MÉTHODES UTILITAIRES ==========

    def test_connection(self) -> Dict[str, bool]:
        """Teste toutes les connexions"""
        results = {}
        
        # Test extraction ID
        test_url = "https://open.spotify.com/track/4EVMhVr6GslvST0uLx8VIJ"
        test_id = self.extract_spotify_id_from_url(test_url)
        results['spotify_id_extraction'] = test_id == "4EVMhVr6GslvST0uLx8VIJ"
        
        # Test ReccoBeats API
        try:
            test_data = self.get_track_from_reccobeats("4EVMhVr6GslvST0uLx8VIJ")
            results['reccobeats_api'] = test_data is not None
        except:
            results['reccobeats_api'] = False
        
        # Test Selenium
        if self.use_selenium:
            try:
                self._init_selenium_driver()
                results['selenium'] = self.driver is not None
            except:
                results['selenium'] = False
        else:
            results['selenium'] = None
        
        logger.info(f"Tests de connexion: {results}")
        return results

    def clear_cache(self):
        """Vide le cache"""
        self.cache.clear()
        self._save_cache()
        logger.info("Cache vidé")

    def get_cache_stats(self) -> Dict:
        """Statistiques du cache"""
        total = len(self.cache)
        errors = len([v for v in self.cache.values() if isinstance(v, dict) and 'error' in v])
        success = len([v for v in self.cache.values() if isinstance(v, dict) and v.get('success')])
        
        return {
            'total_entries': total,
            'successful_entries': success,
            'error_entries': errors,
            'cache_file': self.cache_file
        }

    def close(self):
        """Ferme toutes les connexions"""
        self._close_selenium_driver()
        self.scraper_session.close()
        self.recco_session.close()
        logger.info("Toutes les connexions fermées")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# ========== EXEMPLE D'UTILISATION ==========

if __name__ == "__main__":
    # Configuration du logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Créer le client (headless=False pour voir le navigateur pendant le debug)
    client = ReccoBeatsIntegratedClient(headless=False)
    
    try:
        # Tester les connexions
        print("\n=== Test des connexions ===")
        test_results = client.test_connection()
        for key, value in test_results.items():
            status = "✅" if value else "❌" if value is False else "⏭️"
            print(f"{status} {key}: {value}")
        
        # Exemples de recherches
        tracks = [
            {"artist": "Drake", "title": "God's Plan"},
            {"artist": "The Weeknd", "title": "Blinding Lights"},
            {"artist": "Dua Lipa", "title": "Levitating"}
        ]
        
        print("\n=== Recherche de tracks ===")
        for track in tracks:
            print(f"\nRecherche: {track['artist']} - {track['title']}")
            
            result = client.get_track_info(track['artist'], track['title'])
            
            if result:
                print(f"✅ Trouvé!")
                print(f"  - Spotify ID: {result.get('spotify_id')}")
                print(f"  - BPM: {result.get('bpm')}")
                print(f"  - Key: {result.get('key')}")
                print(f"  - Mode: {result.get('mode')}")
                print(f"  - Energy: {result.get('energy')}")
            else:
                print(f"❌ Non trouvé")
        
        # Afficher les stats du cache
        print("\n=== Statistiques du cache ===")
        stats = client.get_cache_stats()
        for key, value in stats.items():
            print(f"  {key}: {value}")
        
    finally:
        # Toujours fermer le client pour libérer les ressources
        client.close()