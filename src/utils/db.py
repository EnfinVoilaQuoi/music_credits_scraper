"""Connexion SQLite + amorçage du schéma via Alembic.

`Database` centralise l'accès physique à la base : le moteur SQLAlchemy Core
(`engine`, phase E2), la connexion sqlite3 legacy (`connect()`, encore utilisée
par la façade `DataManager._get_connection` et la GUI), et l'amorçage du schéma
au démarrage (`_ensure_schema()`, phase E3b : `alembic upgrade head`, plus de
CREATE TABLE ni de migrations `user_version` en dur). Les repositories
(track/artist) et la façade en dépendent — c'est le seul endroit qui connaît le
fichier SQLite.

E3b : le schéma n'est plus créé/migré ici. Toute base est amenée au head Alembic
par `bootstrap` :
  1. `ensure_stamped` — une base pré-Alembic AVEC schéma est rattrapée (copie
     gelée `legacy_migrations`) puis stampée `e1_initial_schema` ; une base
     VIERGE est laissée telle quelle (créée à l'étape 2) ;
  2. `upgrade_to_head` — `alembic upgrade head` (crée le schéma sur une base
     vierge, applique les révisions en attente sinon), avec backup auto avant
     tout upgrade réel. Toute évolution de schéma passe désormais par une
     révision Alembic (fin du gel de schéma).
"""

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from src.utils.logger import get_logger

logger = get_logger(__name__)


# ── Adaptateurs sqlite3 date/datetime explicites ────────────────────────────
# Python 3.12+ déprécie les adaptateurs date/datetime PAR DÉFAUT du module
# sqlite3 (supprimés en 3.14) : dès qu'un `datetime`/`date` BRUT atteint la
# DBAPI en bind (colonnes date « libres » via `text()` / `date_bind`, cf.
# `src/persistence/binding.py` — SQLAlchemy convertit lui-même les colonnes
# TYPÉES avant la DBAPI, celles-là ne déclenchaient rien), un DeprecationWarning
# était émis à chaque write. On réenregistre des adaptateurs explicites qui
# reproduisent BYTE POUR BYTE l'ancien défaut (datetime → isoformat(" ") ==
# str(datetime) ; date → isoformat()) : zéro changement de stockage, warning
# supprimé, code pérenne pour 3.14. Enregistré au niveau module (process-global,
# une fois) — `db.py` est importé avant toute connexion (Database/bootstrap).
def _adapt_datetime_iso(val: datetime) -> str:
    return val.isoformat(" ")


def _adapt_date_iso(val: date) -> str:
    return val.isoformat()


sqlite3.register_adapter(datetime, _adapt_datetime_iso)
sqlite3.register_adapter(date, _adapt_date_iso)


class Database:
    """Accès physique à la base SQLite : moteur Core, connexion, schéma Alembic."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        # Moteur SQLAlchemy Core (phase E2) : NullPool = une connexion par
        # opération, reproduit EXACTEMENT le comportement de `connect()`
        # (sqlite3). Les deux visent le même fichier ; QueuePool = optimisation à
        # évaluer plus tard (au plus tôt en F).
        self.engine = create_engine(f"sqlite:///{Path(db_path).as_posix()}", poolclass=NullPool)
        self._ensure_schema()

    @contextmanager
    def connect(self):
        """Context manager pour les connexions à la base de données (sqlite3).

        Chemin legacy, encore utilisé par la façade `DataManager._get_connection`
        (GUI, `artist_loader`) — les repositories, eux, passent par `self.engine`.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _ensure_schema(self):
        """Amène la base au head Alembic (schéma à jour) au démarrage.

        Import paresseux de `bootstrap` : il tire `src.utils.logger`, or
        `src.utils.__init__` importe `DataManager` → `db` — un import au niveau
        module recréerait un cycle. Ici, `src.utils` est déjà chargé.
        """
        from src.persistence.bootstrap import ensure_stamped, upgrade_to_head

        # 1. Base pré-Alembic AVEC schéma → rattrapage legacy + stamp (une base
        #    vierge est ignorée : créée par l'upgrade ci-dessous).
        ensure_stamped(self.db_path)
        # 2. `alembic upgrade head` : crée le schéma (base vierge) ou applique les
        #    révisions en attente ; backup auto avant tout upgrade réel ; no-op si
        #    déjà à jour (démarrage normal).
        upgrade_to_head(self.db_path)
        logger.info("Base de données initialisée (schéma Alembic à jour)")
