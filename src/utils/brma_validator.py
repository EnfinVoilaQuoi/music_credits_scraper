"""Validateur du CSV BRMA (certifications belges Ultratop). pandas pur, sans LLM.

Pendant SNEP a `snep_validator` ; ici on adapte au format Ultratop :
  - séparateur VIRGULE, colonnes nommées
    (artist, title, category, certification_level, certification_date, …) ;
  - dates ISO AAAA-MM-JJ ;
  - catégories `singles`/`albums`, niveaux `Or`/`Platine`/`Diamant`…

Vérifie : champs vides (artiste/titre/niveau), doublons, dates illisibles,
fraîcheur, comptes par année (tout l'historique), trous mensuels sur les années
actives, et niveaux/catégories hors référentiel (insensible à la casse).
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd

REQUIRED_COLS = ["artist", "title", "category", "certification_level", "certification_date"]
VALID_CATEGORIES = {"singles", "albums"}
VALID_LEVELS = {
    "or",
    "platine",
    "double platine",
    "triple platine",
    "quadruple platine",
    "diamant",
    "double diamant",
    "triple diamant",
}
# Ultratop note les multi-platine/or/diamant en multiplicateur (ex: '2x Platine',
# '12x Platine') — niveaux VALIDES à reconnaître en plus du référentiel ci-dessus.
_MULTI_LEVEL_RE = re.compile(r"^\d+\s*x\s+(or|platine|diamant)$", re.IGNORECASE)


def _level_known(level: str, lvl_known: set) -> bool:
    l = (level or "").strip()
    return l.lower() in lvl_known or bool(_MULTI_LEVEL_RE.match(l))


MEANINGFUL_YEAR_THRESHOLD = 12
LOW_MONTH_THRESHOLD = 3


def _load(csv_path: Path) -> pd.DataFrame:
    for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            df = pd.read_csv(csv_path, encoding=enc, dtype=str)
            df.columns = [c.strip().lstrip("﻿") for c in df.columns]
            return df
        except UnicodeDecodeError:
            continue
    raise ValueError("Encodage illisible")


def validate_brma_csv(csv_path: str | Path, recent_years: tuple[int, ...] = (2025, 2026)) -> dict:
    csv_path = Path(csv_path)
    report: dict = {
        "path": str(csv_path),
        "ok": False,
        "errors": [],
        "warnings": [],
        "stats": {},
        "recent_years": list(recent_years),
        "per_year": {},
        "missing_years": [],
        "month_gaps": [],
        "low_months": [],
        "duplicates": [],
        "empty_critical": 0,
        "empty_titles": 0,
        "empty_levels": 0,
        "date_parse_failures": 0,
        "invalid_categories": [],
        "invalid_levels": [],
        "casing_levels": [],
        "date_range": None,
        "latest_date": None,
    }

    if not csv_path.exists():
        report["errors"].append(f"Fichier introuvable : {csv_path}")
        return report

    try:
        df = _load(csv_path)
    except Exception as e:
        report["errors"].append(f"Chargement impossible : {e}")
        return report

    missing_cols = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing_cols:
        report["errors"].append(f"Colonnes manquantes : {missing_cols}")
        return report

    report["stats"]["n_rows"] = int(len(df))

    def col(name: str) -> pd.Series:
        return df[name].astype("string").str.strip()

    artist = col("artist")
    title = col("title")
    category = col("category")
    level = col("certification_level")
    date_raw = col("certification_date")

    # Champs critiques : artiste vide = critique ; titre vide = souvent une
    # COMPILATION (nom dans 'artist', pas de titre) → warning, pas bloquant.
    report["empty_critical"] = int((artist.isna() | (artist == "")).sum())
    report["empty_titles"] = int((title.isna() | (title == "")).sum())
    report["empty_levels"] = int((level.isna() | (level == "")).sum())

    # Dates (ISO)
    date = pd.to_datetime(date_raw, format="%Y-%m-%d", errors="coerce")
    report["date_parse_failures"] = int(date.isna().sum() - date_raw.isna().sum())
    valid_dates = date.dropna()
    if not valid_dates.empty:
        report["date_range"] = f"{valid_dates.min():%d/%m/%Y} → {valid_dates.max():%d/%m/%Y}"
        report["latest_date"] = f"{valid_dates.max():%d/%m/%Y}"
        days = (datetime.now() - valid_dates.max().to_pydatetime()).days
        report["stats"]["days_since_latest"] = days
        if days > 45:
            report["warnings"].append(
                f"Certif. la plus récente il y a {days} j ({report['latest_date']}) "
                f"— base possiblement en retard"
            )

    # Doublons (artiste + titre + CATÉGORIE + niveau + date). La catégorie est
    # indispensable : un même nom peut être certifié en single ET en album (ex:
    # Amy Macdonald « This Is The Life ») → ce ne sont pas des doublons.
    key = (
        artist.fillna("").str.upper()
        + " | "
        + title.fillna("").str.upper()
        + " | "
        + category.fillna("")
        + " | "
        + level.fillna("")
        + " | "
        + date_raw.fillna("")
    )
    dup_mask = key.duplicated(keep="first") & (artist.fillna("") != "")
    report["stats"]["duplicates"] = int(dup_mask.sum())
    if dup_mask.any():
        report["duplicates"] = [k for k in key[dup_mask].head(12)]

    # Référentiel (insensible à la casse) — niveaux vides traités à part
    cats = set(c for c in category.dropna() if c)
    lvls = set(l for l in level.dropna() if l)
    cat_known = {c.lower() for c in VALID_CATEGORIES}
    lvl_known = {l.lower() for l in VALID_LEVELS}
    report["invalid_categories"] = sorted(c for c in cats if c.lower() not in cat_known)
    report["invalid_levels"] = sorted(l for l in lvls if not _level_known(l, lvl_known))

    # Couverture temporelle
    if not valid_dates.empty:
        counts = valid_dates.dt.to_period("M").value_counts().sort_index()
        current_month = pd.Period(datetime.now(), freq="M")
        years = valid_dates.dt.year
        y_min, y_max = int(years.min()), int(years.max())
        per_year = {y: int((years == y).sum()) for y in range(y_min, y_max + 1)}
        report["per_year"] = per_year
        report["missing_years"] = [
            y
            for y, c in per_year.items()
            if c == 0 and (per_year.get(y - 1, 0) > 0 or per_year.get(y + 1, 0) > 0)
        ]
        scan_years = [y for y, c in per_year.items() if c >= MEANINGFUL_YEAR_THRESHOLD]
        for y in scan_years:
            for m in range(1, 13):
                per = pd.Period(f"{y}-{m:02d}", freq="M")
                if per > current_month:
                    continue
                if int(counts.get(per, 0)) == 0:
                    report["month_gaps"].append(f"{y}-{m:02d}")
        for y in recent_years:
            for m in range(1, 13):
                per = pd.Period(f"{y}-{m:02d}", freq="M")
                if per > current_month:
                    continue
                n = int(counts.get(per, 0))
                if 0 < n < LOW_MONTH_THRESHOLD:
                    report["low_months"].append(f"{y}-{m:02d} ({n})")
        for y in recent_years:
            report["stats"][f"count_{y}"] = int((years == y).sum())

    # Verdict : artiste vide / doublons / référentiel sont bloquants.
    # Titres vides (compilations) et niveaux vides résiduels = warnings.
    report["ok"] = (
        not report["errors"]
        and report["empty_critical"] == 0
        and report["stats"].get("duplicates", 0) == 0
        and not report["invalid_categories"]
        and not report["invalid_levels"]
    )
    return report


def format_report(report: dict) -> str:
    L = []
    L.append("=" * 52)
    L.append("🔎 VALIDATION DU CSV BRMA (Ultratop / Belgique)")
    L.append("=" * 52)
    L.append(f"Fichier : {report['path']}")

    if report["errors"]:
        for e in report["errors"]:
            L.append(f"❌ {e}")
        return "\n".join(L)

    s = report["stats"]
    L.append(f"Lignes : {s.get('n_rows', 0)}")
    if report.get("date_range"):
        L.append(f"Période couverte : {report['date_range']}")
    if s.get("days_since_latest") is not None:
        L.append(
            f"Certif. la plus récente : {report['latest_date']} "
            f"(il y a {s['days_since_latest']} j)"
        )
    for y in report["recent_years"]:
        if f"count_{y}" in s:
            L.append(f"  • {y} : {s[f'count_{y}']} certifications")

    L.append("")
    L.append(
        f"{'✅' if report['ok'] else '⚠️'} Verdict global : "
        f"{'RAS' if report['ok'] else 'anomalies détectées'}"
    )

    if report["empty_critical"]:
        L.append(f"❌ Artiste vide : {report['empty_critical']} ligne(s)")
    if report.get("empty_titles"):
        L.append(
            f"ℹ️ Titre vide : {report['empty_titles']} ligne(s) "
            f"(souvent des compilations — nom dans 'artiste')"
        )
    if report["empty_levels"]:
        L.append(f"⚠️ Niveau de certification vide : {report['empty_levels']} ligne(s)")
    if report["date_parse_failures"]:
        L.append(f"⚠️ Dates illisibles : {report['date_parse_failures']}")

    def section(title, items, limit=15):
        if items:
            L.append("")
            L.append(f"── {title} ({len(items)}) ──")
            for it in items[:limit]:
                L.append(f"  • {it}")
            if len(items) > limit:
                L.append(f"  … et {len(items) - limit} autre(s)")

    section("Doublons exacts", report["duplicates"])
    section("Catégories hors référentiel", report["invalid_categories"])
    section("Niveaux hors référentiel", report["invalid_levels"])
    section("Niveaux — variantes de casse", report.get("casing_levels", []))
    if report.get("missing_years"):
        section(
            "Années ENTIÈREMENT absentes (trou)",
            [str(y) for y in report["missing_years"]],
            limit=40,
        )

    gaps = report.get("month_gaps", [])
    if gaps:
        by_year = defaultdict(list)
        for g in gaps:
            y, mo = g.split("-")
            by_year[y].append(mo)
        L.append("")
        L.append(f"── Mois SANS certification ({len(gaps)} sur années actives) ──")
        for y in sorted(by_year):
            L.append(f"  • {y} : {len(by_year[y])} mois — {', '.join(by_year[y])}")

    section("Mois à faible couverture (années récentes)", report["low_months"], limit=24)

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

    return Path(DATA_PATH) / "certifications" / "brma" / "certif_brma.csv"


def main():
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else _default_csv_path()
    print(format_report(validate_brma_csv(path)))


if __name__ == "__main__":
    main()
