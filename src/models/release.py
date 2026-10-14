"""Valeurs transitoires décrivant une parution vue par une source.

Une observation ne modifie jamais ``Track.album``. Elle est consommée par le
repository dans la transaction de sauvegarde puis retirée de la fiche en
mémoire : ce n'est donc pas une seconde source de vérité du modèle Track.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ReleaseObservation:
    title: str
    source: str
    credited_artist_name: str | None = None
    release_date: datetime | str | None = None
    record_type: str | None = None
    scope: str = "own"
    external_release_id: str | int | None = None
    external_track_id: str | int | None = None
    disc_number: int | None = None
    track_number: int | None = None
    #: Code-barres UPC/EAN de l'édition (e54) : relie les sources entre elles.
    upc: str | None = None
    # identified = ID de plateforme ; confirmed = métadonnées strictes ;
    # suggested = information incomplète, à montrer sans la fusionner.
    confidence: str = "identified"
    #: (e55) Le libellé de place tel que la source l'écrit (« A1 », « 1-03 ») ;
    #: `disc_number`/`track_number` en sont la conversion.
    position_brute: str | None = None
    #: Ne crée JAMAIS de parution : rejoint seulement celle qu'un identifiant
    #: ou le code-barres désigne déjà, sinon l'observation est ignorée. Pour une
    #: source dont chaque pressage est un objet (Discogs : vinyle, CD, cassette)
    #: et qui ouvrirait sinon une parution par pressage.
    rattacher_seulement: bool = False
