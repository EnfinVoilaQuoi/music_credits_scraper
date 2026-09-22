"""Identifiants multi-sources et preuves des liens de parution.

Revision ID: e32_release_identity_pipeline
Revises: e31_release_catalog
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e32_release_identity_pipeline"
down_revision: str | Sequence[str] | None = "e31_release_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    names = set(inspector.get_table_names())
    release_columns = {column["name"] for column in inspector.get_columns("releases")}
    track_columns = {column["name"] for column in inspector.get_columns("release_tracks")}

    # Les ajouts sont indépendants afin qu'une reprise après interruption soit
    # sans risque, comme e31 sur les bases SQLite réellement utilisées.
    if "status" not in release_columns:
        op.add_column(
            "releases",
            sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'confirmed'")),
        )
    if "external_track_id" not in track_columns:
        op.add_column("release_tracks", sa.Column("external_track_id", sa.Text()))

    if "release_identifiers" not in names:
        op.create_table(
            "release_identifiers",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("release_id", sa.Integer(), sa.ForeignKey("releases.id"), nullable=False),
            sa.Column("artist_id", sa.Integer(), sa.ForeignKey("artists.id"), nullable=False),
            sa.Column("source", sa.Text(), nullable=False),
            sa.Column("external_id", sa.Text(), nullable=False),
            sa.Column("created_at", sa.TIMESTAMP()),
            sa.UniqueConstraint("artist_id", "source", "external_id"),
            sqlite_autoincrement=True,
        )
    if "release_track_sources" not in names:
        op.create_table(
            "release_track_sources",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("release_track_id", sa.Integer(), sa.ForeignKey("release_tracks.id"), nullable=False),
            sa.Column("source", sa.Text(), nullable=False),
            sa.Column("external_track_id", sa.Text()),
            sa.Column("matched_by", sa.Text(), nullable=False),
            sa.Column("created_at", sa.TIMESTAMP()),
            sa.UniqueConstraint("release_track_id", "source", "external_track_id", "matched_by"),
            sqlite_autoincrement=True,
        )

    # L'ancien champ Deezer devient une preuve parmi d'autres. INSERT OR IGNORE
    # tolère autant les relances que les doublons déjà présents.
    op.execute(
        """
        INSERT OR IGNORE INTO release_identifiers
            (release_id, artist_id, source, external_id, created_at)
        SELECT id, artist_id, 'deezer', CAST(deezer_album_id AS TEXT), CURRENT_TIMESTAMP
        FROM releases WHERE deezer_album_id IS NOT NULL
        """
    )
    op.execute(
        """
        INSERT OR IGNORE INTO release_track_sources
            (release_track_id, source, external_track_id, matched_by, created_at)
        SELECT id, COALESCE(source, 'legacy'), CAST(source_track_id AS TEXT),
               COALESCE(matched_by, 'legacy'), CURRENT_TIMESTAMP
        FROM release_tracks
        """
    )


def downgrade() -> None:
    op.drop_table("release_track_sources")
    op.drop_table("release_identifiers")
    with op.batch_alter_table("release_tracks") as batch_op:
        batch_op.drop_column("external_track_id")
    with op.batch_alter_table("releases") as batch_op:
        batch_op.drop_column("status")
