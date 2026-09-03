"""Persistance de l'usage des sources, sur une base temporaire réelle."""

from datetime import datetime

import pytest

from src.observability.issues import IssueKind
from src.observability.repository import SourceUsageRepository
from src.observability.source_usage import Verdict
from src.utils.db import Database

_QUAND = datetime(2026, 9, 3, 10, 30, 0)


@pytest.fixture
def repo(tmp_path):
    """Base créée par le vrai bootstrap : les tables doivent y être."""
    db = Database(str(tmp_path / "usage.db"))
    return SourceUsageRepository(db.engine)


def _verdict(source="deezer", issue=IssueKind.OK, **extra):
    return Verdict(
        source_key=source,
        issue=issue,
        flow=extra.get("flow", "enrichment"),
        artist_id=extra.get("artist_id", 1),
        track_id=extra.get("track_id"),
        detail=extra.get("detail", ""),
        status_code=extra.get("status_code"),
        latency_ms=extra.get("latency_ms", 100),
        attempts=extra.get("attempts", 1),
        expected_blocked=extra.get("expected_blocked", 0),
        at=extra.get("at", _QUAND),
    )


# ── Les tables existent après le bootstrap ─────────────────────────────────────
def test_les_tables_sont_creees_par_la_migration(repo):
    assert repo.daily() == []
    assert repo.recent_failures() == []


# ── Upsert : on incrémente, on ne duplique pas ────────────────────────────────
def test_upsert_incremente_au_lieu_de_dupliquer(repo):
    repo.record([_verdict() for _ in range(3)])
    repo.record([_verdict() for _ in range(2)])
    lignes = repo.daily()
    assert len(lignes) == 1, "chaque lot a créé sa propre ligne"
    assert lignes[0]["n_calls"] == 5
    assert lignes[0]["n_attempts"] == 5


def test_upsert_sans_artiste_ne_duplique_pas(repo):
    """LE piège SQLite : dans un index UNIQUE, deux NULL sont DISTINCTS.

    Un `INSERT ... ON CONFLICT` ne verrait jamais le conflit ici et empilerait
    des doublons en silence à chaque usage hors contexte artiste.
    """
    repo.record([_verdict(artist_id=None) for _ in range(4)])
    repo.record([_verdict(artist_id=None) for _ in range(3)])
    lignes = [r for r in repo.daily() if r["artist_id"] is None]
    assert len(lignes) == 1
    assert lignes[0]["n_calls"] == 7


def test_une_ligne_par_nature_de_verdict(repo):
    repo.record(
        [
            _verdict(issue=IssueKind.OK),
            _verdict(issue=IssueKind.ABSENT),
            _verdict(issue=IssueKind.PARSE, detail="sélecteur mort"),
        ]
    )
    natures = {r["issue"] for r in repo.daily()}
    assert natures == {"ok", "absent", "parse"}


def test_une_ligne_par_flux(repo):
    repo.record([_verdict(source="ytmusic", flow="streams")])
    repo.record([_verdict(source="ytmusic", flow="enrichment")])
    flux = {r["flow"]: r["n_calls"] for r in repo.daily()}
    assert flux == {"streams": 1, "enrichment": 1}


def test_les_jours_ne_se_melangent_pas(repo):
    repo.record([_verdict(at=datetime(2026, 9, 1, 8, 0))])
    repo.record([_verdict(at=datetime(2026, 9, 3, 8, 0))])
    assert len(repo.daily()) == 2
    assert len(repo.daily(since_day="2026-09-02")) == 1


# ── Fenêtre glissante des échecs ───────────────────────────────────────────────
def test_seuls_les_echecs_sont_archives(repo):
    repo.record([_verdict(issue=IssueKind.OK), _verdict(issue=IssueKind.ABSENT)])
    assert repo.recent_failures() == []
    repo.record([_verdict(issue=IssueKind.PARSE, detail="0 entrée")])
    echecs = repo.recent_failures()
    assert len(echecs) == 1
    assert echecs[0]["issue"] == "parse"


def test_la_fenetre_garde_les_n_derniers(repo):
    lot = [_verdict(issue=IssueKind.UNREACHABLE, detail=f"essai {i}") for i in range(200)]
    repo.record(lot, keep=50)
    echecs = repo.recent_failures(limit=1000)
    assert len(echecs) == 50
    assert echecs[0]["message"] == "essai 199", "la fenêtre doit garder les PLUS RÉCENTS"


def test_la_fenetre_est_par_source(repo):
    repo.record(
        [_verdict(source="riaa", issue=IssueKind.CRASH) for _ in range(30)]
        + [_verdict(source="brma", issue=IssueKind.CRASH) for _ in range(30)],
        keep=20,
    )
    assert len(repo.recent_failures("riaa", limit=100)) == 20
    assert len(repo.recent_failures("brma", limit=100)) == 20


def test_message_tronque(repo):
    repo.record([_verdict(issue=IssueKind.CRASH, detail="x" * 900)])
    assert len(repo.recent_failures()[0]["message"]) <= 300


# ── Lecture par artiste ────────────────────────────────────────────────────────
def test_lecture_par_artiste_isole(repo):
    repo.record([_verdict(artist_id=1), _verdict(artist_id=2), _verdict(artist_id=2)])
    assert sum(r["n_calls"] for r in repo.daily(artist_id=2)) == 2
    assert repo.artists_touched() == [1, 2]


def test_artistes_touches_par_source(repo):
    repo.record([_verdict(source="kworb", artist_id=5), _verdict(source="deezer", artist_id=9)])
    assert repo.artists_touched("kworb") == [5]


# ── Piège TIMESTAMP double-face ───────────────────────────────────────────────
def test_horodatages_relus_verbatim(repo):
    repo.record([_verdict(issue=IssueKind.TIMEOUT, at=_QUAND)])
    ligne = repo.daily()[0]
    echec = repo.recent_failures()[0]
    assert ligne["last_seen"] == "2026-09-03T10:30:00"
    assert echec["occurred_at"] == "2026-09-03T10:30:00"


# ── Purge ──────────────────────────────────────────────────────────────────────
def test_purge_des_vieux_compteurs(repo):
    repo.record([_verdict(at=datetime(2024, 1, 1, 9, 0))])
    repo.record([_verdict(at=_QUAND)])
    assert repo.purge_older_than("2026-01-01") == 1
    assert len(repo.daily()) == 1


# ── Branchement bout en bout avec le capteur ──────────────────────────────────
def test_le_capteur_alimente_le_repository(repo):
    from src.observability import source_usage as su

    su.reset()
    su.set_sink(repo.record)
    try:
        with su.run_scope("streams", artist_id=12), su.observe("kworb") as obs:
            obs.note_status(200)
    finally:
        su.set_sink(None)
        su.reset()

    lignes = repo.daily(artist_id=12)
    assert len(lignes) == 1
    assert lignes[0]["source_key"] == "kworb"
    assert lignes[0]["flow"] == "streams"
    assert lignes[0]["issue"] == "ok"


def test_un_lot_vide_ne_fait_rien(repo):
    repo.record([])
    assert repo.daily() == []


# ── Écriture hors du fil appelant ─────────────────────────────────────────────
def test_le_writer_ecrit_en_arriere_plan(repo):
    """Le vidage part parfois de la boucle asyncio, qui écrit déjà dans cette
    base : l'écriture des compteurs ne doit jamais s'y bloquer sur un verrou."""
    from src.observability.repository import BackgroundWriter

    writer = BackgroundWriter(repo)
    try:
        writer([_verdict() for _ in range(5)])
        writer.close(timeout=5)
    finally:
        writer.close(timeout=1)  # idempotent
    assert sum(r["n_calls"] for r in repo.daily()) == 5


def test_le_writer_ignore_un_lot_vide(repo):
    from src.observability.repository import BackgroundWriter

    writer = BackgroundWriter(repo)
    writer([])
    writer.close(timeout=5)
    assert repo.daily() == []
