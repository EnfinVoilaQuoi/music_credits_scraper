"""
ReccoBeats API intégré avec scraper Spotify ID amélioré
Version robuste avec Selenium et webdriver_manager - ÉDITION COMPLÈTE CORRIGÉE
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
            options.add_argument('--disable-logging')
            options.add_argument('--disable-webgl')
            options.add_argument('--disable-webgl2')
            options.add_argument('--disable-3d-apis')
            options.add_argument('--disable-software-rasterizer')
            options.add_argument('--disable-gpu-sandbox')

            # Service avec log_path vers device null
            import os
            service = ChromeService(
                ChromeDriverManager().install(),
                log_path=os.devnull  # Utilise le device null du système (NUL sur Windows, /dev/null sur Linux)
            )

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
        """Gère les popups de cookies - VERSION SANS FAUX POSITIFS"""
        try:
            logger.debug("Vérification des popups de cookies...")
            
            # Attendre un peu que la page se charge
            time.sleep(2)
            
            # Détecter le site actuel
            current_url = self.driver.current_url.lower()
            is_google = 'google.' in current_url
            is_spotify = 'spotify.' in current_url
            
            # Sélecteurs spécifiques par site
            cookie_selectors = []
            
            if is_google:
                # GOOGLE - Sélecteurs spécifiques
                cookie_selectors.extend([
                    "//div[contains(@class, 'QS5gu') and contains(text(), 'Tout accepter')]",
                    "//div[contains(text(), 'Tout accepter')]",
                    "//div[contains(text(), 'Accept all')]",
                    "//div[contains(text(), 'J'accepte')]",
                    "div.QS5gu.sy4vM",
                    "div[class*='QS5gu']",
                    "div[role='button'][aria-label*='Accept']",
                    "div[role='button'][aria-label*='Accepter']",
                    "button[aria-label*='Accept all']",
                    "button[aria-label*='Tout accepter']",
                    "#L2AGLb",
                    ".sy4vM",
                ])
                
            elif is_spotify:
                # SPOTIFY - Sélecteurs spécifiques 
                cookie_selectors.extend([
                    "button[data-testid='accept-all-cookies']",
                    "button[id='onetrust-accept-btn-handler']",
                    "button[class*='accept-all']",
                    "#onetrust-accept-btn-handler",
                    "button.onetrust-close-btn-handler",
                    "button.ot-sdk-btn-primary",
                ])
            
            # Sélecteurs génériques (fallback pour tous les sites)
            cookie_selectors.extend([
                "button[id*='accept']",
                "button[class*='accept']",
                "button[class*='consent']",
                "button[class*='agree']",
                "//button[contains(text(), 'Accept all')]",
                "//button[contains(text(), 'Accepter tout')]",
                "//button[contains(text(), 'Tout accepter')]",
                "//div[contains(text(), 'Accept all')]",
                "//div[contains(text(), 'Accepter tout')]", 
                "//div[contains(text(), 'Tout accepter')]",
            ])
        
            cookies_handled = False
            
            # Essayer chaque sélecteur
            for i, selector in enumerate(cookie_selectors):
                try:
                    if selector.startswith("//"):
                        elements = self.driver.find_elements(By.XPATH, selector)
                    else:
                        elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    
                    for element in elements:
                        if element.is_displayed() and element.is_enabled():
                            try:
                                self.driver.execute_script("arguments[0].scrollIntoView(true);", element)
                                time.sleep(0.5)
                                
                                try:
                                    element.click()
                                except Exception:
                                    self.driver.execute_script("arguments[0].click();", element)
                                
                                logger.info(f"✅ Cookies acceptés avec: {selector}")
                                logger.info(f"   Element: {element.tag_name} - Text: '{element.text[:50]}'")
                                cookies_handled = True
                                time.sleep(3)
                                break
                                
                            except Exception as click_error:
                                logger.debug(f"Erreur clic cookie: {click_error}")
                                continue
                    
                    if cookies_handled:
                        break
                        
                except Exception as selector_error:
                    logger.debug(f"Sélecteur {selector} non trouvé: {selector_error}")
                    continue
            
            if not cookies_handled:
                logger.debug("Aucun popup de cookies détecté")
        
            # NOUVELLE LOGIQUE: Vérification plus intelligente des cookies
            time.sleep(2)
            page_source = self.driver.page_source.lower()
            current_url = self.driver.current_url.lower()
            
            # Pour Spotify: vérifier si on est sur une vraie page de résultats
            if is_spotify:
                # Si on est sur /search/ avec des résultats, les cookies sont OK
                if '/search/' in current_url and any(keyword in page_source for keyword in [
                    'data-testid', 'tracklist', 'search-results', 'track-list'
                ]):
                    logger.debug("✅ Spotify: Page de résultats détectée, cookies OK")
                    return
            
            # Vérification générale: chercher des indicateurs de popup actif
            cookie_popup_indicators = [
                'cookie consent',
                'accept cookies',
                'cookie policy',
                'privacy settings',
                'data-testid="accept',
                'onetrust-banner'
            ]
            
            active_popup = False
            for indicator in cookie_popup_indicators:
                if indicator in page_source:
                    active_popup = True
                    break
            
            if active_popup:
                logger.warning("⚠️ Popup de cookies potentiellement encore actif")
                if not self.headless:
                    logger.info("Mode debug: Vérifiez manuellement si nécessaire")
                    time.sleep(3)
            else:
                logger.debug("✅ Aucun popup de cookies actif détecté")
            
        except Exception as e:
            logger.debug(f"Erreur gestion cookies: {e}")

    def _debug_cookie_elements(self):
        """Méthode de debug pour identifier les éléments de cookies"""
        try:
            logger.info("🔍 DEBUG: Recherche d'éléments de cookies...")
            
            # Chercher tous les éléments contenant "accept", "consent", etc.
            debug_selectors = [
                "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'accept')]",
                "//div[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'accept')]",
                "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'tout')]",
                "//div[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'tout')]",
                "*[class*='accept']",
                "*[class*='consent']",
                "*[class*='cookie']",
            ]
            
            found_elements = []
            for selector in debug_selectors:
                try:
                    if selector.startswith("//"):
                        elements = self.driver.find_elements(By.XPATH, selector)
                    else:
                        elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    
                    for elem in elements[:3]:  # Limiter à 3 par sélecteur
                        if elem.is_displayed():
                            found_elements.append({
                                'tag': elem.tag_name,
                                'text': elem.text[:100],
                                'class': elem.get_attribute('class'),
                                'id': elem.get_attribute('id'),
                                'selector_used': selector
                            })
                except:
                    continue
        
            if found_elements:
                logger.info(f"🔍 Éléments trouvés ({len(found_elements)}):")
                for i, elem in enumerate(found_elements[:5]):  # Afficher max 5
                    logger.info(f"  {i+1}. {elem['tag']} - '{elem['text']}' - class='{elem['class']}' - id='{elem['id']}'")
            else:
                logger.info("🔍 Aucun élément de cookies trouvé")
                
        except Exception as e:
            logger.error(f"Erreur debug cookies: {e}")

    def _calculate_relevance(self, artist: str, title: str, text: str) -> float:
        """Calcule la pertinence d'un résultat"""
        if not text:
            return 0.5
        
        score = 0.0
        artist_lower = artist.lower()
        title_lower = title.lower()
        text_lower = text.lower()
        
        # Bonus pour correspondance exacte
        if artist_lower in text_lower:
            score += 0.4
        if title_lower in text_lower:
            score += 0.4
        
        # Bonus pour correspondance partielle
        artist_words = artist_lower.split()
        title_words = title_lower.split()
        
        for word in artist_words:
            if word in text_lower:
                score += 0.1
        
        for word in title_words:
            if word in text_lower:
                score += 0.1
        
        return min(score, 1.0)

    # ========== NOUVELLES MÉTHODES DE RECHERCHE AMÉLIORÉES ==========

    def _search_duckduckgo_selenium(self, artist: str, title: str) -> Optional[str]:
        """Recherche DuckDuckGo avec Selenium - Alternative à Google"""
        logger.info(f"Recherche DuckDuckGo Selenium pour: {artist} - {title}")
        
        try:
            if not self.driver:
                self._init_selenium_driver()
            
            if not self.driver:
                return None
            
            # Construire la requête DuckDuckGo
            query = f'"{artist}" "{title}" site:open.spotify.com'
            ddg_url = f"https://duckduckgo.com/?q={urllib.parse.quote_plus(query)}"
            
            logger.debug(f"Navigation vers DuckDuckGo: {ddg_url}")
            self.driver.get(ddg_url)
            
            # DuckDuckGo a généralement moins de cookies que Google
            time.sleep(3)
            
            # Chercher les résultats avec plusieurs sélecteurs
            result_selectors = [
                "a[href*='open.spotify.com/track/']",
                "a[href*='spotify.com/track/']",
                ".result__url[href*='spotify']",
                ".result a[href*='spotify']"
            ]
            
            for selector in result_selectors:
                try:
                    links = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    
                    for link in links[:5]:  # Vérifier les 5 premiers
                        href = link.get_attribute('href')
                        if href and 'spotify.com' in href:
                            spotify_id = self.extract_spotify_id_from_url(href)
                            if spotify_id:
                                logger.info(f"✅ ID trouvé via DuckDuckGo Selenium: {spotify_id}")
                                return spotify_id
                except Exception as e:
                    logger.debug(f"Erreur sélecteur {selector}: {e}")
                    continue
            
            # Si pas de résultats directs, cliquer sur "More results" si disponible
            try:
                more_button = self.driver.find_element(By.CSS_SELECTOR, ".btn--more")
                if more_button.is_displayed():
                    more_button.click()
                    time.sleep(2)
                    
                    # Re-chercher après avoir chargé plus de résultats
                    for selector in result_selectors:
                        links = self.driver.find_elements(By.CSS_SELECTOR, selector)
                        for link in links[:5]:
                            href = link.get_attribute('href')
                            if href and 'spotify.com' in href:
                                spotify_id = self.extract_spotify_id_from_url(href)
                                if spotify_id:
                                    logger.info(f"✅ ID trouvé via DuckDuckGo (more results): {spotify_id}")
                                    return spotify_id
            except:
                pass
            
            logger.warning("Aucun ID trouvé via DuckDuckGo Selenium")
            return None
            
        except Exception as e:
            logger.error(f"Erreur DuckDuckGo Selenium: {e}")
            return None

    def get_track_info(self, artist: str, title: str, use_cache: bool = True, 
                   force_refresh: bool = False, spotify_id: str = None) -> Optional[Dict]:
        """
        Récupère les infos complètes d'un track depuis ReccoBeats avec vérification spotify_id
        
        Args:
            artist: Nom de l'artiste
            title: Titre du morceau
            use_cache: Utiliser le cache
            force_refresh: Forcer un rafraîchissement
            spotify_id: ID Spotify fourni (évite le scraping si présent)
            
        Returns:
            Dictionnaire avec les données du track ou None
        """
        import threading  # OK - import dans la fonction
        
        logger.info(f"🎵 get_track_info: {artist} - {title}")
        
        try:
            cache_key = self._get_cache_key(artist, title)
            
            # Force refresh = nettoyer le cache
            if force_refresh and cache_key in self.cache:
                del self.cache[cache_key]
                logger.info(f"Force refresh pour: {artist} - {title}")
            
            # Vérifier le cache
            if use_cache and not force_refresh and cache_key in self.cache:
                cached = self.cache[cache_key]
                if isinstance(cached, dict):
                    # Vérifier que le cache contient des données COMPLÈTES
                    has_spotify_id = cached.get('spotify_id') is not None
                    has_bpm = cached.get('bpm') is not None or cached.get('tempo') is not None
                    has_audio_features = cached.get('audio_features') is not None
                    
                    # Cache complet = spotify_id + BPM
                    cache_is_complete = has_spotify_id and (has_bpm or has_audio_features)
                    
                    if cache_is_complete:
                        logger.info(f"✅ Données COMPLÈTES trouvées dans le cache pour: {artist} - {title}")
                        return cached
                    elif has_spotify_id:
                        logger.info(f"⚠️ Cache INCOMPLET pour {artist} - {title} (pas de BPM)")
                        logger.info(f"🔄 Nouvelle tentative pour récupérer le BPM...")
                        # Ne pas retourner, continuer pour refaire l'appel API
                    elif 'timestamp' in cached:
                        age_hours = (time.time() - cached['timestamp']) / 3600
                        if age_hours > 24:
                            del self.cache[cache_key]
                            logger.info(f"Cache d'erreur expiré, nouvelle tentative")
                    elif 'timestamp' in cached:
                        age_hours = (time.time() - cached['timestamp']) / 3600
                        if age_hours > 24:
                            del self.cache[cache_key]
                            logger.info(f"Cache d'erreur expiré, nouvelle tentative")
            
            # =====================================================
            # CORRECTION 1 : Vérifier si spotify_id est déjà fourni
            # =====================================================
            if not spotify_id:
                # Étape 1: Rechercher l'ID Spotify seulement s'il n'est pas fourni
                logger.info(f"🔍 Recherche ID Spotify pour: {artist} - {title}")
                spotify_id = self.search_spotify_id(artist, title)
                
                # Fermer Selenium immédiatement après récupération ID
                if self.driver:
                    logger.info("🔧 Fermeture Selenium après récupération ID")
                    self._close_selenium_driver()
            else:
                logger.info(f"✅ ID Spotify fourni: {spotify_id} (pas de scraping nécessaire)")
            
            if not spotify_id:
                logger.warning(f"❌ Aucun ID Spotify trouvé pour: {artist} - {title}")
                self.cache[cache_key] = {'error': 'spotify_id_not_found', 'timestamp': time.time()}
                self._save_cache()
                return None
            
            logger.info(f"✅ ID Spotify: {spotify_id}")
            
            # Réponse minimale garantie
            minimal_response = {
                'search_artist': artist,
                'search_title': title,
                'spotify_id': spotify_id,
                'source': 'reccobeats_integrated',
                'success': True,
                'timestamp': time.time(),
            }
            
            # =====================================================
            # CORRECTION 2 : Éviter le conflit de variable 'time'
            # =====================================================
            # Ne JAMAIS créer de variable locale nommée 'time' !
            # Utiliser 'start_time', 'current_time', etc.
            
            # Étape 2: ReccoBeats avec timeout via threading
            logger.info(f"🎵 Récupération ReccoBeats pour ID: {spotify_id}")
            
            track_data = None
            api_error = None
            
            def recco_thread():
                nonlocal track_data, api_error
                try:
                    track_data = self.get_track_from_reccobeats(spotify_id)
                except Exception as e:
                    api_error = e
            
            # Lancer ReccoBeats dans un thread avec timeout
            thread = threading.Thread(target=recco_thread)
            thread.daemon = True
            thread.start()
            thread.join(timeout=30)  # 30 secondes max pour ReccoBeats
            
            if thread.is_alive():
                logger.warning("⏰ Timeout ReccoBeats, abandon de cette requête")
                track_data = None
            elif api_error:
                logger.error(f"❌ Erreur ReccoBeats: {api_error}")
                track_data = None
            
            if track_data:
                logger.info("✅ Données ReccoBeats récupérées avec succès")
                enriched_data = {**minimal_response, **track_data}

                # ⭐ IMPORTANT : Extraire la durée
                if 'durationMs' in track_data:
                    duration_ms = track_data['durationMs']
                    # Convertir millisecondes en secondes
                    enriched_data['duration'] = int(duration_ms / 1000) if duration_ms else None
                    logger.info(f"⏱️ Duration extraite de track_data: {enriched_data['duration']}s ({duration_ms}ms)")
                else:
                    logger.warning(f"⚠️ durationMs absent de track_data. Clés disponibles: {list(track_data.keys())}")
                
                # Audio features (timeout plus court)
                reccobeats_id = track_data.get('id')
                if reccobeats_id:
                    logger.debug(f"🎼 Audio features pour ID: {reccobeats_id}")
                    
                    audio_features = None
                    
                    def audio_thread():
                        nonlocal audio_features
                        try:
                            audio_features = self.get_track_audio_features(reccobeats_id)
                        except:
                            pass
                    
                    audio_t = threading.Thread(target=audio_thread)
                    audio_t.daemon = True
                    audio_t.start()
                    audio_t.join(timeout=10)  # 10 secondes max
                    
                    if audio_features:
                        enriched_data['audio_features'] = audio_features
                
                # Extraire BPM, Key et Mode
                if 'audio_features' in enriched_data:
                    logger.info(f"🔍 DEBUG: audio_features trouvés, extraction en cours...")
                    features = enriched_data['audio_features']
                    logger.info(f"🔍 DEBUG: features keys = {list(features.keys()) if features else None}")
                    logger.info(f"🔍 DEBUG: features tempo={features.get('tempo')}, key={features.get('key')}, mode={features.get('mode')}")
                    
                    enriched_data['bpm'] = features.get('tempo')
                    enriched_data['key'] = features.get('key')
                    enriched_data['mode'] = features.get('mode')
                    enriched_data['energy'] = features.get('energy')
                    enriched_data['danceability'] = features.get('danceability')
                    enriched_data['valence'] = features.get('valence')

                    if enriched_data.get('key') is not None and enriched_data.get('mode') is not None:
                        try:
                            from src.utils.music_theory import key_mode_to_french
                            enriched_data['musical_key'] = key_mode_to_french(
                                enriched_data['key'],
                                enriched_data['mode']
                            )
                            logger.info(f"✅ Musical key convertie: {enriched_data['musical_key']}")
                        except Exception as e:
                            logger.warning(f"⚠️ Erreur conversion musical_key: {e}")
                    
                    logger.info(f"🔍 DEBUG: extraction terminée, enriched_data keys = {list(enriched_data.keys())}")
                    logger.info(f"🔍 DEBUG: enriched_data bpm={enriched_data.get('bpm')}, key={enriched_data.get('key')}, mode={enriched_data.get('mode')}")
                
                self.cache[cache_key] = enriched_data
                self._save_cache()
                
                logger.info(f"✅ FIN SUCCÈS COMPLET: {artist} - {title}")
                return enriched_data
                
            else:
                # Succès partiel avec ID seulement
                logger.warning(f"⚠️ ID Spotify trouvé mais ReccoBeats échoué pour: {spotify_id}")
                minimal_response['warning'] = 'reccobeats_not_found'
                minimal_response['message'] = 'ID Spotify récupéré mais données ReccoBeats indisponibles'
                
                self.cache[cache_key] = minimal_response
                self._save_cache()
                
                logger.info(f"⚠️ FIN SUCCÈS PARTIEL: {artist} - {title}")
                return minimal_response
        
        except Exception as e:
            logger.error(f"❌ Erreur générale get_track_info: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
        
        finally:
            # S'assurer que Selenium est fermé
            if self.driver:
                logger.info("🔧 Fermeture Selenium en finally")
                self._close_selenium_driver()
            
            logger.info(f"🏁 FIN get_track_info: {artist} - {title}")

    def test_quick_enrichment(self, artist: str, title: str) -> Dict:
        """Test rapide d'enrichissement avec timeout strict"""
        logger.info(f"🧪 TEST RAPIDE: {artist} - {title}")
        
        start_time = time.time()
        result = {
            'artist': artist,
            'title': title,
            'success': False,
            'spotify_id': None,
            'duration_seconds': 0,
            'steps_completed': [],
            'errors': []
        }
        
        try:
            # Étape 1: ID Spotify (max 30s)
            logger.info("1️⃣ Recherche ID Spotify...")
            step_start = time.time()
            
            spotify_id = self.search_spotify_id(artist, title)
            step_duration = time.time() - step_start
            
            if spotify_id:
                result['spotify_id'] = spotify_id
                result['steps_completed'].append(f"spotify_id ({step_duration:.1f}s)")
                logger.info(f"✅ ID trouvé en {step_duration:.1f}s: {spotify_id}")
                
                # Fermer Selenium immédiatement
                if self.driver:
                    self._close_selenium_driver()
                
                # Étape 2: ReccoBeats (max 20s)
                logger.info("2️⃣ Test ReccoBeats...")
                step_start = time.time()
                
                track_data = self.get_track_from_reccobeats(spotify_id)
                step_duration = time.time() - step_start
                
                if track_data:
                    result['steps_completed'].append(f"reccobeats ({step_duration:.1f}s)")
                    result['success'] = True
                    logger.info(f"✅ ReccoBeats OK en {step_duration:.1f}s")
                else:
                    result['steps_completed'].append(f"reccobeats_failed ({step_duration:.1f}s)")
                    logger.warning(f"⚠️ ReccoBeats échoué en {step_duration:.1f}s")
                    # Mais c'est quand même un succès partiel car on a l'ID
                    result['success'] = True
            else:
                result['errors'].append(f"No Spotify ID found ({step_duration:.1f}s)")
                logger.error(f"❌ Pas d'ID Spotify en {step_duration:.1f}s")
        
        except Exception as e:
            result['errors'].append(f"Exception: {e}")
            logger.error(f"❌ Exception: {e}")
        
        finally:
            # Toujours fermer Selenium
            if self.driver:
                self._close_selenium_driver()
            
            result['duration_seconds'] = time.time() - start_time
            
            # Résumé
            status = "✅" if result['success'] else "❌"
            logger.info(f"{status} Test terminé en {result['duration_seconds']:.1f}s")
            logger.info(f"  Étapes: {result['steps_completed']}")
            if result['errors']:
                logger.warning(f"  Erreurs: {result['errors']}")
        
        return result

    def _search_spotify_direct_selenium(self, artist: str, title: str) -> Optional[str]:
        """Recherche directe Spotify avec LOGS DÉTAILLÉS"""
        logger.info(f"🎵 Recherche directe Spotify pour: {artist} - {title}")
        
        try:
            if not self.driver:
                self._init_selenium_driver()
            
            if not self.driver:
                logger.error("❌ Driver Selenium non disponible")
                return None
            
            # Essayer plusieurs variantes de requête
            search_queries = [
                f"{artist} {title}",
                f"{title} {artist}",
                f'"{artist}" "{title}"'
            ]
            
            for query_idx, query in enumerate(search_queries):
                logger.info(f"📝 Essai {query_idx + 1}/3 avec requête: '{query}'")
                
                try:
                    # URL de recherche Spotify
                    spotify_url = f"https://open.spotify.com/search/{urllib.parse.quote(query)}"
                    
                    logger.info(f"🌐 Navigation vers: {spotify_url}")
                    self.driver.get(spotify_url)
                    
                    # Gérer les cookies
                    logger.info("🍪 Gestion des cookies...")
                    self._handle_cookies()
                    
                    # Attendre que la page se charge
                    logger.info("⏳ Attente du chargement de la page...")
                    try:
                        WebDriverWait(self.driver, 20).until(
                            lambda driver: any([
                                driver.find_elements(By.CSS_SELECTOR, "[data-testid*='track']"),
                                driver.find_elements(By.CSS_SELECTOR, "a[href*='/track/']"),
                                driver.find_elements(By.CSS_SELECTOR, ".tracklist"),
                                driver.find_elements(By.CSS_SELECTOR, "[role='row']")
                            ])
                        )
                        logger.info("✅ Page chargée avec succès")
                    except TimeoutException:
                        logger.warning(f"⏰ Timeout pour la requête: {query}")
                        continue
                    
                    # Vérifier l'URL actuelle
                    current_url = self.driver.current_url
                    logger.info(f"📍 URL actuelle: {current_url}")
                    
                    if 'search' not in current_url:
                        logger.warning("🔄 Redirection inattendue de Spotify")
                        continue
                    
                    # Chercher les liens tracks avec logs détaillés
                    track_selectors = [
                        "a[href*='/track/'][data-testid]",
                        "div[data-testid*='track'] a[href*='/track/']",
                        "[role='row'] a[href*='/track/']",
                        "a[href*='/track/']",
                        ".tracklist-row a[href*='/track/']",
                        ".track a[href*='/track/']",
                    ]
                    
                    logger.info(f"🔍 Recherche de tracks avec {len(track_selectors)} sélecteurs...")
                    
                    found_tracks = []
                    
                    for selector_idx, selector in enumerate(track_selectors):
                        try:
                            track_links = self.driver.find_elements(By.CSS_SELECTOR, selector)
                            logger.info(f"  📌 Sélecteur {selector_idx + 1}: '{selector}' → {len(track_links)} liens trouvés")
                            
                            for link_idx, link in enumerate(track_links[:10]):
                                try:
                                    href = link.get_attribute('href')
                                    if href and '/track/' in href:
                                        spotify_id = self.extract_spotify_id_from_url(href)
                                        if spotify_id and spotify_id not in [t['id'] for t in found_tracks]:
                                            
                                            # Récupérer le texte pour validation
                                            try:
                                                link_text = link.text.lower()
                                                parent_text = link.find_element(By.XPATH, "./..").text.lower()
                                                combined_text = f"{link_text} {parent_text}"
                                                
                                                relevance = self._calculate_relevance(artist, title, combined_text)
                                                
                                                found_tracks.append({
                                                    'id': spotify_id,
                                                    'text': combined_text,
                                                    'relevance': relevance,
                                                    'href': href
                                                })
                                                
                                                logger.info(f"    🎯 Track {link_idx + 1}: ID={spotify_id}, relevance={relevance:.2f}")
                                                logger.debug(f"       Text: '{combined_text[:100]}'")
                                                
                                            except Exception as text_error:
                                                # Ajouter sans texte
                                                found_tracks.append({
                                                    'id': spotify_id,
                                                    'text': '',
                                                    'relevance': 0.5,
                                                    'href': href
                                                })
                                                logger.info(f"    🎯 Track {link_idx + 1}: ID={spotify_id}, relevance=0.5 (no text)")
                                                
                                except Exception as link_error:
                                    logger.debug(f"    ❌ Erreur lien {link_idx + 1}: {link_error}")
                                    continue
                                
                        except Exception as selector_error:
                            logger.debug(f"  ❌ Erreur sélecteur '{selector}': {selector_error}")
                            continue
                    
                    # Analyser les résultats
                    logger.info(f"📊 Analyse: {len(found_tracks)} tracks trouvés au total")
                    
                    if found_tracks:
                        # Trier par pertinence
                        found_tracks.sort(key=lambda x: x['relevance'], reverse=True)
                        
                        logger.info("🏆 Top 3 des résultats:")
                        for i, track in enumerate(found_tracks[:3]):
                            logger.info(f"  {i+1}. ID={track['id']} (relevance={track['relevance']:.2f})")
                        
                        best_track = found_tracks[0]
                        logger.info(f"✅ SÉLECTIONNÉ: {best_track['id']} (relevance: {best_track['relevance']:.2f})")
                        return best_track['id']
                    else:
                        logger.warning(f"❌ Aucun track trouvé pour la requête: '{query}'")
                    
                except Exception as e:
                    logger.error(f"❌ Erreur avec la requête '{query}': {e}")
                    continue
                
                # Délai entre les requêtes
                if query_idx < len(search_queries) - 1:
                    logger.info("⏳ Délai entre requêtes...")
                    time.sleep(2)
            
            logger.warning("❌ Aucun track trouvé via recherche directe Spotify")
            return None
            
        except Exception as e:
            logger.error(f"❌ Erreur recherche directe Spotify: {e}")
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

    # ========== MÉTHODE PRINCIPALE DE RECHERCHE CORRIGÉE ==========

    def search_spotify_id(self, artist: str, title: str, force_selenium: bool = False) -> Optional[str]:
        """
        Recherche l'ID Spotify - VERSION ANTI-GOOGLE (contournement)
        
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
        
        # NOUVELLE STRATÉGIE : ÉVITER GOOGLE
        if self.use_selenium or force_selenium:
            # 1. PRIORITÉ 1: Recherche directe Spotify (plus fiable)
            logger.info("Tentative recherche directe Spotify...")
            spotify_id = self._search_spotify_direct_selenium(artist, title)
            
            # 2. PRIORITÉ 2: DuckDuckGo avec Selenium (pas de détection bot)
            if not spotify_id:
                logger.info("Tentative DuckDuckGo avec Selenium...")
                spotify_id = self._search_duckduckgo_selenium(artist, title)
        
        # 3. PRIORITÉ 3: Méthodes classiques sans Selenium
        if not spotify_id:
            logger.info("Tentative DuckDuckGo classique...")
            spotify_id = self._search_duckduckgo(artist, title)
        
        # 4. DERNIER RECOURS: Google requests (sans Selenium)
        if not spotify_id:
            logger.info("Dernier recours: Google requests...")
            spotify_id = self._search_google_requests(artist, title)
        
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

    # ========== MÉTHODES RECCOBEATS API CORRIGÉES ==========

    def get_track_from_reccobeats(self, spotify_id: str) -> Optional[Dict]:
        """Récupère les données ReccoBeats - VERSION CORRIGÉE"""
        try:
            # URL CORRECTE avec /track (singulier)
            url = f"{self.recco_base_url}/track"
            params = {'ids': spotify_id}
            
            logger.info(f"🎵 ReccoBeats: Requête pour ID {spotify_id}")
            logger.debug(f"   URL: {url}?ids={spotify_id}")
            
            response = self.recco_session.get(url, params=params, timeout=15)
            
            logger.info(f"📡 Response: Status {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                
                # Gérer différents formats de réponse
                track = None
                
                if isinstance(data, list) and len(data) > 0:
                    # Format liste directe
                    track = data[0]
                    logger.info(f"✅ Track trouvé (liste): {track.get('trackTitle', 'N/A')}")
                elif isinstance(data, dict):
                    # Format dict avec 'content'
                    if 'content' in data:
                        content = data['content']
                        logger.debug(f"🔍 Trouvé clé 'content', type: {type(content)}")
                        
                        # Cas 1: content est une liste
                        if isinstance(content, list):
                            if len(content) > 0:
                                track = content[0]
                                logger.info(f"✅ Track trouvé (dict.content[0]): {track.get('trackTitle', 'N/A')}")
                            else:
                                logger.warning(f"⚠️ Liste 'content' vide")
                        
                        # Cas 2: content est directement un dict (le track)
                        elif isinstance(content, dict):
                            if 'id' in content or 'trackTitle' in content:
                                track = content
                                logger.info(f"✅ Track trouvé (dict.content dict): {track.get('trackTitle', 'N/A')}")
                            else:
                                logger.warning(f"⚠️ Dict 'content' sans 'id' ni 'trackTitle'. Clés: {list(content.keys())}")
                        
                        else:
                            logger.warning(f"⚠️ 'content' n'est ni liste ni dict: {type(content)}")
                    
                    # Ou dict direct (le track lui-même) 
                    elif 'id' in data or 'trackTitle' in data:
                        track = data
                        logger.info(f"✅ Track trouvé (dict direct): {track.get('trackTitle', 'N/A')}")
                    
                    else:
                        logger.warning(f"❌ Structure dict inconnue. Clés: {list(data.keys())}")
                        # Log plus détaillé pour debug
                        logger.debug(f"   Contenu complet: {json.dumps(data, indent=2)[:500]}")
                else:
                    logger.warning(f"❌ Format de réponse inattendu: {type(data)}")
                
                return track

            elif response.status_code == 404:
                logger.warning(f"❌ Track {spotify_id} non trouvé (404)")
            elif response.status_code == 429:
                logger.warning("⏰ Rate limit atteint")
            else:
                logger.error(f"❌ Erreur {response.status_code}: {response.text[:200]}")
                
        except Exception as e:
            logger.error(f"❌ Exception ReccoBeats: {e}")
        
        return None

    def get_track_audio_features(self, reccobeats_id: str) -> Optional[Dict]:
        """Récupère le BPM via audio features - VERSION CORRIGÉE"""
        try:
            url = f"{self.recco_base_url}/track/{reccobeats_id}/audio-features"
            
            logger.debug(f"🎼 Audio features: {url}")
            
            response = self.recco_session.get(url, timeout=15)
            
            if response.status_code == 200:
                features = response.json()
                logger.info(f"✅ BPM récupéré: {features.get('tempo', 'N/A')}")
                return features
            else:
                logger.warning(f"❌ Audio features erreur {response.status_code}")
                    
        except Exception as e:
            logger.error(f"❌ Exception audio features: {e}")
        
        return None

    def get_multiple_tracks_with_bpm(self, spotify_ids: List[str]) -> List[Dict]:
        """Récupère plusieurs tracks + BPM en batch (max 50 IDs)"""
        try:
            # Limiter à 50 IDs par requête (bonne pratique)
            spotify_ids = spotify_ids[:50]
            
            # Étape 1: Récupérer tous les tracks
            url = f"{self.recco_base_url}/track"
            params = {'ids': ','.join(spotify_ids)}  # CSV format
            
            logger.info(f"🎵 Batch request pour {len(spotify_ids)} tracks")
            
            response = self.recco_session.get(url, params=params, timeout=30)
            
            if response.status_code != 200:
                logger.error(f"❌ Batch request failed: {response.status_code}")
                return []
            
            tracks = response.json()
            
            # Étape 2: Récupérer les BPM pour chaque track
            for track in tracks:
                reccobeats_id = track.get('id')
                if reccobeats_id:
                    features = self.get_track_audio_features(reccobeats_id)
                    if features:
                        track['bpm'] = features.get('tempo')
                        track['audio_features'] = features
            
            logger.info(f"✅ {len(tracks)} tracks enrichis avec BPM")
            return tracks
            
        except Exception as e:
            logger.error(f"❌ Batch processing error: {e}")
            return []

    def clear_error_cache(self, artist: str = None, title: str = None):
        """Nettoie les erreurs du cache pour permettre de nouvelles tentatives"""
        if artist and title:
            cache_key = self._get_cache_key(artist, title)
            if cache_key in self.cache:
                cached = self.cache[cache_key]
                if isinstance(cached, dict) and ('error' in cached or not cached.get('success')):
                    del self.cache[cache_key]
                    logger.info(f"Cache d'erreur supprimé pour: {artist} - {title}")
                    self._save_cache()
                    return True
            return False
        else:
            # Nettoyer toutes les erreurs
            errors_removed = 0
            keys_to_remove = []
            
            for key, value in self.cache.items():
                if isinstance(value, dict) and ('error' in value or not value.get('success')):
                    keys_to_remove.append(key)
            
            for key in keys_to_remove:
                del self.cache[key]
                errors_removed += 1
            
            if errors_removed > 0:
                logger.info(f"{errors_removed} entrées d'erreur supprimées du cache")
                self._save_cache()
            
            return errors_removed > 0
    
    # ========== MÉTHODE PRINCIPALE INTÉGRÉE ==========

    def test_spotify_id_storage(self, artist: str, title: str) -> Dict:
        """Test spécifique pour vérifier que l'ID Spotify est bien stocké"""
        logger.info(f"🧪 TEST STOCKAGE SPOTIFY ID: {artist} - {title}")
        
        result = {
            'artist': artist,
            'title': title,
            'spotify_id_found': False,
            'spotify_id': None,
            'stored_successfully': False,
            'reccobeats_success': False,
            'final_bpm': None,
            'errors': []
        }
        
        try:
            # Créer un Track temporaire pour test
            from src.models.track import Track
            from src.models.artist import Artist
            
            test_artist = Artist(name=artist)
            test_track = Track(title=title, artist=test_artist)
            
            # Test enrichissement
            from src.utils.data_enricher import DataEnricher
            enricher = DataEnricher()
            
            # Enrichir avec ReccoBeats
            success = enricher._enrich_with_reccobeats(test_track)
            
            # Vérifier les résultats
            result['stored_successfully'] = hasattr(test_track, 'spotify_id') and test_track.spotify_id
            result['spotify_id'] = getattr(test_track, 'spotify_id', None)
            result['spotify_id_found'] = result['spotify_id'] is not None
            result['reccobeats_success'] = success
            result['final_bpm'] = getattr(test_track, 'bpm', None)
            
            if not result['spotify_id_found']:
                result['errors'].append("Spotify ID not found")
            if not result['stored_successfully']:
                result['errors'].append("Spotify ID not stored in track")
            
        except Exception as e:
            result['errors'].append(f"Exception: {e}")
            logger.error(f"❌ Exception: {e}")
        
        # Résumé
        logger.info("📊 RÉSUMÉ DU TEST STOCKAGE:")
        logger.info(f"  🎵 ID Spotify trouvé: {result['spotify_id_found']}")
        if result['spotify_id']:
            logger.info(f"     ID: {result['spotify_id']}")
        logger.info(f"  💾 Stocké dans Track: {result['stored_successfully']}")
        logger.info(f"  🎼 ReccoBeats succès: {result['reccobeats_success']}")
        if result['final_bpm']:
            logger.info(f"     BPM: {result['final_bpm']}")
        if result['errors']:
            logger.warning(f"  ❌ Erreurs: {result['errors']}")
        
        return result

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

    # ========== MÉTHODES DE TEST ET DIAGNOSTIC ==========

    def test_spotify_direct_only(self, artist: str, title: str) -> Dict:
        """Test uniquement Spotify direct avec logs ultra-détaillés"""
        logger.info(f"🧪 TEST SPOTIFY DIRECT SEULEMENT: {artist} - {title}")
        
        result = {
            'artist': artist,
            'title': title,
            'spotify_found': False,
            'spotify_id': None,
            'reccobeats_found': False,
            'reccobeats_data': None,
            'final_bpm': None,
            'errors': []
        }
        
        try:
            # Étape 1: Test Spotify direct uniquement
            logger.info("🎵 Étape 1: Test Spotify direct...")
            spotify_id = self._search_spotify_direct_selenium(artist, title)
            
            result['spotify_found'] = spotify_id is not None
            result['spotify_id'] = spotify_id
            
            if not spotify_id:
                result['errors'].append("Spotify direct failed")
                logger.error("❌ Échec Spotify direct")
                return result
            
            logger.info(f"✅ Spotify ID trouvé: {spotify_id}")
            
            # Étape 2: Test ReccoBeats avec cet ID
            logger.info("🎵 Étape 2: Test ReccoBeats...")
            reccobeats_data = self.get_track_from_reccobeats(spotify_id)
            
            result['reccobeats_found'] = reccobeats_data is not None
            result['reccobeats_data'] = reccobeats_data
        
            if reccobeats_data:
                logger.info("✅ Données ReccoBeats trouvées")
                
                # Chercher le BPM
                bpm = None
                if 'tempo' in reccobeats_data:
                    bpm = reccobeats_data['tempo']
                    logger.info(f"🎼 BPM trouvé dans 'tempo': {bpm}")
                elif 'bpm' in reccobeats_data:
                    bpm = reccobeats_data['bpm']
                    logger.info(f"🎼 BPM trouvé dans 'bpm': {bpm}")
                else:
                    logger.warning("❌ Aucun BPM trouvé dans les données")
                
                result['final_bpm'] = bpm
            else:
                result['errors'].append("ReccoBeats data not found")
                logger.error("❌ Aucune donnée ReccoBeats")
            
        except Exception as e:
            result['errors'].append(f"Exception: {e}")
            logger.error(f"❌ Exception: {e}")
        
        # Résumé
        logger.info("📊 RÉSUMÉ DU TEST:")
        logger.info(f"  🎵 Spotify trouvé: {result['spotify_found']}")
        if result['spotify_id']:
            logger.info(f"     ID: {result['spotify_id']}")
        logger.info(f"  🎼 ReccoBeats trouvé: {result['reccobeats_found']}")
        if result['final_bpm']:
            logger.info(f"     BPM: {result['final_bpm']}")
        if result['errors']:
            logger.warning(f"  ❌ Erreurs: {result['errors']}")
        
        return result

    def test_single_track(self, artist: str, title: str) -> Dict:
        """Test complet d'un track avec diagnostics détaillés"""
        logger.info(f"🧪 TEST COMPLET: {artist} - {title}")
        
        results = {
            'artist': artist,
            'title': title,
            'steps': {},
            'final_result': None,
            'errors': []
        }
        
        try:
            # Étape 1: Recherche ID Spotify
            logger.info("🔍 Étape 1: Recherche ID Spotify")
            spotify_id = self.search_spotify_id(artist, title)
            results['steps']['spotify_search'] = {
                'success': spotify_id is not None,
                'spotify_id': spotify_id
            }
            
            if not spotify_id:
                results['errors'].append("Aucun ID Spotify trouvé")
                return results
            
            # Étape 2: Test ReccoBeats direct
            logger.info(f"🎵 Étape 2: Test ReccoBeats avec ID {spotify_id}")
            track_data = self.get_track_from_reccobeats(spotify_id)
            results['steps']['reccobeats_track'] = {
                'success': track_data is not None,
                'data_type': type(track_data).__name__ if track_data else None,
                'has_bpm': track_data.get('tempo') is not None if track_data else False
            }
            
            if not track_data:
                results['errors'].append("Données ReccoBeats non trouvées")
                return results
            
            # Étape 3: Test audio features
            reccobeats_id = track_data.get('id')
            if reccobeats_id:
                logger.info(f"🎼 Étape 3: Audio features pour {reccobeats_id}")
                audio_features = self.get_track_audio_features(reccobeats_id)
                results['steps']['audio_features'] = {
                    'success': audio_features is not None,
                    'has_tempo': audio_features.get('tempo') is not None if audio_features else False
                }
            
            # Construire le résultat final
            final_result = self.get_track_info(artist, title, use_cache=False)
            results['final_result'] = {
                'success': final_result is not None and final_result.get('success'),
                'bpm': final_result.get('bpm') if final_result else None,
                'spotify_id': final_result.get('spotify_id') if final_result else None
            }
            
        except Exception as e:
            results['errors'].append(f"Erreur générale: {e}")
            logger.error(f"Erreur test: {e}")
        
        # Afficher le résumé
        logger.info("📊 RÉSUMÉ DU TEST:")
        for step, data in results['steps'].items():
            status = "✅" if data['success'] else "❌"
            logger.info(f"  {status} {step}: {data}")
        
        if results['errors']:
            logger.warning(f"⚠️ Erreurs: {results['errors']}")
        
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
        """Ferme toutes les connexions - VERSION AMÉLIORÉE"""
        try:
            # Fermer le driver Selenium
            self._close_selenium_driver()
            
            # Fermer les sessions
            if hasattr(self, 'recco_session'):
                self.recco_session.close()
            
            if hasattr(self, 'scraper_session'):
                self.scraper_session.close()
            
            logger.info("✅ ReccoBeats client fermé proprement")
            
        except Exception as e:
            logger.error(f"Erreur fermeture ReccoBeats client: {e}")

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
            status = "✅" if value else "❌" if value is False else "⭕"
            print(f"{status} {key}: {value}")
        
        # Test diagnostique complet
        print("\n=== Test diagnostique ===")
        result = client.test_single_track("Drake", "God's Plan")
        print("Résultat:", result)
        
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