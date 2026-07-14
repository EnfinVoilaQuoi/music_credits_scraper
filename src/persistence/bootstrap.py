"""Bootstrap Alembic pour bases pré-Alembic (phase E1d).

Rôle : rendre une base créée par l'ancien mécanisme (`db.py` : CREATE TABLE +
migrations `user_version`, SANS table `alembic_version`) reconnaissable par
Alembic, en la **stampant** à la révision de base — sans rejouer d'`upgrade`
(le schéma est déjà là).

Mapping legacy → Alembic :
    PRAGMA user_version = 46  ≡  révision « e1_initial_schema » (head actuel)

**Permanent, pas one-shot** : `ensure_stamped()` est appelé à CHAQUE init de la
base. Tant que `alembic_version` est absente (ex. un backup pré-Alembic restauré
via `database_backup.py`), la base est re-stampée ; une fois stampée, c'est un
simple test de présence de table (aucun import Alembic). C'est ce qui garantit
qu'après E3 (`alembic upgrade head` au démarrage), une base restaurée garde un
chemin de migration cohérent.

E3a : `ensure_stamped` amène désormais une base pré-Alembic AVEC schéma à
user_version 46 (copie gelée `legacy_migrations.run_migrations`) AVANT de la
stamper — indispensable pour un backup restauré à un `user_version < 46` une
fois que `run_migrations` aura quitté le chemin normal de `db.py` (E3b). Une
base VIERGE n'est PAS stampée : `alembic upgrade head` la créera de zéro (E3b).

Imports Alembic/SQLAlchemy **paresseux** (dans `stamp_head`) : le démarrage
normal (base déjà stampée) ne paie jamais le coût d'import.
"""

import sqlite3
from pathlib import Path

from src.utils.logger import get_logger

logger = get_logger(__name__)

# Racine du projet (src/persistence/bootstrap.py → parent×3) : y trouver alembic/.
_ALEMBIC_DIR = Path(__file__).resolve().parent.parent.parent / "alembic"

# user_version 46 (dernière entrée de _MIGRATIONS dans db.py) ≡ cette révision.
LEGACY_HEAD_REVISION = "e1_initial_schema"


def make_alembic_config(connection=None):
    """Construit une Config Alembic SANS fichier .ini (donc sans reconfigurer le
    logging global de l'app via fileConfig) : seul `script_location` est requis,
    la connexion déjà ouverte est passée par `attributes` (cf. alembic/env.py).
    """
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(_ALEMBIC_DIR))
    if connection is not None:
        cfg.attributes["connection"] = connection
    return cfg


def _has_alembic_version(db_path: str) -> bool:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='alembic_version'"
        ).fetchone()
    return row is not None


def _has_schema(db_path: str) -> bool:
    """La base a-t-elle déjà le schéma métier (table `artists`) ? Sert à
    distinguer une base VIERGE (à créer par `alembic upgrade head`) d'une base
    pré-Alembic AVEC schéma (à stamper)."""
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='artists'"
        ).fetchone()
    return row is not None


def _catch_up_legacy(db_path: str) -> None:
    """Amène une base pré-Alembic à `user_version = 46` (schéma complet) via la
    copie GELÉE des migrations legacy, AVANT de la stamper à `e1_initial_schema`.

    Indispensable pour un vieux backup restauré (`user_version < 46`, colonnes
    manquantes) : le stamper sans rattraper mentirait sur son schéma. Sur une
    base déjà à 46, `run_migrations` sort immédiatement (no-op).
    """
    from src.persistence.legacy_migrations import run_migrations

    with sqlite3.connect(db_path) as conn:
        run_migrations(conn.cursor())
        conn.commit()


def stamp_head(db_path: str) -> None:
    """Stampe la base à la révision head (crée `alembic_version` + 1 ligne)."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    from alembic import command

    engine = create_engine(f"sqlite:///{Path(db_path).as_posix()}", poolclass=NullPool)
    try:
        with engine.connect() as connection:
            command.stamp(make_alembic_config(connection), "head")
            connection.commit()
    finally:
        engine.dispose()


def ensure_stamped(db_path: str) -> None:
    """Rend une base pré-Alembic reconnaissable par Alembic.

    - base déjà stampée (`alembic_version` présente) → rien ;
    - base VIERGE (pas de schéma) → rien : c'est `alembic upgrade head` (E3) qui
      crée tout depuis la révision de base — surtout NE PAS stamper une base
      vide (elle serait déclarée « à jour » sans aucune table) ;
    - base pré-Alembic AVEC schéma → rattrapage legacy jusqu'à 46 puis stamp à
      `e1_initial_schema` (le schéma est déjà là, on ne rejoue pas l'upgrade).

    Idempotent et permanent (voir docstring module). Non fatal : en cas d'échec,
    on log un warning et on continue.
    """
    if _has_alembic_version(db_path):
        return
    if not _has_schema(db_path):
        return
    try:
        _catch_up_legacy(db_path)
        stamp_head(db_path)
        logger.info(f"Base stampée Alembic à '{LEGACY_HEAD_REVISION}' : {db_path}")
    except Exception as e:
        logger.warning(f"Stamp Alembic impossible ({db_path}) : {e}")
