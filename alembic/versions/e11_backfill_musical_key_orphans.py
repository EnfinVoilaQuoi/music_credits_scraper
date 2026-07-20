"""backfill key/mode des orphelins musical_key (E7 Chantier 3, prépa E7-D2)

Revision ID: e11_musical_key_orphans
Revises: e10_backfill_legacy_obs
Create Date: 2026-07-19

Certains morceaux portent un `musical_key` en colonne SANS `key`/`mode` (vieux
enrichissements posant directement la tonalité française). `musical_key` étant une
colonne DÉRIVÉE destinée au drop (E7-D2), ces orphelins la PERDRAIENT — aucune
paire key/mode d'où la reconstruire.

On préserve donc en RÉTRO-DÉRIVANT : `musical_key_to_pitch_mode` décompose la
tonalité FR ("Si mineur" → pitch class 11, mode 0), qu'on backfill en observations
`key`/`mode` `source='legacy'` (paire complète d'une même source → le moteur les
apparie, `musical_key` se recalcule à l'identique via `key_mode_to_french`). Bonus
assumé : ces morceaux gagnent key/mode (cohérents avec leur tonalité).

Ne touche QUE les orphelins totaux (ni obs `key` ni obs `mode`) — une paire
legacy complète est nécessaire à l'appariement. `seen_at` = `updated_at` sinon la
date de migration. Idempotent (l'orphelin traité n'en est plus un). Essai sur
COPIE avant la prod.
"""

from datetime import datetime
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op
from src.utils.music_theory import musical_key_to_pitch_mode

# revision identifiers, used by Alembic.
revision: str = "e11_musical_key_orphans"
down_revision: Union[str, Sequence[str], None] = "e10_backfill_legacy_obs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    orphans = conn.execute(
        sa.text(
            "SELECT t.id, t.musical_key, t.updated_at FROM tracks t "
            "WHERE t.musical_key IS NOT NULL AND t.musical_key != '' "
            "AND NOT EXISTS (SELECT 1 FROM observations o "
            "WHERE o.track_id = t.id AND o.field = 'key') "
            "AND NOT EXISTS (SELECT 1 FROM observations o "
            "WHERE o.track_id = t.id AND o.field = 'mode')"
        )
    ).fetchall()

    for track_id, musical_key, updated_at in orphans:
        parsed = musical_key_to_pitch_mode(musical_key)
        if parsed is None:
            continue  # tonalité non interprétable → reste orpheline (rare)
        pitch_class, mode = parsed
        seen = updated_at or now
        for field, value in (("key", pitch_class), ("mode", mode)):
            conn.execute(
                sa.text(
                    "INSERT INTO observations (track_id, field, value, source, confidence, seen_at) "
                    "VALUES (:tid, :field, :value, 'legacy', NULL, :seen)"
                ),
                {"tid": track_id, "field": field, "value": str(value), "seen": seen},
            )


def downgrade() -> None:
    # Cible les orphelins rétro-dérivés : obs legacy key/mode sur un morceau dont
    # les colonnes key/mode sont NULL (signature de l'orphelin d'origine).
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "DELETE FROM observations WHERE source = 'legacy' AND field IN ('key', 'mode') "
            "AND track_id IN (SELECT id FROM tracks "
            'WHERE musical_key IS NOT NULL AND "key" IS NULL AND "mode" IS NULL)'
        )
    )
