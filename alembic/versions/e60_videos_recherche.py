"""track_videos.recherche : ce que la recherche a vu en posant la vidéo

Revision ID: e60_videos_recherche
Revises: e59_refus_origine
Create Date: 2026-10-20

Une vidéo posée par la recherche (`source = 'search_auto'`) l'a été sur un
score qui a changé depuis (artiste par mots entiers, autre partie, titre
générique jugé sur son disque…), et rien ne la rejugeait : Django « Chaos »
gardait le « Chaos » de Jango Jack. `recherche` (JSON : `titre`, `artistes`,
`album` du résultat ytmusicapi) rend la règle REJOUABLE dans le verdict des
vues comptées (`services.vues_comptees`, décision utilisateur 2026-10-20,
option A). NULL = inconnu : on ne conclut pas. Définition identique à
`src/persistence/schema.py`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e60_videos_recherche"
down_revision: str | Sequence[str] | None = "e59_refus_origine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("track_videos") as batch_op:
        batch_op.add_column(sa.Column("recherche", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("track_videos") as batch_op:
        batch_op.drop_column("recherche")
