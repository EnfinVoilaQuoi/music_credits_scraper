"""revue_corrections : journal des corrections automatiques du panneau « À trancher »

Revision ID: e39_revue_corrections
Revises: e38_revue
Create Date: 2026-09-27

Les détecteurs FORMELS (LRC d'une autre fiche, certification d'un autre titre)
corrigent sans attendre l'utilisateur ; chaque correction est consignée ici avec
de quoi la défaire. Table seule, aucune donnée déplacée. Les colonnes suivent À
L'IDENTIQUE l'ordre de `src/persistence/schema.py`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e39_revue_corrections"
down_revision: str | Sequence[str] | None = "e38_revue"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "revue_corrections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("artist_id", sa.Integer(), nullable=False),
        sa.Column("detecteur", sa.Text(), nullable=False),
        sa.Column("cle", sa.Text(), nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=True),
        sa.Column("morceau", sa.Text(), nullable=True),
        sa.Column("motif", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("compte_rendu", sa.Text(), nullable=True),
        sa.Column("annulation", sa.Text(), nullable=True),
        sa.Column("applied_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("retablie_at", sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(["artist_id"], ["artists.id"]),
        sa.PrimaryKeyConstraint("id"),
        sqlite_autoincrement=True,
    )


def downgrade() -> None:
    op.drop_table("revue_corrections")
