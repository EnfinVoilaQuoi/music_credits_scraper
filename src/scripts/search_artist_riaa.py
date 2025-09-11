#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scraper RIAA optimisé - Version rapide avec détection correcte des éléments
Compatible avec la structure actuelle du site RIAA (2024)
"""

import re
import time
import csv
import json
from urllib.parse import quote_plus
from typing import List, Dict, Tuple, Optional
from datetime import datetime, timedelta
import logging
from pathlib import Path

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    StaleElementReferenceException,
)
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# Configuration
HEADLESS = False
WAIT_SECONDS = 10
MAX_WAIT_DETAIL = 3.0  # Réduit à 3 secondes
POLL_INTERVAL = 0.2    # Polling plus rapide
BASE_UNITS = {"gold": 500_000, "platinum": 1_000_000, "diamond": 10_000_000}

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class RIAAScraper:
    """Scraper optimisé pour les certifications RIAA"""
    
    def __init__(self, headless=HEADLESS, output_dir=None):
        self.headless = headless
        self.driver = None
        self.wait = None
        
        # Définir le répertoire de sortie
        if output_dir:
            self.output_dir = Path(output_dir)
        else:
            # Par défaut dans data/certifications/riaa
            self.output_dir = Path(__file__).parent.parent.parent / 'data' / 'certifications' / 'riaa'
        
        # Créer le répertoire s'il n'existe pas
        self.output_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Fichiers seront sauvegardés dans: {self.output_dir}")
        
    def init_driver(self):
        """Initialise le driver Selenium"""
        options = Options()
        if self.headless:
            options.add_argument("--headless=new")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_experimental_option("excludeSwitches", ["enable-logging"])
        
        # Options pour accélérer le chargement
        options.add_argument("--disable-images")  # Désactive les images pour aller plus vite
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.page_load_strategy = 'eager'  # Ne pas attendre le chargement complet
        
        self.driver = webdriver.Chrome(options=options)
        self.wait = WebDriverWait(self.driver, WAIT_SECONDS)
        
    def close_driver(self):
        """Ferme le driver"""
        if self.driver:
            self.driver.quit()
            
    def parse_certification_level(self, text: str) -> Tuple[str, Optional[int], Optional[int]]:
        """Parse le niveau de certification et calcule les unités"""
        if not text:
            return ("", None, None)
            
        text = text.strip()
        
        # Recherche multiplicateur (ex: "2x Platinum")
        m = re.search(r"(\d+)\s*[xX]\s*([A-Za-z]+)", text, re.I)
        if m:
            mult = int(m.group(1))
            level = m.group(2).lower()
            if level in BASE_UNITS:
                units = BASE_UNITS[level] * mult
                return (f"{mult}x {level.title()}", mult, units)
                
        # Recherche simple (ex: "Gold", "Platinum")
        for level in BASE_UNITS:
            if level.lower() in text.lower():
                return (level.title(), 1, BASE_UNITS[level])
                
        # Extraction directe des millions
        m_units = re.search(r"(\d+(?:\.\d+)?)\s*Million", text, re.I)
        if m_units:
            units = int(float(m_units.group(1)) * 1_000_000)
            return (text, None, units)
            
        return (text, None, None)
        
    def click_and_extract_details(self, row_element, row_id: str) -> List[Dict]:
        """Clique sur MORE DETAILS et extrait immédiatement les données"""
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
                # Fallback avec JavaScript
                if row_id:
                    self.driver.execute_script(f"showDefaultDetail('{row_id}','DI');")
            
            # Attente très courte pour le chargement
            time.sleep(0.5)
            
            # Cherche directement la div des détails qui apparaît
            detail_selector = f"div#recent_{row_id}_detail, div.more_detail_div"
            
            # Attente rapide du contenu
            for _ in range(int(MAX_WAIT_DETAIL / POLL_INTERVAL)):
                try:
                    detail_elements = self.driver.find_elements(By.CSS_SELECTOR, detail_selector)
                    
                    for detail_el in detail_elements:
                        if detail_el.is_displayed():
                            # Extraction directe des lignes de la table
                            rows = detail_el.find_elements(By.CSS_SELECTOR, "tr.content_recent_table")
                            
                            if rows:
                                for row in rows:
                                    cells = row.find_elements(By.TAG_NAME, "td")
                                    
                                    if len(cells) >= 2:
                                        # Parse selon le nombre de colonnes
                                        if len(cells) == 2:
                                            # Format simple: Date | Certification
                                            date_text = cells[0].text.strip()
                                            cert_text = cells[1].text.strip()
                                            
                                            # Parse la certification
                                            parts = cert_text.split("|") if "|" in cert_text else [cert_text]
                                            cert_level = parts[0].strip()
                                            cert_date = parts[1].strip() if len(parts) > 1 else ""
                                            
                                            level, mult, units = self.parse_certification_level(cert_level)
                                            
                                            history.append({
                                                "release_date": date_text,
                                                "certification_level": level,
                                                "certification_date": cert_date,
                                                "units": units if units else ""
                                            })
                                            
                                        elif len(cells) >= 6:
                                            # Format complet
                                            release_date = cells[0].text.strip()
                                            prev_cert = cells[1].text.strip()
                                            category = cells[2].text.strip()
                                            type_field = cells[3].text.strip()
                                            cert_units = cells[4].text.strip()
                                            genre = cells[5].text.strip() if len(cells) > 5 else ""
                                            
                                            # Parse previous certifications
                                            if "|" in prev_cert:
                                                cert_level, cert_date = prev_cert.split("|", 1)
                                                cert_level = cert_level.strip()
                                                cert_date = cert_date.strip()
                                            else:
                                                cert_level = prev_cert
                                                cert_date = ""
                                            
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
                                
                                # Succès, on sort
                                return history
                                
                except Exception:
                    pass
                    
                time.sleep(POLL_INTERVAL)
                
        except Exception as e:
            logger.debug(f"Erreur extraction détails: {e}")
            
        # Si pas de détails trouvés, retourne une entrée vide
        return [{"note": "No details available"}] if not history else history
        
    def click_load_more(self) -> int:
        """Clique sur LOAD MORE jusqu'à charger tous les résultats"""
        clicks = 0
        
        while True:
            try:
                # Cherche le bouton LOAD MORE
                load_more = self.driver.find_element(By.ID, "loadmore")
                
                # Vérifie si visible
                if not load_more.is_displayed():
                    break
                    
                # Compte actuel
                current_count = len(self.driver.find_elements(By.CSS_SELECTOR, "tr.table_award_row"))
                
                # Clique
                self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", load_more)
                time.sleep(0.2)
                self.driver.execute_script("arguments[0].click();", load_more)
                clicks += 1
                
                # Attente courte
                time.sleep(1.5)
                
                # Vérifie si nouvelles lignes
                new_count = len(self.driver.find_elements(By.CSS_SELECTOR, "tr.table_award_row"))
                if new_count == current_count:
                    break
                    
                logger.info(f"Chargé {new_count - current_count} lignes supplémentaires (total: {new_count})")
                
            except NoSuchElementException:
                break
            except Exception as e:
                logger.debug(f"Fin du chargement: {e}")
                break
                
        return clicks
        
    def scrape_by_artist(self, artist_name: str) -> List[Dict]:
        """Scrape toutes les certifications d'un artiste"""
        results = []
        
        # Construction de l'URL
        artist_query = quote_plus(artist_name)
        url = f"https://www.riaa.com/gold-platinum/?tab_active=default-award&ar={artist_query}"
        
        logger.info(f"Scraping artiste: {artist_name}")
        logger.info(f"URL: {url}")
        
        self.driver.get(url)
        time.sleep(2)
        
        # Charge tous les résultats
        logger.info("Chargement de tous les résultats...")
        self.click_load_more()
        
        # Récupère toutes les lignes
        rows = self.driver.find_elements(By.CSS_SELECTOR, "tr.table_award_row")
        logger.info(f"Trouvé {len(rows)} certifications")
        
        for idx, row in enumerate(rows, 1):
            try:
                # Extraction des données principales
                row_data = self.extract_row_data(row, artist_name)
                
                # Récupération de l'ID
                row_id = row.get_attribute("id")
                if row_id and "default_" in row_id:
                    row_id = row_id.replace("default_", "")
                    
                # Log de progression
                logger.info(f"[{idx}/{len(rows)}] {row_data.get('title', 'Unknown')}")
                
                # Extraction des détails (rapide maintenant)
                if row_id:
                    details = self.click_and_extract_details(row, row_id)
                    row_data["history"] = details
                    if details and not details[0].get("note"):
                        logger.debug(f"  -> {len(details)} certification(s)")
                        
                results.append(row_data)
                
            except Exception as e:
                logger.error(f"Erreur ligne {idx}: {e}")
                continue
                
        return results
        
    def scrape_by_date_range(self, start_date: str, end_date: str, 
                            date_option: str = "release") -> List[Dict]:
        """
        Scrape les certifications par plage de dates
        
        Args:
            start_date: Date de début (format YYYY-MM-DD ou MM/DD/YYYY)
            end_date: Date de fin (format YYYY-MM-DD ou MM/DD/YYYY)
            date_option: 'release' ou 'certification'
        """
        results = []
        
        # Conversion des dates au format YYYY-MM-DD si nécessaire
        if "/" in start_date:
            # Conversion MM/DD/YYYY vers YYYY-MM-DD
            parts = start_date.split("/")
            start_date = f"{parts[2]}-{parts[0]:0>2}-{parts[1]:0>2}"
        if "/" in end_date:
            parts = end_date.split("/")
            end_date = f"{parts[2]}-{parts[0]:0>2}-{parts[1]:0>2}"
        
        # Construction de l'URL avec les dates au format RIAA
        url = (f"https://www.riaa.com/gold-platinum/?tab_active=default-award&ar=&ti=&lab=&genre=&format="
               f"&date_option={date_option}"
               f"&from={start_date}"
               f"&to={end_date}"
               f'&award=&type=&category='
               f"&adv=SEARCH#search_section")
        
        logger.info(f"Scraping par dates: {start_date} à {end_date}")
        logger.info(f"URL: {url}")
        
        self.driver.get(url)
        time.sleep(2)
        
        # Charge tous les résultats
        logger.info("Chargement de tous les résultats...")
        clicks = self.click_load_more()
        logger.info(f"Cliqué {clicks} fois sur LOAD MORE")
        
        # Récupère toutes les lignes
        rows = self.driver.find_elements(By.CSS_SELECTOR, "tr.table_award_row")
        logger.info(f"Trouvé {len(rows)} certifications pour la période")
        
        # Mode batch : on traite sans détails pour aller plus vite
        for idx, row in enumerate(rows, 1):
            try:
                row_data = self.extract_row_data(row)
                results.append(row_data)
                
                if idx % 50 == 0:
                    logger.info(f"Traité {idx}/{len(rows)} certifications")
                    
            except Exception as e:
                logger.error(f"Erreur ligne {idx}: {e}")
                continue
                
        return results
        
    def extract_row_data(self, row_element, default_artist: str = "") -> Dict:
        """Extrait les données principales d'une ligne"""
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
            # Parse avec BeautifulSoup
            row_html = row_element.get_attribute("outerHTML")
            soup = BeautifulSoup(row_html, "html.parser")
            
            # Artiste
            artist_cell = soup.select_one("td.artists_cell")
            if artist_cell:
                data["artist"] = artist_cell.get_text(strip=True) or default_artist
                
            # Titre et autres infos
            other_cells = soup.select("td.others_cell")
            if len(other_cells) >= 3:
                data["title"] = other_cells[0].get_text(strip=True)
                data["certification_date"] = other_cells[1].get_text(strip=True)
                data["label"] = other_cells[2].get_text(strip=True)
                
            # Format
            format_cell = soup.select_one("td.format_cell")
            if format_cell:
                format_text = format_cell.get_text(strip=True)
                data["format"] = format_text.replace("MORE DETAILS", "").strip()
                
            # Niveau de certification
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
        
    def save_to_csv(self, data: List[Dict], filename: str):
        """Sauvegarde les données dans un CSV"""
        if not data:
            logger.warning("Aucune donnée à sauvegarder")
            return
            
        # Chemin complet du fichier
        filepath = self.output_dir / filename
        
        # Prépare les données pour l'export
        rows = []
        for item in data:
            base_data = {k: v for k, v in item.items() if k != "history"}
            
            # Si pas d'historique, ajoute une ligne simple
            if not item.get("history"):
                rows.append(base_data)
            else:
                # Fusionne les données principales avec l'historique
                for hist in item["history"]:
                    if not hist.get("note"):  # Skip les entrées vides
                        row = base_data.copy()
                        row.update(hist)
                        rows.append(row)
                    else:
                        # Même les entrées sans détails sont gardées
                        rows.append(base_data)
                        break
                    
        # Détermine les colonnes
        all_keys = set()
        for row in rows:
            all_keys.update(row.keys())
            
        # Ordre des colonnes
        ordered_keys = ["artist", "title", "certification_date", "release_date", 
                       "certification_level", "units", "label", "format", 
                       "award_level", "category", "type", "genre", "certified_units"]
        
        # Ajoute les clés manquantes
        for key in all_keys:
            if key not in ordered_keys:
                ordered_keys.append(key)
                
        # Filtre les clés existantes
        fieldnames = [k for k in ordered_keys if k in all_keys]
        
        # Écrit le CSV
        with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
            
        logger.info(f"✅ Sauvegardé {len(rows)} lignes dans: {filepath}")
        
    def update_database(self, months_back: int = 1):
        """
        Met à jour la base de données avec les certifications récentes
        
        Args:
            months_back: Nombre de mois à récupérer
        """
        # Calcul des dates
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30 * months_back)
        
        # Format YYYY-MM-DD pour l'URL RIAA
        start_str = start_date.strftime("%Y-%m-%d")
        end_str = end_date.strftime("%Y-%m-%d")
        
        logger.info(f"Mise à jour des certifications du {start_str} au {end_str}")
        
        # Scrape par date de certification
        results = self.scrape_by_date_range(start_str, end_str, "certification")
        
        # Sauvegarde
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"riaa_update_{timestamp}.csv"
        self.save_to_csv(results, filename)
        
        return results


def main():
    """Fonction principale avec exemples d'utilisation"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Scraper RIAA pour certifications musicales")
    parser.add_argument("--artist", type=str, help="Nom de l'artiste à rechercher")
    parser.add_argument("--update", type=int, help="Met à jour les X derniers mois")
    parser.add_argument("--start-date", type=str, help="Date de début (YYYY-MM-DD)")
    parser.add_argument("--end-date", type=str, help="Date de fin (YYYY-MM-DD)")
    parser.add_argument("--output", type=str, help="Fichier de sortie")
    parser.add_argument("--output-dir", type=str, help="Répertoire de sortie")
    parser.add_argument("--headless", action="store_true", help="Mode headless")
    
    args = parser.parse_args()
    
    # Initialise le scraper
    scraper = RIAAScraper(headless=args.headless, output_dir=args.output_dir)
    
    try:
        if args.artist:
            # Recherche par artiste
            scraper.init_driver()
            results = scraper.scrape_by_artist(args.artist)
            output = args.output or f"riaa_{args.artist.replace(' ', '_')}.csv"
            scraper.save_to_csv(results, output)
            scraper.close_driver()
            
        elif args.update:
            # Mise à jour mensuelle
            scraper.init_driver()
            results = scraper.update_database(args.update)
            scraper.close_driver()
            
        elif args.start_date and args.end_date:
            # Recherche par dates
            scraper.init_driver()
            results = scraper.scrape_by_date_range(args.start_date, args.end_date)
            output = args.output or f"riaa_dates_{datetime.now():%Y%m%d}.csv"
            scraper.save_to_csv(results, output)
            scraper.close_driver()
            
        else:
            # Mode interactif
            print("\n=== Scraper RIAA ===")
            print("1. Rechercher par artiste")
            print("2. Rechercher par dates")
            print("3. Mise à jour mensuelle")
            print("4. Scraper période complète (2017-09 à aujourd'hui)")
            
            choice = input("\nVotre choix: ").strip()
            
            if choice == "1":
                artist = input("Nom de l'artiste: ").strip()
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
                scraper.init_driver()
                try:
                    results = scraper.scrape_by_date_range(start, end)
                    scraper.save_to_csv(results, f"riaa_dates_{datetime.now():%Y%m%d}.csv")
                finally:
                    scraper.close_driver()
                
            elif choice == "3":
                months = int(input("Nombre de mois à récupérer: ") or "1")
                scraper.init_driver()
                try:
                    results = scraper.update_database(months)
                finally:
                    scraper.close_driver()
                    
            elif choice == "4":
                print("\n⚠️  ATTENTION: Cela va scraper TOUTES les certifications depuis Sept 2017")
                print("Cela peut prendre plusieurs heures...")
                confirm = input("Confirmer (oui/non): ").strip().lower()
                
                if confirm == "oui":
                    scraper.init_driver()
                    try:
                        # Scrape par tranches de 2 mois pour éviter les timeouts
                        all_results = []
                        start = datetime(2017, 9, 1)
                        end = datetime.now()
                        
                        current = start
                        while current < end:
                            next_date = min(current + timedelta(days=60), end)
                            
                            logger.info(f"\n=== Période {current:%Y-%m-%d} à {next_date:%Y-%m-%d} ===")
                            results = scraper.scrape_by_date_range(
                                current.strftime("%Y-%m-%d"),
                                next_date.strftime("%Y-%m-%d"),
                                "certification"  # IMPORTANT: certification et non release
                            )
                            all_results.extend(results)
                            
                            # Sauvegarde intermédiaire
                            scraper.save_to_csv(all_results, f"riaa_complete_{datetime.now():%Y%m%d}.csv")
                            
                            current = next_date
                            time.sleep(5)  # Pause entre les requêtes
                            
                        logger.info(f"\n✅ Terminé! Total: {len(all_results)} certifications")
                        
                    finally:
                        scraper.close_driver()
                    
    except KeyboardInterrupt:
        print("\nInterruption utilisateur")
        if scraper.driver:
            scraper.close_driver()
    except Exception as e:
        logger.error(f"Erreur: {e}")
        if scraper.driver:
            scraper.close_driver()
        raise


if __name__ == "__main__":
    main()