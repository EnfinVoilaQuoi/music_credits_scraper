"""Parutions de DÉCOR pour les tests (2026-11-20).

Un test qui juge le catalogue écrit ses `ReleaseObservation` lui-même. Celui
qui n'a besoin que d'une parution comme décor passe par ici : par la MÊME voie
que la production (`record_release_observations`), sous un identifiant
externe fictif. `ensure_release` / `link_track_to_release`, l'ancienne voie
d'écriture que rien n'appelait hors des tests, ont été retirés.
"""

from sqlalchemy import text

from src.models import ReleaseObservation

#: Source fictive des parutions de décor : chacune y porte un identifiant, si
#: bien qu'aucune n'est jamais ADOPTÉE par une autre observation `decor`
#: (`_adopter_parution_heritee` écarte une parution que la source identifie déjà).
SOURCE = "decor"


def poser_parution(
    dm,
    track_id: int,
    titre: str,
    *,
    source: str = SOURCE,
    ident: str | int | None = None,
    scope: str = "own",
    credited_artist_name: str | None = None,
    **observation,
) -> int:
    """Lie la fiche à la parution que `source` désigne sous `ident` (créée au
    besoin) et rend son id. Sans `ident`, le même titre (même périmètre, même
    artiste crédité) désigne la même parution, comme une saisie à la main."""
    if ident is None:
        ident = f"{titre}|{scope}|{credited_artist_name or ''}"
    dm.record_release_observations(
        track_id,
        [
            ReleaseObservation(
                title=titre,
                source=source,
                external_release_id=ident,
                scope=scope,
                credited_artist_name=credited_artist_name,
                **observation,
            )
        ],
    )
    with dm.engine.connect() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT ri.release_id FROM release_identifiers ri "
                    "JOIN tracks t ON t.artist_id = ri.artist_id "
                    "WHERE t.id = :t AND ri.source = :s AND ri.external_id = :e"
                ),
                {"t": track_id, "s": source, "e": str(ident)},
            ).scalar_one()
        )


def parution_sans_piste(dm, artist_id: int, titre: str, *, scope: str = "own") -> int:
    """Une parution qui ne contient AUCUNE fiche (état réel : 57 en base le
    2026-11-20). La voie vivante ne sait pas l'écrire — une observation lie
    toujours une fiche —, d'où ce SQL brut, réservé à ce cas."""
    with dm.engine.begin() as conn:
        return int(
            conn.execute(
                text(
                    "INSERT INTO releases (artist_id, title, scope, identity_key, "
                    "created_at, updated_at) VALUES (:a, :t, :s, :k, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"a": artist_id, "t": titre, "s": scope, "k": f"{SOURCE}:{scope}:{titre}"},
            ).lastrowid
        )
