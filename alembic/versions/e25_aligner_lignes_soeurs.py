"""Aligne les lignes SŒURS déjà en base — un enregistrement, une seule vérité

Revision ID: e25_lignes_soeurs
Revises: e24_discographie_obs
Create Date: 2026-09-08

`save_track` synchronise désormais les lignes d'un même enregistrement à chaque
écriture (lot C), mais **ce qui a déjà divergé ne se répare pas tout seul** :
« Grünt #33 » portait 36 crédits, les paroles et les streams chez Swing, et 0
crédit, 0 parole, 0 observation chez Isha. Mesuré au 2026-09-08 : 14 familles,
28 lignes, dont **13 divergentes**.

**Aucune règle n'est réimplémentée ici** : la révision rejoue
`track_soeurs.synchroniser_soeurs`, la même fonction que le runtime. Corriger la
règle corrige donc ce backfill — même principe que `repair_certifications.py`,
qui rejoue `apply_certifications` plutôt que d'en recopier la logique.

La fonction est IDEMPOTENTE (unions et comblements, jamais de remplacement), ce
qui rend cette migration sûre à rejouer et son résultat indépendant de l'ordre
des familles.

⚠️ Les doublons INTRA-ARTISTE ne sont pas synchronisés, seulement SIGNALÉS dans
le log : leur DIVERGENCE est justement ce qui les trahit, et les aligner les
rendrait invisibles. Un seul cas connu (Josman « BOSS » / « Boss »), à fusionner
à la main.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op
from src.utils.track_soeurs import synchroniser_soeurs

# revision identifiers, used by Alembic.
revision: str = "e25_lignes_soeurs"
down_revision: Union[str, Sequence[str], None] = "e24_discographie_obs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Synchronise chaque famille d'un `genius_id` partagé par plusieurs lignes."""
    conn = op.get_bind()
    familles = conn.execute(
        sa.text(
            "SELECT genius_id, MIN(id) AS chef FROM tracks "
            "WHERE genius_id IS NOT NULL GROUP BY genius_id HAVING COUNT(*) > 1"
        )
    ).fetchall()

    for genius_id, chef in familles:
        # Un seul appel par famille suffit : la synchronisation aligne TOUTES ses
        # lignes d'un coup, dans les deux sens (chacune comble les trous des
        # autres). L'appeler depuis chaque ligne referait le même travail.
        synchroniser_soeurs(conn, chef, genius_id)


def downgrade() -> None:
    """Aucune annulation possible, et c'est assumé.

    La synchronisation ne fait que REMPLIR : rien n'a été retiré, donc rien n'est
    « à remettre ». Défaire reviendrait à choisir quelles données effacer chez
    quelle ligne — une décision que la révision n'a pas prise et ne peut pas
    reconstituer. Le backup automatique d'avant-migration est le vrai chemin de
    retour.
    """
