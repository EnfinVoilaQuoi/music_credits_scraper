"""Scraper pour récupérer les crédits complets sur Genius"""
import time
import re
from typing import List, Dict, Any, Optional
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
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
                options.add_argument('--headless')
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-gpu')
            options.add_argument('--window-size=1920,1080')
            options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
            
            # Désactiver les images pour accélérer
            prefs = {"profile.managed_default_content_settings.images": 2}
            options.add_experimental_option("prefs", prefs)
            
            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=options)
            self.wait = WebDriverWait(self.driver, SELENIUM_TIMEOUT)
            
            logger.info("Driver Selenium initialisé")
            
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
            
            # Attendre que la page se charge
            time.sleep(2)
            
            # Chercher et cliquer sur le bouton "Expand" ou "Show all credits"
            expand_button = self._find_expand_button()
            if expand_button:
                self.driver.execute_script("arguments[0].click();", expand_button)
                time.sleep(1)
                logger.debug("Bouton Expand cliqué")
            
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
            
            # Patterns pour identifier les rôles
            role_patterns = {
                CreditRole.PRODUCER: [r'Produced by', r'Producer', r'Production'],
                CreditRole.WRITER: [r'Written by', r'Writer', r'Lyrics by', r'Composed by'],
                CreditRole.PERFORMER: [r'Performed by', r'Vocals by', r'Lead Vocals'],
                CreditRole.FEATURED: [r'Featuring', r'feat\.', r'ft\.'],
                CreditRole.BACKGROUND_VOCALS: [r'Background Vocals', r'Backing Vocals', r'Additional Vocals'],
                CreditRole.ADDITIONAL_VOCALS: [r'Additional Vocals', r'Choir', r'Chorus'],
                CreditRole.MUSICIAN: [r'Guitar', r'Bass', r'Drums', r'Piano', r'Keyboards', r'Saxophone', r'Trumpet'],
                CreditRole.ENGINEER: [r'Recorded by', r'Recording Engineer', r'Engineered by'],
                CreditRole.MIXER: [r'Mixed by', r'Mixing Engineer', r'Mix Engineer'],
                CreditRole.MASTERING: [r'Mastered by', r'Mastering Engineer'],
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
            'Guitar', 'Bass', 'Drums', 'Piano', 'Keyboards', 
            'Saxophone', 'Trumpet', 'Violin', 'Cello', 'Flute'
        ]
        
        for instrument in instruments:
            if instrument.lower() in text.lower():
                return instrument
        
        return None
    
    def _identify_role_from_text(self, text: str) -> Optional[CreditRole]:
        """Identifie un rôle à partir d'un texte"""
        role_keywords = {
            CreditRole.PRODUCER: ['producer', 'produced', 'production'],
            CreditRole.WRITER: ['writer', 'written', 'composed', 'lyrics'],
            CreditRole.PERFORMER: ['performed', 'vocals', 'singer'],
            CreditRole.ENGINEER: ['engineer', 'recorded', 'recording'],
            CreditRole.MIXER: ['mixed', 'mixing', 'mix'],
            CreditRole.MASTERING: ['mastered', 'mastering']
        }
        
        text_lower = text.lower()
        for role, keywords in role_keywords.items():
            if any(keyword in text_lower for keyword in keywords):
                return role
        
        return None
    
    def scrape_multiple_tracks(self, tracks: List[Track], progress_callback=None) -> Dict[str, Any]:
        """Scrape plusieurs morceaux avec rapport de progression"""
        results = {
            'success': 0,
            'failed': 0,
            'errors': []
        }
        
        total = len(tracks)
        
        for i, track in enumerate(tracks):
            try:
                credits = self.scrape_track_credits(track)
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
        
        return results