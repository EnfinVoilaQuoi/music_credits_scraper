"""revue_verdicts + revue_signalements : le panneau « À trancher », étape 3

Revision ID: e38_revue
Revises: e37_track_editions
Create Date: 2026-09-27

`revue_verdicts` mémorise les DÉCISIONS de l'utilisateur sur un cas (un cas
tranché n'est plus reproposé tant que ses preuves ne changent pas) ;
`revue_signalements` garde les cas que seuls les runs savent (oracles réseau),
remplacés à chaque run pour l'artiste traité. Tables seules, aucune donnée
déplacée. Les colonnes suivent À L'IDENTIQUE l'ordre de `src/persistence/schema.py`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e38_revue"
down_revision: str | Sequence[str] | None = "e37_track_editions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "revue_verdicts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("artist_id", sa.Integer(), nullable=False),
        sa.Column("detecteur", sa.Text(), nullable=False),
        sa.Column("cle", sa.Text(), nullable=False),
        sa.Column("verdict", sa.Text(), nullable=False),
        sa.Column("morceau", sa.Text(), nullable=True),
        sa.Column("motif", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(["artist_id"], ["artists.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("artist_id", "detecteur", "cle"),
        sqlite_autoincrement=True,
    )
    op.create_table(
        "revue_signalements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("artist_id", sa.Integer(), nullable=False),
        sa.Column("detecteur", sa.Text(), nullable=False),
        sa.Column("cle", sa.Text(), nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=True),
        sa.Column("morceau", sa.Text(), nullable=True),
        sa.Column("motif", sa.Text(), nullable=True),
        sa.Column("preuves", sa.Text(), nullable=True),
        sa.Column("impact", sa.Integer(), nullable=True),
        sa.Column("seen_at", sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(["artist_id"], ["artists.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("artist_id", "detecteur", "cle"),
        sqlite_autoincrement=True,
    )


def downgrade() -> None:
    op.drop_table("revue_signalements")
    op.drop_table("revue_verdicts")
