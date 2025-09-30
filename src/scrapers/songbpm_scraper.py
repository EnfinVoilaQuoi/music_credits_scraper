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
        
        self._init_driver()

    def _init_driver(self):
        """Initialise le driver Selenium avec configuration robuste"""
        try:
            logger.info(f"Initialisation du driver Selenium (headless={self.headless})...")
            
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
            
            # Préférences pour désactiver les popups et notifications
            prefs = {
                "profile.default_content_setting_values.notifications": 2,
                "profile.default_content_settings.popups": 0,
                "profile.managed_default_content_settings.images": 1  # Garder les images pour SongBPM
            }
            options.add_experimental_option("prefs", prefs)
            
            # Utiliser webdriver_manager pour gérer ChromeDriver automatiquement
            service = ChromeService(ChromeDriverManager().install())
            
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
        
        Args:
            result_title: Titre du résultat
            result_artist: Artiste du résultat
            search_title: Titre recherché
            search_artist: Artiste recherché
            result_spotify_id: ID Spotify du résultat (optionnel)
            search_spotify_id: ID Spotify recherché (optionnel)
            
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
        
        # PRIORITÉ 2 : Matching par nom (fallback si pas de Spotify ID)
        norm_result_title = self._normalize_string(result_title)
        norm_result_artist = self._normalize_string(result_artist)
        norm_search_title = self._normalize_string(search_title)
        norm_search_artist = self._normalize_string(search_artist)
        
        # Vérification stricte : titre ET artiste doivent correspondre
        title_match = norm_result_title == norm_search_title
        artist_match = norm_result_artist == norm_search_artist
        
        logger.debug(f"📝 Comparaison noms: '{norm_result_title}' vs '{norm_search_title}' | "
                    f"'{norm_result_artist}' vs '{norm_search_artist}'")
        logger.debug(f"Match: title={title_match}, artist={artist_match}")
        
        if title_match and artist_match:
            logger.info(f"✅ Match par nom/artiste")
            return True
        
        return False

    def _extract_track_details(self, detail_url: str) -> Dict[str, Any]:
        """
        Extrait les détails complets depuis la page de détail d'un morceau
        
        Args:
            detail_url: URL de la page de détail (ex: https://songbpm.com/@josman/bambi-a9yu5)
            
        Returns:
            Dict avec les détails (mode, energy, danceability, etc.)
        """
        details = {}
        
        try:
            logger.info(f"📄 Navigation vers page de détail: {detail_url}")
            self.driver.get(detail_url)
            
            # Attendre un peu plus longtemps pour être sûr
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
            
            logger.debug(f"Texte extrait (premiers 300 caractères): {full_text[:300]}...")
            
            # Extraire le mode (major/minor) depuis le texte
            # Format: "with a F key and a minor mode"
            import re
            mode_match = re.search(r'with a ([A-G][\#b♯♭]?)\s+key and a (\w+) mode', full_text, re.IGNORECASE)
            if mode_match:
                mode = mode_match.group(2).lower()
                details['mode'] = mode
                logger.info(f"🎵 Mode trouvé: {mode}")
            else:
                logger.warning("⚠️ Mode non trouvé dans le texte")
                # Logger un extrait pour debug
                if 'key and' in full_text.lower():
                    idx = full_text.lower().index('key and')
                    logger.debug(f"Extrait autour de 'key and': ...{full_text[max(0, idx-50):idx+100]}...")
            
            # Extraire les BPM alternatifs (half-time, double-time)
            half_time_match = re.search(r'half-time.*?(\d+)\s*BPM', full_text, re.IGNORECASE)
            if half_time_match:
                details['bpm_half_time'] = int(half_time_match.group(1))
                logger.debug(f"BPM half-time: {details['bpm_half_time']}")
            
            double_time_match = re.search(r'double-time.*?(\d+)\s*BPM', full_text, re.IGNORECASE)
            if double_time_match:
                details['bpm_double_time'] = int(double_time_match.group(1))
                logger.debug(f"BPM double-time: {details['bpm_double_time']}")
            
            # Extraire les caractéristiques (energy, danceability)
            full_text_lower = full_text.lower()
            
            if 'high energy' in full_text_lower:
                details['energy'] = 'high'
                logger.debug("Energy: high")
            elif 'low energy' in full_text_lower:
                details['energy'] = 'low'
                logger.debug("Energy: low")
            elif 'medium energy' in full_text_lower:
                details['energy'] = 'medium'
                logger.debug("Energy: medium")
            
            if 'very danceable' in full_text_lower:
                details['danceability'] = 'very high'
                logger.debug("Danceability: very high")
            elif 'danceable' in full_text_lower:
                details['danceability'] = 'high'
                logger.debug("Danceability: high")
            elif 'not very danceable' in full_text_lower:
                details['danceability'] = 'low'
                logger.debug("Danceability: low")
            
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
            
        except TimeoutException:
            logger.warning(f"⏱️ Timeout lors du chargement de la page de détail")
            return details
        except Exception as e:
            logger.error(f"❌ Erreur extraction détails: {e}")
            import traceback
            logger.debug(f"Stacktrace: {traceback.format_exc()}")
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
            
            # NE PAS utiliser wait.until qui peut timeout
            # À la place, vérifier directement si les éléments sont présents
            time.sleep(1)  # Petit délai pour laisser le DOM se stabiliser
            
            # Stratégie : Chercher les liens <a> qui entourent les cartouches
            # Structure : <a href="/@artiste/titre-slug"> ... </a>
            track_links = self.driver.find_elements(By.CSS_SELECTOR, "a[href*='/@']")
            
            logger.debug(f"📋 Trouvé {len(track_links)} liens potentiels")
            
            if not track_links:
                logger.warning("⚠️ Aucun lien de track trouvé sur la page")
                return []
            
            for link in track_links:
                try:
                    href = link.get_attribute('href')
                    
                    # Vérifier que c'est bien un lien vers une page de track
                    # Format attendu : /@artiste/titre-slug ou /@artiste/titre-slug-id
                    if not href or '/@' not in href:
                        continue
                    
                    # Compter les slashes pour vérifier la structure
                    path = href.split('songbpm.com')[-1] if 'songbpm.com' in href else href
                    if path.count('/') < 2:  # Doit avoir au moins /@artiste/titre
                        continue
                    
                    # Éviter les liens vers des services externes (apple-music, spotify, amazon)
                    if any(service in href.lower() for service in ['/apple-music', '/spotify', '/amazon', '/youtube']):
                        continue
                    
                    result = {'detail_url': href}
                    
                    logger.debug(f"🔗 Analyse du lien: {href}")
                    
                    # Extraire les infos depuis le contenu du lien
                    try:
                        # Chercher les div.flex-1 qui contiennent artiste et titre
                        info_divs = link.find_elements(By.CSS_SELECTOR, "div.flex-1")
                        
                        for info_div in info_divs:
                            paragraphs = info_div.find_elements(By.TAG_NAME, "p")
                            
                            if len(paragraphs) >= 2:
                                # Premier <p> = Artiste (text-sm)
                                # Deuxième <p> = Titre (text-lg)
                                artist_p = paragraphs[0]
                                title_p = paragraphs[1]
                                
                                # Vérifier les classes pour être sûr
                                artist_class = artist_p.get_attribute('class') or ''
                                title_class = title_p.get_attribute('class') or ''
                                
                                if 'text-sm' in artist_class and ('text-lg' in title_class or 'text-2xl' in title_class):
                                    result['artist'] = artist_p.text.strip()
                                    result['title'] = title_p.text.strip()
                                    logger.debug(f"   👤 Artiste: {result['artist']}")
                                    logger.debug(f"   🎵 Titre: {result['title']}")
                                    break
                    except Exception as e:
                        logger.debug(f"   ⚠️ Erreur extraction artiste/titre: {e}")
                        continue
                    
                    # Si on n'a pas trouvé de titre/artiste, passer au suivant
                    if 'title' not in result or 'artist' not in result:
                        logger.debug(f"   ⏭️ Pas de titre/artiste trouvé, skip")
                        continue
                    
                    # Extraire BPM, Key, Duration depuis le lien
                    try:
                        metrics_divs = link.find_elements(
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
                            except Exception as e:
                                logger.debug(f"   ⚠️ Erreur extraction métrique: {e}")
                                continue
                    except Exception as e:
                        logger.debug(f"   ⚠️ Erreur extraction métriques: {e}")
                    
                    # Ajouter le résultat s'il contient au minimum titre, artiste et URL
                    if 'title' in result and 'artist' in result and 'detail_url' in result:
                        results.append(result)
                        logger.info(f"✅ Résultat #{len(results)}: {result['artist']} - {result['title']}")
                        logger.info(f"   🔗 URL: {result['detail_url']}")
                    
                except Exception as e:
                    logger.debug(f"⚠️ Erreur analyse lien: {e}")
                    continue
            
            # Maintenant extraire les Spotify IDs depuis les boutons en dehors des liens principaux
            # Chercher tous les liens Spotify sur la page
            try:
                spotify_links = self.driver.find_elements(By.CSS_SELECTOR, "a[href*='spotify.com/track/']")
                logger.debug(f"🎵 Trouvé {len(spotify_links)} liens Spotify")
                
                # Associer les Spotify IDs aux résultats par ordre (même ordre d'affichage)
                for i, (result, spotify_link) in enumerate(zip(results, spotify_links)):
                    try:
                        spotify_url = spotify_link.get_attribute('href')
                        spotify_id = self._extract_spotify_id_from_url(spotify_url)
                        if spotify_id:
                            result['spotify_id'] = spotify_id
                            result['spotify_url'] = spotify_url
                            logger.debug(f"   Résultat #{i+1}: Spotify ID = {spotify_id}")
                    except:
                        continue
            except Exception as e:
                logger.debug(f"⚠️ Erreur extraction Spotify IDs: {e}")
            
            logger.info(f"📊 Total: {len(results)} résultat(s) extrait(s)")
            return results
            
        except Exception as e:
            logger.error(f"❌ Erreur extraction résultats: {e}")
            import traceback
            logger.debug(f"Stacktrace: {traceback.format_exc()}")
            return []

    def enrich_track_data(self, track: Track) -> bool:
        """
        Enrichit un track avec les données depuis SongBPM
        
        Args:
            track: Le track à enrichir
            
        Returns:
            True si l'enrichissement a réussi
        """
        try:
            artist_name = track.artist.name if hasattr(track.artist, 'name') else str(track.artist)
            
            # Extraire le Spotify ID si disponible
            spotify_id = getattr(track, 'spotify_id', None)
            
            # Rechercher avec le Spotify ID si disponible (et récupérer les détails)
            track_data = self.search_track(track.title, artist_name, spotify_id=spotify_id, fetch_details=True)
            if not track_data:
                return False

            # Enrichir avec les données trouvées
            updated = False
            
            if not track.bpm and track_data.get('bpm'):
                track.bpm = track_data['bpm']
                logger.info(f"📊 BPM ajouté depuis SongBPM: {track.bpm} pour {track.title}")
                updated = True
            
            if not hasattr(track, 'key') or not track.key:
                if track_data.get('key'):
                    track.key = track_data['key']
                    logger.info(f"🎵 Key ajoutée depuis SongBPM: {track.key} pour {track.title}")
                    updated = True
            
            # NOUVEAU : Enrichir avec le mode (major/minor)
            if not hasattr(track, 'mode') or not track.mode:
                if track_data.get('mode'):
                    track.mode = track_data['mode']
                    logger.info(f"🎼 Mode ajouté depuis SongBPM: {track.mode} pour {track.title}")
                    updated = True
            
            if not hasattr(track, 'duration') or not track.duration:
                if track_data.get('duration'):
                    track.duration = track_data['duration']
                    logger.info(f"⏱️ Duration ajoutée depuis SongBPM: {track.duration} pour {track.title}")
                    updated = True
            
            # Stocker le Spotify ID si on ne l'avait pas et qu'on l'a trouvé
            if not spotify_id and track_data.get('spotify_id'):
                track.spotify_id = track_data['spotify_id']
                logger.info(f"🎵 Spotify ID ajouté depuis SongBPM: {track.spotify_id} pour {track.title}")
                updated = True
            
            # Enrichir avec les métadonnées supplémentaires si disponibles
            if track_data.get('energy'):
                if not hasattr(track, 'energy') or not track.energy:
                    track.energy = track_data['energy']
                    logger.info(f"⚡ Energy ajoutée depuis SongBPM: {track.energy} pour {track.title}")
                    updated = True
            
            if track_data.get('danceability'):
                if not hasattr(track, 'danceability') or not track.danceability:
                    track.danceability = track_data['danceability']
                    logger.info(f"💃 Danceability ajoutée depuis SongBPM: {track.danceability} pour {track.title}")
                    updated = True
            
            if track_data.get('time_signature'):
                if not hasattr(track, 'time_signature') or not track.time_signature:
                    track.time_signature = track_data['time_signature']
                    logger.info(f"🎼 Time signature ajoutée depuis SongBPM: {track.time_signature}/4 pour {track.title}")
                    updated = True

            time.sleep(DELAY_BETWEEN_REQUESTS)
            return updated

        except Exception as e:
            logger.error(f"❌ Erreur enrichissement SongBPM pour {track.title}: {e}")
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