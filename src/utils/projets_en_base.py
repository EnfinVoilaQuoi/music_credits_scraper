"""Écriture des projets (e67) : le regroupement de `projets_disques` posé en base.

Un seul écrivain, partagé par la migration e67 (premier remplissage) et
`TrackRepository.regrouper_projets` (fin des étapes qui écrivent des
parutions, gestes qui en suppriment ou en fusionnent). Idempotent : rejoué,
il ne change rien ; un projet garde son ID tant qu'il garde une de ses
éditions (les données qu'on y rattachera ne bougent pas à chaque passage).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import text

from src.utils.projets_disques import Edition, edition_de_reference, regrouper


@dataclass
class BilanProjets:
    projets: int = 0
    #: Projets à plusieurs éditions.
    reunis: int = 0
    crees: int = 0
    retires: int = 0
    #: Éditions dont le projet a changé.
    deplacees: int = 0
    #: Groupes non formés (édition cumulée), chacun ses ID d'éditions.
    a_trancher: list[list[int]] = field(default_factory=list)


def _editions(conn, artist_id: int) -> list[Edition]:
    rows = conn.execute(
        text(
            "SELECT id, title, record_type, upc, mb_live, release_date FROM releases "
            "WHERE artist_id = :a AND scope = 'own' AND status = 'confirmed'"
        ),
        {"a": artist_id},
    ).all()
    pistes: dict[int, set[int]] = {r[0]: set() for r in rows}
    for rid, tid in conn.execute(
        text(
            "SELECT rt.release_id, rt.track_id FROM release_tracks rt "
            "JOIN releases r ON r.id = rt.release_id WHERE r.artist_id = :a"
        ),
        {"a": artist_id},
    ):
        if rid in pistes:
            pistes[rid].add(tid)
    return [
        Edition(
            id=rid,
            titre=titre or "",
            record_type=rt,
            upc=upc,
            live=None if live is None else bool(live),
            date=str(date)[:10] if date else None,
            pistes=frozenset(pistes[rid]),
        )
        for rid, titre, rt, upc, live, date in rows
    ]


def regrouper_en_base(conn, artist_id: int, *, maintenant: datetime | None = None) -> BilanProjets:
    """Regroupe les éditions `own` confirmées de l'artiste en projets et l'écrit
    dans la transaction de `conn`. Les autres parutions perdent leur projet."""
    maintenant = maintenant or datetime.now()
    editions = _editions(conn, artist_id)
    par_id = {e.id: e for e in editions}
    regroupement = regrouper(editions)
    actuel = dict(
        conn.execute(
            text(
                "SELECT id, projet_id FROM releases WHERE artist_id = :a AND projet_id IS NOT NULL"
            ),
            {"a": artist_id},
        ).all()
    )
    existants = {
        pid
        for (pid,) in conn.execute(
            text("SELECT id FROM projets WHERE artist_id = :a"), {"a": artist_id}
        )
    }
    bilan = BilanProjets(a_trancher=regroupement.a_trancher)
    pris: set[int] = set()
    # Les plus gros groupes choisissent leur ID d'abord : un projet qui se
    # scinde garde son ID pour la part la plus grande.
    for ids in sorted(regroupement.projets, key=lambda g: (-len(g), g)):
        candidats = sorted(
            {actuel[i] for i in ids if actuel.get(i) in existants} - pris,
            key=lambda p: (-sum(1 for i in ids if actuel.get(i) == p), p),
        )
        ref = edition_de_reference([par_id[i] for i in ids])
        titre = par_id[ref].titre
        if candidats:
            pid = candidats[0]
            conn.execute(
                text(
                    "UPDATE projets SET titre = :t, edition_reference_id = :r, updated_at = :n "
                    "WHERE id = :p AND (titre IS NOT :t OR edition_reference_id IS NOT :r)"
                ),
                {"t": titre, "r": ref, "n": maintenant, "p": pid},
            )
        else:
            pid = conn.execute(
                text(
                    "INSERT INTO projets (artist_id, titre, edition_reference_id, created_at, "
                    "updated_at) VALUES (:a, :t, :r, :n, :n)"
                ),
                {"a": artist_id, "t": titre, "r": ref, "n": maintenant},
            ).lastrowid
            bilan.crees += 1
        pris.add(pid)
        bilan.projets += 1
        bilan.reunis += len(ids) > 1
        for i in ids:
            if actuel.get(i) != pid:
                conn.execute(
                    text("UPDATE releases SET projet_id = :p WHERE id = :r"), {"p": pid, "r": i}
                )
                bilan.deplacees += 1
    # Parutions qui ne sont plus des éditions `own` confirmées : sans projet.
    for rid in actuel:
        if rid not in par_id:
            conn.execute(text("UPDATE releases SET projet_id = NULL WHERE id = :r"), {"r": rid})
            bilan.deplacees += 1
    for pid in sorted(existants - pris):
        conn.execute(text("DELETE FROM projets WHERE id = :p"), {"p": pid})
        bilan.retires += 1
    return bilan


def artistes_a_parutions(conn) -> list[int]:
    return [a for (a,) in conn.execute(text("SELECT DISTINCT artist_id FROM releases ORDER BY 1"))]
