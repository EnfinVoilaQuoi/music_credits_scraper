"""Persistance de l'usage des sources — chemins hors nominal.

Complète `test_source_usage_repository.py` : base indisponible (écritures et
lectures ne lèvent jamais, elles loggent), filtres de `daily`, JOIN artistes,
`attach()` (sink + atexit) et `script_scope` (branché / débranché).
"""

from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from src.observability import repository as rep
from src.observability.issues import IssueKind
from src.observability.repository import SourceUsageRepository
from tests import test_source_usage_repository as _tsur

_verdict = _tsur._verdict
repo = _tsur.repo  # fixture du module voisin, réexposée sous son nom


class _MoteurCasse:
    def connect(self):
        raise SQLAlchemyError("base indisponible")

    def begin(self):
        raise SQLAlchemyError("base indisponible")


class TestBaseIndisponible:
    def test_ecritures_et_lectures_ne_levent_pas(self, caplog):
        r = SourceUsageRepository(_MoteurCasse())
        with caplog.at_level("ERROR"):
            r.record([_verdict()])
            r.push_failures([_verdict(issue=IssueKind.BLOCKED)])
            assert r.daily() == []
        messages = [rec.message for rec in caplog.records]
        assert any("compteurs non enregistrés" in m for m in messages)
        assert any("échecs non archivés" in m for m in messages)
        assert any("lecture impossible" in m for m in messages)

    def test_push_failures_vide_est_un_no_op(self):
        SourceUsageRepository(_MoteurCasse()).push_failures([])  # ne touche pas le moteur


class TestLectures:
    def test_daily_filtre_par_source(self, repo):
        repo.record([_verdict("deezer"), _verdict("genius")])
        assert {r["source_key"] for r in repo.daily()} == {"deezer", "genius"}
        assert [r["source_key"] for r in repo.daily(source_key="genius")] == ["genius"]

    def test_artists_with_usage_joint_les_noms(self, repo):
        with repo.engine.begin() as conn:
            conn.execute(text("INSERT INTO artists (id, name, genius_id) VALUES (1, 'Swing', 10)"))
        repo.record([_verdict(artist_id=1), _verdict(artist_id=None)])
        assert repo.artists_with_usage() == [(1, "Swing")]


class TestIsoformat:
    def test_non_datetime_devient_str(self):
        assert rep._isoformat("2026-09-03") == "2026-09-03"
        assert rep._isoformat(datetime(2026, 9, 3, 10, 30, 5)) == "2026-09-03T10:30:05"


class TestAttach:
    def test_branche_le_sink_et_le_filet_atexit(self, repo, monkeypatch):
        vus = {}
        monkeypatch.setattr(rep.atexit, "register", lambda fn: vus.update(atexit=fn))
        monkeypatch.setattr("src.observability.source_usage.set_sink", lambda s: vus.update(sink=s))
        r = rep.attach(repo.engine)
        assert isinstance(r, SourceUsageRepository) and isinstance(
            vus["sink"], rep.BackgroundWriter
        )
        monkeypatch.setattr("src.observability.source_usage.flush", lambda: [])
        vus["atexit"]()  # flush + close, sans lever


class TestScriptScope:
    def test_branche_pose_le_scope(self, repo, monkeypatch):
        from src.observability import source_usage

        monkeypatch.setattr(rep, "attach", lambda engine: vus.update(engine=engine))
        vus = {}
        monkeypatch.setattr(
            "src.utils.data_manager.DataManager", lambda: type("D", (), {"engine": "E"})()
        )
        scopes = []
        vrai = source_usage.run_scope

        def espion(flow, **k):
            scopes.append(str(flow))
            return vrai(flow, **k)

        monkeypatch.setattr(source_usage, "run_scope", espion)
        with rep.script_scope("certs"):
            pass
        assert vus["engine"] == "E" and scopes == ["certs"]

    def test_data_manager_indisponible_laisse_le_script_tourner(self, monkeypatch, caplog):
        def casse():
            raise RuntimeError("base verrouillée")

        monkeypatch.setattr("src.utils.data_manager.DataManager", casse)
        with caplog.at_level("WARNING"), rep.script_scope("certs"):
            passe = True
        assert passe and any("compteurs d'usage indisponibles" in r.message for r in caplog.records)


@pytest.fixture(autouse=True)
def _sink_propre():
    from src.observability import source_usage

    yield
    source_usage.set_sink(None)
