"""Parseur de date unique et permissif.

Remplace les implémentations dupliquées qui traînaient dans `Track`
(`update_release_date`, `calculate_certification_duration`) et dans la
présentation des certifications. Les dates du projet arrivent sous des formes
hétérogènes : objets `datetime`, ISO avec heure/`Z` (`2020-05-01T12:30:00Z`),
date simple (`2020-05-01`), ou variantes avec heure séparée par un espace.
"""

from datetime import datetime


def parse_flexible(value) -> datetime | None:
    """Convertit une valeur en `datetime`, ou `None` si non interprétable.

    - `None` / chaîne vide → `None`
    - `datetime` → renvoyé tel quel
    - chaîne ISO (`YYYY-MM-DD`, avec `T`/espace + heure, suffixe `Z`) → parsée
    - tout autre type ou chaîne illisible → `None`

    Un `Z` final est traité comme `+00:00` (le datetime résultant est alors
    *timezone-aware*, comme l'ancienne logique de `update_release_date`).
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None

    value = value.strip()
    if not value:
        return None

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        pass

    # Dernier recours : les 10 premiers caractères en YYYY-MM-DD
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d")
    except ValueError:
        return None
