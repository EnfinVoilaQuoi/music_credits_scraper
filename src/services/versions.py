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
from src.utils.version_heritage import famille_de, heriter, socle_parmi

logger = get_logger(__name__)


def heriter_versions(dm, tracks) -> int:
    """Applique l'héritage à chaque fiche de version dont l'original est UNIQUE
    parmi `tracks` (les morceaux d'UN artiste). Rend le nombre de fiches
    enrichies. UNION seulement (`heriter`) : rien n'est remplacé, et une fiche
    qui n'a rien à recevoir n'est pas réécrite."""
    n = 0
    for version in tracks:
        if famille_de(version.title) is None:
            continue
        socle = socle_parmi(version.title, tracks)
        if socle is None or socle is version:
            continue
        if heriter(version, socle).vide:
            continue
        dm.save_track(version)
        n += 1
    if n:
        logger.info(f"↩ Héritage appliqué à {n} fiche(s) de version")
    return n
