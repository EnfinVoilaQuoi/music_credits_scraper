"""acceptations : les valeurs acceptées à la main contre le gate

Revision ID: e58_acceptations
Revises: e57_release_mb_checked_at
Create Date: 2026-10-19

Le pendant de `refus` (e48). Le gate Spotify refuse à chaque run des ID justes
qu'il ne peut pas prouver : Spotify ne crédite pas l'artiste (Diam's
« Température - Live 2006 », crédité à 113 et Magic System), ou les range sous
le profil d'un homonyme (SDM). `refus` mémorise un NON ; il manquait le OUI,
lu par le gate avant les règles d'artiste et de titre — la durée continue de
garder. Remplace les `--exclure` de `scripts/repair_spotify_ids.py`.
Définition identique, colonne par colonne, à `src/persistence/schema.py`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e58_acceptations"
down_revision: str | Sequence[str] | None = "e57_release_mb_checked_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "acceptations",
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
    op.create_index("ix_acceptations_genius_nature", "acceptations", ["genius_id", "nature"])


def downgrade() -> None:
    op.drop_index("ix_acceptations_genius_nature", table_name="acceptations")
    op.drop_table("acceptations")
