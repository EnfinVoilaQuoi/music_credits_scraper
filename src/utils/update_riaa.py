#!/usr/bin/env python3
"""
Script de mise à jour automatique et manuelle des certifications RIAA
Compatible avec le système de gestion unifié des certifications
"""

import argparse
import json
import logging
import re
import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

# Configurer l'encodage UTF-8 pour la console Windows
if sys.platform == "win32" and "pytest" not in sys.modules:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Import du scraper principal (patchright v2 — remplace l'ancien Selenium ;
# API compatible : init_driver/close_driver/scrape_by_date_range/scrape_by_artist)
from src.observability import repository as usage_repository
from src.observability.registry import Flow
from src.scrapers.riaa_scraper_v2 import RIAAScraperV2 as RIAAScraper
from src.utils import cert_clean_report
from src.utils.cert_normalize import programme_riaa, riaa_level, riaa_units


class RIAADatabaseUpdater:
    """Gestionnaire de mise à jour de la base de données RIAA"""

    def __init__(self, base_dir=None):
        """Initialise le gestionnaire de mise à jour.

        `base_dir=None` (défaut) → racine projet fiable via `__file__` (comme les
        constantes module `_RIAA_DIR`). L'ancien défaut `"music_credits_scraper"`
        était un chemin RELATIF : lancé en subprocess depuis la racine, il créait
        un dossier dupliqué `music_credits_scraper/music_credits_scraper/data/…`
        (seul `update_log.txt` y atterrissait, les CSV/meta passant par `_RIAA_DIR`)."""

        # Configuration des chemins
        self.base_dir = Path(base_dir) if base_dir else Path(__file__).parent.parent.parent
        self.data_dir = self.base_dir / "data" / "certifications" / "riaa"
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Persistance CSV : le clean certif_riaa.csv (dérivé du brut riaa_raw.csv,
        # module-niveau) alimente le matcher. Plus de base riaa.db.
        self.log_path = self.data_dir / "update_log.txt"

        # Configuration du logging
        self.setup_logging()

        # Initialise le scraper
        self.scraper = None

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
        # Sans cela, chaque ligne s'affiche DEUX fois : une par ce handler, une
        # par celui de la racine (posé par un `basicConfig` d'import). Le bruit
        # était doublé jusque dans la fenêtre de fin de la GUI, qui relaie cette
        # sortie.
        self.logger.propagate = False

    def get_last_update_date(self) -> datetime | None:
        """Date de la dernière certif connue — lue depuis certif_riaa.csv (le
        fichier canonique alimentant le matcher), pas la base sqlite."""
        try:
            if CERTIF_CSV.exists():
                # `.fillna("")` INDISPENSABLE (comme dans get_statistics juste en
                # dessous) : sans lui une date vide arrive en NaN (un float) et
                # `_riaa_iso` lève AttributeError — absent du `except` ci-dessous,
                # donc l'exception traverse la méthode. Une seule ligne sans date
                # (un palier scrapé sans date, cf. `_flatten_records`) suffirait à
                # casser DÉFINITIVEMENT l'affichage de fraîcheur RIAA.
                df = pd.read_csv(CERTIF_CSV, encoding="utf-8-sig", dtype=str).fillna("")
                dcol = next((c for c in df.columns if c.lower() == "certification_date"), None)
                if dcol:
                    isod = df[dcol].map(_riaa_iso)
                    isod = isod[isod != ""]
                    if len(isod):
                        return datetime.strptime(isod.max(), "%Y-%m-%d")
        except (OSError, ValueError, KeyError, TypeError) as e:
            self.logger.error(f"Lecture dernière date (certif_riaa.csv) : {e}")
        # Repli : fin de la base historique
        return datetime(2017, 10, 1)

    def update_from_scraped_data(self, data: list[dict]) -> tuple:
        """Accumule les données scrapées dans le brut riaa_raw.csv puis dérive le
        clean certif_riaa.csv (lu par le matcher). Plus de base riaa.db.
        Retourne (ajoutées_au_brut, 0)."""
        try:
            _total, added = _merge_certif_csv(_flatten_records(data))
        except (OSError, ValueError, KeyError, TypeError) as e:
            self.logger.error(f"Fusion certif_riaa.csv : {e}")
            return 0, 0
        return added, 0

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
                if not results:
                    self.logger.error(
                        "RIAA : aucune certification vue sur la période — scraper cassé "
                        "ou site modifié. Fraîcheur NON horodatée."
                    )
                    return False

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
            # Dernier ressort de l'orchestrateur (subprocess certifs) : trace + statut.
            self.logger.exception("Erreur mise à jour")
            self.log_update(start_str, end_str, 0, 0, f"ERROR: {e}")
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
                # Fraîcheur = date de dernière VÉRIFICATION : horodater même si
                # rien à récupérer (sinon la GUI affiche une MàJ périmée).
                _write_riaa_meta(source="GLOBAL")
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
            # Lignes VUES, distinctes des lignes AJOUTÉES. « 0 ajoutée » est le
            # régime normal d'un ré-run ; « 0 vue » sur plusieurs tranches d'un
            # mois ne l'est jamais (la RIAA certifie chaque semaine) et
            # signalait, en juillet 2026, un parseur mort que personne n'a vu
            # passer — la MàJ concluait « à jour » et horodatait la fraîcheur.
            total_seen = 0
            periodes = 0

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
                    periodes += 1
                    total_seen += len(results)

                    if results:
                        added, updated = self.update_from_scraped_data(results)
                        total_added += added
                        total_updated += updated
                        self.logger.info(
                            f"  -> {len(results)} vues, {added} ajoutées, {updated} mises à jour"
                        )
                    else:
                        self.logger.warning(
                            f"  -> AUCUNE certification vue sur {start_str}-{end_str}"
                        )

                    # Pause entre les requêtes
                    time.sleep(5)

                except Exception:
                    # Boucle par période résiliente : trace + on passe à la suivante.
                    self.logger.exception(f"Erreur période {start_str}-{end_str}")

                # Garde-fou anti-stagnation : si la tranche n'avance pas, on sort.
                if end_date <= current_date:
                    break
                current_date = end_date

            # Ferme le scraper
            if self.scraper:
                self.scraper.close_driver()
                self.scraper = None

            self.logger.info(
                f"Total: {total_seen} vues, {total_added} ajoutées, "
                f"{total_updated} mises à jour ({periodes} période(s))"
            )

            # Export final
            self.export_to_csv()

            if periodes and total_seen == 0:
                # Ne PAS horodater : une fraîcheur écrite ici affirmerait que la
                # base est à jour alors qu'on n'a rien pu lire. C'est exactement
                # ce qui a masqué la refonte du site pendant deux mois.
                self.logger.error(
                    f"RIAA : aucune certification vue sur {periodes} période(s) — "
                    "scraper cassé ou site modifié. Fraîcheur NON horodatée "
                    "(re-capturer les fixtures : scripts/capture_fixtures.py --only riaa)."
                )
                return False

            # Fraîcheur = date de dernière VÉRIFICATION : horodater à la fin du
            # run même si aucune nouvelle certif (_merge_certif_csv ne le fait que
            # lorsqu'il ajoute des lignes).
            _write_riaa_meta(source="GLOBAL")

            return True

        except Exception:
            self.logger.exception("Erreur mise à jour complète")
            return False

    def log_update(self, start: str, end: str, added: int, updated: int, status: str):
        """Trace la fraîcheur de la MàJ dans le sidecar metadata.json (plus de
        base riaa.db)."""
        _write_riaa_meta(source="GLOBAL")

    def export_to_csv(self):
        """Obsolète : certif_riaa.csv est écrit directement (raw→clean) par
        _merge_certif_csv. Conservé en no-op pour les appelants du flux bulk."""
        return

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
        """Statistiques lues depuis certif_riaa.csv (le clean) — plus de riaa.db."""
        stats = {"total": 0, "by_level": {}, "top_artists": [], "last_updated": None}
        if not CERTIF_CSV.exists():
            return stats
        df = pd.read_csv(CERTIF_CSV, encoding="utf-8-sig", dtype=str).fillna("")
        stats["total"] = len(df)
        if "Certification_Type" in df.columns:
            stats["by_level"] = df["Certification_Type"].value_counts().to_dict()
        if "Artist" in df.columns:
            stats["top_artists"] = list(df["Artist"].value_counts().head(10).items())
        if "Certification_Date" in df.columns and not df.empty:
            stats["last_updated"] = df["Certification_Date"].map(_riaa_iso).max()
        return stats


# ---------------------------------------------------------------------------
# RIAA CSV-centré (comme BRMA) : on écrit dans certif_riaa.csv, le fichier que
# lit le matcher unifié. Schéma compatible avec l'historique existant.
# ---------------------------------------------------------------------------
_RIAA_DIR = Path(__file__).parent.parent.parent / "data" / "certifications" / "riaa"
CERTIF_CSV = _RIAA_DIR / "certif_riaa.csv"  # CLEAN (lu par le matcher)
RIAA_RAW = _RIAA_DIR / "riaa_raw.csv"  # BRUT permanent (union des scrapes)
RIAA_META = _RIAA_DIR / "metadata.json"  # fraîcheur (sidecar)
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
    # Ajoutées le 2026-09-06, EN FIN de liste (les lignes existantes les
    # reçoivent vides, `_align_columns` s'en charge).
    #
    # `Award_Programme` : « US » ou « LATIN ». La RIAA décerne deux familles
    # d'awards aux ÉCHELLES différentes ; sans cette colonne, un « Platino »
    # (60 000 unités) se lit comme un « Platinum » (1 000 000). Relevée à la
    # source (famille du badge), avec repli sur le vocabulaire du niveau pour
    # les lignes du corpus historique.
    #
    # `Units` : ce que vaut le palier sur l'échelle de SON programme. Le scraper
    # la calculait déjà et la JETAIT à la frontière du CSV — c'est précisément
    # pourquoi l'erreur latine est restée invisible si longtemps.
    "Award_Programme",
    "Units",
    # `Award_Family` : ST (physique) / DI (numérique) / LA (latin), verbatim
    # depuis l'`alt` du badge. Le programme s'en déduit, l'inverse est faux —
    # c'est elle qui distingue un single physique d'un single numérique, ce que
    # le libellé de format ne dit pas, et c'est cette distinction qui décide du
    # seuil d'unités applicable avant août 2006.
    "Award_Family",
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


#: Ré-export : voir `cert_normalize.riaa_level` (la fonction vivait ici ET dans
#: `cert_matcher`, byte pour byte).
_riaa_level = riaa_level


def _texte_unites(niveau: str, date: str = "", format_type: str = "", famille: str = "") -> str:
    """Unités du palier, en TEXTE, aux seuils de l'ÉPOQUE quand ils diffèrent.

    Tout le pipeline CSV manipule des chaînes (`dtype=str`) : une colonne
    d'entiers entre en conflit avec la colonne vide que `_align_columns` pose
    sur les lignes qui ne l'ont pas.
    """
    unites = riaa_units(niveau, date=_riaa_iso(date), format_type=format_type, famille=famille)
    return str(unites) if unites else ""


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
            # Le programme est une propriété de l'AWARD, pas du palier : tous
            # les paliers d'une même certification en héritent.
            "Award_Programme": rec.get("award_programme", ""),
            "Award_Family": rec.get("award_family", ""),
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
                        "Units": _texte_unites(
                            _riaa_level(lvl),
                            h.get("certification_date", "") or rec.get("certification_date", ""),
                            base["Format_Type"],
                            rec.get("award_family", ""),
                        ),
                    }
                )
        else:
            niveau = _riaa_level(rec.get("award_level") or rec.get("certification_level", ""))
            rows.append(
                {
                    **base,
                    "Certification_Date": rec.get("certification_date", ""),
                    "Release_Date": rec.get("release_date", ""),
                    "Certification_Type": niveau,
                    "Units": _texte_unites(
                        niveau,
                        rec.get("certification_date", ""),
                        base["Format_Type"],
                        rec.get("award_family", ""),
                    ),
                }
            )
    return rows


def _align_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Restreint/complète aux CERTIF_COLUMNS (colonnes manquantes → '')."""
    df = df[[c for c in df.columns if c in CERTIF_COLUMNS]].copy()
    for c in CERTIF_COLUMNS:
        if c not in df.columns:
            df[c] = ""
    return df[CERTIF_COLUMNS]


def _load_riaa_raw() -> pd.DataFrame:
    """Charge le brut riaa_raw.csv (union permanente). Seedé depuis le clean
    existant au premier appel (meilleur historique dispo)."""
    if RIAA_RAW.exists():
        return _align_columns(pd.read_csv(RIAA_RAW, encoding="utf-8-sig", dtype=str).fillna(""))
    if CERTIF_CSV.exists():
        return _align_columns(pd.read_csv(CERTIF_CSV, encoding="utf-8-sig", dtype=str).fillna(""))
    return pd.DataFrame(columns=CERTIF_COLUMNS)


def _write_riaa_raw(df: pd.DataFrame) -> None:
    """Écrit le brut (backup horodaté avant écriture)."""
    if RIAA_RAW.exists():
        bdir = _RIAA_DIR / "backups"
        bdir.mkdir(exist_ok=True)
        shutil.copy2(RIAA_RAW, bdir / f"riaa_raw_backup_{datetime.now():%Y%m%d_%H%M%S}.csv")
    df.to_csv(RIAA_RAW, index=False, encoding="utf-8-sig")


def _clean_from_raw(raw_df: pd.DataFrame, report: dict | None = None) -> pd.DataFrame:
    """Dérive le CLEAN depuis le brut : retire artiste/titre vides, normalise le
    Format, dédoublonne (Artist|Title|Format|niveau normalisé|date).

    `report` (optionnel) recueille le DÉTAIL de ce qui a été fait. Sans lui, le
    nettoyage RIAA ne disait qu'une chose — « X → Y lignes » — ce qui ne permet
    ni de valider l'opération avant de l'appliquer, ni de comprendre après coup
    d'où vient l'écart.
    """
    df = _align_columns(raw_df)

    vides = df[(df["Artist"].str.strip() == "") | (df["Title"].str.strip() == "")]
    if report is not None:
        report["empty_removed"] = len(vides)
        report["empty_examples"] = [
            f"{r.Artist!r} — {r.Title!r} ({r.Certification_Date})"
            for r in vides.head(20).itertuples()
        ]
    df = df[(df["Artist"].str.strip() != "") & (df["Title"].str.strip() != "")]

    if report is not None:
        report["format_changes"] = _compter_changements(df["Format_Type"], _norm_format)
        report["formats_normalises"] = sum(report["format_changes"].values())
        report["level_changes"] = _compter_changements(df["Certification_Type"], _riaa_level)
        report["niveaux_normalises"] = sum(report["level_changes"].values())

    df = df.copy()
    df["Format_Type"] = df["Format_Type"].map(_norm_format)
    # Niveaux ramenés au vocabulaire canonique : le corpus historique dit
    # « 2x Multi-Platinum » là où le scraper écrit « 2x Platinum ». Le matcher le
    # normalisait déjà au chargement, mais le FICHIER gardait les deux formes —
    # illisible pour un humain, et piégeux pour tout lecteur qui oublierait
    # l'appel (un export, un script) : il retomberait sur le rang le plus bas.
    df["Certification_Type"] = df["Certification_Type"].map(_riaa_level)
    # Programme et unités complétés là où ils manquent (lignes antérieures à la
    # collecte de la famille d'award). Le programme est DÉDUIT du vocabulaire :
    # exact pour les libellés latins, et par défaut « US » sinon — les awards
    # latins écrits en vocabulaire US par l'ancien scraper restent donc
    # étiquetés US, faute de pouvoir les distinguer sans re-scraper.
    vide = df["Award_Programme"].astype(str).str.strip() == ""
    df.loc[vide, "Award_Programme"] = df.loc[vide, "Certification_Type"].map(programme_riaa)
    # Unités RECALCULÉES pour toutes les lignes, et non seulement complétées :
    # elles dépendent de la date, du format et de la famille, donc une valeur
    # écrite avant que ces colonnes existent serait au barème d'aujourd'hui pour
    # une certification qui n'y a jamais été soumise.
    df["Units"] = [
        _texte_unites(niveau, date, fmt, fam)
        for niveau, date, fmt, fam in zip(
            df["Certification_Type"],
            df["Certification_Date"],
            df["Format_Type"],
            df["Award_Family"],
            strict=True,
        )
    ]

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
    avant = len(df)
    df = df.drop_duplicates("_k", keep="first").drop(columns="_k")
    if report is not None:
        report["duplicates_removed"] = avant - len(df)
    return df


def _compter_changements(colonne, canoniser) -> dict[str, int]:
    """{« brut → canonique »: n} pour les valeurs que `canoniser` modifierait."""
    change: dict[str, int] = {}
    for brut in colonne:
        canon = canoniser(brut)
        if canon != brut:
            cle = f"{brut} → {canon}"
            change[cle] = change.get(cle, 0) + 1
    return change


def _write_riaa_meta(source: str = "GLOBAL", count: int | None = None) -> None:
    """Sidecar de fraîcheur (updates par source), aligné sur SNEP/BRMA."""
    meta: dict = {}
    if RIAA_META.exists():
        try:
            meta = json.loads(RIAA_META.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
    now = datetime.now().isoformat()
    updates = meta.get("updates") or {}
    updates[source] = now
    if count is None and CERTIF_CSV.exists():
        try:
            count = len(pd.read_csv(CERTIF_CSV, encoding="utf-8-sig", dtype=str))
        except (OSError, ValueError):
            count = meta.get("count")
    meta.update({"last_update": now, "last_source": source, "count": count, "updates": updates})
    RIAA_META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _merge_certif_csv(new_rows: list[dict]) -> tuple:
    """Accumule les lignes scrapées dans le BRUT (riaa_raw.csv, dédup EXACTE) puis
    dérive le CLEAN certif_riaa.csv. Retourne (total_clean, ajoutées_au_brut)."""
    if not new_rows:
        return (0, 0)
    new_df = _align_columns(pd.DataFrame(new_rows))

    raw = _load_riaa_raw()
    before = len(raw)
    combined = (
        pd.concat([raw, new_df], ignore_index=True) if not raw.empty else new_df
    ).drop_duplicates(ignore_index=True)
    _write_riaa_raw(combined)

    clean = _clean_from_raw(combined)
    if CERTIF_CSV.exists():
        bdir = _RIAA_DIR / "backups"
        bdir.mkdir(exist_ok=True)
        shutil.copy2(CERTIF_CSV, bdir / f"certif_riaa_backup_{datetime.now():%Y%m%d_%H%M%S}.csv")
    clean.to_csv(CERTIF_CSV, index=False, encoding="utf-8-sig")
    _write_riaa_meta(source="GLOBAL", count=len(clean))
    return (len(clean), len(combined) - before)


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


def clean_certif_csv(apply: bool = True) -> dict:
    """« Nettoyer » : régénère certif_riaa.csv (clean) depuis le brut
    riaa_raw.csv (retire vides, normalise Format, dédoublonne niveau normalisé).

    `apply=False` = DRY-RUN : compte sans rien réécrire, comme le nettoyeur
    SNEP. Retourne un rapport détaillé (cf. `cert_clean_report`).
    """
    report = cert_clean_report.rapport_vierge(CERTIF_CSV)
    report.update(
        {
            "empty_removed": 0,
            "duplicates_removed": 0,
            "formats_normalises": 0,
            "niveaux_normalises": 0,
            "empty_examples": [],
            "format_changes": {},
            "level_changes": {},
        }
    )
    raw = _load_riaa_raw()
    if raw.empty:
        report["error"] = "Brut RIAA vide, rien à nettoyer"
        return report

    report["rows_in"] = len(raw)
    clean = _clean_from_raw(raw, report)
    report["rows_out"] = len(clean)
    report["deja_propre"], report["lignes_modifiees"] = cert_clean_report.comparer_au_fichier(
        clean, CERTIF_CSV
    )

    if apply:
        if CERTIF_CSV.exists():
            bdir = _RIAA_DIR / "backups"
            bdir.mkdir(exist_ok=True)
            backup = bdir / f"certif_riaa_backup_{datetime.now():%Y%m%d_%H%M%S}.csv"
            shutil.copy2(CERTIF_CSV, backup)
            report["backup"] = str(backup)
        clean.to_csv(CERTIF_CSV, index=False, encoding="utf-8-sig")
        _write_riaa_meta(source="CLEAN", count=len(clean))
        report["applied"] = True
    return report


def format_clean_report(report: dict) -> str:
    """Rendu du rapport de nettoyage (formateur commun aux 3 sources)."""
    return cert_clean_report.render(
        report,
        titre="🧹 NETTOYAGE DU CSV RIAA",
        counters=[
            ("Lignes sans artiste/titre retirées", report.get("empty_removed", 0)),
            ("Doublons retirés", report.get("duplicates_removed", 0)),
            ("Formats normalisés", report.get("formats_normalises", 0)),
            ("Niveaux normalisés", report.get("niveaux_normalises", 0)),
        ],
        sections=[
            ("Formats normalisés", report.get("format_changes") or {}),
            ("Niveaux normalisés", report.get("level_changes") or {}),
        ],
        examples=[
            (
                f"Lignes retirées ({report.get('empty_removed', 0)})",
                [ex[:90] for ex in report.get("empty_examples") or []],
            )
        ],
        note_dry_run=(
            "ℹ️  DRY-RUN : rien n'a été écrit. Relance sans --dry-run pour "
            "appliquer (un backup sera créé)."
        ),
    )


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
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Avec --clean : compte sans réécrire (aperçu, comme le nettoyeur SNEP)",
    )

    args = parser.parse_args()

    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    if args.clean:
        print(format_clean_report(clean_certif_csv(apply=not args.dry_run)))
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
    # Lancé en SOUS-PROCESSUS par le dialog certifs : on branche les compteurs
    # d'usage sur la même base, sinon rien de ce run ne serait compté.
    with usage_repository.script_scope(Flow.CERTS):
        main()
