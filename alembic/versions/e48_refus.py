"""refus : les refus décidés à la main, en base (chantier C, lot 2)

Revision ID: e48_refus
Revises: e47_credits_cle_null_sure
Create Date: 2026-10-12

Les refus (ID Spotify ou Deezer retiré, vidéo rejetée, LRC refusé, crédit ou
certification retirés, lien YouTube rejeté) vivaient dans
`data/corrections/fiches.json`, rangés PAR ARTISTE : hors de la base (ni
sauvegardés avec elle, ni lisibles par l'audit), et un refus sur la ligne d'un
artiste ne protégeait pas les lignes sœurs. Une ligne par refus : la fiche
désignée (page Genius, sinon titre + album sous un artiste), la nature, une
CLÉ normalisée (unicité) et l'entrée d'origine. Aucun backfill ici : le
fichier est versé par `corrections_fiches.migrer_refus_vers_la_base`, qui
retire du fichier ce qu'il a versé (décision utilisateur 2026-10-12).
Définition identique, colonne par colonne, à `src/persistence/schema.py`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e48_refus"
down_revision: str | Sequence[str] | None = "e47_credits_cle_null_sure"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "refus",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cle_fiche", sa.Text(), nullable=False),
        sa.Column("genius_id", sa.Integer()),
        sa.Column("artiste", sa.Text(), nullable=False),
        sa.Column("titre", sa.Text()),
        sa.Column("album", sa.Text()),
        sa.Column("nature", sa.Text(), nullable=False),
        sa.Column("valeur", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text()),
        sa.Column("decide_le", sa.TIMESTAMP()),
        sa.UniqueConstraint("cle_fiche", "artiste", "nature", "valeur"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_refus_genius_nature", "refus", ["genius_id", "nature"])


def downgrade() -> None:
    op.drop_index("ix_refus_genius_nature", table_name="refus")
    op.drop_table("refus")
