"""Écriture des projets (e67) : le regroupement de `projets_disques` posé en base.

Un seul écrivain, partagé par la migration e67 (premier remplissage) et
`TrackRepository.regrouper_projets` (fin des étapes qui écrivent des
parutions, gestes qui en suppriment ou en fusionnent). Idempotent : rejoué,
il ne change rien ; un projet garde son ID tant qu'il garde une de ses
éditions (les données qu'on y rattachera ne bougent pas à chaque passage).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache

from sqlalchemy import text

from src.utils.projets_disques import Edition, edition_de_reference, regrouper
from src.utils.title_matching import cle_album as _cle_album

#: `cle_album` relu pour chaque titre d'édition à chaque recherche par titre :
#: mis en cache (le report e68 passait 5 000 lignes sur des centaines de titres).
cle_album = lru_cache(maxsize=65536)(_cle_album)


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
            "SELECT id, title, record_type, upc, mb_live, release_date, scope FROM releases "
            "WHERE artist_id = :a AND status = 'confirmed'"
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
            scope=scope or "own",
        )
        for rid, titre, rt, upc, live, date, scope in rows
    ]


def regrouper_en_base(
    conn, artist_id: int, *, maintenant: datetime | None = None, donnees: bool = True
) -> BilanProjets:
    """Regroupe les éditions confirmées de l'artiste en projets et l'écrit
    dans la transaction de `conn`. Les autres parutions perdent leur projet.

    `donnees` (e68) : un projet qui disparaît lègue sa nature et ses streams à
    celui qui reprend ses éditions, puis la nature de chaque projet se recalcule.
    Faux pour la migration e67, où ces colonnes n'existent pas encore."""
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
    # Parutions qui ne sont plus confirmées : sans projet.
    for rid in actuel:
        if rid not in par_id:
            conn.execute(text("UPDATE releases SET projet_id = NULL WHERE id = :r"), {"r": rid})
            bilan.deplacees += 1
    nouveau: dict[int, int] = {}
    for ids in regroupement.projets:
        pid = conn.execute(
            text("SELECT projet_id FROM releases WHERE id = :r"), {"r": ids[0]}
        ).scalar()
        nouveau.update(dict.fromkeys(ids, pid))
    for pid in sorted(existants - pris):
        if donnees:
            heritiers = [nouveau[i] for i, p in actuel.items() if p == pid and i in nouveau]
            if heritiers:
                _leguer(conn, pid, max(set(heritiers), key=heritiers.count))
        conn.execute(text("DELETE FROM projets WHERE id = :p"), {"p": pid})
        bilan.retires += 1
    if donnees:
        for pid in sorted(pris):
            recalculer_nature(conn, pid, maintenant=maintenant)
    return bilan


def artistes_a_parutions(conn) -> list[int]:
    return [a for (a,) in conn.execute(text("SELECT DISTINCT artist_id FROM releases ORDER BY 1"))]


_DONNEES_DU_PROJET = (
    "spotify_streams",
    "spotify_daily_streams",
    "spotify_streams_updated",
    "spotify_streams_source",
    "spotify_album_ids",
    "ytm_streams",
    "ytm_streams_updated",
)


def _leguer(conn, de: int, a: int) -> None:
    """Ce que portait un projet disparu passe à celui qui reprend ses éditions,
    sans rien écraser ; une nature MANUELLE passe avant une nature déduite."""
    conn.execute(
        text(
            "UPDATE projets SET "
            + ", ".join(
                f"{c} = COALESCE({c}, (SELECT {c} FROM projets WHERE id = :de))"
                for c in _DONNEES_DU_PROJET
            )
            + " WHERE id = :a"
        ),
        {"de": de, "a": a},
    )
    conn.execute(
        text(
            "UPDATE projets SET "
            "nature = (SELECT nature FROM projets WHERE id = :de), "
            "nature_source = 'manual', "
            "nature_maj = (SELECT nature_maj FROM projets WHERE id = :de) "
            "WHERE id = :a AND nature_source IS NOT 'manual' "
            "AND (SELECT nature_source FROM projets WHERE id = :de) = 'manual'"
        ),
        {"de": de, "a": a},
    )


# ── Nature (e68) ──────────────────────────────────────────────────────────────


def recalculer_nature(conn, projet_id: int, *, maintenant: datetime | None = None) -> None:
    """La nature du projet : une saisie MANUELLE ne bouge pas ; sinon celle
    que Deezer déclare pour l'édition de référence, puis pour les autres
    éditions à ID Deezer (plus anciennes d'abord), puis toute nature connue."""
    ligne = conn.execute(
        text("SELECT nature, nature_source, edition_reference_id FROM projets WHERE id = :p"),
        {"p": projet_id},
    ).first()
    if ligne is None or ligne[1] == "manual":
        return
    trouvee = conn.execute(
        text(
            "SELECT record_type, deezer_album_id FROM releases WHERE projet_id = :p "
            "AND record_type IS NOT NULL "
            "ORDER BY deezer_album_id IS NULL, id <> :ref, release_date IS NULL, "
            "release_date, id"
        ),
        {"p": projet_id, "ref": ligne[2]},
    ).first()
    nature = trouvee[0] if trouvee else None
    # `deezer` : déclarée par Deezer pour une édition ; `parution` : lue
    # ailleurs (Genius, Apple…) sur une édition sans ID Deezer.
    source = None if nature is None else ("deezer" if trouvee[1] is not None else "parution")
    if nature != ligne[0] or source != ligne[1]:
        conn.execute(
            text(
                "UPDATE projets SET nature = :n, nature_source = :s, nature_maj = :q WHERE id = :p"
            ),
            {
                "n": nature,
                "s": source,
                "q": maintenant or datetime.now(),
                "p": projet_id,
            },
        )


def poser_nature_edition(conn, artist_id: int, deezer_album_id: int, nature: str | None) -> int:
    """La nature que Deezer déclare pour un de ses albums : posée sur les
    éditions de l'artiste qui portent cet ID, puis leur projet se recalcule.
    Rend le nombre d'éditions touchées."""
    projets = {
        p
        for (p,) in conn.execute(
            text(
                "SELECT projet_id FROM releases WHERE artist_id = :a AND deezer_album_id = :d "
                "AND projet_id IS NOT NULL"
            ),
            {"a": artist_id, "d": deezer_album_id},
        )
    }
    n = conn.execute(
        text(
            "UPDATE releases SET record_type = :n WHERE artist_id = :a AND deezer_album_id = :d "
            "AND record_type IS NOT :n"
        ),
        {"a": artist_id, "d": deezer_album_id, "n": nature},
    ).rowcount
    for p in projets:
        recalculer_nature(conn, p)
    return n


def poser_nature_manuelle(conn, projet_id: int, nature: str | None) -> None:
    """Saisie humaine : elle fait foi ; `None` l'efface et rend la main à Deezer."""
    conn.execute(
        text("UPDATE projets SET nature = :n, nature_source = :s, nature_maj = :q WHERE id = :p"),
        {"n": nature, "s": "manual" if nature else None, "q": datetime.now(), "p": projet_id},
    )
    if nature is None:
        recalculer_nature(conn, projet_id)


# ── Le projet d'un disque nommé par une source (e68) ─────────────────────────


def projet_de(
    conn,
    artist_id: int,
    *,
    deezer_album_id: int | None = None,
    spotify_album_ids: str | None = None,
    titre: str | None = None,
) -> int | None:
    """Le projet que désigne une source : par l'ID Deezer, puis par un ID
    d'album Spotify (`release_identifiers`), puis par le TITRE d'une de ses
    éditions (`cle_album`) quand il ne désigne qu'UN projet. Sinon None :
    jamais un projet au hasard."""
    if deezer_album_id is not None:
        p = {
            r
            for (r,) in conn.execute(
                text(
                    "SELECT projet_id FROM releases WHERE artist_id = :a "
                    "AND deezer_album_id = :d AND projet_id IS NOT NULL"
                ),
                {"a": artist_id, "d": deezer_album_id},
            )
        }
        if len(p) == 1:
            return p.pop()
    ids = [i.strip() for i in (spotify_album_ids or "").split(",") if i.strip()]
    if ids:
        p = set()
        for i in ids:
            p |= {
                r
                for (r,) in conn.execute(
                    text(
                        "SELECT r.projet_id FROM release_identifiers ri "
                        "JOIN releases r ON r.id = ri.release_id "
                        "WHERE ri.artist_id = :a AND ri.source = 'spotify_web' "
                        "AND ri.external_id = :i AND r.projet_id IS NOT NULL"
                    ),
                    {"a": artist_id, "i": i},
                )
            }
        if len(p) == 1:
            return p.pop()
    if titre:
        cle = cle_album(titre)
        p = {
            pid
            for pid, t in conn.execute(
                text(
                    "SELECT projet_id, title FROM releases WHERE artist_id = :a "
                    "AND projet_id IS NOT NULL"
                ),
                {"a": artist_id},
            )
            if cle_album(t) == cle
        }
        if len(p) == 1:
            return p.pop()
    return None


# ── Streams (e68) ─────────────────────────────────────────────────────────────


def _fusion_ids(ancien: str | None, nouveau: str | None) -> str | None:
    vus: dict[str, None] = {}
    for ids in (ancien, nouveau):
        for i in (ids or "").split(","):
            if i.strip():
                vus.setdefault(i.strip(), None)
    return ",".join(vus) or None


def poser_streams_spotify(
    conn,
    projet_id: int,
    *,
    streams: int | None,
    daily_streams: int | None,
    spotify_album_ids: str | None,
    updated_at,
    source: str,
    editions_json: str | None = None,
) -> None:
    """Le total Spotify du disque, aux arbitrages d'`upsert_album` : un total
    Kworb n'écrase jamais un total `spotify_web` (Kworb ne somme que les
    morceaux de l'artiste) ; les ID d'édition fusionnent toujours ; un
    quotidien absent n'efface pas celui de Kworb. Le total de CHAQUE édition
    (`editions_json`, {ID d'album Spotify: total}) va sur l'édition."""
    ex = conn.execute(
        text("SELECT spotify_streams_source, spotify_album_ids FROM projets WHERE id = :p"),
        {"p": projet_id},
    ).first()
    if ex is None:
        return
    quand = updated_at or datetime.now()
    valeurs = {"spotify_album_ids": _fusion_ids(ex[1], spotify_album_ids)}
    if not (ex[0] == "spotify_web" and source != "spotify_web"):
        valeurs.update(
            spotify_streams=streams, spotify_streams_updated=quand, spotify_streams_source=source
        )
        if daily_streams is not None:
            valeurs["spotify_daily_streams"] = daily_streams
    conn.execute(
        text("UPDATE projets SET " + ", ".join(f"{c} = :{c}" for c in valeurs) + " WHERE id = :p"),
        {**valeurs, "p": projet_id},
    )
    if not editions_json or source != "spotify_web":
        return
    try:
        par_edition = json.loads(editions_json)
    except ValueError:
        return
    for album_id, total in (par_edition or {}).items():
        conn.execute(
            text(
                "UPDATE releases SET spotify_streams = :s, spotify_streams_updated = :q "
                "WHERE projet_id = :p AND id IN (SELECT release_id FROM release_identifiers "
                "WHERE source = 'spotify_web' AND external_id = :i)"
            ),
            {"s": total, "q": quand, "p": projet_id, "i": str(album_id)},
        )


def poser_streams_ytm(
    conn, projet_id: int, streams: int | None, updated_at=None, *, titre: str | None = None
) -> None:
    """Le total YouTube Music d'un album, rangé sur l'ÉDITION que nomme le titre
    (`cle_album`) ; le projet prend le PLUS GRAND de ses éditions. YTM compte
    chaque album à part : additionner deux éditions compterait deux fois leurs
    titres communs, et garder le dernier écrit faisait osciller le total (163
    projets à plusieurs totaux YTM, mesuré le 2026-11-20)."""
    quand = updated_at or datetime.now()
    if titre:
        cle = cle_album(titre)
        for rid, t in conn.execute(
            text("SELECT id, title FROM releases WHERE projet_id = :p"), {"p": projet_id}
        ).all():
            if cle_album(t) == cle:
                conn.execute(
                    text(
                        "UPDATE releases SET ytm_streams = :s, ytm_streams_updated = :q "
                        "WHERE id = :r"
                    ),
                    {"s": streams, "q": quand, "r": rid},
                )
    plus_grand = conn.execute(
        text(
            "SELECT ytm_streams, ytm_streams_updated FROM releases WHERE projet_id = :p "
            "AND ytm_streams IS NOT NULL ORDER BY ytm_streams DESC LIMIT 1"
        ),
        {"p": projet_id},
    ).first()
    if plus_grand is not None:
        streams, quand = plus_grand
    conn.execute(
        text("UPDATE projets SET ytm_streams = :s, ytm_streams_updated = :q WHERE id = :p"),
        {"s": streams, "q": quand, "p": projet_id},
    )
