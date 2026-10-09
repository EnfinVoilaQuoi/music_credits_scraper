"""source_track_failures : les échecs de communication gardés PAR MORCEAU

Revision ID: e44_source_track_failures
Revises: e43_apple_music
Create Date: 2026-09-30

Les échecs étaient archivés par source (`source_usage_failures`) sans
`track_id` — aucun capteur ne connaissait la fiche — et dans une fenêtre de 50
par source : rien ne permettait de retenter ce qui n'était pas passé. Les
boucles par morceau posent désormais le morceau ambiant (`track_scope`) et
cette table garde, par (morceau, source), le dernier échec tant que la source
n'a pas RÉPONDU pour ce morceau. Aucun backfill : l'historique n'a pas de
morceau. Définition identique, colonne par colonne et dans le même ordre, à
`src/persistence/schema.py`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e44_source_track_failures"
down_revision: str | Sequence[str] | None = "e43_apple_music"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_track_failures",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=False),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column("flow", sa.Text(), nullable=False),
        sa.Column("artist_id", sa.Integer(), nullable=True),
        sa.Column("issue", sa.Text(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("n_echecs", sa.Integer(), nullable=False),
        sa.Column("premier_echec", sa.TIMESTAMP(), nullable=True),
        sa.Column("dernier_echec", sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(["artist_id"], ["artists.id"]),
        sa.ForeignKeyConstraint(["track_id"], ["tracks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("track_id", "source_key"),
        sqlite_autoincrement=True,
    )


def downgrade() -> None:
    op.drop_table("source_track_failures")
