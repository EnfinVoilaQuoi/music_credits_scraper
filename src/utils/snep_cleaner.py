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
import shutil
import sys
from datetime import datetime
from pathlib import Path

from src.utils import cert_clean_report
from src.utils.cert_fixes_io import charger_fixes
from src.utils.cert_normalize import (
    apply_manual_fixes,
    canon_category,
    canon_level,
    clean_field,
    repair_extra_separators,
    restore_apostrophes,
)
from src.utils.snep_build import purger_fantomes, read_canonical_csv

EXPECTED_NCOLS = 7


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
        "manual_fixes_applied": 0,
        "fantomes_retires": 0,
        "fantome_examples": [],
        "manual_fix_examples": [],
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

    # Corrections saisies à la main : réappliquées à CHAQUE nettoyage, car une
    # ré-importation SNEP ressert le libellé corrompu (cf. `cert_fixes_io`).
    fixes = charger_fixes("snep")

    seen_keys = set()
    out_rows = []

    for fields in rows:
        # Lignes au mauvais nombre de colonnes : conservées telles quelles
        if len(fields) != EXPECTED_NCOLS:
            report["malformed_kept"] += 1
            out_rows.append(fields)
            continue

        original = list(fields)
        cleaned = [clean_field(f) for f in fields]
        if cleaned != original:
            report["whitespace_fixed"] += 1

        # Restaurer les apostrophes corrompues (?→') dans artiste et titre
        for i in (0, 1):
            cleaned[i], n_apo = restore_apostrophes(cleaned[i])
            if n_apo:
                report["apostrophes_restored"] += n_apo
                if len(report["apostrophe_examples"]) < 15:
                    report["apostrophe_examples"].append(cleaned[i])

        # Après la restauration AUTOMATIQUE : la saisie manuelle a le dernier
        # mot, elle ne sert justement qu'aux cas qu'aucun motif ne couvre.
        artiste_fixe, titre_fixe = apply_manual_fixes(cleaned[0], cleaned[1], fixes)
        if (artiste_fixe, titre_fixe) != (cleaned[0], cleaned[1]):
            report["manual_fixes_applied"] += 1
            if len(report["manual_fix_examples"]) < 15:
                report["manual_fix_examples"].append(
                    f"{cleaned[0]} — {cleaned[1]}  →  {artiste_fixe} — {titre_fixe}"
                )
            cleaned[0], cleaned[1] = artiste_fixe, titre_fixe

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
        new_cat = canon_category(category)
        if new_cat != category:
            report["categories_recased"] += 1
            report["category_changes"][f"{category} → {new_cat}"] = (
                report["category_changes"].get(f"{category} → {new_cat}", 0) + 1
            )
            cleaned[3] = new_cat
        new_lvl = canon_level(level)
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

    # Lignes FANTÔMES du CSV canonique. Elles ne sont PAS dans ce fichier-ci :
    # le brut est un ré-export complet, donc à jour. Elles vivent dans le clean,
    # qui accumule d'un export à l'autre et garde le libellé d'hier même quand
    # la source a corrigé son encodage depuis. C'est `snep_build.rebuild` qui
    # les retire — appelé juste en dessous quand on applique.
    #
    # On les COMPTE ici pour que le rapport les annonce, et surtout pour que le
    # verdict « déjà propre » en tienne compte : un nettoyage qui va changer
    # 130 lignes ne doit pas s'annoncer sans effet. C'est exactement l'erreur du
    # 2026-09-06, où deux compteurs oubliés faisaient disparaître le bouton
    # « Appliquer » alors que le rapport listait des anomalies.
    _, fantomes = purger_fantomes(read_canonical_csv(csv_path.parent / "certif_snep.csv"))
    if fantomes:
        report["fantomes_retires"] = len(fantomes)
        report["fantome_examples"] = [
            f"{f.get('artist', '')} — {f.get('title', '')}" for f in fantomes[:15]
        ]

    report["rows_out"] = len(out_rows)
    # SNEP nettoie le fichier SUR PLACE : « déjà propre » s'y lit directement du
    # nombre de modifications comptées, sans comparaison de fichiers.
    modifiees = (
        report["levels_recased"]
        + report["categories_recased"]
        + report["whitespace_fixed"]
        + report["duplicates_removed"]
        + report["empty_removed"]
        + report["apostrophes_restored"]
        + report["manual_fixes_applied"]
        + report["fantomes_retires"]
    )
    report["lignes_modifiees"] = modifiees
    report["deja_propre"] = modifiees == 0

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
    """Rend le rapport via le formateur COMMUN aux trois sources.

    Le rendu vivait ici, et BRMA/RIAA n'avaient rien d'équivalent : le nettoyage
    de deux sources sur trois se résumait à « X → Y lignes ». Le squelette est
    désormais dans `cert_clean_report` ; ce qui reste ici, c'est ce que SNEP
    compte — le seul choix qui lui appartienne vraiment.
    """
    counters = [
        ("Niveaux re-cassés", report.get("levels_recased", 0)),
        ("Catégories re-cassées", report.get("categories_recased", 0)),
        ("Lignes espaces/tab nettoyées", report.get("whitespace_fixed", 0)),
        ("Caractères restaurés (?→ '/œ)", report.get("apostrophes_restored", 0)),
        ("Corrections manuelles appliquées", report.get("manual_fixes_applied", 0)),
        ("Doublons retirés", report.get("duplicates_removed", 0)),
        ("Fantômes du CSV canonique (libellé cassé)", report.get("fantomes_retires", 0)),
        ("Lignes vides retirées", report.get("empty_removed", 0)),
    ]
    if report.get("malformed_kept"):
        counters.append(("Lignes malformées conservées", report["malformed_kept"]))

    return cert_clean_report.render(
        report,
        titre="🧹 NETTOYAGE DU CSV MAÎTRE SNEP",
        counters=counters,
        sections=[
            ("Niveaux normalisés", report.get("level_changes", {})),
            ("Catégories normalisées", report.get("category_changes", {})),
        ],
        examples=[
            (
                "Caractères restaurés ?→ '/œ (exemples)",
                [ex[:70] for ex in report.get("apostrophe_examples") or []],
            ),
            (
                "Corrections manuelles appliquées",
                [ex[:90] for ex in report.get("manual_fix_examples") or []],
            ),
            (
                "Fantômes retirés de certif_snep.csv — la version SAINE y reste",
                [ex[:90] for ex in report.get("fantome_examples") or []],
            ),
            (
                f"Lignes vides retirées ({report.get('empty_removed', 0)})",
                [ex[:90] for ex in report.get("empty_examples") or []],
            ),
        ],
        note_dry_run=(
            "ℹ️  DRY-RUN : rien n'a été écrit. Relance avec --apply pour "
            "appliquer (un backup sera créé)."
        ),
    )


def _default_csv_path() -> Path:
    from src.config import DATA_PATH

    return Path(DATA_PATH) / "certifications" / "snep" / "certif-.csv"


def main() -> int:
    """Rend le CODE DE SORTIE : il valait 0 en toutes circonstances, y compris
    sur un rapport porteur d'`error`. La GUI ne décide que sur ce code."""
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
    return 1 if report.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
