"""tracks : unreleased (« pas encore sorti » ≠ données manquantes)

Revision ID: e34_inedit
Revises: e33_release_legacy_adoption
Create Date: 2026-09-23

Genius référence des morceaux INÉDITS — leaks, snippets, titres annoncés — et
la communauté les marque d'une **astérisque finale** dans le titre (168 en base
le 2026-09-22 : Travis Scott « Drugs 2* », SCH « Némésis* », Kanye « Turn To
Gold* »). Rien ne les distinguait d'un morceau sorti dont on n'aurait ni la
durée, ni le BPM, ni les streams : ils portaient un ⚠️ perpétuel et pesaient
dans le compte des morceaux « à valider », qu'aucun run ne pourrait jamais
réduire.

  · `unreleased` — BOOLÉEN nullable À DESSEIN : `NULL` = jamais constaté,
    `1` = inédit constaté, `0` = sorti (une trace de plateforme le prouve).
    Même tri-état que `instrumental` (e27) et pour la même raison : un champ
    vide dirait à la fois « pas inédit » et « jamais regardé ».

**`lyrics_state` de l'API Genius n'est PAS cette information** — mesuré le
2026-09-23 sur les 168 marqués et 168 témoins (`scripts/mesurer_lyrics_state`) :
91 % des inédits sont `complete`, et 6 témoins sans astérisque sont
`unreleased`, dont « Mouvement » d'A2H, qui a 26 312 streams Spotify et une date
de sortie. Ce champ décrit l'état des PAROLES sur Genius, pas celui du morceau.
L'hypothèse était dans le plan ; la mesure l'a écartée avant qu'une ligne ne
soit écrite.

Pas de backfill ici : le constat doit précéder le RENOMMAGE (retirer
l'astérisque), qui passe par `rename_track` — donc par la déduplication, la
synchronisation des sœurs, et un refus explicite en cas de collision. Une
migration ne sait rien faire de tout cela : c'est le travail de
`scripts/backfill_inedits.py`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e34_inedit"
down_revision: str | Sequence[str] | None = "e33_release_legacy_adoption"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Ajoute la colonne. Aucun backfill (cf. docstring)."""
    with op.batch_alter_table("tracks") as batch_op:
        batch_op.add_column(
            sa.Column("unreleased", sa.Boolean(create_constraint=False), nullable=True)
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("tracks") as batch_op:
        batch_op.drop_column("unreleased")
