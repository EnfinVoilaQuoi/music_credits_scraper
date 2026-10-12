"""corrections : les corrections de fiches décidées à la main, en base (chantier C, lot 3)

Revision ID: e49_corrections
Revises: e48_refus
Create Date: 2026-10-12

Après les refus (e48), le reste de `data/corrections/fiches.json` : fusions,
suppressions, départages d'ID, liens YouTube posés, inédits, relations
(version, cover, remix, sample) et versions d'un tiers. Une ligne par ACTION
d'une fiche désignée : la désignation telle quelle (`fiche`, JSON — une
désignation par titre SANS album vaut pour tout album, il faut la garder
exacte), sa clé (page Genius, sinon titre + album), l'action et sa valeur
(JSON). Aucun backfill ici : le fichier est versé par
`corrections_fiches.migrer_corrections_vers_la_base`, qui retire du fichier ce
qu'il a versé (décision utilisateur 2026-10-12, comme pour les refus).
Définition identique, colonne par colonne, à `src/persistence/schema.py`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e49_corrections"
down_revision: str | Sequence[str] | None = "e48_refus"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "corrections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cle_fiche", sa.Text(), nullable=False),
        sa.Column("genius_id", sa.Integer()),
        sa.Column("artiste", sa.Text(), nullable=False),
        sa.Column("fiche", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("valeur", sa.Text(), nullable=False),
        sa.Column("decide_le", sa.TIMESTAMP()),
        sa.UniqueConstraint("cle_fiche", "artiste", "action"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_corrections_artiste_action", "corrections", ["artiste", "action"])


def downgrade() -> None:
    op.drop_index("ix_corrections_artiste_action", table_name="corrections")
    op.drop_table("corrections")
