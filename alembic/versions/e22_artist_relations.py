"""artist_relations : qui appartient à quelle formation (lot 3)

Revision ID: e22_artist_relations
Revises: e21_track_video_title
Create Date: 2026-09-07

Genius n'expose pas l'appartenance à un groupe. Sans elle, la discographie d'un
membre est amputée de tout ce qu'il a sorti avec sa formation — Shurik'N sans
IAM, Swing sans L'Or du Commun — et l'audit des certifications déclare orphelin
tout ce que le membre n'a pas en propre (cf. WIP § recherche par artiste).

Table de LIENS, jamais de duplication de morceaux : la discographie réunie se
fera par **union d'`artist_id` à la LECTURE**. Dupliquer les lignes serait
interdit par `UNIQUE(title, artist_id)` et doublerait streams et certifications.

Ce que la table porte :

  · `artist_id`         — l'artiste de NOTRE base (le point de vue) ;
  · `related_artist_id` — l'autre bout, s'il est chez nous. **Nullable à
    dessein** : IAM peut être lié à Shurik'N sans qu'IAM soit en base, et c'est
    même le cas courant au départ. Un lien vers un artiste absent reste une
    information — il deviendra une jointure le jour où on l'ajoutera ;
  · `related_name`      — le nom de l'autre bout, VERBATIM de la source. C'est
    lui qui fait la clé, précisément parce que l'id peut manquer ;
  · `kind`              — `member_of` (je suis membre de) / `has_member` (a pour
    membre) / `alias`. Les deux premiers sont symétriques, mais on n'écrit QUE
    le point de vue observé : déduire l'inverse fabriquerait une donnée que
    personne n'a confirmée ;
  · `source`            — `musicbrainz` / `discogs` / `manual` ;
  · `formation`         — la NATURE de l'autre bout : `groupe` ou `collectif`.
    Elle décide de la lecture, et c'est toute la difficulté du lot :
      - un **groupe** (IAM, L'Or du Commun, Bavoog Avers) — tous ses morceaux
        entrent dans la discographie de chaque membre ;
      - un **collectif** (L'Animalerie) — SEULS entrent les morceaux où le
        membre est réellement présent, à l'écriture, à la production ou à la
        performance. Un collectif est une maison, pas une formation : tout le
        monde n'y travaille pas toujours ensemble, et il peut abriter un groupe
        plus petit (Bavoog Avers, quatuor issu de L'Animalerie), auquel cas les
        deux liens coexistent sur la personne, sans transitivité.
    Nulle pour un `alias`, où la question ne se pose pas. C'est une propriété de
    la FORMATION, pas du lien — elle est stockée ici parce que la formation
    n'est pas toujours en base, et la fenêtre pré-remplit la nature déjà choisie
    pour le même nom afin qu'elle ne diverge pas d'un membre à l'autre.
  · `begin_date`/`end_date` — dates d'appartenance, en TEXTE brut de la source
    (« 1989-10 », « 2009 ») : MusicBrainz rend des dates PARTIELLES, qu'un type
    date refuserait ou mutilerait.

**`end` n'est pas un nom de colonne** : c'est un mot réservé SQL, et le projet
écrit beaucoup de `text()` à la main — la colonne `key` de l'ancien schéma a déjà
imposé des `"key"` quotés dans les migrations. `begin_date`/`end_date` évitent le
piège plutôt que de le documenter.

La table ne contient que des liens CONFIRMÉS à la main : les candidats proposés
par les sources vivent le temps d'une fenêtre, ils ne sont pas persistés. Un
rapprochement de noms automatique n'a pas le droit de décider seul qui joue avec
qui.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e22_artist_relations"
down_revision: Union[str, Sequence[str], None] = "e21_track_video_title"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Crée la table. Colonnes à l'identique de `src/persistence/schema.py`,
    DANS LE MÊME ORDRE (`test_alembic_baseline` y est sensible)."""
    op.create_table(
        "artist_relations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("artist_id", sa.Integer(), nullable=False),
        sa.Column("related_artist_id", sa.Integer(), nullable=True),
        sa.Column("related_name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("formation", sa.Text(), nullable=True),  # groupe | collectif
        sa.Column("begin_date", sa.Text(), nullable=True),
        sa.Column("end_date", sa.Text(), nullable=True),
        sa.Column("confirmed_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(["artist_id"], ["artists.id"]),
        sa.ForeignKeyConstraint(["related_artist_id"], ["artists.id"]),
        sa.PrimaryKeyConstraint("id"),
        # La clé est le NOM et non l'id : l'autre bout n'est pas toujours en base.
        sa.UniqueConstraint("artist_id", "related_name", "kind"),
        sqlite_autoincrement=True,
    )


def downgrade() -> None:
    """Retire la table. Aucune donnée métier n'en dépend : la discographie
    réunie est une lecture, rien n'a été dupliqué."""
    op.drop_table("artist_relations")
