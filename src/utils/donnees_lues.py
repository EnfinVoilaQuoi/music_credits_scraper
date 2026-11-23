"""Le dossier `data/` à LIRE, et la garde du worktree sans données (2026-11-23).

Dans un worktree git (`.git` y est un FICHIER), `data/` est vide : le brut,
les pages désactivées… n'y sont pas, et une mesure lancée là (script à blanc,
audit d'une copie, simulation) lisait un brut vide SANS RIEN DIRE — résultats
faux en silence (CORRECTIFS, « leçon seule » jusqu'ici). Le lot
(`scripts/lot_runs.py`) pose une jonction `data` vers le vrai dossier dans son
worktree figé : le brut y est, la garde ne dit rien.

Désormais, la lecture d'un de ces magasins depuis un worktree dont `data/` n'a
pas le brut LÈVE `DonneesAbsentes`, avec le moyen d'y pointer : le réglage
`DONNEES_LUES_DIR` (`Settings.donnees_lues_dir`) désigne le `data/` réel, lu
SEULEMENT (les écrivains refusent d'y écrire). Rien n'est redirigé en douce.

Un chemin redirigé par l'appelant (tests, `--donnees`, `--brut` d'un script)
ne passe pas par ici : la garde ne vise que le chemin de la configuration.
"""

from __future__ import annotations

from pathlib import Path

from src import config

#: Ce qui fait d'un `data/` le vrai : le brut des sources (~1 Go, jamais dans
#: un worktree neuf).
MARQUEUR = Path("brut") / "brut.db"


class DonneesAbsentes(RuntimeError):
    """Un magasin de `data/` lu depuis un worktree qui ne l'a pas."""


def dans_un_worktree(racine: Path | None = None) -> bool:
    """Le code tourne-t-il depuis un worktree git ? (`.git` y est un fichier.)"""
    return (Path(racine or config.BASE_DIR) / ".git").is_file()


def dossier_des_donnees(
    racine: Path | None = None, data_dir: Path | None = None
) -> tuple[Path, bool]:
    """`(dossier data/ à lire, lecture_seule)`.

    `DONNEES_LUES_DIR` réglé → ce dossier, en lecture seule (il doit avoir le
    brut) ; sinon le `data/` du code, sauf dans un worktree qui n'a pas le
    brut : `DonneesAbsentes`."""
    racine = Path(racine or config.BASE_DIR)
    data_dir = Path(data_dir or config.DATA_DIR)
    regle = config.settings.donnees_lues_dir
    if regle:
        dossier = (racine / regle).resolve()
        if not (dossier / MARQUEUR).exists():
            raise DonneesAbsentes(
                f"DONNEES_LUES_DIR={regle} : {dossier / MARQUEUR} introuvable "
                "(il doit désigner le data/ réel du dépôt principal)"
            )
        return dossier, True
    if dans_un_worktree(racine) and not (data_dir / MARQUEUR).exists():
        raise DonneesAbsentes(
            f"Code lancé depuis un worktree git ({racine}) dont data/ n'a pas le brut "
            f"({data_dir / MARQUEUR}) : la mesure lirait des données vides. Pointer le "
            "data/ réel, en lecture seule, par la variable d'environnement "
            "DONNEES_LUES_DIR=<dépôt principal>/data (ou l'option --donnees / --brut "
            "du script)."
        )
    return data_dir, False
