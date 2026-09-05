"""albums : colonne spotify_streams_source (qui a calculé le total)

Revision ID: e14_album_streams_source
Revises: e13_source_usage
Create Date: 2026-09-05

`albums.spotify_streams` va porter DEUX sémantiques selon l'écrivain, et ce
n'est pas un défaut de conception mais un fait sur les données :

  · **Kworb** ne somme que les morceaux DE L'ARTISTE présents sur le disque.
    Sur un album commun (Limsa d'Aulnay × ISHA) ou de groupe (L'Or du Commun
    pour Swing), ce total est INCOMPLET — or ces albums sont bien les albums de
    l'artiste, tous leurs morceaux le concernent.
  · **Spotify** permet le total réel, mais au prix d'une page par piste (aucune
    page ne rend les compteurs d'un album d'un coup, vérifié le 2026-09-04), y
    compris les pistes où l'artiste n'apparaît pas.

Mélanger les deux dans une colonne muette produirait exactement ce que le
JOURNAL appelle « un chiffre faux annoncé avec assurance ». La table
`observations` ne peut pas servir de recours : elle est clavetée sur `track_id`,
pas sur `album_id`. D'où cette colonne de provenance.

Backfill : tout album déjà pourvu d'un total est marqué 'kworb'. C'est le seul
choix exact — Kworb était la seule source d'albums jusqu'ici.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e14_album_streams_source"
down_revision: Union[str, Sequence[str], None] = "e13_source_usage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Ajoute la colonne + backfill 'kworb' pour les totaux existants."""
    with op.batch_alter_table("albums") as batch_op:
        batch_op.add_column(sa.Column("spotify_streams_source", sa.Text(), nullable=True))
    op.get_bind().execute(
        sa.text(
            "UPDATE albums SET spotify_streams_source='kworb' "
            "WHERE spotify_streams IS NOT NULL"
        )
    )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("albums") as batch_op:
        batch_op.drop_column("spotify_streams_source")
