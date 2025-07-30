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

            # Log l'URL effective
            logger.debug(f"URL visitée : {self.driver.current_url}")

            # Attendre la banniere de cookies
            WebDriverWait(self.driver, 5).until(
                EC.presence_of_element_located((By.ID, "onetrust-banner-sdk"))
            )

            # Accepter les cookies
            try:
                accept_btn = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((By.ID, "onetrust-accept-btn-handler"))
                )
                accept_btn.click()
                logger.debug("✅ Cookies acceptés")
                time.sleep(1)
            except TimeoutException:
                logger.debug("⚠️ Pas de bannière cookies")

            # Trouver l'élément "Credits"
            credits_header = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((
                    By.XPATH, "//div[contains(@class, 'SongInfo__Title') and text()='Credits']"
                ))
            )
            self.driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", credits_header)

            time.sleep(1)

            # Cliquer sur le bouton Expand
            expand_button = self._find_expand_button()
            if expand_button:
                self.driver.execute_script("arguments[0].click();", expand_button)
                WebDriverWait(self.driver, 10).until(
                    EC.visibility_of_element_located((By.CSS_SELECTOR, ".credits-expanded"))
                )
                logger.debug("✅ Bouton Expand cliqué")
            else:
                logger.debug("⚠️ Aucun bouton Expand trouvé")

            # Extraire les crédits
            credits = self._extract_credits()

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
        """Trouve le bouton pour afficher tous les crédits"""
        button_selectors = [
            "//button[contains(text(), 'Expand')]",
            "//button[contains(text(), 'Show all credits')]",
            "//button[contains(@class, 'ExpandableContent__Button')]",
            "//div[@class='SongInfo__Columns']//button",
            "//button[contains(text(), 'Show Credits')]"
        ]
        
        for selector in button_selectors:
            try:
                button = self.driver.find_element(By.XPATH, selector)
                if button and button.is_displayed():
                    return button
            except NoSuchElementException:
                continue
        
        logger.debug("Bouton Expand non trouvé")
        return None
    
    def _extract_credits(self) -> List[Credit]:
        """Extrait les crédits de la page"""
        credits = []
        
        try:
            # Attendre que les crédits soient visibles
            self.wait.until(
                EC.presence_of_element_located((By.CLASS_NAME, "SongInfo__Credit"))
            )
            
            # Obtenir le HTML de la page
            soup = BeautifulSoup(self.driver.page_source, 'html.parser')
            
            # Méthode 1: Chercher les éléments de crédit
            credit_elements = soup.find_all(class_=re.compile(r'Credit|credit'))
            
            for element in credit_elements:
                credit = self._parse_credit_element(element)
                if credit:
                    credits.append(credit)
            
            # Méthode 2: Chercher dans les sections spécifiques
            sections = soup.find_all(['div', 'section'], class_=re.compile(r'SongInfo|song-info'))
            
            for section in sections:
                # Chercher les patterns de crédits
                credits.extend(self._extract_credits_from_section(section))
            
        except TimeoutException:
            logger.warning("Timeout en attendant les crédits")
        except Exception as e:
            logger.error(f"Erreur lors de l'extraction des crédits: {e}")
        
        return credits
    
    def _parse_credit_element(self, element) -> Optional[Credit]:
        """Parse un élément de crédit"""
        try:
            text = element.get_text(strip=True)
            
            # Patterns pour identifier les rôles (mis à jour avec tous les rôles Genius)
            role_patterns = {
                # Rôles d'écriture
                CreditRole.WRITER: [r'Written by', r'Writer'],
                CreditRole.COMPOSER: [r'Composed by', r'Composer'],
                CreditRole.LYRICIST: [r'Lyrics by', r'Lyricist'],
                CreditRole.TRANSLATOR: [r'Translated by', r'Translator'],
                
                # Rôles de production musicale
                CreditRole.PRODUCER: [r'Produced by', r'Producer(?!.*(?:Co-|Executive|Vocal))'],
                CreditRole.CO_PRODUCER: [r'Co-Produced by', r'Co-Producer'],
                CreditRole.EXECUTIVE_PRODUCER: [r'Executive Producer'],
                CreditRole.VOCAL_PRODUCER: [r'Vocal Producer', r'Vocal Production'],
                CreditRole.ADDITIONAL_PRODUCTION: [r'Additional Production'],
                CreditRole.PROGRAMMER: [r'Programming(?!.*Drum)', r'Programmer(?!.*Drum)'],
                CreditRole.DRUM_PROGRAMMER: [r'Drum Programming', r'Drum Programmer'],
                CreditRole.ARRANGER: [r'Arranged by', r'Arranger', r'Arrangement'],
                
                # Rôles studio
                CreditRole.MIXING_ENGINEER: [r'Mixed by', r'Mixing Engineer(?!.*Assistant)'],
                CreditRole.MASTERING_ENGINEER: [r'Mastered by', r'Mastering Engineer(?!.*Assistant)'],
                CreditRole.RECORDING_ENGINEER: [r'Recorded by', r'Recording Engineer(?!.*Assistant)'],
                CreditRole.ENGINEER: [r'Engineered by', r'Engineer(?!.*(?:Mixing|Mastering|Recording|Assistant))'],
                CreditRole.ASSISTANT_MIXING_ENGINEER: [r'Assistant Mixing Engineer'],
                CreditRole.ASSISTANT_MASTERING_ENGINEER: [r'Assistant Mastering Engineer'],
                CreditRole.ASSISTANT_RECORDING_ENGINEER: [r'Assistant Recording Engineer'],
                CreditRole.ASSISTANT_ENGINEER: [r'Assistant Engineer'],
                CreditRole.STUDIO_PERSONNEL: [r'Studio Personnel'],
                CreditRole.ADDITIONAL_MIXING: [r'Additional Mixing'],
                CreditRole.ADDITIONAL_MASTERING: [r'Additional Mastering'],
                CreditRole.ADDITIONAL_RECORDING: [r'Additional Recording'],
                CreditRole.ADDITIONAL_ENGINEERING: [r'Additional Engineering'],
                CreditRole.PREPARER: [r'Prepared by', r'Preparer'],
                
                # Rôles liés au chant
                CreditRole.VOCALS: [r'Vocals(?!.*(?:Lead|Background|Additional))'],
                CreditRole.LEAD_VOCALS: [r'Lead Vocals'],
                CreditRole.BACKGROUND_VOCALS: [r'Background Vocals', r'Backing Vocals'],
                CreditRole.ADDITIONAL_VOCALS: [r'Additional Vocals'],
                CreditRole.CHOIR: [r'Choir', r'Chorus'],
                CreditRole.AD_LIBS: [r'Ad-Libs', r'Ad Libs'],
                
                # Label / Édition
                CreditRole.LABEL: [r'Label(?!.*Publisher)'],
                CreditRole.PUBLISHER: [r'Publisher', r'Publishing'],
                CreditRole.DISTRIBUTOR: [r'Distributed by', r'Distributor'],
                CreditRole.COPYRIGHT: [r'Copyright ©', r'© '],
                CreditRole.PHONOGRAPHIC_COPYRIGHT: [r'Phonographic Copyright ℗', r'℗ '],
                CreditRole.MANUFACTURER: [r'Manufactured by', r'Manufacturer'],
                
                # Instruments
                CreditRole.GUITAR: [r'Guitar(?!.*(?:Bass|Acoustic|Electric|Rhythm))'],
                CreditRole.BASS_GUITAR: [r'Bass Guitar'],
                CreditRole.ACOUSTIC_GUITAR: [r'Acoustic Guitar'],
                CreditRole.ELECTRIC_GUITAR: [r'Electric Guitar'],
                CreditRole.RHYTHM_GUITAR: [r'Rhythm Guitar'],
                CreditRole.CELLO: [r'Cello'],
                CreditRole.DRUMS: [r'Drums'],
                CreditRole.BASS: [r'Bass(?!.*Guitar)'],
                CreditRole.KEYBOARD: [r'Keyboard', r'Keyboards'],
                CreditRole.PERCUSSION: [r'Percussion'],
                CreditRole.PIANO: [r'Piano'],
                CreditRole.VIOLIN: [r'Violin'],
                CreditRole.ORGAN: [r'Organ'],
                CreditRole.SYNTHESIZER: [r'Synthesizer', r'Synth'],
                CreditRole.STRINGS: [r'Strings'],
                CreditRole.TRUMPET: [r'Trumpet'],
                CreditRole.VIOLA: [r'Viola'],
                CreditRole.SAXOPHONE: [r'Saxophone', r'Sax'],
                CreditRole.TROMBONE: [r'Trombone'],
                CreditRole.SCRATCHES: [r'Scratches', r'Turntables'],
                CreditRole.INSTRUMENTATION: [r'Instrumentation'],
                
                # Lieux
                CreditRole.RECORDED_AT: [r'Recorded at'],
                CreditRole.MASTERED_AT: [r'Mastered at'],
                CreditRole.MIXED_AT: [r'Mixed at'],
                
                # Jaquette
                CreditRole.ARTWORK: [r'Artwork(?!.*Direction)'],
                CreditRole.ART_DIRECTION: [r'Art Direction'],
                CreditRole.GRAPHIC_DESIGN: [r'Graphic Design'],
                CreditRole.ILLUSTRATION: [r'Illustration'],
                CreditRole.LAYOUT: [r'Layout'],
                CreditRole.PHOTOGRAPHY: [r'Photography', r'Photo'],
                
                # Vidéo
                CreditRole.VIDEO_DIRECTOR: [r'Video Director'],
                CreditRole.VIDEO_PRODUCER: [r'Video Producer'],
                CreditRole.VIDEO_DIRECTOR_OF_PHOTOGRAPHY: [r'Video Director of Photography', r'Video DOP'],
                CreditRole.VIDEO_CINEMATOGRAPHER: [r'Video Cinematographer'],
                CreditRole.VIDEO_DIGITAL_IMAGING_TECHNICIAN: [r'Video Digital Imaging Technician', r'Video DIT'],
                CreditRole.VIDEO_CAMERA_OPERATOR: [r'Video Camera Operator'],
                
                # Album
                CreditRole.A_AND_R: [r'A&R', r'A & R'],
                
                # Autres
                CreditRole.FEATURED: [r'Featuring', r'feat\.', r'ft\.'],
                CreditRole.SAMPLE: [r'Contains sample', r'Samples', r'Sample from'],
                CreditRole.INTERPOLATION: [r'Interpolation', r'Interpolates', r'Based on']
            }
            
            # Identifier le rôle
            for role, patterns in role_patterns.items():
                for pattern in patterns:
                    if re.search(pattern, text, re.IGNORECASE):
                        # Extraire le nom
                        name = self._extract_name_from_credit(text, pattern)
                        if name:
                            # Extraire le détail du rôle si applicable
                            role_detail = None
                            if role == CreditRole.MUSICIAN:
                                # Extraire l'instrument
                                role_detail = self._extract_instrument(text)
                            
                            return Credit(
                                name=name,
                                role=role,
                                role_detail=role_detail,
                                source="genius"
                            )
            
        except Exception as e:
            logger.debug(f"Erreur lors du parsing d'un crédit: {e}")
        
        return None
    
    def _extract_credits_from_section(self, section) -> List[Credit]:
        """Extrait les crédits d'une section de la page"""
        credits = []
        
        try:
            # Chercher les listes de crédits
            credit_lists = section.find_all(['ul', 'div'], class_=re.compile(r'credit|Credit'))
            
            for credit_list in credit_lists:
                items = credit_list.find_all(['li', 'div', 'span'])
                
                current_role = None
                for item in items:
                    text = item.get_text(strip=True)
                    
                    # Vérifier si c'est un titre de rôle
                    role = self._identify_role_from_text(text)
                    if role:
                        current_role = role
                    elif current_role and text:
                        # C'est un nom sous le rôle actuel
                        credit = Credit(
                            name=text,
                            role=current_role,
                            source="genius"
                        )
                        credits.append(credit)
            
        except Exception as e:
            logger.debug(f"Erreur lors de l'extraction d'une section: {e}")
        
        return credits
    
    def _extract_name_from_credit(self, text: str, pattern: str) -> Optional[str]:
        """Extrait le nom depuis un texte de crédit"""
        try:
            # Retirer le pattern du texte
            name = re.sub(pattern, '', text, flags=re.IGNORECASE).strip()
            # Retirer les caractères indésirables
            name = name.strip(':,')
            return name if name else None
        except:
            return None
    
    def _extract_instrument(self, text: str) -> Optional[str]:
        """Extrait l'instrument joué"""
        instruments = [
            'Guitar', 'Bass Guitar', 'Acoustic Guitar', 'Electric Guitar', 'Rhythm Guitar',
            'Cello', 'Drums', 'Bass', 'Keyboard', 'Keyboards', 'Percussion',
            'Piano', 'Violin', 'Organ', 'Synthesizer', 'Synth', 'Strings',
            'Trumpet', 'Viola', 'Saxophone', 'Sax', 'Trombone', 'Scratches',
            'Turntables', 'Flute', 'Clarinet', 'Harmonica', 'Accordion',
            'Banjo', 'Mandolin', 'Ukulele', 'Harp'
        ]
        
        text_lower = text.lower()
        for instrument in instruments:
            if instrument.lower() in text_lower:
                return instrument
        
        return None
    
    def _identify_role_from_text(self, text: str) -> Optional[CreditRole]:
        """Identifie un rôle à partir d'un texte"""
        role_keywords = {
            # Écriture
            CreditRole.WRITER: ['writer', 'written', 'wrote'],
            CreditRole.COMPOSER: ['composer', 'composed'],
            CreditRole.LYRICIST: ['lyricist', 'lyrics'],
            CreditRole.TRANSLATOR: ['translator', 'translated'],
            
            # Production
            CreditRole.PRODUCER: ['producer', 'produced', 'production'],
            CreditRole.CO_PRODUCER: ['co-producer', 'co-produced'],
            CreditRole.EXECUTIVE_PRODUCER: ['executive producer'],
            CreditRole.VOCAL_PRODUCER: ['vocal producer', 'vocal production'],
            CreditRole.ADDITIONAL_PRODUCTION: ['additional production'],
            CreditRole.PROGRAMMER: ['programmer', 'programming'],
            CreditRole.DRUM_PROGRAMMER: ['drum programmer', 'drum programming'],
            CreditRole.ARRANGER: ['arranger', 'arranged', 'arrangement'],
            
            # Studio
            CreditRole.MIXING_ENGINEER: ['mixing engineer', 'mixed by', 'mixing'],
            CreditRole.MASTERING_ENGINEER: ['mastering engineer', 'mastered by', 'mastering'],
            CreditRole.RECORDING_ENGINEER: ['recording engineer', 'recorded by', 'recording'],
            CreditRole.ENGINEER: ['engineer', 'engineered'],
            
            # Chant
            CreditRole.VOCALS: ['vocals', 'vocal'],
            CreditRole.LEAD_VOCALS: ['lead vocals', 'lead vocal'],
            CreditRole.BACKGROUND_VOCALS: ['background vocals', 'backing vocals'],
            CreditRole.ADDITIONAL_VOCALS: ['additional vocals'],
            CreditRole.CHOIR: ['choir', 'chorus'],
            CreditRole.AD_LIBS: ['ad-libs', 'ad libs'],
            
            # Instruments
            CreditRole.GUITAR: ['guitar'],
            CreditRole.BASS_GUITAR: ['bass guitar'],
            CreditRole.DRUMS: ['drums', 'drummer'],
            CreditRole.PIANO: ['piano', 'pianist'],
            CreditRole.KEYBOARD: ['keyboard', 'keyboards'],
            CreditRole.SYNTHESIZER: ['synthesizer', 'synth'],
            
            # Autres
            CreditRole.FEATURED: ['featuring', 'feat.', 'ft.'],
            CreditRole.SAMPLE: ['sample', 'samples', 'sampled'],
            CreditRole.INTERPOLATION: ['interpolation', 'interpolates']
        }
        
        text_lower = text.lower()
        
        # Vérifier d'abord les rôles les plus spécifiques
        if 'co-producer' in text_lower or 'co-produced' in text_lower:
            return CreditRole.CO_PRODUCER
        elif 'executive producer' in text_lower:
            return CreditRole.EXECUTIVE_PRODUCER
        elif 'vocal producer' in text_lower or 'vocal production' in text_lower:
            return CreditRole.VOCAL_PRODUCER
        elif 'drum program' in text_lower:
            return CreditRole.DRUM_PROGRAMMER
        elif 'bass guitar' in text_lower:
            return CreditRole.BASS_GUITAR
        elif 'lead vocal' in text_lower:
            return CreditRole.LEAD_VOCALS
        elif 'background vocal' in text_lower or 'backing vocal' in text_lower:
            return CreditRole.BACKGROUND_VOCALS
        elif 'additional vocal' in text_lower:
            return CreditRole.ADDITIONAL_VOCALS
        
        # Ensuite vérifier les rôles généraux
        for role, keywords in role_keywords.items():
            if any(keyword in text_lower for keyword in keywords):
                return role
        
        return None
    
    def scrape_album_credits(self, album_url: str) -> List[Credit]:
        """Scrape les crédits d'un album (Executive Producer, Artwork, etc.)"""
        credits = []
        
        try:
            logger.info(f"Scraping des crédits d'album: {album_url}")
            self.driver.get(album_url)
            
            # Attendre que la page se charge
            time.sleep(2)
            
            # Chercher et cliquer sur le bouton "Expand" si présent
            expand_button = self._find_expand_button()
            if expand_button:
                self.driver.execute_script("arguments[0].click();", expand_button)
                time.sleep(1)
            
            # Extraire les crédits spécifiques à l'album
            credits = self._extract_credits()
            
            # Filtrer seulement les crédits niveau album
            album_level_roles = [
                CreditRole.EXECUTIVE_PRODUCER,
                CreditRole.ARTWORK,
                CreditRole.ART_DIRECTION,
                CreditRole.GRAPHIC_DESIGN,
                CreditRole.ILLUSTRATION,
                CreditRole.LAYOUT,
                CreditRole.PHOTOGRAPHY,
                CreditRole.A_AND_R,
                CreditRole.LABEL,
                CreditRole.PUBLISHER,
                CreditRole.DISTRIBUTOR,
                CreditRole.COPYRIGHT,
                CreditRole.PHONOGRAPHIC_COPYRIGHT
            ]
            
            album_credits = [c for c in credits if c.role in album_level_roles]
            logger.info(f"{len(album_credits)} crédits d'album extraits")
            
            return album_credits
            
        except Exception as e:
            logger.error(f"Erreur lors du scraping de l'album: {e}")
            return []
    
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