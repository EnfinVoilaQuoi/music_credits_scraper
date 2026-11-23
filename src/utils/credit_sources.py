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

from collections.abc import Iterable

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
        elif source in c.confirmations:
            c.confirmations = c.confirmations - {source}
        out.append(c)
    return out


def retirer_source_en_base(conn, track_id: int, source: str) -> int:
    """Côté base, le pendant de `retirer_source` pour une ligne : rend le
    nombre de lignes effacées (celles que seule `source` donnait)."""
    from sqlalchemy import text

    lignes = conn.execute(
        text(
            "SELECT id, source, confirme_par FROM credits WHERE track_id = :t "
            "AND (source = :s OR confirme_par IS NOT NULL)"
        ),
        {"t": track_id, "s": source},
    ).all()
    effaces = 0
    for cid, src, conf in lignes:
        autres = lire(conf)
        if src == source:
            reste = autres - {source}
            if not reste:
                conn.execute(text("DELETE FROM credits WHERE id = :i"), {"i": cid})
                effaces += 1
                continue
            nouvelle = promue(reste)
            conn.execute(
                text("UPDATE credits SET source = :s, confirme_par = :c WHERE id = :i"),
                {"s": nouvelle, "c": ecrire(reste, nouvelle), "i": cid},
            )
        elif source in autres:
            conn.execute(
                text("UPDATE credits SET confirme_par = :c WHERE id = :i"),
                {"c": ecrire(autres - {source}, src), "i": cid},
            )
    return effaces


def unir_confirmations_en_base(conn, source_id: int, cible_id: int) -> int:
    """Chaque crédit de `cible_id` reçoit les sources du MÊME crédit (même clé
    de table) chez `source_id` — ligne sœur, ou fiche absorbée par une fusion.
    UNION, jamais retrait ; un héritage ne confirme rien. Rend le nombre de
    lignes changées."""
    from sqlalchemy import text

    paires = conn.execute(
        text(
            "SELECT d.id, d.source, d.confirme_par, c.source, c.confirme_par "
            "FROM credits c JOIN credits d ON d.track_id = :cible AND d.name = c.name "
            "  AND d.role = c.role AND IFNULL(d.role_detail, '') = IFNULL(c.role_detail, '') "
            "WHERE c.track_id = :source"
        ),
        {"source": source_id, "cible": cible_id},
    ).all()
    changees = 0
    for did, dsrc, dconf, csrc, cconf in paires:
        avant = lire(dconf)
        venues = {s for s in ({csrc} | lire(cconf)) if s and not _est_heritage(s)}
        if _est_heritage(dsrc) and venues:
            # Un héritage cède la place à la source directe (`Track.add_credit`).
            nouvelle = promue(venues)
            conn.execute(
                text("UPDATE credits SET source = :s, confirme_par = :c WHERE id = :i"),
                {"s": nouvelle, "c": ecrire(venues, nouvelle), "i": did},
            )
            changees += 1
            continue
        apres = (avant | venues) - {dsrc}
        if apres != avant:
            conn.execute(
                text("UPDATE credits SET confirme_par = :c WHERE id = :i"),
                {"c": ecrire(apres, dsrc), "i": did},
            )
            changees += 1
    return changees


def _est_heritage(source: str | None) -> bool:
    from src.utils.version_heritage import est_herite

    return est_herite(source)
