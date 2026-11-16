"""Registre des recherches de certifs PAR ARTISTE : qui, quel organisme, quand.

Les sidecars des magasins (`metadata.json`) ne datent qu'un balayage par source,
tous artistes confondus : rien ne disait pour qui `certifs <nom>` avait tourné, ni
quand — une fiche sans certif ne distinguait pas « aucune » de « jamais cherché ».
Écrit par `certifs.rechercher_artiste` pour chaque source qui a abouti (2026-11-16,
demande de la liste de publications du studio : « run certifs passé et à jour »).

`data/certifications/runs_par_artiste.json` : {artiste: {source: date ISO}}.
Écriture atomique ; un fichier illisible n'est JAMAIS réécrit (il serait vidé).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from src.utils.fichiers_json import ecrire_atomique
from src.utils.logger import get_logger

logger = get_logger(__name__)

NOM_FICHIER = "runs_par_artiste.json"


def _chemin() -> Path:
    from src.enrichment.cert_source import _CERT_DIR

    return Path(_CERT_DIR) / NOM_FICHIER


def derniers_runs() -> dict[str, dict[str, str]]:
    """{artiste: {source: date ISO du dernier run abouti}} ; vide si jamais écrit."""
    chemin = _chemin()
    if not chemin.exists():
        return {}
    try:
        donnees = json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning(f"Registre des runs de certifs illisible ({chemin.name}) : {e}")
        return {}
    return donnees if isinstance(donnees, dict) else {}


def noter_run(artiste: str, source: str, quand: datetime | None = None) -> None:
    """Note que la recherche `source` a abouti pour `artiste`."""
    chemin = _chemin()
    donnees: dict = {}
    if chemin.exists():
        try:
            donnees = json.loads(chemin.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.error(f"Registre des runs de certifs illisible, NON réécrit ({chemin}) : {e}")
            return
    donnees.setdefault(artiste, {})[source] = (quand or datetime.now()).isoformat(
        timespec="seconds"
    )
    chemin.parent.mkdir(parents=True, exist_ok=True)
    ecrire_atomique(chemin, dict(sorted(donnees.items())))
