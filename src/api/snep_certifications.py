"""Gestionnaire pour les certifications SNEP"""
import io
import pandas as pd
import sqlite3
from pathlib import Path
from typing import List, Optional, Dict, Any
import unicodedata

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
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        
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
        
        # Migration : ajouter les colonnes manquantes sur les DB existantes
        # (CREATE TABLE IF NOT EXISTS ne modifie pas une table déjà créée)
        # Note: SQLite n'accepte pas CURRENT_TIMESTAMP comme défaut dans ALTER TABLE
        for col in ["created_at", "updated_at"]:
            try:
                cursor.execute(f"ALTER TABLE certifications ADD COLUMN {col} TIMESTAMP DEFAULT NULL")
            except Exception:
                pass  # Colonne déjà présente, ignoré

        self.conn.commit()
        logger.info("📊 Tables de base de données créées/vérifiées")
    
    def normalize_text(self, text: str) -> str:
        """Normalise le texte pour les comparaisons - VERSION AMÉLIORÉE"""
        if not text:
            return ""

        # ÉTAPE 1: Nettoyer les espaces/tabulations (AVANT tout traitement)
        import re
        # Remplacer tous les espaces blancs (espaces, tabs, etc.) par un seul espace
        text = re.sub(r'\s+', ' ', text.strip())

        # ÉTAPE 2: Supprimer les accents
        text = unicodedata.normalize('NFD', text)
        text = ''.join(char for char in text if unicodedata.category(char) != 'Mn')

        # ÉTAPE 3: Mettre en majuscules
        text = text.upper()

        # ÉTAPE 4: Remplacer les caractères spéciaux et ligatures
        replacements = {
            '&': 'AND',
            '$': 'S',
            'Œ': 'OE',
            'OE': 'OE',
            'Æ': 'AE',
            'AE': 'AE',
            ''': "'",
            ''': "'",
            '`': "'",
            '´': "'",
            '"': '"',
            '"': '"',
            '«': '"',
            '»': '"',
            '–': '-',
            '—': '-',
            '…': '...',
        }

        for old, new in replacements.items():
            text = text.replace(old, new)

        # ÉTAPE 5: Supprimer tous les caractères de ponctuation sauf lettres, chiffres et espaces
        # Garder les apostrophes pour les featuring
        text = re.sub(r'[^\w\s\'-]', '', text)

        # ÉTAPE 6: Remplacer espaces multiples par un seul (final cleanup)
        text = re.sub(r'\s+', ' ', text)

        return text.strip()
    
    @staticmethod
    def _repair_extra_separators(text: str, sep: str = ';') -> tuple:
        """
        Répare les lignes ayant plus de colonnes que l'en-tête : les champs
        excédentaires sont fusionnés dans la 3e colonne (Éditeur/Distributeur),
        seule colonne susceptible de contenir le séparateur (noms de labels).
        Retourne (texte_réparé, nombre_de_lignes_réparées).
        """
        lines = text.splitlines()
        if not lines:
            return text, 0

        expected = lines[0].count(sep) + 1
        repaired = 0
        out = [lines[0]]

        for line in lines[1:]:
            fields = line.split(sep)
            # Ne pas toucher aux lignes vides, conformes, ou contenant des quotes
            if len(fields) > expected and '"' not in line and line.strip():
                extra = len(fields) - expected
                # Fusionner la colonne Éditeur avec les champs excédentaires,
                # en QUOTANT le champ pour que le ';' interne ne re-splitte pas
                label = sep.join(fields[2:3 + extra])
                merged = fields[:2] + [f'"{label}"'] + fields[3 + extra:]
                line = sep.join(merged)
                repaired += 1
            out.append(line)

        return "\n".join(out), repaired

    def load_csv(self, filepath: Optional[Path] = None) -> pd.DataFrame:
        """Charge le fichier CSV des certifications SNEP"""
        if filepath is None:
            filepath = self.csv_path

        if not filepath.exists():
            logger.warning(f"⚠️ Fichier CSV non trouvé : {filepath}")
            return pd.DataFrame()

        try:
            # Essayer différents encodages — sans parse_dates pour éviter les erreurs pandas 2+
            encodings = ['utf-8-sig', 'utf-8', 'latin-1', 'cp1252']
            raw_text = None

            for encoding in encodings:
                try:
                    raw_text = filepath.read_text(encoding=encoding)
                    logger.info(f"CSV chargé avec encoding: {encoding}")
                    break
                except UnicodeDecodeError:
                    continue

            if raw_text is None:
                logger.error("Impossible de charger le CSV avec les encodages disponibles")
                return pd.DataFrame()

            # Neutraliser octets nuls et lignes vides (fichiers corrompus/partiels)
            raw_text = raw_text.replace('\x00', '')

            # Réparer les lignes contenant un ';' parasite dans un champ
            # (ex: label "REC; 118 / WARNER MUSIC FRANCE" → 8 colonnes au lieu de 7)
            raw_text, repaired = self._repair_extra_separators(raw_text)
            if repaired:
                logger.warning(f"🩹 {repaired} ligne(s) CSV réparée(s) (séparateur en trop dans un champ)")

            df = pd.read_csv(
                io.StringIO(raw_text),
                sep=';',
                na_values=['', 'N/A', 'null', 'None'],
                dtype=str,
                on_bad_lines='skip',
            )

            # Nettoyer les tabulations/espaces parasites dans les valeurs
            # (ex: "AYA NAKAMURA\t")
            for col in df.columns:
                if df[col].dtype == object:
                    df[col] = df[col].str.strip()

            # Parser les dates manuellement après chargement (compatible pandas 3.x)
            for date_col in ['Date de sortie', 'Date de constat']:
                if date_col in df.columns:
                    df[date_col] = pd.to_datetime(df[date_col], format='%d/%m/%Y', errors='coerce')

            # Nettoyer les noms de colonnes (supprimer BOM, espaces, etc.)
            df.columns = [col.strip().replace('\ufeff', '') for col in df.columns]

            # Normaliser les noms de colonnes (gérer les problèmes d'encodage)
            new_columns = []
            for col in df.columns:
                # Artiste/Interprète
                if 'nterpr' in col or 'Interpr' in col:
                    new_columns.append('Interprète')
                # Éditeur
                elif 'diteur' in col or 'Editeur' in col:
                    new_columns.append('Éditeur / Distributeur')
                # Catégorie
                elif 'at' in col and 'gorie' in col:
                    new_columns.append('Catégorie')
                # Titre
                elif col == 'Titre':
                    new_columns.append('Titre')
                # Certification
                elif col == 'Certification':
                    new_columns.append('Certification')
                # Dates
                elif 'sortie' in col:
                    new_columns.append('Date de sortie')
                elif 'constat' in col:
                    new_columns.append('Date de constat')
                else:
                    new_columns.append(col)

            df.columns = new_columns

            logger.info(f"✅ CSV chargé : {len(df)} enregistrements")
            logger.debug(f"Colonnes: {list(df.columns)}")

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
                # Nettoyer les valeurs (supprimer espaces, tabulations, etc.)
                def clean_value(value):
                    if pd.isna(value):
                        return None
                    # Convertir en string et supprimer tous les espaces blancs (espaces, tabs, etc.)
                    cleaned = str(value).strip()
                    # Remplacer les espaces/tabs multiples par un seul espace
                    import re
                    cleaned = re.sub(r'\s+', ' ', cleaned)
                    return cleaned if cleaned else None

                # Accès par index pour éviter les problèmes d'encodage de noms de colonnes
                cols = list(df.columns)
                artist_name = clean_value(row[cols[0]] if len(cols) > 0 else '')  # Interprète
                title = clean_value(row[cols[1]] if len(cols) > 1 else '')  # Titre
                publisher = clean_value(row[cols[2]] if len(cols) > 2 else None)  # Éditeur / Distributeur
                category_str = clean_value(row[cols[3]] if len(cols) > 3 else 'Singles')  # Catégorie

                # Normaliser la catégorie (Single -> Singles)
                if category_str and category_str.lower() == 'single':
                    category_str = 'Singles'

                certification_str = clean_value(row[cols[4]] if len(cols) > 4 else 'Or')  # Certification
                release_date_raw = row[cols[5]] if len(cols) > 5 else None  # Date de sortie
                certification_date_raw = row[cols[6]] if len(cols) > 6 else None  # Date de constat

                # Convertir les dates Pandas Timestamp en datetime Python
                release_date = None
                if pd.notna(release_date_raw):
                    try:
                        release_date = release_date_raw.to_pydatetime() if hasattr(release_date_raw, 'to_pydatetime') else release_date_raw
                    except:
                        pass

                certification_date = None
                if pd.notna(certification_date_raw):
                    try:
                        certification_date = certification_date_raw.to_pydatetime() if hasattr(certification_date_raw, 'to_pydatetime') else certification_date_raw
                    except:
                        pass

                # Créer l'objet Certification
                cert = Certification(
                    artist_name=artist_name or '',
                    title=title or '',
                    publisher=publisher,
                    category=CertificationCategory.from_string(category_str),
                    level=CertificationLevel.from_string(certification_str),
                    release_date=release_date,
                    certification_date=certification_date,
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
                    cert_date_str = cert.certification_date.isoformat() if cert.certification_date else None
                    cursor.execute('''
                        UPDATE certifications
                        SET certification_date = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ? AND certification_date < ?
                    ''', (cert_date_str, existing[0], cert_date_str))
                    
                    if cursor.rowcount > 0:
                        updated_records += 1
                else:
                    # Nouvelle certification
                    # Convertir les dates en string ISO pour SQLite
                    release_date_str = cert.release_date.isoformat() if cert.release_date else None
                    cert_date_str = cert.certification_date.isoformat() if cert.certification_date else None

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
                        release_date_str,
                        cert_date_str,
                        cert.country,
                        cert.certifying_body
                    ))
                    new_records += 1
                    
            except Exception as e:
                # Utiliser les indices car row.get ne fonctionne plus avec les noms encodés
                artist_debug = row[cols[0]] if len(cols) > 0 else 'Unknown'
                title_debug = row[cols[1]] if len(cols) > 1 else 'Unknown'
                logger.error(f"Erreur pour {artist_debug} - {title_debug}: {e}")
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
        """Récupère la certification la plus élevée d'un morceau - OBSOLÈTE, utiliser get_track_certifications"""
        certifications = self.get_track_certifications(artist_name, track_title)
        return certifications[0] if certifications else None

    def get_track_certifications(self, artist_name: str, track_title: str) -> List[Dict[str, Any]]:
        """Récupère TOUTES les certifications d'un morceau spécifique - VERSION AMÉLIORÉE"""
        results = []

        # Normaliser le titre du morceau
        title_clean = self.normalize_text(track_title)

        # Stratégie 1: Match exact avec l'artiste fourni
        artist_clean = self.normalize_text(artist_name)
        exact_matches = self._search_certifications_by_artist_title(artist_clean, title_clean)
        results.extend(exact_matches)

        # Stratégie 2: Si le titre contient "feat." ou "ft.", extraire l'artiste principal
        import re
        feat_pattern = r'^(.+?)\s+(?:FEAT\.?|FT\.?|FEATURING)\s+(.+)$'
        title_match = re.match(feat_pattern, title_clean, re.IGNORECASE)

        if title_match:
            # Le titre contient un featuring
            main_part = title_match.group(1).strip()
            featured_part = title_match.group(2).strip()

            # Chercher avec juste la partie principale du titre
            main_matches = self._search_certifications_by_artist_title(artist_clean, main_part)
            for match in main_matches:
                if match not in results:
                    results.append(match)

        # Stratégie 3: Chercher dans les certifications où l'artiste apparaît en featuring
        featuring_matches = self._search_featuring_certifications(artist_name, track_title)
        for match in featuring_matches:
            if match not in results:
                results.append(match)

        # Trier par ordre de priorité (Diamant > Platine > Or) et date
        cert_order = {
            'Quadruple Diamant': 1, 'Triple Diamant': 2, 'Double Diamant': 3, 'Diamant': 4,
            'Triple Platine': 5, 'Double Platine': 6, 'Platine': 7,
            'Triple Or': 8, 'Double Or': 9, 'Or': 10
        }

        results.sort(key=lambda x: (
            cert_order.get(x.get('certification', ''), 99),
            x.get('certification_date', '') or ''
        ))

        return results

    def _search_certifications_by_artist_title(self, artist_clean: str, title_clean: str) -> List[Dict[str, Any]]:
        """Recherche les certifications pour un artiste et titre donnés"""
        cursor = self.conn.cursor()

        # Recherche exacte d'abord
        query = '''
        SELECT * FROM certifications
        WHERE artist_clean = ? AND title_clean = ?
        ORDER BY certification_date DESC
        '''
        cursor.execute(query, (artist_clean, title_clean))

        columns = [description[0] for description in cursor.description]
        exact_results = [dict(zip(columns, row)) for row in cursor.fetchall()]

        if exact_results:
            return exact_results

        # Si pas de résultat exact, recherche fuzzy
        query = '''
        SELECT * FROM certifications
        WHERE artist_clean LIKE ? AND title_clean LIKE ?
        ORDER BY certification_date DESC
        '''
        cursor.execute(query, (f'%{artist_clean}%', f'%{title_clean}%'))

        fuzzy_results = [dict(zip(columns, row)) for row in cursor.fetchall()]
        return fuzzy_results

    def _search_featuring_certifications(self, artist_name: str, track_title: str) -> List[Dict[str, Any]]:
        """Recherche les certifications où l'artiste apparaît en featuring"""
        artist_clean = self.normalize_text(artist_name)
        title_clean = self.normalize_text(track_title)

        cursor = self.conn.cursor()

        # Chercher les titres qui contiennent l'artiste ET le titre dans la base
        # Ex: Si on cherche "NINHO" et "EVERY DAY", on trouvera "NINHO FEAT. GRIFF" / "EVERY DAY"
        query = '''
        SELECT * FROM certifications
        WHERE artist_clean LIKE ? AND title_clean LIKE ?
        ORDER BY certification_date DESC
        '''

        cursor.execute(query, (f'%{artist_clean}%', f'%{title_clean}%'))

        columns = [description[0] for description in cursor.description]
        results = [dict(zip(columns, row)) for row in cursor.fetchall()]

        return results

    def get_album_certifications(self, artist_name: str, album_name: str) -> List[Dict[str, Any]]:
        """Récupère toutes les certifications d'un album"""
        artist_clean = self.normalize_text(artist_name)
        album_clean = self.normalize_text(album_name)

        cursor = self.conn.cursor()

        # Recherche exacte
        query = '''
        SELECT * FROM certifications
        WHERE artist_clean = ? AND title_clean = ? AND category = 'Albums'
        ORDER BY certification_date DESC
        '''
        cursor.execute(query, (artist_clean, album_clean))

        columns = [description[0] for description in cursor.description]
        exact_results = [dict(zip(columns, row)) for row in cursor.fetchall()]

        if exact_results:
            return exact_results

        # Recherche fuzzy
        query = '''
        SELECT * FROM certifications
        WHERE artist_clean LIKE ? AND title_clean LIKE ? AND category = 'Albums'
        ORDER BY certification_date DESC
        '''
        cursor.execute(query, (f'%{artist_clean}%', f'%{album_clean}%'))

        fuzzy_results = [dict(zip(columns, row)) for row in cursor.fetchall()]
        return fuzzy_results

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