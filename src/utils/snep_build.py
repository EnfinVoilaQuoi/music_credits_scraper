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
from src.utils import cert_store
from src.utils.cert_normalize import normalize_text, repair_extra_separators, reperer_fantomes
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

    except (OSError, ValueError) as e:
        # pandas : ParserError/EmptyDataError/UnicodeDecodeError ⊂ ValueError.
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
        except (AttributeError, KeyError, IndexError, ValueError, TypeError):
            continue
    return rows


def _key(row: dict) -> tuple:
    """Clé de dédup : artiste, titre, certification **et CATÉGORIE**.

    La catégorie manquait — la clé reproduisait l'ancienne contrainte DB
    (artist_clean, title_clean, certification), héritage d'un schéma qui ne
    distinguait pas les formats. Conséquence mesurée le 2026-09-09 sur le corpus
    réel : **52 certifications masquées**, un même titre certifié dans les deux
    catégories fusionnant en une ligne dont `merge_canonical` ne gardait que la
    date la plus récente.

        NINHO | M.I.L.S      | Platine : Albums 2017-12-15  +  Singles 2025-03-27
        JUL   | ÉMOTIONS     | Diamant : Albums 2023-10-05  +  Singles 2025-09-04
        NINHO | M.I.L.S. 2.0 | Diamant : Singles 2021-03-11 +  Albums 2025-07-17

    Huit ans séparent l'album et le single de NINHO : ce sont deux événements,
    pas un doublon. BRMA l'avait déjà compris (`update_brma._cert_key` inclut la
    catégorie).

    Aucun risque de scission par variante d'orthographe : la catégorie ne prend
    que trois valeurs canoniques (`Singles`, `Albums`, `Vidéos`), vérifié
    identique dans le brut et dans le clean.
    """
    return (
        normalize_text(row["artist"]),
        normalize_text(row["title"]),
        row["certification"],
        row["category"],
    )


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


def purger_fantomes(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Retire les lignes au libellé cassé dont la version SAINE est déjà là.

    C'est un défaut de `merge_canonical`, et il faut en nommer la mécanique :
    sa clé passe par `normalize_text`, qui SUPPRIME le « ? » et développe la
    ligature (« Œ » → « OE »). « AU C?UR D'IAM » devient donc « AU CUR D'IAM »
    et « AU CŒUR D'IAM » devient « AU COEUR D'IAM » — deux clés distinctes, deux
    lignes conservées, pour une seule certification.

    Le clean accumulant d'un export à l'autre — à raison, une certification
    ancienne peut disparaître de l'export — il gardait ainsi éternellement le
    libellé d'hier, même après que la source a corrigé son encodage. Mesuré le
    2026-09-06 : **130 lignes**, dont « SHURIK?N », qui coupait en deux la
    discographie certifiée de l'artiste.

    Retourne (lignes gardées, lignes retirées). Le tri de `reperer_fantomes`
    n'est jamais appliqué à l'ordre du fichier : celui de `base` est préservé,
    comme le promet `merge_canonical`.
    """
    if not rows:
        return rows, []
    colonnes = list(CANONICAL_COLUMNS)
    i_artiste, i_titre = colonnes.index("artist"), colonnes.index("title")
    autres = tuple(colonnes.index(c) for c in ("category", "certification", "certification_date"))
    lignes = [[str(r.get(c, "")) for c in colonnes] for r in rows]

    retires = set(reperer_fantomes(lignes, i_artiste=i_artiste, i_titre=i_titre, autres=autres))
    if not retires:
        return rows, []
    return (
        [r for i, r in enumerate(rows) if i not in retires],
        [r for i, r in enumerate(rows) if i in retires],
    )


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
    """Écrit/met à jour le sidecar meta en gardant un historique PAR SOURCE
    (`updates`). Indispensable pour distinguer une MàJ globale d'une récup par
    artiste (fix JOURNAL 2026-06-25 : une recherche ARTISTE ne doit pas passer
    pour une MàJ globale)."""
    meta: dict = {}
    if path.exists():
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
    now = datetime.now().isoformat()
    updates = meta.get("updates") or {}
    updates[source] = now
    meta.update({"last_update": now, "last_source": source, "count": count, "updates": updates})
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def rebuild(raw_path: Path, csv_path: Path, meta_path: Path, source: str = "GLOBAL") -> int:
    """Régénère le clean en fusionnant le brut courant dans l'existant (accumule),
    puis écrit le CSV canonique + le sidecar meta. Retourne le nombre de lignes."""
    existing = read_canonical_csv(csv_path)
    new = canonical_rows_from_raw(read_raw_snep_csv(raw_path))
    merged, fantomes = purger_fantomes(merge_canonical(existing, new))
    # `rebuild` RÉÉCRIT le clean à chaque appel : il se sauvegarde donc toujours,
    # et plus seulement lorsqu'une purge de fantômes retire quelque chose. La
    # rétention de `cert_store` (les 10 plus récentes) répond au motif qui avait
    # fait choisir l'inverse — sauvegarder à chaque fois n'enterre plus rien.
    backup = cert_store.sauvegarder(csv_path)
    if fantomes:
        nom = backup.name if backup else "aucune (fichier neuf)"
        logger.info(
            f"🧹 {len(fantomes)} ligne(s) fantôme(s) retirée(s) — libellé cassé dont la "
            f"version saine est déjà présente. Sauvegarde : {nom}"
        )
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
