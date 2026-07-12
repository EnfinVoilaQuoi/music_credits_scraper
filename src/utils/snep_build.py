"""Builder du CSV canonique SNEP : brut `certif-.csv` → `certif_snep.csv`.

Convention « brut + clean » (voir plan certifs). Le CLEAN **accumule** : les
certifs de l'export SNEP (fenêtre glissante) sont fusionnées sans jamais rien
retirer — sinon on perd l'historique (l'ancien `certifications.db`, gitignoré,
en contenait 292 de plus que le brut courant). Aucune dépendance DB en régime
permanent ; `bootstrap_rows_from_db` ne sert qu'à la migration initiale.

Colonnes canoniques (lues ensuite par `cert_matcher._load_snep`, qui normalise
à la volée comme pour BRMA/RIAA) :
    artist, title, publisher, category, certification, release_date, certification_date
"""

import io
import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from src.models.certification import CertificationCategory, CertificationLevel
from src.utils.cert_normalize import normalize_text, repair_extra_separators
from src.utils.logger import get_logger

logger = get_logger(__name__)

CANONICAL_COLUMNS = [
    "artist",
    "title",
    "publisher",
    "category",
    "certification",
    "release_date",
    "certification_date",
]


def read_raw_snep_csv(filepath: Path) -> pd.DataFrame:
    """Lit le brut SNEP `certif-.csv` (semicolon, encodages variés, séparateurs
    à réparer) → DataFrame aux colonnes normalisées. Pur (aucune DB).

    Repris tel quel de l'ancien `SNEPCertificationManager.load_csv`.
    """
    if not filepath.exists():
        logger.warning(f"⚠️ Fichier CSV non trouvé : {filepath}")
        return pd.DataFrame()

    try:
        encodings = ["utf-8-sig", "utf-8", "latin-1", "cp1252"]
        raw_text = None
        for encoding in encodings:
            try:
                raw_text = filepath.read_text(encoding=encoding)
                break
            except UnicodeDecodeError:
                continue
        if raw_text is None:
            logger.error("Impossible de charger le CSV avec les encodages disponibles")
            return pd.DataFrame()

        raw_text = raw_text.replace("\x00", "")

        raw_text, repaired = repair_extra_separators(raw_text)
        if repaired:
            logger.warning(f"🩹 {repaired} ligne(s) CSV réparée(s) (séparateur en trop)")

        df = pd.read_csv(
            io.StringIO(raw_text),
            sep=";",
            na_values=["", "N/A", "null", "None"],
            dtype=str,
            on_bad_lines="skip",
        )

        for col in df.columns:
            if df[col].dtype == object:
                df[col] = df[col].str.strip()

        for date_col in ["Date de sortie", "Date de constat"]:
            if date_col in df.columns:
                df[date_col] = pd.to_datetime(df[date_col], format="%d/%m/%Y", errors="coerce")

        df.columns = [col.strip().replace("﻿", "") for col in df.columns]

        new_columns = []
        for col in df.columns:
            if "nterpr" in col or "Interpr" in col:
                new_columns.append("Interprète")
            elif "diteur" in col or "Editeur" in col:
                new_columns.append("Éditeur / Distributeur")
            elif "at" in col and "gorie" in col:
                new_columns.append("Catégorie")
            elif col == "Titre":
                new_columns.append("Titre")
            elif col == "Certification":
                new_columns.append("Certification")
            elif "sortie" in col:
                new_columns.append("Date de sortie")
            elif "constat" in col:
                new_columns.append("Date de constat")
            else:
                new_columns.append(col)
        df.columns = new_columns

        logger.info(f"✅ CSV brut chargé : {len(df)} enregistrements")
        return df

    except Exception as e:
        logger.error(f"❌ Erreur lors du chargement du CSV brut : {e}")
        return pd.DataFrame()


def _clean_value(value):
    """strip + collapse des espaces ; vide → None (repris de parse_and_import_csv)."""
    if pd.isna(value):
        return None
    cleaned = re.sub(r"\s+", " ", str(value).strip())
    return cleaned or None


def _to_date_str(value) -> str:
    """Timestamp/valeur → 'YYYY-MM-DD' (ou '' si absent), comme le fait le
    matcher (`str(x)[:10]`)."""
    if value is None or pd.isna(value):
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    return str(value)[:10]


def canonical_rows_from_raw(df: pd.DataFrame) -> list[dict]:
    """Lignes canoniques depuis le brut. Mappe catégorie/niveau via les enums
    `Certification*` et **saute** les lignes au niveau inconnu/vide (comportement
    identique à l'ancien import DB, qui plantait puis `continue`)."""
    rows: list[dict] = []
    if df.empty:
        return rows
    cols = list(df.columns)
    for _, r in df.iterrows():
        try:
            artist = _clean_value(r[cols[0]] if len(cols) > 0 else "") or ""
            title = _clean_value(r[cols[1]] if len(cols) > 1 else "") or ""
            publisher = _clean_value(r[cols[2]]) if len(cols) > 2 else None
            category_str = _clean_value(r[cols[3]] if len(cols) > 3 else "Singles")
            certification_str = _clean_value(r[cols[4]] if len(cols) > 4 else "Or")

            # from_string(None) lève (mime le skip DB) ; from_string(inconnu) → None
            level = CertificationLevel.from_string(certification_str)
            if level is None:
                continue
            category = CertificationCategory.from_string(category_str)

            rows.append(
                {
                    "artist": artist,
                    "title": title,
                    "publisher": publisher or "",
                    "category": category.value,
                    "certification": level.value,
                    "release_date": _to_date_str(r[cols[5]] if len(cols) > 5 else None),
                    "certification_date": _to_date_str(r[cols[6]] if len(cols) > 6 else None),
                }
            )
        except Exception:
            continue
    return rows


def _key(row: dict) -> tuple:
    """Clé de dédup, identique à l'ancienne contrainte DB
    (artist_clean, title_clean, certification)."""
    return (normalize_text(row["artist"]), normalize_text(row["title"]), row["certification"])


def merge_canonical(base: list[dict], new: list[dict]) -> list[dict]:
    """Fusion ACCUMULANTE : première occurrence gagne pour les champs, la date de
    certif la plus récente l'emporte (mime l'upsert DB). L'ordre de `base` est
    préservé, les nouvelles clés ajoutées à la suite."""
    by_key: dict[tuple, dict] = {}
    order: list[tuple] = []
    for row in [*base, *new]:
        k = _key(row)
        if k not in by_key:
            by_key[k] = dict(row)
            order.append(k)
        else:
            cur = by_key[k]
            if (row.get("certification_date") or "") > (cur.get("certification_date") or ""):
                cur["certification_date"] = row["certification_date"]
    return [by_key[k] for k in order]


def read_canonical_csv(path: Path) -> list[dict]:
    """Relit `certif_snep.csv` (tout en str, vides = '')."""
    if not path.exists():
        return []
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    return df.to_dict("records")


def write_canonical_csv(rows: list[dict], path: Path) -> None:
    df = pd.DataFrame(rows, columns=CANONICAL_COLUMNS)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def write_meta(path: Path, source: str, count: int) -> None:
    path.write_text(
        json.dumps(
            {"last_update": datetime.now().isoformat(), "source": source, "count": count},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def rebuild(raw_path: Path, csv_path: Path, meta_path: Path, source: str = "GLOBAL") -> int:
    """Régénère le clean en fusionnant le brut courant dans l'existant (accumule),
    puis écrit le CSV canonique + le sidecar meta. Retourne le nombre de lignes."""
    existing = read_canonical_csv(csv_path)
    new = canonical_rows_from_raw(read_raw_snep_csv(raw_path))
    merged = merge_canonical(existing, new)
    write_canonical_csv(merged, csv_path)
    write_meta(meta_path, source, len(merged))
    logger.info(f"📄 certif_snep.csv : {len(merged)} lignes ({len(new)} depuis le brut)")
    return len(merged)


def bootstrap_rows_from_db(db_path: Path) -> list[dict]:
    """MIGRATION UNIQUE : lit l'ancien `certifications.db` → lignes canoniques,
    pour préserver l'historique accumulé (non présent dans le brut). Ne pas
    utiliser en régime permanent (la DB est retirée en fin de chantier)."""
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            "SELECT artist_name, title, publisher, category, certification, "
            "release_date, certification_date FROM certifications"
        )
        return [
            {
                "artist": an or "",
                "title": ti or "",
                "publisher": pub or "",
                "category": cat or "Singles",
                "certification": lvl or "Or",
                "release_date": (str(rel)[:10] if rel else ""),
                "certification_date": (str(cdate)[:10] if cdate else ""),
            }
            for an, ti, pub, cat, lvl, rel, cdate in cur.fetchall()
        ]
    finally:
        conn.close()
