"""artist_relations : numéro d'homonyme Discogs retiré, alias reclassés par nature

Revision ID: e35_relations_suffixe_nature
Revises: e34_inedit
Create Date: 2026-09-23

Réparation de DONNÉES, aucun changement de schéma. Deux défauts mesurés par
l'audit du 2026-09-23 sur la table des liens d'artiste :

  · **le numéro d'homonyme Discogs restait dans les noms** (`667 (4)` en double
    de `667`, `CFR (2)`, `Moon Man (9)`). `DiscogsClient._liens_de` promettait
    de retirer le suffixe « à la comparaison », ce qui n'arrivait jamais
    (`normalize_name` garde « (4) ») : le lien ne se résolvait vers aucun
    artiste de la base et partait tel quel dans la recherche de certifs. Le
    client le retire désormais à l'entrée ; ici on répare l'existant. Une
    ligne suffixée dont la jumelle (même artiste, même kind, même nom
    normalisé SANS suffixe) existe est SUPPRIMÉE, la survivante prenant le
    meilleur des deux statuts (confirmed > proposed > info > refused) — sinon
    elle est RENOMMÉE ;
  · **des alias non proposables étaient en `proposed`** : la fenêtre rangeait
    les candidats neufs avec les proposés quel que soit leur type, et les
    enregistrait ainsi s'ils étaient laissés tels quels (20 `name_variation`,
    plus des alias MusicBrainz SANS type : « T. Scott », « Travi$ Scot »).
    Ils passent en `info` selon `formations.nature_alias`, la règle unique.
    **Aucune ligne `confirmed` n'est touchée** par ce second geste : une
    confirmation humaine d'un état civil ou d'une graphie est légitime.

Idempotent. Aucune règle réimplémentée : suffixe et nature viennent des
modules que l'application utilise (même pratique qu'e33 avec `cle_album`).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from src.utils.discogs_identity import nom_sans_suffixe
from src.utils.formations import nature_alias
from src.utils.title_matching import normalize_name

revision: str = "e35_relations_suffixe_nature"
down_revision: str | Sequence[str] | None = "e34_inedit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Rang de chaque statut à la fusion : le plus FORT survit.
_RANG = {"confirmed": 3, "proposed": 2, "info": 1, "refused": 0}


def retirer_suffixes(conn) -> dict:
    """Rend `{"supprimees": n, "renommees": n}`. Idempotent."""
    rapport = {"supprimees": 0, "renommees": 0}
    suffixees = conn.execute(
        sa.text(
            "SELECT id, artist_id, related_name, kind, status, formation, confirmed_at "
            "FROM artist_relations ORDER BY id"
        )
    ).mappings()
    suffixees = [
        dict(r) for r in suffixees if nom_sans_suffixe(r["related_name"]) != r["related_name"]
    ]
    for ligne in suffixees:
        propre = nom_sans_suffixe(ligne["related_name"])
        cible = normalize_name(propre)
        jumelle = None
        for autre in conn.execute(
            sa.text(
                "SELECT id, related_name, status, formation, confirmed_at FROM artist_relations "
                "WHERE artist_id = :aid AND kind = :kind AND id != :id ORDER BY id"
            ),
            {"aid": ligne["artist_id"], "kind": ligne["kind"], "id": ligne["id"]},
        ).mappings():
            if cible and normalize_name(nom_sans_suffixe(autre["related_name"])) == cible:
                jumelle = dict(autre)
                break
        if jumelle is None:
            conn.execute(
                sa.text("UPDATE artist_relations SET related_name = :nom WHERE id = :id"),
                {"nom": propre, "id": ligne["id"]},
            )
            rapport["renommees"] += 1
            continue
        if _RANG.get(ligne["status"], 0) > _RANG.get(jumelle["status"], 0):
            conn.execute(
                sa.text(
                    "UPDATE artist_relations SET status = :st, confirmed_at = :quand "
                    "WHERE id = :id"
                ),
                {"st": ligne["status"], "quand": ligne["confirmed_at"], "id": jumelle["id"]},
            )
        if jumelle["formation"] is None and ligne["formation"] is not None:
            conn.execute(
                sa.text("UPDATE artist_relations SET formation = :f WHERE id = :id"),
                {"f": ligne["formation"], "id": jumelle["id"]},
            )
        conn.execute(sa.text("DELETE FROM artist_relations WHERE id = :id"), {"id": ligne["id"]})
        rapport["supprimees"] += 1
    return rapport


def reclasser_alias(conn) -> int:
    """Alias `proposed` dont la nature n'est pas un nom de scène ⇒ `info`."""
    lignes = conn.execute(
        sa.text(
            "SELECT id, detail, source FROM artist_relations "
            "WHERE kind = 'alias' AND status = 'proposed'"
        )
    ).mappings()
    a_reclasser = [r["id"] for r in lignes if nature_alias(r["detail"], r["source"]) != "scene"]
    for rid in a_reclasser:
        conn.execute(
            sa.text("UPDATE artist_relations SET status = 'info' WHERE id = :id"), {"id": rid}
        )
    return len(a_reclasser)


def upgrade() -> None:
    conn = op.get_bind()
    retirer_suffixes(conn)
    reclasser_alias(conn)


def downgrade() -> None:
    """Réparation de données : rien à défaire (le suffixe n'était pas une information)."""
