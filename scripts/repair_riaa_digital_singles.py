#!/usr/bin/env python3
"""Distingue les singles NUMÉRIQUES des singles physiques (2004 → août 2006).

**Le problème.** De 2004 à juillet 2006, un single NUMÉRIQUE était certifié Gold
à 100 000 téléchargements, contre 500 000 pour un single physique. Les Platinum
numériques de cette période ont été retirés en août 2006 ; les Gold, eux, ont été
CONSERVÉS tels quels — ils subsistent donc avec leur ancien seuil. Sans savoir
si un single de 2005 était physique ou numérique, `riaa_units` annonce 500 000
pour des certifications qui en valent 100 000, soit cinq fois trop.

**Le discriminant.** Le libellé de format ne dit rien : le site écrit « SINGLE »
dans les deux cas. C'est la FAMILLE du badge qui tranche (`alt="badge DI level
0"`), mesuré le 2026-09-06 : les certifications de 1975 sont toutes `ST`
(standard, physique), celles de 2005 sur des singles toutes `DI` (digital). Le
scraper la relève désormais nativement ; ce script la rétro-remplit pour les
lignes du corpus historique, qui l'ignorent.

**Portée volontairement étroite.** Seule la fenêtre où le seuil diffère est
scrapée. Avant 1989, aucune ambiguïté à lever : le numérique n'existait pas, la
date suffit et `riaa_units` applique déjà les seuils de l'époque.

Usage :
    python scripts/repair_riaa_digital_singles.py              # dry-run
    python scripts/repair_riaa_digital_singles.py --apply      # écrit
    python scripts/repair_riaa_digital_singles.py --revert     # annule
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

import pandas as pd

if sys.platform == "win32" and "pytest" not in sys.modules:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.scrapers.riaa_scraper_v2 import RIAAScraperV2, _parse_results
from src.utils.cert_normalize import cle_plate as _plat
from src.utils.title_matching import names_match_as_words
from src.utils.update_riaa import _RIAA_DIR, RIAA_RAW, _riaa_iso, clean_certif_csv

#: La fenêtre où le seuil du single numérique diffère de celui du physique.
DEBUT, FIN = "2004-01-01", "2006-08-01"

#: Découpage par semestre : chaque tranche doit tenir sous le plafond de
#: pagination du scraper (200 clics de 30 lignes).
TRANCHES = [
    ("2004-01-01", "2004-07-01"),
    ("2004-07-01", "2005-01-01"),
    ("2005-01-01", "2005-07-01"),
    ("2005-07-01", "2006-01-01"),
    ("2006-01-01", "2006-08-01"),
]


def collecter() -> list[dict]:
    """Certifications de la fenêtre, avec leur famille d'award."""
    scraper = RIAAScraperV2(headless=True)
    tout: list[dict] = []
    for debut, fin in TRANCHES:
        t0 = time.time()
        url = RIAAScraperV2._search_url(start=debut, end=fin)
        html = scraper._render(url, load_all=True, get_details=False)
        lignes = _parse_results(html, get_details=False) if html else []
        familles = {}
        for ligne in lignes:
            familles[ligne.get("award_family", "?")] = (
                familles.get(ligne.get("award_family", "?"), 0) + 1
            )
        print(f"  {debut} → {fin} : {len(lignes)} ligne(s) {familles} ({time.time() - t0:.0f}s)")
        tout.extend(lignes)
    return tout


def index_familles(lignes: list[dict]) -> dict[tuple[str, str], list[tuple[str, str]]]:
    """{(titre, date): [(artiste, famille), …]}.

    L'artiste reste hors de la clé et se compare en mots entiers : le corpus et
    le site ne créditent pas toujours les mêmes intervenants.
    """
    index: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for ligne in lignes:
        famille = (ligne.get("award_family") or "").strip().upper()
        if not famille:
            continue
        cle = (_plat(ligne.get("title")), _riaa_iso(ligne.get("certification_date", "")))
        if cle[1]:
            index.setdefault(cle, []).append((ligne.get("artist", ""), famille))
    return index


def reparer(df: pd.DataFrame, index: dict) -> tuple[pd.DataFrame, dict]:
    """Remplit `Award_Family` sur les lignes reconnues. Ne touche à rien d'autre :
    les unités sont recalculées par le nettoyage, qui lit cette colonne."""
    rapport = {"remplies": 0, "par_famille": {}, "exemples": []}
    df = df.copy()
    for i, ligne in df.iterrows():
        if str(ligne.get("Award_Family", "")).strip():
            continue
        date = _riaa_iso(ligne["Certification_Date"])
        if not (DEBUT <= date < FIN):
            continue
        candidats = index.get((_plat(ligne["Title"]), date))
        if not candidats:
            continue
        famille = next(
            (f for artiste, f in candidats if names_match_as_words(ligne["Artist"], artiste)),
            None,
        )
        if not famille:
            continue
        df.at[i, "Award_Family"] = famille
        rapport["remplies"] += 1
        rapport["par_famille"][famille] = rapport["par_famille"].get(famille, 0) + 1
        if len(rapport["exemples"]) < 10:
            rapport["exemples"].append(
                f"{ligne['Artist']} — {ligne['Title']} | {ligne['Format_Type']} | "
                f"{ligne['Certification_Type']} | {famille}"
            )
    return df, rapport


def _backup() -> Path:
    dossier = _RIAA_DIR / "backups"
    dossier.mkdir(exist_ok=True)
    return dossier / "riaa_raw_avant_familles.csv"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="écrit réellement (backup avant)")
    parser.add_argument("--revert", action="store_true", help="restaure le backup de ce script")
    args = parser.parse_args()

    if args.revert:
        if not _backup().exists():
            print(f"❌ Aucun backup : {_backup()}")
            return 1
        shutil.copy2(_backup(), RIAA_RAW)
        clean_certif_csv(apply=True)
        print(f"↩️  Brut restauré depuis {_backup()}, clean re-dérivé")
        return 0

    print(f"Collecte de la fenêtre {DEBUT} → {FIN} (familles d'award)…")
    index = index_familles(collecter())
    print(f"{len(index)} couple(s) (titre, date) connus du site\n")

    df = pd.read_csv(RIAA_RAW, encoding="utf-8-sig", dtype=str).fillna("")
    if "Award_Family" not in df.columns:
        df["Award_Family"] = ""
    repare, rapport = reparer(df, index)

    print("=" * 60)
    print("💿 SINGLES NUMÉRIQUES 2004-2006" + ("  (APPLIQUÉ)" if args.apply else "  (DRY-RUN)"))
    print("=" * 60)
    print(f"Familles renseignées : {rapport['remplies']} {rapport['par_famille']}")
    for exemple in rapport["exemples"]:
        print(f"  • {exemple}")

    if not args.apply:
        print("\nℹ️  DRY-RUN : rien n'a été écrit. Relance avec --apply.")
        return 0

    shutil.copy2(RIAA_RAW, _backup())
    repare.to_csv(RIAA_RAW, index=False, encoding="utf-8-sig")
    resultat = clean_certif_csv(apply=True)
    print(f"\n✅ Brut réparé, clean re-dérivé ({resultat['rows_out']} lignes).")
    print(f"   Backup : {_backup()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
