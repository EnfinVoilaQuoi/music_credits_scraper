#!/usr/bin/env python3
"""
Script de mise à jour automatique et manuelle des certifications RIAA
Compatible avec le système de gestion unifié des certifications
"""

import argparse
import logging
import re
import sys
import time
from datetime import date, datetime, timedelta
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
from src.utils import cert_clean_report, cert_store
from src.utils.cert_normalize import (
    FORMAT_JOUR_CLI,
    jour_cli,
    lire_jour_cli,
    normalize_text,
    programme_riaa,
    riaa_level,
    riaa_units,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)


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
        self.logger = get_logger(__name__)
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
        fichier canonique alimentant le matcher), pas la base sqlite.

        Fichier ABSENT → amorçage depuis la fin de la base historique (2017).
        Fichier présent mais ILLISIBLE → `None` : le repli 2017 y faisait
        repartir `update_missing_months` sur ~108 tranches mensuelles (des
        heures de scrape) à cause d'un simple fichier corrompu, avec pour seul
        signal une ligne de log.
        """
        if not CERTIF_CSV.exists():
            return datetime(2017, 10, 1)
        try:
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
            return None
        # Fichier présent, aucune date exploitable : le clean est vide ou sans
        # colonne de date — on repart de la base historique, comme à l'amorçage.
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

                # Fraîcheur = date de dernière vérification (sidecar meta).
                _write_riaa_meta(source="GLOBAL")

                self.logger.info(f"✓ Ajoutées: {added}")
                self.logger.info(f"✓ Mises à jour: {updated}")

                return True

            finally:
                self.scraper.close_driver()

        except Exception:
            # Dernier ressort de l'orchestrateur (subprocess certifs) : trace + statut.
            self.logger.exception("Erreur mise à jour")
            _write_riaa_meta(source="GLOBAL")
            return False

    def update_missing_months(self) -> bool:
        """Met à jour tous les mois manquants depuis la dernière mise à jour"""
        try:
            # Détermine la dernière date
            last_date = self.get_last_update_date()
            if last_date is None:
                self.logger.error(
                    "certif_riaa.csv présent mais illisible : MàJ REFUSÉE (un repli "
                    "sur 2017 rescraperait neuf ans sans le dire)"
                )
                return False
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
            periodes = lues = 0

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

                periodes += 1  # TENTÉE — compter les seules réussies rendait
                # la garde ci-dessous inopérante DANS LE PIRE DES CAS (voir plus bas).
                try:
                    results = self.scraper.scrape_by_date_range(start_str, end_str, "certification")
                    lues += 1
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
                f"{total_updated} mises à jour ({lues}/{periodes} période(s) lue(s))"
            )

            if periodes and not lues:
                # AUCUNE période n'a pu être lue : le scraper lève à chaque appel
                # (navigateur absent, Cloudflare). C'est le pire des cas, et
                # c'était le SEUL que la garde ne couvrait pas — elle testait
                # `periodes and total_seen == 0` en ne comptant `periodes` qu'en
                # cas de SUCCÈS, si bien qu'un échec intégral la court-circuitait
                # par sa propre garde et horodatait la fraîcheur.
                self.logger.error(
                    f"RIAA : {periodes} période(s) demandée(s), AUCUNE lue — "
                    "scraper cassé, navigateur absent ou accès bloqué. "
                    "Fraîcheur NON horodatée."
                )
                return False

            if total_seen == 0:
                # Des pages ont été lues, mais pas une seule certification : le
                # parseur ne comprend plus la page. C'est exactement ce qui a
                # masqué la refonte du site pendant deux mois.
                self.logger.error(
                    f"RIAA : aucune certification vue sur {lues} période(s) lue(s) — "
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
            # Même entrée que `--from/--to` : `fetch_periode` DÉCOUPE la fenêtre.
            # L'ancien branchement appelait `scrape_by_date_range` d'un bloc —
            # la route qui tronque à ~6 000 lignes sans le dire (2026-09-09).
            start = input(f"Date début ({FORMAT_JOUR_CLI}): ").strip()
            end = input(f"Date fin ({FORMAT_JOUR_CLI}): ").strip()
            fetch_periode(start, end)

        elif choice == "4":
            # Même entrée que `--artist` (timeline des paliers incluse).
            fetch_artist(input("Nom de l'artiste: ").strip())

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


#: Un saut de ligne (avec les blancs qui l'entourent) → un espace.
_SAUT_DE_LIGNE = re.compile(r"\s*[\r\n\t]+\s*")


def _align_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Restreint/complète aux CERTIF_COLUMNS (colonnes manquantes → '') et
    aplatit les sauts de ligne des colonnes de TEXTE.

    L'aplatissement est ici, et pas dans le seul parseur, parce que
    `_align_columns` est le point de passage des DEUX côtés de la fusion — le
    brut relu et les lignes qui arrivent. C'est ce qui rend stable la dédup du
    brut, laquelle est une égalité EXACTE : un retour à la ligne relu tantôt en
    CRLF tantôt en LF fabriquait sinon un doublon parfait (mesuré le 2026-09-09,
    YUNG KAI – BLUE, unique « ajout » d'un balayage de 14 h). Le corriger à la
    lecture du site n'aurait valu que pour les lignes à venir, alors que le brut
    en porte déjà.

    Volontairement TIMIDE : on ne touche PAS aux doubles espaces, qui peuplent
    les labels par milliers. Les écraser aurait fait de chaque ligne re-scrapée
    le doublon de celle en base — 2 696 lignes réécrites contre 9.
    """
    df = df[[c for c in df.columns if c in CERTIF_COLUMNS]].copy()
    for c in CERTIF_COLUMNS:
        if c not in df.columns:
            df[c] = ""
    df = df[CERTIF_COLUMNS]
    for colonne in ("Artist", "Title", "Label"):
        df[colonne] = df[colonne].map(lambda s: _SAUT_DE_LIGNE.sub(" ", str(s)).strip())
    return df


def _load_riaa_raw() -> pd.DataFrame:
    """Charge le brut riaa_raw.csv (union permanente). Seedé depuis le clean
    existant au premier appel (meilleur historique dispo)."""
    if RIAA_RAW.exists():
        return _align_columns(pd.read_csv(RIAA_RAW, encoding="utf-8-sig", dtype=str).fillna(""))
    if CERTIF_CSV.exists():
        return _align_columns(pd.read_csv(CERTIF_CSV, encoding="utf-8-sig", dtype=str).fillna(""))
    return pd.DataFrame(columns=CERTIF_COLUMNS)


def _write_riaa_raw(df: pd.DataFrame, backup: bool = True) -> None:
    """Écrit le brut (backup horodaté avant écriture).

    `backup=False` sert au balayage par tranches : l'unité de travail qu'on
    protège est le RUN, pas la tranche. Sauvegarder à chaque fusion ferait
    trente copies de 5 Mo pour un seul balayage — le bruit finirait par cacher
    la sauvegarde qui compte.
    """
    if backup:
        cert_store.sauvegarder(RIAA_RAW)
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

    def norm(s):
        return re.sub(r"\s+", " ", str(s)).strip().upper()

    # Artiste et titre par `normalize_text` — la normalisation du MATCHER, pas
    # un simple upper : « #BEAUTIFUL » et « BEAUTIFUL », « FAST CAR (FEAT.
    # DAKOTA) » et « FAST CAR FEAT. DAKOTA » sont la même certification écrite
    # par l'import et par le site (mesuré le 2026-09-15 : 3 clés, 6 lignes, et
    # rien d'autre ne bouge sur 48 254). Format et niveau restent en upper.
    df["_k"] = (
        df["Artist"].map(normalize_text)
        + "|"
        + df["Title"].map(normalize_text)
        + "|"
        + df["Format_Type"].map(norm)
        + "|"
        + df["Certification_Type"].map(lambda x: norm(_riaa_level(x)))
        + "|"
        + df["Certification_Date"].map(_riaa_iso)
    )
    avant = len(df)
    df = _combiner_representations(df).drop(columns="_k")
    if report is not None:
        report["duplicates_removed"] = avant - len(df)

    # Unités RECALCULÉES pour toutes les lignes, et non seulement complétées :
    # elles dépendent de la date, du format et de la FAMILLE, donc une valeur
    # écrite avant que ces colonnes existent serait au barème d'aujourd'hui pour
    # une certification qui n'y a jamais été soumise. APRÈS la combinaison : la
    # famille peut venir de l'autre représentation de la même certification.
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
    return df


def _combiner_representations(df: pd.DataFrame) -> pd.DataFrame:
    """Une ligne par clé `_k`, qui COMBINE ses représentations au lieu d'en
    garder une.

    Le brut porte souvent la même certification deux fois : la ligne de
    l'import historique (H3nrycrosby — date « October 17, 2017 », `Release_Date`,
    `Genre`, `Group_Type`, mais AUCUNE famille d'award) et celle du scraper
    (date ISO, `Award_Family` lue sur le badge, mais sans date de sortie ni
    genre). `keep="first"` gardait l'import. Mesuré le 2026-09-15, après le
    balayage 2000-2026 : **17 150 lignes du clean sans famille alors que le
    brut l'avait**, dont 12 401 singles — là où la famille (physique `ST` /
    numérique `DI`) décide des seuils d'époque de `riaa_units`. Le run avait
    ramené l'information ; le clean ne la servait pas.

    La ligne AVEC famille sert de base (elle vient du site, le plus récent
    lecteur), chaque colonne vide y est comblée par la première autre
    représentation qui la renseigne. Ordre de première apparition conservé,
    donc stable d'un rebuild à l'autre.
    """
    if not df["_k"].duplicated().any():
        return df
    avec_famille = df["Award_Family"].astype(str).str.strip() != ""
    # Tri STABLE : la ligne à famille passe devant sa jumelle, rien d'autre ne
    # bouge — `groupby(sort=False).first()` prend alors la première valeur non
    # vide de chaque colonne.
    ordre = df.assign(_sans=~avec_famille).sort_values("_sans", kind="stable")
    colonnes = [c for c in df.columns if c != "_k"]
    combine = (
        ordre[colonnes + ["_k"]]
        .replace("", pd.NA)
        .groupby("_k", sort=False, dropna=False)
        .first()
        .reset_index()
        .fillna("")
    )
    # `groupby(sort=False)` ordonne par première apparition dans `ordre`, qui a
    # été trié : on rétablit l'ordre d'apparition dans `df`.
    rang = {k: i for i, k in enumerate(df["_k"].drop_duplicates())}
    combine["_rang"] = combine["_k"].map(rang)
    return combine.sort_values("_rang", kind="stable").drop(columns="_rang")[df.columns]


def _compter_changements(colonne, canoniser) -> dict[str, int]:
    """{« brut → canonique »: n} pour les valeurs que `canoniser` modifierait."""
    change: dict[str, int] = {}
    for brut in colonne:
        canon = canoniser(brut)
        if canon != brut:
            cle = f"{brut} → {canon}"
            change[cle] = change.get(cle, 0) + 1
    return change


def _write_riaa_meta(
    source: str = "GLOBAL", count: int | None = None, *, partial: str = ""
) -> None:
    """Sidecar de fraîcheur — la FORME est portée par `cert_store`.

    Ne reste ici que ce qui est propre à RIAA : le chemin, et le recompte depuis
    le clean quand l'appelant ne donne pas de total.
    """
    if count is None and CERTIF_CSV.exists():
        try:
            count = len(pd.read_csv(CERTIF_CSV, encoding="utf-8-sig", dtype=str))
        except (OSError, ValueError):
            count = None
    cert_store.ecrire_fraicheur(RIAA_META, source, count=count, partial=partial)


def _compter_clean() -> int:
    """Nombre de lignes du clean sur disque (0 s'il n'existe pas encore)."""
    if not CERTIF_CSV.exists():
        return 0
    try:
        return len(pd.read_csv(CERTIF_CSV, encoding="utf-8-sig", dtype=str))
    except (OSError, ValueError):
        return 0


def _merge_certif_csv(new_rows: list[dict], backup: bool = True, *, partial: str = "") -> tuple:
    """Accumule les lignes scrapées dans le BRUT (riaa_raw.csv, dédup EXACTE) puis
    dérive le CLEAN certif_riaa.csv. Retourne (total_clean, ajoutées_au_brut).

    `backup=False` : voir `_write_riaa_raw`. Le balayage par tranches sauvegarde
    à sa PREMIÈRE fusion et pas aux suivantes.
    """
    if not new_rows:
        return (0, 0)
    new_df = _align_columns(pd.DataFrame(new_rows))

    raw = _load_riaa_raw()
    before = len(raw)
    combined = (
        pd.concat([raw, new_df], ignore_index=True) if not raw.empty else new_df
    ).drop_duplicates(ignore_index=True)
    _write_riaa_raw(combined, backup=backup)

    clean = _clean_from_raw(combined)
    if backup:
        cert_store.sauvegarder(CERTIF_CSV)
    clean.to_csv(CERTIF_CSV, index=False, encoding="utf-8-sig")
    _write_riaa_meta(source="GLOBAL", count=len(clean), partial=partial)

    # Le magasin a changé sur disque : le matcher, s'il est vivant dans CE
    # processus, sert encore l'état d'avant. SNEP et BPI le rafraîchissaient
    # depuis leur fusion, BRMA et RIAA non — trois sources sur quatre écrivant
    # le même genre de fichier, deux comportements. C'est l'ÉCRIVAIN qui sait
    # que le fichier a changé ; le consommateur, lui, ne peut que le supposer.
    # (En sous-processus — le cas de la GUI — c'est un no-op : le matcher n'y a
    # jamais été instancié.)
    from src.utils.cert_matcher import reset_cert_matcher

    reset_cert_matcher()
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


#: Nombre de lignes VISÉ par tranche de balayage.
#:
#: Le site plafonne à ~6 000 lignes par requête (`_MAX_LOAD_MORE` × 30 lignes),
#: mais ce n'est PAS le plafond qui commande la taille d'une tranche : le coût
#: d'un « Show More » croît avec la taille du DOM déjà chargé, et il croît vite.
#: Mesuré du 07 au 09/09/2026 sur des fenêtres réelles — 65 pages en 45 min
#: (41 s/clic), 102 pages en 2 h 25 (85 s/clic), 200 pages en 14 h (253 s/clic).
#: Le coût total DÉCROÎT donc quand les tranches raccourcissent, jusqu'à ce que
#: le coût fixe d'une requête (lancement du navigateur + `goto`) reprenne le
#: dessus. La cible est calée sur la seule tranche complète réellement mesurée —
#: 1 957 lignes en 45 min — et non sur une fraction du plafond, qui serait un
#: chiffre inventé.
_CIBLE_LIGNES = 2000

#: Longueur de la PREMIÈRE tranche, en jours. Volontairement courte : la seule
#: chose qui coûte vraiment cher est une tranche tronquée (on paie le plafond
#: plein tarif, puis on recommence), donc on part sous la densité de l'année la
#: plus dense connue (2025 : ~5 000 certifications) et on laisse le contrôle
#: proportionnel rallonger sur les périodes creuses.
_JOURS_DEPART = 180
_JOURS_MIN = 1
_JOURS_MAX = 3653  # 10 ans

#: Tentatives d'une MÊME tranche avant de la déclarer non lue et de passer à la
#: suivante. Une page RIAA qui ne se rend pas est souvent passagère (Cloudflare,
#: navigateur mort) ; insister indéfiniment bloquerait le balayage, ne pas
#: insister du tout sauterait la fenêtre au premier hoquet.
_ESSAIS_PAR_TRANCHE = 3

#: Tranches consécutives SANS la moindre ligne avant de conclure « accès bloqué ».
#: Avec la croissance sur période vide, trois tranches couvrent déjà ~10 ans :
#: au-delà, ce n'est plus une période sans certification, c'est un mur.
_TRANCHES_A_VIDE = 3


def ajuster_tranche(
    jours: int, lignes: int, *, tronque: bool = False, cible: int = _CIBLE_LIGNES
) -> int:
    """Longueur de la PROCHAINE tranche, d'après ce qu'a rendu la précédente.

    Contrôle proportionnel borné, volontairement ASYMÉTRIQUE selon ce que
    `lignes` vaut comme mesure :

    - tranche TRONQUÉE : `lignes` n'est plus une mesure mais un PLANCHER — on
      ignore ce que la fenêtre contenait vraiment. On prend donc le plus
      contractant des deux estimateurs, la moitié ou le prorata. Une bissection
      seule suffirait à terminer, mais pas à finir vite : lancée sur 26 ans, elle
      paierait cinq troncatures à 14 h avant d'atteindre la bonne taille.
    - tranche VIDE : rien à mesurer, on rallonge d'un facteur borné plutôt que
      de traverser une période sans certification tranche par tranche.
    - sinon : prorata simple, facteur bridé dans [0,25 ; 4] pour que la longueur
      n'oscille pas sur une tranche atypique.
    """
    if tronque:
        prorata = int(jours * cible / lignes) if lignes else jours // 2
        return max(_JOURS_MIN, min(jours // 2, prorata))
    if lignes <= 0:
        return min(_JOURS_MAX, jours * 4)
    facteur = min(4.0, max(0.25, cible / lignes))
    return max(_JOURS_MIN, min(_JOURS_MAX, int(jours * facteur)))


def _jour(s: str) -> date:
    """Une date de ligne de commande, « JJ-MM-AAAA » (ISO accepté) — le lecteur
    commun des CLI de certifs, `cert_normalize.lire_jour_cli`."""
    return lire_jour_cli(s)


def fetch_periode(debut: str, fin: str, *, cible: int = _CIBLE_LIGNES) -> bool:
    """Rescrape une période et la fusionne dans le corpus — en la DÉCOUPANT.

    `--auto` repart toujours de la dernière certification connue : il ne sait
    pas revenir en arrière. Or les trous que signale la validation sont
    justement DERRIÈRE cette date (un mois de 2006 manquant ne sera jamais
    rattrapé par une MàJ de 2026). D'où cette entrée, qui vise une fenêtre.

    **Le découpage est fait ICI, pas par l'utilisateur.** Une requête RIAA rend
    au plus ~6 000 lignes : demander « 2000 → 2026 » d'un bloc rendait le bout
    RÉCENT de la fenêtre et laissait croire au succès (mesuré le 2026-09-09 :
    6 029 lignes sur ~37 000, 14 h de scrape, un ✅ à l'écran et le seul signal
    de troncature au fond d'un fichier de log). La fenêtre est donc parcourue à
    rebours, du plus récent au plus ancien — l'ordre dans lequel le site sert
    ses résultats —, en tranches dont la longueur s'ajuste sur la densité RÉELLE
    de certifications rencontrée, laquelle varie d'un facteur dix entre 2004 et
    2025 : aucune découpe fixe ne peut convenir aux deux.

    Chaque tranche est fusionnée AUSSITÔT. Un balayage complet dure des heures ;
    accumuler en mémoire pour tout écrire à la fin, c'est tout perdre sur une
    coupure.
    """
    try:
        d0, d1 = _jour(debut), _jour(fin)
    except ValueError as e:
        print(f"❌ {e}")
        return False
    if d0 >= d1:
        print("❌ --from doit précéder --to")
        return False

    scraper = RIAAScraper(headless=True)
    print(f"=== RIAA, période {jour_cli(d0)} → {jour_cli(d1)} (découpage automatique) ===")

    curseur, jours = d1, min((d1 - d0).days, _JOURS_DEPART)
    total_vues = total_ajoutees = tranches = fusions = 0
    # Deux comptes, et le bandeau les DISTINGUE : les lignes ajoutées au BRUT
    # (dédup exacte — une certification déjà connue par l'import historique y
    # entre une seconde fois dès que le site la représente autrement, famille
    # d'award ou date ISO) et les CERTIFICATIONS nouvelles dans le clean. Le
    # balayage 2000-2026 du 2026-09-15 annonçait « 11 504 ajoutées » : c'était
    # le brut ; le clean n'en comptait que 2 779, et rien ne le disait.
    clean_depart = total_clean = _compter_clean()
    irreductibles: list[date] = []
    non_lues: list[tuple[date, date]] = []
    essais = 0

    while curseur > d0:
        borne = max(d0, curseur - timedelta(days=jours))
        tranches += 1
        resultats = scraper.scrape_by_date_range(
            borne.isoformat(), curseur.isoformat(), "certification"
        )
        vues = len(resultats)
        total_vues += vues
        ajoutees = nouvelles = 0

        # « PAS LU » n'est ni « vide » ni « tronqué ». Sans ce troisième état, une
        # page non rendue passait pour une période creuse : le curseur avançait
        # (fenêtre définitivement sautée) et `ajuster_tranche(jours, 0)` rallongeait
        # la suivante — l'échec ACCÉLÉRAIT le balayage, et le run se terminait sur
        # un ✅. On retente la même tranche, puis on la consigne.
        if scraper.lecture_echouee:
            essais += 1
            if essais < _ESSAIS_PAR_TRANCHE:
                print(f"  {borne} → {curseur} : page non rendue, essai {essais + 1}")
                continue  # curseur inchangé : c'est la MÊME tranche
            non_lues.append((borne, curseur))
            logger.error(f"RIAA : tranche {borne} → {curseur} NON LUE après {essais} essais")
            print(f"  {borne} → {curseur} : NON LUE — corpus incomplet sur cette fenêtre")
            curseur, essais = borne, 0
            continue
        essais = 0
        if resultats:
            # Ce qu'une tranche tronquée a rendu est BON, seulement partiel : on
            # le garde avant de redécouper, la dédup absorbe le recouvrement.
            # Tant que la boucle tourne, le corpus est incomplet PAR
            # CONSTRUCTION : on le dit, et la fin de run efface ou remplace le
            # motif. Un balayage tué en route laisse donc « en cours », ce qui
            # est exactement vrai — c'est ce que le panneau doit montrer plutôt
            # qu'une coche verte héritée de la dernière tranche écrite.
            clean_avant = total_clean
            total_clean, ajoutees = _merge_certif_csv(
                _flatten_records(resultats),
                backup=(fusions == 0),
                partial=f"balayage {d0} → {d1} en cours (tranche {tranches})",
            )
            nouvelles = total_clean - clean_avant
            fusions += 1
            total_ajoutees += ajoutees
        print(
            f"  {jour_cli(borne)} → {jour_cli(curseur)} : {vues} vue(s), "
            f"{nouvelles} nouvelle(s) certif, {ajoutees:+d} ligne(s) au brut"
            + (" — TRONQUÉE" if scraper.tronque else "")
        )

        if scraper.tronque and (curseur - borne).days > _JOURS_MIN:
            jours = ajuster_tranche(jours, vues, tronque=True, cible=cible)
            print(f"    ↳ tranche incomplète, redécoupage à {jours} jour(s)")
            continue  # le curseur NE bouge PAS : la tranche est à refaire

        if scraper.tronque:
            # Une seule journée dépasse le plafond : indivisible, on le DIT
            # plutôt que de la compter pour vue.
            irreductibles.append(borne)
            logger.error(
                f"RIAA : la journée du {borne} dépasse à elle seule le plafond du "
                "site — corpus incomplet ce jour-là"
            )

        curseur = borne
        jours = ajuster_tranche(jours, vues, cible=cible)

        if total_vues == 0 and tranches >= _TRANCHES_A_VIDE:
            print(f"❌ {tranches} tranches sans la moindre ligne — période vide ou accès bloqué")
            return False

    if total_vues == 0:
        print("❌ Aucune certification vue — période réellement vide, ou accès bloqué")
        return False

    print(
        f"✅ {tranches} tranche(s), {total_vues} vue(s), "
        f"{total_clean - clean_depart} nouvelle(s) certification(s) "
        f"(clean {clean_depart} → {total_clean}), {total_ajoutees:+d} ligne(s) au brut"
    )
    motifs = []
    if irreductibles:
        jours_txt = ", ".join(str(j) for j in irreductibles[:5])
        print(f"⚠️  {len(irreductibles)} journée(s) restée(s) tronquée(s) : {jours_txt}")
        motifs.append(f"{len(irreductibles)} journée(s) tronquée(s) : {jours_txt}")
    if non_lues:
        fen = ", ".join(f"{a}→{b}" for a, b in non_lues[:5])
        print(f"⚠️  {len(non_lues)} tranche(s) NON LUE(S) : {fen}")
        motifs.append(f"{len(non_lues)} tranche(s) non lue(s) : {fen}")

    # EFFACE le « en cours » quand tout est passé, le REMPLACE sinon. Les deux
    # moitiés comptent : sans l'effacement, le drapeau du dernier balayage
    # collerait au suivant et le panneau crierait pour toujours.
    _write_riaa_meta(source="GLOBAL", partial=" ; ".join(motifs))
    return not motifs


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
        if (backup := cert_store.sauvegarder(CERTIF_CSV)) is not None:
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
        action="append",
        default=None,
        metavar="NOM",
        help="Récupérer les certifs RIAA d'un artiste (fusion dans certif_riaa.csv). "
        "RÉPÉTABLE : un membre de groupe est crédité sous son nom ET sous celui "
        "du groupe, les deux se cherchent donc en une fois",
    )
    parser.add_argument(
        "--clean", action="store_true", help="Nettoie certif_riaa.csv (dédup + vides) sans scraper"
    )
    parser.add_argument(
        "--from",
        dest="debut",
        metavar=FORMAT_JOUR_CLI,
        help="Rescraper une PÉRIODE précise (avec --to) : sert à combler un trou "
        "signalé par la validation, sans repartir de la dernière date connue",
    )
    parser.add_argument("--to", dest="fin", metavar=FORMAT_JOUR_CLI, help="Fin de la période")
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
        rapport = clean_certif_csv(apply=not args.dry_run)
        print(format_clean_report(rapport))
        # Un rapport porteur d'`error` (« brut vide, rien à nettoyer ») sortait
        # en 0 : la GUI concluait au succès et proposait « Appliquer » pour une
        # opération qui ne pouvait rien faire.
        sys.exit(1 if rapport.get("error") else 0)

    if args.debut or args.fin:
        if not (args.debut and args.fin):
            print("❌ --from et --to vont ensemble")
            sys.exit(2)
        sys.exit(0 if fetch_periode(args.debut, args.fin) else 1)

    # Récup par artiste : CSV-centré, pas besoin de la base sqlite
    if args.artist:
        # Un seul nom en échec ne condamne pas les autres : le code de sortie
        # dit « au moins un nom a rendu quelque chose », pas « tous ».
        ok = False
        for nom in args.artist:
            ok = fetch_artist(nom) or ok
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
