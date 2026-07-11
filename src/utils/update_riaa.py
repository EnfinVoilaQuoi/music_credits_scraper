#!/usr/bin/env python3
"""
Script de mise à jour automatique et manuelle des certifications RIAA
Compatible avec le système de gestion unifié des certifications
"""

import argparse
import io
import logging
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

# Configurer l'encodage UTF-8 pour la console Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Import du scraper principal (patchright v2 — remplace l'ancien Selenium ;
# API compatible : init_driver/close_driver/scrape_by_date_range/scrape_by_artist)
sys.path.append(str(Path(__file__).parent.parent.parent))
from src.scrapers.riaa_scraper_v2 import RIAAScraperV2 as RIAAScraper


class RIAADatabaseUpdater:
    """Gestionnaire de mise à jour de la base de données RIAA"""

    def __init__(self, base_dir="music_credits_scraper"):
        """Initialise le gestionnaire de mise à jour"""

        # Configuration des chemins
        self.base_dir = Path(base_dir) if base_dir else Path(__file__).parent.parent.parent
        self.data_dir = self.base_dir / "data" / "certifications" / "riaa"
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Chemins des fichiers
        self.db_path = self.data_dir / "riaa.db"
        self.csv_path = self.data_dir / "riaa.csv"
        self.log_path = self.data_dir / "update_log.txt"

        # Configuration du logging
        self.setup_logging()

        # Initialise le scraper
        self.scraper = None

        # Charge ou crée la base de données
        self.init_database()

    def setup_logging(self):
        """Configure le système de logging"""
        log_format = "%(asctime)s - %(levelname)s - %(message)s"

        # Logger vers fichier
        file_handler = logging.FileHandler(self.log_path, encoding="utf-8")
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(logging.Formatter(log_format))

        # Logger vers console
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(logging.Formatter(log_format))

        # Configuration du logger
        self.logger = logging.getLogger("RIAA_Updater")
        self.logger.setLevel(logging.INFO)
        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)

    def init_database(self):
        """Initialise ou charge la base de données SQLite"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Création de la table principale
        cursor.execute("""
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
        """)

        # Table de suivi des mises à jour
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS update_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                update_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                period_start TEXT,
                period_end TEXT,
                records_added INTEGER,
                records_updated INTEGER,
                status TEXT
            )
        """)

        # Indices pour optimiser les recherches
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_artist ON certifications(artist)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_date ON certifications(certification_date)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_title ON certifications(title)")

        conn.commit()
        conn.close()

        self.logger.info(f"Base de données initialisée: {self.db_path}")

    def get_last_update_date(self) -> datetime | None:
        """Date de la dernière certif connue — lue depuis certif_riaa.csv (le
        fichier canonique alimentant le matcher), pas la base sqlite."""
        try:
            if CERTIF_CSV.exists():
                df = pd.read_csv(CERTIF_CSV, encoding="utf-8-sig", dtype=str)
                dcol = next((c for c in df.columns if c.lower() == "certification_date"), None)
                if dcol:
                    isod = df[dcol].map(_riaa_iso)
                    isod = isod[isod != ""]
                    if len(isod):
                        return datetime.strptime(isod.max(), "%Y-%m-%d")
        except Exception as e:
            self.logger.error(f"Lecture dernière date (certif_riaa.csv) : {e}")
        # Repli : fin de la base historique
        return datetime(2017, 10, 1)

    def update_from_scraped_data(self, data: list[dict]) -> tuple:
        """Met à jour la base de données avec les données scrapées"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        added = 0
        updated = 0

        for record in data:
            try:
                # Prépare les données
                artist = record.get("artist", "").strip()
                title = record.get("title", "").strip()
                cert_date = record.get("certification_date", "").strip()
                award = record.get("award_level", "").strip()

                if not all([artist, title]):
                    continue

                # Vérifie si l'enregistrement existe
                cursor.execute(
                    """
                    SELECT id FROM certifications
                    WHERE artist = ? AND title = ? 
                    AND certification_date = ? AND award_level = ?
                """,
                    (artist, title, cert_date, award),
                )

                existing = cursor.fetchone()

                if existing:
                    # Mise à jour
                    cursor.execute(
                        """
                        UPDATE certifications
                        SET label = ?, format = ?, units = ?,
                            last_updated = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """,
                        (
                            record.get("label", ""),
                            record.get("format", ""),
                            record.get("units"),
                            existing[0],
                        ),
                    )
                    updated += 1
                else:
                    # Insertion
                    cursor.execute(
                        """
                        INSERT INTO certifications
                        (artist, title, certification_date, release_date,
                         label, format, award_level, units, category, type, genre)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                        (
                            artist,
                            title,
                            cert_date,
                            record.get("release_date", ""),
                            record.get("label", ""),
                            record.get("format", ""),
                            award,
                            record.get("units"),
                            record.get("category", ""),
                            record.get("type", ""),
                            record.get("genre", ""),
                        ),
                    )
                    added += 1

            except Exception as e:
                self.logger.error(f"Erreur traitement record: {e}")
                continue

        conn.commit()
        conn.close()

        # CSV-centré : fusionne aussi dans certif_riaa.csv (le fichier du matcher),
        # pour que les MàJ bulk (backfill 2017→now) alimentent le raccordement.
        try:
            _merge_certif_csv(_flatten_records(data))
        except Exception as e:
            self.logger.error(f"Fusion certif_riaa.csv : {e}")

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

            self.logger.info("=== MISE À JOUR RIAA ===")
            self.logger.info(f"Période: {start_str} - {end_str}")

            # Initialise le scraper
            self.scraper = RIAAScraper(headless=True)
            self.scraper.init_driver()

            try:
                # Scrape les certifications récentes
                self.logger.info("Scraping des certifications en cours...")
                results = self.scraper.scrape_by_date_range(start_str, end_str, "certification")

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

            # Écart en JOURS : un trou < 30 j (ex. 28 j entre le 02/06 et fin juin)
            # doit aussi déclencher la récup. L'ancien `//30` arrondissait à 0 et
            # concluait à tort « déjà à jour », laissant le mois courant non scrapé.
            gap_days = (datetime.now() - last_date).days

            if gap_days <= 0:
                self.logger.info("Base de données déjà à jour")
                return True

            self.logger.info(f"{gap_days} jour(s) à récupérer")

            # Met à jour par tranches mensuelles pour éviter timeout.
            # `now` est FIGÉ ici : sinon, en fin de boucle, current_date rattrape
            # l'instant T mais datetime.now() a déjà avancé (durée du scrape) →
            # la condition reste vraie et on re-scrape le mois courant à l'infini.
            now = datetime.now()
            current_date = last_date
            total_added = 0
            total_updated = 0

            while current_date < now:
                # Période d'un mois
                start_date = current_date
                end_date = min(current_date + timedelta(days=30), now)

                # Format pour RIAA
                start_str = start_date.strftime("%m/%d/%Y")
                end_str = end_date.strftime("%m/%d/%Y")

                self.logger.info(f"Traitement période: {start_str} - {end_str}")

                # Initialise le scraper pour cette période
                if not self.scraper:
                    self.scraper = RIAAScraper(headless=True)
                    self.scraper.init_driver()

                try:
                    results = self.scraper.scrape_by_date_range(start_str, end_str, "certification")

                    if results:
                        added, updated = self.update_from_scraped_data(results)
                        total_added += added
                        total_updated += updated
                        self.logger.info(f"  -> {added} ajoutées, {updated} mises à jour")

                    # Pause entre les requêtes
                    time.sleep(5)

                except Exception as e:
                    self.logger.error(f"Erreur période {start_str}-{end_str}: {e}")

                # Garde-fou anti-stagnation : si la tranche n'avance pas, on sort.
                if end_date <= current_date:
                    break
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

        cursor.execute(
            """
            INSERT INTO update_history
            (period_start, period_end, records_added, records_updated, status)
            VALUES (?, ?, ?, ?, ?)
        """,
            (start, end, added, updated, status),
        )

        conn.commit()
        conn.close()

    def export_to_csv(self):
        """Exporte la base de données vers CSV"""
        try:
            conn = sqlite3.connect(self.db_path)

            # Lecture de toutes les certifications
            df = pd.read_sql_query(
                """
                SELECT artist, title, certification_date, release_date,
                       label, format, award_level, units, category, type, genre
                FROM certifications
                ORDER BY certification_date DESC, artist, title
            """,
                conn,
            )

            conn.close()

            # Sauvegarde
            df.to_csv(self.csv_path, index=False, encoding="utf-8-sig")
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

        if choice == "1":
            self.update_recent_certifications(1)

        elif choice == "2":
            self.update_missing_months()

        elif choice == "3":
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

        elif choice == "4":
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

    def get_statistics(self) -> dict:
        """Retourne les statistiques de la base de données"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        stats = {}

        # Total certifications
        cursor.execute("SELECT COUNT(*) FROM certifications")
        stats["total"] = cursor.fetchone()[0]

        # Par niveau
        cursor.execute("""
            SELECT award_level, COUNT(*) 
            FROM certifications 
            GROUP BY award_level
        """)
        stats["by_level"] = dict(cursor.fetchall())

        # Top artistes
        cursor.execute("""
            SELECT artist, COUNT(*) as count
            FROM certifications
            GROUP BY artist
            ORDER BY count DESC
            LIMIT 10
        """)
        stats["top_artists"] = cursor.fetchall()

        # Dernière mise à jour
        cursor.execute("""
            SELECT MAX(last_updated) FROM certifications
        """)
        stats["last_updated"] = cursor.fetchone()[0]

        conn.close()

        return stats


# ---------------------------------------------------------------------------
# RIAA CSV-centré (comme BRMA) : on écrit dans certif_riaa.csv, le fichier que
# lit le matcher unifié. Schéma compatible avec l'historique existant.
# ---------------------------------------------------------------------------
CERTIF_CSV = (
    Path(__file__).parent.parent.parent / "data" / "certifications" / "riaa" / "certif_riaa.csv"
)
CERTIF_COLUMNS = [
    "Artist",
    "Title",
    "Certification_Date",
    "Label",
    "Format_Type",
    "Release_Date",
    "Group_Type",
    "Media_Type",
    "Certification_Type",
    "Genre",
]


def _riaa_iso(s: str) -> str:
    """« October 17, 2017 » → « 2017-10-17 ». Tolère déjà-ISO / vide."""
    s = (s or "").strip()
    if not s or s.lower() == "none":
        return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s.title() if "," in s else s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s


# Variantes d'orthographe d'un même format RIAA → forme canonique.
_FORMAT_ALIASES = {
    "SHORT FORM ALBUM": "SHORTFORMALBUM",
    "SHORTFORM ALBUM": "SHORTFORMALBUM",
}


def _norm_format(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return s
    return _FORMAT_ALIASES.get(re.sub(r"\s+", " ", s).upper(), s)


def _riaa_level(s: str) -> str:
    """« 4x Multi-Platinum » → « 4x Platinum » (aligne historique et scraper)."""
    s = (s or "").strip()
    m = re.match(r"(\d+)\s*x\s*multi-?platinum", s, re.I)
    if m:
        return f"{m.group(1)}x Platinum"
    if re.fullmatch(r"multi-?platinum", s, re.I):
        return "Platinum"
    return s


def _flatten_records(records: list[dict]) -> list[dict]:
    """Aplati les enregistrements scrapés (ligne principale + historique) vers
    le schéma certif_riaa.csv. Avec MORE DETAILS, chaque palier = une ligne."""
    rows = []
    for rec in records:
        base = {
            "Artist": rec.get("artist", ""),
            "Title": rec.get("title", ""),
            "Label": rec.get("label", ""),
            "Format_Type": _norm_format(rec.get("format", "")),
            "Group_Type": "",
            "Media_Type": "",
            "Genre": "",
        }
        hist = rec.get("history") or []
        if hist:
            for h in hist:
                lvl = h.get("certification_level", "")
                if not lvl:
                    continue
                rows.append(
                    {
                        **base,
                        "Certification_Date": h.get("certification_date", "")
                        or rec.get("certification_date", ""),
                        "Release_Date": h.get("release_date", ""),
                        "Media_Type": h.get("category", "") or "",
                        "Genre": h.get("genre", "") or "",
                        "Certification_Type": _riaa_level(lvl),
                    }
                )
        else:
            rows.append(
                {
                    **base,
                    "Certification_Date": rec.get("certification_date", ""),
                    "Release_Date": rec.get("release_date", ""),
                    "Certification_Type": _riaa_level(
                        rec.get("award_level") or rec.get("certification_level", "")
                    ),
                }
            )
    return rows


def _merge_certif_csv(new_rows: list[dict]) -> tuple:
    """Fusionne des lignes dans certif_riaa.csv (dédup normalisée, backup avant).
    Retourne (total_après, ajoutées)."""
    if not new_rows:
        return (0, 0)
    new_df = pd.DataFrame(new_rows)
    for c in CERTIF_COLUMNS:
        if c not in new_df.columns:
            new_df[c] = ""
    new_df = new_df[CERTIF_COLUMNS]

    if CERTIF_CSV.exists():
        old = pd.read_csv(CERTIF_CSV, encoding="utf-8-sig", dtype=str).fillna("")
        old = old[[c for c in old.columns if c in CERTIF_COLUMNS]]
        for c in CERTIF_COLUMNS:
            if c not in old.columns:
                old[c] = ""
        old = old[CERTIF_COLUMNS]
        before_existing = len(old)
        # backup avant écriture
        bdir = CERTIF_CSV.parent / "backups"
        bdir.mkdir(exist_ok=True)
        old.to_csv(
            bdir / f"certif_riaa_backup_{datetime.now():%Y%m%d_%H%M%S}.csv",
            index=False,
            encoding="utf-8-sig",
        )
        combined = pd.concat([old, new_df], ignore_index=True)
    else:
        before_existing = 0
        combined = new_df

    def norm(s):
        return re.sub(r"\s+", " ", str(s)).strip().upper()

    combined["_k"] = (
        combined["Artist"].map(norm)
        + "|"
        + combined["Title"].map(norm)
        + "|"
        + combined["Certification_Type"].map(lambda x: norm(_riaa_level(x)))
        + "|"
        + combined["Certification_Date"].map(_riaa_iso)
    )
    combined = combined.drop_duplicates("_k", keep="first").drop(columns="_k")
    combined.to_csv(CERTIF_CSV, index=False, encoding="utf-8-sig")
    return (len(combined), len(combined) - before_existing)


def fetch_artist(artist: str) -> bool:
    """Récupère les certifs RIAA d'un artiste (avec MORE DETAILS) et les fusionne
    dans certif_riaa.csv (CSV-centré, alimente le matcher)."""
    from src.scrapers.riaa_scraper_v2 import RIAAScraperV2

    print(f"=== RIAA par artiste : {artist} ===")
    scraper = RIAAScraperV2(headless=True)
    records = scraper.scrape_by_artist(artist, get_details=True)
    if not records:
        print("Aucune certification RIAA trouvée (ou Cloudflare non résolu)")
        return False
    rows = _flatten_records(records)
    total, added = _merge_certif_csv(rows)
    print(f"✅ RIAA {artist} : {added} ligne(s) ajoutée(s) (total {total})")
    return True


def clean_certif_csv() -> tuple:
    """Nettoie certif_riaa.csv SANS scraper : retire artiste/titre vides,
    dédoublonne (niveau normalisé : « 4x Multi-Platinum » = « 4x Platinum »).
    Backup avant écriture. Retourne (avant, après)."""
    if not CERTIF_CSV.exists():
        print("Pas de fichier certif_riaa.csv")
        return (0, 0)
    df = pd.read_csv(CERTIF_CSV, encoding="utf-8-sig", dtype=str).fillna("")
    df = df[[c for c in df.columns if c in CERTIF_COLUMNS]]
    for c in CERTIF_COLUMNS:
        if c not in df.columns:
            df[c] = ""
    df = df[CERTIF_COLUMNS]
    before = len(df)

    bdir = CERTIF_CSV.parent / "backups"
    bdir.mkdir(exist_ok=True)
    df.to_csv(
        bdir / f"certif_riaa_backup_{datetime.now():%Y%m%d_%H%M%S}.csv",
        index=False,
        encoding="utf-8-sig",
    )

    df = df[(df["Artist"].str.strip() != "") & (df["Title"].str.strip() != "")]
    df["Format_Type"] = df["Format_Type"].map(_norm_format)  # variantes → canonique

    def norm(s):
        return re.sub(r"\s+", " ", str(s)).strip().upper()

    df["_k"] = (
        df["Artist"].map(norm)
        + "|"
        + df["Title"].map(norm)
        + "|"
        + df["Format_Type"].map(norm)
        + "|"
        + df["Certification_Type"].map(lambda x: norm(_riaa_level(x)))
        + "|"
        + df["Certification_Date"].map(_riaa_iso)
    )
    df = df.drop_duplicates("_k", keep="first").drop(columns="_k")
    df.to_csv(CERTIF_CSV, index=False, encoding="utf-8-sig")
    print(f"✅ Nettoyage RIAA : {before} → {len(df)} lignes (-{before - len(df)})")
    return (before, len(df))


def main():
    """Fonction principale"""
    parser = argparse.ArgumentParser(description="Mise à jour des certifications RIAA")
    parser.add_argument(
        "--auto", action="store_true", help="Mise à jour automatique des mois manquants"
    )
    parser.add_argument("--months", type=int, default=1, help="Nombre de mois à récupérer")
    parser.add_argument("--manual", action="store_true", help="Mode manuel interactif")
    parser.add_argument("--stats", action="store_true", help="Afficher les statistiques")
    parser.add_argument(
        "--artist",
        type=str,
        default=None,
        help="Récupérer les certifs RIAA d'un artiste (fusion dans certif_riaa.csv)",
    )
    parser.add_argument(
        "--clean", action="store_true", help="Nettoie certif_riaa.csv (dédup + vides) sans scraper"
    )

    args = parser.parse_args()

    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    if args.clean:
        clean_certif_csv()
        sys.exit(0)

    # Récup par artiste : CSV-centré, pas besoin de la base sqlite
    if args.artist:
        ok = fetch_artist(args.artist)
        sys.exit(0 if ok else 1)

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
        for level, count in stats["by_level"].items():
            print(f"  {level}: {count}")
        print("\nTop 10 artistes:")
        for artist, count in stats["top_artists"]:
            print(f"  {artist}: {count} certifications")

    else:
        # Mode interactif par défaut
        updater.manual_update()


if __name__ == "__main__":
    main()
