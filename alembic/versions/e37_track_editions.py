"""track_editions : les éditions de diffusion d'un morceau vivent dans sa fiche

Revision ID: e37_track_editions
Revises: e36_tracks_identite_genius
Create Date: 2026-09-24

Décision utilisateur (2026-09-24) : original, radio edit, clean, explicit,
album/single version et remaster sont UN morceau — « aucun intérêt à avoir deux
fiches ». La ligne de la fiche garde les données de l'original (durée, paroles) ;
chaque édition est détaillée ici, avec ses propres identifiants (« radio edit de
3:12 », son ID Spotify, son ISRC, la page Genius qu'elle absorbait).

Table seule, aucune donnée déplacée : la fusion des fiches d'édition existantes
est un script (`scripts/fusionner_editions.py`), revu en dry-run avant écriture.
Les colonnes suivent À L'IDENTIQUE l'ordre de `src/persistence/schema.py`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e37_track_editions"
down_revision: str | Sequence[str] | None = "e36_tracks_identite_genius"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "track_editions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("duration", sa.Integer(), nullable=True),
        sa.Column("spotify_id", sa.Text(), nullable=True),
        sa.Column("deezer_id", sa.Integer(), nullable=True),
        sa.Column("isrc", sa.Text(), nullable=True),
        sa.Column("genius_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(["track_id"], ["tracks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("track_id", "label"),
        sqlite_autoincrement=True,
    )


def downgrade() -> None:
    op.drop_table("track_editions")
