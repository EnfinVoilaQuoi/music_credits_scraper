"""tracks : deezer_id, deezer_url, explicit_lyrics (des données qui s'évaporaient)

Revision ID: e19_deezer_identifiants
Revises: e18_credit_tracks
Create Date: 2026-09-06

Le provider Deezer POSAIT ces trois valeurs sur l'objet `Track` depuis toujours,
mais aucune colonne ne les attendait : elles disparaissaient à la fin du run, et
comptaient pourtant dans le `updated` qui fait dire à Deezer « enrichissement
réussi ». Repéré au tier 9 en couvrant `_apply_result` — les lignes n'étaient
atteintes par aucun test, ce qui est la trace habituelle du code qui ne sert à
personne.

Décision de l'utilisateur (2026-09-06) : on les garde, pour l'identification et
la vérification. Elles deviennent donc de vraies colonnes.

  · `deezer_id`  — identifiant du morceau chez Deezer (pivot de vérification,
    au même titre que `spotify_id` / `genius_id` / `discogs_id`) ;
  · `deezer_url` — lien de la page du morceau (vérification visuelle) ;
  · `explicit_lyrics` — drapeau « parental advisory » de Deezer. C'est un
    BOOLÉEN (« présence de paroles explicites »), pas un texte : il n'y a rien
    à croiser avec Genius. Nullable À DESSEIN — `NULL` veut dire « jamais
    mesuré », `0` veut dire « Deezer dit que non ». Confondre les deux, c'est
    exactement l'erreur que `spotify_id_checked_at` (e17) a servi à réparer.

Pas de backfill : rien en base ne permet de reconstituer ces valeurs après coup.
Elles se rempliront au prochain enrichissement Deezer de chaque morceau.

Le quatrième attribut du même lot, `deezer_picture_url`, n'est PAS persisté et
sa pose est retirée : le chantier Media récupère déjà la même image en HAUTE
résolution (`deezer_cover_xl` / `deezer_picture_xl` → `media_enricher` →
fichier sur disque + `tracks.cover_path`). C'était un doublon dégradé.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e19_deezer_identifiants"
down_revision: Union[str, Sequence[str], None] = "e18_credit_tracks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Ajoute les trois colonnes. Aucun backfill (cf. docstring)."""
    with op.batch_alter_table("tracks") as batch_op:
        batch_op.add_column(sa.Column("deezer_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("deezer_url", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("explicit_lyrics", sa.Boolean(create_constraint=False), nullable=True)
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("tracks") as batch_op:
        batch_op.drop_column("explicit_lyrics")
        batch_op.drop_column("deezer_url")
        batch_op.drop_column("deezer_id")
