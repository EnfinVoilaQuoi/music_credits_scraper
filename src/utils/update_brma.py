"""
Scraper pour les certifications musicales belges Ultratop.be
Script 2: Mise à jour automatique et manuelle de la base de données
"""

import argparse
import json
import logging
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
import schedule

# Lancé en direct (python src/utils/update_brma.py) ou via la GUI : sys.path[0]
# vaut alors src/utils/, donc `import src.*` (ajouté pour le fetch anti-Cloudflare)
# échouerait. On ajoute la racine du projet au path.

# Configurer l'encodage UTF-8 pour la console Windows
if sys.platform == "win32" and "pytest" not in sys.modules:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def safe_print(message: str):
    """Print sécurisé qui ne plante pas si stdout est fermé"""
    try:
        print(message)
    except (ValueError, AttributeError, OSError):
        # stdout fermé ou non disponible - ignorer silencieusement
        pass


class UltratopUpdater:
    """Scraper pour mettre à jour la base de données des certifications Ultratop"""

    def __init__(
        self,
        database_path="./data/certifications/brma/certif_brma.csv",
        output_dir="./data/certifications/brma",
        delay_min=2,
        delay_max=5,
    ):
        """
        Initialisation du scraper de mise à jour

        Args:
            database_path: Chemin vers la base de données existante
            output_dir: Répertoire de sortie pour les fichiers
            delay_min: Délai minimum entre requêtes (secondes)
            delay_max: Délai maximum entre requêtes (secondes)
        """
        self.base_url = "https://www.ultratop.be/fr/or-platine"
        self.database_path = Path(database_path)  # CLEAN (lu par le matcher)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # BRUT permanent (union des scrapes) — le clean en est dérivé (dédup+tri).
        # Convention brut+clean, alignée sur SNEP/RIAA.
        self.raw_path = self.output_dir / "brma_raw.csv"
        self.delay_min = delay_min
        self.delay_max = delay_max

        # Chargement de la base de données existante
        self.load_existing_database()

        # Configuration du logging
        self.setup_logging()

        # Headers pour simuler un navigateur
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Cache-Control": "max-age=0",
        }

        # Session pour maintenir les cookies
        self.session = requests.Session()
        self.session.headers.update(self.headers)

    def load_existing_database(self):
        """Charge la base de données existante"""
        if self.database_path.exists():
            self.existing_db = pd.read_csv(self.database_path, encoding="utf-8-sig")
            self.logger_print(f"Base de données chargée: {len(self.existing_db)} enregistrements")

            # Création d'un index pour vérification rapide des doublons.
            # Normaliser NaN→'' + strip : un titre vide stocké en blanc est lu
            # NaN par pandas ; sans ça la clé "…|nan|…" ne matche pas le "…||…"
            # créé par le scraper → les compilations (titre vide) se dupliquaient.
            def _k(v):
                return "" if pd.isna(v) else str(v).strip()

            self.existing_keys = set()
            for _, row in self.existing_db.iterrows():
                key = (
                    f"{_k(row['artist'])}|{_k(row['title'])}|"
                    f"{_k(row['certification_level'])}|{_k(row['certification_date'])}"
                )
                self.existing_keys.add(key)
        else:
            self.existing_db = pd.DataFrame()
            self.existing_keys = set()
            self.logger_print("Aucune base de données existante trouvée. Création d'une nouvelle.")

    def logger_print(self, message):
        """Print avec gestion d'erreur si logger pas encore initialisé ou stdout fermé"""
        try:
            print(message)
        except (ValueError, AttributeError, OSError):
            # stdout fermé ou non disponible - utiliser uniquement le logger
            pass
        if hasattr(self, "logger"):
            self.logger.info(message)

    def setup_logging(self):
        """Configuration du système de logging"""
        log_dir = self.output_dir / "logs"
        log_dir.mkdir(exist_ok=True)

        log_file = log_dir / f"ultratop_update_{datetime.now():%Y%m%d_%H%M%S}.log"

        # encoding='utf-8' sur le FileHandler + stream stdout (déjà ré-encodé
        # UTF-8 plus haut) : sinon les emojis (❌, →) crashent en cp1252.
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[
                logging.FileHandler(log_file, encoding="utf-8"),
                logging.StreamHandler(sys.stdout),
            ],
        )
        self.logger = logging.getLogger(__name__)

    def random_delay(self):
        """Délai aléatoire entre les requêtes"""
        delay = random.uniform(self.delay_min, self.delay_max)
        time.sleep(delay)

    def fetch_page(self, year, category):
        """
        Récupère une page de certifications via le navigateur anti-Cloudflare.

        Depuis la refonte d'ultratop.be (passé derrière Cloudflare), `requests`
        prend un 403 → on passe par patchright + profil persistant (cf.
        `src/scrapers/ultratop_fetch.py`). Le parsing reste identique.

        Args:
            year: Année
            category: 'albums' ou 'singles'

        Returns:
            BeautifulSoup object ou None si erreur / Cloudflare non résolu
        """
        from src.scrapers.ultratop_fetch import fetch_ultratop_soup

        self.logger.info(f"Récupération (anti-CF) : {self.base_url}/{year}/{category}")
        return fetch_ultratop_soup(year, category)

    def fetch_page_with_retry(self, year, category, max_retries=3):
        """
        Récupère une page avec relances. Le navigateur anti-Cloudflare
        (`CrawlAIScraperBase`) gère déjà sa propre logique headless→visible ;
        on rajoute juste quelques relances espacées en cas d'échec transitoire.

        Returns:
            BeautifulSoup object ou None
        """
        for attempt in range(max_retries):
            if attempt > 0:
                wait_time = attempt * 10  # 10s, 20s, 30s...
                self.logger.info(f"Attente de {wait_time}s avant nouvelle tentative...")
                time.sleep(wait_time)
            self.logger.info(f"Tentative {attempt + 1}/{max_retries}: {year}/{category}")
            soup = self.fetch_page(year, category)
            if soup is not None:
                return soup

        self.logger.error(f"❌ Échec après {max_retries} tentatives pour {year}/{category}")
        return None

    def parse_certification_date(self, text):
        """Parse les dates et niveaux de certification.

        Le niveau peut contenir un MULTIPLICATEUR (ex: '2x Platine', '3x Platine')
        → on capture tout entre ': ' et la date suivante (ou la fin), au lieu de
        se limiter aux lettres. L'ancien regex `[A-Za-zÀ-ÿ\\s]+` ratait ces
        multi-platine/or et créait des niveaux VIDES (cf. 819 lignes corrompues).
        """
        certifications = []
        pattern = r"(\d{2}/\d{2}/\d{4})\s*:\s*(.+?)(?=\s*\d{2}/\d{2}/\d{4}\s*:|$)"

        for date_str, level in re.findall(pattern, text):
            level = level.strip()
            if not level:
                continue  # garde-fou : jamais de niveau vide
            try:
                date = datetime.strptime(date_str, "%d/%m/%Y").strftime("%Y-%m-%d")
                certifications.append((date, level))
            except ValueError:
                self.logger.warning(f"Impossible de parser la date: {date_str}")

        return certifications

    def extract_certifications(self, soup, year, category):
        """Extrait les certifications d'une page"""
        certifications = []
        error_count = 0

        containers = soup.find_all("div", style=lambda x: x and "display:table-row" in x)

        for container in containers:
            try:
                title_div = container.find("div", class_="chart_title")
                if not title_div:
                    continue

                link_elem = title_div.find("a")
                if not link_elem:
                    continue

                detail_link = urljoin("https://www.ultratop.be", link_elem.get("href", ""))

                text_content = link_elem.get_text(separator="|", strip=True)
                parts = text_content.split("|")

                if len(parts) >= 2:
                    artist = parts[0].strip()
                    title = parts[1].strip()
                else:
                    artist = text_content
                    title = ""

                company_div = container.find("div", class_="company")
                if company_div:
                    cert_text = company_div.get_text(strip=True)
                    cert_list = self.parse_certification_date(cert_text)

                    for cert_date, cert_level in cert_list:
                        # Vérifier si cette certification existe déjà
                        key = f"{artist}|{title}|{cert_level}|{cert_date}"

                        if key not in self.existing_keys:
                            self.existing_keys.add(key)  # anti-doublon INTRA-run
                            certifications.append(
                                {
                                    "artist": artist,
                                    "title": title,
                                    "category": category,
                                    "certification_level": cert_level,
                                    "certification_date": cert_date,
                                    "year_page": year,
                                    "detail_url": detail_link,
                                    "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                }
                            )

            except (AttributeError, KeyError, IndexError, TypeError, ValueError) as e:
                self.logger.error(f"Erreur lors de l'extraction: {e}")
                error_count += 1
                continue

        self.logger.info(
            f"Extraction {year}/{category}: {len(certifications)} certifications, {error_count} erreurs"
        )
        return certifications

    def update_current_year(self):
        """Met à jour les certifications de l'année en cours"""
        current_year = datetime.now().year
        new_certifications = []
        categories = ["albums", "singles"]

        self.logger.info(f"=== Mise à jour pour l'année {current_year} ===")

        for category in categories:
            self.random_delay()

            soup = self.fetch_page(current_year, category)
            if soup:
                certifications = self.extract_certifications(soup, current_year, category)
                new_certifications.extend(certifications)
                self.logger.info(
                    f"Trouvé {len(certifications)} nouvelles certifications pour {current_year}/{category}"
                )

        return new_certifications

    def update_recent_years(self, years_back=2):
        """
        Met à jour les certifications des années récentes

        Args:
            years_back: Nombre d'années à vérifier en arrière
        """
        current_year = datetime.now().year
        new_certifications = []
        categories = ["albums", "singles"]

        for year in range(current_year - years_back, current_year + 1):
            self.logger.info(f"=== Vérification année {year} ===")

            for category in categories:
                self.random_delay()

                soup = self.fetch_page(year, category)
                if soup:
                    certifications = self.extract_certifications(soup, year, category)
                    new_certifications.extend(certifications)

                    if certifications:
                        self.logger.info(
                            f"Trouvé {len(certifications)} nouvelles certifications pour {year}/{category}"
                        )

        return new_certifications

    def _dedup_df(self, df):
        """Dédup clé métier + collapse niveau VIDE/RENSEIGNÉ (NaN normalisé).

        1) retire les doublons exacts (artiste+titre+catégorie+niveau+date) ;
        2) pour une même certif (artiste+titre+catégorie+date), si une ligne au
           niveau renseigné existe, retire la(les) ligne(s) au niveau vide.
        La normalisation NaN→'' évite que titre vide (NaN) et '' soient vus
        comme différents.
        """
        key_cols = ["artist", "title", "category", "certification_level", "certification_date"]
        if df.empty or not all(c in df.columns for c in key_cols):
            return df
        before = len(df)
        df = df.copy()
        for c in key_cols:
            df[c] = df[c].fillna("").astype(str).str.strip()
        # Clés artiste/titre INSENSIBLES À LA CASSE : "'n Zalige kerst!" et
        # "'N zalige kerst!" = même certif. On garde la 1re occurrence (sa casse).
        df["_ka"] = df["artist"].str.upper()
        df["_kt"] = df["title"].str.upper()

        # 1) Doublons EXACTS (même certif scrapée 2× / variante de casse)
        df = df.drop_duplicates(
            ["_ka", "_kt", "category", "certification_level", "certification_date"], keep="first"
        )

        # 2) Collapse SÛR : on retire UNIQUEMENT les lignes au niveau VIDE qui ont
        #    une contrepartie renseignée pour la même certif (artiste+titre+
        #    catégorie+date). On ne supprime JAMAIS un niveau réel → un Platine
        #    (date X) et un Double Platine (date Y) sont TOUS DEUX conservés, et
        #    un niveau vraiment vide sans contrepartie est gardé tel quel.
        grp = ["_ka", "_kt", "category", "certification_date"]
        has_filled = df.groupby(grp)["certification_level"].transform(
            lambda s: (s.str.strip() != "").any()
        )
        is_empty = df["certification_level"].str.strip() == ""
        df = df[~(is_empty & has_filled)]

        df = df.drop(columns=["_ka", "_kt"])
        removed = before - len(df)
        if removed:
            self.logger.info(f"Déduplication : {removed} doublon(s)/vide(s) retiré(s)")
        return df

    def dedup_database(self):
        """« Nettoyer » : régénère le CLEAN (certif_brma.csv) depuis le BRUT
        (dédup métier + tri) SANS scraper. Backup du clean avant écriture."""
        raw = self._load_raw()
        if raw.empty:
            self.logger.info("Brut vide, rien à nettoyer")
            return
        before = len(self.existing_db)
        cleaned = self._clean_from(raw)
        self._write_clean(cleaned)
        self.logger.info(f"Clean régénéré depuis le brut : {before} → {len(cleaned)} ligne(s)")

    def _load_raw(self):
        """Charge le brut `brma_raw.csv` (union permanente des scrapes). Seedé
        depuis le clean existant au premier appel (meilleur historique dispo)."""
        if self.raw_path.exists():
            return pd.read_csv(self.raw_path, encoding="utf-8-sig")
        return self.existing_db.copy()

    def _write_raw(self, df):
        """Écrit le brut (backup avant écriture, écriture atomique)."""
        import os
        import shutil

        if self.raw_path.exists():
            bdir = self.output_dir / "backups"
            bdir.mkdir(exist_ok=True)
            shutil.copy2(self.raw_path, bdir / f"raw_backup_{datetime.now():%Y%m%d_%H%M%S}.csv")
        tmp = self.raw_path.with_suffix(".rawtmp")
        try:
            df.to_csv(tmp, index=False, encoding="utf-8-sig")
            os.replace(tmp, self.raw_path)
        except Exception:
            if tmp.exists():
                tmp.unlink()
            raise

    def _write_clean(self, clean_df):
        """Écrit le clean `certif_brma.csv` (backup du clean avant, atomique)."""
        import os

        if self.database_path.exists():
            bdir = self.output_dir / "backups"
            bdir.mkdir(exist_ok=True)
            bf = bdir / f"backup_{datetime.now():%Y%m%d_%H%M%S}.csv"
            self.existing_db.to_csv(bf, index=False, encoding="utf-8-sig")
            self.logger.info(f"Backup créé: {bf}")
        tmp_path = self.database_path.with_suffix(".tmp")
        try:
            clean_df.to_csv(tmp_path, index=False, encoding="utf-8-sig")
            os.replace(tmp_path, self.database_path)
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink()
            raise

    def _clean_from(self, raw_df):
        """Dérive le clean depuis un brut : dédup métier + tri (date desc)."""
        return self._dedup_df(raw_df).sort_values(
            ["certification_date", "artist", "title"], ascending=[False, True, True]
        )

    def save_updated_database(self, new_certifications):
        """Accumule les certifs scrapées dans le BRUT (brma_raw.csv) puis dérive
        le CLEAN (certif_brma.csv) — convention brut+clean."""
        if not new_certifications:
            self.logger.info("Aucune nouvelle certification trouvée")
            # Fraîcheur = date de dernière VÉRIFICATION, pas de dernier ajout : on
            # horodate même sans nouveauté, sinon la GUI affiche une MàJ périmée
            # (Ultratop n'a quasi jamais de nouvelle certif entre deux runs).
            raw = self._load_raw()
            if not raw.empty:
                self.update_metadata(self._clean_from(raw), 0)
            return

        new_df = pd.DataFrame(new_certifications)

        # 1. BRUT : union de tout ce qui a été scrapé (dédup EXACTE, aucune perte)
        raw = self._load_raw()
        raw_updated = (
            pd.concat([raw, new_df], ignore_index=True) if not raw.empty else new_df
        ).drop_duplicates(ignore_index=True)
        self._write_raw(raw_updated)

        # 2. CLEAN dérivé du brut : dédup métier + tri → certif_brma.csv
        clean = self._clean_from(raw_updated)
        self._write_clean(clean)

        self.logger.info(f"Clean mis à jour: {self.database_path} ({len(clean)} lignes)")
        self.logger.info(
            f"Brut: {len(raw_updated)} lignes ; ajouté {len(new_certifications)} scrapée(s)"
        )

        self.update_metadata(clean, len(new_certifications))
        self.generate_update_report(new_certifications)

    def update_metadata(self, updated_db, new_count):
        """Met à jour le fichier de métadonnées"""
        metadata_path = self.output_dir / "metadata.json"

        if metadata_path.exists():
            with open(metadata_path, encoding="utf-8") as f:
                metadata = json.load(f)
        else:
            metadata = {}

        now = datetime.now().isoformat()
        metadata["last_update"] = now
        metadata["total_records"] = len(updated_db)
        metadata["new_records_added"] = new_count
        metadata["unique_artists"] = updated_db["artist"].nunique()
        # Fraîcheur par source (uniforme avec SNEP/RIAA) : BRMA = scrape global
        # d'Ultratop, donc une seule source logique « GLOBAL ». Permet à
        # cert_source.read_freshness de distinguer MàJ globale / récup artiste.
        updates = metadata.get("updates") or {}
        updates["GLOBAL"] = now
        metadata["updates"] = updates
        metadata["last_source"] = "GLOBAL"
        metadata["count"] = len(updated_db)

        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)

    def generate_update_report(self, new_certifications):
        """Génère un rapport de mise à jour"""
        if not new_certifications:
            return

        report_dir = self.output_dir / "reports"
        report_dir.mkdir(exist_ok=True)

        report_file = report_dir / f"update_report_{datetime.now():%Y%m%d_%H%M%S}.txt"

        with open(report_file, "w", encoding="utf-8") as f:
            f.write("RAPPORT DE MISE À JOUR ULTRATOP\n")
            f.write(f"{'=' * 50}\n")
            f.write(f"Date: {datetime.now():%Y-%m-%d %H:%M:%S}\n")
            f.write(f"Nouvelles certifications: {len(new_certifications)}\n\n")

            f.write("DÉTAIL DES NOUVELLES CERTIFICATIONS:\n")
            f.write("-" * 50 + "\n")

            for cert in sorted(
                new_certifications, key=lambda x: x["certification_date"], reverse=True
            ):
                f.write(
                    f"{cert['certification_date']} - {cert['certification_level']}: "
                    f"{cert['artist']} - {cert['title']} ({cert['category']})\n"
                )

        self.logger.info(f"Rapport généré: {report_file}")

    def retry_missing_pages(self):
        """
        Tente de récupérer les pages qui ont retourné des erreurs 500
        Basé sur votre description des années problématiques

        Returns:
            List[Dict]: Liste des certifications récupérées
        """
        self.logger.info("=== RÉCUPÉRATION DES PAGES MANQUANTES ===")

        # Années connues pour avoir des problèmes (erreur 500)
        # Ajustez cette liste selon vos observations
        problematic_years = []

        # Vérifier quelles années ont des données manquantes ou incomplètes
        if hasattr(self, "existing_db") and not self.existing_db.empty:
            # Analyser la base existante pour détecter les années avec peu de données
            year_counts = self.existing_db.groupby("year_page").size()
            avg_count = year_counts.mean()

            # Identifier les années avec moins de 50% des données moyennes
            for year, count in year_counts.items():
                if count < avg_count * 0.5:
                    problematic_years.append(int(year))

            self.logger.info(f"Années avec données incomplètes détectées: {problematic_years}")
        else:
            # Si pas de base existante, essayer les années récentes qui posent souvent problème
            current_year = datetime.now().year
            problematic_years = [current_year - 1, current_year - 2]
            self.logger.info(f"Tentative sur les années récentes: {problematic_years}")

        all_recovered = []
        categories = ["albums", "singles"]

        for year in problematic_years:
            for category in categories:
                self.logger.info(f"Tentative de récupération: {year}/{category}")

                # Délai plus long pour les pages problématiques
                time.sleep(random.uniform(5, 10))

                try:
                    # Récupérer la page avec retry
                    soup = self.fetch_page_with_retry(year, category, max_retries=3)

                    if soup:
                        # Extraire les certifications
                        certifications = self.extract_certifications(soup, year, category)

                        if certifications:
                            self.logger.info(
                                f"✅ Récupéré {len(certifications)} certifications pour {year}/{category}"
                            )
                            all_recovered.extend(certifications)
                        else:
                            self.logger.warning(
                                f"⚠️ Aucune certification trouvée pour {year}/{category}"
                            )
                    else:
                        self.logger.error(f"❌ Impossible de récupérer {year}/{category}")

                except (
                    requests.RequestException,
                    AttributeError,
                    KeyError,
                    IndexError,
                    TypeError,
                    ValueError,
                ) as e:
                    self.logger.error(f"❌ Erreur lors de la récupération {year}/{category}: {e}")
                    continue

        self.logger.info(f"Total pages manquantes récupérées: {len(all_recovered)} certifications")
        return all_recovered

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

        except Exception:
            self.logger.exception("Erreur lors de la mise à jour")

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

        except Exception:
            self.logger.exception("Erreur lors de la mise à jour programmée")

    def schedule_monthly_updates(self, day_of_month=1, hour=3):
        """
        Programme les mises à jour mensuelles

        Args:
            day_of_month: Jour du mois pour la mise à jour (défaut: 1er)
            hour: Heure de la mise à jour (défaut: 3h du matin)
        """
        # Configuration du job mensuel
        schedule.every().month.at(f"{hour:02d}:00").do(self.run_scheduled_update)

        self.logger.info(
            f"Mise à jour mensuelle programmée pour le {day_of_month} de chaque mois à {hour}h"
        )
        self._running = True

        # Boucle d'exécution — arrêtée via self._running = False ou KeyboardInterrupt
        try:
            while self._running:
                schedule.run_pending()
                time.sleep(3600)  # Vérification toutes les heures
        except KeyboardInterrupt:
            self.logger.info("Mise à jour mensuelle arrêtée")


def main():
    """Fonction principale avec interface en ligne de commande"""
    parser = argparse.ArgumentParser(description="Mise à jour des certifications Ultratop")
    parser.add_argument(
        "--mode",
        choices=["manual", "scheduled", "once"],
        default="manual",
        help="Mode de mise à jour: manual (interactif), scheduled (programmé), once (une fois)",
    )
    parser.add_argument(
        "--years-back",
        type=int,
        default=2,
        help="Nombre d'années à vérifier en arrière (défaut: 2)",
    )
    parser.add_argument(
        "--database",
        type=str,
        default="./data/certifications/brma/certif_brma.csv",
        help="Chemin vers la base de données",
    )
    parser.add_argument(
        "--output-dir", type=str, default="./data/certifications/brma", help="Répertoire de sortie"
    )
    parser.add_argument(
        "--delay-min", type=float, default=2, help="Délai minimum entre requêtes (secondes)"
    )
    parser.add_argument(
        "--delay-max", type=float, default=5, help="Délai maximum entre requêtes (secondes)"
    )
    parser.add_argument(
        "--dedup",
        action="store_true",
        help="Nettoie le CSV existant (dédup + collapse niveau vide) sans scraper",
    )

    args = parser.parse_args()

    # Création de l'updater
    updater = UltratopUpdater(
        database_path=args.database,
        output_dir=args.output_dir,
        delay_min=args.delay_min,
        delay_max=args.delay_max,
    )

    if args.dedup:
        updater.dedup_database()
        sys.exit(0)

    if args.mode == "manual":
        # Mode interactif
        safe_print("\n=== MISE À JOUR ULTRATOP - MODE MANUEL ===")
        safe_print("1. Mise à jour de l'année en cours uniquement")
        safe_print("2. Mise à jour des 2 dernières années")
        safe_print("3. Mise à jour personnalisée (choisir le nombre d'années)")
        safe_print("4. Programmer les mises à jour mensuelles")
        safe_print("5. Quitter")

        while True:
            choice = input("\nVotre choix (1-5): ").strip()

            if choice == "1":
                new_certs = updater.update_current_year()
                updater.save_updated_database(new_certs)

            elif choice == "2":
                updater.run_manual_update(years_back=2)

            elif choice == "3":
                years = input("Nombre d'années à vérifier en arrière: ").strip()
                try:
                    years = int(years)
                    updater.run_manual_update(years_back=years)
                except ValueError:
                    safe_print("Nombre invalide")

            elif choice == "4":
                safe_print("\nConfiguration des mises à jour mensuelles:")
                day = input("Jour du mois (1-28) [défaut: 1]: ").strip() or "1"
                hour = input("Heure (0-23) [défaut: 3]: ").strip() or "3"

                try:
                    day = int(day)
                    hour = int(hour)
                    safe_print(f"\nLancement des mises à jour mensuelles (jour {day} à {hour}h)")
                    safe_print("Appuyez sur Ctrl+C pour arrêter")
                    updater.schedule_monthly_updates(day, hour)
                except ValueError:
                    safe_print("Valeurs invalides")
                except KeyboardInterrupt:
                    safe_print("\nArrêt des mises à jour programmées")

            elif choice == "5":
                safe_print("Au revoir!")
                break

            else:
                safe_print("Choix invalide")

    elif args.mode == "scheduled":
        # Mode programmé (pour cron ou service système)
        safe_print("Lancement des mises à jour programmées mensuelles")
        safe_print("Appuyez sur Ctrl+C pour arrêter")
        try:
            updater.schedule_monthly_updates()
        except KeyboardInterrupt:
            safe_print("\nArrêt des mises à jour programmées")

    elif args.mode == "once":
        # Mode une seule fois (pour cron ou scripts)
        updater.run_manual_update(years_back=args.years_back)

    sys.exit(0)


if __name__ == "__main__":
    main()
