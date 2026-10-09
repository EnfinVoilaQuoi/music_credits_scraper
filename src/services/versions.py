"""Héritage des fiches de VERSION déjà en base (2026-09-26).

`version_heritage.heriter` n'était appelé qu'à la CRÉATION d'une fiche (écarts
Deezer, remix Kworb). Les pages de version importées de Genius (« Boulbi
(Instrumental) », « Heartless (Studio Version) ») n'en recevaient jamais rien :
mesuré, 20 instrumentaux et 171 éditions sans aucun crédit de production alors
que leur original en a. Décision utilisateur : appliquer la table à l'existant,
et à chaque run discographie pour les suivantes.
"""

from __future__ import annotations

from src.utils.logger import get_logger
from src.utils.version_heritage import (
    IndexSocles,
    est_instrumental,
    famille_de,
    heriter,
    socle_parmi,
)

logger = get_logger(__name__)


def heriter_versions(dm, tracks) -> int:
    """Applique l'héritage à chaque fiche de version dont l'original est UNIQUE
    parmi `tracks` (les morceaux d'UN artiste). Rend le nombre de fiches
    enrichies. UNION seulement (`heriter`) : rien n'est remplacé, et une fiche
    qui n'a rien à recevoir n'est pas réécrite."""
    n = 0
    index = IndexSocles(tracks)  # une fois : sinon chaque version relit tout
    for version in tracks:
        if famille_de(version.title) is None:
            continue
        socle = socle_parmi(version.title, tracks, index)
        if socle is None or socle is version:
            continue
        if heriter(version, socle).vide:
            continue
        dm.save_track(version)
        n += 1
    if n:
        logger.info(f"↩ Héritage appliqué à {n} fiche(s) de version")
    return n


def constater_instrumentaux(dm, tracks) -> int:
    """Un TITRE instrumental est constaté instrumental : ni paroles ni LRC
    (décision utilisateur 2026-10-09, CORRECTIFS 1.8). L'héritage ne posait le
    constat que sur une fiche SANS texte ; or la page Genius d'un instrumental
    recopie souvent les paroles de l'original, que la synchro « confirmait »
    ensuite — 17 fiches constatées avec paroles et LRC, ~30 titres non constatés.

    Passe par l'écrivain dédié `constater_instrumental` (`save_track` ne sait
    pas vider des paroles) et met les objets en phase, sans quoi le prochain
    `save_track` du run regarnirait la colonne. Rend le nombre de fiches touchées.
    """
    n = 0
    for t in tracks:
        if t.id is None or not est_instrumental(t.title):
            continue
        if t.lyrics.instrumental is True and not t.lyrics.text and not t.lyrics.synced:
            continue
        if not dm.constater_instrumental(t.id):
            continue
        t.lyrics.instrumental = True
        t.lyrics.text = None
        t.lyrics.present = False
        t.lyrics.synced = None
        t.lyrics.synced_source = None
        t.lyrics.synced_confidence = None
        n += 1
    if n:
        logger.info(f"🎹 {n} titre(s) instrumental(aux) constaté(s) — paroles et LRC retirés")
    return n
