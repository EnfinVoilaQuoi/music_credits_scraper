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
from sqlalchemy.exc import SQLAlchemyError

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


def _backup_si_migration_reelle() -> None:
    """Backup avant un `alembic upgrade` lancé en ligne de commande.

    Seul le fichier SQLite est concerné : une URL non-sqlite (ou en mémoire)
    n'a rien à sauvegarder, et un échec de backup ne doit pas empêcher de
    migrer — mais il doit se VOIR, d'où le log en `warning`.
    """
    from sqlalchemy.engine import make_url

    url = make_url(config.get_main_option("sqlalchemy.url"))
    if not url.drivername.startswith("sqlite") or not url.database:
        return

    try:
        from src.persistence.bootstrap import backup_before_upgrade

        chemin = backup_before_upgrade(url.database)
        if chemin:
            print(f"Backup avant migration : {chemin}")
    except (OSError, SQLAlchemyError) as e:
        print(f"ATTENTION : backup avant migration impossible ({e}) — migration poursuivie")


def run_migrations_online() -> None:
    """Mode 'online' : utilise une connexion fournie, sinon ouvre un Engine."""
    connectable = config.attributes.get("connection", None)

    if connectable is not None:
        # Connexion déjà ouverte (bootstrap db.py, tests) : ne pas la fermer.
        _configure(connection=connectable)
        with context.begin_transaction():
            context.run_migrations()
        return

    # Pas de connexion injectée ⇒ invocation en LIGNE DE COMMANDE
    # (`alembic upgrade head`). Ce chemin ne passe pas par `upgrade_to_head`,
    # donc il ne bénéficiait d'AUCUN backup : une migration lancée au terminal
    # s'appliquait sans filet (constaté le 2026-09-05). La décision est celle de
    # `bootstrap`, pas une copie : no-op si la base est déjà à head ou vierge.
    _backup_si_migration_reelle()

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
