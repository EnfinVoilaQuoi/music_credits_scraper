"""Environnement Alembic — migration SQLAlchemy Core (phase E).

Points clés vis-à-vis du projet :
  - ``target_metadata = src.persistence.schema.metadata`` : source unique de
    vérité du schéma (autogenerate + révision initiale en dérivent).
  - ``render_as_batch=True`` : SQLite ne sait pas ALTER en place → Alembic
    reconstruit la table (batch). Obligatoire dès le départ (note AUDIT §7).
  - **Connexion programmatique** : `db.py` (bootstrap E1d/E3) et les tests
    passent une connexion déjà ouverte via ``config.attributes["connection"]``
    (recette « run within a transaction »). À défaut, on ouvre un Engine depuis
    ``sqlalchemy.url`` (NullPool, comportement constant — cf. E2).

`import src.persistence.schema` fonctionne partout grâce à `pip install -e .`
(packaging 2026-07-11) — ne PAS réintroduire de hack sys.path.
"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from src.persistence.schema import metadata as target_metadata

# Objet Config Alembic (accès aux valeurs du .ini en cours).
config = context.config

# Logging Python depuis le .ini — seulement si un fichier de config est fourni
# (absent en usage purement programmatique).
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _configure(**kwargs) -> None:
    context.configure(
        target_metadata=target_metadata,
        render_as_batch=True,  # SQLite : ALTER via rebuild (batch)
        **kwargs,
    )


def run_migrations_offline() -> None:
    """Mode 'offline' : génère le SQL à partir d'une simple URL (sans DBAPI)."""
    _configure(
        url=config.get_main_option("sqlalchemy.url"),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Mode 'online' : utilise une connexion fournie, sinon ouvre un Engine."""
    connectable = config.attributes.get("connection", None)

    if connectable is not None:
        # Connexion déjà ouverte (bootstrap db.py, tests) : ne pas la fermer.
        _configure(connection=connectable)
        with context.begin_transaction():
            context.run_migrations()
        return

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        _configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
