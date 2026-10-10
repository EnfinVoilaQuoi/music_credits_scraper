"""tracks : colonne genius_detail_checked_at (le détail Genius a-t-il RÉPONDU ?)

Revision ID: e45_genius_detail_checked_at
Revises: e44_source_track_failures
Create Date: 2026-10-10

Calque de `deezer_checked_at` (e41) : la garde du prefill Genius
(`Track.detail_genius_a_lire`) restait ouverte tant qu'il manquait l'album, un
ID Spotify, un lien YouTube ou une relation — c'est-à-dire pour toujours sur la
plupart des morceaux (mesuré : 10 010 fiches sur 11 368 relues à chaque run
discographie, et les feats une seconde fois à l'enrichissement). La date n'est
posée que sur une RÉPONSE, jamais sur une panne. Aucun backfill : rien ne dit,
pour les fiches existantes, quand leur détail a été lu.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e45_genius_detail_checked_at"
down_revision: str | Sequence[str] | None = "e44_source_track_failures"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tracks") as batch_op:
        batch_op.add_column(sa.Column("genius_detail_checked_at", sa.TIMESTAMP(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tracks") as batch_op:
        batch_op.drop_column("genius_detail_checked_at")
