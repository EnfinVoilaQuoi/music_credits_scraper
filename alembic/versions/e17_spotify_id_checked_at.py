"""tracks : colonne spotify_id_checked_at (a-t-on CHERCHÉ l'ID Spotify ?)

Revision ID: e17_spotify_id_checked
Revises: e16_backfill_kworb_streams
Create Date: 2026-09-05

`spotify_id` vide dit deux choses très différentes, et rien ne les distinguait :

  · **cherché et absent** — le morceau n'est pas sur Spotify. Lui réclamer des
    streams Spotify le marquerait incomplet à perpétuité.
  · **jamais cherché** — on n'en sait rien. Le valider serait affirmer une
    absence qu'on n'a pas constatée.

Cette colonne porte la date de la dernière RÉSOLUTION MENÉE À TERME, quel qu'en
soit le résultat. Elle transforme un silence en constat.

Pas de backfill : c'est tout l'enjeu. Rien ne permet de savoir, après coup, si
la recherche a eu lieu — dater à l'aveugle fabriquerait précisément la certitude
qu'on cherche à éviter. Les morceaux existants restent donc « jamais cherché »
jusqu'à leur prochain enrichissement.

À noter : l'ISRC ne peut PAS tenir ce rôle. Un enregistrement distribué en a
forcément un, mais le nôtre vient de Deezer — son absence signifie « Deezer ne
nous en a pas donné ». Mesuré le 2026-09-05 : **292 morceaux ont un ID Spotify
sans ISRC en base**. L'ISRC sert dans l'autre sens (présent sans ID = résolution
ratée, 67 morceaux), jamais comme preuve d'absence.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e17_spotify_id_checked"
down_revision: Union[str, Sequence[str], None] = "e16_backfill_kworb_streams"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Ajoute la colonne. Aucun backfill (cf. docstring)."""
    with op.batch_alter_table("tracks") as batch_op:
        batch_op.add_column(sa.Column("spotify_id_checked_at", sa.TIMESTAMP(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("tracks") as batch_op:
        batch_op.drop_column("spotify_id_checked_at")
