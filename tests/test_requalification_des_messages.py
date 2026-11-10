"""UN référentiel des messages stockés mal classés (2026-11-10).

L'audit (`echec_mal_classe`, une expression régulière) et la requalification
(`requalifier_anciens_echecs`, du SQL) avaient chacun leur liste. Les deux lisent
désormais `issues.REGLES_DES_MESSAGES` : ce fichier prouve que la requalification
reconnaît exactement ce qu'elle reconnaissait, que le Python et le SQL du
référentiel disent la même chose, et nomme ce que l'ancienne expression de
l'audit voyait EN PLUS (et qu'aucune requalification ne corrigeait).
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing

import pytest

from src.observability.issues import (
    Requalification,
    clause_sql,
    requalification_du_message,
)

#: (source, statut, issue, message) — les messages que l'une ou l'autre des
#: deux anciennes listes reconnaissait, et des témoins qu'aucune ne voyait.
CORPUS = [
    ("ytmusic", None, "crash", "pas de paroles pour cette vidéo"),
    ("ytmusic", None, "crash", "Pas de paroles trouvées"),
    ("ytmusic", None, "crash", "titre non confirmé sur les résultats"),
    ("ytmusic", None, "crash", "aucun résultat"),
    ("ytmusic", None, "crash", "Aucun résultat pour la recherche"),
    ("ytmusic", None, "absent", "aucun résultat"),
    ("lrclib", 200, "crash", "aucune parole pour ce morceau"),
    ("lrclib", 200, "unreachable", "aucune parole pour ce morceau"),
    ("lrclib", 503, "unreachable", "aucune parole pour ce morceau"),
    ("lrclib", 503, "crash", "aucune parole pour ce morceau"),
    ("musixmatch", None, "crash", "aucune parole synchronisée"),
    ("songbpm", 200, "crash", "CancelledError: "),
    ("genius", None, "crash", "CancelledError()"),
    ("songbpm", 200, "timeout", "CancelledError: "),
    ("ytmusic", None, "crash", "aucun resultat"),
    ("ytmusic", None, "crash", "titre non confirme"),
    ("ytmusic", None, "crash", "réponse : pas de paroles"),
    ("deezer", None, "crash", "KeyError: 'data'"),
    ("ytmusic", None, "crash", ""),
    ("ytmusic", None, "crash", None),
]

#: Les clauses SQL de `requalifier_anciens_echecs` avant le 2026-11-10, telles
#: quelles.
ANCIEN_SQL = {
    Requalification.REPONSE: "issue = 'crash' AND (message LIKE 'pas de paroles%' "
    "OR message LIKE '%non confirmé%' OR message LIKE 'aucun résultat%')",
    Requalification.ABSENCE: "source_key = 'lrclib' AND status_code = 200 "
    "AND message LIKE 'aucune parole%'",
    Requalification.DELAI: "issue = 'crash' AND message LIKE 'CancelledError%'",
}

#: L'expression régulière de l'audit avant le 2026-11-10 (lignes `crash`).
ANCIENNE_REGEX = re.compile(
    r"pas de paroles|aucun r[ée]sultat|non confirm[ée]|aucune parole", re.IGNORECASE
)


def _table():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE source_track_failures "
        "(id INTEGER PRIMARY KEY, source_key TEXT, status_code INTEGER, issue TEXT, message TEXT)"
    )
    conn.executemany(
        "INSERT INTO source_track_failures (source_key, status_code, issue, message) "
        "VALUES (?, ?, ?, ?)",
        CORPUS,
    )
    return conn


def _ids(conn, clause, params=()):
    return {
        r[0] for r in conn.execute(f"SELECT id FROM source_track_failures WHERE {clause}", params)
    }


@pytest.mark.parametrize("requalification", list(Requalification))
def test_la_requalification_reconnait_ce_qu_elle_reconnaissait(requalification):
    with closing(_table()) as conn:
        clause, params = clause_sql(requalification)
        assert _ids(conn, clause, params) == _ids(conn, ANCIEN_SQL[requalification])


@pytest.mark.parametrize("requalification", list(Requalification))
def test_le_python_et_le_sql_du_referentiel_disent_la_meme_chose(requalification):
    """Le juge Python de l'audit et la clause SQL de la requalification, sur le
    même corpus, dans un vrai SQLite (son LIKE : casse ASCII seule)."""
    with closing(_table()) as conn:
        clause, params = clause_sql(requalification)
        par_sql = _ids(conn, clause, params)
        par_python = {
            i
            for i, (source, statut, issue, message) in enumerate(CORPUS, start=1)
            if requalification_du_message(message, source=source, statut=statut, issue=issue)
            is requalification
        }
        assert par_python == par_sql


def test_l_audit_ne_voit_plus_que_ce_que_la_requalification_corrige():
    """Une ligne `crash` est mal classée si la requalification la requalifie.
    L'ancienne expression voyait EN PLUS des messages qu'aucune requalification
    ne touchait (un cas d'audit que rien ne règle) : un motif au milieu du
    message, sans accent, une absence LRCLIB sans réponse 200, la musixmatch."""
    avant, apres = set(), set()
    for source, statut, issue, message in CORPUS:
        if issue != "crash":
            continue
        texte = message or ""
        if texte.startswith("CancelledError") or ANCIENNE_REGEX.search(texte):
            avant.add((source, statut, texte))
        if requalification_du_message(message, source=source, statut=statut, issue=issue):
            apres.add((source, statut, texte))
    assert apres <= avant
    assert avant - apres == {
        ("ytmusic", None, "aucun resultat"),
        ("ytmusic", None, "titre non confirme"),
        ("ytmusic", None, "réponse : pas de paroles"),
        ("lrclib", 503, "aucune parole pour ce morceau"),
        ("musixmatch", None, "aucune parole synchronisée"),
    }


def test_la_casse_suit_sqlite():
    """LIKE de SQLite : « Pas » = « pas », mais « É » ≠ « é »."""
    assert requalification_du_message("Pas de paroles") is Requalification.REPONSE
    assert requalification_du_message("titre NON CONFIRMÉ") is None
    assert requalification_du_message("titre non confirmé") is Requalification.REPONSE
    assert requalification_du_message("KeyError: 'data'") is None
