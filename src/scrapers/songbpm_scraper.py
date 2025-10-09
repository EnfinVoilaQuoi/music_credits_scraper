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

            # ⭐ CRÉER LE DRIVER
            self.driver = webdriver.Chrome(service=service, options=options)
            self.wait = WebDriverWait(self.driver, 10)

            # ⭐ IMPORTANT : Configurer les timeouts APRÈS création du driver
            self.driver.set_page_load_timeout(30)  # Timeout de chargement de page
            self.driver.set_script_timeout(30)  # Timeout d'exécution de scripts

            # ⭐ CRITICAL : Patcher le RemoteConnection pour réduire le timeout HTTP à 30s
            # C'est ici que se fait vraiment le timeout HTTP !
            try:
                from selenium.webdriver.remote.remote_connection import RemoteConnection
                from urllib3.util.timeout import Timeout

                # Remplacer le timeout par défaut dans le RemoteConnection du driver
                if hasattr(self.driver.command_executor, '_client_config'):
                    # Accéder au client_config et le modifier
                    self.driver.command_executor._client_config.timeout = 30
                    logger.debug("✅ Timeout HTTP du driver configuré à 30s via _client_config")

                # Alternative : Modifier directement la pool urllib3 du driver
                if hasattr(self.driver.command_executor, '_conn'):
                    # Créer un nouveau timeout
                    new_timeout = Timeout(connect=30, read=30)
                    self.driver.command_executor._conn.timeout = new_timeout
                    logger.debug("✅ Timeout HTTP du driver configuré à 30s via _conn")
            except Exception as e:
                logger.warning(f"⚠️ Impossible de patcher le timeout HTTP: {e}")

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

    def _normalize_title_for_matching(self, title: str) -> str:
        """
        Normalise un titre pour le matching en enlevant les featurings
        
        Args:
            title: Titre à normaliser
            
        Returns:
            Titre normalisé sans featuring
        """
        import re
        
        # Enlever les variations de featuring
        patterns_to_remove = [
            r'\s*\(feat\.?\s+[^)]+\)',  # (feat. Artist)
            r'\s*\(ft\.?\s+[^)]+\)',    # (ft. Artist)
            r'\s*feat\.?\s+.+$',        # feat. Artist (en fin de titre)
            r'\s*ft\.?\s+.+$',          # ft. Artist (en fin de titre)
            r'\s*\[feat\.?\s+[^\]]+\]', # [feat. Artist]
            r'\s*\[ft\.?\s+[^\]]+\]',   # [ft. Artist]
        ]
        
        normalized = title
        for pattern in patterns_to_remove:
            normalized = re.sub(pattern, '', normalized, flags=re.IGNORECASE)
        
        return normalized.strip()

    def _match_track(self, result_title: str, result_artist: str, 
                 search_title: str, search_artist: str,
                 result_spotify_id: Optional[str] = None,
                 search_spotify_id: Optional[str] = None) -> bool:
        """
        Vérifie si un résultat correspond au morceau recherché - VERSION AMÉLIORÉE
        
        Stratégie :
        1. Si les deux Spotify IDs sont présents → matching strict par Spotify ID
        2. Sinon → matching par titre (sans featuring) + artiste
        
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
            else:
                # Si on a les deux IDs mais qu'ils ne correspondent pas, c'est un REJET
                logger.info(f"❌ REJET: Spotify IDs différents")
                return False
        
        # PRIORITÉ 2 : Matching par nom (fallback si pas les DEUX Spotify IDs)
        # ⭐ AMÉLIORATION : Normaliser les titres en enlevant les featurings
        norm_result_title = self._normalize_string(self._normalize_title_for_matching(result_title))
        norm_search_title = self._normalize_string(self._normalize_title_for_matching(search_title))
        
        norm_result_artist = self._normalize_string(result_artist)
        norm_search_artist = self._normalize_string(search_artist)
        
        # Vérification : titre (sans feat) ET artiste doivent correspondre
        title_match = norm_result_title == norm_search_title
        artist_match = norm_result_artist == norm_search_artist
        
        logger.debug(f"🔍 Comparaison noms:")
        logger.debug(f"   Titres originaux: '{result_title}' vs '{search_title}'")
        logger.debug(f"   Titres (sans feat): '{norm_result_title}' vs '{norm_search_title}' → {title_match}")
        logger.debug(f"   Artistes: '{norm_result_artist}' vs '{norm_search_artist}' → {artist_match}")
        
        # ⭐ AMÉLIORATION : Accepter si titre correspond (sans feat) ET (artiste correspond OU on a le même Spotify ID)
        if title_match and artist_match:
            logger.info(f"✅ Match par titre (sans featuring) + artiste")
            return True
        elif title_match and result_spotify_id and search_spotify_id and result_spotify_id == search_spotify_id:
            # Cas particulier : titre identique + même Spotify ID (ex: Guy2Bezbar Figaro feat. Josman)
            logger.info(f"✅ Match par titre + Spotify ID (featuring ignoré)")
            return True
        
        logger.info(f"❌ REJET: Pas de correspondance")
        return False

    def _extract_track_details(self, detail_url: str, timeout: int = 30) -> Dict[str, Any]:
        """
        Extrait les détails complets depuis la page de détail d'un morceau
        """
        details = {}
        
        try:
            logger.info(f"📄 Navigation vers page de détail: {detail_url}")
            
            # Définir un timeout court pour le driver
            self.driver.set_page_load_timeout(timeout)
            self.driver.get(detail_url)
            
            # Attendre un peu
            time.sleep(2)
            
            # Récupérer le contenu principal
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
                try:
                    full_text = self.driver.find_element(By.TAG_NAME, "body").text
                    logger.debug("Utilisation du body complet pour extraction")
                except:
                    logger.error("❌ Impossible de récupérer le texte de la page")
                    return details
            
            import re
            
            # Nettoyer le texte
            clean_text = re.sub(r'\s+', ' ', full_text)
            clean_text = clean_text.replace('\xa0', ' ')
            
            # ⭐ AMÉLIORATION 1 : Extraction du MODE avec plusieurs patterns
            mode = None
            
            # Pattern 1 : Avec la key (ex: "with a C♯/D♭ key and a major mode")
            mode_match = re.search(
                r'key\s+and\s+a\s+(major|minor)\s+mode',
                clean_text,
                re.IGNORECASE
            )
            
            if mode_match:
                mode = mode_match.group(1).lower()
                logger.info(f"🎵 Mode trouvé (pattern 1): {mode}")
            else:
                # Pattern 2 : Juste "major" ou "minor" avec "mode" (plus permissif)
                mode_match2 = re.search(
                    r'\b(major|minor)\s+mode\b',
                    clean_text,
                    re.IGNORECASE
                )
                if mode_match2:
                    mode = mode_match2.group(1).lower()
                    logger.info(f"🎵 Mode trouvé (pattern 2): {mode}")
            
            if mode:
                details['mode'] = mode
            else:
                logger.warning("⚠️ Mode non trouvé dans le texte")
                logger.debug(f"🔍 Extrait recherché: {clean_text[max(0, clean_text.lower().find('key')-50):clean_text.lower().find('key')+100] if 'key' in clean_text.lower() else clean_text[:200]}")
            
            # ⭐ AMÉLIORATION 2 : Extraction de la KEY depuis le paragraphe (fallback)
            # Si on n'a pas déjà la key, essayer de l'extraire du texte
            key_match = re.search(
                r'with\s+a\s+([A-G][\#b♯♭/]+)\s+key',
                clean_text,
                re.IGNORECASE
            )
            if key_match:
                key_found = key_match.group(1).strip()
                details['key_from_paragraph'] = key_found
                logger.info(f"🎵 Key trouvée dans paragraphe: {key_found}")
            
            # Extraire time signature
            time_sig_match = re.search(
                r'time\s+signature\s+of\s+(\d+)\s+beats?\s+per\s+bar',
                clean_text,
                re.IGNORECASE
            )
            if time_sig_match:
                details['time_signature'] = int(time_sig_match.group(1))
                logger.info(f"🎵 Time signature trouvée: {details['time_signature']}")
            
            # Logs détaillés
            logger.info(f"✅ Détails extraits: {len(details)} attributs")
            logger.info(f"📊 Détails: {details}")
            
            return details
            
        except TimeoutError:
            logger.warning(f"⏰ Timeout ({timeout}s) lors de la récupération des détails")
            raise
        except Exception as e:
            error_str = str(e)
            # Détecter les erreurs de timeout HTTP ou de session invalide
            if "Read timed out" in error_str or "HTTPConnectionPool" in error_str:
                logger.error(f"❌ Erreur extraction détails: HTTPConnectionPool timeout détecté: {e}")
                # Marquer le driver comme invalide
                self.driver = None
                self.wait = None
            elif "invalid session id" in error_str.lower() or "session deleted" in error_str.lower():
                logger.warning("⚠️ Session invalide lors de l'extraction des détails")
                self.driver = None
                self.wait = None
            else:
                logger.error(f"❌ Erreur extraction détails: {e}")
                import traceback
                logger.debug(traceback.format_exc())
            return details

    def search_track(self, track_title: str, artist_name: str, 
                spotify_id: Optional[str] = None,
                max_results_to_check: int = 5,
                fetch_details: bool = True) -> Optional[Dict[str, Any]]:
        """
        Recherche un morceau sur SongBPM et récupère ses informations
        """
        # S'assurer que le driver est initialisé
        self._ensure_driver()
        
        if not self.driver:
            logger.error("❌ SongBPM: Driver non initialisé")
            return None

        try:
            if spotify_id:
                logger.info(f"🔍 SongBPM: Recherche '{track_title}' par {artist_name} (Spotify ID: {spotify_id})")
            else:
                logger.info(f"🔍 SongBPM: Recherche '{track_title}' par {artist_name}")
            
            # ⭐ IMPORTANT : Définir un timeout strict pour la navigation
            self.driver.set_page_load_timeout(30)  # 30 secondes max pour charger une page
            
            # 1. Aller sur la page d'accueil
            try:
                self.driver.get(self.base_url)
                time.sleep(1)
            except TimeoutException:
                logger.error(f"⏰ TIMEOUT lors du chargement de la page d'accueil SongBPM")
                return None
            
            # 2. Gérer le popup de cookies
            try:
                self._handle_cookies()
            except Exception as e:
                logger.warning(f"⚠️ Erreur gestion cookies: {e}")
                # On continue même si les cookies posent problème
            
            # 3. Recherche
            try:
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
                
            except TimeoutException:
                logger.error(f"⏰ TIMEOUT lors de la recherche sur SongBPM")
                self._reset_driver_on_error()
                return None
            except Exception as e:
                logger.error(f"❌ Erreur lors de la saisie de la recherche: {e}")
                self._reset_driver_on_error()
                return None
            
            # 4. Attendre les résultats (avec timeout)
            try:
                time.sleep(3)  # Attendre que les résultats se chargent
                
                # Vérifier que des résultats sont présents
                result_selectors = [
                    "div.bg-card",
                    "a[href*='/@']",
                ]
                
                results_found = False
                for selector in result_selectors:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    if elements:
                        logger.debug(f"✅ Trouvé {len(elements)} éléments avec sélecteur: {selector}")
                        results_found = True
                        break
                
                if not results_found:
                    logger.warning(f"❌ Aucun résultat trouvé pour '{track_title}' par {artist_name}")
                    return None
                
            except Exception as e:
                logger.error(f"❌ Erreur lors de la vérification des résultats: {e}")
                return None
            
            # 5. Extraire les résultats
            results = self._get_search_results()
            
            if not results:
                logger.warning(f"❌ SongBPM: Aucun résultat extrait pour '{track_title}'")
                return None
            
            # 6. Vérifier les résultats (jusqu'à max_results_to_check)
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
            self._reset_driver_on_error()
            log_api("SongBPM", f"search/{track_title}", False)
            return None
        except Exception as e:
            # Détecter les timeouts HTTP (ReadTimeoutError) et les erreurs de connexion
            error_str = str(e)
            if "Read timed out" in error_str or "HTTPConnectionPool" in error_str:
                logger.error(f"❌ SongBPM: HTTP timeout détecté: {e}")
                # ⭐ NE PAS réinitialiser le driver car il a déjà été fermé par le timeout
                # Juste réinitialiser les références
                self.driver = None
                self.wait = None
            elif "invalid session id" in error_str.lower() or "session deleted" in error_str.lower():
                # Le driver a été fermé de force (par le timeout)
                logger.warning("⚠️ Driver fermé de force, session invalide")
                self.driver = None
                self.wait = None
            else:
                logger.error(f"❌ SongBPM: Erreur lors de la recherche: {e}")
                self._reset_driver_on_error()
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
        """
        try:
            # S'assurer que le driver est initialisé
            self._ensure_driver()
            
            artist_name = track.artist.name if hasattr(track.artist, 'name') else str(track.artist)
            spotify_id = getattr(track, 'spotify_id', None)
            
            # ⭐ AMÉLIORATION : Timeout plus court pour search_track (30s au lieu de défaut)
            try:
                # Rechercher avec timeout court
                track_data = self.search_track(
                    track.title, 
                    artist_name, 
                    spotify_id=spotify_id, 
                    fetch_details=False
                )
            except TimeoutException as e:
                logger.error(f"⏰ TIMEOUT lors de la recherche SongBPM pour '{track.title}': {e}")
                return False
            except Exception as e:
                logger.error(f"❌ Erreur recherche SongBPM pour '{track.title}': {e}")
                return False
            
            if not track_data:
                logger.warning(f"⚠️ Aucune donnée trouvée sur SongBPM pour '{track.title}'")
                return False

            # ÉTAPE 1 : Enrichir avec les DONNÉES DE BASE
            updated = False
            
            # BPM
            if (force_update or not track.bpm) and track_data.get('bpm'):
                track.bpm = track_data['bpm']
                logger.info(f"📊 BPM ajouté depuis SongBPM: {track.bpm} pour {track.title}")
                updated = True
            
            # Key (donnée de base)
            key_value = track_data.get('key')
            if key_value and (force_update or not hasattr(track, 'key') or not track.key):
                track.key = key_value
                logger.info(f"🎵 Key ajoutée depuis SongBPM: {track.key} pour {track.title}")
                updated = True
            
            # Spotify ID (avec validation)
            songbpm_spotify_id = track_data.get('spotify_id')
            if songbpm_spotify_id:
                if not hasattr(track, 'spotify_id') or not track.spotify_id:
                    if not artist_tracks or self.validate_spotify_id_unique(songbpm_spotify_id, track, artist_tracks):
                        track.spotify_id = songbpm_spotify_id
                        logger.info(f"🎵 Spotify ID ajouté depuis SongBPM: {track.spotify_id}")
                        updated = True
            
            # Duration - CONVERSION DE "MM:SS" EN SECONDES
            if (force_update or not hasattr(track, 'duration') or not track.duration):
                if track_data.get('duration'):
                    duration_str = track_data['duration']
                    try:
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
            
            # ÉTAPE 2 : Essayer de récupérer le MODE (avec timeout strict de 30s)
            detail_url = track_data.get('detail_url')
            if detail_url and key_value:
                logger.info(f"🔍 Tentative de récupération du mode pour '{track.title}'...")
                
                try:
                    # ⭐ IMPORTANT : Timeout strict de 30 secondes
                    details = self._extract_track_details(detail_url, timeout=30)
                    
                    if details:
                        # Mode
                        if details.get('mode'):
                            mode_value = details['mode']
                            if force_update or not hasattr(track, 'mode') or not track.mode:
                                track.mode = mode_value
                                logger.info(f"🎼 Mode ajouté depuis SongBPM: {track.mode} pour {track.title}")
                                updated = True
                        
                        # ⭐ NOUVEAU : Utiliser la key du paragraphe si pas encore de key
                        if details.get('key_from_paragraph') and (force_update or not hasattr(track, 'key') or not track.key):
                            track.key = details['key_from_paragraph']
                            logger.info(f"🎵 Key ajoutée depuis paragraphe: {track.key} pour {track.title}")
                            updated = True
                        
                        # Calculer musical_key si on a key ET mode
                        final_key = getattr(track, 'key', None)
                        final_mode = getattr(track, 'mode', None)
                        
                        if final_key and final_mode:
                            if force_update or not hasattr(track, 'musical_key') or not track.musical_key:
                                try:
                                    from src.utils.music_theory import key_mode_to_french_from_string
                                    track.musical_key = key_mode_to_french_from_string(final_key, final_mode)
                                    logger.info(f"🎼 Musical key calculée: {track.musical_key} pour {track.title}")
                                    updated = True
                                except Exception as e:
                                    logger.warning(f"⚠️ Erreur conversion musical_key: {e}")
                    
                except TimeoutException:
                    logger.warning(f"⏰ TIMEOUT (30s) lors de la récupération du mode pour '{track.title}' - Données de base conservées")
                    # ⭐ IMPORTANT : On continue avec les données de base même si timeout
                except Exception as e:
                    logger.warning(f"⚠️ Erreur récupération mode pour '{track.title}': {e} - Données de base conservées")
            
            return updated
            
        except TimeoutException as e:
            logger.error(f"⏰ SongBPM TIMEOUT pour {track.title}: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ SongBPM ERREUR pour {track.title}: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return False

    def _reset_driver_on_error(self):
        """Réinitialise le driver en cas d'erreur"""
        try:
            if self.driver:
                logger.warning("⚠️ Réinitialisation du driver après erreur")
                try:
                    self.driver.quit()
                except Exception as e:
                    # Ignorer les erreurs si le driver est déjà fermé
                    logger.debug(f"Erreur fermeture driver (déjà fermé?): {e}")
                finally:
                    self.driver = None
                    self.wait = None

                # Attendre un peu avant de réinitialiser
                time.sleep(1)

                # NE PAS réinitialiser automatiquement - laissez _ensure_driver le faire
                # self._init_driver()
        except Exception as e:
            logger.error(f"Erreur lors du reset du driver: {e}")
            self.driver = None
            self.wait = None

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