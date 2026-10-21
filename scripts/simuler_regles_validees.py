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
    python scripts/simuler_regles_validees.py --apply   # VRAIE base : sauvegarde d'abord

`--apply` (après l'accord de l'utilisateur sur le passage à blanc, et JAMAIS
pendant un run) : même geste sur la vraie base, après une sauvegarde ; le mode
de journal de la base est affiché (WAL depuis `9c27c01`).
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

#: Familles validées par l'utilisateur (leur interrupteur est levé dans `revue_auto`).
FAMILLES_VALIDEES = ("durees", "id_spotify_propose", "deezer")


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
    p.add_argument("--apply", action="store_true", help="appliquer sur la VRAIE base")
    p.add_argument(
        "--famille",
        choices=("durees", "id_spotify_propose", "deezer", "liens_deezer"),
        default="durees",
        help="la famille à passer : durées (validée) ou ID Spotify proposé (en essai)",
    )
    a = p.parse_args(argv)
    if a.famille != "durees":
        a.sortie = a.sortie.with_name(f"regles_{a.famille}_{date.today()}.md")

    if a.apply:
        return appliquer(a)
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
        revue_auto.REGLES_BRANCHEES = a.famille == "durees"
        revue_auto.REGLES_ID_PROPOSE_BRANCHEES = a.famille == "id_spotify_propose"
        revue_auto.REGLES_DEEZER_BRANCHEES = a.famille == "deezer"
        revue_auto.REGLES_LIENS_DEEZER_BRANCHEES = a.famille == "liens_deezer"
        lignes = simuler(dm, a.artiste, famille=a.famille)
        dm.engine.dispose()
    finally:
        shutil.rmtree(dossier, ignore_errors=True)
    a.sortie.parent.mkdir(parents=True, exist_ok=True)
    a.sortie.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    print("\n".join(lignes[:40]))
    print(f"\n→ {a.sortie}")
    return 0


def appliquer(a) -> int:
    from src.services import revue_auto
    from src.utils.data_manager import DataManager
    from src.utils.database_backup import get_backup_manager

    with closing(sqlite3.connect(f"file:{BASE.as_posix()}?mode=ro", uri=True)) as c:
        mode = c.execute("PRAGMA journal_mode").fetchone()[0]
    print(f"Base : {BASE} — journal_mode={mode}")
    backup = get_backup_manager().create_backup("regles_validees")
    if backup is None:
        print("❌ sauvegarde impossible — rien n'est écrit")
        return 1
    print(f"💾 {backup}")
    # Une famille à la fois, comme au passage à blanc qui l'a fait valider.
    revue_auto.REGLES_BRANCHEES = a.famille == "durees"
    revue_auto.REGLES_ID_PROPOSE_BRANCHEES = a.famille == "id_spotify_propose"
    revue_auto.REGLES_DEEZER_BRANCHEES = a.famille == "deezer"
    revue_auto.REGLES_LIENS_DEEZER_BRANCHEES = a.famille == "liens_deezer"
    dm = DataManager()  # (une famille à la fois, même en --apply)
    lignes = simuler(dm, a.artiste, famille=a.famille)
    lignes[0] = lignes[0].replace("Passage à blanc", "APPLICATION")
    lignes[2] = f"Appliquées sur la VRAIE base (sauvegarde : {backup.name})."
    sortie = a.sortie.with_name(a.sortie.stem + "_applique.md")
    sortie.parent.mkdir(parents=True, exist_ok=True)
    sortie.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    print("\n".join(lignes[:12]))
    print(f"\n→ {sortie}")
    return 0


def simuler(dm, noms=None, famille: str = "durees") -> list[str]:
    from src.services import revue, revue_auto
    from src.services.politiques_revue import REGLES_DEEZER, titre_d_original

    codes = {
        "deezer": {d for d, _ in REGLES_DEEZER},
        "liens_deezer": {"duree_deezer", "deezer_link_review"},
    }.get(famille, {famille})

    par_action = collections.defaultdict(list)
    echecs = []
    hors_restriction = []  # cas de la famille que la restriction rend à l'humain
    vus = 0
    for nom in noms or dm.get_artist_names():
        art = dm.get_artist_by_name(nom)
        art.tracks = dm.discographie_reunie(art)
        if famille != "durees":
            for c in revue.analyser(dm, art).actifs:
                if c.detecteur in codes and c.nature == revue.DECISION:
                    vus += 1
                    piste = ((c.preuves or {}).get("ecart") or {}).get("piste") or {}
                    if not titre_d_original(piste.get("title") or c.morceau):
                        hors_restriction.append((nom, c))
        # Le journal est HISTORIQUE : seules comptent les entrées de ce passage.
        avant = {c["id"] for c in dm.corrections_revue(art.id, retablies=True)}
        bilan = revue_auto.corriger(dm, art)
        echecs += [f"{nom} : {e}" for e in bilan.echecs]
        for c in dm.corrections_revue(art.id):
            if c["id"] not in avant:
                par_action[c["action"]].append((nom, c))
    regles = {k: v for k, v in par_action.items() if k.startswith(revue_auto.PREFIXE_REGLE)}
    formelles = {k: v for k, v in par_action.items() if k not in regles}
    validee = famille in FAMILLES_VALIDEES
    titre = f"famille « {famille} » " + ("(validée)" if validee else "(NON validée)")
    lignes = [
        f"# Passage à blanc — {titre} — {date.today()}",
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
    nom_regles = "Règles validées" if validee else "Règles (NON validées)"
    for titre, groupe in ((nom_regles, regles), ("Formelles (déjà actives)", formelles)):
        for action, liste in sorted(groupe.items()):
            lignes += ["", f"## {titre} — `{action}` ({len(liste)})", ""]
            for nom, c in sorted(liste, key=lambda x: (x[1]["detecteur"], x[0])):
                lignes.append(f"- [{nom}] `{c['detecteur']}` — {c['compte_rendu']}")
    if famille != "durees":
        lignes += [
            "",
            f"## Restriction aux originaux : {len(hors_restriction)} cas sur {vus} rendus à "
            "l'humain",
            "",
        ]
        lignes += [f"- [{n}] {c.morceau} — {c.motif}" for n, c in hors_restriction]
    if echecs:
        lignes += ["", f"## Échecs ({len(echecs)})", ""] + [f"- {e}" for e in echecs]
    return lignes


if __name__ == "__main__":
    sys.exit(main())
