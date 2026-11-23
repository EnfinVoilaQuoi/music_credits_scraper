"""Retrait de la table `albums` : les projets la remplacent

Revision ID: e69_retrait_albums
Revises: e68_projets_nature_streams
Create Date: 2026-11-20

`albums` rangeait les streams et la nature d'un disque sous son TITRE (clé
`(title, artist_id)`) : 143 titres en double une fois normalisés, 8 135 fiches
pointant un album absent (AUDIT §4.2). e68 a reporté son contenu sur les
projets (1 008 / 1 014 totaux Spotify, 4 252 / 4 257 YTM sur la base du
2026-11-20 ; le reste : titres ambigus ou sans parution) ; tous ses lecteurs et
écrivains passent par les projets. Décision utilisateur 2026-11-20 (« Validé »).

Le retour en arrière recrée la table VIDE (sa définition e15-e26) : le contenu
retiré n'est pas reconstruit — il est dans la sauvegarde faite avant l'upgrade.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from src.utils.projets_en_base import projet_de

revision: str = "e69_retrait_albums"
down_revision: str | Sequence[str] | None = "e68_projets_nature_streams"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    # Ce que le report e68 n'a pu poser (même juge : `projet_de`), dit au journal.
    perdues = sum(
        1
        for artiste, titre, deezer, spotify in conn.execute(
            sa.text(
                "SELECT artist_id, title, deezer_album_id, spotify_album_ids FROM albums "
                "WHERE spotify_streams IS NOT NULL OR ytm_streams IS NOT NULL"
            )
        ).all()
        if projet_de(conn, artiste, deezer_album_id=deezer, spotify_album_ids=spotify, titre=titre)
        is None
    )
    if perdues:
        print(f"e69 : {perdues} total(aux) d'album sans disque désigné ne sont pas gardés")
    op.drop_table("albums")


def downgrade() -> None:
    op.create_table(
        "albums",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("artist_id", sa.Integer(), sa.ForeignKey("artists.id"), nullable=False),
        sa.Column("spotify_streams", sa.Integer()),
        sa.Column("spotify_daily_streams", sa.Integer()),
        sa.Column("spotify_streams_updated", sa.TIMESTAMP()),
        sa.Column("ytm_streams", sa.Integer()),
        sa.Column("ytm_streams_updated", sa.TIMESTAMP()),
        sa.Column("spotify_album_ids", sa.Text()),
        sa.Column("spotify_streams_source", sa.Text()),
        sa.Column("spotify_editions_json", sa.Text()),
        sa.Column("record_type", sa.Text()),
        sa.Column("record_type_source", sa.Text()),
        sa.Column("record_type_updated", sa.TIMESTAMP()),
        sa.Column("deezer_album_id", sa.Integer()),
        sa.UniqueConstraint("title", "artist_id"),
        sqlite_autoincrement=True,
    )
