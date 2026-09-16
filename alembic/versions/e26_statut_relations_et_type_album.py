"""artist_relations : statut + type d'alias ; albums : nature du disque (record_type)

Revision ID: e26_statut_album
Revises: e25_lignes_soeurs
Create Date: 2026-09-16

Deux besoins nés du générateur « Timeline » (2026-09-15/16).

1. `albums.record_type` — la nature du disque telle que le DISTRIBUTEUR la
   déclare (`album` | `ep` | `single` | `compile`, vocabulaire de Deezer).
   Mesuré sur Isha : ni le nombre de titres ni la durée ne la déduisent (« La
   Vie Augmente » 1/2/3 = EP à 10 titres et 26 min, « Faites pas chier » = EP
   à 8 titres, « Bitume Caviar » = album à 12 titres et 32 min) ; Deezer, lui,
   concorde avec l'étiquette de l'artiste sur tous. La donnée est SOURCÉE
   (`record_type_source` : `deezer` | `manual`, `record_type_updated`), comme
   les streams : une saisie manuelle n'est jamais écrasée par un run.
   `deezer_album_id` retient la fiche interrogée (pour ne pas la redemander).

2. `artist_relations.status` — la table ne connaissait que des liens CONFIRMÉS
   (e22, fenêtre « Groupes »). L'enrichissement propose désormais des alias en
   fin de run (MusicBrainz + Discogs, après l'oracle d'identité) : il faut un
   état `proposed`, un état `refused` (une MÉMOIRE — sans elle, chaque run
   reproposerait le même refus) et un état `info` pour les alias d'un type non
   proposable (état civil, indice de recherche, variante de graphie), gardés
   pour consultation, jamais utilisés. `detail` porte ce type. Backfill
   implicite : tout l'existant a été confirmé à la main → `confirmed`.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e26_statut_album"
down_revision: Union[str, Sequence[str], None] = "e25_lignes_soeurs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("albums") as batch_op:
        batch_op.add_column(sa.Column("record_type", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("record_type_source", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("record_type_updated", sa.TIMESTAMP(), nullable=True))
        batch_op.add_column(sa.Column("deezer_album_id", sa.Integer(), nullable=True))
    with op.batch_alter_table("artist_relations") as batch_op:
        batch_op.add_column(
            sa.Column(
                "status", sa.Text(), nullable=False, server_default=sa.text("'confirmed'")
            )
        )
        batch_op.add_column(sa.Column("detail", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("artist_relations") as batch_op:
        batch_op.drop_column("detail")
        batch_op.drop_column("status")
    with op.batch_alter_table("albums") as batch_op:
        batch_op.drop_column("deezer_album_id")
        batch_op.drop_column("record_type_updated")
        batch_op.drop_column("record_type_source")
        batch_op.drop_column("record_type")
