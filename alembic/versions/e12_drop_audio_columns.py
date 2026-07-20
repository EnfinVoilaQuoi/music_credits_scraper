"""drop des colonnes audio (E7 Chantier 3, E7-D2)

Revision ID: e12_drop_audio_columns
Revises: e11_musical_key_orphans
Create Date: 2026-07-20

Point d'arrivée du Chantier 3 : les 10 colonnes audio ne sont plus la vérité
(depuis E7-D1 elles ne sont plus écrites ; la vérité vit dans `observations`,
relue par la réconciliation du mapper). Prérequis validé AVANT ce drop :
`scripts/check_observations.py --coverage` = GO (100 % des données audio en
colonne ont une observation équivalente — backfill e10 + orphelins e11).

Colonnes dropées (SQLite = rebuild via batch_alter_table ; backup auto par
`upgrade_to_head`) :
    bpm, bpm_alt, bpm_source, bpm_confidence, key, mode, key_mode_source,
    musical_key, time_signature (reconstruites depuis les observations),
    reccobeats_resolution (provenance debug, perte assumée).

Les ATTRIBUTS `Track.bpm/key/mode/...` SUBSISTENT (posés par la réconciliation) —
seule la persistance colonne disparaît. `to_dict`/GUI lisent les attributs.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e12_drop_audio_columns"
down_revision: Union[str, Sequence[str], None] = "e11_musical_key_orphans"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DROPPED = (
    "bpm",
    "bpm_alt",
    "bpm_source",
    "bpm_confidence",
    "key",
    "mode",
    "key_mode_source",
    "musical_key",
    "time_signature",
    "reccobeats_resolution",
)


def upgrade() -> None:
    with op.batch_alter_table("tracks") as batch_op:
        for column in _DROPPED:
            batch_op.drop_column(column)


def downgrade() -> None:
    # Ré-ajout des colonnes VIDES (les données restent dans `observations`). Types
    # d'origine (cf. schéma legacy) : bpm/bpm_alt/bpm_confidence INTEGER, le reste TEXT.
    with op.batch_alter_table("tracks") as batch_op:
        batch_op.add_column(sa.Column("bpm", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("bpm_alt", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("bpm_source", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("bpm_confidence", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("key", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("mode", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("key_mode_source", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("musical_key", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("time_signature", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("reccobeats_resolution", sa.Text(), nullable=True))
