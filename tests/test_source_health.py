"""Tests du cœur santé des sources — sans réseau (requests monkeypatché)."""

import requests

from src.utils import source_health as sh
from src.utils.source_health import (
    SOURCES,
    SourceSpec,
    check_all,
    check_fast,
    load_health,
    save_health,
)


# ── Cohérence de la déclaration ────────────────────────────────────────────────
def test_sources_coherentes():
    keys = [s.key for s in SOURCES]
    assert len(keys) == len(set(keys)), "clés de source dupliquées"
    for spec in SOURCES:
        assert spec.label
        # Chaque source est sondable en rapide (URL ou callable)
        assert spec.fast_url or spec.fast_probe, spec.key


# ── check_fast : GET par défaut ────────────────────────────────────────────────
class _FakeResponse:
    def __init__(self, status_code=200, text="OK Spotify"):
        self.status_code = status_code
        self.text = text


def _spec():
    return SourceSpec(key="demo", label="Demo", fast_url="https://example.test", fast_marker="OK")


def test_fast_ok(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(200, "OK content"))
    st = check_fast(_spec())
    assert st.status == "ok"
    assert st.level == "fast"
    assert st.last_ok is not None
    assert st.latency_ms is not None


def test_fast_marqueur_absent(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(200, "rien d'attendu"))
    st = check_fast(_spec())
    assert st.status == "degraded"
    assert "absent" in st.message


def test_fast_timeout_est_broken(monkeypatch):
    def boom(*a, **k):
        raise requests.Timeout("timed out")

    monkeypatch.setattr(requests, "get", boom)
    st = check_fast(_spec())
    assert st.status == "broken"
    assert st.last_ok is None


def test_fast_403_tolere_est_degraded(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(403, ""))
    spec = SourceSpec(key="cf", label="CF", fast_url="https://cf.test", tolerate_403=True)
    st = check_fast(spec)
    assert st.status == "degraded"
    assert "403" in st.message


def test_fast_403_non_tolere_est_broken(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(403, ""))
    spec = SourceSpec(key="x", label="X", fast_url="https://x.test")
    st = check_fast(spec)
    assert st.status == "broken"


# ── fast_probe / ProbeSkipped ──────────────────────────────────────────────────
def test_probe_skipped_est_unknown():
    def skip():
        raise sh.ProbeSkipped("clé absente")

    spec = SourceSpec(key="k", label="K", fast_probe=skip)
    st = check_fast(spec)
    assert st.status == "unknown"
    assert st.message == "clé absente"


def test_probe_anomalies_est_broken():
    spec = SourceSpec(key="k", label="K", fast_probe=lambda: ["0 entrée"])
    st = check_fast(spec)
    assert st.status == "broken"
    assert "0 entrée" in st.message


# ── check_all : arrêt coopératif ───────────────────────────────────────────────
def test_check_all_should_stop(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(200, "OK Spotify"))
    results = check_all(should_stop=lambda: True)
    assert results == []


def test_check_all_progress_cb(monkeypatch):
    monkeypatch.setattr(
        requests, "get", lambda *a, **k: _FakeResponse(200, "duration syncedLyrics")
    )
    monkeypatch.setattr(sh.time, "sleep", lambda *_: None)  # pas d'attente en test
    seen = []
    results = check_all(only=["kworb", "deezer"], progress_cb=seen.append)
    assert len(results) == 2
    assert [s.key for s in seen] == ["kworb", "deezer"]


# ── Persistance ────────────────────────────────────────────────────────────────
def test_save_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(sh, "HEALTH_FILE", tmp_path / "health.json")
    st = sh.SourceStatus(
        "kworb", "Kworb", "ok", "fast", 42, "2026-07-11T10:00:00", "2026-07-11T10:00:00", "OK"
    )
    save_health([st])
    loaded = load_health()
    assert loaded["kworb"]["status"] == "ok"
    assert loaded["kworb"]["latency_ms"] == 42


def test_save_preserve_last_ok(tmp_path, monkeypatch):
    """Une source qui casse garde son dernier last_ok connu."""
    monkeypatch.setattr(sh, "HEALTH_FILE", tmp_path / "health.json")
    save_health([sh.SourceStatus("kworb", "Kworb", "ok", "fast", 10, "t1", "t1", "OK")])
    save_health([sh.SourceStatus("kworb", "Kworb", "broken", "fast", None, "t2", None, "HTTP 500")])
    loaded = load_health()
    assert loaded["kworb"]["status"] == "broken"
    assert loaded["kworb"]["last_ok"] == "t1"  # préservé


def test_save_merge_preserve_autres(tmp_path, monkeypatch):
    """Sonder une seule source n'efface pas les autres."""
    monkeypatch.setattr(sh, "HEALTH_FILE", tmp_path / "health.json")
    save_health([sh.SourceStatus("kworb", "Kworb", "ok", "fast", 10, "t1", "t1", "OK")])
    save_health([sh.SourceStatus("deezer", "Deezer", "ok", "fast", 20, "t2", "t2", "OK")])
    loaded = load_health()
    assert set(loaded) == {"kworb", "deezer"}
