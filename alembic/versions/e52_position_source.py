"""release_tracks : la source qui a dit la PLACE du morceau sur le disque

Revision ID: e52_position_source
Revises: e51_regles_appliquees
Create Date: 2026-10-14

Deux fiches au MÊME emplacement d'un même disque sont un seul morceau ; à deux
emplacements, deux morceaux — mais seulement à source égale. La colonne
`source` d'un lien dit qui l'a CRÉÉ, pas qui l'a numéroté : la position était
fusionnée par `COALESCE`, la dernière écriture l'emportait, et un numéro de la
page album Genius (faux dès qu'un titre du disque n'y a pas de page : le
compteur saute — A2H *Art de vivre*, « Trivette » 13 → 12) se mêlait à ceux de
Deezer sur une même parution (`e33` y a versé les liens hérités).

Reprise de l'existant, conservatrice :
  · lien `legacy` numéroté → `legacy` (provenance inconnue, jamais comparée —
    un run Deezer la réécrira, `deezer` passant devant `legacy`) ;
  · tout autre lien numéroté → `deezer` : c'est la SEULE source qui ait jamais
    écrit un numéro dans `release_tracks` en dehors de `e31` (les observations
    Genius, Spotify, Kworb et manuelles n'en portent pas). Les « liens Genius
    numérotés » mesurés le 2026-10-12 (49) tenaient donc leur numéro de Deezer.
Définition identique à `src/persistence/schema.py`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e52_position_source"
down_revision: str | Sequence[str] | None = "e51_regles_appliquees"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def reprendre(conn) -> None:
    """Idempotent : ne touche que les lignes numérotées sans provenance."""
    conn.execute(
        sa.text(
            "UPDATE release_tracks SET position_source = "
            "CASE WHEN source = 'legacy' THEN 'legacy' ELSE 'deezer' END "
            "WHERE track_number IS NOT NULL AND position_source IS NULL"
        )
    )


def upgrade() -> None:
    with op.batch_alter_table("release_tracks") as batch_op:
        batch_op.add_column(sa.Column("position_source", sa.Text(), nullable=True))
    reprendre(op.get_bind())


def downgrade() -> None:
    with op.batch_alter_table("release_tracks") as batch_op:
        batch_op.drop_column("position_source")
