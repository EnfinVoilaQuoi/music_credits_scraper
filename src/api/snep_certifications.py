"""Gestionnaire pour les certifications SNEP"""

import io
import json
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

from src.config import DATA_PATH
from src.models.certification import Certification, CertificationCategory, CertificationLevel
from src.utils.cert_normalize import normalize_text as _normalize_text
from src.utils.cert_normalize import repair_extra_separators as _repair_extra_separators
from src.utils.logger import get_logger

logger = get_logger(__name__)


class SNEPCertificationManager:
    """Gère les certifications SNEP avec mise à jour automatique"""

    def __init__(self, db_path: str | None = None):
        """Initialise le manager des certifications SNEP"""
        # Configuration des chemins
        self.data_dir = Path(DATA_PATH) / "certifications" / "snep"
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Base de données
        if db_path is None:
            db_path = self.data_dir / "certifications.db"
        self.db_path = db_path
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)

        # CSV local - nom exact du fichier téléchargé depuis SNEP
        self.csv_path = self.data_dir / "certif-.csv"

        # Initialisation
        self.setup_database()
        self.cache = {}  # Cache en mémoire

        logger.info(f"✅ Manager SNEP initialisé - DB: {self.db_path}")

    def setup_database(self):
        """Crée les tables nécessaires dans la base de données"""
        cursor = self.conn.cursor()

        # Table principale des certifications
        cursor.execute("""
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
        """)

        # Index pour recherches rapides
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_artist_clean 
        ON certifications(artist_clean)
        """)

        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_title_clean 
        ON certifications(title_clean)
        """)

        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_certification_date 
        ON certifications(certification_date)
        """)

        # Table d'historique des mises à jour
        cursor.execute("""
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
        """)

        # Migration : ajouter les colonnes manquantes sur les DB existantes
        # (CREATE TABLE IF NOT EXISTS ne modifie pas une table déjà créée)
        # Note: SQLite n'accepte pas CURRENT_TIMESTAMP comme défaut dans ALTER TABLE
        for col in ["created_at", "updated_at"]:
            try:
                cursor.execute(
                    f"ALTER TABLE certifications ADD COLUMN {col} TIMESTAMP DEFAULT NULL"
                )
            except Exception:
                pass  # Colonne déjà présente, ignoré

        self.conn.commit()
        logger.info("📊 Tables de base de données créées/vérifiées")

    def normalize_text(self, text: str) -> str:
        """Normalise le texte pour les comparaisons (délègue à cert_normalize)."""
        return _normalize_text(text)

    @staticmethod
    def _repair_extra_separators(text: str, sep: str = ";") -> tuple:
        """Répare les lignes à colonnes excédentaires (délègue à cert_normalize)."""
        return _repair_extra_separators(text, sep)

    def load_csv(self, filepath: Path | None = None) -> pd.DataFrame:
        """Charge le fichier CSV des certifications SNEP"""
        if filepath is None:
            filepath = self.csv_path

        if not filepath.exists():
            logger.warning(f"⚠️ Fichier CSV non trouvé : {filepath}")
            return pd.DataFrame()

        try:
            # Essayer différents encodages — sans parse_dates pour éviter les erreurs pandas 2+
            encodings = ["utf-8-sig", "utf-8", "latin-1", "cp1252"]
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
            raw_text = raw_text.replace("\x00", "")

            # Réparer les lignes contenant un ';' parasite dans un champ
            # (ex: label "REC; 118 / WARNER MUSIC FRANCE" → 8 colonnes au lieu de 7)
            raw_text, repaired = self._repair_extra_separators(raw_text)
            if repaired:
                logger.warning(
                    f"🩹 {repaired} ligne(s) CSV réparée(s) (séparateur en trop dans un champ)"
                )

            df = pd.read_csv(
                io.StringIO(raw_text),
                sep=";",
                na_values=["", "N/A", "null", "None"],
                dtype=str,
                on_bad_lines="skip",
            )

            # Nettoyer les tabulations/espaces parasites dans les valeurs
            # (ex: "AYA NAKAMURA\t")
            for col in df.columns:
                if df[col].dtype == object:
                    df[col] = df[col].str.strip()

            # Parser les dates manuellement après chargement (compatible pandas 3.x)
            for date_col in ["Date de sortie", "Date de constat"]:
                if date_col in df.columns:
                    df[date_col] = pd.to_datetime(df[date_col], format="%d/%m/%Y", errors="coerce")

            # Nettoyer les noms de colonnes (supprimer BOM, espaces, etc.)
            df.columns = [col.strip().replace("\ufeff", "") for col in df.columns]

            # Normaliser les noms de colonnes (gérer les problèmes d'encodage)
            new_columns = []
            for col in df.columns:
                # Artiste/Interprète
                if "nterpr" in col or "Interpr" in col:
                    new_columns.append("Interprète")
                # Éditeur
                elif "diteur" in col or "Editeur" in col:
                    new_columns.append("Éditeur / Distributeur")
                # Catégorie
                elif "at" in col and "gorie" in col:
                    new_columns.append("Catégorie")
                # Titre
                elif col == "Titre":
                    new_columns.append("Titre")
                # Certification
                elif col == "Certification":
                    new_columns.append("Certification")
                # Dates
                elif "sortie" in col:
                    new_columns.append("Date de sortie")
                elif "constat" in col:
                    new_columns.append("Date de constat")
                else:
                    new_columns.append(col)

            df.columns = new_columns

            logger.info(f"✅ CSV chargé : {len(df)} enregistrements")
            logger.debug(f"Colonnes: {list(df.columns)}")

            return df

        except Exception as e:
            logger.error(f"❌ Erreur lors du chargement du CSV : {e}")
            return pd.DataFrame()

    def parse_and_import_csv(self, df: pd.DataFrame, source: str = "CSV") -> tuple[int, int]:
        """Parse et importe les données du CSV dans la base.

        `source` est journalisé dans update_history pour distinguer une MàJ
        GLOBALE d'une récupération ARTISTE (utilisé par la GUI pour afficher
        la vraie date de dernière MàJ globale).
        """
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

                    cleaned = re.sub(r"\s+", " ", cleaned)
                    return cleaned if cleaned else None

                # Accès par index pour éviter les problèmes d'encodage de noms de colonnes
                cols = list(df.columns)
                artist_name = clean_value(row[cols[0]] if len(cols) > 0 else "")  # Interprète
                title = clean_value(row[cols[1]] if len(cols) > 1 else "")  # Titre
                publisher = clean_value(
                    row[cols[2]] if len(cols) > 2 else None
                )  # Éditeur / Distributeur
                category_str = clean_value(
                    row[cols[3]] if len(cols) > 3 else "Singles"
                )  # Catégorie

                # Normaliser la catégorie (Single -> Singles)
                if category_str and category_str.lower() == "single":
                    category_str = "Singles"

                certification_str = clean_value(
                    row[cols[4]] if len(cols) > 4 else "Or"
                )  # Certification
                release_date_raw = row[cols[5]] if len(cols) > 5 else None  # Date de sortie
                certification_date_raw = row[cols[6]] if len(cols) > 6 else None  # Date de constat

                # Convertir les dates Pandas Timestamp en datetime Python
                release_date = None
                if pd.notna(release_date_raw):
                    try:
                        release_date = (
                            release_date_raw.to_pydatetime()
                            if hasattr(release_date_raw, "to_pydatetime")
                            else release_date_raw
                        )
                    except Exception:
                        pass

                certification_date = None
                if pd.notna(certification_date_raw):
                    try:
                        certification_date = (
                            certification_date_raw.to_pydatetime()
                            if hasattr(certification_date_raw, "to_pydatetime")
                            else certification_date_raw
                        )
                    except Exception:
                        pass

                # Créer l'objet Certification
                cert = Certification(
                    artist_name=artist_name or "",
                    title=title or "",
                    publisher=publisher,
                    category=CertificationCategory.from_string(category_str),
                    level=CertificationLevel.from_string(certification_str),
                    release_date=release_date,
                    certification_date=certification_date,
                    country="FR",
                    certifying_body="SNEP",
                )

                # Normaliser les noms pour la recherche
                artist_clean = self.normalize_text(cert.artist_name)
                title_clean = self.normalize_text(cert.title)

                cursor = self.conn.cursor()

                # Vérifier si l'enregistrement existe
                cursor.execute(
                    """
                    SELECT id FROM certifications 
                    WHERE artist_clean = ? AND title_clean = ? AND certification = ?
                    ORDER BY certification_date DESC LIMIT 1
                """,
                    (artist_clean, title_clean, cert.level.value),
                )

                existing = cursor.fetchone()

                if existing:
                    # Mise à jour si la date est plus récente
                    cert_date_str = (
                        cert.certification_date.isoformat() if cert.certification_date else None
                    )
                    cursor.execute(
                        """
                        UPDATE certifications
                        SET certification_date = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ? AND certification_date < ?
                    """,
                        (cert_date_str, existing[0], cert_date_str),
                    )

                    if cursor.rowcount > 0:
                        updated_records += 1
                else:
                    # Nouvelle certification
                    # Convertir les dates en string ISO pour SQLite
                    release_date_str = cert.release_date.isoformat() if cert.release_date else None
                    cert_date_str = (
                        cert.certification_date.isoformat() if cert.certification_date else None
                    )

                    cursor.execute(
                        """
                        INSERT INTO certifications
                        (artist_name, artist_clean, title, title_clean, publisher,
                         category, certification, release_date, certification_date,
                         country, certifying_body)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                        (
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
                            cert.certifying_body,
                        ),
                    )
                    new_records += 1

            except Exception as e:
                # Utiliser les indices car row.get ne fonctionne plus avec les noms encodés
                artist_debug = row[cols[0]] if len(cols) > 0 else "Unknown"
                title_debug = row[cols[1]] if len(cols) > 1 else "Unknown"
                logger.error(f"Erreur pour {artist_debug} - {title_debug}: {e}")
                continue

        self.conn.commit()

        # Enregistrer dans l'historique
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO update_history
            (total_records, new_records, updated_records, status, source)
            VALUES (?, ?, ?, ?, ?)
        """,
            (len(df), new_records, updated_records, "SUCCESS", source),
        )
        self.conn.commit()

        logger.info(f"📥 Import terminé : {new_records} nouveaux, {updated_records} mis à jour")
        return new_records, updated_records

    def import_from_csv(self, filepath: Path | None = None, source: str = "CSV") -> bool:
        """Importe les certifications depuis le fichier CSV.

        `source` ('GLOBAL', 'ARTIST', 'SCRAPE', ...) est journalisé dans
        update_history pour tracer l'origine de la MàJ.
        """
        df = self.load_csv(filepath)
        if df.empty:
            return False

        new_records, updated_records = self.parse_and_import_csv(df, source=source)
        return True

    def get_last_update(self, source: str | None = None) -> str | None:
        """Retourne la date (ISO str) de la dernière MàJ réussie.

        Si `source` est fourni (ex: 'GLOBAL'), ne considère que cette source.
        Retourne None si aucune MàJ correspondante n'est enregistrée.
        """
        cursor = self.conn.cursor()
        if source:
            cursor.execute(
                """
                SELECT update_date FROM update_history
                WHERE status = 'SUCCESS' AND source = ?
                ORDER BY update_date DESC LIMIT 1
            """,
                (source,),
            )
        else:
            cursor.execute("""
                SELECT update_date FROM update_history
                WHERE status = 'SUCCESS'
                ORDER BY update_date DESC LIMIT 1
            """)
        row = cursor.fetchone()
        return row[0] if row else None

    def get_artist_certifications(self, artist_name: str) -> list[dict[str, Any]]:
        """Récupère toutes les certifications d'un artiste"""
        artist_clean = self.normalize_text(artist_name)

        # Vérifier le cache
        if artist_clean in self.cache:
            return self.cache[artist_clean]

        query = """
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
        """

        cursor = self.conn.cursor()
        cursor.execute(query, (f"%{artist_clean}%",))

        columns = [description[0] for description in cursor.description]
        results = []

        for row in cursor.fetchall():
            cert_dict = dict(zip(columns, row, strict=True))
            results.append(cert_dict)

        # Mettre en cache
        self.cache[artist_clean] = results

        return results

    def get_track_certification(self, artist_name: str, track_title: str) -> dict[str, Any] | None:
        """Récupère la certification la plus élevée d'un morceau - OBSOLÈTE, utiliser get_track_certifications"""
        certifications = self.get_track_certifications(artist_name, track_title)
        return certifications[0] if certifications else None

    def get_track_certifications(self, artist_name: str, track_title: str) -> list[dict[str, Any]]:
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

        feat_pattern = r"^(.+?)\s+(?:FEAT\.?|FT\.?|FEATURING)\s+(.+)$"
        title_match = re.match(feat_pattern, title_clean, re.IGNORECASE)

        if title_match:
            # Le titre contient un featuring
            main_part = title_match.group(1).strip()

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

        # Stratégie 4: certif au titre TRONQUÉ (corruption SNEP des vieilles
        # entrées). La certif est plus courte que le morceau et en est un
        # préfixe (ex: "L'EMPIRE DU C" → "L'EMPIRE DU COTE OBSCUR"). En dernier
        # recours uniquement, pour limiter les faux positifs.
        if not results:
            truncated_matches = self._search_truncated_certifications(artist_clean, title_clean)
            for match in truncated_matches:
                if match not in results:
                    results.append(match)

        # Trier par ordre de priorité (Diamant > Platine > Or) et date
        cert_order = {
            "Quadruple Diamant": 1,
            "Triple Diamant": 2,
            "Double Diamant": 3,
            "Diamant": 4,
            "Triple Platine": 5,
            "Double Platine": 6,
            "Platine": 7,
            "Triple Or": 8,
            "Double Or": 9,
            "Or": 10,
        }

        results.sort(
            key=lambda x: (
                cert_order.get(x.get("certification", ""), 99),
                x.get("certification_date", "") or "",
            )
        )

        return results

    def _search_certifications_by_artist_title(
        self, artist_clean: str, title_clean: str
    ) -> list[dict[str, Any]]:
        """Recherche les certifications pour un artiste et titre donnés"""
        cursor = self.conn.cursor()

        # Recherche exacte d'abord
        query = """
        SELECT * FROM certifications
        WHERE artist_clean = ? AND title_clean = ?
        ORDER BY certification_date DESC
        """
        cursor.execute(query, (artist_clean, title_clean))

        columns = [description[0] for description in cursor.description]
        exact_results = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

        if exact_results:
            return exact_results

        # Si pas de résultat exact, recherche fuzzy
        query = """
        SELECT * FROM certifications
        WHERE artist_clean LIKE ? AND title_clean LIKE ?
        ORDER BY certification_date DESC
        """
        cursor.execute(query, (f"%{artist_clean}%", f"%{title_clean}%"))

        fuzzy_results = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        return fuzzy_results

    def _search_truncated_certifications(
        self, artist_clean: str, title_clean: str, min_len: int = 8
    ) -> list[dict[str, Any]]:
        """Récupère les certifs dont le titre (TRONQUÉ dans la source SNEP) est
        un préfixe du titre du morceau.

        Gère la troncature des vieilles entrées SNEP (titres coupés sur les
        caractères accentués). Gardes anti-faux-positifs :
          - le titre du morceau doit faire au moins `min_len` caractères ;
          - la certif doit être STRICTEMENT plus courte (donc tronquée) ;
          - la certif doit faire au moins `min_len` caractères (pas de préfixe
            trop court type "OR" qui matcherait n'importe quoi) ;
          - on prend le préfixe le plus long (le plus spécifique).
        """
        if not title_clean or len(title_clean) < min_len:
            return []

        # Ici le titre du MORCEAU est le sujet du LIKE et le motif est la
        # colonne `title_clean` (titre de la certif) suivie de '%'. On teste
        # donc : « le titre du morceau commence-t-il par le titre de la certif ? »
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT * FROM certifications
            WHERE artist_clean = ?
              AND length(title_clean) >= ?
              AND length(title_clean) < ?
              AND ? LIKE title_clean || '%'
            ORDER BY length(title_clean) DESC
        """,
            (artist_clean, min_len, len(title_clean), title_clean),
        )

        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

    def _search_featuring_certifications(
        self, artist_name: str, track_title: str
    ) -> list[dict[str, Any]]:
        """Recherche les certifications où l'artiste apparaît en featuring"""
        artist_clean = self.normalize_text(artist_name)
        title_clean = self.normalize_text(track_title)

        cursor = self.conn.cursor()

        # Chercher les titres qui contiennent l'artiste ET le titre dans la base
        # Ex: Si on cherche "NINHO" et "EVERY DAY", on trouvera "NINHO FEAT. GRIFF" / "EVERY DAY"
        query = """
        SELECT * FROM certifications
        WHERE artist_clean LIKE ? AND title_clean LIKE ?
        ORDER BY certification_date DESC
        """

        cursor.execute(query, (f"%{artist_clean}%", f"%{title_clean}%"))

        columns = [description[0] for description in cursor.description]
        results = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

        return results

    def get_album_certifications(self, artist_name: str, album_name: str) -> list[dict[str, Any]]:
        """Récupère toutes les certifications d'un album"""
        artist_clean = self.normalize_text(artist_name)
        album_clean = self.normalize_text(album_name)

        cursor = self.conn.cursor()

        # Recherche exacte
        query = """
        SELECT * FROM certifications
        WHERE artist_clean = ? AND title_clean = ? AND category = 'Albums'
        ORDER BY certification_date DESC
        """
        cursor.execute(query, (artist_clean, album_clean))

        columns = [description[0] for description in cursor.description]
        exact_results = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

        if exact_results:
            return exact_results

        # Recherche fuzzy
        query = """
        SELECT * FROM certifications
        WHERE artist_clean LIKE ? AND title_clean LIKE ? AND category = 'Albums'
        ORDER BY certification_date DESC
        """
        cursor.execute(query, (f"%{artist_clean}%", f"%{album_clean}%"))

        fuzzy_results = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        return fuzzy_results

    def get_certification_stats(self, artist_name: str | None = None) -> dict[str, Any]:
        """Récupère des statistiques sur les certifications"""
        stats = {
            "total_certifications": 0,
            "by_level": {},
            "by_category": {},
            "recent_certifications": [],
        }

        cursor = self.conn.cursor()

        if artist_name:
            artist_clean = self.normalize_text(artist_name)
            where_clause = "WHERE artist_clean LIKE ?"
            params = (f"%{artist_clean}%",)
        else:
            where_clause = ""
            params = ()

        # Total
        cursor.execute(
            f"""
            SELECT COUNT(*) FROM certifications {where_clause}
        """,
            params,
        )
        stats["total_certifications"] = cursor.fetchone()[0]

        # Par niveau
        cursor.execute(
            f"""
            SELECT certification, COUNT(*) 
            FROM certifications {where_clause}
            GROUP BY certification
        """,
            params,
        )
        stats["by_level"] = dict(cursor.fetchall())

        # Par catégorie
        cursor.execute(
            f"""
            SELECT category, COUNT(*) 
            FROM certifications {where_clause}
            GROUP BY category
        """,
            params,
        )
        stats["by_category"] = dict(cursor.fetchall())

        # Certifications récentes
        cursor.execute(
            f"""
            SELECT artist_name, title, certification, certification_date
            FROM certifications {where_clause}
            ORDER BY certification_date DESC
            LIMIT 10
        """,
            params,
        )

        columns = ["artist_name", "title", "certification", "certification_date"]
        stats["recent_certifications"] = [
            dict(zip(columns, row, strict=True)) for row in cursor.fetchall()
        ]

        return stats

    def search_certifications(
        self, query: str, category: str | None = None, level: str | None = None
    ) -> list[dict[str, Any]]:
        """Recherche des certifications avec filtres"""
        query_clean = self.normalize_text(query)

        sql = """
        SELECT * FROM certifications
        WHERE (artist_clean LIKE ? OR title_clean LIKE ?)
        """
        params = [f"%{query_clean}%", f"%{query_clean}%"]

        if category:
            sql += " AND category = ?"
            params.append(category)

        if level:
            sql += " AND certification = ?"
            params.append(level)

        sql += " ORDER BY certification_date DESC LIMIT 100"

        cursor = self.conn.cursor()
        cursor.execute(sql, params)

        columns = [description[0] for description in cursor.description]
        results = []

        for row in cursor.fetchall():
            results.append(dict(zip(columns, row, strict=True)))

        return results

    def audit_artist_certifications(
        self, artist_name: str, track_titles: list[str], album_titles: list[str] | None = None
    ) -> dict[str, Any]:
        """Audite les certifs SNEP d'un artiste face à sa discographie connue.

        Retourne les certifs « orphelines » (rattachées à rien), chacune avec sa
        meilleure correspondance approximative — pour distinguer une certif
        probablement TRONQUÉE/corrompue (proche d'un titre existant, donc
        récupérable) d'une certif réellement ABSENTE de la discographie.

        Une certif de catégorie **Albums** est comparée aux **albums** de
        l'artiste ; une certif **Singles/Vidéos** à ses **morceaux**. Si
        `album_titles` n'est pas fourni, on retombe sur les morceaux pour tout.

        `track_titles` / `album_titles` = titres des morceaux / noms d'albums
        (ex: current_artist.tracks et leurs `.album`).
        """
        import difflib
        import re as _re

        artist_clean = self.normalize_text(artist_name)

        # Certifs de l'artiste : on récupère large (LIKE) PUIS on garde seulement
        # celles où l'artiste apparaît comme MOT ENTIER (évite IAM ⊂ WILLIAMS).
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM certifications WHERE artist_clean LIKE ?", (f"%{artist_clean}%",)
        )
        columns = [d[0] for d in cursor.description]
        certs = []
        for row in cursor.fetchall():
            d = dict(zip(columns, row, strict=True))
            if _re.search(r"\b" + _re.escape(artist_clean) + r"\b", d.get("artist_clean") or ""):
                certs.append(d)

        track_cleans = [self.normalize_text(t) for t in track_titles if t]
        album_cleans = [self.normalize_text(a) for a in (album_titles or []) if a]

        matched_tracks = 0
        matched_albums = 0
        orphans = []
        for cert in certs:
            ct = cert.get("title_clean") or ""
            if not ct:
                continue
            # Référentiel selon la catégorie : Albums → albums, sinon morceaux.
            # Repli sur les morceaux si la liste d'albums n'a pas été fournie.
            is_album = cert.get("category") == "Albums"
            ref_cleans = album_cleans if (is_album and album_cleans) else track_cleans
            ref_set = set(ref_cleans)

            is_match = (
                ct in ref_set
                or (len(ct) >= 8 and any(rc.startswith(ct) for rc in ref_cleans))
                or any((ct in rc) or (rc in ct) for rc in ref_cleans if rc)
            )
            if is_match:
                if is_album:
                    matched_albums += 1
                else:
                    matched_tracks += 1
                continue
            # Meilleure correspondance approximative (diagnostic), dans le bon référentiel
            best, best_r = None, 0.0
            for rc in ref_cleans:
                r = difflib.SequenceMatcher(None, ct, rc).ratio()
                if r > best_r:
                    best_r, best = r, rc
            orphans.append(
                {
                    "kind": "album" if is_album else "morceau",
                    "title": cert.get("title"),
                    "certification": cert.get("certification"),
                    "category": cert.get("category"),
                    "certification_date": cert.get("certification_date"),
                    "closest": best,
                    "ratio": round(best_r, 2),
                }
            )

        orphans.sort(key=lambda o: -o["ratio"])
        return {
            "artist": artist_name,
            "total": len(certs),
            "matched": matched_tracks + matched_albums,
            "matched_tracks": matched_tracks,
            "matched_albums": matched_albums,
            "orphans": orphans,
            "has_tracks": bool(track_cleans),
        }

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


def get_snep_last_update(source: str | None = "GLOBAL") -> str | None:
    """Date ISO de dernière régénération du CSV canonique, lue depuis le sidecar
    `certif_snep.meta.json` (plus de dépendance DB). Sûr à appeler depuis
    n'importe quel thread (ex: la GUI).

    Le paramètre `source` est conservé pour compatibilité : ARTIST renvoie None
    (la fraîcheur du sidecar reflète la régénération globale du clean).
    """
    if source and source not in ("GLOBAL", "MIGRATION"):
        return None
    meta_path = Path(DATA_PATH) / "certifications" / "snep" / "certif_snep.meta.json"
    if not meta_path.exists():
        return None
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        return data.get("last_update")
    except Exception as e:
        logger.warning(f"Lecture certif_snep.meta.json impossible : {e}")
        return None
