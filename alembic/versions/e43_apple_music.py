"""tracks : colonnes apple_music_id / apple_music_checked_at (lot B6)

Revision ID: e43_apple_music
Revises: e42_discogs_id_source
Create Date: 2026-09-29

La fiche Genius (`GET /songs/{id}`, déjà lue) porte un `apple_music_id` :
mesuré sur 40 fiches tirées au hasard, 26 en ont un, 17 répondent à
iTunes lookup (libre, sans clé, 200 ID par requête) avec une durée à ±1 s
de la fiche — MAIS 2 désignent un autre artiste (« Heartless » de Kid Cudi
→ la reprise de Kris Allen). Genius le DÉCLARE donc (observation
`apple_music_id_propose`), l'étape Identité le VÉRIFIE (artiste + titre) et
seul un ID vérifié rejoint la colonne ; la date de vérification est posée sur
toute RÉPONSE d'iTunes (retenu ou démenti), jamais sur une panne. Aucun backfill.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e43_apple_music"
down_revision: str | Sequence[str] | None = "e42_discogs_id_source"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tracks") as batch_op:
        batch_op.add_column(sa.Column("apple_music_id", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("apple_music_checked_at", sa.TIMESTAMP(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tracks") as batch_op:
        batch_op.drop_column("apple_music_checked_at")
        batch_op.drop_column("apple_music_id")
