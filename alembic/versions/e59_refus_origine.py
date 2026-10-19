"""refus.origine : un refus posé par une règle n'est pas une décision humaine

Revision ID: e59_refus_origine
Revises: e58_acceptations
Create Date: 2026-10-19

Les règles automatiques (vidéo d'une autre version au YTM, LRC ou certif d'une
autre fiche au panneau « À trancher ») écrivent dans `refus` comme une décision
à la main : le gate disait « retiré à la main » de ce qu'aucun humain n'avait
refusé, et une fusion gardait un refus dont le motif venait de disparaître
(Médine « 17 Octobre », 2026-10-13). `origine` : NULL = à la main (et tout
refus d'avant e59), « regle:<code> » = une règle. Aucun backfill : rien ne
distingue en base les refus automatiques d'avant. Définition identique à
`src/persistence/schema.py`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e59_refus_origine"
down_revision: str | Sequence[str] | None = "e58_acceptations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("refus") as batch_op:
        batch_op.add_column(sa.Column("origine", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("refus") as batch_op:
        batch_op.drop_column("origine")
