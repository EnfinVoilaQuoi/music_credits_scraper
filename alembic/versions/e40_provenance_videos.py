"""track_videos : la provenance oubliée de la vidéo principale

Revision ID: e40_provenance_videos
Revises: e39_revue_corrections
Create Date: 2026-09-28

Le relevé des vues (`update_video_views`) insérait la vidéo du lien
`tracks.youtube_url` sans recopier `tracks.youtube_url_source` : 387 lignes
sans provenance, TOUTES la vidéo principale d'une fiche dont la colonne dit
`genius_media`. On recopie la provenance que la fiche porte déjà — aucune
valeur inventée, seulement là où la vidéo EST celle du lien.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e40_provenance_videos"
down_revision: str | Sequence[str] | None = "e39_revue_corrections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE track_videos SET source = ("
        "SELECT t.youtube_url_source FROM tracks t WHERE t.id = track_videos.track_id) "
        "WHERE source IS NULL AND EXISTS ("
        "SELECT 1 FROM tracks t WHERE t.id = track_videos.track_id "
        "AND t.youtube_url_source IS NOT NULL "
        "AND t.youtube_url LIKE '%' || track_videos.video_id || '%')"
    )


def downgrade() -> None:
    # Irréversible par nature : rien ne distinguerait ensuite une provenance
    # recopiée d'une provenance d'origine. Aucune valeur n'a été inventée.
    pass
