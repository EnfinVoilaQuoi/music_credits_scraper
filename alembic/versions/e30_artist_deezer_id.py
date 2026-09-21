"""artists : identifiant Deezer de l'artiste (oracle d'identité)

Revision ID: e30_artist_deezer_id
Revises: e29_variant_track_id
Create Date: 2026-09-21

Le détecteur d'écarts de discographie lit le catalogue Deezer de l'artiste.
`/search/artist` rend d'abord des HOMONYMES (mesuré : « Isha » → id 259696952,
5 fans, alors que le nôtre est 1236609, 44 albums ; « A2H » → un artiste UK) :
l'identité est tranchée UNE fois par l'oracle (`services/deezer_identite`,
recouvrement d'albums) et mémorisée ici — jamais reprise au rang de recherche.

  · `deezer_id` — NULL tant que l'oracle n'a pas tranché ou qu'un humain n'a
    pas choisi ; jamais écrit sur une ambiguïté.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e30_artist_deezer_id"
down_revision: Union[str, Sequence[str], None] = "e29_variant_track_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("artists") as batch_op:
        batch_op.add_column(sa.Column("deezer_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("artists") as batch_op:
        batch_op.drop_column("deezer_id")
