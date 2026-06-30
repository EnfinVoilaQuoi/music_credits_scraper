"""Validateur du CSV RIAA (certif_riaa.csv). pandas pur, sans LLM.

Pendant SNEP/BRMA ; ici adapté au format RIAA (Ultratop US) :
  - séparateur VIRGULE, colonnes capitalisées (Artist, Title, Certification_Date,
    Format_Type, Certification_Type, …) ;
  - dates US « October 17, 2017 » (ou ISO pour les lignes scrapées récemment) ;
  - niveaux anglais : Gold, Platinum, Nx Platinum, Diamond, Nx Multi-Platinum.

Vérifie : champs vides (artiste/titre/niveau), doublons (niveau normalisé),
dates illisibles, fraîcheur, comptes par année, trous mensuels (années actives),
niveaux hors référentiel.
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

REQUIRED_COLS = ["Artist", "Title", "Certification_Date", "Certification_Type"]
MEANINGFUL_YEAR_THRESHOLD = 12
LOW_MONTH_THRESHOLD = 3

_MULTI_RE = re.compile(r'^\d+\s*x\s*(multi-?)?platinum$', re.I)


def _to_iso(s: str) -> str:
    s = (s or '').strip()
    if not s or s.lower() == 'none':
        return ''
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', s):
        return s
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s.title() if ',' in s else s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ''


def _level_norm(l: str) -> str:
    """« 4x Multi-Platinum » → « 4X PLATINUM » (clé de dédup/référentiel)."""
    l = (l or '').strip()
    m = re.match(r'(\d+)\s*x\s*multi-?platinum', l, re.I)
    if m:
        return f"{m.group(1)}X PLATINUM"
    if re.fullmatch(r'multi-?platinum', l, re.I):
        return "PLATINUM"
    return re.sub(r'\s+', ' ', l).strip().upper()


def _level_known(l: str) -> bool:
    n = (l or '').strip().lower()
    return n in {"gold", "platinum", "diamond", "multi-platinum", "multi platinum"} \
        or bool(_MULTI_RE.match(n))


def _load(csv_path: Path) -> pd.DataFrame:
    for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            df = pd.read_csv(csv_path, encoding=enc, dtype=str).fillna('')
            df.columns = [c.strip().lstrip("﻿") for c in df.columns]
            return df
        except UnicodeDecodeError:
            continue
    raise ValueError("Encodage illisible")


def validate_riaa_csv(csv_path: str | Path,
                      recent_years: tuple[int, ...] = (2024, 2025, 2026)) -> dict:
    csv_path = Path(csv_path)
    report: dict = {
        "path": str(csv_path), "ok": False, "errors": [], "warnings": [], "stats": {},
        "recent_years": list(recent_years), "per_year": {}, "missing_years": [],
        "month_gaps": [], "low_months": [], "duplicates": [],
        "empty_critical": 0, "empty_titles": 0, "empty_levels": 0,
        "date_parse_failures": 0, "invalid_levels": [], "formats": {},
        "date_range": None, "latest_date": None,
    }
    if not csv_path.exists():
        report["errors"].append(f"Fichier introuvable : {csv_path}")
        return report
    try:
        df = _load(csv_path)
    except Exception as e:
        report["errors"].append(f"Chargement impossible : {e}")
        return report

    cmap = {c.lower(): c for c in df.columns}

    def col(name: str) -> pd.Series:
        real = cmap.get(name.lower())
        return df[real].astype("string").str.strip() if real else pd.Series([""] * len(df), dtype="string")

    missing = [c for c in REQUIRED_COLS if c.lower() not in cmap]
    if missing:
        report["errors"].append(f"Colonnes manquantes : {missing}")
        return report

    report["stats"]["n_rows"] = int(len(df))
    artist = col("Artist"); title = col("Title")
    level = col("Certification_Type"); fmt = col("Format_Type")
    date_raw = col("Certification_Date")

    report["empty_critical"] = int((artist.isna() | (artist == "")).sum())
    report["empty_titles"] = int((title.isna() | (title == "")).sum())
    report["empty_levels"] = int((level.isna() | (level == "")).sum())

    # Dates (US ou ISO) → ISO
    iso = date_raw.map(_to_iso)
    date = pd.to_datetime(iso.where(iso != ""), format="%Y-%m-%d", errors="coerce")
    report["date_parse_failures"] = int((iso == "").sum())
    valid = date.dropna()
    if not valid.empty:
        report["date_range"] = f"{valid.min():%d/%m/%Y} → {valid.max():%d/%m/%Y}"
        report["latest_date"] = f"{valid.max():%d/%m/%Y}"
        days = (datetime.now() - valid.max().to_pydatetime()).days
        report["stats"]["days_since_latest"] = days
        if days > 60:
            report["warnings"].append(
                f"Certif. la plus récente il y a {days} j ({report['latest_date']}) — "
                f"base à backfiller")

    # Doublons (artiste+titre+format+niveau normalisé+date)
    key = (artist.fillna("").str.upper() + " | " + title.fillna("").str.upper() + " | "
           + fmt.fillna("").str.upper() + " | " + level.fillna("").map(_level_norm)
           + " | " + iso)
    dup = key.duplicated(keep="first") & (artist.fillna("") != "")
    report["stats"]["duplicates"] = int(dup.sum())
    if dup.any():
        report["duplicates"] = [k.replace("  ", " ") for k in key[dup].head(12)]

    # Niveaux hors référentiel
    report["invalid_levels"] = sorted(
        set(l for l in level.dropna() if l and not _level_known(l)))
    # Formats (info)
    report["formats"] = {k: int(v) for k, v in fmt[fmt != ""].value_counts().head(12).items()}

    # Couverture temporelle
    if not valid.empty:
        counts = valid.dt.to_period("M").value_counts().sort_index()
        cur = pd.Period(datetime.now(), freq="M")
        years = valid.dt.year
        y0, y1 = int(years.min()), int(years.max())
        per_year = {y: int((years == y).sum()) for y in range(y0, y1 + 1)}
        report["per_year"] = per_year
        report["missing_years"] = [y for y, c in per_year.items()
                                   if c == 0 and (per_year.get(y - 1, 0) > 0 or per_year.get(y + 1, 0) > 0)]
        for y in [y for y, c in per_year.items() if c >= MEANINGFUL_YEAR_THRESHOLD]:
            for m in range(1, 13):
                per = pd.Period(f"{y}-{m:02d}", freq="M")
                if per > cur:
                    continue
                if int(counts.get(per, 0)) == 0:
                    report["month_gaps"].append(f"{y}-{m:02d}")
        for y in recent_years:
            for m in range(1, 13):
                per = pd.Period(f"{y}-{m:02d}", freq="M")
                if per > cur:
                    continue
                n = int(counts.get(per, 0))
                if 0 < n < LOW_MONTH_THRESHOLD:
                    report["low_months"].append(f"{y}-{m:02d} ({n})")
        for y in recent_years:
            report["stats"][f"count_{y}"] = int((years == y).sum())

    report["ok"] = (not report["errors"] and report["empty_critical"] == 0
                    and report["stats"].get("duplicates", 0) == 0
                    and not report["invalid_levels"])
    return report


def format_report(report: dict) -> str:
    L = ["=" * 52, "🔎 VALIDATION DU CSV RIAA (USA)", "=" * 52,
         f"Fichier : {report['path']}"]
    if report["errors"]:
        return "\n".join(L + [f"❌ {e}" for e in report["errors"]])
    s = report["stats"]
    L.append(f"Lignes : {s.get('n_rows', 0)}")
    if report.get("date_range"):
        L.append(f"Période couverte : {report['date_range']}")
    if s.get("days_since_latest") is not None:
        L.append(f"Certif. la plus récente : {report['latest_date']} (il y a {s['days_since_latest']} j)")
    for y in report["recent_years"]:
        if f"count_{y}" in s:
            L.append(f"  • {y} : {s[f'count_{y}']} certifications")
    L.append("")
    L.append(f"{'✅' if report['ok'] else '⚠️'} Verdict global : "
             f"{'RAS' if report['ok'] else 'anomalies détectées'}")
    if report["empty_critical"]:
        L.append(f"❌ Artiste vide : {report['empty_critical']} ligne(s)")
    if report["empty_titles"]:
        L.append(f"ℹ️ Titre vide : {report['empty_titles']} ligne(s)")
    if report["empty_levels"]:
        L.append(f"⚠️ Niveau vide : {report['empty_levels']} ligne(s)")
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
    section("Niveaux hors référentiel", report["invalid_levels"])
    if report.get("missing_years"):
        section("Années ENTIÈREMENT absentes (trou)",
                [str(y) for y in report["missing_years"]], limit=40)
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
    if report.get("formats"):
        L.append("")
        L.append("── Formats (info) ──")
        L.append("  " + ", ".join(f"{k}:{v}" for k, v in report["formats"].items()))
    per_year = report.get("per_year", {})
    if per_year:
        L.append("")
        L.append(f"── Comptes par année ({min(per_year)}–{max(per_year)}) ──")
        line = "  "
        for y in sorted(per_year):
            cell = f"{y}:{per_year[y]}"
            if len(line) + len(cell) + 1 > 50:
                L.append(line); line = "  "
            line += cell + "  "
        if line.strip():
            L.append(line)
    L.append("")
    L.append("=" * 52)
    return "\n".join(L)


def _default_csv_path() -> Path:
    from src.config import DATA_PATH
    return Path(DATA_PATH) / "certifications" / "riaa" / "certif_riaa.csv"


def main():
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else _default_csv_path()
    print(format_report(validate_riaa_csv(path)))


if __name__ == "__main__":
    main()
