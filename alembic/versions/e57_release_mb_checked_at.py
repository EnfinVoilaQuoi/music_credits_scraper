"""releases : date de la recherche MusicBrainz par le code-barres

Revision ID: e57_release_mb_checked_at
Revises: e56_release_apple_checked_at
Create Date: 2026-10-15

L'étape Identité demande à MusicBrainz la parution d'un code-barres pour en
tirer l'album Spotify de l'édition — retenu seulement si la parution
MusicBrainz lie EXACTEMENT notre album Deezer (mesuré le 2026-10-15 : 100
liens Deezer justes sur 105 ; les 5 autres, une autre édition du même album,
sont écartés par cette garde). Une recherche répondue est datée ici et refaite
seulement à expiration (`runtime.constat_perime`). Définition identique à
`src/persistence/schema.py`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e57_release_mb_checked_at"
down_revision: str | Sequence[str] | None = "e56_release_apple_checked_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("releases") as batch_op:
        batch_op.add_column(sa.Column("mb_checked_at", sa.TIMESTAMP(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("releases") as batch_op:
        batch_op.drop_column("mb_checked_at")
