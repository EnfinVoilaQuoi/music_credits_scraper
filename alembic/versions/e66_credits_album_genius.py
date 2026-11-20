"""genius_album_credits + genius_album_lectures : les crédits d'ALBUM de Genius

Revision ID: e66_credits_album_genius
Revises: e65_formation_genius
Create Date: 2026-11-20

La base n'avait que les crédits portés par les pages de MORCEAU : l'Artwork,
la Photography, le Graphic Design, l'Executive Producer d'un album ne sont que
sur sa page album (Josman, *M.A.N* : Artwork = Dorian Feraud et Marius
Gonzalez, absent de la base). `GET /albums/{id}` de l'API les rend dans
`performance_groups`. Clé = l'ID d'album Genius (relié aux parutions par
`release_identifiers`), pour qu'aucune fusion de parutions n'ait à les
déplacer. Définition identique à `src/persistence/schema.py`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e66_credits_album_genius"
down_revision: str | Sequence[str] | None = "e65_formation_genius"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "genius_album_credits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("genius_album_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("role_detail", sa.Text(), nullable=True),
        sa.Column("libelle", sa.Text(), nullable=False),
        sa.Column("genius_artist_id", sa.Integer(), nullable=True),
        sa.UniqueConstraint("genius_album_id", "name", "role", "role_detail"),
        sqlite_autoincrement=True,
    )
    op.create_table(
        "genius_album_lectures",
        sa.Column("genius_album_id", sa.Integer(), primary_key=True),
        sa.Column("issue", sa.Text(), nullable=False),
        sa.Column("nb_credits", sa.Integer(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("lu_le", sa.TIMESTAMP(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("genius_album_lectures")
    op.drop_table("genius_album_credits")
