"""credits : colonne `tracks`, pour cesser de loger deux données dans `role_detail`

Revision ID: e18_credit_tracks
Revises: e17_spotify_id_checked
Create Date: 2026-09-05

`role_detail` qualifie le RÔLE (« Guitar », « Piano », le libellé brut quand le
rôle tombe en `OTHER`). Discogs y écrivait en plus la liste des pistes de la
galette auxquelles le crédit s'applique (« 16 », « A1,B3 ») — une information
d'une tout autre nature, sur la même colonne.

Le code d'origine posait `pistes or (libellé si OTHER)` : quand Discogs
fournissait les deux, le libellé disparaissait. Corrigé le 2026-09-04 en
inversant la priorité, ce qui n'a fait que déplacer la perte sur les pistes.
Une colonne pour chacune règle les deux sens à la fois.

**Mesuré le 2026-09-05 sur la base réelle** : 209 lignes portent une référence de
piste dans `role_detail`, toutes `source='discogs'` — Composer 94, Recording
Engineer 50, Producer 40, **Other 21**, Mixing Engineer 4. Le problème était donc
bien plus large que les 21 crédits `Other` signalés au WIP.

Ces 21-là sont les seuls NUISIBLES, et c'est ce qui rend le nettoyage utile :
`Track.get_video_credits` reclasse un `OTHER` en crédit VIDÉO d'après les MOTS de
`role_detail`. Un numéro de piste y est du bruit qui peut déclencher un faux
positif. Pour les 188 autres, la référence était simplement rangée au mauvais
endroit.

Le libellé perdu des 21 n'est PAS récupérable hors ligne (Discogs ne le renvoie
qu'en réseau) : ils repassent à `role_detail = NULL`, ce qui est honnête — mieux
vaut l'absence qu'un numéro de piste qui se fait passer pour un métier. Ils se
rempliront au prochain enrichissement Discogs de ces morceaux.

`tracks` reste HORS de la contrainte d'unicité `(track_id, name, role,
role_detail)` : deux crédits de la même personne, au même rôle, sur le même
morceau sont le même crédit, quelles que soient les pistes de la galette d'où ils
viennent. Conséquence mesurée et assumée : **6 lignes** deviennent homographes
d'une ligne déjà à `role_detail NULL` (même personne créditée au niveau galette
ET au niveau piste). Elles étaient DÉJÀ en double avant cette migration — elles
le restent, sans aggravation, et relèvent du chantier `identity_key` (WIP).
Aucune suppression de ligne ici : une migration de schéma n'efface pas de données.
"""

import re
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e18_credit_tracks"
down_revision: Union[str, Sequence[str], None] = "e17_spotify_id_checked"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: Référence de piste Discogs : « 16 », « A1 », « B3 », « A5,A6,B2 ».
#: Un jeton est soit des chiffres, soit UNE lettre suivie de chiffres — ce qui
#: exclut les vrais libellés courts (« Bass », « Drums »). Sans cette contrainte
#: de chiffre, un premier jet attrapait 2 crédits Genius parfaitement légitimes.
_TRACK_REF = re.compile(r"^\s*[A-Za-z]?\d+(\s*,\s*[A-Za-z]?\d+)*\s*$")


def upgrade() -> None:
    """Ajoute `credits.tracks` et y déplace les références de piste existantes."""
    with op.batch_alter_table("credits") as batch_op:
        batch_op.add_column(sa.Column("tracks", sa.Text(), nullable=True))

    conn = op.get_bind()
    # Restreint à `discogs` : c'est le seul écrivain qui a jamais mis des pistes
    # là. Élargir ferait courir le risque d'exiler un `role_detail` légitime.
    lignes = conn.execute(
        sa.text(
            "SELECT id, role_detail FROM credits "
            "WHERE source = 'discogs' AND role_detail IS NOT NULL"
        )
    ).fetchall()

    a_deplacer = [row_id for row_id, detail in lignes if _TRACK_REF.match(detail or "")]
    for row_id in a_deplacer:
        conn.execute(
            sa.text("UPDATE credits SET tracks = role_detail, role_detail = NULL WHERE id = :i"),
            {"i": row_id},
        )


def downgrade() -> None:
    """Remet les pistes dans `role_detail` là où il est vide, puis retire la colonne."""
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "UPDATE credits SET role_detail = tracks "
            "WHERE tracks IS NOT NULL AND role_detail IS NULL"
        )
    )
    with op.batch_alter_table("credits") as batch_op:
        batch_op.drop_column("tracks")
