"""observations : drop des lignes bpm à source COMBINÉE (backfill E4)

Revision ID: e5_drop_combined_bpm_obs
Revises: e4_observations
Create Date: 2026-07-15

Phase E6 (bascule lecture). Les observations `bpm` créées par le BACKFILL E4
portaient une source COMBINÉE (`reccobeats+songbpm`, reflet de la colonne
`bpm_source` legacy). Depuis E5c-2a, les observations bpm sont émises PAR SOURCE.
Or la lecture E6 réconcilie les observations d'un morceau : une ligne à source
combinée y serait un candidat FANTÔME qui gonflerait le vote. On la supprime.

Couverture préservée : la colonne legacy `bpm` reste écrite (triple écriture).
Un morceau jamais réenrichi depuis le backfill perd son observation bpm → le
mapper retombe sur la colonne legacy (fallback intrinsèque : reconcile n'émet
rien, apply_resolutions ne touche pas le champ). Aucune régression d'affichage.

Ciblage `field='bpm'` uniquement : key/mode ont toujours une source unique
(`key_mode_source`), jamais combinée.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5_drop_combined_bpm_obs"
down_revision: Union[str, Sequence[str], None] = "e4_observations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Supprime les observations bpm à source combinée (contenant un '+')."""
    op.get_bind().execute(
        sa.text("DELETE FROM observations WHERE field = 'bpm' AND source LIKE '%+%'")
    )


def downgrade() -> None:
    """Irréversible : l'information PAR SOURCE n'existait pas au backfill, les
    lignes combinées ne sont pas reconstituables. No-op (les colonnes legacy
    restent la source de vérité)."""
