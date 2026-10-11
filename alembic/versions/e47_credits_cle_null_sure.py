"""credits : clé unique sûre face aux NULL ; retrait de `tracks.musical_key`

Revision ID: e47_credits_cle_null_sure
Revises: e46_source_track_absences
Create Date: 2026-10-11

`UNIQUE(track_id, name, role, role_detail)` ne protège rien quand
`role_detail` est NULL : dans un index UNIQUE SQLite, deux NULL sont
DISTINCTS. 12 doublons en base (Deezer « Featured Artist » ×2, un même
réalisateur ou producteur venu de Genius ET de la description du clip), alors
que tout le code tient `NULL` et `''` pour la même clé (`_unir_credits`,
`add_credit`). Les doublons sont retirés (on garde le crédit Genius s'il y en
a un, sinon le plus ancien), puis un index d'expression pose la règle en base.

`tracks.musical_key` : colonne audio oubliée par e12, toujours NULL (0 valeur
sur 11 577 fiches), absente de `schema.py` — la tonalité vit dans les
observations (`track.audio.musical_key` est dérivé). Retirée si présente.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e47_credits_cle_null_sure"
down_revision: str | Sequence[str] | None = "e46_source_track_absences"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "DELETE FROM credits WHERE id IN ("
            " SELECT c.id FROM credits c WHERE EXISTS ("
            "  SELECT 1 FROM credits d WHERE d.track_id = c.track_id AND d.name = c.name"
            "   AND d.role = c.role"
            "   AND IFNULL(d.role_detail, '') = IFNULL(c.role_detail, '')"
            "   AND d.id != c.id"
            "   AND ((IFNULL(d.source, '') = 'genius') > (IFNULL(c.source, '') = 'genius')"
            "        OR ((IFNULL(d.source, '') = 'genius') = (IFNULL(c.source, '') = 'genius')"
            "            AND d.id < c.id))))"
        )
    )
    op.create_index(
        "ux_credits_cle",
        "credits",
        ["track_id", "name", "role", sa.text("IFNULL(role_detail, '')")],
        unique=True,
    )
    colonnes = {r[1] for r in conn.execute(sa.text("PRAGMA table_info(tracks)"))}
    if "musical_key" in colonnes:
        # DROP COLUMN natif (SQLite >= 3.35) : la colonne n'est dans aucun index
        # ni contrainte, et un batch recréerait `tracks` et ses index partiels.
        conn.execute(sa.text("ALTER TABLE tracks DROP COLUMN musical_key"))


def downgrade() -> None:
    op.drop_index("ux_credits_cle", table_name="credits")
