"""Nettoyage du CSV maître SNEP (déterministe, sans LLM).

Corrige les anomalies repérées par `snep_validator` :
  - normalise la CASSE des niveaux et catégories (ex: "Double diamant" → "Double Diamant") ;
  - supprime les artefacts de tabulation / espaces parasites dans les champs ;
  - retire les doublons exacts (artiste + titre + niveau + date de constat) ;
  - retire les lignes au champ critique vide (artiste ou titre manquant), listées dans le rapport.

Sécurité : un BACKUP horodaté est créé avant toute écriture (règle projet),
et le mode est DRY-RUN par défaut — il faut `apply=True` (ou `--apply` en CLI)
pour réellement réécrire le CSV. Le format est préservé à l'identique
(7 colonnes ';', BOM UTF-8, dates JJ/MM/AAAA en chaînes, labels requotés si
besoin), donc compatible avec le reste du pipeline.

CLI :
    python -m src.utils.snep_cleaner            # dry-run (rapport seulement)
    python -m src.utils.snep_cleaner --apply    # applique + réimporte en base
"""

from __future__ import annotations

import csv
import io
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

from src.utils.cert_normalize import repair_extra_separators

EXPECTED_NCOLS = 7

# Casse canonique des niveaux (clé = forme minuscule)
_LVL_CANON = {
    lvl.lower(): lvl
    for lvl in (
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
    )
}

# Catégories : singulier → pluriel + casse canonique
_CAT_CANON = {
    "single": "Singles",
    "singles": "Singles",
    "album": "Albums",
    "albums": "Albums",
    "vidéo": "Vidéos",
    "vidéos": "Vidéos",
    "video": "Vidéos",
    "videos": "Vidéos",
}


# --- Restauration des caractères corrompus par le SNEP ---
# Le '?' (codepoint 63) gravé dans la donnée SNEP remplace N'IMPORTE QUEL
# caractère perdu lors d'une vieille migration — PAS seulement l'apostrophe :
#   • la ligature œ  (C?UR → CŒUR, S?UR → SŒUR…)
#   • l'apostrophe d'élision/contraction (L?empire → L'empire, it?s → it's)
# On ne traite QUE les contextes à haute confiance ; tout '?' ambigu (ex:
# "SMILE?IT", "Who… are ?") est LAISSÉ tel quel pour révision manuelle.

# 1) Ligature œ : mots français connus (la liste évite les faux positifs).
_OE_PATTERNS = [
    re.compile(p, re.I)
    for p in (
        r"\bC\?URS?\b",  # CŒUR(S)
        r"\bS\?URS?\b",  # SŒUR(S)
        r"\bV\?UX?\b",  # VŒU(X)
        r"\bB\?UFS?\b",  # BŒUF(S)
        r"\bN\?UDS?\b",  # NŒUD(S)
        r"\bM\?URS\b",  # MŒURS
        r"\bF\?TUS\b",  # FŒTUS
        r"(?<![A-Za-zÀ-ÿ])\?UVRES?\b",  # ŒUVRE(S)
        r"(?<![A-Za-zÀ-ÿ])\?UFS?\b",  # ŒUF(S)
        r"(?<![A-Za-zÀ-ÿ])\?IL\b",  # ŒIL
        r"(?<![A-Za-zÀ-ÿ])\?DIPE\b",  # ŒDIPE
    )
]


def _oe_sub(m):
    g = m.group(0)
    lig = "Œ" if g == g.upper() else "œ"
    return g.replace("?", lig, 1)


# 2) Apostrophe : UNIQUEMENT élisions françaises et contractions anglaises.
_ELISION_FR = re.compile(r"\b([CDJLMNST])\?(?=[A-Za-zÀ-ÿ])", re.I)
_ELISION_FR2 = re.compile(r"\b(QU|JUSQU|LORSQU|PUISQU|QUOIQU|AUJOURD)\?(?=[A-Za-zÀ-ÿ])", re.I)
_CONTRACTION_EN = re.compile(r"([A-Za-zÀ-ÿ])\?(S|T|RE|VE|LL|D|M)\b", re.I)


def _clean_field(s: str) -> str:
    """Strip + écrase tab/espaces multiples en un seul espace."""
    return re.sub(r"\s+", " ", (s or "")).strip()


def _restore_apostrophes(s: str) -> tuple[str, int]:
    """Restaure les caractères corrompus (?) en contexte sûr : ligature œ puis
    apostrophe d'élision/contraction. Retourne (texte, nb de '?' restaurés).
    Les '?' ambigus restent intacts (à signaler par le validateur)."""
    before = s.count("?")
    if not before:
        return s, 0
    for pat in _OE_PATTERNS:
        s = pat.sub(_oe_sub, s)
    s = _ELISION_FR.sub(lambda m: m.group(1) + "'", s)
    s = _ELISION_FR2.sub(lambda m: m.group(1) + "'", s)
    s = _CONTRACTION_EN.sub(lambda m: m.group(1) + "'" + m.group(2), s)
    return s, before - s.count("?")


def _canon_category(cat: str) -> str:
    return _CAT_CANON.get(cat.lower(), cat)


def _canon_level(lvl: str) -> str:
    return _LVL_CANON.get(lvl.lower(), lvl)


def _read_rows(csv_path: Path) -> tuple[str, list[list[str]]]:
    """Lit le CSV maître : retourne (header_line, lignes_en_listes_de_champs).

    Réutilise la réparation de séparateurs du manager (labels contenant ';'),
    puis parse avec le module csv (respecte les guillemets).
    """
    raw = None
    for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            raw = csv_path.read_text(encoding=enc)
            break
        except UnicodeDecodeError:
            continue
    if raw is None:
        raise ValueError("Encodage illisible")

    raw = raw.replace("\x00", "")
    raw, _ = repair_extra_separators(raw)

    lines = raw.splitlines()
    if not lines:
        return "", []
    header = lines[0].lstrip("﻿")
    rows = []
    for line in lines[1:]:
        if not line.strip():
            continue
        fields = next(csv.reader([line], delimiter=";", quotechar='"'))
        rows.append(fields)
    return header, rows


def clean_snep_csv(csv_path: str | Path, apply: bool = False, reimport: bool = True) -> dict:
    """Nettoie le CSV maître SNEP. Retourne un rapport des actions.

    apply=False  → dry-run : compte ce qui serait modifié, n'écrit rien.
    apply=True   → backup + réécriture + (si reimport) réimport en base.
    """
    csv_path = Path(csv_path)
    report = {
        "path": str(csv_path),
        "applied": False,
        "backup": None,
        "rows_in": 0,
        "rows_out": 0,
        "levels_recased": 0,
        "categories_recased": 0,
        "whitespace_fixed": 0,
        "duplicates_removed": 0,
        "empty_removed": 0,
        "malformed_kept": 0,
        "apostrophes_restored": 0,
        "empty_examples": [],
        "apostrophe_examples": [],
        "level_changes": {},
        "category_changes": {},
    }

    if not csv_path.exists():
        report["error"] = f"Fichier introuvable : {csv_path}"
        return report

    header, rows = _read_rows(csv_path)
    report["rows_in"] = len(rows)

    seen_keys = set()
    out_rows = []

    for fields in rows:
        # Lignes au mauvais nombre de colonnes : conservées telles quelles
        if len(fields) != EXPECTED_NCOLS:
            report["malformed_kept"] += 1
            out_rows.append(fields)
            continue

        original = list(fields)
        cleaned = [_clean_field(f) for f in fields]
        if cleaned != original:
            report["whitespace_fixed"] += 1

        # Restaurer les apostrophes corrompues (?→') dans artiste et titre
        for i in (0, 1):
            cleaned[i], n_apo = _restore_apostrophes(cleaned[i])
            if n_apo:
                report["apostrophes_restored"] += n_apo
                if len(report["apostrophe_examples"]) < 15:
                    report["apostrophe_examples"].append(cleaned[i])

        artist, title = cleaned[0], cleaned[1]
        category, level = cleaned[3], cleaned[4]
        constat = cleaned[6]

        # Champs critiques vides → retirés (listés)
        if not artist or not title:
            report["empty_removed"] += 1
            if len(report["empty_examples"]) < 20:
                report["empty_examples"].append(";".join(original))
            continue

        # Normalisation casse
        new_cat = _canon_category(category)
        if new_cat != category:
            report["categories_recased"] += 1
            report["category_changes"][f"{category} → {new_cat}"] = (
                report["category_changes"].get(f"{category} → {new_cat}", 0) + 1
            )
            cleaned[3] = new_cat
        new_lvl = _canon_level(level)
        if new_lvl != level:
            report["levels_recased"] += 1
            report["level_changes"][f"{level} → {new_lvl}"] = (
                report["level_changes"].get(f"{level} → {new_lvl}", 0) + 1
            )
            cleaned[4] = new_lvl

        # Déduplication exacte (même clé que le validateur)
        key = (artist.lower(), title.lower(), cleaned[3], cleaned[4], constat)
        if key in seen_keys:
            report["duplicates_removed"] += 1
            continue
        seen_keys.add(key)

        out_rows.append(cleaned)

    report["rows_out"] = len(out_rows)

    if apply:
        # Backup horodaté AVANT toute écriture (règle projet)
        backup = csv_path.with_name(f"certif-backup-{datetime.now():%Y%m%d_%H%M%S}.csv")
        shutil.copy2(csv_path, backup)
        report["backup"] = str(backup)

        buf = io.StringIO()
        writer = csv.writer(
            buf, delimiter=";", quotechar='"', quoting=csv.QUOTE_MINIMAL, lineterminator="\n"
        )
        for fields in out_rows:
            writer.writerow(fields)
        csv_path.write_text("﻿" + header + "\n" + buf.getvalue(), encoding="utf-8")
        report["applied"] = True

        if reimport:
            # Régénère le CSV canonique (clean) depuis le brut nettoyé, puis
            # rafraîchit le matcher — plus d'import DB (convention brut+clean).
            from src.utils.cert_matcher import reset_cert_matcher
            from src.utils.snep_build import rebuild

            snep = Path(csv_path).parent
            rebuild(
                Path(csv_path),
                snep / "certif_snep.csv",
                snep / "certif_snep.meta.json",
                source="CLEAN",
            )
            reset_cert_matcher()

    return report


def format_report(report: dict) -> str:
    L = []
    L.append("=" * 52)
    L.append(
        "🧹 NETTOYAGE DU CSV MAÎTRE SNEP"
        + ("  (DRY-RUN)" if not report.get("applied") else "  (APPLIQUÉ)")
    )
    L.append("=" * 52)
    if report.get("error"):
        L.append(f"❌ {report['error']}")
        return "\n".join(L)

    L.append(f"Fichier : {report['path']}")
    if report.get("backup"):
        L.append(f"Backup  : {report['backup']}")
    L.append(
        f"Lignes : {report['rows_in']} → {report['rows_out']} "
        f"({report['rows_out'] - report['rows_in']:+d})"
    )
    L.append("")
    L.append(f"  • Niveaux re-cassés       : {report['levels_recased']}")
    L.append(f"  • Catégories re-cassées   : {report['categories_recased']}")
    L.append(f"  • Champs espaces/tab nettoyés : {report['whitespace_fixed']}")
    L.append(f"  • Caractères restaurés (?→ '/œ) : {report['apostrophes_restored']}")
    L.append(f"  • Doublons retirés        : {report['duplicates_removed']}")
    L.append(f"  • Lignes vides retirées   : {report['empty_removed']}")
    if report["malformed_kept"]:
        L.append(f"  • Lignes malformées conservées : {report['malformed_kept']}")

    def detail(title, d):
        if d:
            L.append("")
            L.append(f"── {title} ──")
            for k, n in sorted(d.items(), key=lambda x: -x[1]):
                L.append(f"  • {k}  ×{n}")

    detail("Niveaux normalisés", report["level_changes"])
    detail("Catégories normalisées", report["category_changes"])

    if report.get("apostrophe_examples"):
        L.append("")
        L.append("── Caractères restaurés ?→ '/œ (exemples) ──")
        for ex in report["apostrophe_examples"]:
            L.append(f"  • {ex[:70]}")

    if report["empty_examples"]:
        L.append("")
        L.append(f"── Lignes vides retirées ({report['empty_removed']}) ──")
        for ex in report["empty_examples"]:
            L.append(f"  • {ex[:90]}")

    if not report.get("applied"):
        L.append("")
        L.append(
            "ℹ️  DRY-RUN : rien n'a été écrit. Relance avec --apply pour "
            "appliquer (un backup sera créé)."
        )
    L.append("=" * 52)
    return "\n".join(L)


def _default_csv_path() -> Path:
    from src.config import DATA_PATH

    return Path(DATA_PATH) / "certifications" / "snep" / "certif-.csv"


def main():
    import argparse

    # Forcer l'UTF-8 sans ré-emballer stdout (un nouveau TextIOWrapper sur
    # sys.stdout.buffer entre en conflit avec ceux posés par d'autres modules
    # → "I/O operation on closed file"). reconfigure() modifie en place.
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description="Nettoyage du CSV maître SNEP")
    parser.add_argument("path", nargs="?", default=None, help="Chemin du CSV (défaut: CSV maître)")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Appliquer réellement (backup + réécriture + réimport DB)",
    )
    parser.add_argument(
        "--no-reimport", action="store_true", help="Ne pas réimporter en base après nettoyage"
    )
    args = parser.parse_args()

    path = Path(args.path) if args.path else _default_csv_path()
    report = clean_snep_csv(path, apply=args.apply, reimport=not args.no_reimport)
    print(format_report(report))


if __name__ == "__main__":
    main()
