"""regles_appliquees : l'empreinte de la règle qui a produit un verdict, par artiste

Revision ID: e51_regles_appliquees
Revises: e50_kworb_decisions
Create Date: 2026-10-12

Le recalcul du chantier A se déclenche sur une ÉCRITURE de faits ; une règle
changée dans le code n'écrit rien, donc rien ne repartait (2026-10-12 : la
garde des parties du portier des certifs n'aurait pris effet qu'au prochain
« Appliquer les certifs » de chaque artiste, à la main). Une ligne par
(artiste, portier) : l'empreinte de la règle au moment où ses verdicts ont été
calculés. Un run qui trouve une autre empreinte rejoue le portier
(`src/services/regles.py`). Définition identique à `src/persistence/schema.py`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e51_regles_appliquees"
down_revision: str | Sequence[str] | None = "e50_kworb_decisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "regles_appliquees",
        sa.Column("artiste", sa.Text(), primary_key=True),
        sa.Column("portier", sa.Text(), primary_key=True),
        sa.Column("empreinte", sa.Text(), nullable=False),
        sa.Column("applique_le", sa.TIMESTAMP()),
    )


def downgrade() -> None:
    op.drop_table("regles_appliquees")
