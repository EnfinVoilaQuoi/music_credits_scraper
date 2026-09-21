"""track_spotify_ids : nature de l'ID (édition / rendition) + compteur propre

Revision ID: e28_renditions
Revises: e27_instrumental
Create Date: 2026-09-21

Jusqu'ici, tout ID de `track_spotify_ids` était réputé une ÉDITION du même
enregistrement (même compteur Spotify, jamais sommée — e23). Or Kworb liste
aussi, sous le titre nu, des RENDITIONS du morceau par l'artiste : « DKR -
Bonus Track » (108 M), « Le cœur des filles - Unplugged » (15 M), « Freeze Raël
- Chopped & $crewed »… Mesuré le 2026-09-20 sur 20 artistes : 102 lignes Kworb
sans page Genius, presque toutes des versions Live / Radio Edit / Acoustic /
Bonus / Chopped, dont les streams n'étaient attribués à RIEN.

Décision utilisateur : une rendition se RATTACHE au morceau souche, avec son
compteur PROPRE, affiché après le total sans y entrer. Un remix retravaillé
par quelqu'un n'est PAS une rendition : c'est une ligne de morceau à part
(`services/kworb_decisions`).

  · `kind`          — `edition` (défaut, tout l'existant) | `rendition`.
  · `label`         — le titre Spotify de l'ID (« DKR - Bonus Track »), ce qui
                      l'identifie à l'écran.
  · `streams`, `daily_streams`, `streams_at` — le compteur de la rendition,
                      mono-source (Kworb), jamais arbitré, jamais en colonne.

Une rendition n'est pas une édition : `get_track_ids_by_spotify_id` et l'audit
d'identité Spotify ne lisent que `kind='edition'` (une rendition mapperait le
compteur de la variante sur le parent, et l'audit la retirerait comme
« variante étrangère », ce qu'elle est par construction).
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e28_renditions"
down_revision: Union[str, Sequence[str], None] = "e27_instrumental"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Ajoute les colonnes. Aucun backfill : l'existant est une édition (défaut)."""
    with op.batch_alter_table("track_spotify_ids") as batch_op:
        batch_op.add_column(
            sa.Column("kind", sa.Text(), nullable=False, server_default=sa.text("'edition'"))
        )
        batch_op.add_column(sa.Column("label", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("streams", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("daily_streams", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("streams_at", sa.TIMESTAMP(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("track_spotify_ids") as batch_op:
        for col in ("streams_at", "daily_streams", "streams", "label", "kind"):
            batch_op.drop_column(col)
