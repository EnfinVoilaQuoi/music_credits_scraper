"""Scraper pour récupérer les crédits complets sur Genius"""
import time
import re
import platform
from typing import List, Dict, Any, Optional
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup

from src.config import SELENIUM_TIMEOUT, DELAY_BETWEEN_REQUESTS
from src.models import Track, Credit, CreditRole
from src.utils.logger import get_logger, log_error


logger = get_logger(__name__)


class GeniusScraper:
    """Scraper pour extraire les crédits complets depuis Genius"""
    
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.driver = None
        self.wait = None
        self._init_driver()

    def _init_driver(self):
        """Initialise le driver Selenium - COPIE EXACTE de spotify_id_scraper.py"""
        try:
            logger.info(f"🌐 Initialisation du driver Selenium (headless={self.headless})...")

            options = Options()

            # Mode headless
            if self.headless:
                options.add_argument('--headless=new')

            # Options standards
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-gpu')
            options.add_argument('--window-size=1920,1080')
            options.add_argument('--disable-blink-features=AutomationControlled')
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option('useAutomationExtension', False)

            # User-Agent
            options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

            # Réduire les logs
            options.add_argument('--log-level=3')
            options.add_argument('--silent')
            options.add_experimental_option('excludeSwitches', ['enable-logging'])

            # Désactiver WebGL pour supprimer les messages d'erreur GPU
            options.add_argument('--disable-webgl')
            options.add_argument('--disable-webgl2')
            options.add_argument('--disable-software-rasterizer')
            options.add_argument('--disable-gpu-sandbox')
            options.add_argument('--disable-accelerated-2d-canvas')
            options.add_argument('--disable-accelerated-video-decode')

            # Service avec suppression des logs (EXACTEMENT comme spotify_id_scraper)
            import os
            service = ChromeService(
                ChromeDriverManager().install(),
                log_path=os.devnull  # Utilise le device null du système (NUL sur Windows, /dev/null sur Linux)
            )

            # Désactiver images pour accélérer
            prefs = {
                "profile.managed_default_content_settings.images": 2,
                "profile.default_content_setting_values.notifications": 2,
            }
            options.add_experimental_option("prefs", prefs)

            self.driver = webdriver.Chrome(service=service, options=options)
            self.wait = WebDriverWait(self.driver, SELENIUM_TIMEOUT)

            # Masquer les signes d'automation
            self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

            logger.info("✅ Driver Selenium initialisé avec succès")

        except Exception as e:
            logger.error(f"❌ Erreur initialisation Selenium: {e}")
            self.driver = None
            self.wait = None
            raise
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    def close(self):
        """Ferme le driver et nettoie les ressources"""
        if self.driver:
            try:
                self.driver.quit()
                logger.info("✅ GeniusScraper: Driver fermé")
            except Exception as e:
                logger.debug(f"Erreur lors de la fermeture du driver (normale si déjà fermé): {e}")
            finally:
                self.driver = None
                self.wait = None

    def _handle_cookies(self):
        """Gère les popups de cookies Genius"""
        try:
            logger.debug("🍪 Gestion des cookies...")
            time.sleep(1)

            # Sélecteurs pour les popups de cookies Genius
            cookie_selectors = [
                "button[id='onetrust-accept-btn-handler']",
                "button[data-testid='accept-all-cookies']",
                "button[class*='accept-all']",
                "#onetrust-accept-btn-handler",
                "button[class*='CookieConsentNotice']",
            ]

            for selector in cookie_selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for element in elements:
                        if element.is_displayed() and element.is_enabled():
                            element.click()
                            logger.info(f"✅ Clic sur le bouton cookies: '{element.text[:50]}'")
                            time.sleep(1)
                            return
                except:
                    continue

            logger.debug("✅ Popup cookies fermé ou absent")

        except Exception as e:
            logger.debug(f"Erreur gestion cookies: {e}")

    def scrape_track_credits(self, track: Track) -> List[Credit]:
        """Scrape les crédits complets d'un morceau"""
        if not track.genius_url:
            logger.warning(f"Pas d'URL Genius pour {track.title}")
            return []
        
        credits = []
        
        try:
            logger.info(f"Scraping des crédits pour: {track.title}")
            self.driver.get(track.genius_url)
            
            # Gérer les cookies si nécessaire
            self._handle_cookies()
            
            # Attendre le chargement
            time.sleep(2)
            
            # IMPORTANT : Extraire l'album depuis le header AVANT les crédits
            self._extract_header_metadata(track)
            
            # Si on a trouvé un album dans le header, logger le succès
            if track.album:
                logger.info(f"✅ Album trouvé dans le header: '{track.album}'")
            else:
                logger.info(f"⚠️ Aucun album trouvé dans le header pour: {track.title}")
            
            # Ouvrir la section des crédits - Stratégie multi-niveaux
            credits_opened = False

            # STRATÉGIE 1: Chercher le bouton "Expand" dans la section Credits via XPath
            logger.debug("🔍 Recherche du bouton Expand dans section Credits (stratégie 1)")
            try:
                # Chercher le bouton Expand qui suit la section Credits
                expand_selectors = [
                    # Bouton Expand dans ExpandableContent__ButtonContainer après Credits
                    "//div[contains(@class, 'SongInfo__Title') and text()='Credits']/following::button[contains(text(), 'Expand')]",
                    "//div[contains(@class, 'ExpandableContent__ButtonContainer')]//button[contains(text(), 'Expand')]",
                    # Bouton avec les classes spécifiques
                    "//button[contains(@class, 'ExpandableContent__Button')]",
                    # Fallback : chercher "Expand" dans tout button après un élément contenant Credits
                    "//button[contains(., 'Expand')]",
                ]

                for selector in expand_selectors:
                    try:
                        # Attendre que l'élément soit cliquable (pas juste présent)
                        expand_button = WebDriverWait(self.driver, 5).until(
                            EC.element_to_be_clickable((By.XPATH, selector))
                        )

                        logger.debug(f"✅ Bouton Expand trouvé avec: {selector[:50]}")

                        # Stratégie anti-stale: Re-trouver l'élément juste avant chaque action
                        # Retry avec backoff exponentiel en cas de StaleElementReferenceException
                        max_retries = 3
                        for attempt in range(max_retries):
                            try:
                                # Re-trouver l'élément à chaque tentative pour éviter stale reference
                                button = self.driver.find_element(By.XPATH, selector)

                                # Vérifier qu'il est visible et cliquable
                                if button.is_displayed() and button.is_enabled():
                                    # Scroll vers le bouton (re-trouve l'élément)
                                    button = self.driver.find_element(By.XPATH, selector)
                                    self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
                                    time.sleep(0.3)

                                    # Clic JavaScript (re-trouve l'élément une dernière fois)
                                    button = self.driver.find_element(By.XPATH, selector)
                                    button_text = button.text
                                    self.driver.execute_script("arguments[0].click();", button)
                                    logger.info(f"✅ Bouton Expand cliqué (texte: '{button_text}')")
                                    time.sleep(2)
                                    credits_opened = True
                                    break
                                else:
                                    if attempt < max_retries - 1:
                                        time.sleep(0.5 * (attempt + 1))  # Backoff exponentiel
                                        continue
                            except StaleElementReferenceException:
                                if attempt < max_retries - 1:
                                    logger.debug(f"Élément stale, tentative {attempt + 1}/{max_retries}")
                                    time.sleep(0.5 * (attempt + 1))  # Backoff exponentiel
                                    continue
                                else:
                                    raise

                        if credits_opened:
                            break

                    except TimeoutException:
                        logger.debug(f"Timeout avec sélecteur: {selector[:50]}")
                        continue
                    except StaleElementReferenceException as e:
                        logger.debug(f"Élément resté stale après retries: {selector[:50]}")
                        continue
                    except Exception as e:
                        logger.debug(f"Erreur avec sélecteur {selector[:50]}: {type(e).__name__}")
                        continue

            except Exception as e:
                logger.debug(f"Stratégie 1 échouée: {e}")

            # STRATÉGIE 2: Sélecteurs XPath spécifiques
            if not credits_opened:
                logger.debug("🔍 Recherche du bouton Credits (stratégie 2: sélecteurs XPath)")
                credits_selectors = [
                    "//button[contains(@class, 'ExpandableContent')]//span[contains(text(), 'Credits')]",
                    "//button//span[contains(text(), 'Credits')]",
                    "//button[contains(text(), 'Credits')]",
                    "//*[contains(text(), 'Credits')]/ancestor::button",
                    "//button[contains(@aria-label, 'Credits')]",
                    "//div[contains(@class, 'credits')]//button",
                ]

                for selector in credits_selectors:
                    try:
                        credits_button = WebDriverWait(self.driver, 3).until(
                            EC.element_to_be_clickable((By.XPATH, selector))
                        )
                        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", credits_button)
                        time.sleep(0.5)
                        self.driver.execute_script("arguments[0].click();", credits_button)
                        logger.info(f"✅ Bouton Credits cliqué avec XPath: {selector[:50]}")
                        time.sleep(2)
                        credits_opened = True
                        break
                    except:
                        continue

            # STRATÉGIE 3: Fallback - Chercher un "Expand" après la section Credits
            if not credits_opened:
                logger.debug("🔍 Recherche du bouton Credits (stratégie 3: bouton Expand)")
                expand_button = self._find_expand_button()
                if expand_button:
                    try:
                        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", expand_button)
                        time.sleep(0.5)
                        self.driver.execute_script("arguments[0].click();", expand_button)
                        logger.info("✅ Bouton Expand Credits cliqué (fallback)")
                        time.sleep(2)
                        credits_opened = True
                    except Exception as e:
                        logger.debug(f"Échec clic bouton Expand: {e}")

            if not credits_opened:
                logger.warning("❌ Impossible de trouver/cliquer le bouton Credits avec toutes les stratégies")
                # Essayer de sauvegarder une capture d'écran pour debug
                try:
                    screenshot_path = f"debug_credits_{track.title[:20].replace('/', '_').replace('€', 'E')}.png"
                    self.driver.save_screenshot(screenshot_path)
                    logger.info(f"📸 Screenshot sauvegardé: {screenshot_path}")
                except:
                    pass
                return credits
            
            # Extraire les crédits
            credits = self._extract_credits(track)

            # Ajouter les crédits au track
            for credit in credits:
                track.add_credit(credit)

            # Note: track.credits_scraped est une propriété calculée automatiquement
            # qui retourne len(track.credits), donc pas besoin de l'assigner

            logger.info(f"✅ {len(credits)} crédits trouvés pour {track.title}")

            return credits
            
        except Exception as e:
            logger.error(f"Erreur scraping crédits pour {track.title}: {e}")
            return credits
    
    def _find_expand_button(self) -> Optional[Any]:
        """Trouve le bouton Expand spécifiquement dans la section Credits"""
        try:
            logger.debug("Recherche de tous les boutons Expand...")
            
            expand_selectors = [
                "//button[contains(@class, 'ExpandableContent')]",
                "//div[contains(@class, 'ExpandableContent')]//button",
                "//button[contains(text(), 'Expand')]",
                "div[class*='ExpandableContent'] button",
                "button[class*='ExpandableContent']"
            ]
            
            all_buttons = []
            
            for selector in expand_selectors:
                try:
                    if selector.startswith("//"):
                        buttons = self.driver.find_elements(By.XPATH, selector)
                    else:
                        buttons = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    
                    for button in buttons:
                        if button.is_displayed() and button.is_enabled():
                            if button not in all_buttons:
                                all_buttons.append(button)
                            
                except Exception as e:
                    logger.debug(f"Erreur avec sélecteur {selector}: {e}")
                    continue
            
            logger.debug(f"Trouvé {len(all_buttons)} boutons Expand au total")
            
            # Si on a au moins 2 boutons, prendre le 2ème
            if len(all_buttons) >= 2:
                second_button = all_buttons[1]
                logger.debug("✅ Utilisation du 2ème bouton Expand trouvé")
                return second_button
            
            # Chercher spécifiquement après le header "Credits"
            try:
                credits_header = self.driver.find_element(
                    By.XPATH, 
                    "//div[contains(@class, 'SongInfo__Title') and text()='Credits']"
                )
                
                credits_y = credits_header.location['y']
                
                best_button = None
                best_distance = float('inf')
                
                for button in all_buttons:
                    try:
                        button_y = button.location['y']
                        if button_y > credits_y:
                            distance = button_y - credits_y
                            if distance < best_distance and distance < 500:
                                best_distance = distance
                                best_button = button
                    except:
                        continue
                
                if best_button:
                    logger.debug(f"✅ Bouton le plus proche après Credits sélectionné")
                    return best_button
                    
            except Exception as e:
                logger.debug(f"Erreur lors de la recherche par position: {e}")
            
            if len(all_buttons) == 1:
                logger.debug("⚠️ Un seul bouton Expand trouvé, utilisation par défaut")
                return all_buttons[0]
            
            logger.debug("❌ Aucun bouton Expand Credits approprié trouvé")
            return None
            
        except Exception as e:
            logger.error(f"Erreur lors de la recherche du bouton Expand: {e}")
            return None

    def _is_valid_date(self, text: str) -> bool:
        """Vérifie si le texte est une date valide"""
        import re
        # Patterns pour les dates
        patterns = [
            r'\d{4}',  # Année seule
            r'\d{1,2}/\d{1,2}/\d{4}',  # MM/DD/YYYY
            r'\d{4}-\d{2}-\d{2}',  # YYYY-MM-DD
            r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}'
        ]
        
        for pattern in patterns:
            if re.search(pattern, text):
                return True
        return False

    def _extract_header_metadata(self, track: Track):
        """Extrait les métadonnées depuis le header de la page (album, date, etc.)"""
        try:
            # Chercher l'album dans le header
            album_selectors = [
                "//a[contains(@class, 'PrimaryAlbum')]",
                "//div[contains(@class, 'HeaderMetadata')]//a[contains(@href, '/albums/')]",
                "//span[contains(text(), 'from')]/following-sibling::a",
                "//div[@class='song_album']//a"
            ]
            
            for selector in album_selectors:
                try:
                    album_element = self.driver.find_element(By.XPATH, selector)
                    album_name = album_element.text.strip()
                    if album_name and album_name != track.album:  # Nouvelle info ou différente
                        track.album = album_name
                        logger.info(f"Album trouvé dans le header: '{album_name}'")
                        break
                except:
                    continue
            
            # Chercher la date de sortie si pas déjà présente
            if not track.release_date:
                date_selectors = [
                    "//span[contains(@class, 'metadata_unit-info')]//span[contains(text(), '20')]",
                    "//div[contains(@class, 'HeaderMetadata')]//span[contains(text(), '20')]"
                ]
                
                for selector in date_selectors:
                    try:
                        date_element = self.driver.find_element(By.XPATH, selector)
                        date_text = date_element.text.strip()
                        if date_text and self._is_valid_date(date_text):
                            # Utiliser update_release_date pour garder la date la plus ancienne
                            if track.update_release_date(date_text, source="scraper"):
                                logger.info(f"Date mise à jour depuis header: {date_text}")
                            else:
                                logger.debug(f"Date depuis header ignorée (date existante plus ancienne): {date_text}")
                            break
                    except:
                        continue
                        
        except Exception as e:
            logger.error(f"Erreur extraction métadonnées header: {e}")
            
    def _is_valid_album_name(self, name: str, track_title: str = None) -> bool:
        """Valide si un nom est bien un album et pas un artiste/producteur/titre - VERSION STRICTE"""
        
        if not name or len(name.strip()) < 2:
            return False
        
        name_lower = name.lower().strip()
        
        # RÈGLE 1: Rejeter les noms de producteurs connus
        known_producers = [
            # Producteurs rap FR
            'easy dew', 'easydew', 'pyroman', 'the beatmaker', 'dj bellek', 'skread',
            'noxious', 'ponko', 'ikaz', 'kore', 'therapy', 'katrina', 'katrina squad',
            'benjamin epps', 'epps', 'therapy 2093', 'bbp', 'azaia', 'nouvo', 'junior alaprod',
            'hugz', 'myth syzer', 'vm the don', 'lowkey', 'hologram lo', 'bbp beats',
            
            # Producteurs US célèbres
            'mike dean', 'metro boomin', 'pierre bourne', 'wheezy', 'southside',
            'tm88', 'zaytoven', 'lex luger', 'young chop', 'dj mustard', 'hit-boy',
            'boi-1da', 'noah shebib', '40', 'pharrell', 'the neptunes', 'timbaland',
            'dr. dre', 'dr dre', 'scott storch', 'kanye west', 'j dilla', 'madlib', 
            'alchemist', 'dj premier', 'cashmoneyap', 'tay keith', 'ronnyj', 
            'cubeatz', 'murda beatz', 'london on da track', 'jetsonmade'
        ]
        
        # Vérifier si c'est un producteur connu (correspondance exacte ou partielle)
        for producer in known_producers:
            if producer in name_lower or name_lower in producer:
                logger.debug(f"🚫 Album rejeté car producteur connu: '{name}' (match: {producer})")
                return False
        
        # RÈGLE 2: Rejeter si contient des indicateurs de production
        production_indicators = [
            'prod', 'beat', 'music', 'muzik', 'production', 'records',
            'entertainment', 'ent.', 'studio', 'sound', 'audio'
        ]
        
        for indicator in production_indicators:
            if indicator in name_lower:
                logger.debug(f"🚫 Album rejeté car indicateur de production: '{name}' (indicateur: {indicator})")
                return False
        
        # RÈGLE 3: Si c'est identique au titre du morceau, probablement pas un album
        if track_title and name_lower == track_title.lower().strip():
            logger.debug(f"🚫 Album rejeté car identique au titre: '{name}'")
            return False
        
        # RÈGLE 4: Rejeter les noms trop courts qui ressemblent à des surnoms/pseudos
        if len(name) <= 10:
            # Sauf si contient des indicateurs d'album
            album_indicators = ['vol', 'ep', 'lp', 'mixtape', 'deluxe', 'edition']
            if not any(ind in name_lower for ind in album_indicators):
                logger.debug(f"🚫 Album rejeté car nom trop court sans indicateur: '{name}'")
                return False
        
        # RÈGLE 5: Accepter si contient des indicateurs positifs d'album
        positive_indicators = [
            'vol', 'volume', 'ep', 'lp', 'album', 'deluxe', 'edition',
            'part', 'partie', 'chapter', 'chapitre', 'saison', 'tome',
            'tape', 'mixtape', 'collection', 'anthology'
        ]
        
        for indicator in positive_indicators:
            if indicator in name_lower:
                logger.debug(f"✅ Album accepté car indicateur positif: '{name}' (indicateur: {indicator})")
                return True
        
        # RÈGLE 6: Rejeter si commence par des préfixes typiques de producteurs
        producer_prefixes = ['dj ', 'mc ', 'young ', 'lil ', 'big ']
        if any(name_lower.startswith(prefix) for prefix in producer_prefixes):
            logger.debug(f"🚫 Album rejeté car préfixe de producteur: '{name}'")
            return False
        
        # RÈGLE 7: Rejeter les patterns typiques de noms de producteurs
        # Pattern : Nom + chiffres (ex: "Therapy 2093", "BBP 808")
        import re
        if re.match(r'^[a-z]+\s*\d{2,4}$', name_lower):
            logger.debug(f"🚫 Album rejeté car pattern producteur (nom+chiffres): '{name}'")
            return False
        
        # RÈGLE 8: Si plus de 25 caractères, probablement un vrai album
        if len(name) > 25:
            logger.debug(f"✅ Album accepté car nom long: '{name}'")
            return True
        
        # RÈGLE 9: Par défaut, rejeter si ambigu
        logger.debug(f"⚠️ Album ambigu, rejeté par précaution: '{name}'")
        return False

    
    def _extract_credits(self, track: Track) -> List[Credit]:
        """Extrait les crédits de la page - VERSION SANS EXTRACTION D'ALBUM"""
        credits = []
        
        try:
            time.sleep(1)
            self.wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div[class*='SongInfo__Credit']"))
            )
            soup = BeautifulSoup(self.driver.page_source, 'html.parser')
            
            # Parcourir tous les éléments de crédit
            for credit_element in soup.select("div[class*='SongInfo__Credit']"):
                # Trouver le label (rôle)
                label_div = credit_element.find("div", class_=re.compile(r"SongInfo__Label"))
                if not label_div:
                    continue
                    
                role_text = label_div.get_text(strip=True)
                
                # IMPORTANT: IGNORER LE CHAMP "ALBUM" DANS LES CRÉDITS
                # Car on l'extrait déjà depuis le header avec _extract_header_metadata
                if role_text.lower() == 'album':
                    logger.debug(f"⚠️ Champ 'Album' ignoré dans les crédits (déjà extrait du header)")
                    continue  # Passer au crédit suivant
                
                # Traiter les autres cas normalement
                container_div = label_div.find_next_sibling("div")
                if not container_div:
                    continue
                
                names = self._extract_names_intelligently(container_div)
                
                # Gérer la date de sortie
                if role_text.lower() == 'released on':
                    if names:
                        date_text = ' '.join(names)
                        parsed_date = self._parse_release_date(date_text)
                        if parsed_date:
                            # Utiliser update_release_date pour garder la date la plus ancienne
                            if track.update_release_date(parsed_date, source="scraper"):
                                logger.debug(f"📅 Date de sortie mise à jour: {date_text}")
                            else:
                                logger.debug(f"📅 Date de sortie ignorée (date existante plus ancienne): {date_text}")
                    continue
                
                # Gérer le genre
                elif role_text.lower() == 'genre':
                    if names and not track.genre:
                        track.genre = ', '.join(names)
                        logger.debug(f"🎵 Genre scrapé: {track.genre}")
                    continue
                
                # Créer les crédits normaux
                role_enum = self._map_genius_role_to_enum(role_text)
                if not role_enum:
                    role_enum = CreditRole.OTHER
                
                for name in names:
                    if name and len(name.strip()) > 0:
                        credit = Credit(
                            name=name.strip(),
                            role=role_enum,
                            role_detail=role_text if role_enum == CreditRole.OTHER else None,
                            source="genius"
                        )
                        credits.append(credit)
                        logger.debug(f"Crédit créé: {name.strip()} - {role_enum.value}")
            
            # Dédoublonner et retourner
            return self._deduplicate_credits(credits)
            
        except Exception as e:
            logger.error(f"Erreur lors de l'extraction des crédits: {e}")
            return credits
    
    def _extract_names_intelligently(self, container_div) -> List[str]:
        """Extrait les noms depuis le conteneur de manière intelligente - MÉTHODE RESTAURÉE"""
        names = []
        
        try:
            # Récupérer les noms depuis les liens <a>
            for link in container_div.select("a"):
                name = link.get_text(strip=True)
                if name and name not in names:
                    names.append(name)
            
            # Récupérer le texte brut (hors liens) et le nettoyer
            for text_node in container_div.find_all(text=True, recursive=False):
                text = text_node.strip()
                if text and text not in names:
                    # Séparer par différents délimiteurs
                    for separator in [' & ', ', ', ' and ', ' + ', ' / ']:
                        if separator in text:
                            parts = text.split(separator)
                            for part in parts:
                                clean_part = part.strip()
                                if clean_part and clean_part not in names:
                                    names.append(clean_part)
                            break
                    else:
                        # Pas de séparateur trouvé, ajouter le texte tel quel
                        if text not in names:
                            names.append(text)
            
            # Nettoyer les noms (enlever &amp; etc.)
            cleaned_names = []
            for name in names:
                cleaned = name.replace('&amp;', '&').strip()
                if cleaned and len(cleaned) > 1:
                    cleaned_names.append(cleaned)
            
            return cleaned_names
            
        except Exception as e:
            logger.debug(f"Erreur lors de l'extraction intelligente des noms: {e}")
            return []
    
    def _parse_release_date(self, date_text: str) -> Optional[datetime]:
        """Parse une date de sortie depuis le texte"""
        try:
            date_patterns = [
                "%B %d, %Y",      # September 14, 2018
                "%B %d %Y",       # September 14 2018 (sans virgule)
                "%b %d, %Y",      # Sep 14, 2018
                "%b %d %Y",       # Sep 14 2018 (sans virgule)
                "%Y-%m-%d",       # 2018-09-14
                "%d/%m/%Y",       # 14/09/2018
                "%m/%d/%Y",       # 09/14/2018
                "%Y",             # 2018 (année seule)
            ]

            date_text = date_text.strip()
            
            for pattern in date_patterns:
                try:
                    return datetime.strptime(date_text, pattern)
                except ValueError:
                    continue
                    
            logger.debug(f"⚠️ Format de date non reconnu: {date_text}")
            return None

        except Exception as e:
            logger.debug(f"Erreur lors du parsing de la date: {e}")
            return None

    def _map_genius_role_to_enum(self, genius_role: str) -> Optional[CreditRole]:
        """Mappe un rôle Genius vers notre énumération"""
        role_mapping = {
            # Production
            'Producer': CreditRole.PRODUCER,
            'Co-Producer': CreditRole.CO_PRODUCER,
            'Executive Producer': CreditRole.EXECUTIVE_PRODUCER,
            'Vocal Producer': CreditRole.VOCAL_PRODUCER,
            'Additional Production': CreditRole.ADDITIONAL_PRODUCTION,
        
            # Écriture
            'Writer': CreditRole.WRITER,
            'Songwriter': CreditRole.WRITER,
            'Composer': CreditRole.COMPOSER,
            'Lyricist': CreditRole.LYRICIST,
        
            # Studio
            'Mixing Engineer': CreditRole.MIXING_ENGINEER,
            'Mix Engineer': CreditRole.MIXING_ENGINEER,
            'Mastering Engineer': CreditRole.MASTERING_ENGINEER,
            'Recording Engineer': CreditRole.RECORDING_ENGINEER,
            'Engineer': CreditRole.ENGINEER,
        
            # Chant
            'Vocals': CreditRole.VOCALS,
            'Lead Vocals': CreditRole.LEAD_VOCALS,
            'Background Vocals': CreditRole.BACKGROUND_VOCALS,
            'Additional Vocals': CreditRole.ADDITIONAL_VOCALS,
            'Choir': CreditRole.CHOIR,
        
            # Label et édition
            'Label': CreditRole.LABEL,
            'Publisher': CreditRole.PUBLISHER,
            'Distributor': CreditRole.DISTRIBUTOR,

            # Instruments
            'Guitar': CreditRole.GUITAR,
            'Bass Guitar': CreditRole.BASS_GUITAR,
            'Acoustic Guitar': CreditRole.ACOUSTIC_GUITAR,
            'Electric Guitar': CreditRole.ELECTRIC_GUITAR,
            'Drums': CreditRole.DRUMS,
            'Piano': CreditRole.PIANO,
            'Keyboard': CreditRole.KEYBOARD,
            'Synthesizer': CreditRole.SYNTHESIZER,
            'Bass': CreditRole.BASS,

            # Artwork
            'Art Direction': CreditRole.ART_DIRECTION,
            'Artwork': CreditRole.ARTWORK,
            'Graphic Design': CreditRole.GRAPHIC_DESIGN,
            'Photography': CreditRole.PHOTOGRAPHY,
            'Illustration': CreditRole.ILLUSTRATION,

            # Crédits vidéo
            'Video Director': CreditRole.VIDEO_DIRECTOR,
            'Video Producer': CreditRole.VIDEO_PRODUCER,
            'Video Director of Photography': CreditRole.VIDEO_DIRECTOR_OF_PHOTOGRAPHY,
            'Video Cinematographer': CreditRole.VIDEO_CINEMATOGRAPHER,
            'Video Digital Imaging Technician': CreditRole.VIDEO_DIGITAL_IMAGING_TECHNICIAN,
            'Video Camera Operator': CreditRole.VIDEO_CAMERA_OPERATOR,
            'Video Drone Operator': CreditRole.VIDEO_DRONE_OPERATOR,
            'Video Set Decorator': CreditRole.VIDEO_SET_DECORATOR,
            'Video Editor': CreditRole.VIDEO_EDITOR,
            'Video Colorist': CreditRole.VIDEO_COLORIST,

            # Autres
            'Featuring': CreditRole.FEATURED,
            'Sample': CreditRole.SAMPLE,
            'A&R': CreditRole.A_AND_R,
        }
    
        # Recherche exacte d'abord
        if genius_role in role_mapping:
            return role_mapping[genius_role]
    
        # Recherche insensible à la casse
        genius_role_lower = genius_role.lower()
        for key, value in role_mapping.items():
            if key.lower() == genius_role_lower:
                return value
    
        # Recherche partielle pour les rôles complexes
        if 'producer' in genius_role_lower:
            if 'co' in genius_role_lower:
                return CreditRole.CO_PRODUCER
            elif 'executive' in genius_role_lower:
                return CreditRole.EXECUTIVE_PRODUCER
            elif 'vocal' in genius_role_lower:
                return CreditRole.VOCAL_PRODUCER
            else:
                return CreditRole.PRODUCER
    
        if 'engineer' in genius_role_lower:
            if 'mix' in genius_role_lower:
                return CreditRole.MIXING_ENGINEER
            elif 'master' in genius_role_lower:
                return CreditRole.MASTERING_ENGINEER
            elif 'record' in genius_role_lower:
                return CreditRole.RECORDING_ENGINEER
            else:
                return CreditRole.ENGINEER
    
        if 'vocal' in genius_role_lower:
            if 'lead' in genius_role_lower:
                return CreditRole.LEAD_VOCALS
            elif 'background' in genius_role_lower or 'backing' in genius_role_lower:
                return CreditRole.BACKGROUND_VOCALS
            elif 'additional' in genius_role_lower:
                return CreditRole.ADDITIONAL_VOCALS
            else:
                return CreditRole.VOCALS

        if 'guitar' in genius_role_lower:
            if 'bass' in genius_role_lower:
                return CreditRole.BASS_GUITAR
            elif 'acoustic' in genius_role_lower:
                return CreditRole.ACOUSTIC_GUITAR
            elif 'electric' in genius_role_lower:
                return CreditRole.ELECTRIC_GUITAR
            else:
                return CreditRole.GUITAR

        # Gestion spéciale des rôles vidéo
        if 'video' in genius_role_lower:
            if 'director' in genius_role_lower and 'photography' in genius_role_lower:
                return CreditRole.VIDEO_DIRECTOR_OF_PHOTOGRAPHY
            elif 'director' in genius_role_lower:
                return CreditRole.VIDEO_DIRECTOR
            elif 'producer' in genius_role_lower:
                return CreditRole.VIDEO_PRODUCER
            elif 'cinematographer' in genius_role_lower:
                return CreditRole.VIDEO_CINEMATOGRAPHER
            elif 'camera' in genius_role_lower:
                return CreditRole.VIDEO_CAMERA_OPERATOR
            elif 'drone' in genius_role_lower:
                return CreditRole.VIDEO_DRONE_OPERATOR
            elif 'editor' in genius_role_lower:
                return CreditRole.VIDEO_EDITOR
            elif 'colorist' in genius_role_lower:
                return CreditRole.VIDEO_COLORIST
            elif 'set decorator' in genius_role_lower:
                return CreditRole.VIDEO_SET_DECORATOR

        return CreditRole.OTHER

    def _deduplicate_credits(self, credits: List[Credit]) -> List[Credit]:
        """Supprime les doublons de crédits"""
        seen = set()
        unique_credits = []
    
        for credit in credits:
            key = (credit.name.lower().strip(), credit.role.value)
        
            if key not in seen:
                seen.add(key)
                unique_credits.append(credit)
            else:
                logger.debug(f"Doublon ignoré: {credit.name} - {credit.role.value}")
    
        return unique_credits

    def _debug_no_credits_found(self, soup):
        """Debug quand aucun crédit n'est trouvé"""
        logger.debug("🔍 DEBUG: Aucun crédit trouvé, analyse de la structure...")

        # Chercher tous les éléments qui pourraient contenir des crédits
        potential_containers = [
            soup.find_all('div', class_=lambda x: x and 'SongInfo' in x),
            soup.find_all('div', class_=lambda x: x and 'Credit' in x),
            soup.find_all('div', class_=lambda x: x and 'ExpandableContent' in x)
        ]

        for i, containers in enumerate(potential_containers):
            logger.debug(f"Méthode {i+1}: Trouvé {len(containers)} éléments potentiels")
            for j, container in enumerate(containers[:3]):  # Limiter à 3 pour éviter le spam
                text = container.get_text(strip=True)[:100]
                classes = container.get('class', [])
                logger.debug(f"  Élément {j+1}: classes={classes}, texte='{text}...'")

    def get_album_url_from_track(self, track_url: str) -> Optional[str]:
        """Récupère l'URL de l'album depuis une page de morceau"""
        try:
            self.driver.get(track_url)
            time.sleep(1)
            
            # Chercher le lien vers l'album
            album_link = self.driver.find_element(
                By.XPATH, 
                "//a[contains(@class, 'PrimaryAlbum__Title') or contains(@href, '/albums/')]"
            )
            
            if album_link:
                return album_link.get_attribute('href')
                
        except NoSuchElementException:
            logger.debug("Pas de lien album trouvé")
        except Exception as e:
            logger.error(f"Erreur lors de la récupération de l'URL album: {e}")
        
        return None
    
    def scrape_multiple_tracks(self, tracks: List[Track], progress_callback=None) -> Dict[str, Any]:
        """Scrape plusieurs morceaux avec rapport de progression"""
        results = {
            'success': 0,
            'failed': 0,
            'errors': [],
            'albums_scraped': set()
        }
        
        total = len(tracks)
        album_credits_cache = {}
        
        for i, track in enumerate(tracks):
            try:
                logger.info(f"Scraping du morceau {i+1}/{total}: {track.title}")
                
                # Scraper les crédits du morceau
                credits = self.scrape_track_credits(track)
                
                # Vérifier le succès
                if len(track.credits) > 0:
                    results['success'] += 1
                    logger.info(f"✅ {len(track.credits)} crédits extraits pour {track.title}")
                else:
                    results['failed'] += 1
                    logger.warning(f"❌ Aucun crédit extrait pour {track.title}")
                
                # Callback de progression
                if progress_callback:
                    progress_callback(i + 1, total, track.title)
                    
            except Exception as e:
                results['failed'] += 1
                error_msg = f"Erreur sur {track.title}: {str(e)}"
                results['errors'].append({
                    'track': track.title,
                    'error': str(e)
                })
                logger.error(error_msg)
                track.scraping_errors.append(str(e))
        
        logger.info(f"Scraping terminé: {results['success']} réussis, {results['failed']} échoués")
        return results
    
    def scrape_track_lyrics(self, track: Track) -> str:
        """Scrape les paroles d'un morceau et les anecdotes"""
        if not track.genius_url:
            logger.warning(f"Pas d'URL Genius pour {track.title}")
            return ""

        lyrics = ""

        try:
            logger.info(f"Scraping des paroles pour: {track.title}")
            self.driver.get(track.genius_url)

            # Gestion des cookies (si nécessaire)
            try:
                WebDriverWait(self.driver, 5).until(
                    EC.presence_of_element_located((By.ID, "onetrust-banner-sdk"))
                )
                accept_btn = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((By.ID, "onetrust-accept-btn-handler"))
                )
                accept_btn.click()
                time.sleep(1)
            except TimeoutException:
                pass

            # Attendre que les paroles se chargent
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "[data-lyrics-container='true']"))
            )

            # Attendre un peu pour que toute la page se charge
            time.sleep(1)

            # Récupérer le HTML
            soup = BeautifulSoup(self.driver.page_source, 'html.parser')

            # ÉTAPE 1: Extraire l'anecdote/bio depuis la section "About"
            anecdotes_text = None
            try:
                # Chercher directement dans la section About (pas besoin de cliquer sur Read More)
                # Le contenu est déjà dans le DOM
                bio_selectors = [
                    'div.SongDescription__Content-sc-634b42e-2',  # Sélecteur exact du HTML fourni
                    'div.RichText__Container-sc-4013e6a2-0',
                    'div[class*="SongDescription__Content"]',
                    'div[class*="RichText__Container"]',
                ]

                for bio_selector in bio_selectors:
                    bio_container = soup.select_one(bio_selector)
                    if bio_container:
                        # Extraire uniquement le texte (sans les iframes/embeds)
                        # Supprimer les éléments embed avant d'extraire le texte
                        for embed in bio_container.find_all('div', class_=lambda x: x and 'embedly' in x):
                            embed.decompose()

                        anecdotes_text = bio_container.get_text(separator='\n\n', strip=True)
                        if anecdotes_text and len(anecdotes_text) > 50:
                            # Nettoyer les éléments parasites
                            import re
                            anecdotes_text = re.sub(r'\s+', ' ', anecdotes_text)  # Normaliser les espaces
                            anecdotes_text = anecdotes_text.strip()

                            track.anecdotes = anecdotes_text
                            logger.info(f"📝 Anecdote extraite ({len(anecdotes_text)} caractères): {anecdotes_text[:80]}...")
                            break

                if not anecdotes_text:
                    logger.debug("⚠️ Aucune anecdote trouvée dans la section About")

            except Exception as e:
                logger.debug(f"Erreur extraction anecdote: {e}")

            # ÉTAPE 2: Chercher les conteneurs de paroles
            lyrics_containers = soup.find_all('div', {'data-lyrics-container': 'true'})

            if lyrics_containers:
                lyrics_parts = []
                for container in lyrics_containers:
                    # Extraire le texte en préservant les sauts de ligne
                    text = container.get_text(separator='\n', strip=True)
                    if text:
                        lyrics_parts.append(text)

                lyrics = '\n\n'.join(lyrics_parts)

                # ÉTAPE 1: Nettoyer d'abord les artefacts (tags [Paroles de...], etc.)
                lyrics = self._clean_lyrics(lyrics)

                # ÉTAPE 2: Si on a des anecdotes, les retirer des paroles
                if anecdotes_text:
                    import re
                    # Méthode 1: Chercher le premier tag de structure [Couplet], [Intro], [Partie X], etc.
                    first_tag = re.search(r'\[(?:Intro|Couplet|Refrain|Verse|Chorus|Bridge|Hook|Pre-Chorus|Partie|Part|Outro|Interlude)', lyrics, re.IGNORECASE)

                    if first_tag:
                        # Tout avant le premier tag est considéré comme anecdote/intro
                        lyrics = lyrics[first_tag.start():].strip()
                        logger.debug(f"🧹 Anecdote retirée des paroles (méthode tag structure)")
                    else:
                        # Méthode 2: Retirer l'anecdote si elle apparaît au début
                        # Normaliser les espaces pour comparaison
                        anecdote_normalized = re.sub(r'\s+', ' ', anecdotes_text[:150])
                        lyrics_normalized = re.sub(r'\s+', ' ', lyrics[:200])

                        if anecdote_normalized in lyrics_normalized:
                            # Trouver où l'anecdote se termine (après les premiers 200 caractères environ)
                            cut_point = lyrics.find('\n\n', len(anecdotes_text) - 50)
                            if cut_point > 0:
                                lyrics = lyrics[cut_point + 2:].strip()
                                logger.debug(f"🧹 Anecdote retirée des paroles (méthode texte)")
                            else:
                                logger.debug("⚠️ Point de coupure introuvable, anecdote conservée")

                logger.info(f"✅ Paroles récupérées pour {track.title} ({len(lyrics.split())} mots)")
            else:
                logger.warning(f"⚠️ Conteneur de paroles non trouvé pour {track.title}")

            # Respecter le rate limit
            time.sleep(DELAY_BETWEEN_REQUESTS)

        except TimeoutException:
            logger.warning(f"Timeout lors du scraping des paroles de {track.title}")
        except Exception as e:
            logger.error(f"Erreur lors du scraping des paroles: {e}")

        return lyrics

    def _extract_anecdotes(self, text: str) -> tuple:
        """
        Extrait les anecdotes et informations supplémentaires du texte des paroles

        Returns:
            tuple: (lyrics_cleaned, anecdotes)
        """
        import re

        anecdotes = []
        lyrics_paragraphs = []

        # Séparer par double saut de ligne (paragraphes)
        paragraphs = text.split('\n\n')

        for paragraph in paragraphs:
            paragraph = paragraph.strip()
            if not paragraph:
                continue

            # Si le paragraphe est long et ne ressemble pas à des paroles
            if len(paragraph) > 100 and not self._is_lyrics_paragraph(paragraph):
                anecdotes.append(paragraph)
                logger.debug(f"📝 Anecdote détectée : {paragraph[:50]}...")
            else:
                lyrics_paragraphs.append(paragraph)

        # Reconstruire les paroles nettoyées
        lyrics_cleaned = '\n\n'.join(lyrics_paragraphs)
        anecdotes_text = '\n\n'.join(anecdotes) if anecdotes else None

        return lyrics_cleaned, anecdotes_text

    def _is_lyrics_paragraph(self, text: str) -> bool:
        """Détermine si un paragraphe est des paroles ou une anecdote"""
        import re

        # Indicateurs de paroles
        lyrics_indicators = [
            r'\[.*?\]',  # Tags comme [Couplet 1], [Refrain]
            r'^\(',      # Commence par parenthèse (annotations)
        ]

        for indicator in lyrics_indicators:
            if re.search(indicator, text):
                return True

        # Si le texte contient beaucoup de phrases complètes et de ponctuation
        # c'est probablement une anecdote
        sentences = text.count('.') + text.count('!') + text.count('?')
        if sentences > 2:  # Plus de 2 phrases = probablement anecdote
            return False

        return True  # Par défaut, considérer comme paroles

    def _clean_lyrics(self, lyrics: str) -> str:
        """Nettoie les paroles en gardant la mise en page originale - VERSION AMÉLIORÉE"""
        if not lyrics:
            return ""

        import re

        # 1: Supprimer uniquement les éléments parasites spécifiques

        # Supprimer la section contributors au début
        lyrics = re.sub(r'^.*?Contributors.*?Lyrics\s*', '', lyrics, flags=re.DOTALL | re.MULTILINE)

        # Supprimer TOUTES les sections [Paroles de "titre"] (début ET milieu du texte, même sur plusieurs lignes)
        # Utiliser un lookbehind négatif pour éviter de capturer les ] internes
        # On cherche [Paroles de... jusqu'à un ] qui n'est PAS suivi d'un newline puis "ft."
        def remove_paroles_tag(text):
            """Supprime le tag [Paroles de...] même s'il contient des brackets internes"""
            # Chercher le début du tag
            start = text.find('[Paroles de')
            if start == -1:
                return text

            # Chercher le ] de fermeture en comptant les brackets imbriqués
            bracket_count = 1
            i = start + len('[Paroles de')

            while i < len(text) and bracket_count > 0:
                if text[i] == '[':
                    bracket_count += 1
                elif text[i] == ']':
                    bracket_count -= 1
                i += 1

            # Supprimer le tag complet
            if bracket_count == 0:
                logger.debug(f"🔍 Tag [Paroles de...] supprimé: {text[start:i][:80]}...")
                return text[:start] + text[i:]

            return text

        lyrics = remove_paroles_tag(lyrics)

        # Supprimer "You might also like" et les suggestions
        lyrics = re.sub(r'You might also like.*?(?=\[|$)', '', lyrics, flags=re.DOTALL | re.MULTILINE)

        # Supprimer les lignes "123Embed" ou "Embed"
        lyrics = re.sub(r'\n\d*Embed', '', lyrics, flags=re.MULTILINE)

        # Supprimer "See [Language] Translations"
        lyrics = re.sub(r'\nSee.*?Translations', '', lyrics, flags=re.MULTILINE)

        # NOUVEAU: Fusionner les tags sur plusieurs lignes [Refrain : SDM &\nJosman\n] -> [Refrain : SDM & Josman]
        # Approche itérative pour gérer tous les cas de newlines dans les brackets
        while True:
            new_lyrics = re.sub(r'\[([^\[\]]*?)\n([^\[\]]*?)\]', r'[\1 \2]', lyrics)
            if new_lyrics == lyrics:
                break
            lyrics = new_lyrics

        # 2: CORRECTION - Reconstituer les lignes correctement
        
        lines = lyrics.split('\n')
        cleaned_lines = []
        i = 0
        
        while i < len(lines):
            current_line = lines[i].rstrip()
            
            # Si c'est une ligne vide, la garder
            if not current_line.strip():
                cleaned_lines.append('')
                i += 1
                continue
            
            # Vérifier si la ligne suivante est une annotation qui doit être rattachée
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                
                # Cas 1: Annotations entre parenthèses (Yeah), (Fort, fort, mhh), etc.
                if re.match(r'^\([^)]*\)$', next_line):
                    current_line += f" {next_line}"
                    i += 1  # Sauter la ligne suivante car fusionnée
                
                # Cas 2: Annotations simples comme "Yeah", "Mhh" seules sur leur ligne
                elif re.match(r'^(Yeah|Mhh|Ahh|Oh|Wow|Ay|Ey|Hum|Hmm|Fort|Oui|Non)$', next_line, re.IGNORECASE):
                    current_line += f" ({next_line})"
                    i += 1  # Sauter la ligne suivante car fusionnée
            
            cleaned_lines.append(current_line)
            i += 1
        
        lyrics = '\n'.join(cleaned_lines)
        
        # 3: Nettoyer les espaces et retours à la ligne excessifs
        
        # Réduire les retours à la ligne multiples (plus de 2) à maximum 2
        lyrics = re.sub(r'\n\s*\n\s*\n+', '\n\n', lyrics)
        
        # Nettoyer les espaces en fin de lignes seulement
        lines = lyrics.split('\n')
        cleaned_lines = [line.rstrip() for line in lines]
        lyrics = '\n'.join(cleaned_lines)
        
        # Supprimer les lignes vides en début et fin
        lyrics = lyrics.strip()
        
        return lyrics

    def scrape_multiple_tracks_with_lyrics(self, tracks: List[Track], progress_callback=None, include_lyrics=True) -> Dict[str, Any]:
        """Scrape plusieurs morceaux avec option paroles - VERSION SIMPLIFIÉE"""
        results = {
            'success': 0,
            'failed': 0,
            'errors': [],
            'albums_scraped': set(),
            'lyrics_scraped': 0
        }
        
        total = len(tracks)
        
        for i, track in enumerate(tracks):
            try:
                logger.info(f"Scraping du morceau {i+1}/{total}: {track.title}")
                
                # Scraper les crédits (méthode existante)
                credits = self.scrape_track_credits(track)
                
                # Scraper les paroles si demandé
                if include_lyrics:
                    try:
                        lyrics = self.scrape_track_lyrics(track)
                        if lyrics:
                            track.lyrics = lyrics
                            track.has_lyrics = True
                            track.lyrics_scraped_at = datetime.now()
                            results['lyrics_scraped'] += 1
                            logger.info(f"✅ Paroles récupérées pour {track.title}")
                        else:
                            track.has_lyrics = False
                            
                    except Exception as lyrics_error:
                        logger.warning(f"Erreur paroles pour {track.title}: {lyrics_error}")
                        track.has_lyrics = False
                
                # Vérifier le succès
                if len(track.credits) > 0:
                    results['success'] += 1
                    logger.info(f"✅ {len(track.credits)} crédits extraits pour {track.title}")
                else:
                    results['failed'] += 1
                    logger.warning(f"❌ Aucun crédit extrait pour {track.title}")
                
                # Callback de progression
                if progress_callback:
                    status = f"Crédits + paroles: {track.title}" if include_lyrics else f"Crédits: {track.title}"
                    progress_callback(i + 1, total, status)
                    
            except Exception as e:
                results['failed'] += 1
                error_msg = f"Erreur sur {track.title}: {str(e)}"
                results['errors'].append({
                    'track': track.title,
                    'error': str(e)
                })
                logger.error(error_msg)
                track.scraping_errors.append(str(e))
        
        logger.info(f"Scraping terminé: {results['success']} réussis, {results['failed']} échoués")
        if include_lyrics:
            logger.info(f"Paroles: {results['lyrics_scraped']} récupérées")

        return results

    def scrape_lyrics_batch(self, tracks: List[Track], progress_callback=None) -> Dict[str, Any]:
        """Scrape uniquement les paroles de plusieurs morceaux (sans les crédits)"""
        results = {
            'success': 0,
            'failed': 0,
            'errors': [],
            'lyrics_scraped': 0
        }

        total = len(tracks)

        for i, track in enumerate(tracks):
            try:
                logger.info(f"Scraping des paroles {i+1}/{total}: {track.title}")

                # Scraper uniquement les paroles
                lyrics = self.scrape_track_lyrics(track)
                if lyrics:
                    track.lyrics = lyrics
                    track.has_lyrics = True
                    track.lyrics_scraped_at = datetime.now()
                    results['lyrics_scraped'] += 1
                    results['success'] += 1
                    logger.info(f"✅ Paroles récupérées pour {track.title}")
                else:
                    track.has_lyrics = False
                    results['failed'] += 1
                    logger.warning(f"❌ Aucune parole trouvée pour {track.title}")

                # Callback de progression
                if progress_callback:
                    progress_callback(i + 1, total, track.title)

            except Exception as e:
                results['failed'] += 1
                error_msg = f"Erreur paroles sur {track.title}: {str(e)}"
                results['errors'].append({
                    'track': track.title,
                    'error': str(e)
                })
                logger.error(error_msg)
                track.has_lyrics = False

        logger.info(f"Scraping paroles terminé: {results['lyrics_scraped']} récupérées, {results['failed']} échoués")
        return results