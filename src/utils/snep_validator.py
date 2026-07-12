"""Validateur du CSV maître SNEP — détection de trous et anomalies.

100 % pandas, déterministe, sans LLM (cf. décision projet : repérer des trous
dans un CSV est du travail déterministe ; un modèle local serait plus lent,
non-déterministe et pourrait halluciner des trous inexistants).

Vérifications effectuées :
  - Intégrité structurelle : nb de colonnes, lignes malformées, artefacts tab,
    champs critiques vides (Interprète / Titre / Certification).
  - Doublons exacts (artiste + titre + certif + date de constat).
  - Dates : échecs de parsing, plage couverte, fraîcheur (date la plus récente).
  - Couverture mensuelle des années cibles (2025/2026 par défaut) : mois à 0
    certification, mois suspects (couverture faible).
  - Valeurs hors-référentiel pour Catégorie et Certification.

Utilisable en CLI (`python -m src.utils.snep_validator [chemin.csv]`) ou depuis
la GUI via `validate_snep_csv(path)` puis `format_report(report)`.
"""

from __future__ import annotations

import io
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

# Réutilise la réparation de séparateurs (fonction pure partagée)
from src.utils.cert_normalize import repair_extra_separators

HEADER_COLS = [
    "Interprète",
    "Titre",
    "Éditeur / Distributeur",
    "Catégorie",
    "Certification",
    "Date de sortie",
    "Date de constat",
]
EXPECTED_NCOLS = 7

VALID_CATEGORIES = {"Singles", "Albums", "Vidéos", "Single", "Album", "Vidéo"}
VALID_LEVELS = {
    "Or",
    "Double Or",
    "Triple Or",
    "Platine",
    "Double Platine",
    "Triple Platine",
    "Diamant",
    "Double Diamant",
    "Triple Diamant",
    "Quadruple Diamant",
}

# Seuil en dessous duquel un mois est jugé « suspect » (possiblement incomplet).
LOW_MONTH_THRESHOLD = 3

# En vérification COMPLÈTE, on ne signale les trous mensuels que pour les
# années « actives » (≥ ce nombre de certifs/an). Sous ce seuil, une année est
# naturellement clairsemée (décennies anciennes) et ses mois vides ne sont pas
# des anomalies — elle reste visible dans le tableau par année.
MEANINGFUL_YEAR_THRESHOLD = 12


def _load_raw_df(csv_path: Path) -> tuple[pd.DataFrame, list[str], int]:
    """Charge le CSV de façon robuste. Retourne (df, lignes_malformées, nb_réparées)."""
    raw_text = None
    for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            raw_text = csv_path.read_text(encoding=enc)
            break
        except UnicodeDecodeError:
            continue
    if raw_text is None:
        raise ValueError("Encodage illisible")

    raw_text = raw_text.replace("\x00", "")
    raw_text, repaired = repair_extra_separators(raw_text)

    # Repérer les lignes encore malformées (mauvais nombre de colonnes) AVANT le parse
    malformed = []
    lines = raw_text.splitlines()
    if lines:
        for i, line in enumerate(lines[1:], start=2):  # ligne 1 = header
            if not line.strip():
                continue
            # On compte les ';' hors champs quotés de façon simple
            ncols = len(next(csv_split(line)))
            if ncols != EXPECTED_NCOLS:
                malformed.append(f"L{i}: {ncols} colonnes — {line[:80]}")

    df = pd.read_csv(
        io.StringIO(raw_text),
        sep=";",
        dtype=str,
        na_values=["", "N/A", "null", "None"],
        on_bad_lines="skip",
    )
    df.columns = [c.strip().replace("﻿", "") for c in df.columns]
    return df, malformed, repaired


def csv_split(line: str):
    """Génère les champs d'une ligne CSV ';' en respectant les guillemets."""
    import csv as _csv

    yield from _csv.reader([line], delimiter=";", quotechar='"')


def _col(df: pd.DataFrame, idx: int) -> pd.Series:
    """Accès colonne par index (robuste aux soucis d'encodage du header)."""
    if idx < len(df.columns):
        return df.iloc[:, idx].astype("string")
    return pd.Series([pd.NA] * len(df), dtype="string")


def validate_snep_csv(
    csv_path: str | Path,
    target_years: tuple[int, ...] | None = None,
    recent_years: tuple[int, ...] = (2025, 2026),
) -> dict:
    """Analyse le CSV maître SNEP et retourne un rapport structuré.

    target_years=None  → vérification COMPLÈTE : trous mensuels scannés sur
                         toutes les années actives + tableau de comptes par année.
    target_years=(...) → scan limité à ces années (mode ciblé/rapide).
    recent_years        → années pour lesquelles on signale aussi les mois à
                         faible couverture (signal de scrape récent incomplet).
    """
    csv_path = Path(csv_path)
    full_mode = target_years is None
    report: dict = {
        "path": str(csv_path),
        "ok": False,
        "errors": [],
        "warnings": [],
        "stats": {},
        "mode": "complète" if full_mode else "ciblée",
        "target_years": list(target_years) if target_years else [],
        "recent_years": list(recent_years),
        "per_year": {},
        "missing_years": [],
        "month_gaps": [],
        "low_months": [],
        "duplicates": [],
        "malformed": [],
        "tab_artifacts": 0,
        "empty_critical": 0,
        "corrupted_apostrophes": [],
        "corrupted_apostrophes_count": 0,
        "date_parse_failures": 0,
        "invalid_categories": [],
        "invalid_levels": [],
        "casing_categories": [],
        "casing_levels": [],
        "date_range": None,
        "latest_constat": None,
    }

    if not csv_path.exists():
        report["errors"].append(f"Fichier introuvable : {csv_path}")
        return report

    try:
        df, malformed, repaired = _load_raw_df(csv_path)
    except Exception as e:
        report["errors"].append(f"Chargement impossible : {e}")
        return report

    report["malformed"] = malformed
    report["stats"]["repaired_lines"] = repaired
    report["stats"]["n_rows"] = int(len(df))
    report["stats"]["n_cols"] = int(len(df.columns))

    if len(df.columns) != EXPECTED_NCOLS:
        report["warnings"].append(
            f"{len(df.columns)} colonnes au lieu de {EXPECTED_NCOLS} attendues"
        )

    artist = _col(df, 0).str.strip()
    title = _col(df, 1).str.strip()
    category = _col(df, 3).str.strip()
    level = _col(df, 4).str.strip()
    constat_raw = _col(df, 6).str.strip()

    # Artefacts tabulation (ex: "THE WEEKND\t")
    tab_mask = pd.Series(False, index=df.index)
    for s in (_col(df, i) for i in range(min(len(df.columns), EXPECTED_NCOLS))):
        tab_mask |= s.fillna("").str.contains("\t", regex=False)
    report["tab_artifacts"] = int(tab_mask.sum())

    # Champs critiques vides
    empty_mask = artist.isna() | (artist == "") | title.isna() | (title == "")
    report["empty_critical"] = int(empty_mask.sum())

    # Apostrophes corrompues : '?' collé entre deux lettres (corruption SNEP
    # des vieilles entrées, ex: L?empire, QU?IL). Les vrais '?' (après espace
    # ou en fin) ne matchent pas. Le nettoyeur les restaure (?→').
    apo_pat = r"[^\W\d_]\?[^\W\d_]"
    apo_mask = artist.fillna("").str.contains(apo_pat, regex=True, na=False) | title.fillna(
        ""
    ).str.contains(apo_pat, regex=True, na=False)
    report["corrupted_apostrophes_count"] = int(apo_mask.sum())
    if apo_mask.any():
        ex = (artist.fillna("") + " — " + title.fillna(""))[apo_mask].head(15).tolist()
        report["corrupted_apostrophes"] = ex
        report["warnings"].append(
            f"{report['corrupted_apostrophes_count']} entrée(s) avec caractère "
            f"corrompu (?) — nettoyeur (élision/œ auto) puis revue manuelle du reste"
        )

    # Dates de constat
    constat = pd.to_datetime(constat_raw, format="%d/%m/%Y", errors="coerce")
    report["date_parse_failures"] = int(constat.isna().sum() - constat_raw.isna().sum())
    valid_dates = constat.dropna()
    if not valid_dates.empty:
        report["date_range"] = f"{valid_dates.min():%d/%m/%Y} → {valid_dates.max():%d/%m/%Y}"
        report["latest_constat"] = f"{valid_dates.max():%d/%m/%Y}"
        days_stale = (datetime.now() - valid_dates.max().to_pydatetime()).days
        report["stats"]["days_since_latest"] = days_stale
        if days_stale > 21:
            report["warnings"].append(
                f"Certif. la plus récente date d'il y a {days_stale} jours "
                f"({report['latest_constat']}) — base possiblement en retard"
            )

    # Doublons exacts (clé métier)
    key = (
        artist.fillna("").str.upper().str.strip()
        + " | "
        + title.fillna("").str.upper().str.strip()
        + " | "
        + level.fillna("")
        + " | "
        + constat_raw.fillna("")
    )
    dup_mask = key.duplicated(keep="first") & (artist.fillna("") != "")
    dup_count = int(dup_mask.sum())
    report["stats"]["duplicates"] = dup_count
    if dup_count:
        ex = []
        for k in key[dup_mask].head(10):
            ex.append(k.replace(" |  | ", " | "))
        report["duplicates"] = ex

    # Valeurs hors référentiel — comparaison insensible à la casse pour ne pas
    # confondre un niveau réellement inconnu et une simple variante de casse
    # (ex: "Double diamant" vs "Double Diamant", fréquent dans les vieilles
    # lignes SNEP). Les variantes de casse sont signalées à part (info).
    cat_known = {c.lower() for c in VALID_CATEGORIES}
    lvl_known = {lvl.lower() for lvl in VALID_LEVELS}
    cats = set(category.dropna())
    lvls = set(level.dropna())
    bad_cat = sorted(c for c in cats if c.lower() not in cat_known)
    bad_lvl = sorted(lvl for lvl in lvls if lvl.lower() not in lvl_known)
    case_cat = sorted(c for c in cats if c not in VALID_CATEGORIES and c.lower() in cat_known)
    case_lvl = sorted(lvl for lvl in lvls if lvl not in VALID_LEVELS and lvl.lower() in lvl_known)
    report["invalid_categories"] = bad_cat
    report["invalid_levels"] = bad_lvl
    report["casing_categories"] = case_cat
    report["casing_levels"] = case_lvl

    # Couverture temporelle
    if not valid_dates.empty:
        months = valid_dates.dt.to_period("M")
        counts = months.value_counts().sort_index()
        current_month = pd.Period(datetime.now(), freq="M")
        year_series = valid_dates.dt.year

        y_min = int(year_series.min())
        y_max = int(year_series.max())

        # Tableau de comptes par année sur toute la plage (0 = année absente)
        per_year = {y: int((year_series == y).sum()) for y in range(y_min, y_max + 1)}
        report["per_year"] = per_year
        # Année à 0 dont un voisin immédiat est actif = trou notable (bord de
        # lacune ou année isolée manquante). En données continues : liste vide.
        report["missing_years"] = [
            y
            for y, c in per_year.items()
            if c == 0 and (per_year.get(y - 1, 0) > 0 or per_year.get(y + 1, 0) > 0)
        ]

        # Années à scanner pour les trous mensuels
        if full_mode:
            scan_years = [y for y, c in per_year.items() if c >= MEANINGFUL_YEAR_THRESHOLD]
        else:
            scan_years = list(target_years)

        for year in scan_years:
            for m in range(1, 13):
                per = pd.Period(f"{year}-{m:02d}", freq="M")
                if per > current_month:
                    continue
                if int(counts.get(per, 0)) == 0:
                    report["month_gaps"].append(f"{year}-{m:02d}")

        # Mois à faible couverture : seulement sur les années récentes
        for year in recent_years:
            for m in range(1, 13):
                per = pd.Period(f"{year}-{m:02d}", freq="M")
                if per > current_month:
                    continue
                n = int(counts.get(per, 0))
                if 0 < n < LOW_MONTH_THRESHOLD:
                    report["low_months"].append(f"{year}-{m:02d} ({n})")

        for year in recent_years:
            report["stats"][f"count_{year}"] = int((year_series == year).sum())

    # Verdict
    report["ok"] = (
        not report["errors"]
        and not malformed
        and report["empty_critical"] == 0
        and dup_count == 0
        and not bad_cat
        and not bad_lvl
    )
    return report


def format_report(report: dict) -> str:
    """Met en forme le rapport pour affichage console / GUI."""
    L = []
    L.append("=" * 52)
    L.append(f"🔎 VALIDATION DU CSV MAÎTRE SNEP — vérif {report.get('mode', 'ciblée')}")
    L.append("=" * 52)
    L.append(f"Fichier : {report['path']}")

    s = report["stats"]
    if report["errors"]:
        for e in report["errors"]:
            L.append(f"❌ {e}")
        return "\n".join(L)

    L.append(f"Lignes : {s.get('n_rows', 0)}   Colonnes : {s.get('n_cols', 0)}")
    if report.get("date_range"):
        L.append(f"Période couverte : {report['date_range']}")
    if s.get("days_since_latest") is not None:
        L.append(
            f"Certif. la plus récente : {report['latest_constat']} "
            f"(il y a {s['days_since_latest']} j)"
        )
    for y in report.get("recent_years", []):
        if f"count_{y}" in s:
            L.append(f"  • {y} : {s[f'count_{y}']} certifications")

    L.append("")
    L.append(
        f"{'✅' if report['ok'] else '⚠️'} Verdict global : "
        f"{'RAS' if report['ok'] else 'anomalies détectées'}"
    )

    def section(title, items, limit=15):
        if items:
            L.append("")
            L.append(f"── {title} ({len(items)}) ──")
            for it in items[:limit]:
                L.append(f"  • {it}")
            if len(items) > limit:
                L.append(f"  … et {len(items) - limit} autre(s)")

    if report["tab_artifacts"]:
        L.append("")
        L.append(
            f"🩹 Artefacts tabulation : {report['tab_artifacts']} ligne(s) "
            f"(nettoyés à l'import, mais présents dans le CSV)"
        )
    if report["empty_critical"]:
        L.append(f"❌ Champs critiques vides (artiste/titre) : {report['empty_critical']}")
    if report.get("corrupted_apostrophes_count"):
        L.append(
            f"🩹 Caractères corrompus (?) : {report['corrupted_apostrophes_count']} "
            f"entrée(s) — « Nettoyer » restaure élisions/œ, le reste à vérifier"
        )
    if report["date_parse_failures"]:
        L.append(f"⚠️ Dates de constat illisibles : {report['date_parse_failures']}")
    if s.get("repaired_lines"):
        L.append(f"🩹 Lignes réparées (séparateur en trop) : {s['repaired_lines']}")

    section("Lignes malformées", report["malformed"])
    section("Caractères corrompus (?) — à vérifier", report.get("corrupted_apostrophes", []))
    section("Doublons exacts", report["duplicates"])
    section("Catégories hors référentiel", report["invalid_categories"])
    section("Niveaux hors référentiel", report["invalid_levels"])
    section("Catégories — variantes de casse (à normaliser)", report.get("casing_categories", []))
    section("Niveaux — variantes de casse (à normaliser)", report.get("casing_levels", []))

    # Années entièrement absentes entre deux années actives (vrais trous)
    if report.get("missing_years"):
        section(
            "Années ENTIÈREMENT absentes (trou)",
            [str(y) for y in report["missing_years"]],
            limit=40,
        )

    # Trous mensuels groupés par année (lisible même sur tout l'historique)
    gaps = report.get("month_gaps", [])
    if gaps:
        from collections import defaultdict

        by_year = defaultdict(list)
        for g in gaps:
            y, mo = g.split("-")
            by_year[y].append(mo)
        L.append("")
        L.append(f"── Mois SANS certification ({len(gaps)} sur années actives) ──")
        for y in sorted(by_year):
            L.append(f"  • {y} : {len(by_year[y])} mois manquant(s) — " f"{', '.join(by_year[y])}")

    section("Mois à faible couverture (années récentes)", report["low_months"], limit=24)

    # Tableau de comptes par année (vue de complétude sur tout l'historique)
    per_year = report.get("per_year", {})
    if per_year:
        L.append("")
        L.append(f"── Comptes par année ({min(per_year)}–{max(per_year)}) ──")
        line = "  "
        for y in sorted(per_year):
            cell = f"{y}:{per_year[y]}"
            if len(line) + len(cell) + 1 > 50:
                L.append(line)
                line = "  "
            line += cell + "  "
        if line.strip():
            L.append(line)

    L.append("")
    L.append("=" * 52)
    return "\n".join(L)


def _default_csv_path() -> Path:
    from src.config import DATA_PATH

    return Path(DATA_PATH) / "certifications" / "snep" / "certif-.csv"


def main():
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else _default_csv_path()
    report = validate_snep_csv(path)
    print(format_report(report))


if __name__ == "__main__":
    main()
