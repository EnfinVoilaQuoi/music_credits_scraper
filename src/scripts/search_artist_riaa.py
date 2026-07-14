#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RIAA scraper robuste :
 - clique sur LOAD MORE RESULTS jusqu'à la fin
 - pour chaque ligne : scroll -> click MORE DETAILS -> poll jusqu'à avoir du HTML utile
 - écrit deux CSV : détaillé (une ligne par historique) et compact
"""

import re
import time
import csv
import json
from urllib.parse import quote_plus
from typing import List, Dict, Tuple, Optional, Set

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import (
    TimeoutException,
    ElementClickInterceptedException,
    NoSuchElementException,
    StaleElementReferenceException,
)
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ---------------- CONFIG ----------------
HEADLESS = False         # mettre False pour voir le navigateur pendant le debug
QUERY_PARAM = "ar"       # 'ar' ou 'se'
WAIT_SECONDS = 12        # attente générale
MAX_WAIT_DETAIL = 10.0   # temps max (s) pour attendre l'injection des details (par ligne)
POLL_INTERVAL = 0.5      # intervalle de polling (s)
RECLICK_ATTEMPTS = 3     # re-click attempts pour MORE DETAILS
# ----------------------------------------

BASE_UNITS = {"gold": 500_000, "platinum": 1_000_000, "diamond": 10_000_000}


def init_driver(headless: bool = HEADLESS, window_size: str = "1920,1080"):
    options = Options()
    if headless:
        try:
            options.add_argument("--headless=new")
        except Exception:
            options.add_argument("--headless")
    options.add_argument(f"--window-size={window_size}")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_experimental_option("excludeSwitches", ["enable-logging"])
    driver = webdriver.Chrome(options=options)
    return driver


def safe_text(el) -> str:
    if not el:
        return ""
    try:
        txt = el.get_text(" ", strip=True)
        txt = txt.replace("\xa0", " ").replace("\u200b", "").strip()
        return txt
    except Exception:
        try:
            txt = el.get_text(strip=True)
            return txt.replace("\xa0", " ").strip()
        except Exception:
            return ""


def parse_prev_cert_text(text: str) -> Tuple[str, Optional[int], Optional[int]]:
    if not text:
        return ("", None, None)
    s = text.replace("\u00a0", " ").strip()
    m = re.search(r"(\d+)\s*[xX]\s*([A-Za-z]+)", s)
    if m:
        mult = int(m.group(1))
        label = m.group(2).lower()
        if label in BASE_UNITS:
            return (f"{mult}x {label.title()}", mult, BASE_UNITS[label] * mult)
        return (f"{mult}x {label.title()}", mult, None)
    m2 = re.search(r"\b(Diamond|Platinum|Gold)\b", s, flags=re.I)
    if m2:
        label0 = m2.group(1).lower()
        return (label0.title(), 1, BASE_UNITS.get(label0))
    m3 = re.search(r"(\d+(?:[,\.\s]\d{3})*(?:\.\d+)?)\s*(Million|M)?", s, flags=re.I)
    if m3:
        num_str = m3.group(1).replace(",", "").replace(" ", "")
        has_million = bool(m3.group(2))
        try:
            if has_million:
                val = float(num_str) * 1_000_000
                return (f"{int(val)} units", None, int(val))
            val_int = int(float(num_str))
            return (f"{val_int} units", None, val_int)
        except Exception:
            pass
    m4 = re.search(r"(\d+(?:\.\d+)?)\s*Million", s, flags=re.I)
    if m4:
        try:
            val = float(m4.group(1)) * 1_000_000
            return (f"{int(val)} units", None, int(val))
        except:
            pass
    return (s, None, None)


def extract_numeric_id_from_onclick(onclick_value: str) -> str:
    if not onclick_value:
        return ""
    m = re.search(r"showDefaultDetail\('(\d+)'", onclick_value)
    if m:
        return m.group(1)
    m2 = re.search(r"(\d{4,})", onclick_value)
    return m2.group(1) if m2 else ""


def click_load_more_until_end(driver, wait: WebDriverWait):
    """
    Clique sur LOAD MORE RESULTS jusqu'à ce qu'il n'y ait plus de nouveaux éléments.
    Retourne True si au moins une fois cliqué/chargé.
    """
    clicked_any = False
    prev_count = len(driver.find_elements(By.CSS_SELECTOR, "tr.table_award_row"))
    while True:
        try:
            link = driver.find_element(By.CSS_SELECTOR, "a#loadmore.link-arrow-gnp, a.link-arrow-gnp#loadmore")
            # data-total may indicate none; attempt click until no new rows
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", link)
            time.sleep(0.15)
            driver.execute_script("arguments[0].click();", link)
            clicked_any = True
            # attendre apparition de nouvelles lignes
            total_wait = 0.0
            while total_wait < 8.0:
                time.sleep(0.5)
                total_wait += 0.5
                cur_count = len(driver.find_elements(By.CSS_SELECTOR, "tr.table_award_row"))
                if cur_count > prev_count:
                    prev_count = cur_count
                    break
            else:
                # aucune nouvelle ligne apparue -> sortir
                break
            # petite pause puis refaire la boucle (peut y avoir plusieurs pages)
            time.sleep(0.3)
        except NoSuchElementException:
            break
        except Exception:
            # s'il y a une erreur (ex: onclick different), on tente d'appeler la fonction JS loadMoreSearch
            try:
                driver.execute_script("if(typeof loadMoreSearch === 'function'){ loadMoreSearch(document.getElementById('loadmore')); }")
                clicked_any = True
                time.sleep(0.5)
                continue
            except Exception:
                break
    return clicked_any


def wait_for_detail_html_for_id(driver, numeric_id: str, timeout: float = MAX_WAIT_DETAIL) -> Optional[str]:
    """
    Poll jusqu'à `timeout` pour que div.award_more_detail[data-id=numeric_id]
    contienne du HTML cohérent (tr.content_recent_table ou texte long).
    Retourne outerHTML si trouvé, sinon None.
    """
    sel_attr = f"div.award_more_detail[data-id='{numeric_id}']"
    waited = 0.0
    while waited < timeout:
        try:
            div_el = driver.find_element(By.CSS_SELECTOR, sel_attr)
            inner = (div_el.get_attribute("innerHTML") or "").strip()
            if "content_recent_table" in inner or "Previous Certification" in inner or len(inner) > 80:
                return div_el.get_attribute("outerHTML")
        except Exception:
            pass
        time.sleep(POLL_INTERVAL)
        waited += POLL_INTERVAL
    return None


def process_row_element(driver, row_el, artist_name) -> List[Dict]:
    """
    Traite un élément Selenium tr.table_award_row :
      - lit les champs principaux
      - clique sur MORE DETAILS (retry)
      - attend detail html (poll)
      - parse history (liste de dicts)
    """
    # preparer row_soup local
    row_html = row_el.get_attribute("outerHTML")
    row_soup = BeautifulSoup(row_html, "html.parser")
    artist = safe_text(row_soup.select_one("td.artists_cell")) or artist_name
    others = row_soup.select("td.others_cell")
    tds_all = row_soup.find_all("td")

    title = safe_text(others[0]) if len(others) >= 1 else (safe_text(tds_all[2]) if len(tds_all) > 2 else "")
    cert_date_main = safe_text(others[1]) if len(others) >= 2 else (safe_text(tds_all[3]) if len(tds_all) > 3 else "")
    label = safe_text(others[2]) if len(others) >= 3 else (safe_text(tds_all[4]) if len(tds_all) > 4 else "")
    format_td = row_soup.select_one("td.format_cell")
    format_ = safe_text(format_td) if format_td else (safe_text(others[-1]) if others else (safe_text(tds_all[-1]) if tds_all else ""))
    format_ = format_.replace("MORE DETAILS", "").strip()

    award_img = row_soup.select_one("img.award")
    award = ""
    if award_img and award_img.has_attr("src"):
        fn = award_img["src"].split("/")[-1]
        award = fn.replace("_big.png", "").replace(".png", "")

    # identifier numeric_id
    row_id = row_el.get_attribute("id") or ""
    numeric_id = ""
    if row_id and "_" in row_id:
        numeric_id = row_id.split("_")[-1]
    elif row_id:
        numeric_id = row_id
    else:
        onclick_a = row_soup.select_one("a[onclick^='showDefaultDetail']")
        if onclick_a and onclick_a.has_attr("onclick"):
            numeric_id = extract_numeric_id_from_onclick(onclick_a["onclick"])

    # scroll into view
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", row_el)
    except Exception:
        pass
    time.sleep(0.08)

    # tenter plusieurs fois de cliquer et d'attendre le detail
    detail_html = None
    for attempt in range(RECLICK_ATTEMPTS):
        # essayer click on-line 'MORE DETAILS'
        clicked = False
        try:
            try:
                # essayer chercher le bouton dans le contexte de la row via XPath
                more_btn = row_el.find_element(By.XPATH, ".//a[contains(., 'MORE DETAILS') or contains(., 'More Details')]")
                driver.execute_script("arguments[0].click();", more_btn)
                clicked = True
            except Exception:
                # fallback : appeler la fonction JS showDefaultDetail si numeric_id connu
                if numeric_id:
                    try:
                        driver.execute_script(f"if(typeof showDefaultDetail === 'function') showDefaultDetail('{numeric_id}','DI');")
                        clicked = True
                    except Exception:
                        clicked = False
        except Exception:
            clicked = False

        # si on a cliqué, attendre le détail via polling
        if numeric_id:
            detail_html = wait_for_detail_html_for_id(driver, numeric_id, timeout=MAX_WAIT_DETAIL / RECLICK_ATTEMPTS)
            if detail_html:
                break
        # si pas numeric_id ou pas trouvé, attendre un court moment et retenter
        time.sleep(0.3)

    # fallback global parsing si detail_html None : chercher siblings dans page_source
    history = []
    if detail_html:
        detail_soup = BeautifulSoup(detail_html, "html.parser")
        content_rows = detail_soup.select("tr.content_recent_table")
        for cr in content_rows:
            tds = cr.find_all("td")
            if len(tds) >= 6:
                release_date = safe_text(tds[0])
                prev_cert_text = safe_text(tds[1])
                category = safe_text(tds[2])
                type_ = safe_text(tds[3])
                cert_units_text = safe_text(tds[4])
                genre = safe_text(tds[5])
                label_str, mult, computed_units = parse_prev_cert_text(prev_cert_text)
                if computed_units is None:
                    _, _, computed_units = parse_prev_cert_text(cert_units_text)
                history.append({
                    "Release Date": release_date,
                    "Previous Certification(s)": prev_cert_text,
                    "Category": category,
                    "Type": type_,
                    "Certified Units (text)": cert_units_text,
                    "Certified Units (computed)": computed_units if computed_units is not None else "",
                    "Parsed Label": label_str,
                    "Genre": genre,
                })
            elif len(tds) == 2:
                release_date = safe_text(tds[0])
                prev_cert_text = safe_text(tds[1])
                label_str, mult, computed_units = parse_prev_cert_text(prev_cert_text)
                history.append({
                    "Release Date": release_date,
                    "Previous Certification(s)": prev_cert_text,
                    "Category": "",
                    "Type": "",
                    "Certified Units (text)": "",
                    "Certified Units (computed)": computed_units if computed_units is not None else "",
                    "Parsed Label": label_str,
                    "Genre": "",
                })
            else:
                history.append({
                    "Release Date": "",
                    "Previous Certification(s)": cr.get_text(" | ", strip=True),
                    "Category": "",
                    "Type": "",
                    "Certified Units (text)": "",
                    "Certified Units (computed)": "",
                    "Parsed Label": "",
                    "Genre": "",
                })
    else:
        # fallback via global page source: trouver la ligne correspondante et ses siblings
        global_soup = BeautifulSoup(driver.page_source, "html.parser")
        global_row = None
        if numeric_id:
            # select by attribute id (safe for numeric-first ids)
            global_row = global_soup.select_one(f"tr.table_award_row[id='default_{numeric_id}'], tr.table_award_row[id='{numeric_id}']")
        if not global_row:
            # try match by title (best-effort)
            for r in global_soup.select("tr.table_award_row"):
                t = safe_text(r.select_one("td.others_cell"))
                if t and t.strip() == title.strip():
                    global_row = r
                    break
        if global_row:
            nxt = global_row.find_next_sibling()
            crs = []
            while nxt and nxt.name == "tr" and "content_recent_table" in nxt.get("class", []):
                crs.append(nxt)
                nxt = nxt.find_next_sibling()
            if crs:
                for cr in crs:
                    tds = cr.find_all("td")
                    if len(tds) >= 6:
                        release_date = safe_text(tds[0])
                        prev_cert_text = safe_text(tds[1])
                        category = safe_text(tds[2])
                        type_ = safe_text(tds[3])
                        cert_units_text = safe_text(tds[4])
                        genre = safe_text(tds[5])
                        label_str, mult, computed_units = parse_prev_cert_text(prev_cert_text)
                        if computed_units is None:
                            _, _, computed_units = parse_prev_cert_text(cert_units_text)
                        history.append({
                            "Release Date": release_date,
                            "Previous Certification(s)": prev_cert_text,
                            "Category": category,
                            "Type": type_,
                            "Certified Units (text)": cert_units_text,
                            "Certified Units (computed)": computed_units if computed_units is not None else "",
                            "Parsed Label": label_str,
                            "Genre": genre,
                        })
                    elif len(tds) == 2:
                        release_date = safe_text(tds[0])
                        prev_cert_text = safe_text(tds[1])
                        label_str, mult, computed_units = parse_prev_cert_text(prev_cert_text)
                        history.append({
                            "Release Date": release_date,
                            "Previous Certification(s)": prev_cert_text,
                            "Category": "",
                            "Type": "",
                            "Certified Units (text)": "",
                            "Certified Units (computed)": computed_units if computed_units is not None else "",
                            "Parsed Label": label_str,
                            "Genre": "",
                        })
        if not history:
            # No data found — add one empty record to keep the main line
            history.append({
                "Release Date": "",
                "Previous Certification(s)": "",
                "Category": "",
                "Type": "",
                "Certified Units (text)": "",
                "Certified Units (computed)": "",
                "Parsed Label": "",
                "Genre": "",
            })
            # debug log left to caller
    # Prepare result rows (attach main fields)
    result_rows = []
    for h in history:
        result_rows.append({
            "Artist": artist,
            "Title": title,
            "Certification Date (Main)": cert_date_main,
            "Label": label,
            "Format": format_,
            "Level": award,
            "Release Date": h.get("Release Date", ""),
            "Previous Certification(s)": h.get("Previous Certification(s)", ""),
            "Category": h.get("Category", ""),
            "Type": h.get("Type", ""),
            "Certified Units (text)": h.get("Certified Units (text)", ""),
            "Certified Units (computed)": h.get("Certified Units (computed)", ""),
            "Parsed Label": h.get("Parsed Label", ""),
            "Genre": h.get("Genre", ""),
        })
    return result_rows


def fetch_certifications(artist_name: str,
                         output_csv_detailed: str = None,
                         output_csv_compact: str = None,
                         query_param: str = QUERY_PARAM) -> List[Dict]:
    driver = init_driver(headless=HEADLESS)
    wait = WebDriverWait(driver, WAIT_SECONDS)
    artist_query = quote_plus(artist_name)
    url = f"https://www.riaa.com/gold-platinum/?tab_active=default-award&{query_param}={artist_query}"

    print(f"Ouverture de l'URL: {url}")
    driver.get(url)

    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "tr.table_award_row")))
    except TimeoutException:
        print("Aucune ligne trouvée.")
        driver.quit()
        return []

    # First ensure we load all pages by clicking load more repeatedly
    print("Cliquage sur LOAD MORE RESULTS (si présent) pour charger toutes les pages...")
    click_load_more_until_end(driver, wait)
    # small pause to let final injection finish
    time.sleep(0.4)

    # get all row elements
    rows_elements = driver.find_elements(By.CSS_SELECTOR, "tr.table_award_row")
    print(f"{len(rows_elements)} éléments 'table_award_row' trouvés (via Selenium).")

    processed_ids: Set[str] = set()
    detailed_results: List[Dict] = []
    compact_results: List[Dict] = []

    for idx, row_el in enumerate(rows_elements, start=1):
        try:
            # extract a stable id string to deduplicate (prefer numeric id)
            row_id = (row_el.get_attribute("id") or "").strip()
            numeric_id = ""
            if row_id and "_" in row_id:
                numeric_id = row_id.split("_")[-1]
            elif row_id:
                numeric_id = row_id
            # fallback: create pseudo id from title+index
            html = row_el.get_attribute("outerHTML")
            soup = BeautifulSoup(html, "html.parser")
            title = safe_text(soup.select_one("td.others_cell")) if soup.select_one("td.others_cell") else f"row_{idx}"

            uid = numeric_id or f"{title[:60]}__{idx}"
            if uid in processed_ids:
                continue
            # mark processed now (to avoid reprocessing if load more changes DOM)
            processed_ids.add(uid)

            # process the row
            row_results = process_row_element(driver, row_el, artist_name)
            # debug: if first row_result has both Previous Certification(s) empty -> log
            if row_results and all(not r["Previous Certification(s)"] for r in row_results):
                print(f"[DEBUG] ligne #{idx} ({title}) : historique vide (après clics/poll).")
            # append results to lists
            for r in row_results:
                detailed_results.append(r)
            compact_results.append({
                "Artist": row_results[0]["Artist"] if row_results else artist_name,
                "Title": row_results[0]["Title"] if row_results else title,
                "Certification Date": row_results[0]["Certification Date (Main)"] if row_results else "",
                "Label": row_results[0]["Label"] if row_results else "",
                "Format": row_results[0]["Format"] if row_results else "",
                "Level": row_results[0]["Level"] if row_results else "",
                "History": row_results
            })
            print(f"[{idx}/{len(rows_elements)}] OK - {title} -> {len(row_results)} historique(s)")
        except Exception as e:
            print(f"[{idx}] Erreur traitement ligne: {e}")
            continue

    # write detailed CSV
    if output_csv_detailed is None:
        safe_name = artist_name.strip().replace(" ", "_")
        output_csv_detailed = f"riaa_{safe_name}_detailed.csv"
    detailed_fieldnames = [
        "Artist", "Title", "Certification Date (Main)", "Label", "Format", "Level",
        "Release Date", "Previous Certification(s)", "Category", "Type",
        "Certified Units (text)", "Certified Units (computed)", "Parsed Label", "Genre"
    ]
    try:
        with open(output_csv_detailed, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=detailed_fieldnames)
            writer.writeheader()
            for r in detailed_results:
                writer.writerow(r)
        print(f"Export détaillé OK : {output_csv_detailed} ({len(detailed_results)} lignes)")
    except Exception as e:
        print(f"Erreur écriture détaillé : {e}")

    # write compact CSV
    if output_csv_compact is None:
        safe_name = artist_name.strip().replace(" ", "_")
        output_csv_compact = f"riaa_{safe_name}.csv"
    compact_fieldnames = ["Artist", "Title", "Certification Date", "Label", "Format", "Level", "History"]
    try:
        with open(output_csv_compact, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=compact_fieldnames)
            writer.writeheader()
            for c in compact_results:
                writer.writerow({
                    "Artist": c["Artist"],
                    "Title": c["Title"],
                    "Certification Date": c["Certification Date"],
                    "Label": c["Label"],
                    "Format": c["Format"],
                    "Level": c["Level"],
                    "History": json.dumps(c["History"], ensure_ascii=False)
                })
        print(f"Export compact OK : {output_csv_compact} ({len(compact_results)} lignes)")
    except Exception as e:
        print(f"Erreur écriture compact : {e}")

    driver.quit()
    return detailed_results


if __name__ == "__main__":
    artist = "Kanye West"
    fetch_certifications(artist)
