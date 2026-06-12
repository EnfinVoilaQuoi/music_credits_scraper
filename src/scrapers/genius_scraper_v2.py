"""Scraper pour récupérer les crédits complets sur Genius - VERSION 2 (Playwright)"""
import time
import re
from typing import List, Dict, Any, Optional
from datetime import datetime

from playwright.sync_api import (
    sync_playwright,
    Page,
    Browser,
    BrowserContext,
    Playwright as PlaywrightInstance,
    TimeoutError as PlaywrightTimeoutError,
    ElementHandle,
)
from bs4 import BeautifulSoup

from src.config import SELENIUM_TIMEOUT, DELAY_BETWEEN_REQUESTS
from src.models import Track, Credit, CreditRole
from src.utils.logger import get_logger


logger = get_logger(__name__)

# Alias de timeout : SELENIUM_TIMEOUT est en secondes, Playwright attend des ms
PW_TIMEOUT = SELENIUM_TIMEOUT * 1000  # ms


class GeniusScraper:
    """Scraper pour extraire les crédits complets depuis Genius (Playwright)"""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._playwright: Optional[PlaywrightInstance] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self._init_driver()

    def _init_driver(self):
        """Initialise le browser Playwright (remplace webdriver.Chrome)"""
        try:
            logger.info(f"🌐 Initialisation du browser Playwright (headless={self.headless})...")

            self._playwright = sync_playwright().start()

            launch_args = [
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--disable-webgl',
                '--disable-webgl2',
                '--disable-software-rasterizer',
                '--disable-gpu-sandbox',
                '--disable-accelerated-2d-canvas',
                '--disable-accelerated-video-decode',
            ]

            self.browser = self._playwright.chromium.launch(
                headless=self.headless,
                args=launch_args,
            )

            self.context = self.browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                # Désactiver images pour accélérer (équivalent de prefs managed_default_content_settings)
                java_script_enabled=True,
            )

            # Bloquer les images (équivalent du prefs Selenium)
            self.context.route(
                "**/*.{png,jpg,jpeg,gif,webp,svg,ico}",
                lambda route: route.abort()
            )

            self.page = self.context.new_page()

            # Masquer les signes d'automation (équivalent de navigator.webdriver = undefined)
            self.page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

            logger.info("✅ Browser Playwright initialisé avec succès")

        except Exception as e:
            logger.error(f"❌ Erreur initialisation Playwright: {e}")
            self._cleanup_resources()
            raise

    def _cleanup_resources(self):
        """Nettoyage interne des ressources Playwright"""
        try:
            if self.page:
                self.page.close()
        except Exception:
            pass
        try:
            if self.context:
                self.context.close()
        except Exception:
            pass
        try:
            if self.browser:
                self.browser.close()
        except Exception:
            pass
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        self.page = None
        self.context = None
        self.browser = None
        self._playwright = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self):
        """Ferme le browser et nettoie les ressources"""
        try:
            self._cleanup_resources()
            logger.info("✅ GeniusScraper: Browser fermé")
        except Exception as e:
            logger.debug(f"Erreur lors de la fermeture du browser (normale si déjà fermé): {e}")

    def _handle_cookies(self):
        """Gère les popups de cookies Genius"""
        try:
            logger.debug("🍪 Gestion des cookies...")
            time.sleep(1)

            # Sélecteurs pour les popups de cookies Genius
            cookie_selectors = [
                "button#onetrust-accept-btn-handler",
                "button[data-testid='accept-all-cookies']",
                "button[class*='accept-all']",
                "#onetrust-accept-btn-handler",
                "button[class*='CookieConsentNotice']",
            ]

            for selector in cookie_selectors:
                try:
                    element = self.page.query_selector(selector)
                    if element and element.is_visible() and element.is_enabled():
                        text = element.inner_text()[:50]
                        element.click()
                        logger.info(f"✅ Clic sur le bouton cookies: '{text}'")
                        time.sleep(1)
                        return
                except Exception:
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
            self.page.goto(track.genius_url, wait_until="domcontentloaded")

            # Gérer les cookies si nécessaire
            self._handle_cookies()

            # Attendre le chargement
            time.sleep(2)

            # IMPORTANT : Extraire l'album depuis le header AVANT les crédits
            self._extract_header_metadata(track)

            if track.album:
                logger.info(f"✅ Album trouvé dans le header: '{track.album}'")
            else:
                logger.info(f"⚠️ Aucun album trouvé dans le header pour: {track.title}")

            # Ouvrir la section des crédits - Stratégie multi-niveaux
            credits_opened = False

            # STRATÉGIE 1: Chercher le bouton "Expand" dans la section Credits via XPath
            logger.debug("🔍 Recherche du bouton Expand dans section Credits (stratégie 1)")
            try:
                expand_selectors = [
                    "xpath=//div[contains(@class, 'SongInfo__Title') and text()='Credits']/following::button[contains(text(), 'Expand')]",
                    "xpath=//div[contains(@class, 'ExpandableContent__ButtonContainer')]//button[contains(text(), 'Expand')]",
                    "xpath=//button[contains(@class, 'ExpandableContent__Button')]",
                    "xpath=//button[contains(., 'Expand')]",
                ]

                for selector in expand_selectors:
                    try:
                        # Attendre que l'élément soit visible (équivalent de element_to_be_clickable)
                        self.page.wait_for_selector(selector, timeout=5000, state="visible")
                        button = self.page.query_selector(selector)

                        if button and button.is_visible() and button.is_enabled():
                            logger.debug(f"✅ Bouton Expand trouvé avec: {selector[:50]}")

                            max_retries = 3
                            for attempt in range(max_retries):
                                try:
                                    button = self.page.query_selector(selector)
                                    if button and button.is_visible() and button.is_enabled():
                                        # Scroll vers le bouton
                                        button.scroll_into_view_if_needed()
                                        time.sleep(0.3)

                                        # Clic JavaScript (force=True équivalent de execute_script click)
                                        button_text = button.inner_text()
                                        button.evaluate("el => el.click()")
                                        logger.info(f"✅ Bouton Expand cliqué (texte: '{button_text}')")
                                        time.sleep(2)
                                        credits_opened = True
                                        break
                                    else:
                                        if attempt < max_retries - 1:
                                            time.sleep(0.5 * (attempt + 1))
                                            continue
                                except Exception as attempt_e:
                                    if attempt < max_retries - 1:
                                        logger.debug(f"Tentative {attempt + 1}/{max_retries} échouée: {attempt_e}")
                                        time.sleep(0.5 * (attempt + 1))
                                        continue
                                    else:
                                        raise

                        if credits_opened:
                            break

                    except PlaywrightTimeoutError:
                        logger.debug(f"Timeout avec sélecteur: {selector[:50]}")
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
                    "xpath=//button[contains(@class, 'ExpandableContent')]//span[contains(text(), 'Credits')]",
                    "xpath=//button//span[contains(text(), 'Credits')]",
                    "xpath=//button[contains(text(), 'Credits')]",
                    "xpath=//*[contains(text(), 'Credits')]/ancestor::button",
                    "xpath=//button[contains(@aria-label, 'Credits')]",
                    "xpath=//div[contains(@class, 'credits')]//button",
                ]

                for selector in credits_selectors:
                    try:
                        self.page.wait_for_selector(selector, timeout=3000, state="visible")
                        credits_button = self.page.query_selector(selector)
                        if credits_button:
                            credits_button.scroll_into_view_if_needed()
                            time.sleep(0.5)
                            credits_button.evaluate("el => el.click()")
                            logger.info(f"✅ Bouton Credits cliqué avec XPath: {selector[:50]}")
                            time.sleep(2)
                            credits_opened = True
                            break
                    except Exception:
                        continue

            # STRATÉGIE 3: Fallback - Chercher un "Expand" après la section Credits
            if not credits_opened:
                logger.debug("🔍 Recherche du bouton Credits (stratégie 3: bouton Expand)")
                expand_button = self._find_expand_button()
                if expand_button:
                    try:
                        expand_button.scroll_into_view_if_needed()
                        time.sleep(0.5)
                        expand_button.evaluate("el => el.click()")
                        logger.info("✅ Bouton Expand Credits cliqué (fallback)")
                        time.sleep(2)
                        credits_opened = True
                    except Exception as e:
                        logger.debug(f"Échec clic bouton Expand: {e}")

            if not credits_opened:
                logger.info("ℹ️ Aucun bouton Expand trouvé - Les crédits sont peut-être déjà affichés")
                logger.debug("💡 Tentative d'extraction des crédits sans cliquer sur le bouton...")
            else:
                logger.debug("✅ Section Credits ouverte, prêt pour l'extraction")

            # Extraire les crédits (même si pas de bouton - peut-être déjà affichés)
            credits = self._extract_credits(track)

            if not credits and not credits_opened:
                logger.warning("❌ Aucun crédit trouvé et aucun bouton Expand détecté")
                try:
                    screenshot_path = f"debug_credits_{track.title[:20].replace('/', '_').replace('€', 'E')}.png"
                    self.page.screenshot(path=screenshot_path)
                    logger.info(f"📸 Screenshot sauvegardé: {screenshot_path}")
                except Exception:
                    pass

            for credit in credits:
                track.add_credit(credit)

            logger.info(f"✅ {len(credits)} crédits trouvés pour {track.title}")
            return credits

        except Exception as e:
            logger.error(f"Erreur scraping crédits pour {track.title}: {e}")
            return credits

    def _find_expand_button(self) -> Optional[ElementHandle]:
        """Trouve le bouton Expand spécifiquement dans la section Credits"""
        try:
            logger.debug("Recherche de tous les boutons Expand...")

            expand_selectors = [
                "xpath=//button[contains(@class, 'ExpandableContent')]",
                "xpath=//div[contains(@class, 'ExpandableContent')]//button",
                "xpath=//button[contains(text(), 'Expand')]",
                "div[class*='ExpandableContent'] button",
                "button[class*='ExpandableContent']",
            ]

            all_buttons: List[ElementHandle] = []

            for selector in expand_selectors:
                try:
                    buttons = self.page.query_selector_all(selector)
                    for button in buttons:
                        if button.is_visible() and button.is_enabled():
                            if button not in all_buttons:
                                all_buttons.append(button)
                except Exception as e:
                    logger.debug(f"Erreur avec sélecteur {selector}: {e}")
                    continue

            logger.debug(f"Trouvé {len(all_buttons)} boutons Expand au total")

            # Si on a au moins 2 boutons, prendre le 2ème
            if len(all_buttons) >= 2:
                logger.debug("✅ Utilisation du 2ème bouton Expand trouvé")
                return all_buttons[1]

            # Chercher spécifiquement après le header "Credits"
            try:
                credits_header = self.page.query_selector(
                    "xpath=//div[contains(@class, 'SongInfo__Title') and text()='Credits']"
                )

                if credits_header:
                    credits_box = credits_header.bounding_box()
                    credits_y = credits_box['y'] if credits_box else 0

                    best_button = None
                    best_distance = float('inf')

                    for button in all_buttons:
                        try:
                            box = button.bounding_box()
                            if box:
                                button_y = box['y']
                                if button_y > credits_y:
                                    distance = button_y - credits_y
                                    if distance < best_distance and distance < 500:
                                        best_distance = distance
                                        best_button = button
                        except Exception:
                            continue

                    if best_button:
                        logger.debug("✅ Bouton le plus proche après Credits sélectionné")
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
        patterns = [
            r'\d{4}',
            r'\d{1,2}/\d{1,2}/\d{4}',
            r'\d{4}-\d{2}-\d{2}',
            r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}'
        ]
        for pattern in patterns:
            if re.search(pattern, text):
                return True
        return False

    def _extract_header_metadata(self, track: Track):
        """Extrait les métadonnées depuis le header de la page (album, date, etc.)"""
        try:
            album_selectors = [
                "xpath=//a[contains(@class, 'PrimaryAlbum')]",
                "xpath=//div[contains(@class, 'HeaderMetadata')]//a[contains(@href, '/albums/')]",
                "xpath=//span[contains(text(), 'from')]/following-sibling::a",
                "xpath=//div[@class='song_album']//a",
            ]

            for selector in album_selectors:
                try:
                    album_element = self.page.query_selector(selector)
                    if album_element:
                        album_name = album_element.inner_text().strip()
                        if album_name and album_name != track.album:
                            track.album = album_name
                            logger.info(f"Album trouvé dans le header: '{album_name}'")
                            break
                except Exception:
                    continue

            if not track.release_date:
                date_selectors = [
                    "xpath=//span[contains(@class, 'metadata_unit-info')]//span[contains(text(), '20')]",
                    "xpath=//div[contains(@class, 'HeaderMetadata')]//span[contains(text(), '20')]",
                ]

                for selector in date_selectors:
                    try:
                        date_element = self.page.query_selector(selector)
                        if date_element:
                            date_text = date_element.inner_text().strip()
                            if date_text and self._is_valid_date(date_text):
                                if track.update_release_date(date_text, source="scraper"):
                                    logger.info(f"Date mise à jour depuis header: {date_text}")
                                else:
                                    logger.debug(f"Date depuis header ignorée (date existante plus ancienne): {date_text}")
                                break
                    except Exception:
                        continue

        except Exception as e:
            logger.error(f"Erreur extraction métadonnées header: {e}")

    def _is_valid_album_name(self, name: str, track_title: str = None) -> bool:
        """Valide si un nom est bien un album et pas un artiste/producteur/titre - VERSION STRICTE"""

        if not name or len(name.strip()) < 2:
            return False

        name_lower = name.lower().strip()

        known_producers = [
            'easy dew', 'easydew', 'pyroman', 'the beatmaker', 'dj bellek', 'skread',
            'noxious', 'ponko', 'ikaz', 'kore', 'therapy', 'katrina', 'katrina squad',
            'benjamin epps', 'epps', 'therapy 2093', 'bbp', 'azaia', 'nouvo', 'junior alaprod',
            'hugz', 'myth syzer', 'vm the don', 'lowkey', 'hologram lo', 'bbp beats',
            'mike dean', 'metro boomin', 'pierre bourne', 'wheezy', 'southside',
            'tm88', 'zaytoven', 'lex luger', 'young chop', 'dj mustard', 'hit-boy',
            'boi-1da', 'noah shebib', '40', 'pharrell', 'the neptunes', 'timbaland',
            'dr. dre', 'dr dre', 'scott storch', 'kanye west', 'j dilla', 'madlib',
            'alchemist', 'dj premier', 'cashmoneyap', 'tay keith', 'ronnyj',
            'cubeatz', 'murda beatz', 'london on da track', 'jetsonmade'
        ]

        for producer in known_producers:
            if producer in name_lower or name_lower in producer:
                logger.debug(f"🚫 Album rejeté car producteur connu: '{name}' (match: {producer})")
                return False

        production_indicators = [
            'prod', 'beat', 'music', 'muzik', 'production', 'records',
            'entertainment', 'ent.', 'studio', 'sound', 'audio'
        ]

        for indicator in production_indicators:
            if indicator in name_lower:
                logger.debug(f"🚫 Album rejeté car indicateur de production: '{name}' (indicateur: {indicator})")
                return False

        if track_title and name_lower == track_title.lower().strip():
            logger.debug(f"🚫 Album rejeté car identique au titre: '{name}'")
            return False

        if len(name) <= 10:
            album_indicators = ['vol', 'ep', 'lp', 'mixtape', 'deluxe', 'edition']
            if not any(ind in name_lower for ind in album_indicators):
                logger.debug(f"🚫 Album rejeté car nom trop court sans indicateur: '{name}'")
                return False

        positive_indicators = [
            'vol', 'volume', 'ep', 'lp', 'album', 'deluxe', 'edition',
            'part', 'partie', 'chapter', 'chapitre', 'saison', 'tome',
            'tape', 'mixtape', 'collection', 'anthology'
        ]

        for indicator in positive_indicators:
            if indicator in name_lower:
                logger.debug(f"✅ Album accepté car indicateur positif: '{name}' (indicateur: {indicator})")
                return True

        producer_prefixes = ['dj ', 'mc ', 'young ', 'lil ', 'big ']
        if any(name_lower.startswith(prefix) for prefix in producer_prefixes):
            logger.debug(f"🚫 Album rejeté car préfixe de producteur: '{name}'")
            return False

        if re.match(r'^[a-z]+\s*\d{2,4}$', name_lower):
            logger.debug(f"🚫 Album rejeté car pattern producteur (nom+chiffres): '{name}'")
            return False

        if len(name) > 25:
            logger.debug(f"✅ Album accepté car nom long: '{name}'")
            return True

        logger.debug(f"⚠️ Album ambigu, rejeté par précaution: '{name}'")
        return False

    def _extract_credits(self, track: Track) -> List[Credit]:
        """Extrait les crédits de la page - VERSION SANS EXTRACTION D'ALBUM"""
        credits = []

        try:
            time.sleep(1)
            # Équivalent de WebDriverWait(...).until(EC.presence_of_element_located(...))
            self.page.wait_for_selector(
                "div[class*='SongInfo__Credit']",
                timeout=PW_TIMEOUT
            )
            soup = BeautifulSoup(self.page.content(), 'html.parser')

            for credit_element in soup.select("div[class*='SongInfo__Credit']"):
                label_div = credit_element.find("div", class_=re.compile(r"SongInfo__Label"))
                if not label_div:
                    continue

                role_text = label_div.get_text(strip=True)

                if role_text.lower() == 'album':
                    logger.debug("⚠️ Champ 'Album' ignoré dans les crédits (déjà extrait du header)")
                    continue

                container_div = label_div.find_next_sibling("div")
                if not container_div:
                    continue

                names = self._extract_names_intelligently(container_div)

                if role_text.lower() == 'released on':
                    if names:
                        date_text = ' '.join(names)
                        parsed_date = self._parse_release_date(date_text)
                        if parsed_date:
                            if track.update_release_date(parsed_date, source="scraper"):
                                logger.debug(f"📅 Date de sortie mise à jour: {date_text}")
                            else:
                                logger.debug(f"📅 Date de sortie ignorée (date existante plus ancienne): {date_text}")
                    continue

                elif role_text.lower() == 'genre':
                    if names and not track.genre:
                        track.genre = ', '.join(names)
                        logger.debug(f"🎵 Genre scrapé: {track.genre}")
                    continue

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

            return self._deduplicate_credits(credits)

        except Exception as e:
            logger.error(f"Erreur lors de l'extraction des crédits: {e}")
            return credits

    def _extract_names_intelligently(self, container_div) -> List[str]:
        """Extrait les noms depuis le conteneur de manière intelligente - MÉTHODE RESTAURÉE"""
        names = []

        try:
            for link in container_div.select("a"):
                name = link.get_text(strip=True)
                if name and name not in names:
                    names.append(name)

            for text_node in container_div.find_all(text=True, recursive=False):
                text = text_node.strip()
                if text and text not in names:
                    for separator in [' & ', ', ', ' and ', ' + ', ' / ']:
                        if separator in text:
                            parts = text.split(separator)
                            for part in parts:
                                clean_part = part.strip()
                                if clean_part and clean_part not in names:
                                    names.append(clean_part)
                            break
                    else:
                        if text not in names:
                            names.append(text)

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
                "%B %d, %Y",
                "%B %d %Y",
                "%b %d, %Y",
                "%b %d %Y",
                "%Y-%m-%d",
                "%d/%m/%Y",
                "%m/%d/%Y",
                "%Y",
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
            'Producer': CreditRole.PRODUCER,
            'Co-Producer': CreditRole.CO_PRODUCER,
            'Executive Producer': CreditRole.EXECUTIVE_PRODUCER,
            'Vocal Producer': CreditRole.VOCAL_PRODUCER,
            'Additional Production': CreditRole.ADDITIONAL_PRODUCTION,
            'Writer': CreditRole.WRITER,
            'Songwriter': CreditRole.WRITER,
            'Composer': CreditRole.COMPOSER,
            'Lyricist': CreditRole.LYRICIST,
            'Mixing Engineer': CreditRole.MIXING_ENGINEER,
            'Mix Engineer': CreditRole.MIXING_ENGINEER,
            'Mastering Engineer': CreditRole.MASTERING_ENGINEER,
            'Recording Engineer': CreditRole.RECORDING_ENGINEER,
            'Engineer': CreditRole.ENGINEER,
            'Vocals': CreditRole.VOCALS,
            'Lead Vocals': CreditRole.LEAD_VOCALS,
            'Background Vocals': CreditRole.BACKGROUND_VOCALS,
            'Additional Vocals': CreditRole.ADDITIONAL_VOCALS,
            'Choir': CreditRole.CHOIR,
            'Label': CreditRole.LABEL,
            'Publisher': CreditRole.PUBLISHER,
            'Distributor': CreditRole.DISTRIBUTOR,
            'Guitar': CreditRole.GUITAR,
            'Bass Guitar': CreditRole.BASS_GUITAR,
            'Acoustic Guitar': CreditRole.ACOUSTIC_GUITAR,
            'Electric Guitar': CreditRole.ELECTRIC_GUITAR,
            'Drums': CreditRole.DRUMS,
            'Piano': CreditRole.PIANO,
            'Keyboard': CreditRole.KEYBOARD,
            'Synthesizer': CreditRole.SYNTHESIZER,
            'Bass': CreditRole.BASS,
            'Art Direction': CreditRole.ART_DIRECTION,
            'Artwork': CreditRole.ARTWORK,
            'Graphic Design': CreditRole.GRAPHIC_DESIGN,
            'Photography': CreditRole.PHOTOGRAPHY,
            'Illustration': CreditRole.ILLUSTRATION,
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
            'Featuring': CreditRole.FEATURED,
            'Sample': CreditRole.SAMPLE,
            'A&R': CreditRole.A_AND_R,
        }

        if genius_role in role_mapping:
            return role_mapping[genius_role]

        genius_role_lower = genius_role.lower()
        for key, value in role_mapping.items():
            if key.lower() == genius_role_lower:
                return value

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

        potential_containers = [
            soup.find_all('div', class_=lambda x: x and 'SongInfo' in x),
            soup.find_all('div', class_=lambda x: x and 'Credit' in x),
            soup.find_all('div', class_=lambda x: x and 'ExpandableContent' in x)
        ]

        for i, containers in enumerate(potential_containers):
            logger.debug(f"Méthode {i+1}: Trouvé {len(containers)} éléments potentiels")
            for j, container in enumerate(containers[:3]):
                text = container.get_text(strip=True)[:100]
                classes = container.get('class', [])
                logger.debug(f"  Élément {j+1}: classes={classes}, texte='{text}...'")

    def get_album_url_from_track(self, track_url: str) -> Optional[str]:
        """Récupère l'URL de l'album depuis une page de morceau"""
        try:
            self.page.goto(track_url, wait_until="domcontentloaded")
            time.sleep(1)

            album_link = self.page.query_selector(
                "xpath=//a[contains(@class, 'PrimaryAlbum__Title') or contains(@href, '/albums/')]"
            )

            if album_link:
                return album_link.get_attribute('href')

        except PlaywrightTimeoutError:
            logger.debug("Timeout lors de la récupération de l'URL album")
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

        for i, track in enumerate(tracks):
            try:
                logger.info(f"Scraping du morceau {i+1}/{total}: {track.title}")

                credits = self.scrape_track_credits(track)

                if len(track.credits) > 0:
                    results['success'] += 1
                    logger.info(f"✅ {len(track.credits)} crédits extraits pour {track.title}")
                else:
                    results['failed'] += 1
                    logger.warning(f"❌ Aucun crédit extrait pour {track.title}")

                if progress_callback:
                    progress_callback(i + 1, total, track.title)

            except Exception as e:
                results['failed'] += 1
                results['errors'].append({'track': track.title, 'error': str(e)})
                logger.error(f"Erreur sur {track.title}: {str(e)}")
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
            self.page.goto(track.genius_url, wait_until="domcontentloaded")

            # Gestion des cookies (si nécessaire)
            try:
                self.page.wait_for_selector("#onetrust-banner-sdk", timeout=5000)
                self.page.click("#onetrust-accept-btn-handler", timeout=5000)
                time.sleep(1)
            except PlaywrightTimeoutError:
                pass

            # Attendre que les paroles se chargent
            self.page.wait_for_selector("[data-lyrics-container='true']", timeout=10000)

            time.sleep(1)

            soup = BeautifulSoup(self.page.content(), 'html.parser')

            # ÉTAPE 1: Extraire l'anecdote/bio depuis la section "About"
            anecdotes_text = None
            try:
                bio_selectors = [
                    'div.SongDescription__Content-sc-634b42e-2',
                    'div.RichText__Container-sc-4013e6a2-0',
                    'div[class*="SongDescription__Content"]',
                    'div[class*="RichText__Container"]',
                ]

                for bio_selector in bio_selectors:
                    bio_container = soup.select_one(bio_selector)
                    if bio_container:
                        for embed in bio_container.find_all('div', class_=lambda x: x and 'embedly' in x):
                            embed.decompose()

                        anecdotes_text = bio_container.get_text(separator='\n\n', strip=True)
                        if anecdotes_text and len(anecdotes_text) > 50:
                            anecdotes_text = re.sub(r'\s+', ' ', anecdotes_text)
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
                    text = container.get_text(separator='\n', strip=True)
                    if text:
                        lyrics_parts.append(text)

                lyrics = '\n\n'.join(lyrics_parts)

                lyrics = self._clean_lyrics(lyrics)

                if anecdotes_text:
                    first_tag = re.search(
                        r'\[(?:Intro|Couplet|Refrain|Verse|Chorus|Bridge|Hook|Pre-Chorus|Partie|Part|Outro|Interlude)',
                        lyrics, re.IGNORECASE
                    )

                    if first_tag:
                        lyrics = lyrics[first_tag.start():].strip()
                        logger.debug("🧹 Anecdote retirée des paroles (méthode tag structure)")
                    else:
                        anecdote_normalized = re.sub(r'\s+', ' ', anecdotes_text[:150])
                        lyrics_normalized = re.sub(r'\s+', ' ', lyrics[:200])

                        if anecdote_normalized in lyrics_normalized:
                            cut_point = lyrics.find('\n\n', len(anecdotes_text) - 50)
                            if cut_point > 0:
                                lyrics = lyrics[cut_point + 2:].strip()
                                logger.debug("🧹 Anecdote retirée des paroles (méthode texte)")
                            else:
                                logger.debug("⚠️ Point de coupure introuvable, anecdote conservée")

                logger.info(f"✅ Paroles récupérées pour {track.title} ({len(lyrics.split())} mots)")
            else:
                logger.warning(f"⚠️ Conteneur de paroles non trouvé pour {track.title}")

            time.sleep(DELAY_BETWEEN_REQUESTS)

        except PlaywrightTimeoutError:
            logger.warning(f"Timeout lors du scraping des paroles de {track.title}")
        except Exception as e:
            logger.error(f"Erreur lors du scraping des paroles: {e}")

        return lyrics

    def _extract_anecdotes(self, text: str) -> tuple:
        """Extrait les anecdotes et informations supplémentaires du texte des paroles"""
        anecdotes = []
        lyrics_paragraphs = []

        paragraphs = text.split('\n\n')

        for paragraph in paragraphs:
            paragraph = paragraph.strip()
            if not paragraph:
                continue

            if len(paragraph) > 100 and not self._is_lyrics_paragraph(paragraph):
                anecdotes.append(paragraph)
                logger.debug(f"📝 Anecdote détectée : {paragraph[:50]}...")
            else:
                lyrics_paragraphs.append(paragraph)

        lyrics_cleaned = '\n\n'.join(lyrics_paragraphs)
        anecdotes_text = '\n\n'.join(anecdotes) if anecdotes else None

        return lyrics_cleaned, anecdotes_text

    def _is_lyrics_paragraph(self, text: str) -> bool:
        """Détermine si un paragraphe est des paroles ou une anecdote"""
        lyrics_indicators = [
            r'\[.*?\]',
            r'^\(',
        ]

        for indicator in lyrics_indicators:
            if re.search(indicator, text):
                return True

        sentences = text.count('.') + text.count('!') + text.count('?')
        if sentences > 2:
            return False

        return True

    def _clean_lyrics(self, lyrics: str) -> str:
        """Nettoie les paroles en gardant la mise en page originale - VERSION AMÉLIORÉE"""
        if not lyrics:
            return ""

        lyrics = re.sub(r'^.*?Contributors.*?Lyrics\s*', '', lyrics, flags=re.DOTALL | re.MULTILINE)

        def remove_paroles_tag(text):
            start = text.find('[Paroles de')
            if start == -1:
                return text
            bracket_count = 1
            i = start + len('[Paroles de')
            while i < len(text) and bracket_count > 0:
                if text[i] == '[':
                    bracket_count += 1
                elif text[i] == ']':
                    bracket_count -= 1
                i += 1
            if bracket_count == 0:
                logger.debug(f"🔍 Tag [Paroles de...] supprimé: {text[start:i][:80]}...")
                return text[:start] + text[i:]
            return text

        lyrics = remove_paroles_tag(lyrics)

        lyrics = re.sub(r'You might also like.*?(?=\[|$)', '', lyrics, flags=re.DOTALL | re.MULTILINE)
        lyrics = re.sub(r'\n\d*Embed', '', lyrics, flags=re.MULTILINE)
        lyrics = re.sub(r'\nSee.*?Translations', '', lyrics, flags=re.MULTILINE)

        while True:
            new_lyrics = re.sub(r'\[([^\[\]]*?)\n([^\[\]]*?)\]', r'[\1 \2]', lyrics)
            if new_lyrics == lyrics:
                break
            lyrics = new_lyrics

        lines = lyrics.split('\n')
        cleaned_lines = []
        i = 0

        while i < len(lines):
            current_line = lines[i].rstrip()

            if not current_line.strip():
                cleaned_lines.append('')
                i += 1
                continue

            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()

                if re.match(r'^\([^)]*\)$', next_line):
                    current_line += f" {next_line}"
                    i += 1
                elif re.match(r'^(Yeah|Mhh|Ahh|Oh|Wow|Ay|Ey|Hum|Hmm|Fort|Oui|Non)$', next_line, re.IGNORECASE):
                    current_line += f" ({next_line})"
                    i += 1

            cleaned_lines.append(current_line)
            i += 1

        lyrics = '\n'.join(cleaned_lines)
        lyrics = re.sub(r'\n\s*\n\s*\n+', '\n\n', lyrics)
        lines = lyrics.split('\n')
        cleaned_lines = [line.rstrip() for line in lines]
        lyrics = '\n'.join(cleaned_lines)
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

                credits = self.scrape_track_credits(track)

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

                if len(track.credits) > 0:
                    results['success'] += 1
                    logger.info(f"✅ {len(track.credits)} crédits extraits pour {track.title}")
                else:
                    results['failed'] += 1
                    logger.warning(f"❌ Aucun crédit extrait pour {track.title}")

                if progress_callback:
                    status = f"Crédits + paroles: {track.title}" if include_lyrics else f"Crédits: {track.title}"
                    progress_callback(i + 1, total, status)

            except Exception as e:
                results['failed'] += 1
                results['errors'].append({'track': track.title, 'error': str(e)})
                logger.error(f"Erreur sur {track.title}: {str(e)}")
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

                if progress_callback:
                    progress_callback(i + 1, total, track.title)

            except Exception as e:
                results['failed'] += 1
                results['errors'].append({'track': track.title, 'error': str(e)})
                logger.error(f"Erreur paroles sur {track.title}: {str(e)}")
                track.has_lyrics = False

        logger.info(f"Scraping paroles terminé: {results['lyrics_scraped']} récupérées, {results['failed']} échoués")
        return results
