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
    # identified = ID de plateforme ; confirmed = métadonnées strictes ;
    # suggested = information incomplète, à montrer sans la fusionner.
    confidence: str = "identified"
