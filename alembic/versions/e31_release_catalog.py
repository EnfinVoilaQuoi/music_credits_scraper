"""Catalogue des parutions et liens morceau-parution.

Revision ID: e31_release_catalog
Revises: e30_artist_deezer_id
Create Date: 2026-09-22

`tracks.album` était un pointeur unique. Il reste l'album de référence pour
les anciens flux, mais ne peut pas dire qu'un même enregistrement est aussi
sur une deluxe ou une compilation. Cette révision crée les deux tables de
catalogue et reprend chaque valeur historique sans toucher aux fiches tracks.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e31_release_catalog"
down_revision: str | Sequence[str] | None = "e30_artist_deezer_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite peut conserver les CREATE TABLE d'une migration interrompue tout
    # en laissant `alembic_version` sur la révision précédente. La reprise doit
    # donc être sûre au redémarrage, pas seulement sur une base vierge.
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "releases" not in existing:
        op.create_table(
            "releases",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("artist_id", sa.Integer(), sa.ForeignKey("artists.id"), nullable=False),
            sa.Column("title", sa.Text(), nullable=False),
            sa.Column("credited_artist_name", sa.Text()),
            sa.Column("release_date", sa.TIMESTAMP()),
            sa.Column("record_type", sa.Text()),
            sa.Column("deezer_album_id", sa.Integer()),
            sa.Column("scope", sa.Text(), nullable=False, server_default=sa.text("'own'")),
            sa.Column("identity_key", sa.Text(), nullable=False),
            sa.Column("created_at", sa.TIMESTAMP()),
            sa.Column("updated_at", sa.TIMESTAMP()),
            sa.UniqueConstraint("artist_id", "identity_key"),
            sqlite_autoincrement=True,
        )
    if "release_tracks" not in existing:
        op.create_table(
            "release_tracks",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("release_id", sa.Integer(), sa.ForeignKey("releases.id"), nullable=False),
            sa.Column("track_id", sa.Integer(), sa.ForeignKey("tracks.id"), nullable=False),
            sa.Column("disc_number", sa.Integer()),
            sa.Column("track_number", sa.Integer()),
            sa.Column("source_track_id", sa.Integer()),
            sa.Column("source", sa.Text()),
            sa.Column("matched_by", sa.Text()),
            sa.Column("created_at", sa.TIMESTAMP()),
            sa.Column("updated_at", sa.TIMESTAMP()),
            sa.UniqueConstraint("release_id", "track_id"),
            sqlite_autoincrement=True,
        )
    # Backfill conservateur : une ligne historique = une parution own et un
    # seul lien legacy. Les titres restent tels quels pour que le lien soit
    # parfaitement réversible et qu'aucune normalisation ne fusionne deux CD.
    op.execute(
        """
        INSERT OR IGNORE INTO releases (artist_id, title, scope, identity_key, created_at, updated_at)
        SELECT artist_id, MIN(trim(album)), 'own',
               'legacy:' || artist_id || ':' || lower(trim(album)),
               CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        FROM tracks
        WHERE album IS NOT NULL AND trim(album) != ''
        GROUP BY artist_id, lower(trim(album))
        """
    )
    op.execute(
        """
        INSERT OR IGNORE INTO release_tracks
            (release_id, track_id, track_number, source, matched_by, created_at, updated_at)
        SELECT r.id, t.id, t.track_number, 'legacy', 'legacy', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        FROM tracks t
        JOIN releases r
          ON r.artist_id = t.artist_id
         AND r.identity_key = 'legacy:' || t.artist_id || ':' || lower(trim(t.album))
        WHERE t.album IS NOT NULL AND trim(t.album) != ''
        """
    )


def downgrade() -> None:
    op.drop_table("release_tracks")
    op.drop_table("releases")
