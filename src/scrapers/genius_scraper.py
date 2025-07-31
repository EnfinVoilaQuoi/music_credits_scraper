"""Scraper pour récupérer les crédits complets sur Genius - Version corrigée"""
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
            options.add_argument('--log-level=3')
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
        """Scrape les crédits complets d'un morceau - VERSION CORRIGÉE"""
        if not track.genius_url:
            logger.warning(f"Pas d'URL Genius pour {track.title}")
            return []
    
        credits = []
    
        try:
            logger.info(f"Scraping des crédits pour: {track.title}")
            self.driver.get(track.genius_url)

            logger.debug(f"URL visitée : {self.driver.current_url}")

            # Gestion des cookies
            try:
                WebDriverWait(self.driver, 5).until(
                    EC.presence_of_element_located((By.ID, "onetrust-banner-sdk"))
                )
                accept_btn = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((By.ID, "onetrust-accept-btn-handler"))
                )
                accept_btn.click()
                logger.debug("✅ Cookies acceptés")
                time.sleep(1)
            except TimeoutException:
                logger.debug("⚠️ Pas de bannière cookies")

            # NOUVEAU: Extraire d'abord les métadonnées du header (album, numéro de piste)
            self._extract_header_metadata(track)

            # Aller à la section "Credits"
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
                try:
                    button_text = expand_button.text.strip()
                    button_location = expand_button.location
                    logger.debug(f"📍 Bouton sélectionné: '{button_text}' à position {button_location}")
                    
                    self.driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", expand_button)
                    time.sleep(1)
                
                    self.driver.execute_script("arguments[0].click();", expand_button)
                    logger.debug("Bouton Expand cliqué")
                
                    # Attendre que le contenu étendu soit visible
                    try:
                        WebDriverWait(self.driver, 10).until(
                            lambda driver: len(driver.find_elements(By.CSS_SELECTOR, "div[class*='SongInfo__Credit']")) > 0
                        )
                        time.sleep(2)
                    except TimeoutException:
                        logger.warning("Timeout en attendant le contenu étendu")
                except Exception as e:
                    logger.error(f"Erreur lors du clic sur le bouton Expand: {e}")
            else:
                logger.debug("Aucun bouton Expand trouvé")

            # ✅ CORRECTION : Extraire les crédits avec la bonne signature de méthode
            credits = self._extract_credits(track)

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

    def _extract_header_metadata(self, track: Track):
        """Extrait les métadonnées depuis le header de la page (album, numéro de piste)"""
        try:
            soup = BeautifulSoup(self.driver.page_source, 'html.parser')
            
            # Chercher l'info album dans le header
            album_container = soup.find('div', class_=lambda x: x and 'HeaderCredit__Container' in x)
            
            if album_container:
                # Extraire le numéro de piste
                track_label = album_container.find('span', class_=lambda x: x and 'HeaderCredit__Label' in x)
                if track_label:
                    track_text = track_label.get_text(strip=True)
                    # Extraire le numéro de la piste (ex: "Track 14 on")
                    track_match = re.search(r'Track (\d+)', track_text)
                    if track_match:
                        track.track_number = int(track_match.group(1))
                        logger.debug(f"🔢 Numéro de piste: {track.track_number}")
                
                # Extraire le nom de l'album
                album_link = album_container.find('a', class_=lambda x: x and 'StyledLink' in x)
                if album_link and not track.album:  # Ne pas écraser si déjà défini
                    album_name = album_link.get_text(strip=True)
                    # Nettoyer le nom (enlever les symboles de flèche)
                    album_name = re.sub(r'\s*[\u2192\u2190\u2191\u2193→←↑↓]\s*', '', album_name).strip()
                    track.album = album_name
                    logger.debug(f"💿 Album extrait du header: {album_name}")
                    
        except Exception as e:
            logger.debug(f"Erreur lors de l'extraction des métadonnées du header: {e}")
            
    def _extract_credits(self, track: Track) -> List[Credit]:
        """Extrait les crédits de la page - VERSION CORRIGÉE basée sur l'ancien code"""
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
                
                # Trouver le conteneur des valeurs (noms)
                container_div = label_div.find_next_sibling("div")
                if not container_div:
                    continue
                
                # MÉTHODE CORRIGÉE : Extraire les noms intelligemment
                names = self._extract_names_intelligently(container_div)
                
                # Traiter les cas spéciaux (métadonnées) - SEULEMENT SI MANQUANT
                if role_text.lower() == 'released on':
                    if names and not track.release_date:  # Seulement si manquant de l'API
                        date_text = ' '.join(names)
                        parsed_date = self._parse_release_date(date_text)
                        if parsed_date:
                            track.release_date = parsed_date
                            logger.debug(f"📅 Date de sortie scrapée: {date_text}")
                    continue
                    
                elif role_text.lower() == 'album':
                    if names and not track.album:  # Seulement si manquant de l'API
                        track.album = ' '.join(names)
                        logger.debug(f"💿 Album scrapé: {track.album}")
                    continue
                    
                elif role_text.lower() == 'genre':
                    if names and not track.genre:  # Seulement si manquant de l'API
                        track.genre = ', '.join(names)
                        logger.debug(f"🎵 Genre scrapé: {track.genre}")
                    continue
                
                # Créer les objets Credit pour chaque nom
                role_enum = self._map_genius_role_to_enum(role_text)
                if not role_enum:
                    role_enum = CreditRole.OTHER
                
                for name in names:
                    if name and len(name.strip()) > 1:
                        credit = Credit(
                            name=name.strip(),
                            role=role_enum,
                            role_detail=role_text if role_enum == CreditRole.OTHER else None,
                            source="genius"
                        )
                        credits.append(credit)
                        logger.debug(f"Crédit créé: {name.strip()} - {role_enum.value}")
            
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
            import traceback
            logger.debug(f"Traceback complet: {traceback.format_exc()}")
        
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
                "%b %d, %Y",      # Sep 14, 2018
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