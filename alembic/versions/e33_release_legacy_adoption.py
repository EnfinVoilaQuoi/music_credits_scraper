"""Fusionne les parutions héritées avec leur jumelle Deezer.

Revision ID: e33_release_legacy_adoption
Revises: e32_release_identity_pipeline
Create Date: 2026-09-22

e31 a créé une parution `legacy:` par album de `tracks.album` ; le résolveur
ouvrait ensuite une parution `deezer:` NEUVE pour le même disque au premier
rattachement (mesuré : 53 titres en double, « Matrix » de Josman sous les deux
clés). Le résolveur ADOPTE désormais la parution héritée ; cette révision
rejoue l'adoption sur ce qui a déjà été écrit : les liens de la parution
héritée rejoignent la parution Deezer la plus ancienne portant la même clé de
titre (`cle_album`, « … » = « ... »), puis la parution héritée disparaît.
Les doublons entre deux parutions Deezer (deux éditions) sont conservés.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from src.utils.title_matching import cle_album

revision: str = "e33_release_legacy_adoption"
down_revision: str | Sequence[str] | None = "e32_release_identity_pipeline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def fusionner_heritees(conn) -> int:
    """Rend le nombre de parutions héritées absorbées. Idempotent."""
    rows = conn.execute(
        sa.text(
            "SELECT id, artist_id, title, identity_key FROM releases "
            "WHERE identity_key LIKE 'legacy:%' OR identity_key LIKE 'deezer:%' ORDER BY id"
        )
    ).all()
    deezer: dict[tuple[int, str], int] = {}
    for rid, artist_id, title, key in rows:
        if key.startswith("deezer:"):
            deezer.setdefault((artist_id, cle_album(title)), rid)
    absorbees = 0
    for rid, artist_id, title, key in rows:
        if not key.startswith("legacy:"):
            continue
        cible = deezer.get((artist_id, cle_album(title)))
        if cible is None:
            continue
        # Liens que la cible porte déjà : leurs preuves puis eux-mêmes.
        conn.execute(
            sa.text(
                "DELETE FROM release_track_sources WHERE release_track_id IN ("
                "SELECT h.id FROM release_tracks h WHERE h.release_id = :src AND EXISTS ("
                "SELECT 1 FROM release_tracks k WHERE k.release_id = :dst AND k.track_id = h.track_id))"
            ),
            {"src": rid, "dst": cible},
        )
        conn.execute(
            sa.text(
                "DELETE FROM release_tracks WHERE release_id = :src AND EXISTS ("
                "SELECT 1 FROM release_tracks k WHERE k.release_id = :dst "
                "AND k.track_id = release_tracks.track_id)"
            ),
            {"src": rid, "dst": cible},
        )
        conn.execute(
            sa.text("UPDATE release_tracks SET release_id = :dst WHERE release_id = :src"),
            {"src": rid, "dst": cible},
        )
        conn.execute(
            sa.text("DELETE FROM release_identifiers WHERE release_id = :src"), {"src": rid}
        )
        conn.execute(sa.text("DELETE FROM releases WHERE id = :src"), {"src": rid})
        absorbees += 1
    return absorbees


def upgrade() -> None:
    fusionner_heritees(op.get_bind())


def downgrade() -> None:
    # Fusion de données : pas de retour en arrière (la parution Deezer garde
    # tous les liens, rien n'est perdu).
    pass
