"""
Scraper Spotify ID avec Selenium - Version modulaire propre
Recherche directe sur open.spotify.com/search
"""
import time
import re
import json
import urllib.parse
import logging
from typing import Optional
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.common.exceptions import TimeoutException, WebDriverException
from webdriver_manager.chrome import ChromeDriverManager

logger = logging.getLogger('SpotifyIDScraper')


class SpotifyIDScraper:
    """Scraper pour récupérer les IDs Spotify via recherche directe Selenium"""

    def __init__(self, cache_file: str = "spotify_ids_cache.json", headless: bool = True):
        """
        Initialise le scraper Spotify ID

        Args:
            cache_file: Fichier de cache JSON
            headless: Mode headless (True par défaut)
        """
        self.cache_file = cache_file
        self.cache = self._load_cache()
        self.headless = headless
        self.driver = None
        self.wait = None
        self.selenium_timeout = 20

        # Patterns pour extraire les IDs Spotify
        self.spotify_id_patterns = [
            r'open\.spotify\.com/(?:intl-[a-z]{2}/)?track/([a-zA-Z0-9]{22})',
            r'spotify\.com/track/([a-zA-Z0-9]{22})',
            r'spotify:track:([a-zA-Z0-9]{22})',
            r'/track/([a-zA-Z0-9]{22})(?:\?|$|/)',
        ]

        logger.info(f"SpotifyIDScraper initialisé (headless={headless})")

    def _load_cache(self):
        """Charge le cache depuis le fichier"""
        try:
            with open(self.cache_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_cache(self):
        """Sauvegarde le cache"""
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.cache, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Erreur sauvegarde cache: {e}")

    def _get_cache_key(self, artist: str, title: str) -> str:
        """Génère une clé de cache"""
        return f"{artist.lower().strip()}::{title.lower().strip()}"

    def _init_selenium_driver(self):
        """Initialise le driver Selenium"""
        if self.driver:
            return  # Déjà initialisé

        logger.info(f"🌐 Initialisation du driver Selenium (headless={self.headless})...")

        try:
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

            # Service avec suppression des logs
            import platform
            if platform.system() == 'Windows':
                service = ChromeService(
                    ChromeDriverManager().install(),
                    log_path='NUL'
                )
            else:
                service = ChromeService(
                    ChromeDriverManager().install(),
                    log_path='/dev/null'
                )

            # Désactiver images pour accélérer
            prefs = {
                "profile.managed_default_content_settings.images": 2,
                "profile.default_content_setting_values.notifications": 2,
            }
            options.add_experimental_option("prefs", prefs)

            self.driver = webdriver.Chrome(service=service, options=options)
            self.wait = WebDriverWait(self.driver, self.selenium_timeout)

            # Masquer les signes d'automation
            self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

            logger.info("✅ Driver Selenium initialisé avec succès")

        except Exception as e:
            logger.error(f"❌ Erreur initialisation Selenium: {e}")
            self.driver = None
            self.wait = None
            raise

    def _handle_cookies(self):
        """Gère les popups de cookies Spotify"""
        try:
            logger.debug("🍪 Gestion des cookies...")
            time.sleep(2)

            cookie_selectors = [
                "button[id='onetrust-accept-btn-handler']",
                "button[data-testid='accept-all-cookies']",
                "button[class*='accept-all']",
                "#onetrust-accept-btn-handler",
            ]

            for selector in cookie_selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for element in elements:
                        if element.is_displayed() and element.is_enabled():
                            element.click()
                            logger.info(f"✅ Clic sur le bouton AGREE: '{element.text[:50]}'")
                            time.sleep(2)
                            return
                except:
                    continue

            logger.debug("✅ Popup CMP fermé ou absent")

        except Exception as e:
            logger.debug(f"Erreur gestion cookies: {e}")

    def extract_spotify_id_from_url(self, url: str) -> Optional[str]:
        """Extrait l'ID Spotify depuis une URL"""
        if not url or 'spotify' not in url.lower():
            return None

        url = url.strip()

        for pattern in self.spotify_id_patterns:
            match = re.search(pattern, url)
            if match:
                spotify_id = match.group(1)
                if len(spotify_id) == 22 and re.match(r'^[a-zA-Z0-9_-]+$', spotify_id):
                    return spotify_id
        return None

    def _calculate_relevance(self, artist: str, title: str, text: str) -> float:
        """Calcule la pertinence d'un résultat"""
        if not text:
            return 0.5

        score = 0.0
        artist_lower = artist.lower()
        title_lower = title.lower()
        text_lower = text.lower()

        # Bonus pour correspondance exacte
        if artist_lower in text_lower:
            score += 0.4
        if title_lower in text_lower:
            score += 0.4

        # Bonus pour mots individuels
        for word in artist_lower.split():
            if len(word) > 2 and word in text_lower:
                score += 0.1

        for word in title_lower.split():
            if len(word) > 2 and word in text_lower:
                score += 0.1

        return min(score, 1.0)

    def get_spotify_id(self, artist: str, title: str) -> Optional[str]:
        """
        Recherche l'ID Spotify via recherche directe open.spotify.com

        Args:
            artist: Nom de l'artiste
            title: Titre du morceau

        Returns:
            L'ID Spotify ou None
        """
        logger.info(f"🔍 Recherche ID Spotify pour: '{artist}' - '{title}'")

        # Vérifier le cache
        cache_key = self._get_cache_key(artist, title)
        if cache_key in self.cache:
            cached_id = self.cache[cache_key]
            if cached_id and cached_id != 'not_found':
                logger.info(f"✅ ID trouvé en cache: {cached_id}")
                return cached_id
            else:
                logger.debug("Échec précédent en cache")
                return None

        try:
            # Initialiser Selenium si nécessaire
            if not self.driver:
                self._init_selenium_driver()

            if not self.driver:
                logger.error("❌ Driver Selenium non disponible")
                return None

            # Essayer plusieurs variantes de requête
            search_queries = [
                f"{artist} {title}",
                f'"{artist}" "{title}"',
                f"{title} {artist}",
            ]

            found_tracks = []

            for query_idx, query in enumerate(search_queries):
                logger.info(f"📝 Essai {query_idx + 1}/{len(search_queries)}: '{query}'")

                try:
                    # URL de recherche Spotify
                    spotify_url = f"https://open.spotify.com/search/{urllib.parse.quote(query)}"

                    logger.debug(f"🌐 Navigation vers: {spotify_url}")
                    self.driver.get(spotify_url)

                    # Gérer les cookies
                    self._handle_cookies()

                    # Attendre le chargement
                    logger.debug("⏳ Attente du chargement...")
                    try:
                        WebDriverWait(self.driver, 15).until(
                            lambda driver: any([
                                driver.find_elements(By.CSS_SELECTOR, "[data-testid*='track']"),
                                driver.find_elements(By.CSS_SELECTOR, "a[href*='/track/']"),
                            ])
                        )
                        logger.debug("✅ Page chargée")
                    except TimeoutException:
                        logger.warning(f"⏰ Timeout pour: {query}")
                        continue

                    # Chercher les liens tracks
                    track_selectors = [
                        "a[href*='/track/'][data-testid]",
                        "div[data-testid*='track'] a[href*='/track/']",
                        "[role='row'] a[href*='/track/']",
                        "a[href*='/track/']",
                    ]

                    logger.debug(f"🔍 Recherche de tracks...")

                    for selector in track_selectors:
                        try:
                            track_links = self.driver.find_elements(By.CSS_SELECTOR, selector)
                            logger.debug(f"  • Sélecteur '{selector}': {len(track_links)} liens")

                            for link in track_links[:10]:
                                try:
                                    href = link.get_attribute('href')
                                    if href and '/track/' in href:
                                        spotify_id = self.extract_spotify_id_from_url(href)
                                        if spotify_id and spotify_id not in [t['id'] for t in found_tracks]:
                                            # Récupérer le texte
                                            try:
                                                link_text = link.text.lower()
                                                parent_text = link.find_element(By.XPATH, "./..").text.lower()
                                                combined_text = f"{link_text} {parent_text}"
                                                relevance = self._calculate_relevance(artist, title, combined_text)
                                            except:
                                                combined_text = ''
                                                relevance = 0.5

                                            found_tracks.append({
                                                'id': spotify_id,
                                                'text': combined_text,
                                                'relevance': relevance,
                                                'href': href
                                            })
                                            logger.debug(f"    🎯 ID={spotify_id}, relevance={relevance:.2f}")

                                except Exception as link_error:
                                    continue
                        except Exception as selector_error:
                            continue

                    # Si on a trouvé des tracks, arrêter
                    if found_tracks:
                        break

                except Exception as e:
                    logger.error(f"❌ Erreur avec requête '{query}': {e}")
                    continue

                # Délai entre requêtes
                if query_idx < len(search_queries) - 1:
                    time.sleep(2)

            # Analyser les résultats
            if found_tracks:
                # Trier par pertinence
                found_tracks.sort(key=lambda x: x['relevance'], reverse=True)

                logger.info(f"📊 {len(found_tracks)} track(s) trouvé(s)")
                logger.info(f"🏆 Top 3:")
                for i, track in enumerate(found_tracks[:3]):
                    logger.info(f"  {i+1}. ID={track['id']} (relevance={track['relevance']:.2f})")

                best_track = found_tracks[0]
                spotify_id = best_track['id']

                logger.info(f"✅ SÉLECTIONNÉ: {spotify_id} (relevance: {best_track['relevance']:.2f})")

                # Sauvegarder en cache
                self.cache[cache_key] = spotify_id
                self._save_cache()

                return spotify_id
            else:
                logger.warning(f"❌ Aucun ID Spotify trouvé pour '{title}'")
                self.cache[cache_key] = 'not_found'
                self._save_cache()
                return None

        except Exception as e:
            logger.error(f"❌ Erreur recherche Spotify: {e}")
            return None

    def get_spotify_page_title(self, spotify_id: str) -> Optional[str]:
        """
        Récupère le titre de la page Spotify pour un ID donné

        Args:
            spotify_id: L'ID Spotify du track

        Returns:
            Le titre de la page ou None
        """
        try:
            spotify_url = f"https://open.spotify.com/track/{spotify_id}"
            logger.debug(f"📄 Récupération du titre de page pour: {spotify_url}")

            # Initialiser Selenium si nécessaire
            if not self.driver:
                self._init_selenium_driver()

            if not self.driver:
                logger.error("❌ Driver Selenium non disponible")
                return None

            # Naviguer vers la page
            self.driver.get(spotify_url)

            # Attendre un peu que la page charge
            time.sleep(2)

            # Récupérer le titre de la page
            page_title = self.driver.title

            if page_title:
                # Nettoyer le titre (enlever " | Spotify" à la fin)
                if " | Spotify" in page_title:
                    page_title = page_title.replace(" | Spotify", "")

                logger.info(f"✅ Titre de page récupéré: {page_title[:50]}...")
                return page_title
            else:
                logger.warning("❌ Titre de page vide")
                return None

        except Exception as e:
            logger.error(f"❌ Erreur récupération titre: {e}")
            return None

    def get_spotify_id_for_track(self, track) -> Optional[str]:
        """
        Méthode compatible avec Track object
        Gère automatiquement les featurings

        Args:
            track: L'objet Track

        Returns:
            L'ID Spotify ou None
        """
        # Déterminer le bon artiste
        if hasattr(track, 'is_featuring') and track.is_featuring:
            if hasattr(track, 'primary_artist_name') and track.primary_artist_name:
                artist_name = track.primary_artist_name
                logger.info(f"🎤 Featuring détecté, utilisation de l'artiste principal: {artist_name}")
            else:
                artist_name = track.artist.name if hasattr(track.artist, 'name') else str(track.artist)
        else:
            artist_name = track.artist.name if hasattr(track.artist, 'name') else str(track.artist)

        return self.get_spotify_id(artist_name, track.title)

    def close(self):
        """Ferme le driver Selenium"""
        if self.driver:
            try:
                self.driver.quit()
                logger.info("✅ SpotifyIDScraper fermé")
            except:
                pass
            finally:
                self.driver = None
                self.wait = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __del__(self):
        """Cleanup automatique"""
        try:
            self.close()
        except:
            pass


# Test rapide
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    scraper = SpotifyIDScraper(headless=True)

    try:
        # Test avec quelques tracks
        tests = [
            ("PNL", "Au DD"),
            ("Orelsan", "La Quête"),
            ("Nekfeu", "Énergie sombre"),
        ]

        for artist, title in tests:
            print(f"\n{'='*60}")
            print(f"Test: {artist} - {title}")
            print('='*60)

            spotify_id = scraper.get_spotify_id(artist, title)

            if spotify_id:
                print(f"✅ Succès: {spotify_id}")
                print(f"URL: https://open.spotify.com/track/{spotify_id}")
            else:
                print(f"❌ Échec")

    finally:
        scraper.close()
