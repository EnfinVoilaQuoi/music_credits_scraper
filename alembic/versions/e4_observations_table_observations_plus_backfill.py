"""observations : table + backfill bpm/key/mode

Revision ID: e4_observations
Revises: e1_initial_schema
Create Date: 2026-07-14

Phase E4. Crée la table `observations` (provenance scalaire par
(track_id, field, source), modèle upsert) et la remplit par BACKFILL depuis les
colonnes legacy `*_source` du trio audio réconciliable :

    bpm_source        -> observation field='bpm'   (value=bpm, confidence=bpm_confidence)
    key_mode_source   -> observation field='key'   (value=key)   si key non nul
    key_mode_source   -> observation field='mode'  (value=mode)  si mode non nul

Les champs à gros payload / non scalaires (lyrics, lyrics_synced, youtube_url,
streams) restent en colonnes : ils adopteront le modèle Observation à l'étage 2
(phase E7). `seen_at` = date de migration (aucune colonne `*_updated` pour
bpm/key/mode). `confidence` INTEGER legacy -> REAL.

Invariant (script de contrôle E4) : après backfill, il y a exactement une
observation par `bpm_source` non nul et une par champ non nul (key/mode) dont
`key_mode_source` est renseigné.
"""

from datetime import datetime
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4_observations"
down_revision: Union[str, Sequence[str], None] = "e1_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "observations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=False),
        sa.Column("field", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("confidence", sa.REAL(), nullable=True),
        sa.Column("seen_at", sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(["track_id"], ["tracks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("track_id", "field", "source"),
        sqlite_autoincrement=True,
    )

    # Backfill depuis les colonnes legacy. `seen_at` = date de migration (string
    # verbatim, cohérent avec le stockage TIMESTAMP legacy). Les colonnes `key` /
    # `mode` sont citées entre guillemets (noms sensibles selon le dialecte).
    conn = op.get_bind()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        sa.text(
            "INSERT INTO observations (track_id, field, value, source, confidence, seen_at) "
            "SELECT id, 'bpm', CAST(bpm AS TEXT), bpm_source, CAST(bpm_confidence AS REAL), :now "
            "FROM tracks WHERE bpm_source IS NOT NULL AND bpm IS NOT NULL"
        ),
        {"now": now},
    )
    conn.execute(
        sa.text(
            "INSERT INTO observations (track_id, field, value, source, confidence, seen_at) "
            "SELECT id, 'key', CAST(\"key\" AS TEXT), key_mode_source, NULL, :now "
            'FROM tracks WHERE key_mode_source IS NOT NULL AND "key" IS NOT NULL'
        ),
        {"now": now},
    )
    conn.execute(
        sa.text(
            "INSERT INTO observations (track_id, field, value, source, confidence, seen_at) "
            "SELECT id, 'mode', CAST(\"mode\" AS TEXT), key_mode_source, NULL, :now "
            'FROM tracks WHERE key_mode_source IS NOT NULL AND "mode" IS NOT NULL'
        ),
        {"now": now},
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("observations")
