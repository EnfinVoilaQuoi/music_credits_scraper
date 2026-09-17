"""Corrections MANUELLES des libellés de certifications (fichier par source).

Le SNEP a gravé un « ? » à la place de caractères perdus lors d'une vieille
migration. `cert_normalize.restore_apostrophes` en restaure automatiquement une
partie — mais UNIQUEMENT dans les contextes sûrs (élisions, contractions
anglaises, mots en Œ connus). Mesuré le 2026-09-06 sur le CSV réel : il reste
102 « ? », dont l'écrasante majorité sont de VRAIS points d'interrogation
(« QUI SAIT ? », « ...READY FOR IT? »). Ce qui reste vraiment corrompu tient en
une poignée de titres — trop peu, et trop irrégulier, pour un motif automatique.

D'où ce fichier : l'utilisateur corrige à la main, et la correction est
RÉAPPLIQUÉE à chaque nettoyage. Corriger le CSV en place ne tiendrait qu'un
tour : le SNEP ressert la ligne fautive à la première ré-importation, et la
correction serait perdue sans que personne s'en aperçoive.

Deux décisions humaines y sont conservées, et elles ont autant de valeur l'une
que l'autre : une CORRECTION (« ce libellé est cassé, voici le bon ») et une
ACCEPTATION (« ce ? est un vrai point d'interrogation, ne me le represente
plus »). Sans la seconde, la liste des libellés à revoir ne décroît jamais et
redemande éternellement d'arbitrer les mêmes titres.

Format (`data/certifications/<source>/manual_fixes.json`) :

    {
      "version": 1,
      "fixes": {
        "LES ENFOIRES|ORGANIZ?": {"artist": "LES ENFOIRES", "title": "ORGANIZÉ"}
      },
      "acceptes": ["ADE|ET ALORS ?"]
    }

La clé est produite par `cert_normalize.cle_correction` (artiste|titre mis à
plat) : elle survit à une différence de casse ou d'espaces, mais reste indexée
sur le libellé CORROMPU — c'est lui qui revient à chaque import.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from src.utils.cert_normalize import (
    cle_correction,
    contient_caractere_corrompu,
    libelle_tronque,
    restore_apostrophes,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)

FICHIER = "manual_fixes.json"


def chemin_fixes(source: str) -> Path:
    """Chemin du fichier de corrections d'une source ('snep', 'brma', 'riaa')."""
    from src.config import DATA_PATH

    return Path(DATA_PATH) / "certifications" / source.lower() / FICHIER


def charger_fixes(source: str) -> dict:
    """Corrections d'une source : `{clé: {"artist": …, "title": …}}`.

    Fichier absent ou illisible → dictionnaire vide : une correction manquante
    doit dégrader le nettoyage, jamais l'empêcher.
    """
    chemin = chemin_fixes(source)
    if not chemin.exists():
        return {}
    try:
        data = json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning(f"Corrections manuelles {source} illisibles ({e}) — ignorées")
        return {}
    fixes = data.get("fixes")
    return fixes if isinstance(fixes, dict) else {}


def charger_acceptes(source: str) -> set[str]:
    """Clés des libellés VALIDÉS TELS QUELS (le « ? » y est légitime)."""
    chemin = chemin_fixes(source)
    if not chemin.exists():
        return set()
    try:
        data = json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    acceptes = data.get("acceptes")
    return set(acceptes) if isinstance(acceptes, list) else set()


def accepter(source: str, artist: str, title: str) -> None:
    """Mémorise qu'un libellé est CORRECT tel quel : il ne sera plus proposé."""
    acceptes = charger_acceptes(source)
    acceptes.add(cle_correction(artist, title))
    ecrire_fixes(source, charger_fixes(source), acceptes)


def enregistrer_fix(
    source: str, artist: str, title: str, artist_fixe: str, title_fixe: str
) -> None:
    """Ajoute (ou remplace) une correction et réécrit le fichier."""
    fixes = charger_fixes(source)
    fixes[cle_correction(artist, title)] = {
        "artist": artist_fixe,
        "title": title_fixe,
        "avant": f"{artist} — {title}",
    }
    ecrire_fixes(source, fixes)


def retirer_fix(source: str, artist: str, title: str) -> bool:
    """Retire une correction. True si elle existait."""
    fixes = charger_fixes(source)
    if fixes.pop(cle_correction(artist, title), None) is None:
        return False
    ecrire_fixes(source, fixes)
    return True


def ecrire_fixes(source: str, fixes: dict, acceptes: set[str] | None = None) -> None:
    chemin = chemin_fixes(source)
    if acceptes is None:
        acceptes = charger_acceptes(source)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(
        json.dumps(
            {"version": 1, "fixes": fixes, "acceptes": sorted(acceptes)},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.info(
        f"Corrections manuelles {source} : {len(fixes)} correction(s), "
        f"{len(acceptes)} acceptation(s) → {chemin}"
    )


def candidats_a_corriger(
    rows: list[list[str]],
    fixes: dict,
    acceptes: set[str] | None = None,
    oracle: Callable[[str, str], bool | None] | None = None,
) -> list[tuple]:
    """Libellés qu'aucune réparation automatique ne sait remettre d'aplomb.

    DEUX défauts y mènent, et aucun des deux n'est devinable :

      · un « ? » que `restore_apostrophes` ne couvre pas. Elle ne traite que les
        contextes sûrs (élisions, contractions, mots en Œ connus) parce que la
        plupart des « ? » sont de VRAIS points d'interrogation — mesuré, 78 sur
        91. Élargir les motifs abîmerait plus de titres qu'il n'en réparerait.
      · un libellé TRONQUÉ par la source à l'endroit d'un caractère accentué
        (« L'empire du c »). Là il ne manque pas un caractère mais du TEXTE, et
        rien ne dit lequel : cherché le 2026-09-06, aucun titre tronqué n'a sa
        version complète ailleurs dans le corpus. Il n'y a donc rien à deviner,
        seulement quelqu'un à qui demander.

    `oracle(artiste, titre)` (2026-09-17) : le verdict des slugs du site
    (`snep_slugs.verdict_troncature`) — False innocente un libellé que le crible
    croyait coupé (« Le Tour de M » est complet), None laisse le crible parler.
    L'oracle n'ACCUSE jamais seul : un slug plus long qu'un titre non signalé est
    le cas normal des titres à apostrophe, où le slug perd des lettres.

    `rows` = lignes déjà découpées (champ 0 = artiste, champ 1 = titre).
    Retourne `[(suspect, artiste, titre, correction_existante), …]`, les cas
    SUSPECTS d'abord : « ? » entre deux lettres ou libellé coupé, donc défaut
    quasi certain. Le reste (« QUI SAIT ? », « ...READY FOR IT? ») est montré
    pour permettre de trancher, jamais pour suggérer qu'il faut le corriger.

    Fonction PURE (aucune E/S, aucun widget) : c'est elle qui porte les
    décisions du tri, et elle serait intestable enfouie dans la fenêtre.
    """
    vus: set[str] = set()
    candidats: list[tuple] = []
    for champs in rows:
        if len(champs) < 2:
            continue
        artiste, titre = champs[0].strip(), champs[1].strip()
        tronque = libelle_tronque(artiste) or libelle_tronque(titre)
        if tronque and oracle is not None and oracle(artiste, titre) is False:
            tronque = False
        if "?" not in artiste + titre and not tronque:
            continue
        cle = cle_correction(artiste, titre)
        if cle in vus or cle in (acceptes or ()):
            continue
        vus.add(cle)
        # Ce que la restauration automatique répare déjà n'a pas à être saisi —
        # sauf si le libellé est AUSSI tronqué, auquel cas il reste à voir.
        auto_artiste, _ = restore_apostrophes(artiste)
        auto_titre, _ = restore_apostrophes(titre)
        if "?" not in auto_artiste + auto_titre and not tronque:
            continue
        suspect = (
            tronque or contient_caractere_corrompu(artiste) or contient_caractere_corrompu(titre)
        )
        candidats.append((suspect, artiste, titre, fixes.get(cle)))

    candidats.sort(key=lambda c: (not c[0], c[1], c[2]))
    return candidats
