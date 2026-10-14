"""release_tracks : le libellé de place tel que la source l'écrit

Revision ID: e55_position_brute
Revises: e54_release_upc
Create Date: 2026-10-14

Discogs écrit la place d'une piste en LIBELLÉ : « 1-03 » (disque 1, piste 3),
« CD2-5 », ou une face de vinyle « A1, B2 ». `disc_number`/`track_number` en
portent la conversion (`discogs_positions.numeroter` : une face prend son ordre
dans la liste, intertitres sautés) ; le libellé reste ici, à côté, pour qu'on
puisse toujours relire ce que la source a dit. Il suit la place retenue (un
numéro Deezer qui passe devant n'emporte pas le « A1 » de Discogs).
NULL pour les lignes existantes. Définition identique à
`src/persistence/schema.py`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e55_position_brute"
down_revision: str | Sequence[str] | None = "e54_release_upc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("release_tracks") as batch_op:
        batch_op.add_column(sa.Column("position_brute", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("release_tracks") as batch_op:
        batch_op.drop_column("position_brute")
