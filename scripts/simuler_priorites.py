"""Simule un changement d'ordre des sources AVANT de le garder.

Règle « simuler avant d'activer » (PROJET.md, 2026-10-14) : une source passée
en tête d'un arbitrage se juge sur TOUTE la base, pas sur le cas qui l'a fait
naître. La veille, la vidéo de chaîne officielle avait été mise devant Deezer
sur le seul cas de « HLM 3 » ; simulée, elle changeait 97 durées, surtout à
tort.

Le script rejoue, fiche par fiche, l'arbitrage de chaque champ de
`DISCOGRAPHY_PRIORITIES` avec l'ordre du CLICHÉ (`tests/fixtures/
priorites_simulees.json`, le dernier ordre simulé) et avec celui du CODE, et
compte les colonnes qui changeraient. Lecture seule.

⚠ Il ne voit que les observations DÉJÀ en base : une source NOUVELLE (qui n'a
encore rien écrit) donne « 0 fiche » quel que soit son effet réel — c'était le
cas de `youtube_officiel`. Une source nouvelle se simule avec son PRODUCTEUR
(ex. le 2026-10-14 : 10 055 vidéos relues, verdicts recalculés), puis ce script
fixe le cliché. `--ecrire` met le cliché à
jour avec l'ordre du code ET le résultat de la simulation — c'est ce qui
remet au vert `tests/test_priorites_simulees.py`.

    python scripts/simuler_priorites.py            # mesure
    python scripts/simuler_priorites.py --ecrire   # mesure, puis met le cliché à jour
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from contextlib import closing
from datetime import date
from pathlib import Path

from src.config import DATA_DIR
from src.enrichment.observation import Observation
from src.enrichment.reconcile import (
    DISCOGRAPHY_PRIORITIES,
    resolve_by_priority,
    resoudre_date_de_sortie,
)

CLICHE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "priorites_simulees.json"
EXEMPLES = 5


def lire_cliche(chemin: Path = CLICHE) -> dict:
    try:
        return json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def ordres_du_code() -> dict[str, list[str]]:
    return {champ: list(ordre) for champ, ordre in DISCOGRAPHY_PRIORITIES.items()}


def verdict(champ: str, observations: list, ordre) -> object:
    """Valeur retenue pour `champ` avec cet ordre (le même calcul que l'écriture)."""
    if champ == "release_date":
        res = resoudre_date_de_sortie(observations, ordre)
    else:
        res = resolve_by_priority(observations, champ, ordre)
    return res.value if res else None


def simuler(lignes, avant: dict, apres: dict) -> dict:
    """PUR. `lignes` : (track_id, artiste, titre, champ, source, valeur). Rend, par
    champ dont l'ordre change, le nombre de fiches dont le verdict changerait et
    quelques exemples."""
    par_fiche: dict[tuple, list] = defaultdict(list)
    noms: dict[int, str] = {}
    for tid, artiste, titre, champ, source, valeur in lignes:
        par_fiche[(tid, champ)].append(Observation(champ, valeur, source))
        noms[tid] = f"{artiste} — {titre}"
    resultat = {}
    for champ in sorted(set(avant) | set(apres)):
        if avant.get(champ) == apres.get(champ) or not apres.get(champ):
            continue
        changes, exemples = 0, []
        for (tid, c), obs in par_fiche.items():
            if c != champ:
                continue
            v_avant = verdict(champ, obs, avant.get(champ) or ())
            v_apres = verdict(champ, obs, apres[champ])
            if str(v_avant) != str(v_apres):
                changes += 1
                if len(exemples) < EXEMPLES:
                    exemples.append(f"{noms[tid]} : {v_avant} → {v_apres}")
        resultat[champ] = {"fiches_changees": changes, "exemples": exemples}
    return resultat


def lire_observations(db_path: Path, champs) -> list:
    """Une lecture courte (fetchall puis fermeture) : jamais de curseur ouvert
    pendant qu'un run écrit (journal_mode=delete)."""
    marqueurs = ",".join("?" * len(champs))
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)) as c:
        return c.execute(
            "SELECT o.track_id, a.name, t.title, o.field, o.source, o.value "
            "FROM observations o JOIN tracks t ON t.id = o.track_id "
            f"JOIN artists a ON a.id = t.artist_id WHERE o.field IN ({marqueurs})",
            list(champs),
        ).fetchall()


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--ecrire", action="store_true", help="met le cliché à jour après la mesure")
    p.add_argument("--base", type=Path, default=Path(DATA_DIR) / "music_credits.db")
    a = p.parse_args(argv)
    cliche = lire_cliche()
    avant, apres = cliche.get("priorites") or {}, ordres_du_code()
    if avant == apres:
        print("L'ordre du code est celui du cliché : rien à simuler.")
        return 0
    resultat = simuler(lire_observations(a.base, apres), avant, apres)
    for champ, r in resultat.items():
        print(f"{champ} : {r['fiches_changees']} fiche(s) changeraient")
        for e in r["exemples"]:
            print(f"   {e}")
    if not a.ecrire:
        print("\n(mesure seule — relancer avec --ecrire pour mettre le cliché à jour)")
        return 0
    CLICHE.write_text(
        json.dumps(
            {
                "priorites": apres,
                "simulation": {"date": date.today().isoformat(), "resultat": resultat},
            },
            ensure_ascii=False,
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\nCliché mis à jour : {CLICHE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
