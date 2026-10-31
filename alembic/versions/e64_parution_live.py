"""releases.mb_live : la parution est un album LIVE selon MusicBrainz

Revision ID: e64_parution_live
Revises: e63_videos_chaine
Create Date: 2026-10-31

MC Solaar « Les temps changent » (studio, *Paradisiaque*, 197 s) portait les
ID Deezer, Apple et Spotify du live de *Le tour de la question* (219 s) : la
fiche studio avait été rattachée à la parution Deezer du live, dont le titre
ne dit rien ; Spotify et Apple ont suivi par la place. Seul MusicBrainz le
dit (type secondaire « Live » du release-group), à la même recherche par
code-barres que l'album Spotify (e57). 1 = live, 0 = MusicBrainz a répondu
autre chose, NULL = inconnu. Définition identique à `src/persistence/schema.py`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e64_parution_live"
down_revision: str | Sequence[str] | None = "e63_videos_chaine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("releases") as batch_op:
        batch_op.add_column(
            sa.Column("mb_live", sa.Boolean(create_constraint=False), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("releases") as batch_op:
        batch_op.drop_column("mb_live")
