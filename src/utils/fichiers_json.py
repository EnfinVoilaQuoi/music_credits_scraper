"""Écriture ATOMIQUE et relecture prudente des fichiers JSON du projet (2026-10-11).

Deux défauts partagés par une dizaine de modules :

· l'écriture se faisait EN PLACE (`open(path, "w")` puis `json.dump`) : un arrêt
  au milieu — Ctrl-C, fermeture de l'app, disque plein — laissait un fichier
  tronqué, donc illisible ;
· la relecture rendait `{}` sur un fichier illisible, et l'écrivain qui
  relit-modifie-réécrit écrasait alors TOUT le fichier avec sa seule entrée.
  Pour un cache, c'est une perte de temps ; pour une MÉMOIRE (refus décidés à
  la main dans `fiches.json`, morceaux supprimés, décisions Kworb), c'est la
  perte silencieuse de décisions humaines — et le run suivant réécrit les
  données qu'elles écartaient.

`ecrire_atomique` écrit à côté puis remplace (`os.replace`, atomique sur un même
volume). `lire_memoire` distingue « absent » (`{}`) de « illisible » (lève
`MemoireIllisible`, après avoir mis le fichier de côté) : un écrivain de
mémoire refuse alors d'écrire plutôt que d'effacer.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path


class MemoireIllisible(Exception):
    """Le fichier existe mais ne se relit pas : ne PAS le réécrire par-dessus."""


def ecrire_atomique(path, donnees, *, indent: int | None = 2, fin: str = "") -> None:
    """Sérialise `donnees` dans `path` sans jamais laisser un fichier à moitié écrit."""
    ecrire_texte_atomique(path, json.dumps(donnees, ensure_ascii=False, indent=indent) + fin)


def ecrire_texte_atomique(path, texte: str) -> None:
    """Écrit `texte` dans `path` sans jamais laisser un fichier à moitié écrit
    (un JSON déjà mis en forme : la sauvegarde des décisions, une entrée par ligne)."""
    chemin = Path(path)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    temporaire = chemin.with_name(f"{chemin.name}.{os.getpid()}.tmp")
    try:
        temporaire.write_text(texte, encoding="utf-8")
        os.replace(temporaire, chemin)
    finally:
        if temporaire.exists():
            temporaire.unlink()


def lire_cache(path) -> dict:
    """Contenu d'un CACHE : `{}` s'il manque ou s'il est illisible (on repart à
    vide, la copie illisible restant à côté) — un cache se reconstruit, une
    mémoire non."""
    try:
        return lire_memoire(path)
    except MemoireIllisible:
        return {}


def lire_memoire(path) -> dict:
    """Contenu d'un fichier de MÉMOIRE : `{}` s'il n'existe pas, `MemoireIllisible`
    s'il existe sans se relire (une copie `.illisible-<date>` est laissée à côté
    pour qu'on puisse le réparer à la main)."""
    chemin = Path(path)
    try:
        texte = chemin.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as e:
        raise MemoireIllisible(f"{chemin} : {e}") from e
    try:
        return json.loads(texte)
    except ValueError as e:
        copie = chemin.with_name(f"{chemin.name}.illisible-{datetime.now():%Y%m%d_%H%M%S}")
        with contextlib.suppress(OSError):
            shutil.copy2(chemin, copie)
        raise MemoireIllisible(f"{chemin} : {e} (copie : {copie.name})") from e
