"""track_videos : un morceau a souvent DEUX vidéos, la base n'en gardait qu'une

Revision ID: e20_track_videos
Revises: e19_deezer_identifiants
Create Date: 2026-09-07

Un morceau existe fréquemment en DEUX vidéos sur YouTube : le clip officiel et
la version « audio » servie par YouTube Music depuis la chaîne « - Topic ». Les
colonnes `tracks.youtube_url` / `youtube_video_views` n'en portent qu'UNE — la
seconde n'était donc mesurée nulle part, et ses vues perdues (cas mesurés :
« Magot », « Déluge »).

La clé métier est le **video id** (11 caractères), pas l'URL : `youtu.be/X` et
`watch?v=X` désignent la même vidéo, et sommer les deux compterait deux fois.
D'où `UNIQUE(track_id, video_id)`.

Écrivain DÉDIÉ (`TrackRepository.record_track_videos`), jamais `save_track` :
c'est la règle posée le 2026-09-06 pour `certifications` / `relationships` — une
façade générique appelée par treize flux ne peut pas écrire une donnée que douze
d'entre eux ignorent sans rendre tout RETRAIT impossible.

**Les colonnes `tracks.youtube_*` restent en place** et gardent leur rôle : la
vidéo « principale », celle que la GUI affiche et sur laquelle porte le choix de
lien. La table les complète.

Backfill : une ligne par morceau qui a déjà un lien. Mesuré sur la base réelle
au 2026-09-07 — 1 505 morceaux sur 1 698, dont 1 493 `genius_media`, 11 `manual`
et 1 `search_auto` ; aucune provenance nulle, aucune URL dont le video id ne
s'extraie. La provenance est recopiée VERBATIM (pas de défaut inventé) et les
vues suivent leur horodatage d'origine.
"""

from datetime import datetime
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op
from src.utils.youtube_utils import extract_video_id

# revision identifiers, used by Alembic.
revision: str = "e20_track_videos"
down_revision: Union[str, Sequence[str], None] = "e19_deezer_identifiants"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Crée la table puis y verse la vidéo déjà connue de chaque morceau.

    Les colonnes reproduisent À L'IDENTIQUE, et DANS LE MÊME ORDRE, celles de
    `src/persistence/schema.py` : `test_alembic_baseline` compare les deux et
    est sensible à l'ordre.
    """
    op.create_table(
        "track_videos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=False),
        sa.Column("video_id", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("views", sa.Integer(), nullable=True),
        sa.Column("views_updated", sa.TIMESTAMP(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(["track_id"], ["tracks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("track_id", "video_id"),
        sqlite_autoincrement=True,
    )

    conn = op.get_bind()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        sa.text(
            "SELECT id, youtube_url, youtube_url_source, youtube_video_kind, "
            "youtube_video_views, youtube_video_views_updated FROM tracks "
            "WHERE youtube_url IS NOT NULL AND youtube_url != ''"
        )
    ).fetchall()

    for track_id, url, source, kind, views, views_updated in rows:
        video_id = extract_video_id(url)
        if not video_id:
            # Lien non reconnaissable : rien à indexer, la colonne le garde.
            continue
        conn.execute(
            sa.text(
                "INSERT INTO track_videos "
                "(track_id, video_id, url, kind, source, views, views_updated, created_at) "
                "VALUES (:tid, :vid, :url, :kind, :source, :views, :vu, :now)"
            ),
            {
                "tid": track_id,
                "vid": video_id,
                "url": url,
                "kind": kind,
                "source": source,
                "views": views,
                "vu": views_updated,
                "now": now,
            },
        )


def downgrade() -> None:
    """Retire la table. Les colonnes `tracks.youtube_*` n'ont pas bougé : rien
    de ce que portait la base avant cette révision n'est perdu."""
    op.drop_table("track_videos")
