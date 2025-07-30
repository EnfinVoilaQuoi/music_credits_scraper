"""Scraper pour récupérer les crédits complets sur Genius"""
import time
import re
import subprocess
from typing import List, Dict, Any, Optional
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException
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
        self._init_driver()
    
    def _init_driver(self):
        """Initialise le driver Selenium"""
        try:
            options = Options()
            if self.headless:
                options.add_argument('--headless=new')
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-gpu')
            options.add_argument('--window-size=1920,1080')
            options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
            
            # Options pour réduire les messages d'erreur
            options.add_argument('--log-level=3')  # Ne montrer que les erreurs fatales
            options.add_argument('--disable-logging')
            options.add_argument('--silent')
            options.add_experimental_option('excludeSwitches', ['enable-logging'])
            options.add_experimental_option('useAutomationExtension', False)
            
            # Désactiver les fonctionnalités inutiles
            options.add_argument('--disable-blink-features=AutomationControlled')
            options.add_argument('--disable-features=VizDisplayCompositor')
            options.add_argument('--disable-background-networking')
            
            # Désactiver les images pour accélérer
            prefs = {
                "profile.managed_default_content_settings.images": 2,
                "profile.default_content_setting_values.notifications": 2,
                "profile.default_content_settings.popups": 0
            }
            options.add_experimental_option("prefs", prefs)
            
            # Essayer d'abord le driver local
            from pathlib import Path
            local_driver = Path(__file__).parent.parent.parent / "drivers" / "chromedriver.exe"
            
            # Chercher aussi dans un sous-dossier (pour les nouvelles versions)
            if not local_driver.exists():
                for path in (Path(__file__).parent.parent.parent / "drivers").rglob("chromedriver.exe"):
                    local_driver = path
                    break
            
            if local_driver.exists():
                logger.info(f"Utilisation du ChromeDriver local: {local_driver}")
                service = ChromeService(str(local_driver))
            else:
                logger.info("ChromeDriver local non trouvé, utilisation de webdriver-manager")
                service = ChromeService(ChromeDriverManager().install())
            
            # Configurer le service pour être silencieux
            service.log_output = subprocess.DEVNULL
            
            self.driver = webdriver.Chrome(service=service, options=options)
            self.wait = WebDriverWait(self.driver, SELENIUM_TIMEOUT)
            
            logger.info("Driver Selenium initialisé avec succès")
            
        except Exception as e:
            logger.error(f"Erreur lors de l'initialisation du driver: {e}")
            raise
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    def close(self):
        """Ferme le driver"""
        if self.driver:
            self.driver.quit()
            logger.info("Driver Selenium fermé")
    
    def scrape_track_credits(self, track: Track) -> List[Credit]:
        """Scrape les crédits complets d'un morceau"""
        if not track.genius_url:
            logger.warning(f"Pas d'URL Genius pour {track.title}")
            return []
    
        credits = []
    
        try:
            logger.info(f"Scraping des crédits pour: {track.title}")
            self.driver.get(track.genius_url)

            # Initialiser les variables pour les métadonnées
            self._current_release_date = None
            self._current_album = None
            self._current_genre = None

            # Log l'URL effective
            logger.debug(f"URL visitée : {self.driver.current_url}")

            # Gestion des cookies
            WebDriverWait(self.driver, 5).until(
                EC.presence_of_element_located((By.ID, "onetrust-banner-sdk"))
            )
            try:
                accept_btn = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((By.ID, "onetrust-accept-btn-handler"))
                )
                accept_btn.click()
                logger.debug("✅ Cookies acceptés")
                time.sleep(1)
            except TimeoutException:
                logger.debug("⚠️ Pas de bannière cookies")

            # Aller a "Credits"
            credits_header = WebDriverWait(self.driver, 5).until(
                EC.presence_of_element_located((By.XPATH, "//div[contains(@class, 'SongInfo__Title') and text()='Credits']"))
            )
            if not credits_header:
                logger.error("❌ Aucun header Credits trouvé")
                return credits
            self.driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", credits_header)
            time.sleep(2)

            # Bouton "Expand"
            expand_button = self._find_expand_button()
            if expand_button:
                # Scroller vers le bouton pour s'assurer qu'il est visible
                self.driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", expand_button)
                time.sleep(1)
            
                # Cliquer sur le bouton
                self.driver.execute_script("arguments[0].click();", expand_button)
                logger.debug("✅ Bouton Expand cliqué")
            
                # Attendre que le contenu étendu soit visible
                try:
                    WebDriverWait(self.driver, 10).until(
                        lambda driver: len(driver.find_elements(By.CSS_SELECTOR, "div[class*='SongInfo__Credit']")) > 0
                    )
                    time.sleep(2)
                    logger.debug("Bouton Expand cliqué")
                except TimeoutException:
                    logger.warning("Timeout en attendant le contenu étendu")
            else:
                logger.debug("Aucun bouton Expand trouvé")

            # Extraire les crédits
            credits = self._extract_credits()

            # Mettre à jour les métadonnées si elles ont été capturées
            if hasattr(self, '_current_release_date') and self._current_release_date:
                if not track.release_date:  # Ne pas écraser si déjà définie
                    track.release_date = self._current_release_date
                    logger.debug(f"📅 Date de sortie ajoutée au track: {self._current_release_date.strftime('%Y-%m-%d')}")
        
            if hasattr(self, '_current_album') and self._current_album:
                if not track.album:  # Ne pas écraser si déjà défini
                    track.album = self._current_album
                    logger.debug(f"💿 Album ajouté au track: {self._current_album}")
        
            if hasattr(self, '_current_genre') and self._current_genre:
                if not track.genre:  # Ne pas écraser si déjà défini
                    track.genre = self._current_genre
                    logger.debug(f"🎵 Genre ajouté au track: {self._current_genre}")

            # Debug : afficher le HTML de la zone des crédits
            if not credits:
                logger.debug("Aucun crédit extrait, analyse du HTML...")
                try:
                    soup = BeautifulSoup(self.driver.page_source, 'html.parser')
                    credits_areas = soup.find_all('div', class_=lambda x: x and 'SongInfo' in x)
                    logger.debug(f"Trouvé {len(credits_areas)} zones SongInfo")
                
                    for i, area in enumerate(credits_areas[:3]):  # Limiter à 3 pour ne pas spammer
                        logger.debug(f"Zone {i}: {area.get_text()[:200]}...")
                except Exception as e:
                    logger.debug(f"Erreur lors de l'analyse debug: {e}")

            # Mettre à jour le track
            track.last_scraped = datetime.now()
            for credit in credits:
                track.add_credit(credit)

            logger.info(f"{len(credits)} crédits extraits pour {track.title}")

        except TimeoutException:
            error_msg = f"Timeout lors du scraping de {track.title}"
            logger.error(error_msg)
            track.scraping_errors.append(error_msg)
            log_error(track.title, error_msg, "genius_scraper")

        except Exception as e:
            error_msg = f"Erreur lors du scraping: {str(e)}"
            logger.error(f"{error_msg} pour {track.title}")
            track.scraping_errors.append(error_msg)
            log_error(track.title, error_msg, "genius_scraper")

        # Respecter le rate limit
        time.sleep(DELAY_BETWEEN_REQUESTS)

        return credits
    
    def _find_expand_button(self) -> Optional[Any]:
        """Trouve le bouton Expand spécifiquement dans la section Credits"""
    
        # Stratégie 1: Chercher le bouton Expand dans le conteneur Credits spécifique
        credits_expand_selectors = [
            # Sélecteur très spécifique basé sur la structure complète
            "div.About__Container-sc-6e5dc9c5-1 div.ExpandableContent__ButtonContainer-sc-8775ac96-3 button",

            # Alternatifs avec classes partielles
            "div[class*='About__Container'] div[class*='ExpandableContent__ButtonContainer'] button",
            "div[class*='ExpandableContent__Container'] div[class*='ExpandableContent__ButtonContainer'] button",

            # XPath pour chercher après avoir trouvé "Credits"
            "//div[contains(@class, 'SongInfo__Title') and text()='Credits']/ancestor::div[contains(@class, 'ExpandableContent')]//button",
            "//div[text()='Credits']/following-sibling::*//button[contains(@class, 'ExpandableContent')]",
            "//div[text()='Credits']/ancestor::*[contains(@class, 'ExpandableContent')]//button",

            # Chercher dans le conteneur qui contient "Credits"
            "//div[.//div[text()='Credits']]//button[contains(@class, 'ExpandableContent')]",
            "//div[.//div[text()='Credits']]//div[contains(@class, 'ButtonContainer')]//button",
        ]

        for i, selector in enumerate(credits_expand_selectors):
            try:
                if selector.startswith("//"):
                    # XPath selector
                    buttons = self.driver.find_elements(By.XPATH, selector)
                else:
                    # CSS selector
                    buttons = self.driver.find_elements(By.CSS_SELECTOR, selector)

                logger.debug(f"Sélecteur Credits {i+1} ({selector}): {len(buttons)} boutons trouvés")

                for button in buttons:
                    try:
                        if button.is_displayed() and button.is_enabled():
                            button_text = button.text.strip()
                            logger.debug(f"Bouton Credits trouvé: '{button_text}'")

                            # Vérifier que le bouton est dans la bonne zone (près de Credits)
                            if self._is_button_near_credits(button):
                                logger.debug(f"✅ Bouton Expand Credits sélectionné: '{button_text}'")
                                return button
                            else:
                                logger.debug(f"⚠️ Bouton ignoré (pas près de Credits): '{button_text}'")

                    except Exception as e:
                        logger.debug(f"Erreur lors de la vérification du bouton Credits: {e}")
                        continue

            except Exception as e:
                logger.debug(f"Erreur avec sélecteur Credits {i+1}: {e}")
                continue
    
        # Stratégie 2: Si pas trouvé, chercher tous les boutons Expand et prendre le 2ème
        logger.debug("Recherche du 2ème bouton Expand (fallback)")
        try:
            all_expand_buttons = self.driver.find_elements(
                By.XPATH, 
                "//button[contains(@class, 'ExpandableContent') or contains(text(), 'Expand')]"
            )

            logger.debug(f"Trouvé {len(all_expand_buttons)} boutons Expand au total")

            if len(all_expand_buttons) >= 2:
                second_button = all_expand_buttons[1]  # Index 1 = 2ème bouton
                if second_button.is_displayed() and second_button.is_enabled():
                    logger.debug("✅ Utilisation du 2ème bouton Expand trouvé")
                    return second_button

        except Exception as e:
            logger.debug(f"Erreur lors de la recherche du 2ème bouton: {e}")

        logger.debug("❌ Aucun bouton Expand Credits trouvé")
        return None

    def _is_button_near_credits(self, button) -> bool:
        """Vérifie si un bouton est proche de la section Credits"""
        try:
            # Chercher si le bouton est dans un conteneur qui contient "Credits"
            parent = button.find_element(By.XPATH, "./ancestor-or-self::*[contains(., 'Credits')]")
            if parent:
                parent_text = parent.text
                # Vérifier que c'est bien la section Credits et pas juste un mot "credits" ailleurs
                if 'Credits' in parent_text and ('Producer' in parent_text or 'Writer' in parent_text or 'Label' in parent_text):
                    logger.debug("Bouton trouvé dans la section Credits")
                    return True
        except:
            pass
            
        try:
            # Alternative: vérifier la position relative par rapport au header Credits
            credits_header = self.driver.find_element(
                By.XPATH, 
                "//div[contains(@class, 'SongInfo__Title') and text()='Credits']"
            )

            # Calculer les positions
            button_location = button.location['y']
            credits_location = credits_header.location['y']

            # Le bouton doit être après le header Credits (position Y plus grande)
            # et pas trop loin (maximum 500px de différence)
            if credits_location < button_location < credits_location + 500:
                logger.debug(f"Bouton positionné après Credits (Credits: {credits_location}, Bouton: {button_location})")
                return True

        except Exception as e:
            logger.debug(f"Erreur lors de la vérification de position: {e}")

        return False
        
    def _extract_credits(self) -> List[Credit]:
        """Extrait les crédits de la page"""
        credits = []
        
        try:
            # Attendre que les crédits soient visibles
            time.sleep(1)
            self.wait.until(
                EC.presence_of_element_located((By.CLASS_NAME, "SongInfo__Credit"))
            )
            
            # Obtenir le HTML de la page
            soup = BeautifulSoup(self.driver.page_source, 'html.parser')
            
            # 1. MÉTHODE PRINCIPALE : Structure HTML exacte de Genius
            # Chercher le conteneur principal des crédits
            credits_container = soup.find('div', class_='SongInfo__Columns-sc-4162678b-2')
            
            if not credits_container:
                # Fallback avec classes partielles
                credits_container = soup.find('div', class_=lambda x: x and 'SongInfo__Columns' in x)

            if credits_container:
                logger.debug("✅ Conteneur de crédits trouvé")
                credits.extend(self._extract_from_genius_structure(credits_container))
            else:
                logger.warning("❌ Conteneur de crédits non trouvé")

            # 2. MÉTHODE ALTERNATIVE : Si pas de conteneur, chercher les crédits individuels
            if not credits:
                logger.debug("Recherche des crédits individuels...")
                credit_elements = soup.find_all('div', class_=lambda x: x and 'SongInfo__Credit' in x)

                if credit_elements:
                    logger.debug(f"Trouvé {len(credit_elements)} éléments de crédit individuels")
                    for element in credit_elements:
                        credit = self._parse_genius_credit_element(element)
                        if credit:
                            credits.append(credit)
        
            # Dédoublonner les crédits
            credits = self._deduplicate_credits(credits)
        
            logger.info(f"Extraction terminée : {len(credits)} crédits uniques trouvés")
        
            # Debug si aucun crédit trouvé
            if not credits:
                self._debug_no_credits_found(soup)

        except TimeoutException:
            logger.warning("Timeout en attendant les crédits")
        except Exception as e:
            logger.error(f"Erreur lors de l'extraction des crédits: {e}")
        
        return credits
    
    def _extract_from_genius_structure(self, container) -> List[Credit]:
        """Extrait les crédits depuis le conteneur Genius avec la structure HTML réelle"""
        credits = []
    
        try:
            # Chercher tous les éléments de crédit dans le conteneur
            credit_elements = container.find_all('div', class_=lambda x: x and 'SongInfo__Credit' in x)
        
            logger.debug(f"Trouvé {len(credit_elements)} éléments de crédit dans le conteneur")
        
            for element in credit_elements:
                credit = self._parse_genius_credit_element(element)
                if credit:
                    credits.append(credit)
                    logger.debug(f"Crédit ajouté: {credit.name} - {credit.role.value}")
    
        except Exception as e:
            logger.error(f"Erreur dans _extract_from_genius_structure: {e}")
    
        return credits

    def _parse_genius_credit_element(self, element) -> Optional[Credit]:
        """Parse un élément de crédit selon la structure HTML de Genius"""
        try:
            # Chercher le label (rôle)
            label_element = element.find('div', class_=lambda x: x and 'SongInfo__Label' in x)
            if not label_element:
                return None
        
            role_text = label_element.get_text(strip=True)
        
            # Chercher le contenu (nom/valeur)
            # Le contenu est dans le div suivant le label
            content_divs = element.find_all('div')
            content_element = None
        
            # Trouver le div qui contient le contenu (pas le label)
            for div in content_divs:
                if div != label_element:
                    div_text = div.get_text(strip=True)
                    if div_text and div_text != role_text:  # S'assurer que ce n'est pas le label
                        content_element = div
                        break
        
            if not content_element:
                logger.debug(f"Pas de contenu trouvé pour le rôle: {role_text}")
                return None
        
            # Extraire le nom/valeur
            content_text = content_element.get_text(strip=True)
        
            # Nettoyer le contenu (enlever &amp; etc.)
            content_text = content_text.replace('&amp;', '&').replace('&', ',')
        
            logger.debug(f"Parsing crédit: {role_text} = {content_text}")
        
            # Traiter les cas spéciaux (métadonnées utiles)
            if role_text.lower() == 'released on':
                # Capturer la date de sortie et l'ajouter au track si possible
                self._handle_release_date(content_text)
                return None  # Ne pas créer de crédit pour la date

            if role_text.lower() == 'album':
                # Capturer l'info d'album si disponible
                self._handle_album_info(content_text)
                return None

            if role_text.lower() == 'genre':
                # Capturer l'info de genre si disponible
                self._handle_genre_info(content_text)
                return None

            # Mapper le rôle Genius vers notre enum
            role = self._map_genius_role_to_enum(role_text)
            
            if not role:
                logger.debug(f"Rôle non mappé: {role_text}")
                # Créer un crédit avec rôle OTHER pour ne pas perdre l'info
                role = CreditRole.OTHER

            # Extraire les noms (peut y en avoir plusieurs séparés par &, virgules, etc.)
            names = self._extract_names_from_genius_content(content_text)
        
            # Créer des crédits pour tous les noms trouvés
            created_credits = []
            for name in names:
                if name:  # S'assurer que le nom n'est pas vide
                    credit = Credit(
                        name=name,
                        role=role,
                        role_detail=role_text if role == CreditRole.OTHER else None,
                        source="genius"
                    )
                    created_credits.append(credit)
                    logger.debug(f"Crédit créé: {name} - {role.value}")

            # Retourner le premier crédit (les autres seront traités dans la boucle principale)
            return created_credits[0] if created_credits else None
    
        except Exception as e:
            logger.debug(f"Erreur lors du parsing d'un élément de crédit: {e}")
    
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

        # Si aucune correspondance, retourner OTHER
        logger.debug(f"Rôle non reconnu: {genius_role}")
        return CreditRole.OTHER

    def _extract_names_from_genius_content(self, content: str) -> List[str]:
        """Extrait les noms depuis le contenu d'un crédit Genius"""
        if not content:
            return []

        names = []

        # Séparer par différents délimiteurs
        separators = [' & ', ', ', ' and ', ' + ', ' / ']
        current_parts = [content]

        for sep in separators:
            new_parts = []
            for part in current_parts:
                new_parts.extend(part.split(sep))
            current_parts = new_parts

        # Nettoyer chaque nom
        for part in current_parts:
            cleaned = part.strip()
            if cleaned and len(cleaned) > 1:
                # Enlever les caractères HTML résiduels
                cleaned = cleaned.replace('&amp;', '&')
                names.append(cleaned)

        return names

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

    def _handle_album_info(self, album_text: str):
        """Traite et stocke l'info d'album trouvée dans les crédits"""
        try:
            album_name = album_text.strip()
            if album_name:
                self._current_album = album_name
                logger.debug(f"💿 Info album capturée: {album_name}")
        except Exception as e:
            logger.debug(f"Erreur lors du traitement de l'album: {e}")

    def _handle_genre_info(self, genre_text: str):
        """Traite et stocke l'info de genre trouvée dans les crédits"""
        try:
            genre_name = genre_text.strip()
            if genre_name:
                self._current_genre = genre_name
                logger.debug(f"🎵 Info genre capturée: {genre_name}")
        except Exception as e:
            logger.debug(f"Erreur lors du traitement du genre: {e}")

    def _handle_release_date(self, date_text: str):
        """Traite et stocke la date de sortie trouvée dans les crédits"""
        try:
            from datetime import datetime

            # Patterns de dates courants
            date_patterns = [
                "%B %d, %Y",      # September 14, 2018
                "%b %d, %Y",      # Sep 14, 2018
                "%Y-%m-%d",       # 2018-09-14
                "%d/%m/%Y",       # 14/09/2018
                "%m/%d/%Y",       # 09/14/2018
                "%Y",             # 2018 (année seule)
            ]

            date_text = date_text.strip()
            parsed_date = None

            for pattern in date_patterns:
                try:
                    parsed_date = datetime.strptime(date_text, pattern)
                    break
                except ValueError:
                    continue
                    
            if parsed_date:
                # Stocker dans une variable d'instance pour l'utiliser dans scrape_track_credits
                self._current_release_date = parsed_date
                logger.debug(f"📅 Date de sortie capturée: {date_text} -> {parsed_date.strftime('%Y-%m-%d')}")
            else:
                logger.debug(f"⚠️ Format de date non reconnu: {date_text}")

        except Exception as e:
            logger.debug(f"Erreur lors du parsing de la date: {e}")

    def _deduplicate_credits(self, credits: List[Credit]) -> List[Credit]:
        """Supprime les doublons de crédits"""
        seen = set()
        unique_credits = []
    
        for credit in credits:
            # Créer une clé unique basée sur nom + rôle
            key = (credit.name.lower().strip(), credit.role.value)
        
            if key not in seen:
                seen.add(key)
                unique_credits.append(credit)
            else:
                logger.debug(f"Doublon ignoré: {credit.name} - {credit.role.value}")
    
        return unique_credits


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
        album_credits_cache = {}  # Cache pour éviter de scraper le même album plusieurs fois
        
        for i, track in enumerate(tracks):
            try:
                # Scraper les crédits du morceau
                credits = self.scrape_track_credits(track)
                
                # Scraper l'album si pas déjà fait
                if track.album and track.album not in album_credits_cache:
                    album_url = self.get_album_url_from_track(track.genius_url)
                    if album_url:
                        album_credits = self.scrape_album_credits(album_url)
                        album_credits_cache[track.album] = album_credits
                        results['albums_scraped'].add(track.album)
                        
                        # Ajouter les crédits d'album au track
                        for credit in album_credits:
                            track.add_credit(credit)
                
                # Si l'album a déjà été scrapé, ajouter les crédits du cache
                elif track.album in album_credits_cache:
                    for credit in album_credits_cache[track.album]:
                        track.add_credit(credit)
                
                if credits:
                    results['success'] += 1
                else:
                    results['failed'] += 1
                
                # Callback de progression
                if progress_callback:
                    progress_callback(i + 1, total, track.title)
                    
            except Exception as e:
                results['failed'] += 1
                results['errors'].append({
                    'track': track.title,
                    'error': str(e)
                })
                logger.error(f"Erreur sur {track.title}: {e}")
        
        logger.info(f"Albums scrapés: {len(results['albums_scraped'])}")
        return results