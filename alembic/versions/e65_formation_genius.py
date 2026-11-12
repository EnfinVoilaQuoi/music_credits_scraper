"""artist_relations.genius_id + formation_origine : l'ID Genius d'une formation,
et qui a choisi sa nature

Revision ID: e65_formation_genius
Revises: e64_parution_live
Create Date: 2026-11-12

59 formations confirmées sur 61 ne sont pas en base comme artistes : leurs
morceaux ne viennent que si Genius les range aussi sous le membre (IAM : 90
pages sur 335 absentes de Shurik'n). Pour lancer leur discographie, il faut leur
ID Genius, que la base ne gardait pas (`tracks` n'a que `primary_artist_name`).
Il se prouve par les morceaux du membre (`utils.formations_genius`), jamais par
le seul nom (homonymes : « Soleil Noir »).

`formation_origine` : NULL = nature choisie à la main (et toute nature d'avant
e65 — aucune n'avait été posée par règle) ; « regle:<code> » = posée par une
règle, qu'une règle peut recalculer. Une nature humaine ne bouge jamais.
Définition identique à `src/persistence/schema.py`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e65_formation_genius"
down_revision: str | Sequence[str] | None = "e64_parution_live"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("artist_relations") as batch_op:
        batch_op.add_column(sa.Column("genius_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("formation_origine", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("artist_relations") as batch_op:
        batch_op.drop_column("formation_origine")
        batch_op.drop_column("genius_id")
