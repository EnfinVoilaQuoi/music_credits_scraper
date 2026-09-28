"""tracks : colonne deezer_checked_at (Deezer a-t-il RÉPONDU pour ce morceau ?)

Revision ID: e41_deezer_checked_at
Revises: e40_provenance_videos
Create Date: 2026-09-28

Calque de `spotify_id_checked_at` (e17) : sans date de constat, une fiche hors
plateformes était redemandée à Deezer à chaque passage de l'étape Identité. La
date n'est posée que sur une RÉPONSE (trouvé ou non) — jamais sur une panne
(`SansReponse`). Aucun backfill : rien ne dit, pour les fiches existantes, si
Deezer a répondu ou échoué.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e41_deezer_checked_at"
down_revision: str | Sequence[str] | None = "e40_provenance_videos"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tracks") as batch_op:
        batch_op.add_column(sa.Column("deezer_checked_at", sa.TIMESTAMP(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tracks") as batch_op:
        batch_op.drop_column("deezer_checked_at")
