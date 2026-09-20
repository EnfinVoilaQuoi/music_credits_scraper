"""tracks : instrumental (« pas de paroles par nature » ≠ scrape raté)

Revision ID: e27_instrumental
Revises: e26_statut_album
Create Date: 2026-09-20

Sur Genius, un morceau instrumental n'a aucun conteneur de paroles : la page
affiche « This song is an instrumental ». Le scraper rendait alors une chaîne
vide, ce que `scrape_lyrics_batch` comptait en ÉCHEC, et rien n'était écrit —
le morceau restait « paroles manquantes », donc re-scrapé à chaque run (avec
12 s d'attente du sélecteur de paroles) et ⚠️ à perpétuité dans la GUI.

Un champ vide disait deux choses (« pas de paroles » et « pas encore cherché »),
c'est le même piège que `spotify_id` avant e17 et `explicit_lyrics` (e19).

  · `instrumental` — BOOLÉEN nullable À DESSEIN : `NULL` = jamais constaté,
    `1` = la page Genius l'affiche (placeholder ou `__PRELOADED_STATE__`),
    `0` = des paroles ont été trouvées. L'API publique Genius n'expose PAS ce
    champ (vérifié le 2026-09-20 sur GET /songs/8875569) : seule la page le
    porte.

Pas de backfill : rien en base ne distingue un instrumental d'un échec passé.
Le constat se pose au prochain scrape de paroles de chaque morceau.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e27_instrumental"
down_revision: Union[str, Sequence[str], None] = "e26_statut_album"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Ajoute la colonne. Aucun backfill (cf. docstring)."""
    with op.batch_alter_table("tracks") as batch_op:
        batch_op.add_column(
            sa.Column("instrumental", sa.Boolean(create_constraint=False), nullable=True)
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("tracks") as batch_op:
        batch_op.drop_column("instrumental")
