"""Audit des DONNÉES — lecture seule, zéro réseau (CORRECTIFS.md, phase 0).

Les familles et les invariants vivent dans `src/services/audit.py` (chantier B,
2026-10-12) ; ce script n'en est que la ligne de commande — même chose que
`python -m src.cli audit`.

    python scripts/audit_donnees.py                       # base entière
    python scripts/audit_donnees.py --artiste Booba --artiste "Kid Cudi"
    python scripts/audit_donnees.py --detail lrc_emprunte --detail doublon_equivalent
    python scripts/audit_donnees.py --json data/diagnostics/audit_reference.json
    python scripts/audit_donnees.py --comparer data/diagnostics/audit_reference.json
    python scripts/audit_donnees.py --sans-persistance    # sans les invariants (plus rapide)
    python scripts/audit_donnees.py --base <copie.db> --donnees <data réel>

Auditer une COPIE (`scripts/copie_base.py`) : `--base` la désigne, `--donnees`
le dossier `data/` dont l'audit lit, EN LECTURE SEULE, les pages désactivées
(`disabled_tracks/`), le brut (`brut/brut.db`) et les images (`images/`). Sans
eux, le critère gonfle (477 contre 440 le 2026-11-09 : les pages désactivées
produisaient des cas). `--base` sans `--donnees` est refusé, sauf si la copie a
ces dossiers à côté d'elle (le `data/` d'un worktree ne les a pas).
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

from src.services import audit as audit_mod
from src.utils.data_manager import DataManager

DOSSIERS_LUS = ("disabled_tracks", "brut", "images")


def dossier_des_donnees(base: Path, donnees: Path | None) -> Path:
    """PUR (lit le disque). Le dossier `data/` à lire à côté de `base` :
    `donnees` s'il est donné, sinon celui de la copie s'il a les dossiers que
    l'audit lit ; sinon ValueError (un audit sans eux gonfle le critère)."""
    dossier = donnees or base.parent
    manquants = [d for d in DOSSIERS_LUS if not (dossier / d).exists()]
    if manquants:
        raise ValueError(
            f"{dossier} n'a pas {', '.join(manquants)} : --donnees <le data/ réel> "
            "(pages désactivées, brut et images lus en lecture seule)"
        )
    return dossier


def viser(base: Path, donnees: Path) -> None:
    """Pointe l'audit sur la base `base` et le dossier `donnees` (pages
    désactivées, brut, images), à la place de ceux de la configuration."""
    import src.persistence.brut as brut
    import src.utils.data_manager as dm
    import src.utils.disabled_tracks_manager as dtm

    dm.DATABASE_URL = f"sqlite:///{base.as_posix()}"
    dtm.DATA_DIR = donnees
    brut.CHEMIN = donnees / "brut" / "brut.db"
    audit_mod.DOSSIER_IMAGES = donnees / "images"


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--artiste", action="append", help="limiter à cet artiste (répétable)")
    p.add_argument(
        "--detail", action="append", help="lister les cas de cette famille (« all » = toutes)"
    )
    p.add_argument("--par-artiste", action="store_true", help="compte par artiste et famille")
    p.add_argument("--json", type=Path, help="écrire l'audit complet (comptes + cas) en JSON")
    p.add_argument("--comparer", type=Path, help="JSON d'un audit précédent à comparer")
    p.add_argument(
        "--sans-persistance", action="store_true", help="sans les invariants de persistance"
    )
    p.add_argument(
        "--sans-revue", action="store_true", help="sans les cas du panneau « À trancher »"
    )
    p.add_argument("--base", type=Path, help="auditer cette base (une copie) au lieu de la vraie")
    p.add_argument(
        "--donnees",
        type=Path,
        help="avec --base : le data/ réel (pages désactivées, brut, images), lu seulement",
    )
    a = p.parse_args(argv)
    if a.donnees and not a.base:
        raise SystemExit("--donnees ne va qu'avec --base")
    if a.base:
        try:
            viser(a.base, dossier_des_donnees(a.base, a.donnees))
        except ValueError as e:
            raise SystemExit(str(e)) from e

    try:
        audit = audit_mod.auditer(
            DataManager(),
            a.artiste,
            persistance=not a.sans_persistance,
            revue=not a.sans_revue,
        )
    except ValueError as e:
        raise SystemExit(str(e)) from e
    reference = json.loads(a.comparer.read_text(encoding="utf-8")) if a.comparer else None
    print(audit_mod.tableau(audit, reference))

    if a.par_artiste:
        par = collections.defaultdict(collections.Counter)
        for cas in audit.cas:
            par[cas.artiste][cas.famille] += 1
        print()
        for nom, compte in sorted(par.items(), key=lambda x: -sum(x[1].values())):
            print(
                f"{nom:20} {sum(compte.values()):5}  "
                + ", ".join(f"{k} {v}" for k, v in compte.most_common())
            )

    if a.detail:
        print(audit_mod.detail(audit, a.detail))

    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(
            json.dumps(audit.vers_dict(), ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(f"\n💾 {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
