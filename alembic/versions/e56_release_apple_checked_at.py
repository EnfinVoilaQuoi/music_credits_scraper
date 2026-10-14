"""releases : date de la recherche de l'album Apple par le code-barres

Revision ID: e56_release_apple_checked_at
Revises: e55_position_brute
Create Date: 2026-10-15

L'étape Identité rattache l'album Apple Music d'une parution par son
code-barres (`lookup?upc=`, ~3 s par album) — mesuré le 2026-10-14 : album
sûr pour 72 % des UPC, pistes concordantes à 99,3 %. Une recherche RÉPONDUE
(trouvé, inconnu de la boutique, ou ambigu) est datée ici et n'est refaite
qu'à expiration (`runtime.constat_perime` : 30 j sortie récente, 180 j sinon).
NULL tant que jamais demandé. Définition identique à `src/persistence/schema.py`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e56_release_apple_checked_at"
down_revision: str | Sequence[str] | None = "e55_position_brute"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("releases") as batch_op:
        batch_op.add_column(sa.Column("apple_checked_at", sa.TIMESTAMP(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("releases") as batch_op:
        batch_op.drop_column("apple_checked_at")
