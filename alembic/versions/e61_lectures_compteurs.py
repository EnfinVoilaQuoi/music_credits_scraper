"""lectures_compteurs : l'historique des compteurs Spotify lus

Revision ID: e61_lectures_compteurs
Revises: e60_videos_recherche
Create Date: 2026-10-27

`observations` ne garde qu'une lecture par (fiche, champ, source) : la suivante
écrase la précédente. Cette table garde UNE lecture par jour et par source (la
dernière du jour gagne), clé = l'ID Spotify lu, pour voir plus tard deux
compteurs partagés s'écarter, un compteur reculer, ou le rythme d'un ID que
Kworb ne liste pas. Définition identique à `src/persistence/schema.py`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e61_lectures_compteurs"
down_revision: str | Sequence[str] | None = "e60_videos_recherche"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lectures_compteurs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("spotify_id", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("valeur", sa.Integer(), nullable=False),
        sa.Column("quotidien", sa.Integer(), nullable=True),
        sa.Column("lu_le", sa.TIMESTAMP(), nullable=False),
        sa.Column("jour", sa.Text(), nullable=False),
        sa.UniqueConstraint("spotify_id", "source", "jour"),
        sqlite_autoincrement=True,
    )


def downgrade() -> None:
    op.drop_table("lectures_compteurs")
