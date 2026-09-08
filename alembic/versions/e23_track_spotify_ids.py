"""track_spotify_ids : un morceau a souvent PLUSIEURS identifiants Spotify

Revision ID: e23_track_spotify_ids
Revises: e22_artist_relations
Create Date: 2026-09-08

Le pluriel d'IDs Spotify était déjà écrit ET testé côté objet
(`Track.spotify_ids`, `add_spotify_id`, `get_all_spotify_ids`), et la GUI a même
un sélecteur « Version 1 / Version 2 / (N versions) ». **Aucune colonne, aucune
table** ne le portait : la liste mourait au `save_track`, et « accepter un ID
alternatif » dégénérait en « écrire le même `spotify_id` sur l'autre ligne ».
C'est le cas d'école de la règle « une fonctionnalité qui a perdu son câblage se
RÉPARE ».

Le cas est le plus courant qui soit : un titre sorti en single **et** sur
l'album a deux `spotify_id`. Le pluriel est d'ailleurs déjà admis au niveau
ALBUM (`albums.spotify_album_ids`, e15) — il manquait au niveau morceau.

Calque exact de `track_videos` (e20) : écrivain DÉDIÉ et ADDITIF, retrait
EXPLICITE, lecture groupée par artiste. `tracks.spotify_id` reste l'ID
PRINCIPAL, comme `tracks.youtube_url` reste la vidéo principale.

Backfill : une ligne par morceau qui a déjà un ID, `source='legacy'` (même
convention que le backfill audio d'e10 : une provenance qu'on ne connaît pas ne
s'invente pas) et `is_primary=1` — c'était le seul ID connu, c'est donc bien le
principal.

Ajoute aussi l'**index sur `tracks.genius_id`** : la colonne était nue, alors
que c'est par elle que se retrouvent les lignes sœurs d'un même enregistrement,
parcours désormais fait à chaque écriture de donnée d'enregistrement (lot C).
"""

from datetime import datetime
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e23_track_spotify_ids"
down_revision: Union[str, Sequence[str], None] = "e22_artist_relations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Crée la table, y verse l'ID déjà connu de chaque morceau, indexe genius_id.

    Les colonnes reproduisent À L'IDENTIQUE, et DANS LE MÊME ORDRE, celles de
    `src/persistence/schema.py` : `test_alembic_baseline` compare les deux et
    est sensible à l'ordre.
    """
    op.create_table(
        "track_spotify_ids",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=False),
        sa.Column("spotify_id", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("is_primary", sa.BOOLEAN(), server_default=sa.text("0"), nullable=True),
        sa.Column("seen_at", sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(["track_id"], ["tracks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("track_id", "spotify_id"),
        sqlite_autoincrement=True,
    )

    op.create_index("ix_tracks_genius_id", "tracks", ["genius_id"])

    conn = op.get_bind()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        sa.text(
            "INSERT INTO track_spotify_ids "
            "(track_id, spotify_id, source, is_primary, seen_at) "
            "SELECT id, spotify_id, 'legacy', 1, :now FROM tracks "
            "WHERE spotify_id IS NOT NULL AND spotify_id != ''"
        ),
        {"now": now},
    )


def downgrade() -> None:
    """Retire la table et l'index. `tracks.spotify_id` n'a pas bougé : rien de ce
    que portait la base avant cette révision n'est perdu."""
    op.drop_index("ix_tracks_genius_id", table_name="tracks")
    op.drop_table("track_spotify_ids")
