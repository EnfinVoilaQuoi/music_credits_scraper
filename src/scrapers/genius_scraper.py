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
        self.temp_dir = None  # Stocker le répertoire temporaire
        self._init_driver()
    
    def _init_driver(self):
        """Initialise le driver Selenium"""
        try:
            options = Options()

            # Mode headless
            if self.headless:
                options.add_argument('--headless=new')

            # Arguments de base
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-gpu')
            options.add_argument('--window-size=1920,1080')
            options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')

            # Supprimer les logs
            options.add_argument('--log-level=3')
            options.add_experimental_option('excludeSwitches', ['enable-logging', 'enable-automation'])
            options.add_experimental_option('useAutomationExtension', False)

            # Désactiver fonctionnalités inutiles
            options.add_argument('--disable-blink-features=AutomationControlled')
            options.add_argument('--disable-background-networking')
            options.add_argument('--disable-webgl')
            options.add_argument('--disable-webgl2')

            # Désactiver les images pour accélérer
            prefs = {
                "profile.managed_default_content_settings.images": 2,
                "profile.default_content_setting_values.notifications": 2,
                "profile.default_content_settings.popups": 0
            }
            options.add_experimental_option("prefs", prefs)

            # Créer un répertoire de données utilisateur temporaire unique
            import tempfile
            import uuid
            import os

            # Utiliser un identifiant unique pour éviter les conflits
            session_id = str(uuid.uuid4())
            self.temp_dir = os.path.join(tempfile.gettempdir(), f'chrome_session_{session_id}')
            os.makedirs(self.temp_dir, exist_ok=True)

            options.add_argument(f'--user-data-dir={self.temp_dir}')
            options.add_argument('--disable-dev-shm-usage')  # Éviter les problèmes de mémoire partagée
            options.add_argument('--remote-debugging-port=0')  # Port aléatoire pour éviter conflits

            # Essayer d'abord le driver local
            from pathlib import Path
            local_driver = Path(__file__).parent.parent.parent / "drivers" / "chromedriver.exe"

            if not local_driver.exists():
                for path in (Path(__file__).parent.parent.parent / "drivers").rglob("chromedriver.exe"):
                    local_driver = path
                    break

            if local_driver.exists():
                logger.info(f"Utilisation du ChromeDriver local: {local_driver}")
                service = ChromeService(
                    str(local_driver),
                    log_output=subprocess.DEVNULL
                )
            else:
                logger.info("ChromeDriver local non trouvé, utilisation de webdriver-manager")
                # Forcer le téléchargement de la version compatible avec Chrome 141
                service = ChromeService(
                    ChromeDriverManager(driver_version="141.0.7390.70").install(),
                    log_output=subprocess.DEVNULL
                )

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
        """Ferme le driver et nettoie les ressources"""
        if self.driver:
            try:
                self.driver.quit()
                logger.info("Driver Selenium fermé")
            except Exception as e:
                logger.warning(f"Erreur lors de la fermeture du driver: {e}")

        # Nettoyer le répertoire temporaire
        if self.temp_dir:
            try:
                import shutil
                import time
                import os
                time.sleep(1)  # Attendre que Chrome libère les fichiers
                if os.path.exists(self.temp_dir):
                    shutil.rmtree(self.temp_dir, ignore_errors=True)
                    logger.debug(f"Répertoire temporaire supprimé: {self.temp_dir}")
            except Exception as e:
                logger.debug(f"Impossible de supprimer le répertoire temporaire: {e}")
    
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
            
            # Ouvrir la section des crédits
            try:
                credits_button = WebDriverWait(self.driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[contains(@class, 'ExpandableContent')]//span[contains(text(), 'Credits')]"))
                )
                self.driver.execute_script("arguments[0].click();", credits_button)
                time.sleep(1)
            except Exception as e:
                logger.warning(f"Impossible d'ouvrir les crédits: {e}")
                return credits
            
            # Extraire les crédits
            credits = self._extract_credits(track)
            
            # Marquer comme scrapé
            track.credits_scraped = True
            
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

            # Cliquer sur "Read More" pour les anecdotes si présent
            try:
                read_more_button = self.driver.find_element(
                    By.CSS_SELECTOR,
                    "span.SongBioPreview__ViewBio-sc-d13d64be-2, .iqEAIt"
                )
                if read_more_button.is_displayed():
                    self.driver.execute_script("arguments[0].click();", read_more_button)
                    time.sleep(1)
                    logger.debug("Bouton 'Read More' cliqué pour anecdotes")
            except Exception as e:
                logger.debug(f"Bouton 'Read More' non trouvé (normal si pas d'anecdotes): {e}")

            # Récupérer les paroles
            soup = BeautifulSoup(self.driver.page_source, 'html.parser')

            # Chercher les conteneurs de paroles
            lyrics_containers = soup.find_all('div', {'data-lyrics-container': 'true'})

            if lyrics_containers:
                lyrics_parts = []
                for container in lyrics_containers:
                    # Extraire le texte en préservant les sauts de ligne
                    text = container.get_text(separator='\n', strip=True)
                    if text:
                        lyrics_parts.append(text)

                lyrics = '\n\n'.join(lyrics_parts)

                # Extraire et séparer les anecdotes des paroles
                lyrics, anecdotes = self._extract_anecdotes(lyrics)

                # Nettoyer les paroles
                lyrics = self._clean_lyrics(lyrics)

                # Sauvegarder les anecdotes si trouvées
                if anecdotes:
                    track.anecdotes = anecdotes
                    logger.info(f"📝 Anecdotes extraites pour {track.title}")

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

        # Supprimer TOUTES les sections [Paroles de "titre"] (début ET milieu du texte)
        lyrics = re.sub(r'\[Paroles de[^\]]*\]\s*', '', lyrics, flags=re.MULTILINE)

        # Supprimer "You might also like" et les suggestions
        lyrics = re.sub(r'You might also like.*?(?=\[|$)', '', lyrics, flags=re.DOTALL | re.MULTILINE)

        # Supprimer les lignes "123Embed" ou "Embed"
        lyrics = re.sub(r'\n\d*Embed', '', lyrics, flags=re.MULTILINE)

        # Supprimer "See [Language] Translations"
        lyrics = re.sub(r'\nSee.*?Translations', '', lyrics, flags=re.MULTILINE)
        
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