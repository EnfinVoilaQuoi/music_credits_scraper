"""artists : colonne discogs_id_source (d'où vient l'identité Discogs ?)

Revision ID: e42_discogs_id_source
Revises: e41_deezer_checked_at
Create Date: 2026-09-29

L'identité Discogs peut désormais venir du lien que MusicBrainz porte vers
Discogs (décision utilisateur 2026-09-29), AVANT qu'aucun disque ne puisse
voter. Garde-fou : une identité venue de MusicBrainz reste RÉVISABLE — le vote
par les disques la recontrôle tant qu'il ne l'a pas confirmée, et une
contradiction part dans « À trancher » au lieu de l'écraser. Il faut donc
savoir d'où vient l'ID : 'manuelle' | 'disques' | 'musicbrainz'. Aucun
backfill : les ID déjà en base ont été posés par le vote ou à la main, et
restent tenus pour vérifiés (NULL = comme avant).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e42_discogs_id_source"
down_revision: str | Sequence[str] | None = "e41_deezer_checked_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("artists") as batch_op:
        batch_op.add_column(sa.Column("discogs_id_source", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("artists") as batch_op:
        batch_op.drop_column("discogs_id_source")
