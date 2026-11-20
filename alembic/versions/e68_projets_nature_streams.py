"""projets : nature et streams d'album ; releases : total Spotify de l'édition

Revision ID: e68_projets_nature_streams
Revises: e67_projets
Create Date: 2026-11-20

La nature (EP / album) et les streams d'album vivaient dans `albums`, sous le
TITRE que chaque source donne au disque (143 titres en double une fois
normalisés, 3 270 natures absentes de la parution qu'elles décrivent). Ils
passent au PROJET (e67). Report :
- nature : `albums.record_type` (Deezer) va sur les éditions au même ID Deezer
  qui n'en ont pas, puis chaque projet calcule son verdict ; une nature
  `manual` va au projet que son titre désigne ;
- streams : chaque ligne `albums` va au projet que désignent son ID Deezer,
  ses ID d'album Spotify ou son titre (un seul projet, sinon rien), aux
  arbitrages d'`upsert_album`, les plus anciennes d'abord ; le total par
  édition de `spotify_editions_json` va sur l'édition ; un total YTM va sur
  l'édition que nomme son titre, et le projet prend le plus grand.
`albums` n'est pas touchée : ses lecteurs passent aux projets dans un lot
suivant. Définition identique à `src/persistence/schema.py`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from src.utils.projets_en_base import (
    poser_nature_manuelle,
    poser_streams_spotify,
    poser_streams_ytm,
    projet_de,
    recalculer_nature,
)

revision: str = "e68_projets_nature_streams"
down_revision: str | Sequence[str] | None = "e67_projets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PROJETS = (
    ("nature", sa.Text()),
    ("nature_source", sa.Text()),
    ("nature_maj", sa.TIMESTAMP()),
    ("spotify_streams", sa.Integer()),
    ("spotify_daily_streams", sa.Integer()),
    ("spotify_streams_updated", sa.TIMESTAMP()),
    ("spotify_streams_source", sa.Text()),
    ("spotify_album_ids", sa.Text()),
    ("ytm_streams", sa.Integer()),
    ("ytm_streams_updated", sa.TIMESTAMP()),
)
_RELEASES = (
    ("spotify_streams", sa.Integer()),
    ("spotify_streams_updated", sa.TIMESTAMP()),
    ("ytm_streams", sa.Integer()),
    ("ytm_streams_updated", sa.TIMESTAMP()),
)


def reporter(conn) -> dict[str, int]:
    """Le report d'`albums` vers les projets. Rend les comptes (tests, journal)."""
    t = sa.text
    comptes = {"natures_editions": 0, "spotify": 0, "ytm": 0, "manuelles": 0, "sans_projet": 0}
    comptes["natures_editions"] = conn.execute(
        t(
            "UPDATE releases SET record_type = (SELECT a.record_type FROM albums a "
            "WHERE a.artist_id = releases.artist_id AND a.deezer_album_id = releases.deezer_album_id "
            "AND a.record_type IS NOT NULL AND a.record_type_source = 'deezer' LIMIT 1) "
            "WHERE record_type IS NULL AND deezer_album_id IS NOT NULL AND EXISTS ("
            "SELECT 1 FROM albums a WHERE a.artist_id = releases.artist_id "
            "AND a.deezer_album_id = releases.deezer_album_id AND a.record_type IS NOT NULL "
            "AND a.record_type_source = 'deezer')"
        )
    ).rowcount
    for (pid,) in conn.execute(t("SELECT id FROM projets ORDER BY id")).all():
        recalculer_nature(conn, pid)
    lignes = (
        conn.execute(
            t(
                "SELECT artist_id, title, deezer_album_id, record_type, record_type_source, "
                "spotify_streams, spotify_daily_streams, spotify_streams_updated, "
                "spotify_streams_source, spotify_album_ids, spotify_editions_json, "
                "ytm_streams, ytm_streams_updated FROM albums"
            )
        )
        .mappings()
        .all()
    )
    sans = set()
    for a in lignes:
        if a["record_type_source"] == "manual" and a["record_type"]:
            pid = projet_de(conn, a["artist_id"], titre=a["title"])
            if pid is not None:
                poser_nature_manuelle(conn, pid, a["record_type"])
                comptes["manuelles"] += 1
    for a in sorted(
        (a for a in lignes if a["spotify_streams"] is not None),
        key=lambda a: str(a["spotify_streams_updated"] or ""),
    ):
        pid = projet_de(
            conn,
            a["artist_id"],
            deezer_album_id=a["deezer_album_id"],
            spotify_album_ids=a["spotify_album_ids"],
            titre=a["title"],
        )
        if pid is None:
            sans.add((a["artist_id"], a["title"]))
            continue
        poser_streams_spotify(
            conn,
            pid,
            streams=a["spotify_streams"],
            daily_streams=a["spotify_daily_streams"],
            spotify_album_ids=a["spotify_album_ids"],
            updated_at=a["spotify_streams_updated"],
            source=a["spotify_streams_source"] or "kworb",
            editions_json=a["spotify_editions_json"],
        )
        comptes["spotify"] += 1
    for a in sorted(
        (a for a in lignes if a["ytm_streams"] is not None),
        key=lambda a: str(a["ytm_streams_updated"] or ""),
    ):
        pid = projet_de(
            conn, a["artist_id"], deezer_album_id=a["deezer_album_id"], titre=a["title"]
        )
        if pid is None:
            sans.add((a["artist_id"], a["title"]))
            continue
        poser_streams_ytm(conn, pid, a["ytm_streams"], a["ytm_streams_updated"], titre=a["title"])
        comptes["ytm"] += 1
    comptes["sans_projet"] = len(sans)
    return comptes


def upgrade() -> None:
    with op.batch_alter_table("projets") as batch_op:
        for nom, type_ in _PROJETS:
            batch_op.add_column(sa.Column(nom, type_, nullable=True))
    with op.batch_alter_table("releases") as batch_op:
        for nom, type_ in _RELEASES:
            batch_op.add_column(sa.Column(nom, type_, nullable=True))
    reporter(op.get_bind())


def downgrade() -> None:
    with op.batch_alter_table("releases") as batch_op:
        for nom, _ in reversed(_RELEASES):
            batch_op.drop_column(nom)
    with op.batch_alter_table("projets") as batch_op:
        for nom, _ in reversed(_PROJETS):
            batch_op.drop_column(nom)
