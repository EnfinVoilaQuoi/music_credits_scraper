"""Validateur du CSV BPI (certif_bpi.csv). pandas pur, sans LLM.

Même famille que les validateurs SNEP/BRMA/RIAA, adapté au format BPI :
  - séparateur VIRGULE, colonnes minuscules (artist, title, certification_level,
    certification_date, category, units, format_id/artist_id/title_id…) ;
  - dates déjà ISO (le scraper les normalise) ;
  - niveaux anglais : Silver, Gold, Platinum, Nx Platinum.

**Le référentiel de niveaux n'est pas redéfini ici** : il est demandé à
`cert_normalize.bpi_level_connu`. C'est la leçon du 2026-09-06, où le validateur
RIAA avait sa propre liste, restée au vocabulaire américain — d'où un verdict
« anomalies » sur 61 niveaux latins parfaitement valides pendant que le nettoyeur
déclarait le fichier propre. Deux outils qui parlent du même fichier ne peuvent
pas avoir chacun leur référentiel.

⚠️ **Un « mois sans certification » ne se lit pas comme chez les autres.** La
fenêtre de dates du site ne rend que la DERNIÈRE certification d'un titre : les
titres certifiés en 2015 puis réhaussés depuis portent aujourd'hui leur date
récente. Un trou ancien peut donc être un artefact de collecte (corpus constitué
sans les pages de détail) et non une lacune de la source. Le rapport le dit.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd

from src.utils.cert_coverage import annee_assez_dense
from src.utils.cert_normalize import bpi_level, bpi_level_connu, bpi_units

REQUIRED_COLS = ["artist", "title", "certification_level", "certification_date"]
LOW_MONTH_THRESHOLD = 3

#: Catégories servies par le site. « Music DVDs » au pluriel : c'est le libellé
#: verbatim, et `cert_matcher._CAT_MAP` le rabat sur « video ».
VALID_CATEGORIES = {"Album", "Single", "Music DVDs"}


def _load(csv_path: Path) -> pd.DataFrame:
    for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            df = pd.read_csv(csv_path, encoding=enc, dtype=str).fillna("")
            df.columns = [c.strip().lstrip("﻿") for c in df.columns]
            return df
        except UnicodeDecodeError:
            continue
    raise ValueError("Encodage illisible")


def validate_bpi_csv(
    csv_path: str | Path, recent_years: tuple[int, ...] = (2024, 2025, 2026)
) -> dict:
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
        "invalid_levels": [],
        "invalid_categories": [],
        "units_mismatch": [],
        "sans_identite": 0,
        "categories": {},
        "date_range": None,
        "latest_date": None,
    }
    if not csv_path.exists():
        report["errors"].append(f"Fichier introuvable : {csv_path}")
        return report
    try:
        df = _load(csv_path)
    except (OSError, ValueError) as e:  # pandas : ParserError/EmptyDataError heritent de ValueError
        report["errors"].append(f"Chargement impossible : {e}")
        return report

    cmap = {c.lower(): c for c in df.columns}

    def col(name: str) -> pd.Series:
        real = cmap.get(name.lower())
        return (
            df[real].astype("string").str.strip()
            if real
            else pd.Series([""] * len(df), dtype="string")
        )

    missing = [c for c in REQUIRED_COLS if c.lower() not in cmap]
    if missing:
        report["errors"].append(f"Colonnes manquantes : {missing}")
        return report

    report["stats"]["n_rows"] = int(len(df))
    artist = col("artist")
    title = col("title")
    level = col("certification_level")
    categorie = col("category")
    date_raw = col("certification_date")
    units = col("units")

    report["empty_critical"] = int((artist.isna() | (artist == "")).sum())
    report["empty_titles"] = int((title.isna() | (title == "")).sum())
    report["empty_levels"] = int((level.isna() | (level == "")).sum())

    iso = date_raw.where(date_raw.str.fullmatch(r"\d{4}-\d{2}-\d{2}", na=False), "")
    date = pd.to_datetime(iso.where(iso != ""), format="%Y-%m-%d", errors="coerce")
    report["date_parse_failures"] = int(((date_raw != "") & (iso == "")).sum())
    valid = date.dropna()
    if not valid.empty:
        report["date_range"] = f"{valid.min():%d/%m/%Y} → {valid.max():%d/%m/%Y}"
        report["latest_date"] = f"{valid.max():%d/%m/%Y}"
        days = (datetime.now() - valid.max().to_pydatetime()).days
        report["stats"]["days_since_latest"] = days
        if days > 60:
            report["warnings"].append(
                f"Certif. la plus récente il y a {days} j ({report['latest_date']}) — "
                "base à backfiller"
            )

    # Doublons — même clé que l'updater : identité stable + palier + date.
    ids = col("format_id") + "|" + col("artist_id") + "|" + col("title_id")
    sans_identite = ids.isin(["||", "0|0|0"])
    report["sans_identite"] = int(sans_identite.sum())
    identite = ids.where(
        ~sans_identite, artist.fillna("").str.upper() + "|" + title.fillna("").str.upper()
    )
    key = identite + " | " + level.fillna("").map(bpi_level).str.upper() + " | " + iso
    dup = key.duplicated(keep="first") & (artist.fillna("") != "")
    report["stats"]["duplicates"] = int(dup.sum())
    if dup.any():
        report["duplicates"] = list(key[dup].head(12))

    # Référentiels
    report["invalid_levels"] = sorted(
        {lvl for lvl in level.dropna() if lvl and not bpi_level_connu(lvl)}
    )
    report["invalid_categories"] = sorted(
        {c for c in categorie.dropna() if c and c not in VALID_CATEGORIES}
    )
    report["categories"] = {
        k: int(v) for k, v in categorie[categorie != ""].value_counts().head(12).items()
    }

    # Cohérence des unités : elles DÉRIVENT du couple (niveau, format). Un écart
    # signale une ligne écrite avec un barème périmé, pas une saisie douteuse —
    # et c'est justement le genre d'incohérence qu'un CSV traîne en silence.
    ecarts: dict[str, int] = {}
    for lvl, cat, u in zip(level, categorie, units, strict=False):
        if not lvl or not u:
            continue
        attendu = bpi_units(lvl, format_type=cat)
        try:
            lu = float(str(u).strip())
        except ValueError:
            lu = None
        if attendu is not None and lu != attendu:
            ecarts[f"{lvl} / {cat} : {u} au lieu de {attendu}"] = (
                ecarts.get(f"{lvl} / {cat} : {u} au lieu de {attendu}", 0) + 1
            )
    report["units_mismatch"] = [f"{k} ({n}×)" for k, n in sorted(ecarts.items())]

    # Couverture temporelle
    if not valid.empty:
        counts = valid.dt.to_period("M").value_counts().sort_index()
        cur = pd.Period(datetime.now(), freq="M")
        years = valid.dt.year
        y0, y1 = int(years.min()), int(years.max())
        per_year = {y: int((years == y).sum()) for y in range(y0, y1 + 1)}
        report["per_year"] = per_year
        report["missing_years"] = [
            y
            for y, c in per_year.items()
            if c == 0 and (per_year.get(y - 1, 0) > 0 or per_year.get(y + 1, 0) > 0)
        ]
        for y in [y for y, c in per_year.items() if annee_assez_dense(c)]:
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

    report["ok"] = (
        not report["errors"]
        and report["empty_critical"] == 0
        and report["stats"].get("duplicates", 0) == 0
        and not report["invalid_levels"]
        and not report["invalid_categories"]
        and not report["units_mismatch"]
    )
    return report


def format_report(report: dict) -> str:
    L = [
        "=" * 52,
        "🔎 VALIDATION DU CSV BPI (Royaume-Uni)",
        "=" * 52,
        f"Fichier : {report['path']}",
    ]
    if report["errors"]:
        return "\n".join(L + [f"❌ {e}" for e in report["errors"]])
    s = report["stats"]
    L.append(f"Lignes : {s.get('n_rows', 0)}")
    if report.get("date_range"):
        L.append(f"Période couverte : {report['date_range']}")
    if s.get("days_since_latest") is not None:
        L.append(
            f"Certif. la plus récente : {report['latest_date']} (il y a {s['days_since_latest']} j)"
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
    if report["empty_titles"]:
        L.append(f"ℹ️ Titre vide : {report['empty_titles']} ligne(s)")
    if report["empty_levels"]:
        L.append(f"⚠️ Niveau vide : {report['empty_levels']} ligne(s)")
    if report["date_parse_failures"]:
        L.append(f"⚠️ Dates illisibles : {report['date_parse_failures']}")
    if report["sans_identite"]:
        L.append(
            f"⚠️ Lignes sans identité de source : {report['sans_identite']} "
            "(dédup rabattue sur artiste+titre)"
        )

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
    section("Catégories inconnues", report["invalid_categories"])
    section("Unités incohérentes avec le barème", report["units_mismatch"])
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
        volumes = report.get("per_year", {})
        for y in sorted(by_year):
            volume = volumes.get(int(y)) if str(y).isdigit() else None
            contexte = f"  (sur {volume} certifications cette année-là)" if volume else ""
            L.append(f"  • {y} : {len(by_year[y])} mois — {', '.join(by_year[y])}{contexte}")
        L.append("  ⚠️ Chez la BPI, un mois ancien peut être vide SANS lacune de la source :")
        L.append("     sa fenêtre de dates ne rend que la DERNIÈRE certification, donc un")
        L.append("     titre réhaussé depuis a migré vers sa date récente. Le remède est un")
        L.append("     balayage complet AVEC les paliers (--full), pas un rescrape mensuel.")
    section("Mois à faible couverture (années récentes)", report["low_months"], limit=24)
    if report.get("categories"):
        L.append("")
        L.append("── Catégories (info) ──")
        L.append("  " + ", ".join(f"{k}:{v}" for k, v in report["categories"].items()))
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

    return Path(DATA_PATH) / "certifications" / "bpi" / "certif_bpi.csv"


def main():
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else _default_csv_path()
    print(format_report(validate_bpi_csv(path)))


if __name__ == "__main__":
    main()
