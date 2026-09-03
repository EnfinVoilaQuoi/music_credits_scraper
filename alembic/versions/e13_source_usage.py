"""Usage réel des sources : compteurs agrégés + fenêtre des derniers échecs

Revision ID: e13_source_usage
Revises: e12_drop_audio_columns
Create Date: 2026-09-03

Chantier « état des sources nourri par l'usage réel ». Jusqu'ici la santé des
sources ne reposait que sur des sondes actives ponctuelles ; les centaines
d'appels réels d'un enrichissement étaient loggés puis jetés.

  source_usage_daily     — compteurs agrégés, forme LONGUE : une ligne par
                           (jour, source, artiste, flux, nature de verdict).
                           Ajouter une nature ne coûtera aucune migration.
  source_usage_failures  — les derniers échecs par source (fenêtre de 50),
                           pour le diagnostic : nature, message, morceau.

Deux `create_table` : pas de `batch_alter_table` (on ne modifie aucune table
existante). Les définitions reproduisent À L'IDENTIQUE, colonne par colonne et
DANS LE MÊME ORDRE, celles de `src/persistence/schema.py` — `test_alembic_baseline`
et `test_schema_reflects_db` comparent les deux et sont sensibles à l'ordre.

Aucun index en v1 : quelques milliers de lignes par an ne le justifient pas, et
un index déclaré d'un seul côté ferait diverger `create_all` de `upgrade head`.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e13_source_usage"
down_revision: Union[str, Sequence[str], None] = "e12_drop_audio_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Crée les deux tables d'usage des sources."""
    op.create_table(
        "source_usage_daily",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Text(), nullable=False),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column("artist_id", sa.Integer(), nullable=True),
        sa.Column("flow", sa.Text(), nullable=False),
        sa.Column("issue", sa.Text(), nullable=False),
        sa.Column("n_calls", sa.Integer(), nullable=True),
        sa.Column("n_attempts", sa.Integer(), nullable=True),
        sa.Column("expected_blocked", sa.Integer(), nullable=True),
        sa.Column("latency_ms_total", sa.Integer(), nullable=True),
        sa.Column("last_seen", sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(["artist_id"], ["artists.id"]),
        sa.PrimaryKeyConstraint("id"),
        # Filet pour les lignes à artiste renseigné SEULEMENT : SQLite tient
        # deux NULL pour distincts, donc l'upsert null-safe du repository reste
        # la vraie garantie d'unicité.
        sa.UniqueConstraint("day", "source_key", "artist_id", "flow", "issue"),
        sqlite_autoincrement=True,
    )
    op.create_table(
        "source_usage_failures",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column("issue", sa.Text(), nullable=False),
        sa.Column("artist_id", sa.Integer(), nullable=True),
        sa.Column("track_id", sa.Integer(), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(["artist_id"], ["artists.id"]),
        sa.ForeignKeyConstraint(["track_id"], ["tracks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sqlite_autoincrement=True,
    )


def downgrade() -> None:
    """Retire les deux tables (aucune donnée métier n'en dépend)."""
    op.drop_table("source_usage_failures")
    op.drop_table("source_usage_daily")
