"""Scraper pour récupérer le BPM depuis songbpm.com avec Selenium"""
import re
import time
from typing import Optional, Dict, Any, List
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager

from src.models import Track
from src.utils.logger import get_logger, log_api
from src.config import DELAY_BETWEEN_REQUESTS

logger = get_logger(__name__)


class SongBPMScraper:
    """Scrape songbpm.com pour obtenir le BPM, Key et Duration"""

    def __init__(self, headless: bool = False):
        """
        Initialise le scraper avec Selenium
        
        Args:
            headless: Si True, exécute le navigateur en mode invisible
        """
        self.base_url = "https://songbpm.com/"
        self.driver = None
        self.wait = None
        self.headless = headless
        
        # Pattern pour extraire l'ID Spotify depuis une URL
        self.spotify_id_pattern = re.compile(r'spotify\.com/(?:intl-[a-z]{2}/)?track/([a-zA-Z0-9]{22})')

    def _ensure_driver(self):
        """
        S'assure que le driver est initialisé (initialisation paresseuse)
        Cette méthode sera appelée automatiquement avant chaque utilisation du driver
        """
        if self.driver is None:
            self._init_driver()

    def _init_driver(self):
        """Initialise le driver Selenium avec configuration robuste"""
        try:
            logger.info(f"🌐 Initialisation du driver Selenium SongBPM (headless={self.headless})...")
            
            options = Options()
            
            # Mode headless ou visible selon configuration
            if self.headless:
                options.add_argument('--headless=new')  # Nouveau mode headless
                options.add_argument('--window-size=1920,1080')
            
            # Options standards pour éviter la détection
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-gpu')
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

            # Désactiver WebGL/WebGPU/GPU/DirectX complètement
            options.add_argument('--disable-webgl')
            options.add_argument('--disable-webgl2')
            options.add_argument('--disable-webgpu')
            options.add_argument('--disable-3d-apis')
            options.add_argument('--disable-software-rasterizer')
            options.add_argument('--disable-gpu-sandbox')
            options.add_argument('--use-angle=disabled')
            options.add_argument('--disable-d3d11')
            options.add_argument('--disable-features=VizDisplayCompositor')
            options.add_argument('--disable-accelerated-2d-canvas')
            options.add_argument('--disable-accelerated-video-decode')

            # Service avec redirection des logs vers NUL
            import platform
            if platform.system() == 'Windows':
                service = ChromeService(ChromeDriverManager().install(), log_path='NUL')
            else:
                service = ChromeService(ChromeDriverManager().install(), log_path='/dev/null')
            
            # Préférences pour désactiver les popups et notifications
            prefs = {
                "profile.default_content_setting_values.notifications": 2,
                "profile.default_content_settings.popups": 0,
                "profile.managed_default_content_settings.images": 1
            }
            options.add_experimental_option("prefs", prefs)
            
            self.driver = webdriver.Chrome(service=service, options=options)
            self.wait = WebDriverWait(self.driver, 10)
            
            # Script pour masquer les signes d'automation
            self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            logger.info("✅ SongBPM: Driver Selenium initialisé avec succès")
            
        except Exception as e:
            logger.error(f"❌ Erreur initialisation driver SongBPM: {e}")
            self.driver = None
            self.wait = None
            raise

    def _handle_cookies(self):
        """Gère le popup de cookies sur SongBPM"""
        try:
            logger.debug("Vérification du popup de cookies...")
            
            # Attendre un peu que le popup apparaisse
            time.sleep(1.5)
            
            # PRIORITÉ ABSOLUE : Chercher le bouton "AGREE" du CMP (Consent Management Platform)
            try:
                # Chercher le bouton AGREE spécifique dans qc-cmp2-summary-buttons
                agree_selectors = [
                    "div.qc-cmp2-summary-buttons button span:contains('AGREE')",
                    ".qc-cmp2-summary-buttons button[mode='primary']",
                    "button.css-47sehv",
                    "//div[contains(@class, 'qc-cmp2-summary-buttons')]//button//span[text()='AGREE']/..",
                    "//button[.//span[text()='AGREE']]"
                ]
                
                for selector in agree_selectors:
                    try:
                        if selector.startswith('//'):
                            # XPath
                            buttons = self.driver.find_elements(By.XPATH, selector)
                        else:
                            # CSS
                            buttons = self.driver.find_elements(By.CSS_SELECTOR, selector)
                        
                        if buttons:
                            for button in buttons:
                                try:
                                    if button.is_displayed() and button.is_enabled():
                                        button_text = button.text.strip()
                                        logger.info(f"✅ Clic sur le bouton AGREE du CMP: '{button_text}'")
                                        button.click()
                                        time.sleep(1)
                                        logger.info("✅ Popup CMP fermé")
                                        return
                                except Exception as e:
                                    logger.debug(f"Erreur clic AGREE: {e}")
                                    continue
                    except Exception as e:
                        logger.debug(f"Erreur recherche AGREE avec {selector}: {e}")
                        continue
            except Exception as e:
                logger.debug(f"Erreur recherche CMP AGREE: {e}")
            
            # Stratégie 1: Chercher spécifiquement "Save & Exit" (le bouton exact)
            try:
                # XPath précis pour "Save & Exit" ou "Save and Exit"
                save_exit_xpath = "//button[contains(translate(text(), 'AND', 'and'), 'save') and contains(translate(text(), 'AND', 'and'), 'exit')]"
                buttons = self.driver.find_elements(By.XPATH, save_exit_xpath)
                
                if buttons:
                    for button in buttons:
                        try:
                            if button.is_displayed() and button.is_enabled():
                                button_text = button.text.strip()
                                # Vérifier que ce n'est pas un bouton indésirable
                                if button_text.lower() not in ['partners', 'settings', 'customize', 'manage']:
                                    logger.info(f"✅ Clic sur le bouton de cookies: '{button_text}'")
                                    button.click()
                                    time.sleep(1)
                                    logger.info("✅ Popup de cookies fermé")
                                    return
                        except Exception as e:
                            logger.debug(f"Erreur clic bouton: {e}")
                            continue
            except Exception as e:
                logger.debug(f"Erreur recherche Save & Exit: {e}")
            
            # Stratégie 2: Chercher un bouton "Accept All" ou similaire
            accept_keywords = ['accept all', 'accept cookies', 'agree', 'i understand']
            for keyword in accept_keywords:
                try:
                    xpath = f"//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{keyword}')]"
                    buttons = self.driver.find_elements(By.XPATH, xpath)
                    
                    if buttons:
                        for button in buttons:
                            try:
                                if button.is_displayed() and button.is_enabled():
                                    button_text = button.text.strip()
                                    # Éviter les boutons indésirables
                                    if button_text.lower() not in ['partners', 'settings', 'customize', 'manage', 'reject']:
                                        logger.info(f"✅ Clic sur le bouton de cookies: '{button_text}'")
                                        button.click()
                                        time.sleep(1)
                                        logger.info("✅ Popup de cookies fermé")
                                        return
                            except Exception as e:
                                logger.debug(f"Erreur clic bouton: {e}")
                                continue
                except Exception as e:
                    logger.debug(f"Erreur recherche {keyword}: {e}")
            
            # Stratégie 3: Chercher dans un conteneur de cookies spécifique
            try:
                # Chercher un div/section avec "cookie" ou "consent" dans la classe
                cookie_containers = self.driver.find_elements(By.CSS_SELECTOR, "[class*='cookie'], [class*='consent'], [id*='cookie'], [id*='consent']")
                
                for container in cookie_containers:
                    try:
                        if container.is_displayed():
                            # Chercher les boutons dans ce conteneur
                            buttons = container.find_elements(By.TAG_NAME, "button")
                            
                            # Chercher le bouton qui ressemble à une acceptation/fermeture
                            for button in buttons:
                                try:
                                    if button.is_displayed() and button.is_enabled():
                                        button_text = button.text.strip().lower()
                                        # Mots-clés positifs pour accepter
                                        positive_keywords = ['save', 'exit', 'accept', 'agree', 'ok', 'continue', 'got it', 'close']
                                        # Mots-clés négatifs à éviter
                                        negative_keywords = ['partners', 'settings', 'customize', 'manage', 'preferences', 'reject', 'decline', 'more options']
                                        
                                        if any(kw in button_text for kw in positive_keywords) and not any(kw in button_text for kw in negative_keywords):
                                            logger.info(f"✅ Clic sur le bouton de cookies dans conteneur: '{button.text.strip()}'")
                                            button.click()
                                            time.sleep(1)
                                            logger.info("✅ Popup de cookies fermé")
                                            return
                                except Exception as e:
                                    logger.debug(f"Erreur clic bouton dans conteneur: {e}")
                                    continue
                    except Exception as e:
                        logger.debug(f"Erreur inspection conteneur: {e}")
                        continue
            except Exception as e:
                logger.debug(f"Erreur recherche conteneur cookies: {e}")
            
            logger.debug("Aucun popup de cookies détecté ou déjà fermé")
            
        except Exception as e:
            logger.debug(f"Erreur gestion cookies (non bloquant): {e}")

    def _extract_spotify_id_from_url(self, url: str) -> Optional[str]:
        """
        Extrait l'ID Spotify depuis une URL
        
        Args:
            url: URL Spotify
            
        Returns:
            ID Spotify (22 caractères) ou None
        """
        if not url:
            return None
            
        match = self.spotify_id_pattern.search(url)
        if match:
            return match.group(1)
        return None

    def _normalize_string(self, s: str) -> str:
        """Normalise une chaîne pour la comparaison (minuscules, sans espaces superflus)"""
        return " ".join(s.lower().strip().split())

    def _match_track(self, result_title: str, result_artist: str, 
                 search_title: str, search_artist: str,
                 result_spotify_id: Optional[str] = None,
                 search_spotify_id: Optional[str] = None) -> bool:
        """
        Vérifie si un résultat correspond au morceau recherché
        
        Returns:
            True si le résultat correspond
        """
        # PRIORITÉ 1 : Matching par Spotify ID (le plus fiable)
        if result_spotify_id and search_spotify_id:
            spotify_match = result_spotify_id == search_spotify_id
            logger.debug(f"🎵 Match Spotify ID: {result_spotify_id} vs {search_spotify_id} → {spotify_match}")
            if spotify_match:
                logger.info(f"✅ MATCH PARFAIT via Spotify ID: {search_spotify_id}")
                return True
            else:
                # Si on a les deux IDs mais qu'ils ne correspondent pas, c'est un REJET
                logger.info(f"❌ REJET: Spotify IDs différents")
                return False
        
        # PRIORITÉ 2 : Matching par nom (fallback si pas les DEUX Spotify IDs)
        norm_result_title = self._normalize_string(result_title)
        norm_result_artist = self._normalize_string(result_artist)
        norm_search_title = self._normalize_string(search_title)
        norm_search_artist = self._normalize_string(search_artist)
        
        # Vérification stricte : titre ET artiste doivent correspondre EXACTEMENT
        title_match = norm_result_title == norm_search_title
        artist_match = norm_result_artist == norm_search_artist
        
        logger.debug(f"🔍 Comparaison noms:")
        logger.debug(f"   Titres: '{norm_result_title}' vs '{norm_search_title}' → {title_match}")
        logger.debug(f"   Artistes: '{norm_result_artist}' vs '{norm_search_artist}' → {artist_match}")
        
        if title_match and artist_match:
            logger.info(f"✅ Match par nom/artiste")
            return True
        
        logger.info(f"❌ REJET: Titre ou artiste ne correspond pas")
        return False

    def _extract_track_details(self, detail_url: str, timeout: int = 30) -> Dict[str, Any]:
        """
        Extrait les détails complets depuis la page de détail d'un morceau
        
        Args:
            detail_url: URL de la page de détail (ex: https://songbpm.com/@josman/bambi-a9yu5)
            
        Returns:
            Dict avec les détails (mode, energy, danceability, etc.)
        """
        self._ensure_driver()
        details = {}
        
        try:
            logger.info(f"📄 Navigation vers page de détail: {detail_url}")
            
            # NOUVEAU: Définir un timeout court pour le driver
            self.driver.set_page_load_timeout(timeout)
            self.driver.get(detail_url)
            
            # Attendre un peu
            time.sleep(2)
            
            logger.debug("Attente du chargement de la page de détail...")
            
            # Essayer plusieurs sélecteurs pour trouver le contenu principal
            content_selectors = [
                "div.lg\\:prose-xl",
                "div[class*='prose']",
                "main",
                "article"
            ]
            
            full_text = None
            for selector in content_selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    if elements and elements[0].is_displayed():
                        full_text = elements[0].text
                        logger.debug(f"✅ Contenu trouvé avec sélecteur: {selector}")
                        break
                except Exception as e:
                    logger.debug(f"Sélecteur {selector} échoué: {e}")
                    continue
            
            if not full_text:
                # Dernière tentative : récupérer tout le body
                try:
                    full_text = self.driver.find_element(By.TAG_NAME, "body").text
                    logger.debug("Utilisation du body complet pour extraction")
                except:
                    logger.error("❌ Impossible de récupérer le texte de la page")
                    return details
            
            # Extraire le mode (major/minor) depuis le texte
            import re
            
            # NOUVEAU: Nettoyer le texte d'abord
            # Remplacer les espaces multiples, retours à la ligne, et espaces insécables
            clean_text = re.sub(r'\s+', ' ', full_text)  # Normaliser tous les espaces
            clean_text = clean_text.replace('\xa0', ' ')  # Espaces insécables → espaces normaux
            
            # NOUVEAU: Logs de debug pour voir exactement ce qu'on cherche
            if 'key and' in clean_text.lower():
                idx = clean_text.lower().index('key and')
                excerpt = clean_text[max(0, idx-20):idx+50]
                logger.debug(f"🔍 Extrait autour de 'key and': ...{excerpt}...")
            
            # Regex AMÉLIORÉE : Plus flexible sur les espaces
            mode_match = re.search(
                r'with\s+a\s+([A-G][\#b♯♭]?)\s+key\s+and\s+a\s+(\w+)\s+mode',
                clean_text,
                re.IGNORECASE
            )
            
            if mode_match:
                mode = mode_match.group(2).lower()
                details['mode'] = mode
                logger.info(f"🎵 Mode trouvé: {mode}")
            else:
                logger.warning("⚠️ Mode non trouvé dans le texte")
                logger.debug(f"🔍 Texte analysé (premiers 500 char): {clean_text[:500]}")
            
            # Extraire la signature temporelle
            time_sig_match = re.search(r'(\d+)\s+beats per bar', full_text, re.IGNORECASE)
            if time_sig_match:
                details['time_signature'] = int(time_sig_match.group(1))
                logger.debug(f"Time signature: {details['time_signature']}/4")
            
            logger.info(f"✅ Détails extraits: {len(details)} attributs")
            if details:
                logger.info(f"📊 Détails: {details}")
            else:
                logger.warning("⚠️ Aucun détail extrait de la page")
            
            return details
            
        except TimeoutError:
            logger.warning(f"⏰ Timeout ({timeout}s) lors de la récupération des détails")
            raise  # Re-lever l'exception pour qu'elle soit gérée par enrich_track
        except Exception as e:
            logger.error(f"❌ Erreur extraction détails: {e}")
            return details

    def search_track(self, track_title: str, artist_name: str, 
                    spotify_id: Optional[str] = None,
                    max_results_to_check: int = 5,
                    fetch_details: bool = True) -> Optional[Dict[str, Any]]:
        """
        Recherche un morceau sur SongBPM et récupère ses informations
        
        Args:
            track_title: Titre du morceau
            artist_name: Nom de l'artiste
            spotify_id: ID Spotify du morceau (optionnel, permet un matching précis)
            max_results_to_check: Nombre maximum de résultats à vérifier (par défaut 5)
            fetch_details: Si True, navigue vers la page de détail pour récupérer le mode (par défaut True)
            
        Returns:
            Dict contenant les infos du morceau ou None si non trouvé
        """
        self._ensure_driver()
        if not self.driver:
            logger.error("❌ SongBPM: Driver non initialisé")
            return None

        try:
            if spotify_id:
                logger.info(f"🔍 SongBPM: Recherche '{track_title}' par {artist_name} (Spotify ID: {spotify_id})")
            else:
                logger.info(f"🔍 SongBPM: Recherche '{track_title}' par {artist_name}")
            
            # 1. Aller sur la page d'accueil
            self.driver.get(self.base_url)
            time.sleep(1)
            
            # 2. Gérer le popup de cookies
            self._handle_cookies()
            
            # 3. Trouver la barre de recherche et entrer la requête
            search_input = self.wait.until(
                EC.presence_of_element_located((
                    By.CSS_SELECTOR, 
                    "input[name='query'][placeholder='type a song, get a bpm']"
                ))
            )
            
            search_query = f"{artist_name} {track_title}"
            search_input.clear()
            search_input.send_keys(search_query)
            search_input.send_keys(Keys.RETURN)
            
            logger.debug(f"📝 SongBPM: Recherche soumise: '{search_query}'")
            
            # 3. Attendre que les résultats se chargent
            # Attendre soit que l'URL change, soit que les résultats apparaissent
            logger.debug("⏳ Attente du chargement des résultats...")
            time.sleep(3)  # Attendre un peu plus longtemps
            
            # Vérifier l'URL actuelle
            current_url = self.driver.current_url
            logger.debug(f"📍 URL actuelle: {current_url}")
            
            # Vérifier si des résultats sont présents
            try:
                # Essayer plusieurs sélecteurs pour les résultats
                result_selectors = [
                    "a[href*='/@']",
                    "div.flex-1 > p",
                    "[class*='card']"
                ]
                
                results_found = False
                for selector in result_selectors:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    if elements:
                        logger.debug(f"✅ Trouvé {len(elements)} éléments avec sélecteur: {selector}")
                        results_found = True
                        break
                
                if not results_found:
                    logger.error("❌ Aucun élément de résultat trouvé sur la page")
                    # Prendre un screenshot pour debug si en mode visible
                    if not self.headless:
                        try:
                            screenshot_path = "songbpm_debug.png"
                            self.driver.save_screenshot(screenshot_path)
                            logger.info(f"📸 Screenshot sauvegardé: {screenshot_path}")
                        except:
                            pass
                    log_api("SongBPM", f"search/{track_title}", False)
                    return None
                    
            except Exception as e:
                logger.error(f"Erreur vérification résultats: {e}")
            
            # 4. Récupérer les résultats (maintenant avec Spotify ID et detail_url)
            results = self._get_search_results()
            
            if not results:
                logger.warning(f"❌ SongBPM: Aucun résultat pour '{search_query}'")
                log_api("SongBPM", f"search/{track_title}", False)
                return None
            
            logger.info(f"📊 SongBPM: {len(results)} résultat(s) trouvé(s)")
            
            # 5. Vérifier les résultats (jusqu'à max_results_to_check)
            for i, result in enumerate(results[:max_results_to_check], 1):
                logger.debug(f"Vérification résultat {i}/{min(len(results), max_results_to_check)}")
                
                # Matcher avec le Spotify ID si disponible, sinon par nom
                if self._match_track(
                    result['title'], 
                    result['artist'],
                    track_title,
                    artist_name,
                    result_spotify_id=result.get('spotify_id'),
                    search_spotify_id=spotify_id
                ):
                    logger.info(f"✅ SongBPM: Correspondance trouvée (résultat #{i})")
                    logger.info(f"📊 Données de base: BPM={result.get('bpm')}, "
                              f"Key={result.get('key')}, Duration={result.get('duration')}")
                    if result.get('spotify_id'):
                        logger.info(f"🎵 Spotify ID confirmé: {result['spotify_id']}")
                    
                    # 6. Récupérer les détails depuis la page de détail (notamment le mode)
                    if fetch_details and result.get('detail_url'):
                        try:
                            details = self._extract_track_details(result['detail_url'])
                            # Fusionner les détails avec le résultat
                            result.update(details)
                            logger.info(f"📊 Données complètes avec mode: {result.get('mode')}")
                        except Exception as e:
                            logger.warning(f"⚠️ Impossible de récupérer les détails: {e}")
                    
                    log_api("SongBPM", f"search/{track_title}", True)
                    return result
            
            # Aucune correspondance trouvée
            logger.warning(f"❌ SongBPM: Aucune correspondance exacte trouvée parmi "
                         f"{min(len(results), max_results_to_check)} résultat(s)")
            log_api("SongBPM", f"search/{track_title}", False)
            return None

        except TimeoutException:
            logger.error("❌ SongBPM: Timeout lors de la recherche")
            log_api("SongBPM", f"search/{track_title}", False)
            return None
        except Exception as e:
            logger.error(f"❌ SongBPM: Erreur lors de la recherche: {e}")
            log_api("SongBPM", f"search/{track_title}", False)
            return None

    def _get_search_results(self) -> List[Dict[str, Any]]:
        """
        Extrait les résultats de recherche de la page
        
        Returns:
            Liste de dictionnaires contenant les infos des résultats (avec spotify_id et detail_url)
        """
        results = []
        
        try:
            logger.debug("🔍 Début extraction des résultats...")
            time.sleep(1)
            
            # Stratégie : Trouver d'abord tous les conteneurs de résultats (div.bg-card)
            # Chaque conteneur contient TOUT : le lien track + le lien Spotify
            result_containers = self.driver.find_elements(By.CSS_SELECTOR, "div.bg-card")
            
            logger.debug(f"📋 Trouvé {len(result_containers)} conteneurs de résultats")
            
            if not result_containers:
                logger.warning("⚠️ Aucun conteneur de résultat trouvé")
                return []
            
            for container_idx, container in enumerate(result_containers, 1):
                try:
                    result = {}
                    
                    # 1. Extraire le lien principal du track (/@artiste/titre)
                    track_links = container.find_elements(By.CSS_SELECTOR, "a[href*='/@']")
                    if not track_links:
                        logger.debug(f"Conteneur {container_idx}: Pas de lien track, skip")
                        continue
                    
                    track_link = track_links[0]
                    href = track_link.get_attribute('href')
                    
                    # Vérifier la structure
                    if not href or '/@' not in href:
                        continue
                    
                    path = href.split('songbpm.com')[-1] if 'songbpm.com' in href else href
                    if path.count('/') < 2:
                        continue
                    
                    # Éviter les liens vers des services externes
                    if any(service in href.lower() for service in ['/apple-music', '/spotify', '/amazon', '/youtube']):
                        continue
                    
                    result['detail_url'] = href
                    logger.debug(f"📦 Conteneur {container_idx}: {href}")
                    
                    # 2. Extraire titre et artiste
                    try:
                        info_divs = track_link.find_elements(By.CSS_SELECTOR, "div.flex-1")
                        
                        for info_div in info_divs:
                            paragraphs = info_div.find_elements(By.TAG_NAME, "p")
                            
                            if len(paragraphs) >= 2:
                                artist_p = paragraphs[0]
                                title_p = paragraphs[1]
                                
                                artist_class = artist_p.get_attribute('class') or ''
                                title_class = title_p.get_attribute('class') or ''
                                
                                if 'text-sm' in artist_class and ('text-lg' in title_class or 'text-2xl' in title_class):
                                    result['artist'] = artist_p.text.strip()
                                    result['title'] = title_p.text.strip()
                                    logger.debug(f"   👤 {result['artist']} - 🎵 {result['title']}")
                                    break
                    except Exception as e:
                        logger.debug(f"   Erreur extraction titre/artiste: {e}")
                        continue
                    
                    if 'title' not in result or 'artist' not in result:
                        logger.debug(f"   Pas de titre/artiste, skip")
                        continue
                    
                    # 3. Extraire BPM, Key, Duration
                    try:
                        metrics_divs = track_link.find_elements(
                            By.CSS_SELECTOR, 
                            "div.flex.flex-1.flex-col.items-center"
                        )
                        
                        for metric_div in metrics_divs:
                            try:
                                spans = metric_div.find_elements(By.TAG_NAME, "span")
                                if len(spans) >= 2:
                                    label = spans[0].text.strip().upper()
                                    value = spans[1].text.strip()
                                    
                                    if label == "BPM":
                                        try:
                                            result['bpm'] = int(value)
                                            logger.debug(f"   📊 BPM: {result['bpm']}")
                                        except ValueError:
                                            pass
                                    elif label == "KEY":
                                        result['key'] = value
                                        logger.debug(f"   🎹 Key: {result['key']}")
                                    elif label == "DURATION":
                                        result['duration'] = value
                                        logger.debug(f"   ⏱️ Duration: {result['duration']}")
                            except:
                                continue
                    except Exception as e:
                        logger.debug(f"   Erreur extraction métriques: {e}")
                    
                    # 4. ⬇️ CORRECTION : Extraire Spotify ID depuis le CONTENEUR (pas le lien track)
                    try:
                        # Chercher le lien Spotify dans CE conteneur spécifique
                        spotify_links = container.find_elements(By.CSS_SELECTOR, "a[href*='spotify.com/track/']")
                        if spotify_links:
                            spotify_url = spotify_links[0].get_attribute('href')
                            spotify_id = self._extract_spotify_id_from_url(spotify_url)
                            if spotify_id:
                                result['spotify_id'] = spotify_id
                                result['spotify_url'] = spotify_url
                                logger.debug(f"   🎵 Spotify ID: {spotify_id}")
                    except Exception as e:
                        logger.debug(f"   Erreur extraction Spotify: {e}")
                    
                    # Ajouter le résultat
                    if 'title' in result and 'artist' in result and 'detail_url' in result:
                        results.append(result)
                        logger.info(f"✅ Résultat #{len(results)}: {result['artist']} - {result['title']}")
                        if result.get('spotify_id'):
                            logger.info(f"   🎵 Spotify ID: {result['spotify_id']}")
                    
                except Exception as e:
                    logger.debug(f"Erreur conteneur {container_idx}: {e}")
                    continue
            
            logger.info(f"📊 Total: {len(results)} résultat(s) extrait(s)")
            return results
            
        except Exception as e:
            logger.error(f"❌ Erreur extraction résultats: {e}")
            import traceback
            logger.debug(f"Stacktrace: {traceback.format_exc()}")
            return []
    
    def enrich_track_data(self, track: Track, force_update: bool = False, artist_tracks: Optional[List[Track]] = None) -> bool:
        """
        Enrichit un track avec les données depuis SongBPM
        
        Args:
            track: Le track à enrichir
            force_update: Si True, met à jour même si les données existent déjà
            artist_tracks: Liste de tous les tracks de l'artiste (pour validation Spotify ID)
            
        Returns:
            True si l'enrichissement a réussi
        """
        try:
            artist_name = track.artist.name if hasattr(track.artist, 'name') else str(track.artist)
            
            # Extraire le Spotify ID si disponible
            spotify_id = getattr(track, 'spotify_id', None)
            
            # Rechercher avec le Spotify ID si disponible (récupérer les données de base)
            track_data = self.search_track(track.title, artist_name, spotify_id=spotify_id, fetch_details=False)
            if not track_data:
                return False

            # ÉTAPE 1 : Enrichir avec les DONNÉES DE BASE (toujours disponibles)
            updated = False
            
            # BPM
            if (force_update or not track.bpm) and track_data.get('bpm'):
                track.bpm = track_data['bpm']
                logger.info(f"📊 BPM ajouté depuis SongBPM: {track.bpm} pour {track.title}")
                updated = True
            
            # Key (donnée de base, toujours présente)
            key_value = track_data.get('key')
            if key_value and (force_update or not hasattr(track, 'key') or not track.key):
                track.key = key_value
                logger.info(f"🎵 Key ajoutée depuis SongBPM: {track.key} pour {track.title}")
                updated = True
            
            # Spotify ID depuis SongBPM (avec validation stricte)
            songbpm_spotify_id = track_data.get('spotify_id')
            if songbpm_spotify_id:
                if not hasattr(track, 'spotify_id') or not track.spotify_id:
                    # Valider l'unicité
                    if not artist_tracks or self.validate_spotify_id_unique(songbpm_spotify_id, track, artist_tracks):
                        track.spotify_id = songbpm_spotify_id
                        logger.info(f"🎵 Spotify ID ajouté depuis SongBPM: {track.spotify_id}")
                        updated = True
                    else:
                        logger.warning(f"⚠️ REJET: Spotify ID de SongBPM déjà utilisé: {songbpm_spotify_id}")
            
            # Duration - CONVERSION DE "MM:SS" EN SECONDES
            if (force_update or not hasattr(track, 'duration') or not track.duration):
                if track_data.get('duration'):
                    duration_str = track_data['duration']
                    try:
                        # Convertir "3:53" en secondes (233)
                        if isinstance(duration_str, str) and ':' in duration_str:
                            parts = duration_str.split(':')
                            if len(parts) == 2:
                                minutes = int(parts[0])
                                seconds = int(parts[1])
                                track.duration = minutes * 60 + seconds
                                logger.info(f"⏱️ Duration ajoutée depuis SongBPM: {track.duration}s ({duration_str}) pour {track.title}")
                                updated = True
                        elif isinstance(duration_str, (int, float)):
                            track.duration = int(duration_str)
                            logger.info(f"⏱️ Duration ajoutée depuis SongBPM: {track.duration}s pour {track.title}")
                            updated = True
                    except ValueError as e:
                        logger.warning(f"⚠️ Erreur conversion duration '{duration_str}': {e}")
            
            # ÉTAPE 2 : Essayer de récupérer le MODE (OPTIONNEL, peut timeout)
            detail_url = track_data.get('detail_url')
            if detail_url and key_value:
                logger.info(f"🔍 Tentative de récupération du mode pour '{track.title}'...")
                
                try:
                    # Récupérer les détails avec timeout court
                    details = self._extract_track_details(detail_url, timeout=30)
                    
                    if details and details.get('mode'):
                        mode_value = details['mode']
                        
                        # Stocker le mode
                        if force_update or not hasattr(track, 'mode') or not track.mode:
                            track.mode = mode_value
                            logger.info(f"🎼 Mode ajouté depuis SongBPM: {track.mode} pour {track.title}")
                            updated = True
                    
                    # ⭐ NOUVEAU: Calculer musical_key même si le mode vient de la base de données
                    # Vérifier si on a SOIT récupéré le mode ci-dessus, SOIT s'il existe déjà
                    final_key = getattr(track, 'key', None)
                    final_mode = getattr(track, 'mode', None)
                    
                    if final_key and final_mode:
                        # Calculer musical_key seulement si elle n'existe pas encore
                        if force_update or not hasattr(track, 'musical_key') or not track.musical_key:
                            try:
                                from src.utils.music_theory import key_mode_to_french_from_string
                                track.musical_key = key_mode_to_french_from_string(final_key, final_mode)
                                logger.info(f"🎼 Musical key calculée: {track.musical_key} pour {track.title}")
                                updated = True
                            except Exception as e:
                                logger.warning(f"⚠️ Erreur conversion musical_key: {e}")
                    
                except TimeoutError:
                    logger.warning(f"⏰ Timeout lors de la récupération du mode pour '{track.title}'")
                    # ⭐ MÊME SI TIMEOUT, calculer musical_key si on a déjà key et mode
                    final_key = getattr(track, 'key', None)
                    final_mode = getattr(track, 'mode', None)
                    
                    if final_key and final_mode and (force_update or not hasattr(track, 'musical_key') or not track.musical_key):
                        try:
                            from src.utils.music_theory import key_mode_to_french_from_string
                            track.musical_key = key_mode_to_french_from_string(final_key, final_mode)
                            logger.info(f"🎼 Musical key calculée (fallback après timeout): {track.musical_key}")
                            updated = True
                        except Exception as e:
                            logger.warning(f"⚠️ Erreur conversion musical_key (fallback): {e}")
                    else:
                        logger.warning(f"⚠️ Mode non trouvé dans les détails pour '{track.title}'")
                        
                except TimeoutError:
                    logger.warning(f"⏰ Timeout lors de la récupération du mode pour '{track.title}' - Données de base conservées")
                except Exception as e:
                    logger.warning(f"⚠️ Erreur récupération mode pour '{track.title}': {e} - Données de base conservées")
            
            return updated
            
        except TimeoutError as e:
            logger.error(f"⏰ SongBPM timeout pour {track.title}: {e}")
            return False
        except Exception as e:
            logger.error(f"Erreur SongBPM pour {track.title}: {e}")
            return False


    def close(self):
        """Ferme le driver Selenium"""
        if self.driver:
            try:
                self.driver.quit()
                logger.info("✅ SongBPM: Driver fermé")
            except Exception as e:
                logger.error(f"Erreur fermeture driver SongBPM: {e}")
            finally:
                self.driver = None
                self.wait = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __del__(self):
        """Destructeur pour s'assurer que le driver est fermé"""
        self.close()