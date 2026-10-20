"""Passage à BLANC des règles d'arbitrage VALIDÉES (« À trancher », 2026-10-20).

Les règles de `politiques_revue.REGLES_VALIDEES` ne s'appliquent qu'une fois
`revue_auto.REGLES_BRANCHEES` levé — après l'accord de l'utilisateur sur CE
passage à blanc. Le script les applique pour de vrai, mais sur une COPIE de la
base (sauvegarde SQLite en ligne : sûre même pendant un run ; la vraie base
n'est que lue), artiste par artiste, puis rend le journal obtenu : chaque
correction, son sens, et de quoi la défaire. Les corrections FORMELLES déjà
actives (LRC d'une autre fiche, certif d'un autre titre…) sont listées à part :
elles sont de toute façon faites en fin de run.

    python scripts/simuler_regles_validees.py [--artiste A2H] [--sortie rapport.md]
"""

from __future__ import annotations

import argparse
import collections
import shutil
import sqlite3
import sys
import tempfile
from contextlib import closing
from datetime import date
from pathlib import Path

from src.config import DATA_DIR

BASE = Path(DATA_DIR) / "music_credits.db"


def copier_la_base(copie: Path) -> None:
    with (
        closing(sqlite3.connect(f"file:{BASE.as_posix()}?mode=ro", uri=True)) as source,
        closing(sqlite3.connect(copie)) as cible,
    ):
        source.backup(cible)


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--artiste", action="append", help="limiter à cet artiste (répétable)")
    p.add_argument(
        "--sortie",
        type=Path,
        default=Path(DATA_DIR) / "diagnostics" / f"regles_validees_{date.today()}.md",
    )
    a = p.parse_args(argv)

    dossier = Path(tempfile.mkdtemp(prefix="regles_validees_"))
    copie = dossier / "copie.db"
    try:
        copier_la_base(copie)
        import src.utils.data_manager as module_dm
        from src.services import revue_auto
        from src.utils import corrections_fiches

        module_dm.DATABASE_URL = f"sqlite:///{copie.as_posix()}"
        dm = module_dm.DataManager()
        ouverte = Path(dm.db_path).resolve()
        refus = Path(corrections_fiches.moteur.url.database).resolve()
        if ouverte != copie.resolve() or refus != copie.resolve():
            print(f"❌ la base ouverte n'est pas la copie : {ouverte} / refus {refus}")
            return 1
        revue_auto.REGLES_BRANCHEES = True
        lignes = simuler(dm, a.artiste)
        dm.engine.dispose()
    finally:
        shutil.rmtree(dossier, ignore_errors=True)
    a.sortie.parent.mkdir(parents=True, exist_ok=True)
    a.sortie.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    print("\n".join(lignes[:40]))
    print(f"\n→ {a.sortie}")
    return 0


def simuler(dm, noms=None) -> list[str]:
    from src.services import revue_auto

    par_action = collections.defaultdict(list)
    echecs = []
    for nom in noms or dm.get_artist_names():
        art = dm.get_artist_by_name(nom)
        art.tracks = dm.discographie_reunie(art)
        # Le journal est HISTORIQUE : seules comptent les entrées de ce passage.
        avant = {c["id"] for c in dm.corrections_revue(art.id, retablies=True)}
        bilan = revue_auto.corriger(dm, art)
        echecs += [f"{nom} : {e}" for e in bilan.echecs]
        for c in dm.corrections_revue(art.id):
            if c["id"] not in avant:
                par_action[c["action"]].append((nom, c))
    regles = {k: v for k, v in par_action.items() if k.startswith(revue_auto.PREFIXE_REGLE)}
    formelles = {k: v for k, v in par_action.items() if k not in regles}
    lignes = [
        f"# Passage à blanc des règles validées — {date.today()}",
        "",
        "Appliquées sur une COPIE de la base ; la vraie n'est que lue.",
        "",
        "| action | corrections |",
        "|---|---:|",
    ]
    lignes += [f"| `{k}` | {len(v)} |" for k, v in sorted(regles.items())]
    lignes += [
        f"| `{k}` (formelle, déjà active) | {len(v)} |" for k, v in sorted(formelles.items())
    ]
    for titre, groupe in (("Règles validées", regles), ("Formelles (déjà actives)", formelles)):
        for action, liste in sorted(groupe.items()):
            lignes += ["", f"## {titre} — `{action}` ({len(liste)})", ""]
            for nom, c in sorted(liste, key=lambda x: (x[1]["detecteur"], x[0])):
                lignes.append(f"- [{nom}] `{c['detecteur']}` — {c['compte_rendu']}")
    if echecs:
        lignes += ["", f"## Échecs ({len(echecs)})", ""] + [f"- {e}" for e in echecs]
    return lignes


if __name__ == "__main__":
    sys.exit(main())
