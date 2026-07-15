"""artists : colonne ytm_channel_source (origine du canal YTM épinglé)

Revision ID: e8_ytm_channel_source
Revises: e5_drop_combined_bpm_obs
Create Date: 2026-07-15

Durcissement de la désambiguïsation YTMusic. Ajoute `ytm_channel_source` sur
`artists` pour distinguer un canal SAISI À LA MAIN (jamais écrasable, jamais
bloqué par le gate d'identité) d'un canal INFÉRÉ par vote (ré-effaçable si le
gate le juge suspect).

Backfill : tout `ytm_channel_id` déjà présent est marqué 'manual'. C'est le seul
choix sûr — on ne peut pas distinguer a posteriori un pin manuel d'un pin inféré
persisté par l'ancien code. 'manual' = statu quo EXACT (protège les canaux
épinglés existants, ex. Django) ; 'inferred' les rendrait ré-écrasables.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e8_ytm_channel_source"
down_revision: Union[str, Sequence[str], None] = "e5_drop_combined_bpm_obs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Ajoute la colonne + backfill 'manual' pour les pins existants."""
    with op.batch_alter_table("artists") as batch_op:
        batch_op.add_column(sa.Column("ytm_channel_source", sa.Text(), nullable=True))
    op.get_bind().execute(
        sa.text(
            "UPDATE artists SET ytm_channel_source='manual' "
            "WHERE ytm_channel_id IS NOT NULL"
        )
    )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("artists") as batch_op:
        batch_op.drop_column("ytm_channel_source")
