"""tracks : une fiche Genius est unique par (artiste, genius_id), plus par titre

Revision ID: e36_tracks_identite_genius
Revises: e35_relations_suffixe_nature
Create Date: 2026-09-24

`UNIQUE(title, artist_id)` faisait FUSIONNER en une seule ligne des morceaux
Genius DIFFÉRENTS au même titre. L'API `/artists/{id}/songs` rend aussi les
covers de tiers où l'artiste n'est qu'auteur ; `save_track` retrouvait la fiche
par son titre, le premier `genius_id` gagnait (les suivants loggés « genius_id
concurrent », 132 collisions sur 117 fiches) et les autres champs se
mélangeaient. « goosebumps » portait la page de la cover de Skylar Grey (d'où
« Skylar Grey » en artiste principal) et les streams du morceau de Travis.

Nouvelle identité :
  · une fiche Genius est unique par (artist_id, genius_id) ;
  · le titre reste unique seulement parmi les fiches SANS genius_id (Deezer,
    remix Kworb), qui n'ont que lui pour être retrouvées.

SQLite ne sait pas retirer une contrainte UNIQUE sans nom : la table est
recréée (batch), avec une convention de nommage qui permet de la désigner.

Deux (artist_id, genius_id) étaient portés par deux lignes (mesuré le
2026-09-24) et bloqueraient l'index. Règle hors réseau :
  · même `genius_url` sur toutes les lignes ⇒ vrai doublon (Josman « BOSS » /
    « Boss ») : l'ID reste sur la plus ancienne, le doublon reste signalé et se
    fusionne à la main ;
  · URL différentes (Django « Brouillard (extrait) » portait l'ID de « Locke »)
    ⇒ impossible de dire à qui il appartient : il est retiré de TOUTES. Le
    prochain run discographie les réadopte par leur titre (une fiche sans
    genius_id est adoptée par la page Genius au même titre).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e36_tracks_identite_genius"
down_revision: str | Sequence[str] | None = "e35_relations_suffixe_nature"
branch_labels = None
depends_on = None

_CONVENTION = {"uq": "uq_%(table_name)s_%(column_0_name)s"}


def _lever_les_doublons_genius(conn) -> None:
    groupes = conn.execute(
        sa.text(
            "SELECT artist_id, genius_id FROM tracks WHERE genius_id IS NOT NULL "
            "GROUP BY artist_id, genius_id HAVING COUNT(*) > 1"
        )
    ).fetchall()
    for artist_id, genius_id in groupes:
        lignes = conn.execute(
            sa.text(
                "SELECT id, genius_url FROM tracks "
                "WHERE artist_id = :a AND genius_id = :g ORDER BY id"
            ),
            {"a": artist_id, "g": genius_id},
        ).fetchall()
        if len({url for _, url in lignes}) == 1:
            a_vider = [tid for tid, _ in lignes[1:]]
        else:
            a_vider = [tid for tid, _ in lignes]
        for tid in a_vider:
            conn.execute(sa.text("UPDATE tracks SET genius_id = NULL WHERE id = :id"), {"id": tid})


def upgrade() -> None:
    conn = op.get_bind()
    _lever_les_doublons_genius(conn)
    with op.batch_alter_table(
        "tracks", recreate="always", naming_convention=_CONVENTION
    ) as batch_op:
        batch_op.drop_constraint("uq_tracks_title", type_="unique")
    op.create_index(
        "ux_tracks_artist_genius",
        "tracks",
        ["artist_id", "genius_id"],
        unique=True,
        sqlite_where=sa.text("genius_id IS NOT NULL"),
    )
    op.create_index(
        "ux_tracks_titre_sans_genius",
        "tracks",
        ["artist_id", "title"],
        unique=True,
        sqlite_where=sa.text("genius_id IS NULL"),
    )


def downgrade() -> None:
    # Retour impossible sans perte dès que des homonymes existent : la contrainte
    # par titre les refuserait. On ne recrée que les index.
    op.drop_index("ux_tracks_titre_sans_genius", table_name="tracks")
    op.drop_index("ux_tracks_artist_genius", table_name="tracks")
