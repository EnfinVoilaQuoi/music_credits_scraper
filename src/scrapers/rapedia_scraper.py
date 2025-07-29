"""Scraper pour récupérer les BPM et autres infos sur Rapedia.fr"""
import time
import re
from typing import Optional, Dict, Any, List
from urllib.parse import quote
import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

from src.config import SELENIUM_TIMEOUT, DELAY_BETWEEN_REQUESTS
from src.models import Track, Artist
from src.utils.logger import get_logger, log_error


logger = get_logger(__name__)


class RapediaScraper:
    """Scraper pour extraire les infos depuis Rapedia.fr"""
    
    def __init__(self, use_selenium: bool = False):
        """
        Args:
            use_selenium: Si True, utilise Selenium (plus lent mais plus fiable)
                         Si False, utilise requests + BeautifulSoup (plus rapide)
        """
        self.use_selenium = use_selenium
        self.base_url = "https://rapedia.fr"
        self.driver = None
        
        if use_selenium:
            self._init_driver()
    
    def _init_driver(self):
        """Initialise le driver Selenium si nécessaire"""
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.chrome.options import Options
        from webdriver_manager.chrome import ChromeDriverManager
        
        options = Options()
        options.add_argument('--headless')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--window-size=1920,1080')
        
        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=options)
        self.wait = WebDriverWait(self.driver, SELENIUM_TIMEOUT)
        
        logger.info("Driver Selenium initialisé pour Rapedia")
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    def close(self):
        """Ferme le driver si utilisé"""
        if self.driver:
            self.driver.quit()
            logger.info("Driver Selenium fermé")
    
    def search_track(self, track_title: str, artist_name: str) -> Optional[Dict[str, Any]]:
        """Recherche un morceau sur Rapedia"""
        try:
            # Nettoyer le titre (enlever les features entre parenthèses)
            clean_title = re.sub(r'\s*\([^)]*\)', '', track_title)
            clean_title = re.sub(r'\s*feat\..*', '', clean_title, flags=re.IGNORECASE)
            
            # Construire l'URL de recherche
            search_query = f"{artist_name} {clean_title}".strip()
            search_url = f"{self.base_url}/recherche?q={quote(search_query)}"
            
            logger.debug(f"Recherche Rapedia: {search_query}")
            
            if self.use_selenium:
                return self._search_with_selenium(search_url, track_title, artist_name)
            else:
                return self._search_with_requests(search_url, track_title, artist_name)
                
        except Exception as e:
            logger.error(f"Erreur lors de la recherche Rapedia: {e}")
            return None
    
    def _search_with_requests(self, search_url: str, track_title: str, artist_name: str) -> Optional[Dict[str, Any]]:
        """Recherche avec requests + BeautifulSoup"""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = requests.get(search_url, headers=headers, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Chercher les résultats de recherche
            results = soup.find_all('div', class_='search-result') or \
                     soup.find_all('article', class_='track') or \
                     soup.find_all('div', class_='song')
            
            for result in results:
                # Vérifier si c'est le bon artiste et titre
                result_text = result.get_text().lower()
                if artist_name.lower() in result_text and \
                   any(word in result_text for word in track_title.lower().split()[:3]):
                    
                    # Extraire le lien vers la page du morceau
                    link = result.find('a')
                    if link and link.get('href'):
                        track_url = link['href']
                        if not track_url.startswith('http'):
                            track_url = self.base_url + track_url
                        
                        # Récupérer les infos de la page du morceau
                        return self._get_track_info(track_url)
            
            logger.warning(f"Morceau non trouvé sur Rapedia: {track_title} - {artist_name}")
            return None
            
        except Exception as e:
            logger.error(f"Erreur lors de la recherche requests: {e}")
            return None
    
    def _search_with_selenium(self, search_url: str, track_title: str, artist_name: str) -> Optional[Dict[str, Any]]:
        """Recherche avec Selenium"""
        try:
            self.driver.get(search_url)
            time.sleep(2)
            
            # Attendre les résultats
            results = self.wait.until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, ".search-result, .track, .song"))
            )
            
            for result in results:
                result_text = result.text.lower()
                if artist_name.lower() in result_text and \
                   any(word in result_text for word in track_title.lower().split()[:3]):
                    
                    # Cliquer sur le résultat
                    link = result.find_element(By.TAG_NAME, "a")
                    track_url = link.get_attribute('href')
                    
                    # Récupérer les infos
                    return self._get_track_info(track_url)
            
            return None
            
        except TimeoutException:
            logger.warning("Timeout lors de la recherche Selenium")
            return None
    
    def _get_track_info(self, track_url: str) -> Optional[Dict[str, Any]]:
        """Récupère les informations d'un morceau depuis sa page"""
        try:
            if self.use_selenium:
                self.driver.get(track_url)
                time.sleep(1)
                soup = BeautifulSoup(self.driver.page_source, 'html.parser')
            else:
                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
                response = requests.get(track_url, headers=headers, timeout=10)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, 'html.parser')
            
            track_info = {
                'url': track_url,
                'bpm': None,
                'key': None,
                'genre': None,
                'label': None,
                'release_date': None
            }
            
            # Chercher le BPM
            bpm_patterns = [
                r'BPM\s*[:\s]*(\d+)',
                r'Tempo\s*[:\s]*(\d+)',
                r'(\d+)\s*BPM',
                r'bpm["\']?\s*[:\s]*(\d+)'
            ]
            
            page_text = soup.get_text()
            for pattern in bpm_patterns:
                match = re.search(pattern, page_text, re.IGNORECASE)
                if match:
                    track_info['bpm'] = int(match.group(1))
                    logger.info(f"BPM trouvé sur Rapedia: {track_info['bpm']}")
                    break
            
            # Chercher d'autres infos dans les métadonnées ou tableaux
            info_table = soup.find('table', class_='info') or \
                        soup.find('dl', class_='metadata') or \
                        soup.find('div', class_='track-info')
            
            if info_table:
                # Extraire les paires clé-valeur
                rows = info_table.find_all(['tr', 'div', 'dt', 'dd'])
                
                for i in range(0, len(rows), 2):
                    if i + 1 < len(rows):
                        key = rows[i].get_text(strip=True).lower()
                        value = rows[i + 1].get_text(strip=True)
                        
                        if 'tonalité' in key or 'key' in key:
                            track_info['key'] = value
                        elif 'genre' in key:
                            track_info['genre'] = value
                        elif 'label' in key:
                            track_info['label'] = value
                        elif 'date' in key or 'sortie' in key:
                            track_info['release_date'] = value
            
            # Si on a au moins le BPM, c'est un succès
            if track_info['bpm']:
                return track_info
            
            # Sinon, chercher dans les balises meta ou data attributes
            meta_bpm = soup.find('meta', {'property': 'music:bpm'}) or \
                      soup.find('span', {'data-bpm': True})
            
            if meta_bpm:
                bpm_value = meta_bpm.get('content') or meta_bpm.get('data-bpm')
                if bpm_value and bpm_value.isdigit():
                    track_info['bpm'] = int(bpm_value)
                    return track_info
            
            logger.warning(f"Aucune info utile trouvée sur la page: {track_url}")
            return None
            
        except Exception as e:
            logger.error(f"Erreur lors de la récupération des infos: {e}")
            return None
    
    def enrich_track_data(self, track: Track) -> bool:
        """Enrichit les données d'un morceau avec les infos Rapedia"""
        try:
            # Rechercher le morceau
            rapedia_data = self.search_track(track.title, track.artist.name)
            
            if not rapedia_data:
                return False
            
            # Mettre à jour les données (Rapedia est prioritaire)
            if rapedia_data.get('bpm'):
                track.bpm = rapedia_data['bpm']
                logger.info(f"BPM ajouté depuis Rapedia: {track.bpm} pour {track.title}")
            
            if rapedia_data.get('genre') and not track.genre:
                track.genre = rapedia_data['genre']
            
            # Ajouter l'info dans les crédits si on a trouvé un label
            if rapedia_data.get('label'):
                from src.models import Credit, CreditRole
                label_credit = Credit(
                    name=rapedia_data['label'],
                    role=CreditRole.LABEL,
                    source="rapedia"
                )
                track.add_credit(label_credit)
            
            # Respecter le rate limit
            time.sleep(DELAY_BETWEEN_REQUESTS)
            
            return True
            
        except Exception as e:
            logger.error(f"Erreur lors de l'enrichissement Rapedia: {e}")
            return False
    
    def enrich_multiple_tracks(self, tracks: List[Track], progress_callback=None) -> Dict[str, int]:
        """Enrichit plusieurs morceaux avec les données Rapedia"""
        results = {
            'enriched': 0,
            'failed': 0,
            'skipped': 0
        }
        
        total = len(tracks)
        
        for i, track in enumerate(tracks):
            # Vérifier si l'artiste pourrait être sur Rapedia (rap français principalement)
            if self._is_likely_on_rapedia(track.artist.name):
                if self.enrich_track_data(track):
                    results['enriched'] += 1
                else:
                    results['failed'] += 1
            else:
                results['skipped'] += 1
                logger.debug(f"Artiste probablement pas sur Rapedia: {track.artist.name}")
            
            if progress_callback:
                progress_callback(i + 1, total, f"Rapedia: {track.title}")
        
        logger.info(f"Enrichissement Rapedia terminé: {results['enriched']} réussis, "
                   f"{results['failed']} échoués, {results['skipped']} ignorés")
        return results
    
    def _is_likely_on_rapedia(self, artist_name: str) -> bool:
        """Détermine si un artiste a des chances d'être sur Rapedia"""
        # Liste d'artistes connus sur Rapedia (à étendre)
        known_artists = [
            'booba', 'kaaris', 'nekfeu', 'orelsan', 'pnl', 'jul', 'ninho', 
            'damso', 'lomepal', 'vald', 'sch', 'lacrim', 'gradur', 'niska',
            'maes', 'rim\'k', 'rohff', 'kery james', 'mc solaar', 'iam',
            'ntm', 'fonky family', 'lunatic', 'mhd', 'heuss l\'enfoiré',
            'soolking', 'lefa', 'alpha wann', 'freeze corleone', 'zola'
        ]
        
        artist_lower = artist_name.lower()
        
        # Vérifier si c'est un artiste connu
        for known in known_artists:
            if known in artist_lower or artist_lower in known:
                return True
        
        # Heuristiques pour le rap français
        french_indicators = ['rappeur', 'rap fr', 'rap français']
        return any(indicator in artist_lower for indicator in french_indicators)
