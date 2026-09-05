"""albums : colonne spotify_editions_json (le total de CHAQUE édition)

Revision ID: e15_album_editions
Revises: e14_album_streams_source
Create Date: 2026-09-05

Un album réédité existe en plusieurs éditions Spotify, et le total du disque ne
peut pas être leur somme. Mesuré le 2026-09-05 sur « Bitume Caviar (vol.1) » :

  · une édition de 11 pistes (l'originale) et une de 15 (la réédition, qui
    ajoute un « Disque 2 » de 4 inédits) ;
  · **aucun `track_id` en commun** entre les deux ;
  · mais « Clio 4 », présent sur les deux, y affiche le MÊME compteur
    (4 293 392).

Spotify compte donc par **enregistrement**, pas par identifiant de piste : la
réédition ne remet pas les compteurs à zéro. Additionner les éditions
compterait deux fois les 11 titres partagés — c'est ce que fait aujourd'hui
l'agrégation Kworb (52 826 894 + 46 379 590 = 99 206 484, là où le total réel
des 15 enregistrements distincts avoisine 52 826 894).

`spotify_streams` porte donc la somme des enregistrements DISTINCTS. Cette
colonne garde à côté le détail par édition (`{album_id: total}`), qui reste une
donnée utile en soi — et sans laquelle on ne pourrait plus reconstituer ce que
chaque pressage a fait.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e15_album_editions"
down_revision: Union[str, Sequence[str], None] = "e14_album_streams_source"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Ajoute la colonne. Aucun backfill possible : le détail par édition n'a
    jamais été conservé (Kworb ne gardait que la somme et la liste d'IDs)."""
    with op.batch_alter_table("albums") as batch_op:
        batch_op.add_column(sa.Column("spotify_editions_json", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("albums") as batch_op:
        batch_op.drop_column("spotify_editions_json")
