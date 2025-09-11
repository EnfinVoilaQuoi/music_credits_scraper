"""
Gestionnaire unifié des certifications musicales
Intègre SNEP (France), RIAA (USA) et BRMA (Belgique)
Fichier: music_credits_scraper/src/managers/certification_manager.py
"""

import pandas as pd
import sqlite3
from pathlib import Path
from datetime import datetime
import json
import logging
import subprocess
import sys
from typing import Dict, List, Optional

class CertificationManager:
    """Gestionnaire unifié pour toutes les certifications musicales"""
    
    def __init__(self, base_dir='music_credits_scraper'):
        """Initialise le gestionnaire de certifications"""
        
        # Obtenir le chemin racine du projet de manière fiable
        self.base_dir = Path(__file__).parent.parent.parent  # Depuis src/utils/ remonter au projet
        self.data_dir = self.base_dir / 'data' / 'certifications'
        self.db_path = self.data_dir / 'unified_certifications.db'
        
        # Chemins des données par organisme
        self.paths = {
            'SNEP': {
                'csv': self.data_dir / 'snep' / 'certif_snep.csv',
                'script': self.base_dir / 'src' / 'scripts' / 'update_snep.py',
                'country': 'FR',
                'name': 'Syndicat National de l\'Édition Phonographique'
            },
            'RIAA': {
                'csv': self.data_dir / 'riaa' / 'riaa.csv',
                'script': self.base_dir / 'src' / 'scripts' / 'update_riaa.py',
                'country': 'US',
                'name': 'Recording Industry Association of America'
            },
            'BRMA': {
                'csv': self.data_dir / 'brma' / 'certif_brma.csv',
                'script': self.base_dir / 'src' / 'scripts' / 'update_brma.py',
                'country': 'BE',
                'name': 'Belgian Recorded Music Association'
            }
        }
        
        # Tables de conversion des seuils
        self.thresholds = {
            'FR': {  # SNEP
                'singles': {'Or': 15000000, 'Platine': 30000000, 'Diamant': 50000000},
                'albums': {'Or': 50000, 'Platine': 100000, 'Diamant': 500000}
            },
            'US': {  # RIAA
                'singles': {'Gold': 500000, 'Platinum': 1000000, 'Diamond': 10000000},
                'albums': {'Gold': 500000, 'Platinum': 1000000, 'Diamond': 10000000}
            },
            'BE': {  # BRMA
                'singles': {'Or': 10000, 'Platine': 20000, 'Diamant': 40000},
                'albums': {'Or': 10000, 'Platine': 20000, 'Diamant': 200000}
            }
        }
        
        # Mapping des niveaux de certification
        self.level_mapping = {
            # Français vers anglais
            'Or': 'Gold',
            'Platine': 'Platinum',
            'Diamant': 'Diamond',
            'Double Or': '2x Gold',
            'Double Platine': '2x Platinum',
            'Triple Platine': '3x Platinum',
            'Quadruple Platine': '4x Platinum',
            # Anglais (déjà normalisé)
            'Gold': 'Gold',
            'Platinum': 'Platinum',
            'Diamond': 'Diamond',
            'Multi-Platinum': 'Multi-Platinum',
            '2x Multi-Platinum': '2x Platinum',
            '3x Multi-Platinum': '3x Platinum',
            '4x Multi-Platinum': '4x Platinum',
            '5x Multi-Platinum': '5x Platinum',
            '6x Multi-Platinum': '6x Platinum',
            '7x Multi-Platinum': '7x Platinum',
            '8x Multi-Platinum': '8x Platinum',
            '9x Multi-Platinum': '9x Platinum',
            '10x Multi-Platinum': '10x Platinum'
        }
        
        self.setup_logging()
        self.initialize_database()
        
    def setup_logging(self):
        """Configure le système de logging"""
        log_dir = self.base_dir / 'data' / 'logs'
        log_dir.mkdir(exist_ok=True)
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_dir / f'certification_manager_{datetime.now():%Y%m%d}.log'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
        
    def initialize_database(self):
        """Initialise la base de données SQLite unifiée"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Table principale des certifications
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS certifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artist TEXT NOT NULL,
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            certification_level TEXT NOT NULL,
            certification_level_normalized TEXT,
            certification_date DATE NOT NULL,
            country_code TEXT NOT NULL,
            certifying_body TEXT NOT NULL,
            detail_url TEXT,
            threshold_units INTEGER,
            sales_figures INTEGER,
            streaming_figures BIGINT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(artist, title, certification_level, certification_date, country_code)
        )
        ''')
        
        # Table des seuils de certification
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS certification_thresholds (
            country_code TEXT,
            certification_level TEXT,
            category TEXT,
            threshold_value INTEGER,
            effective_date DATE,
            PRIMARY KEY (country_code, certification_level, category, effective_date)
        )
        ''')
        
        # Table de suivi des mises à jour
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS update_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            certifying_body TEXT NOT NULL,
            update_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            records_added INTEGER,
            records_updated INTEGER,
            status TEXT,
            error_message TEXT
        )
        ''')
        
        # Index pour performances
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_artist ON certifications(artist)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_date ON certifications(certification_date)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_country ON certifications(country_code)')
        
        conn.commit()
        conn.close()
        
        self.logger.info("Base de données initialisée")
        
    def load_snep_data(self):
        """Charge et normalise les données SNEP"""
        csv_path = self.paths['SNEP']['csv']
        if not csv_path.exists():
            self.logger.warning(f"Fichier SNEP non trouvé: {csv_path}")
            return pd.DataFrame()
            
        try:
            df = pd.read_csv(csv_path, encoding='utf-8-sig')
            
            # Normalisation pour le format unifié
            df['country_code'] = 'FR'
            df['certifying_body'] = 'SNEP'
            
            # Normalisation des niveaux
            df['certification_level_normalized'] = df['certification'].map(
                lambda x: self.level_mapping.get(x, x)
            )
            
            # Renommage des colonnes pour correspondre au schéma unifié
            df = df.rename(columns={
                'interprete': 'artist',
                'titre': 'title',
                'categorie': 'category',
                'certification': 'certification_level',
                'date_constat': 'certification_date'
            })
            
            self.logger.info(f"Chargé {len(df)} certifications SNEP")
            return df
            
        except Exception as e:
            self.logger.error(f"Erreur chargement SNEP: {e}")
            return pd.DataFrame()
            
    def load_riaa_data(self):
        """Charge et normalise les données RIAA"""
        csv_path = self.paths['RIAA']['csv']
        if not csv_path.exists():
            self.logger.warning(f"Fichier RIAA non trouvé: {csv_path}")
            return pd.DataFrame()
            
        try:
            df = pd.read_csv(csv_path, encoding='utf-8')
            
            # Normalisation pour le format unifié
            df['country_code'] = 'US'
            df['certifying_body'] = 'RIAA'
            
            # Normalisation des colonnes
            df = df.rename(columns={
                'Artist': 'artist',
                'Title': 'title',
                'Format_Type': 'category',
                'Certification_Type': 'certification_level',
                'Certification_Date': 'certification_date'
            })
            
            # Normalisation des catégories
            df['category'] = df['category'].str.lower()
            df['category'] = df['category'].replace({'single': 'singles', 'album': 'albums'})
            
            # Normalisation des niveaux
            df['certification_level_normalized'] = df['certification_level'].map(
                lambda x: self.level_mapping.get(x, x)
            )
            
            # Conversion des dates
            df['certification_date'] = pd.to_datetime(df['certification_date']).dt.strftime('%Y-%m-%d')
            
            self.logger.info(f"Chargé {len(df)} certifications RIAA")
            return df
            
        except Exception as e:
            self.logger.error(f"Erreur chargement RIAA: {e}")
            return pd.DataFrame()
            
    def load_brma_data(self):
        """Charge et normalise les données BRMA (Belgique)"""
        csv_path = self.paths['BRMA']['csv']
        if not csv_path.exists():
            self.logger.warning(f"Fichier BRMA non trouvé: {csv_path}")
            return pd.DataFrame()
            
        try:
            df = pd.read_csv(csv_path, encoding='utf-8-sig')
            
            # Normalisation pour le format unifié
            df['country_code'] = 'BE'
            df['certifying_body'] = 'BRMA'
            
            # Normalisation des niveaux
            df['certification_level_normalized'] = df['certification_level'].map(
                lambda x: self.level_mapping.get(x, x)
            )
            
            self.logger.info(f"Chargé {len(df)} certifications BRMA")
            return df
            
        except Exception as e:
            self.logger.error(f"Erreur chargement BRMA: {e}")
            return pd.DataFrame()
            
    def update_unified_database(self):
        """Met à jour la base de données unifiée avec toutes les sources"""
        self.logger.info("=== MISE À JOUR BASE UNIFIÉE ===")
        
        # Chargement des données
        dfs = []
        
        snep_df = self.load_snep_data()
        if not snep_df.empty:
            dfs.append(snep_df)
            
        riaa_df = self.load_riaa_data()
        if not riaa_df.empty:
            dfs.append(riaa_df)
            
        brma_df = self.load_brma_data()
        if not brma_df.empty:
            dfs.append(brma_df)
            
        if not dfs:
            self.logger.error("Aucune donnée à charger")
            return
            
        # Combinaison des DataFrames
        unified_df = pd.concat(dfs, ignore_index=True)
        
        # Colonnes requises
        required_cols = ['artist', 'title', 'category', 'certification_level', 
                        'certification_date', 'country_code', 'certifying_body']
        
        # Vérifier les colonnes manquantes
        for col in required_cols:
            if col not in unified_df.columns:
                unified_df[col] = None
                
        # Ajout timestamp
        unified_df['updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # Sauvegarde dans SQLite
        conn = sqlite3.connect(self.db_path)
        
        # Écriture avec gestion des doublons
        for _, row in unified_df.iterrows():
            try:
                row_dict = row.to_dict()
                
                # Requête d'insertion ou mise à jour
                placeholders = ', '.join(['?' for _ in row_dict])
                columns = ', '.join(row_dict.keys())
                
                query = f'''
                INSERT OR REPLACE INTO certifications ({columns})
                VALUES ({placeholders})
                '''
                
                conn.execute(query, list(row_dict.values()))
                
            except sqlite3.Error as e:
                self.logger.error(f"Erreur insertion: {e}")
                continue
                
        conn.commit()
        
        # Statistiques
        cursor = conn.cursor()
        stats = {}
        for body in ['SNEP', 'RIAA', 'BRMA']:
            cursor.execute(
                'SELECT COUNT(*) FROM certifications WHERE certifying_body = ?',
                (body,)
            )
            stats[body] = cursor.fetchone()[0]
            
        conn.close()
        
        self.logger.info(f"Base unifiée mise à jour: {stats}")
        
        # Sauvegarde CSV de backup
        backup_path = self.data_dir / f'unified_backup_{datetime.now():%Y%m%d}.csv'
        unified_df.to_csv(backup_path, index=False, encoding='utf-8-sig')
        self.logger.info(f"Backup créé: {backup_path}")
        
    def trigger_update(self, source: str):
        """
        Déclenche la mise à jour d'une source spécifique
        
        Args:
            source: 'SNEP', 'RIAA', ou 'BRMA'
        """
        if source not in self.paths:
            self.logger.error(f"Source inconnue: {source}")
            return False
            
        script_path = self.paths[source]['script']
        
        if not script_path.exists():
            self.logger.error(f"Script non trouvé: {script_path}")
            return False
            
        try:
            self.logger.info(f"Lancement mise à jour {source}")
            
            # Exécution du script de mise à jour
            result = subprocess.run(
                [sys.executable, str(script_path), '--mode', 'once'],
                capture_output=True,
                text=True,
                cwd=self.base_dir
            )
            
            if result.returncode == 0:
                self.logger.info(f"Mise à jour {source} réussie")
                
                # Log dans la base
                conn = sqlite3.connect(self.db_path)
                conn.execute('''
                    INSERT INTO update_log (certifying_body, status)
                    VALUES (?, ?)
                ''', (source, 'SUCCESS'))
                conn.commit()
                conn.close()
                
                return True
            else:
                self.logger.error(f"Erreur mise à jour {source}: {result.stderr}")
                return False
                
        except Exception as e:
            self.logger.error(f"Erreur exécution script {source}: {e}")
            return False
            
    def update_all_sources(self):
        """Met à jour toutes les sources de certification"""
        self.logger.info("=== MISE À JOUR COMPLÈTE ===")
        
        results = {}
        
        # Mise à jour de chaque source
        for source in ['SNEP', 'RIAA', 'BRMA']:
            results[source] = self.trigger_update(source)
            
        # Mise à jour de la base unifiée
        self.update_unified_database()
        
        # Rapport
        self.generate_update_report(results)
        
        return results
        
    def generate_update_report(self, results: Dict):
        """Génère un rapport de mise à jour"""
        report_dir = self.data_dir / 'reports'
        report_dir.mkdir(exist_ok=True)
        
        report_path = report_dir / f'update_report_{datetime.now():%Y%m%d_%H%M%S}.json'
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Statistiques globales
        cursor.execute('SELECT COUNT(*) FROM certifications')
        total = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(DISTINCT artist) FROM certifications')
        artists = cursor.fetchone()[0]
        
        # Stats par pays
        cursor.execute('''
            SELECT country_code, COUNT(*) 
            FROM certifications 
            GROUP BY country_code
        ''')
        by_country = dict(cursor.fetchall())
        
        conn.close()
        
        report = {
            'timestamp': datetime.now().isoformat(),
            'update_results': results,
            'statistics': {
                'total_certifications': total,
                'unique_artists': artists,
                'by_country': by_country
            }
        }
        
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
            
        self.logger.info(f"Rapport généré: {report_path}")
        
    def query_artist(self, artist_name: str, country: Optional[str] = None):
        """
        Recherche les certifications d'un artiste
        
        Args:
            artist_name: Nom de l'artiste
            country: Code pays optionnel (FR, US, BE)
        """
        conn = sqlite3.connect(self.db_path)
        
        query = '''
            SELECT * FROM certifications 
            WHERE artist LIKE ?
        '''
        params = [f'%{artist_name}%']
        
        if country:
            query += ' AND country_code = ?'
            params.append(country)
            
        query += ' ORDER BY certification_date DESC'
        
        df = pd.read_sql(query, conn, params=params)
        conn.close()
        
        return df


def main():
    """Fonction principale pour utilisation en ligne de commande"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Gestionnaire unifié de certifications')
    parser.add_argument('--update', choices=['all', 'snep', 'riaa', 'brma'],
                       help='Mettre à jour les certifications')
    parser.add_argument('--query', type=str,
                       help='Rechercher un artiste')
    parser.add_argument('--country', choices=['FR', 'US', 'BE'],
                       help='Filtrer par pays')
    
    args = parser.parse_args()
    
    manager = CertificationManager()
    
    if args.update:
        if args.update == 'all':
            manager.update_all_sources()
        else:
            manager.trigger_update(args.update.upper())
            manager.update_unified_database()
            
    elif args.query:
        results = manager.query_artist(args.query, args.country)
        print(f"\nRésultats pour '{args.query}':")
        print(results[['artist', 'title', 'certification_level', 'certification_date', 'country_code']])
        
    else:
        # Mode interactif
        print("\n=== GESTIONNAIRE DE CERTIFICATIONS ===")
        print("1. Mettre à jour SNEP (France)")
        print("2. Mettre à jour RIAA (USA)")
        print("3. Mettre à jour BRMA (Belgique)")
        print("4. Mettre à jour TOUT")
        print("5. Rechercher un artiste")
        print("6. Quitter")
        
        while True:
            choice = input("\nVotre choix: ").strip()
            
            if choice == '1':
                manager.trigger_update('SNEP')
                manager.update_unified_database()
            elif choice == '2':
                manager.trigger_update('RIAA')
                manager.update_unified_database()
            elif choice == '3':
                manager.trigger_update('BRMA')
                manager.update_unified_database()
            elif choice == '4':
                manager.update_all_sources()
            elif choice == '5':
                artist = input("Nom de l'artiste: ").strip()
                results = manager.query_artist(artist)
                print(results[['artist', 'title', 'certification_level', 'certification_date', 'country_code']].head(20))
            elif choice == '6':
                break
            else:
                print("Choix invalide")


if __name__ == "__main__":
    main()