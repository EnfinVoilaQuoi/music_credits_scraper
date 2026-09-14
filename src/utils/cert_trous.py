"""Périodes manquantes dans le CSV BRUT d'une source de certification.

Ces quatre-vingts lignes de pandas vivaient dans `CertificationUpdateDialog`,
sans un seul widget autour. Elles n'étaient donc couvertes par rien — la fenêtre
compte 37 méthodes et deux tests, les deux seules `@staticmethod` pures, rendues
telles PRÉCISÉMENT pour être testables. Le geste n'avait simplement pas été
poursuivi.

Le voisin de palier est `cert_rescrape`, qui prend le relais : ce module DIT où
sont les trous, celui-là sait quelles commandes les combleraient.

⚠️ **Deux définitions de « période manquante » coexistent dans le logiciel**, et
elles s'affichent dans la même fenêtre sans se distinguer. Ici, un mois compte
comme suspect en dessous de `_SEUIL_MOIS_MAIGRE` certifications ; les
validateurs, eux, ne signalent que les mois VIDES des années actives. Le second
est un fait, le premier une présomption — c'est voulu, mais il faut le savoir en
lisant les chiffres.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from src.utils.logger import get_logger

logger = get_logger(__name__)

#: Le CSV BRUT de chaque source (accumulation complète, avec sa colonne de date
#: NATIVE). C'est le brut et non le clean : on cherche ce qui n'a jamais été
#: collecté, pas ce que la dérivation a écarté.
BRUTS_PAR_SOURCE: dict[str, tuple[str, str]] = {
    "SNEP": ("snep", "certif-.csv"),
    "BRMA": ("brma", "brma_raw.csv"),
    "RIAA": ("riaa", "riaa_raw.csv"),
    "BPI": ("bpi", "bpi_raw.csv"),
}

#: Nom NATIF de la colonne de date et sens de lecture, par source. Chacune a les
#: siens, et SNEP n'est même pas en anglais : c'est la rançon du brut, qui garde
#: la forme de sa source au lieu de la nôtre.
#:
#: ⚠️ **`dayfirst` est PAR SOURCE, et ce n'est pas un détail.** Il valait `True`
#: pour tout le monde : correct pour le SNEP, qui écrit « JJ/MM/AAAA », FAUX
#: pour les trois autres, qui écrivent en ISO. Mesuré le 2026-09-10 :
#:
#:     SNEP  3 940 / 12 238 lignes lues différemment — et True est le BON choix
#:     BRMA  2 070 /  5 847 lignes  — 35 % de dates fausses, mois inversé
#:     RIAA      0 / 54 638   BPI       0 / 49 377
#:
#: Le zéro de RIAA et BPI n'est pas une absence de risque, c'est un hasard de
#: FORMAT : quand une colonne est uniformément ISO, pandas infère un format
#: unique et ignore `dayfirst` ; dès qu'elle mélange les écritures — le cas de
#: BRMA — il retombe sur un parsing élément par élément où le drapeau reprend
#: effet. Un défaut qui ne se déclenche que selon l'homogénéité des données est
#: exactement celui qu'on ne voit jamais venir.
#:
#: Conséquence concrète : les « périodes manquantes » de BRMA étaient en partie
#: fictives, l'histogramme mensuel étant construit sur des mois inversés.
_LECTURE_DATE = {
    "SNEP": ("Date de constat", True),
    "BRMA": ("certification_date", False),
    "BPI": ("certification_date", False),
    "RIAA": ("Certification_Date", False),
}

#: En dessous, un mois est jugé « possiblement incomplet ». Présomption, pas
#: constat — voir l'avertissement en tête de module.
_SEUIL_MOIS_MAIGRE = 5


def _lire(csv_path: Path) -> pd.DataFrame:
    """Le brut, séparateur auto-détecté (SNEP « ; », les autres « , »)."""
    for encodage in ("utf-8", "latin1"):
        try:
            return pd.read_csv(csv_path, encoding=encodage, sep=None, engine="python")
        except UnicodeDecodeError:
            continue
    return pd.read_csv(csv_path, encoding="latin1", sep=None, engine="python")


def colonne_de_date(df: pd.DataFrame, source: str) -> str | None:
    """La colonne de date de cette source, avec deux replis.

    Le nom attendu d'abord ; sinon le même à la casse près ; sinon la première
    colonne dont le nom contient « date ». Le dernier repli est délibérément
    permissif : mieux vaut analyser la mauvaise colonne et le voir dans les
    résultats que de rendre « colonne introuvable » sur un simple renommage.
    """
    attendu = _LECTURE_DATE.get(source, (None, False))[0]
    if attendu and attendu in df.columns:
        return attendu
    minuscules = {c.lower(): c for c in df.columns}
    if attendu and attendu.lower() in minuscules:
        return minuscules[attendu.lower()]
    return next((c for c in df.columns if "date" in c.lower()), None)


def periodes_manquantes(csv_path: Path, source: str) -> dict:
    """Mois sans certification (ou trop peu) entre la plus vieille et la plus récente.

    Rend `{total, gaps, erreur, date_range, monthly_avg}`. `gaps` ne porte QUE de
    vraies périodes manquantes ; les défauts d'analyse (fichier illisible,
    colonne absente, aucune date valide) vont dans `erreur`. Ils partageaient
    `gaps` auparavant, et l'appelant les affichait comme des périodes manquantes
    — un fichier introuvable comptait pour un « trou ».
    """
    vide = {"total": 0, "gaps": [], "erreur": None, "date_range": None}
    try:
        df = _lire(csv_path)
    except (OSError, ValueError) as e:
        logger.exception(f"Lecture du brut {source}")
        return {**vide, "erreur": f"Erreur de lecture : {e}"}

    if df.empty:
        return vide

    date_col = colonne_de_date(df, source)
    if not date_col:
        return {**vide, "total": len(df), "erreur": "Colonne de date non trouvée"}

    df = df.copy()
    jour_en_tete = _LECTURE_DATE.get(source, (None, False))[1]
    # `format="mixed"` : sans lui, pandas INFÈRE un format unique depuis le
    # premier élément et met en `NaT` tout ce qui ne lui ressemble pas. Le brut
    # RIAA mélange deux écritures — « October 17, 2017 » pour le corpus
    # historique, l'ISO pour tout ce que le scraper a ramené depuis — et
    # l'inférence retenait la première. Mesuré le 2026-09-10 : **26 468 dates
    # perdues sur 54 638**, soit 48 % du corpus, et une analyse qui s'arrêtait
    # net en 2017-10 sans que rien ne le dise. Les lignes n'étaient pas
    # comptées manquantes : elles n'existaient tout simplement plus.
    df[date_col] = pd.to_datetime(
        df[date_col], errors="coerce", dayfirst=jour_en_tete, format="mixed"
    )
    df = df.dropna(subset=[date_col])
    if df.empty:
        return {**vide, "erreur": "Aucune date valide"}

    par_mois = df.groupby(df[date_col].dt.to_period("M")).size()
    if par_mois.empty:
        return {**vide, "total": len(df)}

    debut, fin = par_mois.index.min(), par_mois.index.max()
    # Le mois COURANT n'est pas un trou : il n'est pas fini. Celui d'après non
    # plus, les organismes publiant avec du retard.
    mois_courant = pd.Period(datetime.now(), freq="M")

    trous = []
    for mois in pd.period_range(start=debut, end=fin, freq="M"):
        if mois >= mois_courant:
            continue
        if mois not in par_mois.index:
            trous.append(f"{mois.strftime('%Y-%m')} (0 certifications)")
        elif par_mois[mois] < _SEUIL_MOIS_MAIGRE:
            trous.append(
                f"{mois.strftime('%Y-%m')} ({par_mois[mois]} certifications"
                " - possiblement incomplet)"
            )

    return {
        "total": len(df),
        "gaps": trous,
        "erreur": None,
        "date_range": f"{debut.strftime('%Y-%m')} à {fin.strftime('%Y-%m')}",
        "monthly_avg": float(par_mois.mean()),
    }
