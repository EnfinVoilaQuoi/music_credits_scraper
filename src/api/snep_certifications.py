"""Gestionnaire pour les certifications SNEP"""
import pandas as pd
import sqlite3
from pathlib import Path
from datetime import datetime
import requests
import logging
from typing import List, Optional, Dict, Any
import unicodedata
from io import StringIO

from src.models.certification import Certification, CertificationLevel, CertificationCategory
from src.utils.logger import get_logger
from src.config import DATA_PATH


logger = get_logger(__name__)


class SNEPCertificationManager:
    """Gère les certifications SNEP avec mise à jour automatique"""
    
    def __init__(self, db_path: Optional[str] = None):
        """Initialise le manager des certifications SNEP"""
        # Configuration des chemins
        self.data_dir = Path(DATA_PATH) / 'certifications' / 'snep'
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        # Base de données
        if db_path is None:
            db_path = self.data_dir / 'certifications.db'
        self.db_path = db_path
        self.conn = sqlite3.connect(str(self.db_path))
        
        # CSV local - nom exact du fichier téléchargé depuis SNEP
        self.csv_path = self.data_dir / 'certif-.csv'
        
        # Initialisation
        self.setup_database()
        self.cache = {}  # Cache en mémoire
        
        logger.info(f"✅ Manager SNEP initialisé - DB: {self.db_path}")
    
    def setup_database(self):
        """Crée les tables nécessaires dans la base de données"""
        cursor = self.conn.cursor()
        
        # Table principale des certifications
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS certifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artist_name TEXT NOT NULL,
            artist_clean TEXT NOT NULL,
            title TEXT NOT NULL,
            title_clean TEXT NOT NULL,
            publisher TEXT,
            category TEXT NOT NULL,
            certification TEXT NOT NULL,
            release_date DATE,
            certification_date DATE NOT NULL,
            country TEXT DEFAULT 'FR',
            certifying_body TEXT DEFAULT 'SNEP',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(artist_name, title, certification, certification_date)
        )
        ''')
        
        # Index pour recherches rapides
        cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_artist_clean 
        ON certifications(artist_clean)
        ''')
        
        cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_title_clean 
        ON certifications(title_clean)
        ''')
        
        cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_certification_date 
        ON certifications(certification_date)
        ''')
        
        # Table d'historique des mises à jour
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS update_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            update_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            total_records INTEGER,
            new_records INTEGER,
            updated_records INTEGER,
            status TEXT,
            source TEXT,
            error_message TEXT
        )
        ''')
        
        self.conn.commit()
        logger.info("📊 Tables de base de données créées/vérifiées")
    
    def normalize_text(self, text: str) -> str:
        """Normalise le texte pour les comparaisons"""
        if not text:
            return ""
        
        # Supprimer les accents
        text = unicodedata.normalize('NFD', text)
        text = ''.join(char for char in text if unicodedata.category(char) != 'Mn')
        
        # Nettoyer et mettre en majuscules
        text = text.strip().upper()
        
        # Remplacer les caractères spéciaux
        text = text.replace('&', 'AND')
        text = text.replace('$', 'S')
        
        return text
    
    def load_csv(self, filepath: Optional[Path] = None) -> pd.DataFrame:
        """Charge le fichier CSV des certifications SNEP"""
        if filepath is None:
            filepath = self.csv_path
        
        if not filepath.exists():
            logger.warning(f"⚠️ Fichier CSV non trouvé : {filepath}")
            return pd.DataFrame()
        
        try:
            # Charger le CSV avec les bons paramètres
            df = pd.read_csv(
                filepath,
                sep=';',  # SNEP utilise le point-virgule
                encoding='utf-8',
                dtype={
                    'Interprète': str,
                    'Titre': str,
                    'Éditeur / Distributeur': str,
                    'Catégorie': str,
                    'Certification': str
                },
                parse_dates=['Date de sortie', 'Date de constat'],
                dayfirst=True,  # Format DD/MM/YYYY
                date_format='%d/%m/%Y',
                na_values=['', 'N/A', 'null', 'None']
            )
            
            # Renommer les colonnes pour uniformité
            df.columns = [col.strip() for col in df.columns]
            
            logger.info(f"✅ CSV chargé : {len(df)} enregistrements")
            return df
            
        except Exception as e:
            logger.error(f"❌ Erreur lors du chargement du CSV : {e}")
            return pd.DataFrame()
    
    def parse_and_import_csv(self, df: pd.DataFrame) -> tuple[int, int]:
        """Parse et importe les données du CSV dans la base"""
        if df.empty:
            return 0, 0
        
        new_records = 0
        updated_records = 0
        
        for _, row in df.iterrows():
            try:
                # Créer l'objet Certification
                cert = Certification(
                    artist_name=str(row.get('Interprète', '')).strip(),
                    title=str(row.get('Titre', '')).strip(),
                    publisher=str(row.get('Éditeur / Distributeur', '')).strip() if pd.notna(row.get('Éditeur / Distributeur')) else None,
                    category=CertificationCategory.from_string(str(row.get('Catégorie', 'Singles'))),
                    level=CertificationLevel.from_string(str(row.get('Certification', 'Or'))),
                    release_date=row.get('Date de sortie') if pd.notna(row.get('Date de sortie')) else None,
                    certification_date=row.get('Date de constat'),
                    country='FR',
                    certifying_body='SNEP'
                )
                
                # Normaliser les noms pour la recherche
                artist_clean = self.normalize_text(cert.artist_name)
                title_clean = self.normalize_text(cert.title)
                
                cursor = self.conn.cursor()
                
                # Vérifier si l'enregistrement existe
                cursor.execute('''
                    SELECT id FROM certifications 
                    WHERE artist_clean = ? AND title_clean = ? AND certification = ?
                    ORDER BY certification_date DESC LIMIT 1
                ''', (artist_clean, title_clean, cert.level.value))
                
                existing = cursor.fetchone()
                
                if existing:
                    # Mise à jour si la date est plus récente
                    cursor.execute('''
                        UPDATE certifications 
                        SET certification_date = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ? AND certification_date < ?
                    ''', (cert.certification_date, existing[0], cert.certification_date))
                    
                    if cursor.rowcount > 0:
                        updated_records += 1
                else:
                    # Nouvelle certification
                    cursor.execute('''
                        INSERT INTO certifications 
                        (artist_name, artist_clean, title, title_clean, publisher, 
                         category, certification, release_date, certification_date,
                         country, certifying_body)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        cert.artist_name,
                        artist_clean,
                        cert.title,
                        title_clean,
                        cert.publisher,
                        cert.category.value,
                        cert.level.value,
                        cert.release_date,
                        cert.certification_date,
                        cert.country,
                        cert.certifying_body
                    ))
                    new_records += 1
                    
            except Exception as e:
                logger.error(f"Erreur pour {row.get('Interprète')} - {row.get('Titre')}: {e}")
                continue
        
        self.conn.commit()
        
        # Enregistrer dans l'historique
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO update_history 
            (total_records, new_records, updated_records, status, source)
            VALUES (?, ?, ?, ?, ?)
        ''', (len(df), new_records, updated_records, 'SUCCESS', 'CSV'))
        self.conn.commit()
        
        logger.info(f"📥 Import terminé : {new_records} nouveaux, {updated_records} mis à jour")
        return new_records, updated_records
    
    def import_from_csv(self, filepath: Optional[Path] = None) -> bool:
        """Importe les certifications depuis le fichier CSV"""
        df = self.load_csv(filepath)
        if df.empty:
            return False
        
        new_records, updated_records = self.parse_and_import_csv(df)
        return True
    
    def get_artist_certifications(self, artist_name: str) -> List[Dict[str, Any]]:
        """Récupère toutes les certifications d'un artiste"""
        artist_clean = self.normalize_text(artist_name)
        
        # Vérifier le cache
        if artist_clean in self.cache:
            return self.cache[artist_clean]
        
        query = '''
        SELECT * FROM certifications
        WHERE artist_clean LIKE ?
        ORDER BY certification_date DESC, 
                 CASE certification
                    WHEN 'Quadruple Diamant' THEN 1
                    WHEN 'Triple Diamant' THEN 2
                    WHEN 'Double Diamant' THEN 3
                    WHEN 'Diamant' THEN 4
                    WHEN 'Triple Platine' THEN 5
                    WHEN 'Double Platine' THEN 6
                    WHEN 'Platine' THEN 7
                    WHEN 'Triple Or' THEN 8
                    WHEN 'Double Or' THEN 9
                    WHEN 'Or' THEN 10
                    ELSE 11
                 END
        '''
        
        cursor = self.conn.cursor()
        cursor.execute(query, (f'%{artist_clean}%',))
        
        columns = [description[0] for description in cursor.description]
        results = []
        
        for row in cursor.fetchall():
            cert_dict = dict(zip(columns, row))
            results.append(cert_dict)
        
        # Mettre en cache
        self.cache[artist_clean] = results
        
        return results
    
    def get_track_certification(self, artist_name: str, track_title: str) -> Optional[Dict[str, Any]]:
        """Récupère la certification d'un morceau spécifique"""
        artist_clean = self.normalize_text(artist_name)
        title_clean = self.normalize_text(track_title)
        
        query = '''
        SELECT * FROM certifications
        WHERE artist_clean = ? AND title_clean = ?
        ORDER BY certification_date DESC
        LIMIT 1
        '''
        
        cursor = self.conn.cursor()
        cursor.execute(query, (artist_clean, title_clean))
        
        row = cursor.fetchone()
        if row:
            columns = [description[0] for description in cursor.description]
            return dict(zip(columns, row))
        
        return None
    
    def get_certification_stats(self, artist_name: Optional[str] = None) -> Dict[str, Any]:
        """Récupère des statistiques sur les certifications"""
        stats = {
            'total_certifications': 0,
            'by_level': {},
            'by_category': {},
            'recent_certifications': []
        }
        
        cursor = self.conn.cursor()
        
        if artist_name:
            artist_clean = self.normalize_text(artist_name)
            where_clause = "WHERE artist_clean LIKE ?"
            params = (f'%{artist_clean}%',)
        else:
            where_clause = ""
            params = ()
        
        # Total
        cursor.execute(f'''
            SELECT COUNT(*) FROM certifications {where_clause}
        ''', params)
        stats['total_certifications'] = cursor.fetchone()[0]
        
        # Par niveau
        cursor.execute(f'''
            SELECT certification, COUNT(*) 
            FROM certifications {where_clause}
            GROUP BY certification
        ''', params)
        stats['by_level'] = dict(cursor.fetchall())
        
        # Par catégorie
        cursor.execute(f'''
            SELECT category, COUNT(*) 
            FROM certifications {where_clause}
            GROUP BY category
        ''', params)
        stats['by_category'] = dict(cursor.fetchall())
        
        # Certifications récentes
        cursor.execute(f'''
            SELECT artist_name, title, certification, certification_date
            FROM certifications {where_clause}
            ORDER BY certification_date DESC
            LIMIT 10
        ''', params)
        
        columns = ['artist_name', 'title', 'certification', 'certification_date']
        stats['recent_certifications'] = [
            dict(zip(columns, row)) for row in cursor.fetchall()
        ]
        
        return stats
    
    def search_certifications(self, query: str, category: Optional[str] = None, 
                            level: Optional[str] = None) -> List[Dict[str, Any]]:
        """Recherche des certifications avec filtres"""
        query_clean = self.normalize_text(query)
        
        sql = '''
        SELECT * FROM certifications
        WHERE (artist_clean LIKE ? OR title_clean LIKE ?)
        '''
        params = [f'%{query_clean}%', f'%{query_clean}%']
        
        if category:
            sql += ' AND category = ?'
            params.append(category)
        
        if level:
            sql += ' AND certification = ?'
            params.append(level)
        
        sql += ' ORDER BY certification_date DESC LIMIT 100'
        
        cursor = self.conn.cursor()
        cursor.execute(sql, params)
        
        columns = [description[0] for description in cursor.description]
        results = []
        
        for row in cursor.fetchall():
            results.append(dict(zip(columns, row)))
        
        return results
    
    def close(self):
        """Ferme la connexion à la base de données"""
        self.conn.close()
        logger.info("Connexion DB fermée")


# Instance singleton pour utilisation globale
_snep_manager_instance = None

def get_snep_manager() -> SNEPCertificationManager:
    """Retourne l'instance singleton du manager SNEP"""
    global _snep_manager_instance
    if _snep_manager_instance is None:
        _snep_manager_instance = SNEPCertificationManager()
    return _snep_manager_instance