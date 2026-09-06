#!/usr/bin/env python3
"""Rend leur programme aux certifications LATINES du corpus RIAA historique.

**Le problème.** La RIAA décerne deux programmes aux échelles différentes : le
classique (Gold 500 000 unités / Platinum 1 000 000 / Diamond 10 000 000) et le
latin (« Los Premios de Oro y de Platino » : Oro 30 000 / Platino 60 000 /
Diamante 600 000). Le corpus historique du projet a été collecté sans cette
distinction : un Oro latin y est écrit « Gold », indiscernable d'un Gold
américain. Les seuls cas repérables à l'œil étaient les multiplicateurs absurdes
(« LUIS FONSI — DESPACITO, 55x Multi-Platinum »), soit **110 lignes** — mais la
mesure en a trouvé **1 597**. Le visible n'était pas l'étendue.

**Pourquoi une mesure et non une heuristique.** Le site sépare les deux
programmes en deux ONGLETS (`tab_active=platinum-latin`). On peut donc demander
la liste exacte des awards latins au lieu de la deviner. Et comme le corpus
déplie l'échelle (une ligne par palier, toutes à la même date) là où la liste du
site n'affiche qu'un palier courant par award, on descend jusqu'à la TIMELINE de
chaque award : elle donne chaque palier avec sa date, soit exactement la
granularité du corpus. L'appariement est alors EXACT — pas de seuil, pas de
« probablement ».

**Ce que le script écrit.** Trois choses, pas une : le programme
(`Award_Programme`), le libellé dans le bon vocabulaire (« Gold » → « Oro ») et
les unités correspondantes. N'en écrire qu'une laisserait des lignes « LATIN »
annonçant « Gold » à 500 000 unités — l'erreur qu'on répare.

**Où il écrit.** Dans le BRUT (`riaa_raw.csv`), puis il re-dérive le clean.
Écrire dans le seul clean serait sans lendemain : celui-ci est REGÉNÉRÉ depuis
le brut à chaque « 🧹 Nettoyer », qui effacerait donc la réparation en silence.
C'est la même leçon que les corrections manuelles de libellés SNEP.

Usage :
    python scripts/mark_latin_awards.py                    # dry-run (défaut)
    python scripts/mark_latin_awards.py --apply            # écrit (backup avant)
    python scripts/mark_latin_awards.py --source x.json    # rejoue une collecte
    python scripts/mark_latin_awards.py --collect x.json   # collecte puis s'arrête
    python scripts/mark_latin_awards.py --revert           # restaure le backup
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

import pandas as pd

if sys.platform == "win32" and "pytest" not in sys.modules:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.scrapers.riaa_scraper_v2 import RIAAScraperV2, _parse_results
from src.utils.cert_normalize import (
    PROGRAMME_LATIN,
    riaa_level,
    riaa_units,
)
from src.utils.cert_normalize import (
    cle_plate as _plat,
)
from src.utils.title_matching import names_match_as_words
from src.utils.update_riaa import _RIAA_DIR, RIAA_RAW, _riaa_iso, clean_certif_csv

#: Le corpus s'arrête en 2017 ; on couvre le programme latin depuis sa création
#: (2000). Découpé pour que chaque tranche tienne sous le plafond de pagination.
TRANCHES = [
    ("2000-01-01", "2008-01-01"),
    ("2008-01-01", "2012-01-01"),
    ("2012-01-01", "2015-01-01"),
    ("2015-01-01", "2016-07-01"),
    ("2016-07-01", "2017-04-01"),
    ("2017-04-01", "2018-01-01"),
]

#: Vocabulaire latin → vocabulaire US de MÊME numéro de palier. Le numéro est
#: commun aux deux échelles (badge « level 61 ») : c'est lui que la collecte
#: historique a conservé, en lui collant le mauvais mot.
_LATIN_VERS_US = {"Oro": "Gold", "Platino": "Platinum", "Diamante": "Diamond"}
_US_VERS_LATIN = {us: lat for lat, us in _LATIN_VERS_US.items()}


def _traduire(niveau: str, table: dict[str, str]) -> str:
    """« 15x Platino » → « 15x Platinum » (ou l'inverse), multiplicateur gardé."""
    niveau = riaa_level(niveau)
    for avant, apres in table.items():
        if niveau.endswith(avant):
            return niveau[: -len(avant)] + apres
    return niveau


def collecter(verbeux: bool = True) -> list[dict]:
    """Awards latins de la période du corpus, avec leur historique de paliers."""
    scraper = RIAAScraperV2(headless=True)
    tout: list[dict] = []
    for debut, fin in TRANCHES:
        t0 = time.time()
        url = RIAAScraperV2._search_url(start=debut, end=fin, programme=PROGRAMME_LATIN)
        html = scraper._render(url, load_all=True, get_details=True)
        lignes = _parse_results(html, get_details=True) if html else []
        if verbeux:
            avec = sum(1 for c in lignes if c.get("history"))
            print(
                f"  {debut} → {fin} : {len(lignes)} award(s), {avec} avec historique "
                f"({time.time() - t0:.0f}s)",
                flush=True,
            )
        tout.extend(lignes)
    return tout


def index_latin(awards: list[dict]) -> tuple[dict, set[tuple]]:
    """Index des événements (par titre et date) et des paliers exacts.

    Un événement de certification est identifié par (artiste, titre, DATE).
    C'est la bonne maille, et pas le palier, parce que le corpus historique
    DÉPLIE l'échelle : « NICKY JAM — EL AMANTE » y occupe 14 lignes (Gold,
    Platinum, 2x … 13x) toutes datées du même jour, alors que la timeline du
    site n'a qu'UNE étape (13x Platino, 2017-09-18). Ces paliers intermédiaires
    n'ont jamais été des événements — ils appartiennent au même award, donc au
    même programme.

    L'index par palier sert de CORROBORATION : il dit combien de lignes le site
    confirme au niveau près, ce qui permet de juger l'appariement au lieu de le
    croire sur parole.

    Les événements sont indexés par **(titre, date)** et portent la LISTE des
    libellés d'artiste vus. L'artiste ne peut pas entrer dans la clé : le corpus
    dit « LUIS FONSI » là où le site dit « LUIS FONSI & DADDY YANKEE ». Il est
    donc comparé à part, par `names_match_as_words` — jamais par sous-chaîne
    nue, motif que le projet interdit par test structurel.
    """
    evenements: dict[tuple[str, str], list[str]] = {}
    paliers_exacts: set[tuple] = set()
    for award in awards:
        artiste, titre = award.get("artist", ""), _plat(award.get("title"))
        paliers = award.get("history") or [
            {
                "certification_level": award.get("award_level", ""),
                "certification_date": award.get("certification_date", ""),
            }
        ]
        for palier in paliers:
            niveau = _traduire(palier.get("certification_level", ""), _LATIN_VERS_US)
            date = _riaa_iso(palier.get("certification_date", ""))
            if not date:
                continue
            evenements.setdefault((titre, date), []).append(artiste)
            if niveau:
                paliers_exacts.add((_plat(artiste), titre, date, niveau))
    return evenements, paliers_exacts


def reparer(df: pd.DataFrame, evenements: dict, paliers_exacts: set = frozenset()) -> tuple:
    """Marque, retraduit et recalcule. Retourne (dataframe, rapport).

    (titre, date) sert de PRÉ-FILTRE exact ; l'artiste n'est comparé qu'aux
    quelques candidats de ce couple, en mots entiers. Comparer 43 000 lignes à
    945 événements avec un prédicat de mots serait inutilement coûteux, et
    l'ordre pré-filtre-puis-ancrage est celui déjà retenu pour `cert_matcher`.
    """
    rapport = {"marquees": 0, "corroborees": 0, "deja_latines": 0, "exemples": []}
    df = df.copy()
    for i, ligne in df.iterrows():
        if ligne["Award_Programme"] == PROGRAMME_LATIN:
            rapport["deja_latines"] += 1
            continue
        titre = _plat(ligne["Title"])
        date = _riaa_iso(ligne["Certification_Date"])
        candidats = evenements.get((titre, date))
        if not candidats:
            continue
        if not any(names_match_as_words(ligne["Artist"], candidat) for candidat in candidats):
            continue
        niveau = riaa_level(ligne["Certification_Type"])
        if (_plat(ligne["Artist"]), titre, date, niveau) in paliers_exacts:
            rapport["corroborees"] += 1
        latin = _traduire(ligne["Certification_Type"], _US_VERS_LATIN)
        if len(rapport["exemples"]) < 15:
            rapport["exemples"].append(
                f"{ligne['Artist']} — {ligne['Title']} | "
                f"{ligne['Certification_Type']} → {latin} | {ligne['Certification_Date']}"
            )
        df.at[i, "Award_Programme"] = PROGRAMME_LATIN
        df.at[i, "Certification_Type"] = latin
        unites = riaa_units(latin)
        df.at[i, "Units"] = str(unites) if unites else ""
        rapport["marquees"] += 1
    return df, rapport


def _backup() -> Path:
    dossier = _RIAA_DIR / "backups"
    dossier.mkdir(exist_ok=True)
    return dossier / "riaa_raw_avant_latin.csv"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="écrit réellement (backup avant)")
    parser.add_argument("--source", help="JSON d'une collecte précédente (évite de re-scraper)")
    parser.add_argument("--collect", help="collecte vers ce JSON puis s'arrête")
    parser.add_argument("--revert", action="store_true", help="restaure le backup de ce script")
    args = parser.parse_args()

    if args.revert:
        backup = _backup()
        if not backup.exists():
            print(f"❌ Aucun backup : {backup}")
            return 1
        shutil.copy2(backup, RIAA_RAW)
        clean_certif_csv(apply=True)
        print(f"↩️  Brut restauré depuis {backup}, clean re-dérivé")
        return 0

    if args.source:
        awards = json.loads(Path(args.source).read_text(encoding="utf-8"))
        print(f"Collecte relue : {len(awards)} award(s) latins")
    else:
        print("Collecte du programme latin (site RIAA, onglet platinum-latin)…")
        awards = collecter()
        if args.collect:
            Path(args.collect).write_text(json.dumps(awards, ensure_ascii=False), encoding="utf-8")
            print(f"→ {args.collect}")
            return 0

    evenements, paliers = index_latin(awards)
    print(
        f"{len(awards)} awards latins → {len(evenements)} couple(s) (titre, date), "
        f"{len(paliers)} palier(s) daté(s)\n"
    )

    df = pd.read_csv(RIAA_RAW, encoding="utf-8-sig", dtype=str).fillna("")
    for colonne in ("Award_Programme", "Certification_Type", "Units"):
        if colonne not in df.columns:
            df[colonne] = ""
    repare, rapport = reparer(df, evenements, paliers)

    print("=" * 60)
    print("🇺🇸 AWARDS LATINS DU CORPUS RIAA" + ("  (APPLIQUÉ)" if args.apply else "  (DRY-RUN)"))
    print("=" * 60)
    print(f"Lignes du BRUT                : {len(df)}")
    print(f"Déjà étiquetées LATIN         : {rapport['deja_latines']}")
    print(f"Reconnues et corrigées        : {rapport['marquees']}")
    print(
        f"  dont confirmées au palier près : {rapport['corroborees']} "
        f"(le reste = paliers intermédiaires dépliés par la collecte historique)"
    )
    print("\n── Exemples ──")
    for exemple in rapport["exemples"]:
        print(f"  • {exemple}")

    if not args.apply:
        print("\nℹ️  DRY-RUN : rien n'a été écrit. Relance avec --apply.")
        return 0

    shutil.copy2(RIAA_RAW, _backup())
    repare.to_csv(RIAA_RAW, index=False, encoding="utf-8-sig")
    rapport_clean = clean_certif_csv(apply=True)
    print(f"\n✅ Brut réparé, clean re-dérivé ({rapport_clean['rows_out']} lignes).")
    print(f"   Backup : {_backup()}")
    print("   (relance avec --revert pour annuler)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
