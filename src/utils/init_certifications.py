"""Script d'initialisation SNEP"""

from pathlib import Path

# Ajouter le dossier parent au path
parent_dir = Path(__file__).parent.parent.parent

# Configuration
DATA_PATH = parent_dir / "data"

import sqlite3
import unicodedata


class SimpleCSVReader:
    """Lecteur CSV simple sans pandas pour éviter les problèmes de types"""

    @staticmethod
    def read_csv(filepath, delimiter=";", encoding="utf-8"):
        """Lit un CSV et retourne une liste de dictionnaires"""
        rows = []

        with open(filepath, encoding=encoding) as f:
            # Lire la première ligne (headers)
            header_line = f.readline().strip()
            # Enlever le BOM si présent
            if header_line.startswith("\ufeff"):
                header_line = header_line[1:]

            headers = [h.strip() for h in header_line.split(delimiter)]
            print(f"  Headers détectés: {headers}")

            # Lire les données
            line_num = 1
            for line in f:
                line_num += 1
                line = line.strip()
                if not line:
                    continue

                # Diviser la ligne
                values = line.split(delimiter)

                # Créer un dictionnaire si on a le bon nombre de colonnes
                if len(values) == len(headers):
                    row = {}
                    for i, header in enumerate(headers):
                        row[header] = values[i].strip() if i < len(values) else ""
                    rows.append(row)
                else:
                    # Gérer l'erreur REC; 118
                    if "REC; 118" in line:
                        fixed_line = line.replace("REC; 118", "REC. 118")
                        values = fixed_line.split(delimiter)
                        if len(values) == len(headers):
                            row = {}
                            for i, header in enumerate(headers):
                                row[header] = values[i].strip() if i < len(values) else ""
                            rows.append(row)
                            print(f"  ✅ Ligne {line_num} corrigée (REC; 118)")

        return rows, headers


class SimpleSNEPImporter:
    """Importeur SNEP simplifié"""

    def __init__(self):
        self.data_dir = DATA_PATH / "certifications" / "snep"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "certifications.db"
        self.csv_path = self.data_dir / "certif-.csv"
        self.conn = sqlite3.connect(str(self.db_path))
        self.setup_database()

    def setup_database(self):
        """Crée les tables"""
        cursor = self.conn.cursor()

        # Supprimer la table existante pour repartir de zéro
        cursor.execute("DROP TABLE IF EXISTS certifications")

        # Créer la table avec des types simples
        cursor.execute("""
        CREATE TABLE certifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artist_name TEXT NOT NULL,
            artist_clean TEXT NOT NULL,
            title TEXT NOT NULL,
            title_clean TEXT NOT NULL,
            publisher TEXT,
            category TEXT NOT NULL,
            certification TEXT NOT NULL,
            release_date TEXT,
            certification_date TEXT,
            country TEXT DEFAULT 'FR',
            certifying_body TEXT DEFAULT 'SNEP'
        )
        """)

        # Index
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_artist ON certifications(artist_clean)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_title ON certifications(title_clean)")

        self.conn.commit()
        print("✅ Base de données réinitialisée")

    def normalize_text(self, text):
        """Normalise le texte"""
        if not text:
            return ""

        # Supprimer les accents
        text = unicodedata.normalize("NFD", str(text))
        text = "".join(char for char in text if unicodedata.category(char) != "Mn")

        # Nettoyer
        text = text.strip().upper()
        text = text.replace("&", "AND").replace("$", "S")

        return text

    def parse_date(self, date_str):
        """Parse une date au format DD/MM/YYYY vers YYYY-MM-DD"""
        if not date_str or date_str == "":
            return None

        try:
            # Format DD/MM/YYYY
            parts = date_str.split("/")
            if len(parts) == 3:
                day, month, year = parts
                return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
        except Exception:
            pass

        return None

    def import_csv(self):
        """Importe le CSV dans la base"""
        if not self.csv_path.exists():
            print(f"❌ Fichier non trouvé : {self.csv_path}")
            return False

        print(f"📂 Lecture du fichier : {self.csv_path}")

        try:
            # Lire le CSV
            rows, headers = SimpleCSVReader.read_csv(self.csv_path)
            print(f"✅ {len(rows)} lignes lues")

            # Identifier les colonnes
            col_map = {}
            for h in headers:
                h_upper = h.upper()
                if "INTERPRETE" in h_upper or "ARTISTE" in h_upper:
                    col_map["artist"] = h
                elif "TITRE" in h_upper:
                    col_map["title"] = h
                elif "EDITEUR" in h_upper or "DISTRIBUTEUR" in h_upper:
                    col_map["publisher"] = h
                elif "CATEGORIE" in h_upper or "CATÉGORIE" in h_upper:
                    col_map["category"] = h
                elif "CERTIFICATION" in h_upper and "DATE" not in h_upper:
                    col_map["certification"] = h
                elif "SORTIE" in h_upper:
                    col_map["release_date"] = h
                elif "CONSTAT" in h_upper:
                    col_map["certification_date"] = h

            print("\n📋 Colonnes identifiées :")
            for key, value in col_map.items():
                print(f"  {key}: {value}")

            # Compter Josman pour debug
            josman_count = 0
            for row in rows:
                if "JOSMAN" in row.get(col_map.get("artist", ""), "").upper():
                    josman_count += 1
            print(f"\n🔍 {josman_count} lignes JOSMAN trouvées dans le CSV")

            # Importer
            cursor = self.conn.cursor()
            new_records = 0
            errors = 0

            for i, row in enumerate(rows):
                try:
                    # Extraire les valeurs
                    artist = row.get(col_map.get("artist", ""), "")
                    title = row.get(col_map.get("title", ""), "")
                    publisher = row.get(col_map.get("publisher", ""), "")
                    category = row.get(col_map.get("category", ""), "Singles")
                    certification = row.get(col_map.get("certification", ""), "")
                    release_date = self.parse_date(row.get(col_map.get("release_date", ""), ""))
                    cert_date = self.parse_date(row.get(col_map.get("certification_date", ""), ""))

                    if not artist or not title:
                        continue

                    # Normaliser
                    artist_clean = self.normalize_text(artist)
                    title_clean = self.normalize_text(title)

                    # Insérer dans la base
                    cursor.execute(
                        """
                        INSERT INTO certifications 
                        (artist_name, artist_clean, title, title_clean, publisher, 
                         category, certification, release_date, certification_date,
                         country, certifying_body)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                        (
                            artist,
                            artist_clean,
                            title,
                            title_clean,
                            publisher,
                            category,
                            certification,
                            release_date,
                            cert_date,
                            "FR",
                            "SNEP",
                        ),
                    )

                    new_records += 1

                    # Debug Josman (afficher les 5 premiers)
                    if "JOSMAN" in artist_clean and new_records <= 5:
                        print(f"  ✅ Ajouté: {title} - {certification}")

                    # Progression
                    if (i + 1) % 1000 == 0:
                        print(f"  Progression: {i + 1}/{len(rows)}")
                        self.conn.commit()

                except Exception as e:
                    errors += 1
                    if errors <= 3:
                        print(f"  ⚠️ Erreur ligne {i}: {e}")

            self.conn.commit()

            # Uniformiser les catégories APRÈS l'import
            cursor.execute("""
                UPDATE certifications 
                SET category = 'Singles' 
                WHERE category = 'Single'
            """)
            self.conn.commit()

            # Compter les corrections
            cursor.execute("SELECT changes()")
            changes = cursor.fetchone()[0]
            if changes > 0:
                print(f"  • Catégories uniformisées : {changes} 'Single' → 'Singles'")

            print("\n✅ Import terminé !")
            print(f"  • Nouveaux enregistrements : {new_records}")
            print(f"  • Erreurs : {errors}")

            return True

        except Exception as e:
            print(f"❌ Erreur : {e}")
            import traceback

            traceback.print_exc()
            return False

    def test_josman(self):
        """Test avec Josman"""
        cursor = self.conn.cursor()

        # Rechercher Josman
        cursor.execute("""
            SELECT * FROM certifications
            WHERE artist_clean LIKE '%JOSMAN%'
            ORDER BY certification_date DESC
        """)

        results = cursor.fetchall()

        if not results:
            print("❌ Aucune certification trouvée pour Josman dans la base")

            # Debug
            cursor.execute("SELECT COUNT(*) FROM certifications")
            total = cursor.fetchone()[0]
            print(f"  Total dans la base: {total}")

            if total > 0:
                cursor.execute("SELECT DISTINCT artist_name FROM certifications LIMIT 10")
                print("  Exemples d'artistes:")
                for row in cursor.fetchall():
                    print(f"    - {row[0]}")
            return

        print(f"\n✅ {len(results)} certifications trouvées pour JOSMAN")

        # Afficher quelques exemples
        print("\n📀 Exemples:")
        for result in results[:10]:
            # result est un tuple: (id, artist_name, artist_clean, title, title_clean, publisher, category, certification, release_date, cert_date, country, body)
            title = result[3]
            cert = result[7]
            date = result[9] if result[9] else "N/A"
            category = result[6]
            print(f"  • {title} ({category}) - {cert} - {date}")

    def get_stats(self):
        """Affiche les statistiques"""
        cursor = self.conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM certifications")
        total = cursor.fetchone()[0]

        print("\n📊 STATISTIQUES:")
        print(f"  • Total: {total} certifications")

        if total > 0:
            # Par catégorie
            cursor.execute("""
                SELECT category, COUNT(*) 
                FROM certifications 
                GROUP BY category 
                ORDER BY COUNT(*) DESC
            """)
            print("\n  Par catégorie:")
            for cat, count in cursor.fetchall():
                print(f"    - {cat}: {count}")

            # Par niveau
            cursor.execute("""
                SELECT certification, COUNT(*) 
                FROM certifications 
                GROUP BY certification 
                ORDER BY COUNT(*) DESC
                LIMIT 10
            """)
            print("\n  Top certifications:")
            for cert, count in cursor.fetchall():
                print(f"    - {cert}: {count}")

            # Top artistes
            cursor.execute("""
                SELECT artist_name, COUNT(*) 
                FROM certifications 
                GROUP BY artist_name 
                ORDER BY COUNT(*) DESC
                LIMIT 10
            """)
            print("\n  Top 10 artistes:")
            for artist, count in cursor.fetchall():
                print(f"    - {artist}: {count}")


def main():
    print("=" * 60)
    print(" IMPORT CERTIFICATIONS SNEP (Version corrigée)")
    print("=" * 60)

    importer = SimpleSNEPImporter()

    if not importer.csv_path.exists():
        print("\n❌ Fichier CSV non trouvé!")
        print(f"📁 Placez 'certif-.csv' dans: {importer.csv_path.parent}")
        return

    print("\n1. Importer le CSV")
    print("2. Tester avec Josman")
    print("3. Statistiques")
    print("4. Tout faire")

    choice = input("\nChoix (1-4): ").strip()

    if choice in ["1", "4"]:
        print("\n" + "=" * 40)
        print(" IMPORT CSV")
        print("=" * 40)
        importer.import_csv()

    if choice in ["2", "4"]:
        print("\n" + "=" * 40)
        print(" TEST JOSMAN")
        print("=" * 40)
        importer.test_josman()

    if choice in ["3", "4"]:
        print("\n" + "=" * 40)
        print(" STATISTIQUES")
        print("=" * 40)
        importer.get_stats()

    print("\n✅ Terminé!")


if __name__ == "__main__":
    main()
