"""Corrections de fiches décidées à la main — et leur MÉMOIRE (étape 3, 2026-09-24).

`data/corrections/fiches.json` (VERSIONNÉ : la base en dépend, comme
`manual_fixes.json` des certifs) : `{artiste: [entrée, …]}`. Une entrée désigne
UNE fiche (`{"genius_id": N}`, sinon `{"titre": …, "album": …}` pour une fiche
Deezer / Kworb) et porte ses actions ; `scripts/appliquer_corrections_fiches.py`
les applique.

Corriger en base ne tient qu'un tour : le run suivant réécrit ce que sa source
dit. Le fichier est donc relu par les PRODUCTEURS, à trois endroits :

  · `ids_refuses` — le gate d'identité Spotify refuse un ID retiré à la main
    (sinon Kworb ou le scraper le reposent au prochain run) ;
  · `deezer_refuses` — même chose pour une piste Deezer DÉLIÉE (écarts Deezer,
    provider par morceau) : sans elle, « L.U.N.A.T.I.C. » et « Par Amour »
    reprenaient au run suivant l'ID qu'on venait de leur retirer (2026-10-10) ;
  · `credits_refuses` — `save_track` n'écrit pas un crédit retiré (Genius
    ressert « Matthieu Cabaret — Composer », qui est le compositing du clip) ;
  · `genius_ids_absorbes` — une page Genius FUSIONNÉE dans une autre fiche n'est
    pas recréée par le run discographie.

Aucune de ces lectures ne lève : un fichier illisible vaut « aucune correction »,
et c'est dit une fois au log.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.config import DATA_DIR
from src.utils.fichiers_json import MemoireIllisible, ecrire_atomique, lire_memoire
from src.utils.logger import get_logger
from src.utils.title_matching import normalize_name, normalize_title

logger = get_logger(__name__)

FICHIER = Path(DATA_DIR) / "corrections" / "fiches.json"

_cache: dict = {"chemin": None, "mtime": None, "donnees": {}}


def charger() -> dict:
    """Le fichier, relu seulement s'il a changé (appelé par morceau et par crédit)."""
    try:
        mtime = FICHIER.stat().st_mtime
    except OSError:
        return {}
    if _cache["chemin"] == FICHIER and _cache["mtime"] == mtime:
        return _cache["donnees"]
    try:
        donnees = json.loads(FICHIER.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.error(f"Corrections de fiches illisibles ({FICHIER}) : {e} — ignorées")
        donnees = {}
    _cache.update(chemin=FICHIER, mtime=mtime, donnees=donnees)
    return donnees


def _relire_pour_ecrire() -> dict | None:
    """Le fichier, pour le MODIFIER. `None` s'il est illisible : on refuse alors
    d'écrire — réécrire par-dessus effacerait toutes les décisions qu'il garde
    (avant le 2026-10-11, un fichier illisible était remplacé par la seule
    entrée qu'on ajoutait)."""
    try:
        return lire_memoire(FICHIER)
    except MemoireIllisible as e:
        logger.error(f"Corrections de fiches illisibles, rien n'y est écrit : {e}")
        return None


def designe(designation: dict, *, genius_id, titre: str | None, album: str | None) -> bool:
    """PUR. La désignation d'une entrée vise-t-elle cette fiche ?"""
    if designation.get("genius_id") is not None:
        return genius_id is not None and int(genius_id) == int(designation["genius_id"])
    # Titre EXACT : normalisé, « Boss » désignait aussi « BOSS » — et une fois
    # « Boss » fusionnée, sa désignation retombait sur la fiche GARDÉE.
    if (designation.get("titre") or "").strip() != (titre or "").strip():
        return False
    if "album" in designation:
        return normalize_title(designation["album"] or "") == normalize_title(album or "")
    return True


def entrees_du_morceau(track) -> list[dict]:
    artiste = track.artist.name if track.artist else None
    if not artiste:
        return []
    return [
        e
        for e in charger().get(artiste, [])
        if designe(
            e.get("fiche") or {}, genius_id=track.genius_id, titre=track.title, album=track.album
        )
    ]


def entrees_de_l_enregistrement(track) -> list[dict]:
    """Les entrées de la fiche ET celles qui désignent la même page Genius chez
    un AUTRE artiste (ses lignes sœurs). Sert aux refus de ce que les sœurs
    PARTAGENT (ID, vidéos, LRC, crédits) : un refus décidé sur la ligne de
    Kanye protège aussi celle de Kid Cudi, sans quoi l'union des sœurs rendait
    la valeur refusée (chantier C, 2026-10-12 : 40 refus sur des
    enregistrements à lignes sœurs ne protégeaient que leur ligne). Pas pour
    les certifications : elles sont recalculées PAR ARTISTE."""
    entrees = list(entrees_du_morceau(track))
    if track.genius_id is None:
        return entrees
    artiste = track.artist.name if track.artist else None
    for autre, liste in charger().items():
        if autre == artiste:
            continue
        entrees += [
            e
            for e in liste
            if (e.get("fiche") or {}).get("genius_id") is not None
            and int(e["fiche"]["genius_id"]) == int(track.genius_id)
        ]
    return entrees


def ids_refuses(track) -> set[str]:
    return {
        sid for e in entrees_de_l_enregistrement(track) for sid in e.get("retirer_id_spotify") or []
    }


def deezer_refuses(track) -> set[int]:
    return {
        int(did)
        for e in entrees_de_l_enregistrement(track)
        for did in e.get("retirer_id_deezer") or []
    }


def credits_refuses(track) -> set[tuple[str, str]]:
    """{(nom normalisé, rôle)} — le rôle est la valeur de `CreditRole`."""
    return {
        (normalize_name(c["nom"]), c["role"])
        for e in entrees_de_l_enregistrement(track)
        for c in e.get("retirer_credits") or []
    }


def genius_ids_absorbes(artiste: str) -> set[int]:
    """Pages Genius fusionnées dans une autre fiche de `artiste`."""
    return {
        int(e["fiche"]["genius_id"])
        for e in charger().get(artiste, [])
        if e.get("fusionner_dans") and (e.get("fiche") or {}).get("genius_id") is not None
    }


def designation_de(track) -> dict:
    """La désignation d'une fiche dans le fichier : sa page Genius, sinon titre
    + album (fiche Deezer)."""
    if track.genius_id:
        return {"genius_id": int(track.genius_id)}
    return {"titre": track.title, "album": track.album}


def memoriser_fusion(artiste: str, absorbee: dict, gardee: dict) -> None:
    """Consigne une fusion décidée par une RÈGLE (pas à la main) : sans elle, le
    prochain import recréerait la page absorbée (`genius_ids_absorbes`)."""
    donnees = _relire_pour_ecrire()
    if donnees is None:
        return
    entrees = donnees.setdefault(artiste, [])
    if any(e.get("fiche") == absorbee and e.get("fusionner_dans") for e in entrees):
        return
    entrees.append({"fiche": absorbee, "fusionner_dans": gardee})
    ecrire_atomique(FICHIER, donnees, fin="\n")


def _ajouter_a_la_fiche(track, cle: str, valeur, meme=None) -> bool:
    """Ajoute `valeur` à la liste `cle` de l'entrée de la fiche (créée au besoin).
    Rend False si elle y était déjà (`meme(a, b)` décide de l'égalité)."""
    artiste = track.artist.name if track.artist else None
    if not artiste or valeur in (None, ""):
        return False
    meme = meme or (lambda a, b: a == b)
    donnees = _relire_pour_ecrire()
    if donnees is None:
        return False
    entrees = donnees.setdefault(artiste, [])
    fiche = designation_de(track)
    ligne = next((e for e in entrees if e.get("fiche") == fiche), None)
    if ligne is None:
        ligne = {"fiche": fiche}
        entrees.append(ligne)
    liste = ligne.setdefault(cle, [])
    if any(meme(x, valeur) for x in liste):
        return False
    liste.append(valeur)
    ecrire_atomique(FICHIER, donnees, fin="\n")
    return True


def _retirer_de_la_fiche(track, cle: str, valeur, meme=None) -> bool:
    """Pendant de `_ajouter_a_la_fiche` : oublie `valeur` de la liste `cle` (une
    correction automatique qu'on RÉTABLIT). Rend False si elle n'y était pas."""
    artiste = track.artist.name if track.artist else None
    if not artiste:
        return False
    meme = meme or (lambda a, b: a == b)
    donnees = _relire_pour_ecrire()
    if donnees is None:
        return False
    ligne = next(
        (e for e in donnees.get(artiste, []) if e.get("fiche") == designation_de(track)), None
    )
    if ligne is None or not any(meme(x, valeur) for x in ligne.get(cle) or []):
        return False
    ligne[cle] = [x for x in ligne[cle] if not meme(x, valeur)]
    if not ligne[cle]:
        del ligne[cle]
    ecrire_atomique(FICHIER, donnees, fin="\n")
    return True


def memoriser_id_refuse(track, spotify_id: str) -> None:
    """Consigne un ID Spotify retiré À LA MAIN (panneau « À trancher ») : relu par
    le gate d'identité (`ids_refuses`), sans quoi Kworb ou le scraper le
    reposeraient au prochain run."""
    _ajouter_a_la_fiche(track, "retirer_id_spotify", spotify_id)


def memoriser_deezer_refuse(track, deezer_id: int) -> None:
    """Consigne une piste Deezer DÉLIÉE de la fiche (`deezer_refuses`) : ni les
    écarts Deezer ni le provider par morceau ne la reposent."""
    _ajouter_a_la_fiche(track, "retirer_id_deezer", int(deezer_id))


def cle_certif(entree: dict) -> tuple:
    """PUR. L'identité d'une certification rattachée : organisme, titre certifié,
    palier, date — ce qui la distingue d'une autre certif du même titre."""
    return (
        (entree.get("body") or "").strip().upper(),
        normalize_title(entree.get("title") or ""),
        (entree.get("certification") or "").strip().casefold(),
        str(entree.get("certification_date") or "")[:10],
    )


def certifs_refusees(track) -> set[tuple]:
    """Certifications retirées À LA MAIN de cette fiche (panneau « À trancher ») :
    `apply_certifications` ne les rattache plus, sans quoi le matcher les
    reposerait au prochain « Appliquer les certifs »."""
    return {
        cle_certif(c) for e in entrees_du_morceau(track) for c in e.get("retirer_certifs") or []
    }


def memoriser_certif_refusee(track, entree: dict) -> None:
    _ajouter_a_la_fiche(
        track,
        "retirer_certifs",
        {k: entree.get(k) for k in ("body", "title", "certification", "certification_date")},
        meme=lambda a, b: cle_certif(a) == cle_certif(b),
    )


def oublier_certif_refusee(track, entree: dict) -> bool:
    return _retirer_de_la_fiche(
        track,
        "retirer_certifs",
        entree,
        meme=lambda a, b: cle_certif(a) == cle_certif(b),
    )


def videos_refusees(track) -> set[str]:
    """Vidéos rejetées À LA MAIN (✖️ de la fiche ou du panneau) : ni Genius, ni la
    recherche, ni le canal YTM ne les rattachent plus à cette fiche (2026-09-27 —
    avant, le run discographie reposait le lien Genius rejeté)."""
    return {v for e in entrees_de_l_enregistrement(track) for v in e.get("retirer_videos") or []}


def memoriser_video_refusee(track, video_id: str) -> None:
    _ajouter_a_la_fiche(track, "retirer_videos", video_id)


def empreinte_lrc(lrc: str | None) -> str:
    """PUR. L'identité d'un LRC : son TEXTE (espaces normalisés), pas sa source —
    la même source peut servir plus tard un autre LRC, qui ne doit pas être
    bloqué par le refus du premier."""
    import hashlib
    import re

    return hashlib.sha1(re.sub(r"\s+", " ", (lrc or "").strip()).encode("utf-8")).hexdigest()[:16]


def lrc_refuses(track) -> set[str]:
    """Empreintes des LRC retirés À LA MAIN : le résolveur ne les retient plus."""
    return {x for e in entrees_de_l_enregistrement(track) for x in e.get("retirer_lrc") or []}


def memoriser_lrc_refuse(track, lrc: str) -> None:
    _ajouter_a_la_fiche(track, "retirer_lrc", empreinte_lrc(lrc))


def oublier_lrc_refuse(track, lrc: str) -> bool:
    return _retirer_de_la_fiche(track, "retirer_lrc", empreinte_lrc(lrc))
