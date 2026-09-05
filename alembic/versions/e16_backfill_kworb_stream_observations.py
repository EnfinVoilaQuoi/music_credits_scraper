"""observations : backfill des streams Kworb depuis les colonnes

Revision ID: e16_backfill_kworb_streams
Revises: e15_album_editions
Create Date: 2026-09-05

L'arbitrage des streams (`reconcile_spotify_streams`) raisonne sur les
OBSERVATIONS. Or l'écriture de provenance n'existe que depuis E7e : mesuré le
2026-09-05, **276 morceaux sur 407** portaient une valeur en colonne sans aucune
observation `kworb`.

Conséquence si on ne fait rien : au premier passage du scrape Spotify, l'arbitre
ne voit qu'une seule source pour ces morceaux et Spotify remporte la colonne —
alors que le réglage dit « Kworb maître ». La règle serait respectée dans le code
et démentie dans les données.

Le backfill est EXACT, pas une approximation : jusqu'à ce jour Kworb était la
seule source de `tracks.spotify_streams`, et `spotify_streams_updated` porte sa
date « Last updated ». L'observation reprend donc la colonne verbatim, avec sa
vraie fraîcheur.

Garde d'idempotence : on n'écrit QUE pour les morceaux sans aucune observation de
ce champ. Un morceau déjà couvert (par Kworb depuis E7e, ou par un run Spotify
antérieur à cette migration) n'est pas touché — sans quoi on fabriquerait une
observation « kworb » portant une valeur qui ne vient pas de lui.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e16_backfill_kworb_streams"
down_revision: Union[str, Sequence[str], None] = "e15_album_editions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Crée une observation `kworb` par colonne orpheline."""
    op.get_bind().execute(
        sa.text(
            "INSERT INTO observations (track_id, field, value, source, confidence, seen_at) "
            "SELECT t.id, 'spotify_streams', CAST(t.spotify_streams AS TEXT), 'kworb', "
            "       NULL, t.spotify_streams_updated "
            "FROM tracks t "
            "WHERE t.spotify_streams IS NOT NULL "
            "  AND NOT EXISTS (SELECT 1 FROM observations o "
            "                  WHERE o.track_id = t.id AND o.field = 'spotify_streams')"
        )
    )


def downgrade() -> None:
    """Pas de retrait ciblé possible : une observation backfillée est
    indiscernable d'une observation écrite par un run Kworb réel."""
