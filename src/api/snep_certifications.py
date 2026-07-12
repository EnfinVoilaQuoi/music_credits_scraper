"""Fraîcheur des certifications SNEP.

La gestion des certifs SNEP est passée au modèle « brut + clean » : le brut
`certif-.csv` est fusionné dans `certif_snep.csv` (voir `src/utils/snep_build.py`),
lu directement par le matcher unifié (`src/utils/cert_matcher.py`), comme
BRMA/RIAA. Ce module ne conserve que la lecture de la fraîcheur (sidecar
`certif_snep.meta.json`).

L'ancien `SNEPCertificationManager` (base intermédiaire `certifications.db`,
import CSV → DB, méthodes de matching DB) a été retiré : la normalisation vit
dans `src/utils/cert_normalize.py`, le nettoyage dans le builder, le matching
dans le matcher unifié.
"""

import json
from pathlib import Path

from src.config import DATA_PATH
from src.utils.logger import get_logger

logger = get_logger(__name__)


def get_snep_last_update(source: str | None = "GLOBAL") -> str | None:
    """Date ISO de dernière régénération du CSV canonique, lue depuis le sidecar
    `certif_snep.meta.json`. Sûr à appeler depuis n'importe quel thread (GUI).

    Le suivi est PAR SOURCE (`updates`) : `source='ARTIST'` renvoie la dernière
    récup par artiste, tout le reste ('GLOBAL' par défaut) renvoie la MàJ NON
    artiste la plus récente — pour ne pas faire passer une recherche artiste
    pour une MàJ globale (fix JOURNAL 2026-06-25).
    """
    meta_path = Path(DATA_PATH) / "certifications" / "snep" / "certif_snep.meta.json"
    if not meta_path.exists():
        return None
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"Lecture certif_snep.meta.json impossible : {e}")
        return None

    updates = data.get("updates") or {}
    if source == "ARTIST":
        return updates.get("ARTIST")
    non_artist = [t for s, t in updates.items() if s != "ARTIST"]
    if non_artist:
        return max(non_artist)
    # Repli (meta ancien format sans 'updates')
    if data.get("last_source", data.get("source")) != "ARTIST":
        return data.get("last_update")
    return None
