#!/usr/bin/env python3
"""
Scraper RIAA - Version corrigée pour les sessions Chrome et les dates
"""

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from bs4 import BeautifulSoup
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from urllib.parse import quote_plus
import pandas as pd
import logging
import time
import csv
import argparse
import sys
import tempfile
import os
import shutil
import uuid

# Configuration
MAX_WAIT_DETAIL = 10
POLL_INTERVAL = 0.5

# Units de base pour chaque niveau
BASE_UNITS = {
    "gold": 500000,
    "platinum": 1000000, 
    "diamond": 10000000
}

# Logging
logger = logging.getLogger(__name__)

class RIAAScraper:
    """Scraper pour les certifications RIAA avec gestion améliorée des sessions"""
    
    def __init__(self, headless: bool = False, output_dir: str = "data"):
        """
        Initialise le scraper
        
        Args:
            headless: Si True, lance Chrome en mode headless
            output_dir: Dossier de sortie pour les fichiers
        """
        self.driver = None
        self.wait = None
        self.headless = headless
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.temp_dir = None
        
    def init_driver(self):
        """Initialise le driver Selenium avec un profil temporaire unique"""
        try:
            # Fermer toute session existante
            if self.driver:
                try:
                    self.driver.quit()
                except:
                    pass
                self.driver = None
            
            # Créer un répertoire temporaire unique pour cette session
            self.temp_dir = tempfile.mkdtemp(prefix="chrome_temp_")
            user_data_dir = os.path.join(self.temp_dir, f"profile_{uuid.uuid4().hex[:8]}")
            
            options = Options()
            
            # Utiliser un profil utilisateur temporaire unique
            options.add_argument(f"--user-data-dir={user_data_dir}")
            options.add_argument(f"--profile-directory=Profile_{uuid.uuid4().hex[:8]}")
            
            if self.headless:
                options.add_argument("--headless=new")  # Nouveau mode headless
                options.add_argument("--window-size=1920,1080")
            
            # Options pour éviter les conflits
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-gpu")
            options.add_argument("--disable-web-security")
            options.add_argument("--disable-features=VizDisplayCompositor")
            options.add_argument("--disable-extensions")
            
            # Options pour éviter la détection
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option('useAutomationExtension', False)
            
            # Préférences pour éviter les popups
            prefs = {
                "profile.default_content_setting_values.notifications": 2,
                "profile.default_content_settings.popups": 0,
                "profile.managed_default_content_settings.images": 1
            }
            options.add_experimental_option("prefs", prefs)
            
            # Tentative avec gestion d'erreur
            max_attempts = 3
            for attempt in range(max_attempts):
                try:
                    logger.info(f"Tentative {attempt + 1}/{max_attempts} d'initialisation du driver...")
                    
                    # Essayer d'abord avec Service
                    try:
                        from webdriver_manager.chrome import ChromeDriverManager
                        service = Service(ChromeDriverManager().install())
                        self.driver = webdriver.Chrome(service=service, options=options)
                    except:
                        # Fallback sans Service
                        self.driver = webdriver.Chrome(options=options)
                    
                    self.wait = WebDriverWait(self.driver, 10)
                    logger.info("✅ Driver Selenium initialisé avec succès")
                    return
                    
                except Exception as e:
                    logger.warning(f"Tentative {attempt + 1} échouée: {e}")
                    if attempt < max_attempts - 1:
                        time.sleep(2)
                        # Nettoyer le répertoire temporaire pour la prochaine tentative
                        if os.path.exists(user_data_dir):
                            try:
                                shutil.rmtree(user_data_dir)
                            except:
                                pass
                        # Créer un nouveau répertoire
                        user_data_dir = os.path.join(self.temp_dir, f"profile_{uuid.uuid4().hex[:8]}")
                    else:
                        raise Exception(f"Impossible d'initialiser le driver après {max_attempts} tentatives")
                    
        except Exception as e:
            logger.error(f"Erreur critique lors de l'initialisation: {e}")
            self.cleanup_temp_dir()
            raise
            
    def cleanup_temp_dir(self):
        """Nettoie le répertoire temporaire"""
        if self.temp_dir and os.path.exists(self.temp_dir):
            try:
                shutil.rmtree(self.temp_dir)
                logger.debug(f"Répertoire temporaire supprimé: {self.temp_dir}")
            except Exception as e:
                logger.warning(f"Impossible de supprimer le répertoire temporaire: {e}")
        
    def close_driver(self):
        """Ferme le driver Selenium et nettoie les ressources"""
        if self.driver:
            try:
                self.driver.quit()
                logger.info("Driver fermé")
            except Exception as e:
                logger.warning(f"Erreur lors de la fermeture du driver: {e}")
            finally:
                self.driver = None
                
        # Nettoyer le répertoire temporaire
        self.cleanup_temp_dir()
            
    def parse_certification_level(self, text: str) -> tuple:
        """
        Parse le niveau de certification et calcule les unités
        
        Args:
            text: Texte de certification (ex: "2x Platinum", "Gold")
            
        Returns:
            Tuple (niveau, multiplicateur, unités)
        """
        if not text:
            return "", 1, None
            
        text = text.strip().lower()
        
        # Extraction du multiplicateur
        mult = 1
        if "x" in text:
            try:
                mult = int(text.split("x")[0].strip())
                text = text.split("x")[1].strip()
            except (ValueError, IndexError):
                pass
                
        # Détermination du niveau
        if "diamond" in text:
            level = "Diamond"
            base = BASE_UNITS["diamond"]
        elif "platinum" in text or "platine" in text:
            level = "Platinum"
            base = BASE_UNITS["platinum"]
        elif "gold" in text or "or" in text:
            level = "Gold"
            base = BASE_UNITS["gold"]
        else:
            return text.title(), mult, None
            
        # Calcul des unités
        units = base * mult
        
        # Format du niveau avec multiplicateur
        if mult > 1:
            level = f"{mult}x {level}"
            
        return level, mult, units
        
    def click_load_more(self) -> int:
        """
        Clique sur le bouton LOAD MORE jusqu'à ce qu'il n'y ait plus de résultats
        
        Returns:
            Nombre de clics effectués
        """
        clicks = 0
        consecutive_fails = 0
        max_consecutive_fails = 3
        
        while consecutive_fails < max_consecutive_fails:
            try:
                current_count = len(self.driver.find_elements(By.CSS_SELECTOR, "tr.table_award_row"))
                
                # Cherche le bouton
                load_more = self.driver.find_element(By.ID, "loadMore")
                
                # Vérifie si le bouton est visible et actif
                if not load_more.is_displayed() or load_more.get_attribute("style") == "display: none;":
                    break
                    
                # Scroll et clique
                self.driver.execute_script("arguments[0].scrollIntoView(true);", load_more)
                time.sleep(0.5)
                self.driver.execute_script("arguments[0].click();", load_more)
                
                clicks += 1
                consecutive_fails = 0
                
                # Attente du chargement
                time.sleep(2.5)
                
                # Vérifie si nouvelles lignes
                new_count = len(self.driver.find_elements(By.CSS_SELECTOR, "tr.table_award_row"))
                if new_count == current_count:
                    time.sleep(2)
                    new_count = len(self.driver.find_elements(By.CSS_SELECTOR, "tr.table_award_row"))
                    if new_count == current_count:
                        consecutive_fails += 1
                else:
                    logger.info(f"Chargé {new_count - current_count} lignes supplémentaires (total: {new_count})")
                
            except NoSuchElementException:
                consecutive_fails += 1
            except Exception as e:
                logger.debug(f"Erreur lors du clic: {e}")
                consecutive_fails += 1
                
        logger.info(f"Fin du chargement après {clicks} clics")
        return clicks
        
    def click_and_extract_details(self, row_element, row_id: str, base_cert_date: str = "") -> List[Dict]:
        """
        Clique sur MORE DETAILS et extrait les données détaillées
        
        Args:
            row_element: Element Selenium de la ligne
            row_id: ID de la ligne
            base_cert_date: Date de certification de base extraite du tableau principal
            
        Returns:
            Liste des certifications historiques
        """
        history = []
        
        try:
            # Trouve et clique sur le bouton MORE DETAILS
            try:
                more_button = row_element.find_element(
                    By.XPATH, 
                    ".//a[contains(text(), 'MORE DETAILS')]"
                )
                self.driver.execute_script("arguments[0].click();", more_button)
            except:
                if row_id:
                    self.driver.execute_script(f"showDefaultDetail('{row_id}','DI');")
            
            # Attente courte pour le chargement
            time.sleep(0.5)
            
            # Cherche la div des détails
            detail_selector = f"div#recent_{row_id}_detail, div.more_detail_div"
            
            # Attente du contenu
            for _ in range(int(MAX_WAIT_DETAIL / POLL_INTERVAL)):
                try:
                    detail_elements = self.driver.find_elements(By.CSS_SELECTOR, detail_selector)
                    
                    for detail_el in detail_elements:
                        if detail_el.is_displayed():
                            # Extraction des lignes de la table
                            rows = detail_el.find_elements(By.CSS_SELECTOR, "tr.content_recent_table")
                            
                            if rows:
                                for row in rows:
                                    cells = row.find_elements(By.TAG_NAME, "td")
                                    
                                    if len(cells) >= 2:
                                        if len(cells) == 2:
                                            # Format simple: Date | Certification
                                            date_text = cells[0].text.strip()
                                            cert_text = cells[1].text.strip()
                                            
                                            # Parse certification et date
                                            if "|" in cert_text:
                                                parts = cert_text.split("|")
                                                cert_level = parts[0].strip()
                                                cert_date = parts[1].strip() if len(parts) > 1 else base_cert_date
                                            else:
                                                cert_level = cert_text
                                                # Utiliser la date de base si pas de date dans le détail
                                                cert_date = base_cert_date
                                            
                                            level, mult, units = self.parse_certification_level(cert_level)
                                            
                                            history.append({
                                                "release_date": date_text,
                                                "certification_level": level,
                                                "certification_date": cert_date,
                                                "units": units if units else ""
                                            })
                                            
                                        elif len(cells) >= 6:
                                            # Format complet avec toutes les colonnes
                                            release_date = cells[0].text.strip()
                                            prev_cert_text = cells[1].text.strip()
                                            category = cells[2].text.strip()
                                            type_field = cells[3].text.strip()
                                            cert_units = cells[4].text.strip()
                                            genre = cells[5].text.strip() if len(cells) > 5 else ""
                                            
                                            # Parse certification et date
                                            if "|" in prev_cert_text:
                                                cert_level, cert_date = prev_cert_text.split("|", 1)
                                                cert_level = cert_level.strip()
                                                cert_date = cert_date.strip()
                                            else:
                                                cert_level = prev_cert_text
                                                # Utiliser la date de base si pas de date dans le détail
                                                cert_date = base_cert_date
                                            
                                            level, mult, units = self.parse_certification_level(cert_level)
                                            if not units and cert_units:
                                                _, _, units = self.parse_certification_level(cert_units)
                                            
                                            history.append({
                                                "release_date": release_date,
                                                "certification_level": level,
                                                "certification_date": cert_date,
                                                "category": category,
                                                "type": type_field,
                                                "certified_units": cert_units,
                                                "units": units if units else "",
                                                "genre": genre
                                            })
                                
                                return history
                                
                except Exception:
                    pass
                    
                time.sleep(POLL_INTERVAL)
                
        except Exception as e:
            logger.debug(f"Erreur extraction détails: {e}")
            
        return [{"note": "No details available"}] if not history else history
        
    def extract_row_data(self, row_element, default_artist: str = "") -> Dict:
        """
        Extrait les données principales d'une ligne du tableau RIAA
        
        Args:
            row_element: Element Selenium de la ligne
            default_artist: Artiste par défaut si non trouvé
            
        Returns:
            Dictionnaire avec les données de base incluant la date de certification
        """
        data = {
            "artist": default_artist,
            "title": "",
            "certification_date": "",
            "label": "",
            "format": "",
            "award_level": "",
            "units": None
        }
        
        try:
            row_html = row_element.get_attribute("outerHTML")
            soup = BeautifulSoup(row_html, "html.parser")
            
            # Artiste
            artist_cell = soup.select_one("td.artists_cell")
            if artist_cell:
                data["artist"] = artist_cell.get_text(strip=True) or default_artist
                
            # Titre, Date de certification et Label
            other_cells = soup.select("td.others_cell")
            if len(other_cells) >= 3:
                data["title"] = other_cells[0].get_text(strip=True)
                # Extraction correcte de la date de certification
                data["certification_date"] = other_cells[1].get_text(strip=True)
                data["label"] = other_cells[2].get_text(strip=True)
                
                logger.debug(f"Extrait: {data['title']} - Date: {data['certification_date']}")
                
            # Format
            format_cell = soup.select_one("td.format_cell")
            if format_cell:
                format_text = format_cell.get_text(strip=True)
                data["format"] = format_text.replace("MORE DETAILS", "").strip()
                
            # Niveau de certification depuis l'image
            award_img = soup.select_one("img.award")
            if award_img and award_img.get("src"):
                src = award_img["src"]
                if "gold" in src.lower():
                    data["award_level"] = "Gold"
                    data["units"] = BASE_UNITS["gold"]
                elif "platinum" in src.lower():
                    data["award_level"] = "Platinum"
                    data["units"] = BASE_UNITS["platinum"]
                elif "diamond" in src.lower():
                    data["award_level"] = "Diamond"
                    data["units"] = BASE_UNITS["diamond"]
                    
        except Exception as e:
            logger.error(f"Erreur extraction données: {e}")
            
        return data
        
    def scrape_by_date_range(self, start_date: str, end_date: str, 
                            date_option: str = "release", get_details: bool = False) -> List[Dict]:
        """
        Scrape les certifications par plage de dates
        
        Args:
            start_date: Date de début (format YYYY-MM-DD ou MM/DD/YYYY)
            end_date: Date de fin (format YYYY-MM-DD ou MM/DD/YYYY)
            date_option: 'release' ou 'certification'
            get_details: Si True, clique sur MORE DETAILS pour chaque certification
            
        Returns:
            Liste des certifications trouvées
        """
        results = []
        
        # Conversion des dates au format YYYY-MM-DD si nécessaire
        if "/" in start_date:
            parts = start_date.split("/")
            start_date = f"{parts[2]}-{parts[0]:0>2}-{parts[1]:0>2}"
        if "/" in end_date:
            parts = end_date.split("/")
            end_date = f"{parts[2]}-{parts[0]:0>2}-{parts[1]:0>2}"
        
        # Construction de l'URL
        url = (f"https://www.riaa.com/gold-platinum/?tab_active=default-award&ar=&ti=&lab=&genre=&format="
               f"&date_option={date_option}"
               f"&from={start_date}"
               f"&to={end_date}"
               f'&award=&type=&category='
               f"&adv=SEARCH#search_section")
        
        logger.info(f"Scraping par dates: {start_date} à {end_date}")
        logger.info(f"Mode: {date_option} | Détails: {'Oui' if get_details else 'Non'}")
        
        self.driver.get(url)
        time.sleep(3)
        
        logger.info("Chargement de tous les résultats...")
        clicks = self.click_load_more()
        logger.info(f"Cliqué {clicks} fois sur LOAD MORE")
        
        time.sleep(2)
        
        rows = self.driver.find_elements(By.CSS_SELECTOR, "tr.table_award_row")
        logger.info(f"Trouvé {len(rows)} certifications pour la période")
        
        if not rows:
            logger.warning("Aucune certification trouvée pour cette période")
            return results
        
        for idx, row in enumerate(rows, 1):
            try:
                # Extraction des données de base
                row_data = self.extract_row_data(row)
                
                if get_details:
                    row_id = row.get_attribute("id")
                    if row_id and "default_" in row_id:
                        row_id = row_id.replace("default_", "")
                    
                    if row_id:
                        if idx % 10 == 0 or idx == 1:
                            logger.info(f"[{idx}/{len(rows)}] Traitement avec détails...")
                        
                        # Passer la date de certification de base aux détails
                        details = self.click_and_extract_details(row, row_id, row_data.get("certification_date", ""))
                        row_data["history"] = details
                
                results.append(row_data)
                
                if not get_details and idx % 50 == 0:
                    logger.info(f"Traité {idx}/{len(rows)} certifications")
                    
            except Exception as e:
                logger.error(f"Erreur ligne {idx}: {e}")
                continue
                
        logger.info(f"✅ Terminé: {len(results)} certifications extraites")
        return results
        
    def save_to_csv(self, data: List[Dict], filename: str):
        """
        Sauvegarde les données dans un fichier CSV avec gestion correcte des dates
        
        Args:
            data: Liste des données à sauvegarder
            filename: Nom du fichier de sortie
        """
        if not data:
            logger.warning("Aucune donnée à sauvegarder")
            return
            
        filepath = self.output_dir / filename
        
        rows = []
        for item in data:
            base_data = {k: v for k, v in item.items() if k != "history"}
            
            # Si pas d'historique ou si l'historique est vide/invalide
            if not item.get("history") or (len(item.get("history", [])) == 1 and item["history"][0].get("note")):
                # Utiliser les données de base
                row = {
                    "artist": base_data.get("artist", ""),
                    "title": base_data.get("title", ""),
                    "certification_date": base_data.get("certification_date", ""),
                    "release_date": base_data.get("release_date", ""),
                    "certification_level": base_data.get("award_level", ""),
                    "units": base_data.get("units", ""),
                    "label": base_data.get("label", ""),
                    "format": base_data.get("format", ""),
                    "award_level": base_data.get("award_level", ""),
                    "category": "",
                    "type": "",
                    "genre": "",
                    "certified_units": ""
                }
                rows.append(row)
            else:
                # Traiter l'historique détaillé
                for hist in item["history"]:
                    if not hist.get("note"):  # Skip les entrées vides
                        row = base_data.copy()
                        
                        # Ne pas écraser la date de certification si elle est vide dans l'historique
                        cert_date_from_hist = hist.get("certification_date", "")
                        cert_date_from_base = base_data.get("certification_date", "")
                        
                        # Utiliser la date de l'historique si elle existe, sinon la date de base
                        final_cert_date = cert_date_from_hist if cert_date_from_hist else cert_date_from_base
                        
                        # Fusionner les données
                        row.update({
                            "artist": base_data.get("artist", ""),
                            "title": base_data.get("title", ""),
                            "label": base_data.get("label", ""),
                            "format": base_data.get("format", ""),
                            "certification_date": final_cert_date,
                            "release_date": hist.get("release_date", base_data.get("release_date", "")),
                            "certification_level": hist.get("certification_level", base_data.get("award_level", "")),
                            "units": hist.get("units", base_data.get("units", "")),
                            "category": hist.get("category", ""),
                            "type": hist.get("type", ""),
                            "genre": hist.get("genre", ""),
                            "certified_units": hist.get("certified_units", ""),
                            "award_level": hist.get("certification_level", base_data.get("award_level", ""))
                        })
                        
                        rows.append(row)
                    
        # Ordre des colonnes
        columns = [
            "artist", "title", "certification_date", "release_date",
            "certification_level", "units", "label", "format", "award_level",
            "category", "type", "genre", "certified_units"
        ]
        
        # Création du DataFrame et sauvegarde
        df = pd.DataFrame(rows)
        
        # S'assurer que toutes les colonnes existent
        for col in columns:
            if col not in df.columns:
                df[col] = ""
                
        # Réorganiser les colonnes
        df = df[columns]
        
        # Sauvegarde
        df.to_csv(filepath, index=False, encoding='utf-8-sig')
        logger.info(f"✅ Données sauvegardées: {filepath} ({len(df)} lignes)")


def setup_logging(verbose: bool = False):
    """Configure le système de logging"""
    level = logging.DEBUG if verbose else logging.INFO
    
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%H:%M:%S'
    )
    
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    
    logger.setLevel(level)
    logger.addHandler(console)
    
    # Logger pour Selenium en mode warning seulement
    logging.getLogger('selenium').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)


def main():
    """Point d'entrée principal avec gestion améliorée des erreurs"""
    
    # Configuration du logging avant tout
    setup_logging(False)
    
    scraper = None
    
    try:
        # Mode interactif simple pour le test
        print("\n=== Scraper RIAA ===")
        print("1. Rechercher par artiste")
        print("2. Rechercher par dates")
        print("3. Quitter")
        
        choice = input("\nVotre choix: ").strip()
        
        if choice == "1":
            artist = input("Nom de l'artiste: ").strip()
            scraper = RIAAScraper(headless=False, output_dir="data")
            scraper.init_driver()
            
            try:
                results = scraper.scrape_by_artist(artist)
                output = f"riaa_{artist.replace(' ', '_')}.csv"
                scraper.save_to_csv(results, output)
            finally:
                scraper.close_driver()
            
        elif choice == "2":
            start = input("Date début (YYYY-MM-DD): ").strip()
            end = input("Date fin (YYYY-MM-DD): ").strip()
            details = input("Récupérer les détails ? (oui/non, défaut: non): ").strip().lower()
            get_details = details == "oui"
            
            scraper = RIAAScraper(headless=False, output_dir="data")
            scraper.init_driver()
            
            try:
                results = scraper.scrape_by_date_range(start, end, "certification", get_details)
                scraper.save_to_csv(results, f"riaa_dates_{datetime.now():%Y%m%d}.csv")
            finally:
                scraper.close_driver()
                
        elif choice == "3":
            print("Au revoir!")
            sys.exit(0)
                
    except KeyboardInterrupt:
        print("\n\n⚠️ Interruption utilisateur")
        if scraper and scraper.driver:
            scraper.close_driver()
    except Exception as e:
        logger.error(f"❌ Erreur: {e}")
        if scraper and scraper.driver:
            scraper.close_driver()
        sys.exit(1)


if __name__ == "__main__":
    main()