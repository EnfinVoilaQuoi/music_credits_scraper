"""releases : le code-barres (UPC/EAN) de la parution

Revision ID: e54_release_upc
Revises: e53_artist_spotify_ids
Create Date: 2026-10-14

Un ID d'album est propre à UNE source ; le code-barres désigne l'ÉDITION
partout : Deezer le porte sur chaque album (mesuré le 2026-10-14 : 1 653 sur
1 653, deux seuls doublons — « A V L D » / « AVLD », la même édition sous deux
ID Deezer), Discogs et MusicBrainz aussi (`barcode`). C'est la clé qui relie
une parution Deezer à son édition Discogs ou MusicBrainz, et donc leurs
numéros de piste aux nôtres. NULL tant qu'aucune source ne l'a dit.
Définition identique à `src/persistence/schema.py`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e54_release_upc"
down_revision: str | Sequence[str] | None = "e53_artist_spotify_ids"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("releases") as batch_op:
        batch_op.add_column(sa.Column("upc", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("releases") as batch_op:
        batch_op.drop_column("upc")
