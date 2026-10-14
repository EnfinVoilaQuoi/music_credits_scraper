"""artist_spotify_ids : les profils Spotify CONNUS d'un artiste, par source

Revision ID: e53_artist_spotify_ids
Revises: e52_position_source
Create Date: 2026-10-14

Le garde d'identité Spotify reconnaît un homonyme à son URI quand l'ID de
l'artiste est connu ; `artists.spotify_id` n'en tient qu'UN, alors que Spotify
dédouble des artistes (deux profils légitimes). La fiche MusicBrainz les lie
(`url-rels`, lus par la requête déjà faite) : une ligne par (artiste, profil,
source), remplacée à chaque lecture de la source. Définition identique à
`src/persistence/schema.py`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e53_artist_spotify_ids"
down_revision: str | Sequence[str] | None = "e52_position_source"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "artist_spotify_ids",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("artist_id", sa.Integer(), sa.ForeignKey("artists.id"), nullable=False),
        sa.Column("spotify_id", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("seen_at", sa.TIMESTAMP()),
        sa.UniqueConstraint("artist_id", "spotify_id", "source"),
        sqlite_autoincrement=True,
    )


def downgrade() -> None:
    op.drop_table("artist_spotify_ids")
