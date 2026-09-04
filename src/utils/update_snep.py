"""Script de mise à jour automatique des certifications SNEP"""

import shutil
import sys
from datetime import datetime
from pathlib import Path

import requests

# Configurer l'encodage UTF-8 pour la console Windows
if sys.platform == "win32" and "pytest" not in sys.modules:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


from src.config import DATA_PATH
from src.observability import repository as usage_repository
from src.observability import source_usage
from src.observability.registry import Flow
from src.utils.logger import get_logger

logger = get_logger(__name__)

#: Clé de `source_health.SOURCES` sous laquelle cet usage est compté.
_SOURCE = "snep"


def safe_print(message: str):
    """Print sécurisé qui utilise logger si stdout est fermé"""
    try:
        print(message)
    except (ValueError, AttributeError, OSError):
        # stdout fermé ou non disponible - utiliser le logger
        logger.info(message)


def _merge_csv_history(history_path: Path, new_path: Path) -> int:
    """
    Fusionne l'historique (backup) avec le nouvel export téléchargé.
    L'export SNEP ne contient qu'une fenêtre récente : le résultat est
    l'union dédupliquée (historique + nouveautés), écrite dans new_path.
    Retourne le nombre de nouvelles lignes apportées par l'export.
    """
    history_raw = history_path.read_text(encoding="utf-8-sig")
    new_raw = new_path.read_text(encoding="utf-8-sig")

    history_lines = history_raw.splitlines()
    new_lines = new_raw.splitlines()
    if not history_lines:
        return 0

    header = history_lines[0]
    seen = set()
    merged = []
    for line in history_lines[1:]:
        key = line.strip()
        if key and key not in seen:
            seen.add(key)
            merged.append(line)

    added = 0
    for line in new_lines[1:]:
        key = line.strip()
        if key and key not in seen:
            seen.add(key)
            merged.append(line)
            added += 1

    new_path.write_text("﻿" + header + "\n" + "\n".join(merged) + "\n", encoding="utf-8")
    return added


def download_latest_snep_csv():
    """Télécharge la dernière version du CSV depuis le site SNEP"""
    safe_print("=" * 60)
    safe_print("MISE À JOUR DES CERTIFICATIONS SNEP")
    safe_print("=" * 60)

    # Construire l'URL dynamiquement basée sur la date actuelle
    # Format observé : https://snepmusique.com/wp-content/uploads/YYYY/MM/certif-.csv
    current_date = datetime.now()
    year = current_date.year
    month = f"{current_date.month:02d}"

    # Essayer plusieurs URLs possibles (mois actuel et précédent)
    prev_month_num = (current_date.month - 2) % 12 + 1  # janvier → 12, pas 0
    prev_year = year if current_date.month > 1 else year - 1
    urls_to_try = [
        f"https://snepmusique.com/wp-content/uploads/{year}/{month}/certif-.csv",
        f"https://snepmusique.com/wp-content/uploads/{prev_year}/{prev_month_num:02d}/certif-.csv",
        # URL de fallback si le pattern change
        "https://snepmusique.com/wp-content/uploads/certif-.csv",
    ]

    # Chemin de destination
    dest_dir = Path(DATA_PATH) / "certifications" / "snep"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / "certif-.csv"

    # Backup du fichier existant si présent
    backup_path = None
    if dest_path.exists():
        backup_path = dest_dir / f"certif-backup-{datetime.now():%Y%m%d_%H%M%S}.csv"
        shutil.copy2(dest_path, backup_path)
        safe_print(f"✅ Backup créé : {backup_path.name}")

    # Essayer de télécharger le fichier
    for url in urls_to_try:
        if url is None:
            continue

        safe_print("\n🔍 Tentative de téléchargement depuis :")
        safe_print(f"   {url}")

        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://snepmusique.com/les-certifications/",
                "Accept": "text/csv,application/csv,text/plain,*/*",
            }

            response = source_usage.requests_get(_SOURCE, url, headers=headers, timeout=30)

            if response.status_code == 200:
                # Vérifier que c'est bien un CSV — bloquer si ce n'est pas le cas
                content_type = response.headers.get("Content-Type", "")
                if "csv" not in content_type.lower() and "text" not in content_type.lower():
                    safe_print(
                        f"⚠️ Contenu ignoré (type inattendu : {content_type}), fichier existant conservé"
                    )
                    continue

                # Écriture atomique : temp → rename pour éviter la corruption partielle
                import os
                import tempfile

                tmp_fd, tmp_name = tempfile.mkstemp(dir=dest_dir, suffix=".tmp")
                try:
                    with os.fdopen(tmp_fd, "wb") as f:
                        f.write(response.content)
                    os.replace(tmp_name, dest_path)
                except Exception:
                    try:
                        os.unlink(tmp_name)
                    except OSError:
                        pass
                    raise

                file_size = dest_path.stat().st_size
                safe_print("✅ Fichier téléchargé avec succès !")
                safe_print(f"   Taille : {file_size / 1024:.1f} KB")

                # FUSION avec l'historique : l'export SNEP est une fenêtre
                # glissante — l'écraser ferait perdre les certifications anciennes
                if backup_path and backup_path.exists():
                    added = _merge_csv_history(backup_path, dest_path)
                    safe_print(
                        f"🔀 Fusion avec l'historique : {added} nouvelle(s) certification(s) ajoutée(s)"
                    )

                return dest_path
            else:
                safe_print(f"❌ Erreur HTTP {response.status_code}")

        except requests.exceptions.RequestException as e:
            safe_print(f"❌ Erreur de connexion : {e}")
        except (OSError, ValueError) as e:
            safe_print(f"❌ Erreur inattendue : {e}")

    safe_print("\n⚠️ Impossible de télécharger le fichier CSV")
    safe_print("   Le fichier existant sera utilisé si disponible")

    if dest_path.exists():
        return dest_path
    return None


import random
import re as _re
import time

try:
    from src.config import DELAY_BETWEEN_REQUESTS, MAX_RETRIES, SELENIUM_TIMEOUT
except ImportError:  # repli si config minimale
    DELAY_BETWEEN_REQUESTS, MAX_RETRIES, SELENIUM_TIMEOUT = 1, 3, 30

_SNEP_BASE = "https://snepmusique.com/les-certifications/"
_HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": _SNEP_BASE,
}
_CSV_HEADER = (
    "Interprete;Titre;Éditeur / Distributeur;Catégorie;"
    "Certification;Date de sortie;Date de constat"
)


def _get_session() -> "requests.Session":
    """Session HTTP réutilisable (connexions keep-alive, headers communs)."""
    s = requests.Session()
    s.headers.update(_HTTP_HEADERS)
    return s


def _fetch(session, url: str, timeout: int = None) -> str:
    """GET avec retries + backoff progressif (idée reprise du scraper Genius).

    Respecte MAX_RETRIES / DELAY_BETWEEN_REQUESTS de config.py. Lève la
    dernière exception si tous les essais échouent.
    """
    timeout = timeout or SELENIUM_TIMEOUT
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as e:
            last_exc = e
            if attempt < MAX_RETRIES:
                time.sleep(DELAY_BETWEEN_REQUESTS * attempt)
    raise last_exc


def _parse_certifications_page(html: str) -> list:
    """Parse une page /les-certifications/ via les sélecteurs DOM des blocs
    `div.certification` (robuste, remplace l'ancien regex sur le texte brut).

    Structure d'un bloc (inspectée en live) :
        .certification
          .description > .categorie / .titre / .artiste / .editeur
          .certif.icon-XXX        (niveau : Or, Platine, Diamant…)
          .block_dates > .date (valeur + <span> "Date de sortie/constat")

    Retourne des lignes CSV (sans header) au format :
        Interprete;Titre;Éditeur;Catégorie;Certif;Date sortie;Date constat
    """
    from bs4 import BeautifulSoup

    def _clean(s: str) -> str:
        return _re.sub(r"\s+", " ", (s or "")).strip()

    soup = BeautifulSoup(html, "html.parser")
    rows = []

    for block in soup.select("div.certification"):
        desc = block.select_one(".description")
        if desc is None:
            continue

        def field(sel, desc=desc):
            el = desc.select_one(sel)
            return _clean(el.get_text(" ", strip=True)) if el else ""

        categorie = field(".categorie")
        titre = field(".titre")
        artiste = field(".artiste")
        label = field(".editeur")

        cert_el = block.select_one(".certif")
        certif = _clean(cert_el.get_text(" ", strip=True)) if cert_el else ""

        sortie = constat = ""
        for d in block.select(".block_dates .date"):
            span = d.find("span")
            lab = span.get_text(strip=True).lower() if span else ""
            val = d.get_text(" ", strip=True)
            if span:
                val = val.replace(span.get_text(" ", strip=True), "")
            val = _clean(val)
            if "sortie" in lab:
                sortie = val
            elif "constat" in lab:
                constat = val

        # Ligne valide seulement si les champs indispensables sont présents
        if not (artiste and titre and certif and constat):
            continue
        rows.append(";".join([artiste, titre, label, categorie, certif, sortie, constat]))

    return rows


def _row_key(fields: list) -> tuple:
    """Clé de déduplication stable (sans le label, qui varie entre sources).

    Les 4 derniers champs sont indexés par la FIN (2026-09-04). Le label est le
    seul champ de longueur variable : il peut contenir le séparateur `;`, auquel
    cas la ligne se découpe en 8 champs (cas réel, unique sur les 12 808 lignes
    du brut : « AYA NAKAMURA;DNK;"REC; 118 / WARNER MUSIC FRANCE";Albums;… »).
    Indexée par le DÉBUT, la clé de la ligne ENTRANTE cessait alors de coïncider
    avec celle de la ligne DÉJÀ EN BASE : la certification n'était jamais
    reconnue et se ré-ajoutait à chaque passage.
    """
    return (
        fields[0].strip().lower(),
        fields[1].strip().lower(),
        fields[-4],
        fields[-3],
        fields[-2],
        fields[-1],
    )


def _load_existing(dest_path: Path):
    """Charge le CSV maître : retourne (header, lignes_existantes, set_de_clés)."""
    header = _CSV_HEADER
    existing_lines = []
    keys = set()
    if dest_path.exists():
        lines = dest_path.read_text(encoding="utf-8-sig").splitlines()
        if lines:
            header = lines[0]
            existing_lines = [line for line in lines[1:] if line.strip()]
            for line in existing_lines:
                f = line.split(";")
                if len(f) >= 7:
                    keys.add(_row_key(f))
    return header, existing_lines, keys


def _write_merged(dest_path: Path, header: str, existing_lines: list, new_lines: list):
    """Écrit l'union (existant + nouveautés) avec BOM UTF-8, si nouveautés."""
    if new_lines:
        dest_path.write_text(
            "﻿" + header + "\n" + "\n".join(existing_lines + new_lines) + "\n",
            encoding="utf-8",
        )


def _discover_last_page(html: str) -> int:
    """Déduit le numéro de dernière page depuis les liens de pagination.

    Sur une page filtrée `?annee=YYYY`, tous les liens /page/N pointent vers
    cette année — le max est donc la dernière page de l'année.
    """
    nums = [int(n) for n in _re.findall(r"/page/(\d+)", html)]
    return max(nums) if nums else 1


def _norm_for_match(s: str) -> str:
    """Normalise pour comparaison : sans accents, en majuscules."""
    import unicodedata

    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.upper()


def _artist_matches(artist_field: str, query: str) -> bool:
    """Vrai si `query` apparaît comme MOT ENTIER dans le champ artiste
    (insensible casse/accents). Évite le bruit de sous-chaîne du filtre SNEP
    `?interprete=` (ex: 'IAM' qui matche WILLIAMS, LIAM, DIAM'S, IAMCHINO)."""
    q = _norm_for_match(query)
    if not q:
        return True
    return _re.search(r"\b" + _re.escape(q) + r"\b", _norm_for_match(artist_field)) is not None


def scrape_year(dest_path: Path, year: int, max_pages: int = 400) -> int:
    """Scrape l'intégralité d'une année via le filtre serveur `?annee=YYYY`,
    page par page, et fusionne les nouveautés dans le CSV maître.

    C'est la brique de backfill / comblement de trous : contrairement au
    rattrapage incrémental (qui s'arrête à la 1re page déjà connue), on
    parcourt toutes les pages de l'année pour garantir la complétude.
    Retourne le nombre de certifications ajoutées.
    """
    session = _get_session()
    header, existing_lines, existing_keys = _load_existing(dest_path)

    url1 = f"{_SNEP_BASE}?annee={year}"
    try:
        html = _fetch(session, url1)
    except requests.RequestException as e:
        safe_print(f"❌ Année {year} : page 1 inaccessible : {e}")
        return 0

    last_page = min(_discover_last_page(html), max_pages)
    safe_print(f"📅 Année {year} : {last_page} page(s) à parcourir")

    new_lines = []
    page = 1
    while page <= last_page:
        if page == 1:
            cur_html = html
        else:
            url = f"{_SNEP_BASE}page/{page}?annee={year}"
            try:
                cur_html = _fetch(session, url)
            except requests.RequestException as e:
                safe_print(f"❌ Année {year} page {page} : {e} — arrêt")
                break

        rows = _parse_certifications_page(cur_html)
        if not rows:
            safe_print(f"⚠️ Année {year} page {page} : aucun bloc — arrêt")
            break

        page_new = 0
        for row in rows:
            f = row.split(";")
            if len(f) < 7:
                continue
            key = _row_key(f)
            if key not in existing_keys:
                existing_keys.add(key)
                new_lines.append(row)
                page_new += 1

        safe_print(f"📄 {year} p{page}/{last_page} : {len(rows)} certifs, {page_new} nouvelle(s)")
        page += 1
        if page <= last_page:
            time.sleep(random.uniform(DELAY_BETWEEN_REQUESTS, DELAY_BETWEEN_REQUESTS * 1.8))

    _write_merged(dest_path, header, existing_lines, new_lines)
    safe_print(f"🔀 Année {year} : {len(new_lines)} nouvelle(s) certification(s) fusionnée(s)")
    return len(new_lines)


# `scrape_recent_certifications` a été RETIRÉE le 2026-09-04. Elle s'arrêtait
# à la première page intégralement connue ; mesuré sur le site réel, elle
# rendait 0 là où le parcours complet des mêmes années trouvait 159
# certifications (le SNEP antidate — cf. `update_snep_database`).
# Supplantée par `scrape_year`, qui est exhaustive et déjà testée.


def _rebuild_canonical(source: str = "GLOBAL") -> tuple[int, int]:
    """Régénère `certif_snep.csv` (+ meta) depuis le brut `certif-.csv` courant
    (fusion accumulante) et rafraîchit le matcher unifié. Retourne (nb lignes
    avant, nb lignes après)."""
    from src.utils.cert_matcher import reset_cert_matcher
    from src.utils.snep_build import read_canonical_csv, rebuild

    snep = Path(DATA_PATH) / "certifications" / "snep"
    csv_path = snep / "certif_snep.csv"
    before = len(read_canonical_csv(csv_path)) if csv_path.exists() else 0
    after = rebuild(snep / "certif-.csv", csv_path, snep / "certif_snep.meta.json", source=source)
    reset_cert_matcher()
    return before, after


def _years_to_scrape(today: datetime | None = None) -> list[int]:
    """Années à parcourir lors d'une mise à jour nominale.

    L'année courante, plus la précédente pendant les deux premiers mois : le SNEP
    antidate, donc une certification publiée en janvier peut porter une date de
    constat de décembre et n'apparaître que dans le classement de l'an passé.
    """
    today = today or datetime.now()
    return [today.year, today.year - 1] if today.month <= 2 else [today.year]


def update_snep_database():
    """Met à jour le CSV canonique avec le dernier export + scraping du site."""
    # 1. Télécharger le dernier export CSV (fenêtre partielle, fusionné à l'historique)
    csv_path = download_latest_snep_csv()

    if not csv_path:
        safe_print("❌ Impossible de mettre à jour : pas de fichier CSV disponible")
        return False

    # 2. Scraper le site pour combler les trous de l'export, ANNÉE COMPLÈTE.
    #
    # Jusqu'au 2026-09-04 on appelait ici `scrape_recent_certifications`, qui
    # s'arrêtait à la première page dont aucune ligne n'était nouvelle. Mesuré
    # sur le site réel : ce passage rendait 0 alors qu'un parcours exhaustif des
    # deux mêmes années trouvait **159 certifications**. Le SNEP ANTIDATE — une
    # certification publiée aujourd'hui porte une date de constat ancienne et
    # atterrit au milieu du classement, jamais en tête. Le profil relevé sur 2026
    # est sans appel : 0 sur les pages 10 à 14, puis 150 nouvelles sur les pages
    # 15 à 18. Aucun seuil « N pages connues d'affilée » ne survit à un trou de
    # cinq pages ; seul le parcours complet de l'année est correct.
    annees = _years_to_scrape()
    safe_print(f"\n🌐 Scraping SNEP, année(s) complète(s) : {', '.join(map(str, annees))}...")
    for annee in annees:
        try:
            added = scrape_year(csv_path, annee)
            safe_print(f"🔀 Année {annee} : {added} certification(s) ajoutée(s) depuis le site")
        except (
            requests.RequestException,
            AttributeError,
            KeyError,
            IndexError,
            TypeError,
            ValueError,
        ) as e:
            safe_print(f"⚠️ Scraping {annee} impossible ({e}) — on continue avec l'export")

    safe_print("\n📄 Régénération du CSV canonique (clean)...")
    total_before, total_after = _rebuild_canonical(source="GLOBAL")

    safe_print("\n✅ MISE À JOUR TERMINÉE")
    safe_print("\n📊 Résumé :")
    safe_print(f"  • Certifications avant : {total_before}")
    safe_print(f"  • Certifications après : {total_after}")
    safe_print(f"  • Nouvelles/mises à jour : {total_after - total_before}")
    return True


def check_for_updates():
    """Vérifie s'il y a de nouvelles certifications disponibles"""
    safe_print("🔍 Vérification des mises à jour...")

    # Cette fonction pourrait comparer les dates ou le contenu
    # Pour l'instant, elle lance simplement la mise à jour
    return update_snep_database()


def schedule_monthly_update():
    """Programme une mise à jour mensuelle (à utiliser avec cron ou task scheduler)"""
    import logging

    # Configuration du logging pour le mode automatique
    log_file = Path(DATA_PATH) / "certifications" / "snep" / "update_log.txt"
    logging.basicConfig(
        filename=str(log_file),
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    logging.info("=" * 50)
    logging.info("Début de la mise à jour mensuelle programmée")

    try:
        success = update_snep_database()
        if success:
            logging.info("✅ Mise à jour réussie")
        else:
            logging.error("❌ Échec de la mise à jour")
    except Exception:
        logging.exception("❌ Erreur lors de la mise à jour")

    logging.info("Fin de la mise à jour mensuelle")
    logging.info("=" * 50)


def fetch_artist_certifications(artist_name: str) -> bool:
    """
    Récupère le CSV COMPLET des certifications d'un artiste via le filtre
    ?interprete= du site SNEP (seul export encore fiable), le fusionne dans
    le CSV maître et réimporte en base.
    """
    from urllib.parse import quote, urljoin

    safe_print("=" * 60)
    safe_print(f"CERTIFICATIONS SNEP — ARTISTE : {artist_name}")
    safe_print("=" * 60)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://snepmusique.com/les-certifications/",
    }

    # 1. Charger la page filtrée (génère l'export côté serveur)
    page_url = f"https://snepmusique.com/les-certifications/?interprete={quote(artist_name)}"
    safe_print(f"🌐 {page_url}")
    try:
        resp = source_usage.requests_get(_SOURCE, page_url, headers=headers, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        safe_print(f"❌ Page artiste inaccessible : {e}")
        return False

    # 2. Trouver le lien "Télécharger en CSV" généré pour ce filtre
    import re as _re

    m = _re.search(r'href="([^"]*certif-[^"]*\.csv)"', resp.text)
    if not m:
        safe_print("❌ Lien CSV introuvable sur la page (artiste inconnu du SNEP ?)")
        return False
    csv_url = urljoin("https://snepmusique.com/", m.group(1))
    safe_print(f"📥 Export : {csv_url}")

    # 3. Télécharger et valider le CSV
    try:
        csv_resp = source_usage.requests_get(_SOURCE, csv_url, headers=headers, timeout=30)
        csv_resp.raise_for_status()
    except requests.RequestException as e:
        safe_print(f"❌ Téléchargement CSV impossible : {e}")
        return False

    content = csv_resp.content.decode("utf-8-sig", errors="replace")
    lines = content.splitlines()
    if not lines or "Interprete" not in lines[0]:
        safe_print("❌ Contenu CSV inattendu, abandon")
        return False
    safe_print(f"✅ {len(lines) - 1} certification(s) renvoyée(s) par le SNEP")

    # Filtre mot-entier : le SNEP fait un `contains` sur ?interprete=, donc
    # 'IAM' ramène aussi WILLIAMS, LIAM, DIAM'S… On ne garde que les lignes où
    # l'artiste correspond réellement au terme recherché (comme mot entier).
    header_line = lines[0]
    kept = [header_line]
    dropped = 0
    for line in lines[1:]:
        if not line.strip():
            continue
        artist_field = line.split(";")[0]
        if _artist_matches(artist_field, artist_name):
            kept.append(line)
        else:
            dropped += 1
    content = "\n".join(kept) + "\n"
    lines = kept
    safe_print(
        f"🔎 Filtre mot entier '{artist_name}' : "
        f"{len(kept) - 1} gardée(s), {dropped} bruit écarté(s)"
    )
    if len(kept) <= 1:
        safe_print(
            "⚠️ Aucune ligne ne correspond exactement — abandon "
            "(artiste mal orthographié ou inconnu ?)"
        )
        return False

    # 4. Fusionner dans le CSV maître
    dest_path = Path(DATA_PATH) / "certifications" / "snep" / "certif-.csv"
    import os
    import tempfile

    fd, tmp_name = tempfile.mkstemp(suffix=".csv")
    os.close(fd)  # Windows : fermer le descripteur sinon unlink échoue (WinError 32)
    tmp = Path(tmp_name)
    tmp.write_text(content, encoding="utf-8")
    try:
        if dest_path.exists():
            # _merge_csv_history écrit l'union (historique + nouveau) dans le 2e arg
            shutil.copy2(dest_path, tmp.with_suffix(".hist"))
            added = _merge_csv_history(tmp.with_suffix(".hist"), tmp)
            shutil.copy2(tmp, dest_path)
            tmp.with_suffix(".hist").unlink(missing_ok=True)
        else:
            shutil.copy2(tmp, dest_path)
            added = len(lines) - 1
    finally:
        tmp.unlink(missing_ok=True)
    safe_print(f"🔀 {added} nouvelle(s) certification(s) ajoutée(s) au CSV maître")

    # 5. Régénérer le CSV canonique (récupération ARTISTE)
    safe_print("\n📄 Régénération du CSV canonique (clean)...")
    _, after = _rebuild_canonical(source="ARTIST")
    safe_print(f"✅ Clean régénéré : {after} certification(s)")
    return True


def backfill_years(years) -> int:
    """Scrape intégralement une ou plusieurs années (filtre ?annee=) dans le
    CSV maître puis réimporte en base. Brique de comblement de trous.
    """
    dest_path = Path(DATA_PATH) / "certifications" / "snep" / "certif-.csv"
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    safe_print("=" * 60)
    safe_print(f"BACKFILL SNEP — année(s) : {', '.join(str(y) for y in years)}")
    safe_print("=" * 60)

    total = 0
    for y in years:
        total += scrape_year(dest_path, int(y))

    safe_print("\n📄 Régénération du CSV canonique (clean)...")
    _rebuild_canonical(source="SCRAPE")
    safe_print(f"✅ Backfill terminé : {total} nouvelle(s) certification(s) au total")
    return total


def main():
    """Point d'entrée principal du script"""
    import argparse

    parser = argparse.ArgumentParser(description="Mise à jour automatique des certifications SNEP")
    parser.add_argument(
        "--update", action="store_true", help="Télécharger et importer les dernières certifications"
    )
    parser.add_argument(
        "--check", action="store_true", help="Vérifier s'il y a des mises à jour disponibles"
    )
    parser.add_argument(
        "--scheduled",
        action="store_true",
        help="Mode automatique pour tâche planifiée (cron/scheduler)",
    )
    parser.add_argument(
        "--artist",
        type=str,
        default=None,
        help="Récupérer le CSV complet d'un artiste (filtre ?interprete=) et le fusionner",
    )
    parser.add_argument(
        "--year",
        type=int,
        action="append",
        default=None,
        metavar="AAAA",
        help="Backfill complet d'une année via ?annee= (répétable, ex: --year 2025 --year 2026)",
    )

    args = parser.parse_args()

    if args.year:
        backfill_years(args.year)
    elif args.artist:
        fetch_artist_certifications(args.artist)
    elif args.scheduled:
        # Mode silencieux pour les tâches planifiées
        schedule_monthly_update()
    elif args.update:
        update_snep_database()
    elif args.check:
        check_for_updates()
    else:
        # Par défaut, lancer la mise à jour
        safe_print("💡 Conseil : Utilisez --help pour voir toutes les options\n")
        update_snep_database()


if __name__ == "__main__":
    # Ce script tourne en SOUS-PROCESSUS (dialog certifs de la GUI) : sans ce
    # branchement, son usage de la source serait compté nulle part. La base est
    # la même, les compteurs se rejoignent donc dans les mêmes tables.
    with usage_repository.script_scope(Flow.CERTS):
        main()
# fin — scraper SNEP : parser BS4 par sélecteurs + backfill par année (?annee=)
