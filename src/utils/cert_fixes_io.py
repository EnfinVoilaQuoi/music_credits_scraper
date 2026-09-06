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

Format (`data/certifications/<source>/manual_fixes.json`) :

    {
      "version": 1,
      "fixes": {
        "LES ENFOIRES|ORGANIZ?": {"artist": "LES ENFOIRES", "title": "ORGANIZÉ"}
      }
    }

La clé est produite par `cert_normalize.cle_correction` (artiste|titre mis à
plat) : elle survit à une différence de casse ou d'espaces, mais reste indexée
sur le libellé CORROMPU — c'est lui qui revient à chaque import.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.utils.cert_normalize import (
    cle_correction,
    contient_caractere_corrompu,
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


def ecrire_fixes(source: str, fixes: dict) -> None:
    chemin = chemin_fixes(source)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(
        json.dumps({"version": 1, "fixes": fixes}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(f"Corrections manuelles {source} : {len(fixes)} entrée(s) → {chemin}")


def candidats_a_corriger(rows: list[list[str]], fixes: dict) -> list[tuple]:
    """Libellés porteurs d'un « ? » que la restauration AUTOMATIQUE ne règle pas.

    `rows` = lignes déjà découpées (champ 0 = artiste, champ 1 = titre).
    Retourne `[(suspect, artiste, titre, correction_existante), …]`, les cas
    SUSPECTS d'abord — « ? » entre deux lettres, donc corruption quasi certaine.
    Le reste (« QUI SAIT ? », « ...READY FOR IT? ») est presque toujours un vrai
    point d'interrogation : on le montre pour permettre de trancher, jamais pour
    suggérer qu'il faut le corriger.

    Fonction PURE (aucune E/S, aucun widget) : c'est elle qui porte les trois
    décisions du tri, et elle serait intestable enfouie dans la fenêtre.
    """
    vus: set[str] = set()
    candidats: list[tuple] = []
    for champs in rows:
        if len(champs) < 2:
            continue
        artiste, titre = champs[0].strip(), champs[1].strip()
        if "?" not in artiste + titre:
            continue
        cle = cle_correction(artiste, titre)
        if cle in vus:
            continue
        vus.add(cle)
        # Ce que la restauration automatique répare déjà n'a pas à être saisi.
        auto_artiste, _ = restore_apostrophes(artiste)
        auto_titre, _ = restore_apostrophes(titre)
        if "?" not in auto_artiste + auto_titre:
            continue
        suspect = contient_caractere_corrompu(artiste) or contient_caractere_corrompu(titre)
        candidats.append((suspect, artiste, titre, fixes.get(cle)))

    candidats.sort(key=lambda c: (not c[0], c[1], c[2]))
    return candidats
