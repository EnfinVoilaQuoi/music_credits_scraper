"""Cache de la vérification « nouveaux titres », par artiste.

Un CACHE, pas une donnée : sa perte n'a aucune conséquence (la prochaine
ouverture re-vérifie), et il ne vit que pour éviter un appel Genius par
chargement d'artiste. C'est ce qui le distingue des morceaux désactivés
(`disabled_tracks_manager`), qui sont un travail ÉDITORIAL et doivent survivre
à tout — d'où leur fichier par artiste et leurs sauvegardes.

Il est indexé par `artist_id` : contrairement à une mémoire « vu » par morceau,
il ne pourrit pas aux fusions ni aux suppressions, puisque la liste des titres
inconnus est RECALCULÉE à chaque vérification.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from src.config import DATA_DIR
from src.utils.logger import get_logger

logger = get_logger(__name__)

FICHIER = Path(DATA_DIR) / "nouveautes_cache.json"

#: Au-delà, la vérification repart sur le réseau. Une journée : on veut savoir
#: qu'un morceau est sorti, pas à quelle heure.
FRAICHEUR_HEURES = 24


def _charger() -> dict:
    try:
        with open(FICHIER, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        logger.debug(f"Cache des nouveautés illisible ({e}) — reparti de zéro")
        return {}


def lire(artist_id: int) -> dict | None:
    """L'entrée de cet artiste, ou None. `verifie_le` est une chaîne ISO."""
    if artist_id is None:
        return None
    return _charger().get(str(artist_id))


def ecrire(artist_id: int, entree: dict) -> None:
    """Remplace l'entrée d'un artiste. Un échec d'écriture n'est JAMAIS fatal :
    au pire on refera l'appel au prochain chargement."""
    if artist_id is None:
        return
    data = _charger()
    data[str(artist_id)] = entree
    try:
        FICHIER.parent.mkdir(parents=True, exist_ok=True)
        with open(FICHIER, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except OSError as e:
        logger.debug(f"Cache des nouveautés non écrit ({e})")


def est_frais(entree: dict | None, maintenant: datetime | None = None) -> bool:
    if not entree or not entree.get("verifie_le"):
        return False
    try:
        vu = datetime.fromisoformat(entree["verifie_le"])
    except (TypeError, ValueError):
        return False
    ecart = (maintenant or datetime.now()) - vu
    return 0 <= ecart.total_seconds() < FRAICHEUR_HEURES * 3600
