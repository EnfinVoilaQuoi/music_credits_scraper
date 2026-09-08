"""duration / release_date / isrc rejoignent `observations` — enfin une provenance

Revision ID: e24_discographie_obs
Revises: e23_track_spotify_ids
Create Date: 2026-09-08

Ces trois champs vivaient en colonne NUE : aucune source, aucune date. Le
contraste avec le reste de la base était net — `observations` porte
`(field, value, source, confidence, seen_at)` pour neuf champs et six sources,
`credits` a sa colonne `source`, les streams la leur — et les colonnes les plus
anciennes, elles, ne disaient rien de leur origine.

C'est exactement ce qui a manqué le 2026-09-08 : 99 morceaux portaient une durée
qui avait SUIVI un identifiant Spotify fautif (ReccoBeats s'interroge par le
Track ID et rend la durée du morceau que cet ID désigne), et rien en base ne
pouvait le dire. Le nettoyage a dû se rabattre sur une inférence — « il y avait
une observation ReccoBeats, donc la durée en vient probablement ».

**Aucune colonne n'est droppée** (contrairement à e12 pour l'audio) : la GUI, les
exports, `cert_matcher` et `structure` lisent tous `track.duration`. Triple
écriture, comme pour les paroles et les streams.

Backfill en `source='legacy'` — même convention qu'e10 : la colonne portait déjà
la valeur retenue, l'observation la reprend VERBATIM, et une provenance qu'on ne
connaît pas ne s'invente pas. `legacy` ne sert que SEUL : dès qu'une source
réelle observe le champ, il est écarté du verdict (`_drop_legacy`).

⚠️ **La durée passe par `_clean_duration`**, la coercition PARTAGÉE de la
frontière DB→objet. La colonne est HÉTÉROGÈNE — mesuré au 2026-09-08 :
1 241 valeurs dont **19 sont des chaînes « 2:30 »**, SQLite acceptant n'importe
quel type dans une colonne INTEGER. Sans elle, ces 19 deviendraient des
observations illisibles et le problème serait déplacé au lieu d'être résolu.
Les colonnes elles-mêmes sont normalisées dans le même mouvement.
"""

from datetime import datetime
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op
from src.utils.track_mapper import _clean_duration

# revision identifiers, used by Alembic.
revision: str = "e24_discographie_obs"
down_revision: Union[str, Sequence[str], None] = "e23_track_spotify_ids"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Les trois champs, et comment lire leur colonne. `duration` est la seule à
#: demander une coercition — les deux autres sont du texte verbatim.
CHAMPS = ("duration", "release_date", "isrc")


def upgrade() -> None:
    """Verse les trois colonnes en observations `legacy`, et normalise les durées."""
    conn = op.get_bind()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── Les 19 durées écrites « 2:30 » : la colonne est remise en secondes.
    #    Le même `_clean_duration` que partout ailleurs, jamais une seconde
    #    lecture maison de ce format.
    for track_id, brute in conn.execute(
        sa.text("SELECT id, duration FROM tracks WHERE typeof(duration) = 'text'")
    ).fetchall():
        secondes = _clean_duration(brute)
        if secondes is None:
            # Illisible : on ne devine pas. La colonne garde sa valeur, et
            # l'observation ne sera pas créée non plus — un champ qu'on ne sait
            # pas lire ne doit pas entrer dans le moteur sous une fausse forme.
            continue
        conn.execute(
            sa.text("UPDATE tracks SET duration = :d WHERE id = :tid"),
            {"d": secondes, "tid": track_id},
        )

    # ── Le backfill proprement dit, un champ à la fois.
    for champ in CHAMPS:
        rows = conn.execute(
            sa.text(
                f"SELECT id, {champ} FROM tracks "  # noqa: S608 — nom de colonne interne
                f"WHERE {champ} IS NOT NULL AND {champ} != ''"
            )
        ).fetchall()
        for track_id, valeur in rows:
            if champ == "duration":
                valeur = _clean_duration(valeur)
                if valeur is None:
                    continue
            conn.execute(
                sa.text(
                    "INSERT OR IGNORE INTO observations "
                    "(track_id, field, value, source, confidence, seen_at) "
                    "VALUES (:tid, :field, :value, 'legacy', NULL, :now)"
                ),
                {"tid": track_id, "field": champ, "value": str(valeur), "now": now},
            )


def downgrade() -> None:
    """Retire les observations backfillées. Les colonnes n'ont pas bougé (hors
    normalisation des durées, qui est une CORRECTION et n'est pas défaite)."""
    op.get_bind().execute(
        sa.text(
            "DELETE FROM observations WHERE source = 'legacy' "
            "AND field IN ('duration', 'release_date', 'isrc')"
        )
    )
