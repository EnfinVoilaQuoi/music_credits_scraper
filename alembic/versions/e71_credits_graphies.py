"""credits.graphies : le nom que chaque AUTRE source donne à la personne

Revision ID: e71_credits_graphies
Revises: e70_credits_confirme_par
Create Date: 2026-11-25

Le même crédit sous deux graphies (« Al Tarba » de Discogs, « Al’Tarba » de
Genius) tenait deux lignes. Une seule reste, à la graphie Genius ; celle des
autres sources est gardée ici, en JSON `{source: nom}` (NULL = aucune, toutes
donnent le nom de la ligne) : c'est la PREUVE d'une même personne que lit
`credit_normalize.cles_fondues` (studio), et le nom à rendre si la source de la
ligne se retire (`src/utils/credit_sources.py`).
Rattrapage : `scripts/fondre_graphies_credits.py`.
Définition identique à `src/persistence/schema.py`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e71_credits_graphies"
down_revision: str | Sequence[str] | None = "e70_credits_confirme_par"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("credits", sa.Column("graphies", sa.Text(), nullable=True))


def downgrade() -> None:
    # Pas de `batch_alter_table` : voir e70.
    op.execute("ALTER TABLE credits DROP COLUMN graphies")
