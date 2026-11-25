"""Les sources d'un crédit : la sienne (`credits.source`) et celles qui le
CONFIRMENT (`credits.confirme_par`, e70, 2026-11-23).

La table `credits` n'a qu'une ligne par clé `(track_id, name, role,
role_detail)` : quand deux sources (page Genius, API Genius, Discogs, Deezer,
descriptions YouTube) donnaient le même crédit, la seconde était jetée et sa
confirmation perdue — le panneau ne voyait qu'1 crédit 🎫 confirmé sur 189.
Une COLONNE plutôt qu'une table `credit_sources(credit_id, …)` : la
confirmation vit et meurt avec sa ligne (les dix écrivains qui effacent des
crédits, `delete_track`, `delete_artist` n'ont rien à purger, aucun orphelin
possible, `merge_tracks` la déplace avec la ligne) ; aucun lecteur n'a besoin
de la date de chaque source.

Forme en base : les sources triées, séparées par des virgules, sans celle de
la ligne ; NULL quand il n'y en a pas. Un héritage n'est jamais une
confirmation (`Credit.confirmer_par`).

Une source RELUE (page Genius, disque Discogs, réponse de l'API) remplace ce
qu'elle disait : elle se RETIRE des crédits (`retirer_source`,
`retirer_source_en_base`) au lieu d'effacer les lignes dont elle est la source
— un crédit qu'une autre source confirme reste, porté par elle.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping

#: Ordre de promotion quand la source d'une ligne se retire et qu'une
#: confirmation prend sa place : la page Genius d'abord (c'est elle que
#: l'étape crédits relit), puis les sources indépendantes, l'API en dernier
#: (un crédit 🎫 est « provisoire »).
ORDRE_DE_PROMOTION = (
    "genius",
    "discogs",
    "deezer",
    "youtube_topic",
    "youtube_clip",
    "genius_api",
)


def lire(valeur: str | None) -> frozenset[str]:
    """`credits.confirme_par` → ensemble de sources."""
    if not valeur:
        return frozenset()
    return frozenset(s.strip() for s in str(valeur).split(",") if s.strip())


def ecrire(confirmations: Iterable[str], source: str | None) -> str | None:
    """Ensemble de sources → `credits.confirme_par` (sans `source`, NULL si vide)."""
    autres = sorted({s for s in confirmations if s and s != source})
    return ",".join(autres) or None


def lire_graphies(valeur: str | None) -> dict[str, str]:
    """`credits.graphies` (e71) → `{source: nom}`."""
    return dict(json.loads(valeur)) if valeur else {}


def ecrire_graphies(graphies: Mapping[str, str], sources: Iterable[str], nom: str) -> str | None:
    """`{source: nom}` → `credits.graphies` : les seules sources du crédit, aux
    noms qui diffèrent du sien ; NULL si vide, une seule forme (clés triées)."""
    sources = set(sources)
    gardees = {s: n for s, n in graphies.items() if s in sources and n != nom}
    return json.dumps(gardees, ensure_ascii=False, sort_keys=True) if gardees else None


def promue(sources: Iterable[str]) -> str:
    """La source qui prend la place d'une source retirée."""
    sources = set(sources)
    for s in ORDRE_DE_PROMOTION:
        if s in sources:
            return s
    return sorted(sources)[0]


def retirer_source(credits: list, source: str) -> list:
    """PUR. Les crédits sans `source` : retirée des confirmations ; un crédit
    dont elle est la source garde sa ligne si une autre le confirme (promue
    source), sinon il part. Remplace `[c for c in credits if c.source != s]`
    là où la source est relue."""
    out = []
    for c in credits:
        if c.source == source:
            reste = c.confirmations - {source}
            if not reste:
                continue
            c.source = promue(reste)
            c.confirmations = frozenset(reste - {c.source})
            # Le nom est celui de la source qui porte la ligne : celui de la
            # source partie ne tient plus.
            noms = {s: c.nom_selon(s) for s in reste}
            c.name = noms[c.source]
            c.graphies = {s: n for s, n in noms.items() if n != c.name}
        elif source in c.confirmations:
            c.confirmations = c.confirmations - {source}
            c.graphies = {s: n for s, n in c.graphies.items() if s != source}
        out.append(c)
    return out


def fondre_graphies(credits: list) -> list:
    """Les crédits d'un morceau, une ligne par personne et rôle
    (`credit_normalize.graphies_a_fondre`) : la graphie Genius, les autres
    sources en confirmation avec la leur (gagnants complétés sur place)."""
    from src.utils.credit_normalize import graphies_a_fondre

    partis: set[int] = set()
    for gagnant, perdants in graphies_a_fondre(credits):
        for p in perdants:
            gagnant.confirmer_par(p)
            gagnant.tracks = gagnant.tracks or (None if _est_heritage(p.source) else p.tracks)
            partis.add(id(p))
    return [c for c in credits if id(c) not in partis]


def lignes_de_credit(rows) -> list[tuple]:
    """Lignes de la table `credits` → `[(id, Credit)]`, rôle laissé tel quel (le
    juge des graphies accepte la chaîne)."""
    from src.models.track import Credit

    return [
        (
            r["id"],
            Credit(
                name=r["name"],
                role=r["role"],
                role_detail=r["role_detail"],
                tracks=r["tracks"],
                source=r["source"],
                confirmations=lire(r["confirme_par"]),
                graphies=lire_graphies(r["graphies"]),
            ),
        )
        for r in rows
    ]


def _lignes_en_base(conn, track_id: int) -> list[tuple]:
    from sqlalchemy import text

    return lignes_de_credit(
        conn.execute(
            text("SELECT * FROM credits WHERE track_id = :t AND name IS NOT NULL ORDER BY id"),
            {"t": track_id},
        ).mappings()
    )


def fondre_graphies_en_base(conn, track_id: int) -> int:
    """Côté base, le pendant de `fondre_graphies` pour une fiche. Rend le nombre
    de lignes fondues (effacées)."""
    lignes = _lignes_en_base(conn, track_id)
    gardes = {id(c) for c in fondre_graphies([c for _i, c in lignes])}
    partis = [i for i, c in lignes if id(c) not in gardes]
    if not partis:
        return 0
    for i in partis:
        _effacer_ligne(conn, i)
    for i, c in lignes:
        if id(c) in gardes and c.graphies:
            _ecrire_ligne(conn, i, c)
    return len(partis)


def _effacer_ligne(conn, credit_id: int) -> None:
    from sqlalchemy import text

    conn.execute(text("DELETE FROM credits WHERE id = :i"), {"i": credit_id})


def _ecrire_ligne(conn, credit_id: int, c) -> None:
    from sqlalchemy import text

    conn.execute(
        text(
            "UPDATE credits SET name = :n, source = :s, tracks = :p, confirme_par = :c, "
            "graphies = :g WHERE id = :i"
        ),
        {
            "n": c.name,
            "s": c.source,
            "p": c.tracks,
            "c": ecrire(c.confirmations, c.source),
            "g": ecrire_graphies(c.graphies, c.sources, c.name),
            "i": credit_id,
        },
    )


def retirer_source_en_base(conn, track_id: int, source: str) -> int:
    """Côté base, le pendant de `retirer_source` pour une ligne : rend le
    nombre de lignes effacées (celles que seule `source` donnait)."""
    lignes = _lignes_en_base(conn, track_id)
    effaces = 0
    for cid, c in lignes:
        if source not in c.sources:
            continue
        nom = c.name
        if not retirer_source([c], source):
            _effacer_ligne(conn, cid)
            effaces += 1
            continue
        if c.name != nom:
            # Le nom que rend la source restante peut déjà tenir une ligne.
            for aid, a in _lignes_en_base(conn, track_id):
                if aid != cid and (a.name, a.role, a.role_detail or "") == (
                    c.name,
                    c.role,
                    c.role_detail or "",
                ):
                    c.confirmer_par(a)
                    _effacer_ligne(conn, aid)
        _ecrire_ligne(conn, cid, c)
    return effaces


def unir_confirmations_en_base(conn, source_id: int, cible_id: int) -> int:
    """Chaque crédit de `cible_id` reçoit les sources du MÊME crédit (même clé
    de table) chez `source_id` — ligne sœur, ou fiche absorbée par une fusion.
    UNION, jamais retrait ; un héritage ne confirme rien. Rend le nombre de
    lignes changées."""

    def par_cle(tid):
        return {
            (c.name, c.role, c.role_detail or ""): (i, c) for i, c in _lignes_en_base(conn, tid)
        }

    venant = par_cle(source_id)
    changees = 0
    for cle, (did, d) in par_cle(cible_id).items():
        if cle not in venant:
            continue
        c = venant[cle][1]
        avant = (d.source, d.confirmations, d.graphies)
        if _est_heritage(d.source) and not _est_heritage(c.source):
            # Un héritage cède la place à la source directe (`Track.add_credit`).
            d.source, d.confirmations, d.graphies = c.source, c.confirmations, c.graphies
        else:
            d.confirmer_par(c)
        if (d.source, d.confirmations, d.graphies) != avant:
            _ecrire_ligne(conn, did, d)
            changees += 1
    return changees


def _est_heritage(source: str | None) -> bool:
    from src.utils.version_heritage import est_herite

    return est_herite(source)
