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

Note E3 : quand `run_migrations` quittera le chemin normal de `db.py`, le
bootstrap devra d'abord amener une base restaurée à user_version 46 (copie gelée
de `run_migrations`) AVANT de stamper. Aujourd'hui `init_schema` applique encore
`run_migrations`, donc `ensure_stamped` est appelé sur une base déjà à 46.

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
    """Stampe la base si elle n'a pas encore de table `alembic_version`.

    Idempotent et permanent (voir docstring module). Non fatal : en cas d'échec,
    on log un warning et on continue — en E1, le schéma reste piloté par
    `db.py`, le stamp n'est que préparatoire.
    """
    if _has_alembic_version(db_path):
        return
    try:
        stamp_head(db_path)
        logger.info(f"Base stampée Alembic à '{LEGACY_HEAD_REVISION}' : {db_path}")
    except Exception as e:
        logger.warning(f"Stamp Alembic impossible ({db_path}) : {e}")
