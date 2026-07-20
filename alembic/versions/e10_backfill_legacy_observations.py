"""backfill observations `source='legacy'` (E7 Chantier 3, E7-D0)

Revision ID: e10_backfill_legacy_obs
Revises: e9_media_images
Create Date: 2026-07-19

Phase E7, Chantier 3 (arrêt écriture legacy + drop des colonnes audio). Prérequis
mesurable du drop : 100 % des morceaux ayant une donnée audio EN COLONNE doivent
avoir une observation d'où `reconcile` reproduit la valeur (go/no-go :
`scripts/check_observations.py --coverage`). Les morceaux enrichis AVANT l'ère
per-source (E5) — ou jamais réenrichis depuis — n'ont pas d'observation : cette
révision les backfill.

Pour chaque champ, on insère une observation `source='legacy'` UNIQUEMENT là où la
colonne est renseignée mais qu'aucune observation du champ n'existe :

    bpm            -> field='bpm'            (value=bpm, confidence=bpm_confidence)
    key            -> field='key'            (value=key)
    mode           -> field='mode'           (value=mode)
    bpm_alt        -> field='bpm_alt'        (value=bpm_alt)  — voir ci-dessous
    time_signature -> field='time_signature' (value=time_signature)

`seen_at` = `updated_at` du morceau (dernière fois qu'il a été touché), à défaut
la date de migration. `confidence` INTEGER legacy -> REAL.

Règle moteur (déjà en place, `src/enrichment/reconcile.py`) : une observation
`legacy` ne sert QUE seule — elle est reprise VERBATIM (la colonne portait déjà la
valeur réconciliée ; la re-voter la fausserait) et ÉCARTÉE du vote dès qu'une
source réelle existe (candidat fantôme sinon).

`bpm_alt` est une valeur DÉRIVÉE du vote, irrécupérable pour un candidat unique :
la branche legacy-seul reprend l'alt depuis l'observation `bpm_alt`. On la backfill
donc pour les morceaux SANS vraie source bpm (les morceaux avec vote réel voient
leur alt re-dérivé — inutile d'en persister une).

Idempotent (les gardes `NOT EXISTS` sautorisent un re-run) et réversible (le
downgrade supprime les observations `legacy`). Essai sur COPIE avant la prod.
"""

from datetime import datetime
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e10_backfill_legacy_obs"
down_revision: Union[str, Sequence[str], None] = "e9_media_images"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LEGACY = "legacy"


def upgrade() -> None:
    conn = op.get_bind()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    seen = "COALESCE(t.updated_at, :now)"

    def backfill(field: str, column: str, where: str, confidence: str = "NULL") -> None:
        conn.execute(
            sa.text(
                "INSERT INTO observations (track_id, field, value, source, confidence, seen_at) "
                f"SELECT t.id, '{field}', CAST(t.{column} AS TEXT), '{LEGACY}', {confidence}, {seen} "
                f"FROM tracks t WHERE {where} "
                "AND NOT EXISTS (SELECT 1 FROM observations o "
                f"WHERE o.track_id = t.id AND o.field = '{field}')"
            ),
            {"now": now},
        )

    # bpm / key / mode / time_signature : colonne renseignée, aucune obs du champ.
    backfill("bpm", "bpm", "t.bpm IS NOT NULL", confidence="CAST(t.bpm_confidence AS REAL)")
    backfill("key", '"key"', "t.\"key\" IS NOT NULL AND t.\"key\" != ''")
    backfill("mode", '"mode"', "t.\"mode\" IS NOT NULL AND t.\"mode\" != ''")
    backfill(
        "time_signature",
        "time_signature",
        "t.time_signature IS NOT NULL AND t.time_signature != ''",
    )

    # bpm_alt : dérivé du vote, préservé SEULEMENT quand aucune vraie source bpm
    # n'existe (sinon le vote le re-dérive). D'où la garde `source != 'legacy'`.
    conn.execute(
        sa.text(
            "INSERT INTO observations (track_id, field, value, source, confidence, seen_at) "
            f"SELECT t.id, 'bpm_alt', CAST(t.bpm_alt AS TEXT), '{LEGACY}', NULL, {seen} "
            "FROM tracks t WHERE t.bpm_alt IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM observations o "
            "WHERE o.track_id = t.id AND o.field = 'bpm' AND o.source != 'legacy') "
            "AND NOT EXISTS (SELECT 1 FROM observations o "
            "WHERE o.track_id = t.id AND o.field = 'bpm_alt')"
        ),
        {"now": now},
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(f"DELETE FROM observations WHERE source = '{LEGACY}'"))
