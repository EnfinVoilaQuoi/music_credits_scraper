"""track_videos.channel / channel_id : la chaîne de CHAQUE vidéo

Revision ID: e63_videos_chaine
Revises: e62_videos_duree
Create Date: 2026-10-29

La passe YouTube Music lit la chaîne de chaque vidéo (`snippet`, même appel
que la durée) sans la garder. Or la chaîne « X - Topic » d'un artiste dit à
quelle page revient une vidéo que Genius a posée sur deux pages au même titre
(Castro → Yo Gotti, Hiver → Aladin 135), et, avec une durée contraire, qu'une
vidéo n'est pas le morceau de sa fiche (décision utilisateur 2026-10-29).
NULL = inconnue. Définition identique à `src/persistence/schema.py`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e63_videos_chaine"
down_revision: str | Sequence[str] | None = "e62_videos_duree"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("track_videos") as batch_op:
        batch_op.add_column(sa.Column("channel", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("channel_id", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("track_videos") as batch_op:
        batch_op.drop_column("channel_id")
        batch_op.drop_column("channel")
