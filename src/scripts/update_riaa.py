#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script de mise à jour automatique et manuelle des certifications RIAA
Compatible avec le système de gestion unifié des certifications
"""

import pandas as pd
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
import logging
import sys
import time
import argparse
from typing import Optional, List, Dict

# Import du scraper principal
from search_artist_riaa import RIAAScraper

class RIAADatabaseUpdater:
    """Gestionnaire de mise à jour de la base de données RIAA"""
    
    def __init__(self, base_dir='music_credits_scraper'):
        """Initialise le gestionnaire de mise à jour"""
        
        # Configuration des chemins
        self.base_dir = Path(base_dir) if base_dir else Path(__file__).parent.parent.parent
        self.data_dir = self.base_dir / 'data' / 'certifications' / 'riaa'
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        # Chemins des fichiers
        self.db_path = self.data_dir / 'riaa.db'
        self.csv_path = self.data_dir / 'riaa.csv'
        self.log_path = self.data_dir / 'update_log.txt'
        
        # Configuration du logging
        self.setup_logging()
        
        # Initialise le scraper
        self.scraper = None
        
        # Charge ou crée la base de données
        self.init_database()
        
    def setup_logging(self):
        """Configure le système de logging"""
        log_format = '%(asctime)s - %(levelname)s - %(message)s'
        
        # Logger vers fichier
        file_handler = logging.FileHandler(self.log_path, encoding='utf-8')
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(logging.Formatter(log_format))
        
        # Logger vers console
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(logging.Formatter(log_format))
        
        # Configuration du logger
        self.logger = logging.getLogger('RIAA_Updater')
        self.logger.setLevel(logging.INFO)
        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)
        
    def init_database(self):
        """Initialise ou charge la base de données SQLite"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Création de la table principale
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS certifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                artist TEXT NOT NULL,
                title TEXT NOT NULL,
                certification_date TEXT,
                release_date TEXT,
                label TEXT,
                format TEXT,
                award_level TEXT,
                units INTEGER,
                previous_certifications TEXT,
                category TEXT,
                type TEXT,
                genre TEXT,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(artist, title, certification_date, award_level)
            )
        ''')
        
        # Table de suivi des mises à jour
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS update_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                update_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                period_start TEXT,
                period_end TEXT,
                records_added INTEGER,
                records_updated INTEGER,
                status TEXT
            )
        ''')
        
        # Indices pour optimiser les recherches
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_artist ON certifications(artist)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_date ON certifications(certification_date)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_title ON certifications(title)')
        
        conn.commit()
        conn.close()
        
        self.logger.info(f"Base de données initialisée: {self.db_path}")
        
    def get_last_update_date(self) -> Optional[datetime]:
        """Récupère la date de la dernière mise à jour"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT MAX(certification_date) FROM certifications
            WHERE certification_date IS NOT NULL
        ''')
        
        result = cursor.fetchone()
        conn.close()
        
        if result and result[0]:
            try:
                # Parse la date (format MM/DD/YYYY ou YYYY-MM-DD)
                date_str = result[0]
                if '/' in date_str:
                    return datetime.strptime(date_str, "%m/%d/%Y")
                else:
                    return datetime.strptime(date_str, "%Y-%m-%d")
            except Exception as e:
                self.logger.error(f"Erreur parsing date: {e}")
                
        # Si pas de date, retourne October 2017 (fin de la DB historique)
        return datetime(2017, 10, 1)
        
    def update_from_scraped_data(self, data: List[Dict]) -> tuple:
        """Met à jour la base de données avec les données scrapées"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        added = 0
        updated = 0
        
        for record in data:
            try:
                # Prépare les données
                artist = record.get('artist', '').strip()
                title = record.get('title', '').strip()
                cert_date = record.get('certification_date', '').strip()
                award = record.get('award_level', '').strip()
                
                if not all([artist, title]):
                    continue
                    
                # Vérifie si l'enregistrement existe
                cursor.execute('''
                    SELECT id FROM certifications
                    WHERE artist = ? AND title = ? 
                    AND certification_date = ? AND award_level = ?
                ''', (artist, title, cert_date, award))
                
                existing = cursor.fetchone()
                
                if existing:
                    # Mise à jour
                    cursor.execute('''
                        UPDATE certifications
                        SET label = ?, format = ?, units = ?,
                            last_updated = CURRENT_TIMESTAMP
                        WHERE id = ?
                    ''', (
                        record.get('label', ''),
                        record.get('format', ''),
                        record.get('units'),
                        existing[0]
                    ))
                    updated += 1
                else:
                    # Insertion
                    cursor.execute('''
                        INSERT INTO certifications
                        (artist, title, certification_date, release_date,
                         label, format, award_level, units, category, type, genre)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        artist, title, cert_date,
                        record.get('release_date', ''),
                        record.get('label', ''),
                        record.get('format', ''),
                        award,
                        record.get('units'),
                        record.get('category', ''),
                        record.get('type', ''),
                        record.get('genre', '')
                    ))
                    added += 1
                    
            except Exception as e:
                self.logger.error(f"Erreur traitement record: {e}")
                continue
                
        conn.commit()
        conn.close()
        
        return added, updated
        
    def update_recent_certifications(self, months_back: int = 1) -> bool:
        """
        Met à jour les certifications récentes
        
        Args:
            months_back: Nombre de mois à récupérer
            
        Returns:
            bool: True si succès, False sinon
        """
        try:
            # Calcul de la période
            end_date = datetime.now()
            start_date = end_date - timedelta(days=30 * months_back)
            
            # Format pour RIAA (MM/DD/YYYY)
            start_str = start_date.strftime("%m/%d/%Y")
            end_str = end_date.strftime("%m/%d/%Y")
            
            self.logger.info(f"=== MISE À JOUR RIAA ===")
            self.logger.info(f"Période: {start_str} - {end_str}")
            
            # Initialise le scraper
            self.scraper = RIAAScraper(headless=True)
            self.scraper.init_driver()
            
            try:
                # Scrape les certifications récentes
                self.logger.info("Scraping des certifications en cours...")
                results = self.scraper.scrape_by_date_range(
                    start_str, end_str, "certification"
                )
                
                self.logger.info(f"Trouvé {len(results)} certifications")
                
                # Met à jour la base de données
                added, updated = self.update_from_scraped_data(results)
                
                # Enregistre l'historique
                self.log_update(start_str, end_str, added, updated, "SUCCESS")
                
                self.logger.info(f"✓ Ajoutées: {added}")
                self.logger.info(f"✓ Mises à jour: {updated}")
                
                # Export vers CSV
                self.export_to_csv()
                
                return True
                
            finally:
                self.scraper.close_driver()
                
        except Exception as e:
            self.logger.error(f"Erreur mise à jour: {e}")
            self.log_update(start_str, end_str, 0, 0, f"ERROR: {str(e)}")
            return False
            
    def update_missing_months(self) -> bool:
        """Met à jour tous les mois manquants depuis la dernière mise à jour"""
        try:
            # Détermine la dernière date
            last_date = self.get_last_update_date()
            self.logger.info(f"Dernière mise à jour: {last_date:%Y-%m-%d}")
            
            # Calcul du nombre de mois à récupérer
            months_diff = (datetime.now() - last_date).days // 30
            
            if months_diff <= 0:
                self.logger.info("Base de données déjà à jour")
                return True
                
            self.logger.info(f"{months_diff} mois à récupérer")
            
            # Met à jour par tranches mensuelles pour éviter timeout
            current_date = last_date
            total_added = 0
            total_updated = 0
            
            while current_date < datetime.now():
                # Période d'un mois
                start_date = current_date
                end_date = min(current_date + timedelta(days=30), datetime.now())
                
                # Format pour RIAA
                start_str = start_date.strftime("%m/%d/%Y")
                end_str = end_date.strftime("%m/%d/%Y")
                
                self.logger.info(f"Traitement période: {start_str} - {end_str}")
                
                # Initialise le scraper pour cette période
                if not self.scraper:
                    self.scraper = RIAAScraper(headless=True)
                    self.scraper.init_driver()
                    
                try:
                    results = self.scraper.scrape_by_date_range(
                        start_str, end_str, "certification"
                    )
                    
                    if results:
                        added, updated = self.update_from_scraped_data(results)
                        total_added += added
                        total_updated += updated
                        self.logger.info(f"  -> {added} ajoutées, {updated} mises à jour")
                        
                    # Pause entre les requêtes
                    time.sleep(5)
                    
                except Exception as e:
                    self.logger.error(f"Erreur période {start_str}-{end_str}: {e}")
                    
                current_date = end_date
                
            # Ferme le scraper
            if self.scraper:
                self.scraper.close_driver()
                self.scraper = None
                
            self.logger.info(f"Total: {total_added} ajoutées, {total_updated} mises à jour")
            
            # Export final
            self.export_to_csv()
            
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur mise à jour complète: {e}")
            return False
            
    def log_update(self, start: str, end: str, added: int, updated: int, status: str):
        """Enregistre l'historique des mises à jour"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO update_history
            (period_start, period_end, records_added, records_updated, status)
            VALUES (?, ?, ?, ?, ?)
        ''', (start, end, added, updated, status))
        
        conn.commit()
        conn.close()
        
    def export_to_csv(self):
        """Exporte la base de données vers CSV"""
        try:
            conn = sqlite3.connect(self.db_path)
            
            # Lecture de toutes les certifications
            df = pd.read_sql_query('''
                SELECT artist, title, certification_date, release_date,
                       label, format, award_level, units, category, type, genre
                FROM certifications
                ORDER BY certification_date DESC, artist, title
            ''', conn)
            
            conn.close()
            
            # Sauvegarde
            df.to_csv(self.csv_path, index=False, encoding='utf-8-sig')
            self.logger.info(f"Export CSV: {self.csv_path} ({len(df)} lignes)")
            
        except Exception as e:
            self.logger.error(f"Erreur export CSV: {e}")
            
    def manual_update(self):
        """Interface de mise à jour manuelle"""
        print("\n=== MISE À JOUR MANUELLE RIAA ===")
        print("1. Mise à jour du dernier mois")
        print("2. Mise à jour des mois manquants")
        print("3. Mise à jour personnalisée (dates)")
        print("4. Recherche par artiste")
        print("5. Retour")
        
        choice = input("\nVotre choix: ").strip()
        
        if choice == '1':
            self.update_recent_certifications(1)
            
        elif choice == '2':
            self.update_missing_months()
            
        elif choice == '3':
            start = input("Date début (MM/DD/YYYY): ").strip()
            end = input("Date fin (MM/DD/YYYY): ").strip()
            
            self.scraper = RIAAScraper(headless=False)
            self.scraper.init_driver()
            
            try:
                results = self.scraper.scrape_by_date_range(start, end)
                added, updated = self.update_from_scraped_data(results)
                self.logger.info(f"Ajoutées: {added}, Mises à jour: {updated}")
                self.export_to_csv()
            finally:
                self.scraper.close_driver()
                
        elif choice == '4':
            artist = input("Nom de l'artiste: ").strip()
            
            self.scraper = RIAAScraper(headless=False)
            self.scraper.init_driver()
            
            try:
                results = self.scraper.scrape_by_artist(artist)
                added, updated = self.update_from_scraped_data(results)
                self.logger.info(f"Ajoutées: {added}, Mises à jour: {updated}")
                self.export_to_csv()
            finally:
                self.scraper.close_driver()
                
    def get_statistics(self) -> Dict:
        """Retourne les statistiques de la base de données"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        stats = {}
        
        # Total certifications
        cursor.execute('SELECT COUNT(*) FROM certifications')
        stats['total'] = cursor.fetchone()[0]
        
        # Par niveau
        cursor.execute('''
            SELECT award_level, COUNT(*) 
            FROM certifications 
            GROUP BY award_level
        ''')
        stats['by_level'] = dict(cursor.fetchall())
        
        # Top artistes
        cursor.execute('''
            SELECT artist, COUNT(*) as count
            FROM certifications
            GROUP BY artist
            ORDER BY count DESC
            LIMIT 10
        ''')
        stats['top_artists'] = cursor.fetchall()
        
        # Dernière mise à jour
        cursor.execute('''
            SELECT MAX(last_updated) FROM certifications
        ''')
        stats['last_updated'] = cursor.fetchone()[0]
        
        conn.close()
        
        return stats


def main():
    """Fonction principale"""
    parser = argparse.ArgumentParser(description='Mise à jour des certifications RIAA')
    parser.add_argument('--auto', action='store_true', 
                       help='Mise à jour automatique des mois manquants')
    parser.add_argument('--months', type=int, default=1,
                       help='Nombre de mois à récupérer')
    parser.add_argument('--manual', action='store_true',
                       help='Mode manuel interactif')
    parser.add_argument('--stats', action='store_true',
                       help='Afficher les statistiques')
    
    args = parser.parse_args()
    
    # Initialise le gestionnaire
    updater = RIAADatabaseUpdater()
    
    if args.auto:
        # Mise à jour automatique
        success = updater.update_missing_months()
        sys.exit(0 if success else 1)
        
    elif args.manual:
        # Mode manuel
        updater.manual_update()
        
    elif args.stats:
        # Affichage des statistiques
        stats = updater.get_statistics()
        print("\n=== STATISTIQUES RIAA ===")
        print(f"Total certifications: {stats['total']}")
        print(f"Dernière mise à jour: {stats['last_updated']}")
        print("\nPar niveau:")
        for level, count in stats['by_level'].items():
            print(f"  {level}: {count}")
        print("\nTop 10 artistes:")
        for artist, count in stats['top_artists']:
            print(f"  {artist}: {count} certifications")
            
    else:
        # Mode interactif par défaut
        updater.manual_update()


if __name__ == "__main__":
    main()