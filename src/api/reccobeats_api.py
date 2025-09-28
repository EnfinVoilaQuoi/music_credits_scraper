"""
ReccoBeats API intégré avec scraper Spotify ID + Selenium
Solution complète : Nom artiste + titre → ID Spotify → Features ReccoBeats
Version avec navigateur visible pour debug
"""
import requests
import json
import time
import logging
import re
import urllib.parse
import random
from typing import Dict, List, Optional
from bs4 import BeautifulSoup

# Imports Selenium
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, WebDriverException

logger = logging.getLogger('ReccoBeatsIntegrated')

class ReccoBeatsIntegratedClient:
    """Client ReccoBeats avec scraper Spotify ID intégré + Selenium"""
    
    def __init__(self, cache_file: str = "reccobeats_integrated_cache.json"):
        self.cache_file = cache_file
        self.cache = self._load_cache()
        
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
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })
        
        # Configuration Selenium
        self.driver = None
        self.use_selenium = True  # Activer/désactiver Selenium
        
        # Patterns pour extraire les IDs Spotify
        self.spotify_id_patterns = [
            r'open\.spotify\.com/(?:intl-[a-z]{2}/)?track/([a-zA-Z0-9]{22})',
            r'spotify:track:([a-zA-Z0-9]{22})',
        ]
        
        logger.info("ReccoBeats client intégré initialisé avec Selenium")

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
        """Extrait l'ID Spotify depuis une URL"""
        for pattern in self.spotify_id_patterns:
            match = re.search(pattern, url)
            if match:
                spotify_id = match.group(1)
                if len(spotify_id) == 22 and spotify_id.replace('_', '').replace('-', '').isalnum():
                    return spotify_id
        return None

    # ========== MÉTHODES SELENIUM ==========

    def _init_selenium_driver(self):
        """Initialise le driver Selenium avec navigateur visible"""
        if self.driver:
            return  # Déjà initialisé
        
        print("🌐 Initialisation du navigateur Selenium...")
        
        try:
            # Configuration Chrome
            chrome_options = Options()
            
            # MODE VISIBLE (pour voir ce qui se passe)
            # chrome_options.add_argument("--headless")  # Commenté pour voir le navigateur
            
            # Options pour éviter la détection
            chrome_options.add_argument("--disable-blink-features=AutomationControlled")
            chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
            chrome_options.add_experimental_option('useAutomationExtension', False)
            chrome_options.add_argument("--disable-extensions")
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            
            # User-Agent réaliste
            chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            
            self.driver = webdriver.Chrome(options=chrome_options)
            
            # Script pour masquer les signes d'automation
            self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            print("✅ Navigateur Chrome initialisé (mode visible)")
            
        except Exception as e:
            print(f"❌ Erreur initialisation Selenium: {e}")
            print("💡 Assurez-vous d'avoir Chrome et ChromeDriver installés")
            self.driver = None

    def _close_selenium_driver(self):
        """Ferme le driver Selenium"""
        if self.driver:
            self.driver.quit()
            self.driver = None
            print("🔒 Navigateur fermé")

    def _search_google_selenium(self, artist: str, title: str) -> Optional[str]:
        """Recherche Google avec Selenium (navigateur visible)"""
        print(f"\n🔍 === RECHERCHE GOOGLE SELENIUM ===")
        print(f"Artiste: {artist}")
        print(f"Titre: {title}")
        
        try:
            if not self.driver:
                self._init_selenium_driver()
            
            if not self.driver:
                print("❌ Driver Selenium non disponible")
                return None
            
            # Construire la requête
            query = f'"{artist}" "{title}" site:open.spotify.com'
            google_url = f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}"
            
            print(f"📝 Requête: {query}")
            print(f"🔗 URL: {google_url}")
            print(f"🌐 Ouverture dans le navigateur...")
            
            # Naviguer vers Google
            self.driver.get(google_url)
            
            # Attendre que la page se charge
            print(f"⏳ Attente du chargement...")
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            
            # Vérifier le titre de la page
            page_title = self.driver.title
            print(f"📄 Titre de la page: {page_title}")
            
            # Vérifier s'il y a un CAPTCHA ou blocage
            page_source = self.driver.page_source.lower()
            blocking_signs = ['captcha', 'unusual traffic', 'verify you are human', 'blocked']
            detected_blocks = [sign for sign in blocking_signs if sign in page_source]
            
            if detected_blocks:
                print(f"🚫 Signes de blocage détectés: {detected_blocks}")
                print(f"👀 REGARDEZ LE NAVIGATEUR - Il pourrait y avoir un CAPTCHA à résoudre")
                
                # Demander à l'utilisateur de résoudre manuellement
                input("🔧 Résolvez manuellement le problème dans le navigateur puis appuyez sur Entrée...")
            
            # Chercher les liens Spotify
            print(f"🔍 Recherche des liens Spotify...")
            
            # Méthode 1: Par sélecteur CSS
            spotify_links = []
            try:
                link_elements = self.driver.find_elements(By.CSS_SELECTOR, "a[href*='open.spotify.com']")
                spotify_links = [elem.get_attribute('href') for elem in link_elements if elem.get_attribute('href')]
                print(f"🎵 Liens Spotify trouvés (CSS): {len(spotify_links)}")
            except Exception as e:
                print(f"⚠️ Erreur recherche CSS: {e}")
            
            # Méthode 2: Par XPath si CSS échoue
            if not spotify_links:
                try:
                    link_elements = self.driver.find_elements(By.XPATH, "//a[contains(@href, 'open.spotify.com')]")
                    spotify_links = [elem.get_attribute('href') for elem in link_elements if elem.get_attribute('href')]
                    print(f"🎵 Liens Spotify trouvés (XPath): {len(spotify_links)}")
                except Exception as e:
                    print(f"⚠️ Erreur recherche XPath: {e}")
            
            # Afficher et traiter les liens trouvés
            if spotify_links:
                print(f"📋 Liens Spotify détectés:")
                for i, link in enumerate(spotify_links[:5]):
                    print(f"   {i+1}. {link}")
                    
                    # Extraire l'ID
                    spotify_id = self.extract_spotify_id_from_url(link)
                    if spotify_id:
                        print(f"      ✅ ID extrait: {spotify_id}")
                        print(f"🎯 SUCCÈS GOOGLE SELENIUM!")
                        return spotify_id
                    else:
                        print(f"      ❌ Pas d'ID extractible")
                
                print(f"😞 Aucun ID valide dans les liens trouvés")
            else:
                print(f"😞 Aucun lien Spotify trouvé")
                print(f"👀 REGARDEZ LE NAVIGATEUR - Y a-t-il des résultats visibles ?")
                
                # Option : laisser l'utilisateur copier manuellement
                manual_url = input("🔧 Si vous voyez un lien Spotify, copiez-le ici (ou Entrée pour continuer): ").strip()
                if manual_url and 'open.spotify.com' in manual_url:
                    spotify_id = self.extract_spotify_id_from_url(manual_url)
                    if spotify_id:
                        print(f"✅ ID extrait manuellement: {spotify_id}")
                        return spotify_id
            
        except TimeoutException:
            print(f"⏰ Timeout Selenium")
        except WebDriverException as e:
            print(f"🌐 Erreur WebDriver: {e}")
        except Exception as e:
            print(f"💥 Erreur inattendue Selenium: {e}")
        
        print(f"❌ ÉCHEC GOOGLE SELENIUM")
        return None

    # ========== MÉTHODES SCRAPING CLASSIQUE ==========

    def _search_google(self, artist: str, title: str) -> Optional[str]:
        """Recherche sur Google avec logs visuels détaillés"""
        print(f"\n🔍 === RECHERCHE GOOGLE CLASSIQUE ===")
        print(f"Artiste: {artist}")
        print(f"Titre: {title}")
        
        try:
            # Délai aléatoire
            delay = random.uniform(3, 7)
            print(f"⏱️  Délai anti-détection: {delay:.1f}s")
            time.sleep(delay)
            
            # User-Agent aléatoire
            user_agents = [
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/120.0.0.0 Safari/537.36'
            ]
            selected_ua = random.choice(user_agents)
            self.scraper_session.headers['User-Agent'] = selected_ua
            print(f"🤖 User-Agent: {selected_ua[:60]}...")
            
            # Construction de la requête
            query = f'"{artist}" "{title}" site:open.spotify.com'
            encoded_query = urllib.parse.quote_plus(query)
            google_url = f"https://www.google.com/search?q={encoded_query}"
            
            print(f"📝 Requête: {query}")
            print(f"🔗 URL: {google_url}")
            print(f"📡 Envoi de la requête...")
            
            response = self.scraper_session.get(google_url, timeout=15)
            
            print(f"📊 Status HTTP: {response.status_code}")
            print(f"📏 Taille réponse: {len(response.text)} caractères")
            
            if response.status_code == 200:
                # Analyser le contenu HTML
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Vérifier s'il y a des signes de blocage
                page_text = response.text.lower()
                blocking_signs = [
                    'unusual traffic', 'captcha', 'blocked', 'robot', 'automation',
                    'verify you are human', 'suspicious activity', 'temporary error'
                ]
                
                detected_blocks = [sign for sign in blocking_signs if sign in page_text]
                if detected_blocks:
                    print(f"🚫 Signes de blocage détectés: {detected_blocks}")
                
                # Chercher les liens
                all_links = soup.find_all('a', href=True)
                print(f"🔗 Total liens trouvés: {len(all_links)}")
                
                spotify_links = []
                
                for link in all_links:
                    href = link.get('href', '')
                    
                    if 'open.spotify.com' in href:
                        spotify_links.append(href)
                
                print(f"🎵 Liens Spotify trouvés: {len(spotify_links)}")
                
                if spotify_links:
                    print(f"📋 Liens Spotify détectés:")
                    for i, link in enumerate(spotify_links[:5]):  # Afficher max 5
                        print(f"   {i+1}. {link}")
                        
                        # Nettoyer l'URL Google
                        if link.startswith('/url?q='):
                            actual_url = urllib.parse.unquote(link.split('/url?q=')[1].split('&')[0])
                            print(f"      → Nettoyée: {actual_url}")
                        else:
                            actual_url = link
                        
                        # Tenter d'extraire l'ID
                        spotify_id = self.extract_spotify_id_from_url(actual_url)
                        if spotify_id:
                            print(f"      ✅ ID extrait: {spotify_id}")
                            print(f"🎯 SUCCÈS GOOGLE!")
                            return spotify_id
                        else:
                            print(f"      ❌ Pas d'ID extractible")
                    
                    print(f"😞 Aucun ID valide trouvé dans les liens Spotify")
                else:
                    print(f"😞 Aucun lien Spotify trouvé")
                    
            elif response.status_code == 429:
                print(f"🚫 Rate limit Google (429)")
            else:
                print(f"❌ Erreur HTTP {response.status_code}")
                
        except requests.exceptions.Timeout:
            print(f"⏰ Timeout Google")
        except requests.exceptions.RequestException as e:
            print(f"🌐 Erreur réseau Google: {e}")
        except Exception as e:
            print(f"💥 Erreur inattendue Google: {e}")
        
        print(f"❌ ÉCHEC GOOGLE")
        return None

    def _search_duckduckgo(self, artist: str, title: str) -> Optional[str]:
        """Recherche sur DuckDuckGo avec logs visuels détaillés"""
        print(f"\n🦆 === RECHERCHE DUCKDUCKGO ===")
        print(f"Artiste: {artist}")
        print(f"Titre: {title}")
        
        try:
            # Délai plus court pour DDG
            delay = random.uniform(2, 4)
            print(f"⏱️  Délai: {delay:.1f}s")
            time.sleep(delay)
            
            # User-Agent
            user_agents = [
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            ]
            selected_ua = random.choice(user_agents)
            self.scraper_session.headers['User-Agent'] = selected_ua
            print(f"🤖 User-Agent: {selected_ua[:60]}...")
            
            query = f'"{artist}" "{title}" site:open.spotify.com'
            encoded_query = urllib.parse.quote_plus(query)
            ddg_url = f"https://html.duckduckgo.com/html/?q={encoded_query}"
            
            print(f"📝 Requête: {query}")
            print(f"🔗 URL: {ddg_url}")
            print(f"📡 Envoi de la requête DDG...")
            
            response = self.scraper_session.get(ddg_url, timeout=15)
            
            print(f"📊 Status HTTP: {response.status_code}")
            print(f"📏 Taille réponse: {len(response.text)} caractères")
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Analyser les liens DDG
                all_links = soup.find_all('a', href=True)
                print(f"🔗 Total liens DDG: {len(all_links)}")
                
                spotify_links = []
                for link in all_links:
                    href = link.get('href', '')
                    if 'open.spotify.com' in href:
                        spotify_links.append(href)
                
                print(f"🎵 Liens Spotify DDG: {len(spotify_links)}")
                
                if spotify_links:
                    print(f"📋 Liens Spotify DDG:")
                    for i, link in enumerate(spotify_links[:3]):
                        print(f"   {i+1}. {link}")
                        
                        spotify_id = self.extract_spotify_id_from_url(link)
                        if spotify_id:
                            print(f"      ✅ ID extrait: {spotify_id}")
                            print(f"🎯 SUCCÈS DUCKDUCKGO!")
                            return spotify_id
                        else:
                            print(f"      ❌ Pas d'ID extractible")
                else:
                    print(f"😞 Aucun lien Spotify DDG")
                    
            elif response.status_code == 202:
                print(f"⏳ DDG Status 202 (traitement en cours)")
            else:
                print(f"❌ Erreur DDG {response.status_code}")
                
        except Exception as e:
            print(f"💥 Erreur DDG: {e}")
        
        print(f"❌ ÉCHEC DUCKDUCKGO")
        return None

    # ========== MÉTHODE FALLBACK MANUEL ==========

    def get_spotify_id_manual(self, artist: str, title: str) -> Optional[str]:
        """Demande l'ID Spotify manuellement à l'utilisateur"""
        print(f"\n🔍 Recherche manuelle nécessaire:")
        print(f"   Artiste: {artist}")
        print(f"   Titre: {title}")
        
        search_url = f"https://open.spotify.com/search/{urllib.parse.quote(f'{artist} {title}')}"
        print(f"📱 Ouvrez: {search_url}")
        print(f"💡 Cliquez sur le bon morceau et copiez l'URL complète")
        
        spotify_url = input("Collez l'URL Spotify (ou Entrée pour passer): ").strip()
        
        if spotify_url and 'open.spotify.com' in spotify_url:
            extracted_id = self.extract_spotify_id_from_url(spotify_url)
            if extracted_id:
                print(f"✅ ID extrait: {extracted_id}")
                return extracted_id
            else:
                print("❌ Impossible d'extraire l'ID de cette URL")
        
        return None

    # ========== MÉTHODE PRINCIPALE DE RECHERCHE ==========

    def search_spotify_id_via_web(self, artist: str, title: str, allow_manual: bool = True) -> Optional[str]:
        """Recherche avec Selenium en priorité puis fallback classique"""
        print(f"\n" + "="*60)
        print(f"🚀 DÉBUT RECHERCHE SPOTIFY ID")
        print(f"🎤 Artiste: {artist}")
        print(f"🎵 Titre: {title}")
        print(f"="*60)
        
        if self.use_selenium:
            print(f"\n🚀 RECHERCHE AVEC SELENIUM (NAVIGATEUR VISIBLE)")
            
            # Tentative Selenium Google
            spotify_id = self._search_google_selenium(artist, title)
            
            if spotify_id:
                self._close_selenium_driver()  # Fermer le navigateur après succès
                print(f"\n🎉 SUCCÈS SELENIUM! ID: {spotify_id}")
                print(f"="*60)
                return spotify_id
            
            # Si Selenium échoue, proposer de continuer ou passer aux méthodes classiques
            print(f"\n🤔 Selenium a échoué. Options:")
            print(f"1. Essayer les méthodes classiques (requests)")
            print(f"2. Recherche manuelle")
            print(f"3. Passer ce morceau")
            
            choice = input("Choix (1/2/3): ").strip()
            
            if choice == "1":
                print(f"🔄 Basculement vers méthodes classiques...")
                self._close_selenium_driver()
                # Continuer avec les méthodes classiques ci-dessous
            elif choice == "2":
                if allow_manual:
                    spotify_id = self.get_spotify_id_manual(artist, title)
                    self._close_selenium_driver()
                    if spotify_id:
                        print(f"\n🎉 SUCCÈS MANUEL! ID: {spotify_id}")
                    print(f"="*60)
                    return spotify_id
            else:
                self._close_selenium_driver()
                print(f"\n💔 ABANDONNÉ par l'utilisateur")
                print(f"="*60)
                return None
        
        # Méthodes classiques (requests) si Selenium désactivé ou a échoué
        print(f"\n🔄 Recherche classique (requests)...")
        
        # Tentative Google classique
        spotify_id = self._search_google(artist, title)
        
        # Tentative DuckDuckGo si Google échoue
        if not spotify_id:
            spotify_id = self._search_duckduckgo(artist, title)
        
        # Fallback manuel si tout échoue
        if not spotify_id and allow_manual:
            print(f"\n" + "⚠️ "*20)
            print(f"💥 TOUTES LES RECHERCHES AUTOMATIQUES ONT ÉCHOUÉ")
            print(f"⚠️ "*20)
            
            response = input(f"\nRecherche manuelle pour '{artist} - {title}' ? (o/n): ").strip().lower()
            
            if response in ['o', 'oui', 'y', 'yes']:
                spotify_id = self.get_spotify_id_manual(artist, title)
        
        # Résultat final
        if spotify_id:
            print(f"\n🎉 SUCCÈS! ID trouvé: {spotify_id}")
        else:
            print(f"\n💔 ÉCHEC TOTAL pour '{artist} - {title}'")
        
        print(f"="*60)
        return spotify_id

    # ========== MÉTHODES RECCOBEATS API ==========

    def get_track_from_reccobeats(self, spotify_id: str) -> Optional[Dict]:
        """Récupère les données d'un track depuis ReccoBeats via son ID Spotify"""
        try:
            url = f"{self.recco_base_url}/track"
            params = {'ids': spotify_id}
            
            response = self.recco_session.get(url, params=params, timeout=15)
            logger.debug(f"ReccoBeats response status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                logger.debug(f"ReccoBeats response: {json.dumps(data, indent=2)[:200]}...")
                
                # Traiter la réponse ReccoBeats avec structure {"content": [...]}
                if isinstance(data, dict) and 'content' in data:
                    # Structure : {"content": [track_data]}
                    content = data['content']
                    if isinstance(content, list) and len(content) > 0:
                        track_data = content[0]
                    else:
                        logger.warning("Contenu vide dans la réponse ReccoBeats")
                        return None
                elif isinstance(data, list) and len(data) > 0:
                    track_data = data[0]
                elif isinstance(data, dict):
                    track_data = data
                else:
                    logger.warning("Format de réponse ReccoBeats inattendu")
                    return None
                
                logger.debug("✅ Données track récupérées depuis ReccoBeats")
                return track_data
                
            elif response.status_code == 404:
                logger.warning(f"Track non trouvé dans ReccoBeats: {spotify_id}")
            else:
                logger.error(f"Erreur ReccoBeats: {response.status_code} - {response.text}")
                
        except Exception as e:
            logger.error(f"Erreur récupération ReccoBeats: {e}")
        
        return None

    def get_track_audio_features(self, reccobeats_id: str) -> Optional[Dict]:
        """Récupère les audio features d'un track via son ID ReccoBeats"""
        try:
            url = f"{self.recco_base_url}/track/{reccobeats_id}/audio-features"
            
            response = self.recco_session.get(url, timeout=15)
            
            if response.status_code == 200:
                features = response.json()
                logger.debug("✅ Audio features récupérées")
                return features
            elif response.status_code == 404:
                logger.warning(f"Audio features non trouvées pour: {reccobeats_id}")
            else:
                logger.error(f"Erreur audio features: {response.status_code}")
                
        except Exception as e:
            logger.error(f"Erreur récupération audio features: {e}")
        
        return None

    def search_track_complete(self, artist: str, title: str) -> Optional[Dict]:
        """
        Recherche complète : artiste + titre → ID Spotify → données ReccoBeats
        """
        logger.info(f"Recherche complète: '{artist}' - '{title}'")
        
        # Vérifier le cache
        cache_key = self._get_cache_key(artist, title)
        if cache_key in self.cache:
            cached_data = self.cache[cache_key]
            if 'error' not in cached_data:
                logger.debug("Données trouvées en cache")
                return cached_data
        
        # Étape 1: Rechercher l'ID Spotify
        logger.debug("🔍 Recherche ID Spotify...")
        spotify_id = self.search_spotify_id_via_web(artist, title)
        
        if not spotify_id:
            logger.warning("❌ Aucun ID Spotify trouvé")
            self.cache[cache_key] = {'error': 'spotify_id_not_found'}
            self._save_cache()
            return None
        
        logger.info(f"✅ ID Spotify trouvé: {spotify_id}")
        
        # Étape 2: Récupérer les données ReccoBeats
        logger.debug("🔍 Récupération données ReccoBeats...")
        track_data = self.get_track_from_reccobeats(spotify_id)
        
        if not track_data:
            logger.warning("❌ Données track non trouvées dans ReccoBeats")
            self.cache[cache_key] = {'error': 'reccobeats_not_found', 'spotify_id': spotify_id}
            self._save_cache()
            return None
        
        # Étape 3: Enrichir avec audio features si possible
        reccobeats_id = track_data.get('id')
        if reccobeats_id:
            logger.debug("🔍 Récupération audio features...")
            audio_features = self.get_track_audio_features(reccobeats_id)
            if audio_features:
                track_data['audio_features'] = audio_features
                logger.debug("✅ Audio features ajoutées")
        
        # Enrichir les métadonnées
        enriched_data = {
            'search_artist': artist,
            'search_title': title,
            'spotify_id': spotify_id,
            'source': 'reccobeats_integrated',
            'success': True,
            **track_data
        }
        
        # Extraire le BPM si disponible
        if 'audio_features' in enriched_data:
            features = enriched_data['audio_features']
            if 'tempo' in features:
                enriched_data['bpm'] = int(round(features['tempo']))
        
        # Mettre en cache
        self.cache[cache_key] = enriched_data
        self._save_cache()
        
        logger.info(f"✅ Succès complet pour '{title}'")
        return enriched_data

    def fetch_discography(self, artist: str, track_titles: List[str]) -> List[Dict]:
        """
        Récupère les données pour une discographie complète
        """
        logger.info(f"=== FETCH DISCOGRAPHY INTÉGRÉ ===")
        logger.info(f"Artiste: '{artist}'")
        logger.info(f"Titres: {len(track_titles)} morceaux")
        
        results = []
        
        for i, title in enumerate(track_titles):
            logger.debug(f"Traitement {i+1}/{len(track_titles)}: '{title}'")
            
            track_data = self.search_track_complete(artist, title)
            
            if track_data and track_data.get('success'):
                # Succès
                result = {
                    'artist': artist,
                    'title': title,
                    **track_data
                }
            else:
                # Échec
                result = {
                    'artist': artist,
                    'title': title,
                    'success': False,
                    'error': track_data.get('error', 'unknown_error') if track_data else 'search_failed',
                    'source': 'reccobeats_integrated'
                }
            
            results.append(result)
            
            # Rate limiting respectueux (important pour le scraping)
            if i < len(track_titles) - 1:
                time.sleep(4)  # 4 secondes entre chaque track
        
        success_count = len([r for r in results if r.get('success')])
        logger.info(f"=== RÉSULTATS FINAUX ===")
        logger.info(f"Succès: {success_count}/{len(track_titles)}")
        
        return results

    # ========== MÉTHODES UTILITAIRES ==========

    def test_connection(self) -> Dict[str, bool]:
        """Teste les connexions"""
        results = {}
        
        # Test scraper avec un exemple connu
        test_id = self.extract_spotify_id_from_url("https://open.spotify.com/track/4EVMhVr6GslvST0uLx8VIJ")
        results['spotify_id_extraction'] = test_id == "4EVMhVr6GslvST0uLx8VIJ"
        
        # Test ReccoBeats avec un ID Spotify connu
        try:
            test_data = self.get_track_from_reccobeats("4EVMhVr6GslvST0uLx8VIJ")  # "Shape of You"
            results['reccobeats_api'] = test_data is not None
        except:
            results['reccobeats_api'] = False
        
        # Test Selenium
        try:
            self._init_selenium_driver()
            results['selenium'] = self.driver is not None
            self._close_selenium_driver()
        except:
            results['selenium'] = False
        
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
        success = total - errors
        
        return {
            'total_entries': total,
            'successful_entries': success,
            'error_entries': errors,
            'cache_file': self.cache_file
        }

    def close(self):
        """Ferme toutes les connexions"""
        self._close_selenium_driver()
        logger.info("Connexions fermées")
        self.scraper_session.close()