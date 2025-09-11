"""
Scraper pour les certifications musicales belges Ultratop.be
Script 2: Mise à jour automatique et manuelle de la base de données
"""

import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import random
from datetime import datetime, timedelta
import logging
from pathlib import Path
import re
from urllib.parse import urljoin
import json
import schedule
import argparse
import sys

class UltratopUpdater:
    """Scraper pour mettre à jour la base de données des certifications Ultratop"""
    
    def __init__(self, database_path='./data/ultratop/ultratop_historical_database.csv', 
                 output_dir='./data/ultratop', delay_min=2, delay_max=5):
        """
        Initialisation du scraper de mise à jour
        
        Args:
            database_path: Chemin vers la base de données existante
            output_dir: Répertoire de sortie pour les fichiers
            delay_min: Délai minimum entre requêtes (secondes)
            delay_max: Délai maximum entre requêtes (secondes)
        """
        self.base_url = "https://www.ultratop.be/fr/or-platine"
        self.database_path = Path(database_path)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.delay_min = delay_min
        self.delay_max = delay_max
        
        # Chargement de la base de données existante
        self.load_existing_database()
        
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
        
    def load_existing_database(self):
        """Charge la base de données existante"""
        if self.database_path.exists():
            self.existing_db = pd.read_csv(self.database_path, encoding='utf-8-sig')
            self.logger_print(f"Base de données chargée: {len(self.existing_db)} enregistrements")
            
            # Création d'un index pour vérification rapide des doublons
            self.existing_keys = set()
            for _, row in self.existing_db.iterrows():
                key = f"{row['artist']}|{row['title']}|{row['certification_level']}|{row['certification_date']}"
                self.existing_keys.add(key)
        else:
            self.existing_db = pd.DataFrame()
            self.existing_keys = set()
            self.logger_print("Aucune base de données existante trouvée. Création d'une nouvelle.")
            
    def logger_print(self, message):
        """Print avec gestion d'erreur si logger pas encore initialisé"""
        print(message)
        if hasattr(self, 'logger'):
            self.logger.info(message)
            
    def setup_logging(self):
        """Configuration du système de logging"""
        log_dir = self.output_dir / 'logs'
        log_dir.mkdir(exist_ok=True)
        
        log_file = log_dir / f'ultratop_update_{datetime.now():%Y%m%d_%H%M%S}.log'
        
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
        """Délai aléatoire entre les requêtes"""
        delay = random.uniform(self.delay_min, self.delay_max)
        time.sleep(delay)
        
    def fetch_page(self, year, category):
        """
        Récupère une page de certifications
        
        Args:
            year: Année
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
        """Parse les dates et niveaux de certification"""
        certifications = []
        
        pattern = r'(\d{2}/\d{2}/\d{4}):\s*([A-Za-zÀ-ÿ\s]+)'
        matches = re.findall(pattern, text)
        
        for date_str, level in matches:
            try:
                date = datetime.strptime(date_str, '%d/%m/%Y').strftime('%Y-%m-%d')
                level = level.strip()
                certifications.append((date, level))
            except ValueError:
                self.logger.warning(f"Impossible de parser la date: {date_str}")
                
        return certifications
        
    def extract_certifications(self, soup, year, category):
        """Extrait les certifications d'une page"""
        certifications = []
        
        containers = soup.find_all('div', style=lambda x: x and 'display:table-row' in x)
        
        for container in containers:
            try:
                title_div = container.find('div', class_='chart_title')
                if not title_div:
                    continue
                    
                link_elem = title_div.find('a')
                if not link_elem:
                    continue
                    
                detail_link = urljoin('https://www.ultratop.be', link_elem.get('href', ''))
                
                text_content = link_elem.get_text(separator='|', strip=True)
                parts = text_content.split('|')
                
                if len(parts) >= 2:
                    artist = parts[0].strip()
                    title = parts[1].strip()
                else:
                    artist = text_content
                    title = ''
                    
                company_div = container.find('div', class_='company')
                if company_div:
                    cert_text = company_div.get_text(strip=True)
                    cert_list = self.parse_certification_date(cert_text)
                    
                    for cert_date, cert_level in cert_list:
                        # Vérifier si cette certification existe déjà
                        key = f"{artist}|{title}|{cert_level}|{cert_date}"
                        
                        if key not in self.existing_keys:
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
                self.logger.error(f"Erreur lors de l'extraction: {e}")
                continue
                
        return certifications
        
    def update_current_year(self):
        """Met à jour les certifications de l'année en cours"""
        current_year = datetime.now().year
        new_certifications = []
        categories = ['albums', 'singles']
        
        self.logger.info(f"=== Mise à jour pour l'année {current_year} ===")
        
        for category in categories:
            self.random_delay()
            
            soup = self.fetch_page(current_year, category)
            if soup:
                certifications = self.extract_certifications(soup, current_year, category)
                new_certifications.extend(certifications)
                self.logger.info(f"Trouvé {len(certifications)} nouvelles certifications pour {current_year}/{category}")
                
        return new_certifications
        
    def update_recent_years(self, years_back=2):
        """
        Met à jour les certifications des années récentes
        
        Args:
            years_back: Nombre d'années à vérifier en arrière
        """
        current_year = datetime.now().year
        new_certifications = []
        categories = ['albums', 'singles']
        
        for year in range(current_year - years_back, current_year + 1):
            self.logger.info(f"=== Vérification année {year} ===")
            
            for category in categories:
                self.random_delay()
                
                soup = self.fetch_page(year, category)
                if soup:
                    certifications = self.extract_certifications(soup, year, category)
                    new_certifications.extend(certifications)
                    
                    if certifications:
                        self.logger.info(f"Trouvé {len(certifications)} nouvelles certifications pour {year}/{category}")
                        
        return new_certifications
        
    def save_updated_database(self, new_certifications):
        """Sauvegarde la base de données mise à jour"""
        if not new_certifications:
            self.logger.info("Aucune nouvelle certification trouvée")
            return
            
        # Création d'un DataFrame avec les nouvelles certifications
        new_df = pd.DataFrame(new_certifications)
        
        # Ajout à la base existante
        if not self.existing_db.empty:
            updated_db = pd.concat([self.existing_db, new_df], ignore_index=True)
        else:
            updated_db = new_df
            
        # Tri par date décroissante
        updated_db = updated_db.sort_values(['certification_date', 'artist', 'title'], 
                                          ascending=[False, True, True])
        
        # Backup de l'ancienne base
        if self.database_path.exists():
            backup_path = self.output_dir / 'backups'
            backup_path.mkdir(exist_ok=True)
            backup_file = backup_path / f'backup_{datetime.now():%Y%m%d_%H%M%S}.csv'
            self.existing_db.to_csv(backup_file, index=False, encoding='utf-8-sig')
            self.logger.info(f"Backup créé: {backup_file}")
            
        # Sauvegarde de la nouvelle base
        updated_db.to_csv(self.database_path, index=False, encoding='utf-8-sig')
        self.logger.info(f"Base de données mise à jour: {self.database_path}")
        self.logger.info(f"Ajouté {len(new_certifications)} nouvelles certifications")
        
        # Mise à jour du fichier de métadonnées
        self.update_metadata(updated_db, len(new_certifications))
        
        # Rapport de mise à jour
        self.generate_update_report(new_certifications)
        
    def update_metadata(self, updated_db, new_count):
        """Met à jour le fichier de métadonnées"""
        metadata_path = self.output_dir / 'metadata.json'
        
        if metadata_path.exists():
            with open(metadata_path, 'r', encoding='utf-8') as f:
                metadata = json.load(f)
        else:
            metadata = {}
            
        metadata['last_update'] = datetime.now().isoformat()
        metadata['total_records'] = len(updated_db)
        metadata['new_records_added'] = new_count
        metadata['unique_artists'] = updated_db['artist'].nunique()
        
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
            
    def generate_update_report(self, new_certifications):
        """Génère un rapport de mise à jour"""
        if not new_certifications:
            return
            
        report_dir = self.output_dir / 'reports'
        report_dir.mkdir(exist_ok=True)
        
        report_file = report_dir / f'update_report_{datetime.now():%Y%m%d_%H%M%S}.txt'
        
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write(f"RAPPORT DE MISE À JOUR ULTRATOP\n")
            f.write(f"{'=' * 50}\n")
            f.write(f"Date: {datetime.now():%Y-%m-%d %H:%M:%S}\n")
            f.write(f"Nouvelles certifications: {len(new_certifications)}\n\n")
            
            f.write("DÉTAIL DES NOUVELLES CERTIFICATIONS:\n")
            f.write("-" * 50 + "\n")
            
            for cert in sorted(new_certifications, key=lambda x: x['certification_date'], reverse=True):
                f.write(f"{cert['certification_date']} - {cert['certification_level']}: "
                       f"{cert['artist']} - {cert['title']} ({cert['category']})\n")
                
        self.logger.info(f"Rapport généré: {report_file}")
        
    def run_manual_update(self, years_back=2):
        """Lance une mise à jour manuelle"""
        self.logger.info("=== MISE À JOUR MANUELLE ===")
        
        try:
            # Tenter d'abord de récupérer les pages manquantes
            new_certifications = self.retry_missing_pages()
            
            # Mise à jour des années récentes
            recent_certifications = self.update_recent_years(years_back)
            new_certifications.extend(recent_certifications)
            
            # Sauvegarde
            self.save_updated_database(new_certifications)
            
            self.logger.info("=== FIN DE LA MISE À JOUR MANUELLE ===")
            
        except Exception as e:
            self.logger.error(f"Erreur lors de la mise à jour: {e}", exc_info=True)
            
    def run_scheduled_update(self):
        """Lance une mise à jour programmée (mensuelle)"""
        self.logger.info("=== MISE À JOUR PROGRAMMÉE ===")
        
        try:
            # Tenter d'abord de récupérer les pages manquantes
            new_certifications = self.retry_missing_pages()
            
            # Mise à jour de l'année en cours uniquement
            current_year_certifications = self.update_current_year()
            new_certifications.extend(current_year_certifications)
            
            # Sauvegarde
            self.save_updated_database(new_certifications)
            
            self.logger.info("=== FIN DE LA MISE À JOUR PROGRAMMÉE ===")
            
        except Exception as e:
            self.logger.error(f"Erreur lors de la mise à jour programmée: {e}", exc_info=True)
            
    def schedule_monthly_updates(self, day_of_month=1, hour=3):
        """
        Programme les mises à jour mensuelles
        
        Args:
            day_of_month: Jour du mois pour la mise à jour (défaut: 1er)
            hour: Heure de la mise à jour (défaut: 3h du matin)
        """
        # Configuration du job mensuel
        schedule.every().month.at(f"{hour:02d}:00").do(self.run_scheduled_update)
        
        self.logger.info(f"Mise à jour mensuelle programmée pour le {day_of_month} de chaque mois à {hour}h")
        
        # Boucle d'exécution
        while True:
            schedule.run_pending()
            time.sleep(3600)  # Vérification toutes les heures


def main():
    """Fonction principale avec interface en ligne de commande"""
    parser = argparse.ArgumentParser(description='Mise à jour des certifications Ultratop')
    parser.add_argument('--mode', choices=['manual', 'scheduled', 'once'], 
                       default='manual',
                       help='Mode de mise à jour: manual (interactif), scheduled (programmé), once (une fois)')
    parser.add_argument('--years-back', type=int, default=2,
                       help='Nombre d\'années à vérifier en arrière (défaut: 2)')
    parser.add_argument('--database', type=str, 
                       default='./data/ultratop/ultratop_historical_database.csv',
                       help='Chemin vers la base de données')
    parser.add_argument('--output-dir', type=str, default='./data/ultratop',
                       help='Répertoire de sortie')
    parser.add_argument('--delay-min', type=float, default=2,
                       help='Délai minimum entre requêtes (secondes)')
    parser.add_argument('--delay-max', type=float, default=5,
                       help='Délai maximum entre requêtes (secondes)')
    
    args = parser.parse_args()
    
    # Création de l'updater
    updater = UltratopUpdater(
        database_path=args.database,
        output_dir=args.output_dir,
        delay_min=args.delay_min,
        delay_max=args.delay_max
    )
    
    if args.mode == 'manual':
        # Mode interactif
        print("\n=== MISE À JOUR ULTRATOP - MODE MANUEL ===")
        print("1. Mise à jour de l'année en cours uniquement")
        print("2. Mise à jour des 2 dernières années")
        print("3. Mise à jour personnalisée (choisir le nombre d'années)")
        print("4. Programmer les mises à jour mensuelles")
        print("5. Quitter")
        
        while True:
            choice = input("\nVotre choix (1-5): ").strip()
            
            if choice == '1':
                new_certs = updater.update_current_year()
                updater.save_updated_database(new_certs)
                
            elif choice == '2':
                updater.run_manual_update(years_back=2)
                
            elif choice == '3':
                years = input("Nombre d'années à vérifier en arrière: ").strip()
                try:
                    years = int(years)
                    updater.run_manual_update(years_back=years)
                except ValueError:
                    print("Nombre invalide")
                    
            elif choice == '4':
                print("\nConfiguration des mises à jour mensuelles:")
                day = input("Jour du mois (1-28) [défaut: 1]: ").strip() or "1"
                hour = input("Heure (0-23) [défaut: 3]: ").strip() or "3"
                
                try:
                    day = int(day)
                    hour = int(hour)
                    print(f"\nLancement des mises à jour mensuelles (jour {day} à {hour}h)")
                    print("Appuyez sur Ctrl+C pour arrêter")
                    updater.schedule_monthly_updates(day, hour)
                except ValueError:
                    print("Valeurs invalides")
                except KeyboardInterrupt:
                    print("\nArrêt des mises à jour programmées")
                    
            elif choice == '5':
                print("Au revoir!")
                break
                
            else:
                print("Choix invalide")
                
    elif args.mode == 'scheduled':
        # Mode programmé (pour cron ou service système)
        print("Lancement des mises à jour programmées mensuelles")
        print("Appuyez sur Ctrl+C pour arrêter")
        try:
            updater.schedule_monthly_updates()
        except KeyboardInterrupt:
            print("\nArrêt des mises à jour programmées")
            
    elif args.mode == 'once':
        # Mode une seule fois (pour cron ou scripts)
        updater.run_manual_update(years_back=args.years_back)
        
    sys.exit(0)


if __name__ == "__main__":
    main()