"""projets + releases.projet_id : les éditions d'un même disque réunies

Revision ID: e67_projets
Revises: e65_formation_genius
Create Date: 2026-11-20

Une parution est une ÉDITION (*M.A.N* et *M.A.N (Bonus)* sont deux lignes
`releases`) ; rien ne disait qu'elles font UN disque, et ce qui appartient au
disque (nature, streams d'album, crédits d'album Genius) vivait sous un TITRE
(`albums`) : 8 135 fiches pointaient un album absent de `albums` le
2026-11-20. Le projet réunit les éditions (règles `utils/projets_disques`,
simulées le 2026-11-20 : 4 557 projets, 167 à plusieurs éditions) ; cette
révision crée la table, la colonne, et pose les projets de toute la base.
La nature et les streams y passeront dans une révision suivante.

`down_revision` : e65 tant que e66 (crédits d'album Genius, autre branche)
n'est pas fusionnée — la branche fusionnée en second se rebranche sur l'autre.
Définition identique à `src/persistence/schema.py`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from src.utils.projets_en_base import artistes_a_parutions, regrouper_en_base

revision: str = "e67_projets"
down_revision: str | Sequence[str] | None = "e65_formation_genius"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "projets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("artist_id", sa.Integer(), sa.ForeignKey("artists.id"), nullable=False),
        sa.Column("titre", sa.Text(), nullable=False),
        sa.Column("edition_reference_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("updated_at", sa.TIMESTAMP(), nullable=True),
        sqlite_autoincrement=True,
    )
    # Sans clé étrangère en base, comme les autres liens ajoutés par `batch`
    # (une contrainte anonyme y est refusée ; `PRAGMA foreign_keys` est éteint
    # de toute façon) : le schéma la déclare, l'audit `orphelin` la vérifie.
    with op.batch_alter_table("releases") as batch_op:
        batch_op.add_column(sa.Column("projet_id", sa.Integer(), nullable=True))
    conn = op.get_bind()
    for artist_id in artistes_a_parutions(conn):
        regrouper_en_base(conn, artist_id)


def downgrade() -> None:
    with op.batch_alter_table("releases") as batch_op:
        batch_op.drop_column("projet_id")
    op.drop_table("projets")
