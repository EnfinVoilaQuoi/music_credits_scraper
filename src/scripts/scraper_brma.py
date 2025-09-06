"""
Scraper pour les certifications musicales belges Ultratop.be
Script 1: Création de la base de données historique (1995-2024)
"""

import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import random
from datetime import datetime
import logging
from pathlib import Path
import re
from urllib.parse import urljoin

class UltratopScraperInitial:
    """Scraper pour créer la base de données historique des certifications Ultratop"""
    
    def __init__(self, output_dir='./data/ultratop', delay_min=2, delay_max=5):
        """
        Initialisation du scraper
        
        Args:
            output_dir: Répertoire de sortie pour les fichiers CSV
            delay_min: Délai minimum entre requêtes (secondes)
            delay_max: Délai maximum entre requêtes (secondes)
        """
        self.base_url = "https://www.ultratop.be/fr/or-platine"
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.delay_min = delay_min
        self.delay_max = delay_max
        
        # Configuration du logging
        self.setup_logging()
        
        # Headers pour simuler un navigateur
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Cache-Control': 'max-age=0'
        }
        
        # Session pour maintenir les cookies
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        
    def setup_logging(self):
        """Configuration du système de logging"""
        log_file = self.output_dir / f'ultratop_scraper_{datetime.now():%Y%m%d_%H%M%S}.log'
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
        
    def random_delay(self):
        """Délai aléatoire entre les requêtes pour éviter la détection"""
        delay = random.uniform(self.delay_min, self.delay_max)
        time.sleep(delay)
        
    def fetch_page(self, year, category):
        """
        Récupère une page de certifications
        
        Args:
            year: Année (1995-2024)
            category: 'albums' ou 'singles'
            
        Returns:
            BeautifulSoup object ou None si erreur
        """
        url = f"{self.base_url}/{year}/{category}"
        
        try:
            self.logger.info(f"Récupération: {url}")
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            
            return BeautifulSoup(response.content, 'html.parser')
            
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Erreur lors de la récupération de {url}: {e}")
            return None
            
    def parse_certification_date(self, text):
        """
        Parse les dates et niveaux de certification
        
        Args:
            text: Texte contenant les certifications (ex: "08/02/2019: Platine")
            
        Returns:
            Liste de tuples (date, niveau)
        """
        certifications = []
        
        # Pattern pour capturer date et niveau
        pattern = r'(\d{2}/\d{2}/\d{4}):\s*([A-Za-zÀ-ÿ\s]+)'
        matches = re.findall(pattern, text)
        
        for date_str, level in matches:
            try:
                # Conversion de la date
                date = datetime.strptime(date_str, '%d/%m/%Y').strftime('%Y-%m-%d')
                level = level.strip()
                certifications.append((date, level))
            except ValueError:
                self.logger.warning(f"Impossible de parser la date: {date_str}")
                
        return certifications
        
    def extract_certifications(self, soup, year, category):
        """
        Extrait les certifications d'une page
        
        Args:
            soup: BeautifulSoup object de la page
            year: Année de la page
            category: Catégorie (albums/singles)
            
        Returns:
            Liste de dictionnaires avec les données extraites
        """
        certifications = []
        
        # Sélectionner tous les conteneurs de certification
        containers = soup.find_all('div', style=lambda x: x and 'display:table-row' in x)
        
        for container in containers:
            try:
                # Extraction du titre et de l'artiste
                title_div = container.find('div', class_='chart_title')
                if not title_div:
                    continue
                    
                link_elem = title_div.find('a')
                if not link_elem:
                    continue
                    
                # Lien vers la page de détail
                detail_link = urljoin('https://www.ultratop.be', link_elem.get('href', ''))
                
                # Extraction de l'artiste et du titre
                text_content = link_elem.get_text(separator='|', strip=True)
                parts = text_content.split('|')
                
                if len(parts) >= 2:
                    artist = parts[0].strip()
                    title = parts[1].strip()
                else:
                    # Fallback si le format est différent
                    artist = text_content
                    title = ''
                    
                # Extraction des certifications
                company_div = container.find('div', class_='company')
                if company_div:
                    cert_text = company_div.get_text(strip=True)
                    cert_list = self.parse_certification_date(cert_text)
                    
                    # Créer une entrée pour chaque certification
                    for cert_date, cert_level in cert_list:
                        certifications.append({
                            'artist': artist,
                            'title': title,
                            'category': category,
                            'certification_level': cert_level,
                            'certification_date': cert_date,
                            'year_page': year,
                            'detail_url': detail_link,
                            'scraped_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        })
                        
            except Exception as e:
                self.logger.error(f"Erreur lors de l'extraction d'une certification: {e}")
                continue
                
        self.logger.info(f"Extrait {len(certifications)} certifications de {year}/{category}")
        return certifications
        
    def scrape_year_range(self, start_year=1995, end_year=2024):
        """
        Scrape toutes les certifications pour une plage d'années
        
        Args:
            start_year: Année de début (défaut: 1995)
            end_year: Année de fin (défaut: 2024)
            
        Returns:
            DataFrame avec toutes les certifications
        """
        all_certifications = []
        categories = ['albums', 'singles']
        
        total_pages = (end_year - start_year + 1) * len(categories)
        current_page = 0
        
        for year in range(start_year, end_year + 1):
            for category in categories:
                current_page += 1
                self.logger.info(f"Progression: {current_page}/{total_pages} - Année {year}, {category}")
                
                # Délai aléatoire entre les requêtes
                self.random_delay()
                
                # Récupération de la page
                soup = self.fetch_page(year, category)
                if soup:
                    # Extraction des certifications
                    certifications = self.extract_certifications(soup, year, category)
                    all_certifications.extend(certifications)
                    
                    # Sauvegarde intermédiaire tous les 10 pages
                    if current_page % 10 == 0:
                        self.save_intermediate(all_certifications)
                else:
                    self.logger.warning(f"Impossible de récupérer {year}/{category}")
                    
        return pd.DataFrame(all_certifications)
        
    def save_intermediate(self, certifications):
        """Sauvegarde intermédiaire des données"""
        if certifications:
            df = pd.DataFrame(certifications)
            temp_file = self.output_dir / f'ultratop_temp_{datetime.now():%Y%m%d_%H%M%S}.csv'
            df.to_csv(temp_file, index=False, encoding='utf-8-sig')
            self.logger.info(f"Sauvegarde intermédiaire: {temp_file}")
            
    def save_database(self, df, filename=None):
        """
        Sauvegarde la base de données complète
        
        Args:
            df: DataFrame avec les certifications
            filename: Nom du fichier de sortie (optionnel)
        """
        if filename is None:
            filename = f'ultratop_certifications_{datetime.now():%Y%m%d}.csv'
            
        output_path = self.output_dir / filename
        
        # Tri par date de certification décroissante
        df_sorted = df.sort_values(['certification_date', 'artist', 'title'], ascending=[False, True, True])
        
        # Sauvegarde
        df_sorted.to_csv(output_path, index=False, encoding='utf-8-sig')
        self.logger.info(f"Base de données sauvegardée: {output_path}")
        self.logger.info(f"Total: {len(df)} certifications")
        
        # Statistiques
        self.print_statistics(df)
        
    def print_statistics(self, df):
        """Affiche des statistiques sur les données collectées"""
        print("\n=== STATISTIQUES ===")
        print(f"Total certifications: {len(df)}")
        print(f"Artistes uniques: {df['artist'].nunique()}")
        print(f"Albums: {len(df[df['category'] == 'albums'])}")
        print(f"Singles: {len(df[df['category'] == 'singles'])}")
        
        print("\nRépartition par niveau de certification:")
        print(df['certification_level'].value_counts())
        
        print("\nCertifications par année:")
        df['cert_year'] = pd.to_datetime(df['certification_date']).dt.year
        print(df['cert_year'].value_counts().sort_index().tail(10))
        
    def run(self):
        """Lance le scraping complet pour créer la base de données historique"""
        self.logger.info("=== DÉBUT DU SCRAPING HISTORIQUE ULTRATOP ===")
        
        try:
            # Scraping de 1995 à 2024
            df = self.scrape_year_range(1995, 2024)
            
            if not df.empty:
                # Sauvegarde de la base de données complète
                self.save_database(df, 'ultratop_historical_database.csv')
                
                # Création d'un fichier de métadonnées
                metadata = {
                    'created_at': datetime.now().isoformat(),
                    'total_records': len(df),
                    'start_year': 1995,
                    'end_year': 2024,
                    'categories': ['albums', 'singles'],
                    'unique_artists': df['artist'].nunique(),
                    'certification_levels': df['certification_level'].unique().tolist()
                }
                
                import json
                metadata_path = self.output_dir / 'metadata.json'
                with open(metadata_path, 'w', encoding='utf-8') as f:
                    json.dump(metadata, f, indent=2, ensure_ascii=False)
                    
                self.logger.info(f"Métadonnées sauvegardées: {metadata_path}")
                
            else:
                self.logger.error("Aucune donnée collectée")
                
        except Exception as e:
            self.logger.error(f"Erreur fatale: {e}", exc_info=True)
            
        finally:
            self.logger.info("=== FIN DU SCRAPING ===")


if __name__ == "__main__":
    # Création du scraper
    scraper = UltratopScraperInitial(
        output_dir='./data/ultratop',
        delay_min=2,  # Délai minimum entre requêtes
        delay_max=5   # Délai maximum entre requêtes
    )
    
    # Lancement du scraping historique
    scraper.run()
