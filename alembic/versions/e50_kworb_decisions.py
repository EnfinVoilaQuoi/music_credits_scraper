"""kworb_decisions : les décisions de rapprochement Kworb, en base (chantier C, lot 3)

Revision ID: e50_kworb_decisions
Revises: e49_corrections
Create Date: 2026-10-12

Les décisions « même morceau ? » sur une ligne Kworb (confirmée vers une fiche,
rejetée) et la taxonomie des variantes (édition, rendition, existant, collab,
tiers, ignore) vivaient dans `data/kworb_links/<artiste>.json` — ignorés par
git, donc ni versionnés ni sauvegardés avec la base. Une ligne par (artiste,
titre Kworb normalisé, nature) : nature `lien` (statut `confirme` + fiche, ou
`rejete` : l'un remplace l'autre) ou `decision` (statut = la taxonomie, qui
l'emporte sur le lien). Versé par `KworbLinksManager.migrer_vers_la_base`.
Définition identique, colonne par colonne, à `src/persistence/schema.py`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e50_kworb_decisions"
down_revision: str | Sequence[str] | None = "e49_corrections"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "kworb_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("artiste", sa.Text(), nullable=False),
        sa.Column("titre", sa.Text(), nullable=False),
        sa.Column("nature", sa.Text(), nullable=False),
        sa.Column("statut", sa.Text(), nullable=False),
        sa.Column("track_id", sa.Integer()),
        sa.Column("decide_le", sa.TIMESTAMP()),
        sa.UniqueConstraint("artiste", "titre", "nature"),
        sqlite_autoincrement=True,
    )


def downgrade() -> None:
    op.drop_table("kworb_decisions")
