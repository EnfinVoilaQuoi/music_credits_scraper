"""track_spotify_ids : la variante qui a SA PROPRE FICHE (pointeur)

Revision ID: e29_variant_track_id
Revises: e28_renditions
Create Date: 2026-09-21

Une rendition dont une fiche existe en base (« Nudes (Live at AK Studios) »
chez Genius, « Nudes - Acoustic » chez Spotify) est attribuée à cette fiche —
ses streams sont les siens, en colonne. Décision utilisateur : le morceau
SOUCHE garde quand même l'indication, comme pour les autres versions, en
précisant que celle-ci a sa propre fiche.

  · `variant_track_id` — sur une ligne `kind='rendition'` du souche, l'id du
    morceau qui EST cette version ; NULL pour une variante sans fiche.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e29_variant_track_id"
down_revision: Union[str, Sequence[str], None] = "e28_renditions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("track_spotify_ids") as batch_op:
        batch_op.add_column(sa.Column("variant_track_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("track_spotify_ids") as batch_op:
        batch_op.drop_column("variant_track_id")
