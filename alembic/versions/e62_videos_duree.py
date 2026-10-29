"""track_videos.duration : la durée de CHAQUE vidéo

Revision ID: e62_videos_duree
Revises: e61_lectures_compteurs
Create Date: 2026-10-29

La passe YouTube Music lit la durée de chaque vidéo (`contentDetails`, même
appel que les vues) mais ne la gardait qu'en observation de la FICHE
(`duree_a_declarer`) : la durée propre à la vidéo était perdue. Or 28 pages
Genius au même titre partagent une vidéo (« KING », « 530 »…), et seule cette
durée, comparée à celle de chaque fiche, dirait à laquelle elle revient
(décision utilisateur 2026-10-29). Secondes entières, NULL = inconnue.
Définition identique à `src/persistence/schema.py`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e62_videos_duree"
down_revision: str | Sequence[str] | None = "e61_lectures_compteurs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("track_videos") as batch_op:
        batch_op.add_column(sa.Column("duration", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("track_videos") as batch_op:
        batch_op.drop_column("duration")
