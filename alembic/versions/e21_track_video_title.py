"""track_videos.title : nommer la vidéo, pour qu'un partage soit VÉRIFIABLE

Revision ID: e21_track_video_title
Revises: e20_track_videos
Create Date: 2026-09-07

Une même vidéo rattachée à plusieurs morceaux d'un artiste a DEUX causes, et
rien en base ne permettait de les distinguer :

  · un lien Genius fautif — « Innocent » et « Interlude » de Jazzy Bazz pointent
    sur la même vidéo, qui n'appartient qu'à l'un des deux ;
  · un CLIP DOUBLE, parfaitement légitime — `iIHdTMHWAic` s'intitule
    « B.B. Jacques - Donjon & 2h22 » et couvre réellement les deux morceaux, qui
    existent par ailleurs séparément en audio sur le canal « - Topic ».

Ce qui tranche, c'est le TITRE de la vidéo : il nomme les morceaux couverts.
Sans lui, le signal « cette vidéo est partagée » obligerait à ouvrir chaque lien
à la main pour savoir s'il y a quelque chose à corriger.

Le titre ne coûte AUCUN quota supplémentaire : `fetch_video_meta_batch`
(`part="statistics,snippet"`) le ramène déjà, au même prix que
`fetch_view_counts_batch` (`part="statistics"`) — une unité par lot de 50. Il
rend au passage `kind` auditable, puisque c'est de lui que `kind` est dérivé.

Pas de backfill : le titre n'existe nulle part en base. Il se remplira à la
prochaine passe « vues des vidéos », morceau par morceau.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e21_track_video_title"
down_revision: Union[str, Sequence[str], None] = "e20_track_videos"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Ajoute la colonne, en FIN de table (ordre d'`ALTER TABLE ADD COLUMN`)."""
    with op.batch_alter_table("track_videos") as batch_op:
        batch_op.add_column(sa.Column("title", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("track_videos") as batch_op:
        batch_op.drop_column("title")
