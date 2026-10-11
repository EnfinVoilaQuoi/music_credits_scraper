"""source_track_absences : la source a RÉPONDU « rien » pour ce morceau

Revision ID: e46_source_track_absences
Revises: e45_genius_detail_checked_at
Create Date: 2026-10-11

Sans mémoire d'absence, une donnée qu'une source n'a pas restait « manquante »
pour toujours et la source était redemandée à chaque run (Discogs n'a rien pour
146 des 173 morceaux d'Isha). Sept mémoires coexistaient, chacune à sa façon
(caches négatifs ReccoBeats et Musixmatch, colonnes `*_checked_at`…), et la
plupart des sources n'en avaient aucune. Une ligne par (morceau, source), posée
par l'observabilité quand la source répond `absent`, retirée quand elle répond
`ok` : le pendant de `source_track_failures` (e44) pour les absences. Aucun
backfill. Définition identique, colonne par colonne et dans le même ordre, à
`src/persistence/schema.py`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e46_source_track_absences"
down_revision: str | Sequence[str] | None = "e45_genius_detail_checked_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_track_absences",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=False),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column("premiere_absence", sa.TIMESTAMP(), nullable=True),
        sa.Column("derniere_absence", sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(["track_id"], ["tracks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("track_id", "source_key"),
        sqlite_autoincrement=True,
    )


def downgrade() -> None:
    op.drop_table("source_track_absences")
