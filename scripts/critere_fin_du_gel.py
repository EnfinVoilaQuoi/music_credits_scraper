"""Compte rendu du critère de fin du gel (quatre volets, `src/services/critere_gel.py`).

    python scripts/critere_fin_du_gel.py
    python scripts/critere_fin_du_gel.py --controle data/diagnostics/echantillon_temoin/controle_20261110.csv

Lecture seule : les audits datés `data/diagnostics/audit_*.json`, les hausses de fin
de run `data/diagnostics/audit_par_artiste/hausses.jsonl` et, avec `--controle`, la
fiche de contrôle remplie de l'échantillon témoin. N'ouvre pas la base.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.config import DATA_DIR
from src.services import critere_gel

DIAG = Path(DATA_DIR) / "diagnostics"


def _audits(dossier: Path) -> list[dict]:
    audits = []
    for chemin in sorted(dossier.glob("audit_*.json")):
        try:
            audits.append(json.loads(chemin.read_text(encoding="utf-8")))
        except (OSError, ValueError) as e:
            print(f"  (illisible, ignoré : {chemin.name} — {e})", file=sys.stderr)
    return audits


def _hausses(chemin: Path) -> list[dict]:
    if not chemin.exists():
        return []
    lignes = []
    for brut in chemin.read_text(encoding="utf-8").splitlines():
        try:
            lignes.append(json.loads(brut))
        except ValueError:
            continue
    return lignes


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--diagnostics", type=Path, default=DIAG)
    p.add_argument("--controle", type=Path, help="fiche de contrôle REMPLIE (CSV)")
    p.add_argument(
        "--seuils", help='seuils du témoin en JSON, ex. \'{"duree": 0.02, "isrc": 0.01}\''
    )
    a = p.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    audits = _audits(a.diagnostics)
    entiers = sorted(
        (x for x in audits if x.get("perimetre") == critere_gel.BASE_ENTIERE),
        key=lambda x: x.get("date", ""),
    )
    if not entiers:
        print("Aucun audit de la base entière.")
        return 1
    dernier = entiers[-1]
    maintenant = critere_gel._date(dernier["date"])
    depouillement = None
    if a.controle:
        from scripts.echantillon_temoin import depouiller, lire_controle

        depouillement = depouiller(lire_controle(a.controle), dernier)
    seuils = json.loads(a.seuils) if a.seuils else None
    volets = [
        critere_gel.volet_invariants(dernier),
        critere_gel.volet_regressions(
            _hausses(a.diagnostics / "audit_par_artiste" / "hausses.jsonl"), maintenant
        ),
        critere_gel.volet_delai(audits),
        critere_gel.volet_temoin(depouillement, seuils),
    ]
    print(f"Dernier audit de la base entière : {dernier['date']} (critère {dernier['critere']})")
    print(critere_gel.rapport(volets))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
