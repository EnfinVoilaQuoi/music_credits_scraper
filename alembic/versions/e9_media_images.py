"""artists/tracks : chemins d'images + vues/kind vidéo (chantier « Media »)

Revision ID: e9_media_images
Revises: e8_ytm_channel_source
Create Date: 2026-07-18

Chantier « Media » : persistance des chemins d'images téléchargées et des
métadonnées de la vidéo YouTube (vues du clip/show, catégorie).

  artists : image_path (Text)                    — photo de profil de l'artiste

  tracks  : cover_path (Text)                     — pochette album/single/sample
            yt_thumbnail_path (Text)              — vignette YouTube (shows/lives)
            youtube_video_kind (Text)             — 'clip'/'show'/'audio'/'unknown'
            youtube_video_views (Integer)         — vues de LA vidéo (≠ ytm_streams)
            youtube_video_views_updated (TIMESTAMP)

Colonnes ajoutées EN FIN de table (native ADD COLUMN append en dernière
position), reflétées à l'identique dans `src/persistence/schema.py` (garde-fou
`test_alembic_baseline` / `test_schema_reflects_db` sur l'ordre et le compte).
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e9_media_images"
down_revision: Union[str, Sequence[str], None] = "e8_ytm_channel_source"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Ajoute les colonnes de chemins d'images et de métadonnées vidéo."""
    with op.batch_alter_table("artists") as batch_op:
        batch_op.add_column(sa.Column("image_path", sa.Text(), nullable=True))

    with op.batch_alter_table("tracks") as batch_op:
        batch_op.add_column(sa.Column("cover_path", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("yt_thumbnail_path", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("youtube_video_kind", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("youtube_video_views", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("youtube_video_views_updated", sa.TIMESTAMP(), nullable=True)
        )


def downgrade() -> None:
    """Drop des colonnes ajoutées (batch)."""
    with op.batch_alter_table("tracks") as batch_op:
        batch_op.drop_column("youtube_video_views_updated")
        batch_op.drop_column("youtube_video_views")
        batch_op.drop_column("youtube_video_kind")
        batch_op.drop_column("yt_thumbnail_path")
        batch_op.drop_column("cover_path")

    with op.batch_alter_table("artists") as batch_op:
        batch_op.drop_column("image_path")
