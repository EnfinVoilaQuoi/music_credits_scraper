"""credits.confirme_par : les AUTRES sources qui donnent le même crédit

Revision ID: e70_credits_confirme_par
Revises: e69_retrait_albums
Create Date: 2026-11-23

La table `credits` n'a qu'une ligne par clé `(track_id, name, role,
role_detail)` : quand deux sources donnaient le même crédit, une seule restait,
avec UNE source — la confirmation par la seconde était perdue (le panneau
« crédits 🎫 » n'en voyait qu'une sur 189). Sources triées, séparées par des
virgules, sans celle de la ligne ; NULL = aucune autre (`src/utils/credit_sources.py`).
Une colonne plutôt qu'une table : la confirmation vit et meurt avec sa ligne.
Rattrapage : `scripts/rattraper_confirmations_credits.py`.
Définition identique à `src/persistence/schema.py`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e70_credits_confirme_par"
down_revision: str | Sequence[str] | None = "e69_retrait_albums"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("credits", sa.Column("confirme_par", sa.Text(), nullable=True))


def downgrade() -> None:
    # Pas de `batch_alter_table` : il recréerait la table et son index
    # d'expression `ux_credits_cle` ; SQLite sait retirer une colonne nue.
    op.execute("ALTER TABLE credits DROP COLUMN confirme_par")
