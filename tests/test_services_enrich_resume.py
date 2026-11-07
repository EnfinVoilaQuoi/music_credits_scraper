"""Service enrichissement — cas limites de `run_async`, pont sync, résumé.

Complète `test_services_enrichissement.py` : liste vide, `run()` soumet à la
boucle unique et attend, et le texte du résumé (statuts par source, verdict
global, titre tronqué, compteurs, mode force, run incomplet).
"""

import asyncio
from types import SimpleNamespace

from src.models import Artist, Track
from src.services import enrichissement as enr
from src.services.runtime import Hooks
from tests.test_services_enrichissement import _run, _setup


def _fiche(tid):
    t = Track(title=f"T{tid}", artist=Artist(name="A"))
    t.id = tid
    return t


class TestRunAsync:
    def test_sans_morceau_rend_incomplet_sans_teardown(self, monkeypatch):
        rt, _, journal = _setup(monkeypatch)
        bilan = _run(rt, [])
        assert not bilan.complete and "aucun morceau" in bilan.motif and journal == []

    def test_rien_a_traiter_est_complet_sans_teardown(self, monkeypatch):
        """La restriction a tout écarté : rien n'a manqué (2026-11-06)."""
        rt, _, journal = _setup(monkeypatch)
        artist = Artist(name="A")
        artist.tracks = [_fiche(1)]
        bilan = asyncio.run(enr.run_async(rt, artist, [], enr.OptionsEnrich(), Hooks()))
        assert bilan.complete and bilan.motif == "rien à traiter" and journal == []

    def test_fiches_d_un_autre_artiste_restent_un_echec(self, monkeypatch):
        rt, _, _ = _setup(monkeypatch)
        artist = Artist(name="A")
        artist.tracks = [_fiche(1)]
        options = enr.OptionsEnrich(track_ids=frozenset({999}))
        bilan = asyncio.run(enr.run_async(rt, artist, [], options, Hooks()))
        assert not bilan.complete and "aucun morceau" in bilan.motif


class TestPontSync:
    def test_run_soumet_a_la_boucle_et_attend(self, monkeypatch):
        vus = {}

        class _Boucle:
            def start(self):
                vus["start"] = True

            def submit(self, coro):
                vus["coro"] = coro.__name__
                coro.close()
                return SimpleNamespace(result=lambda: "bilan")

        monkeypatch.setattr(enr, "async_loop", _Boucle())
        assert enr.run(None, None, [], enr.OptionsEnrich(), None) == "bilan"
        assert vus == {"start": True, "coro": "run_async"}


class TestStatuts:
    def test_status_char(self):
        assert [enr._status_char(v) for v in ("not_needed", None, True, False)] == [
            "-",
            "?",
            "✓",
            "✗",
        ]

    def test_overall(self):
        assert enr._overall({"a": True, "b": None}) == "✓"
        assert enr._overall({"a": None, "b": False}) == "?"
        assert enr._overall({"a": "not_needed"}) == "-"
        assert enr._overall({"a": False}) == "✗"
        assert enr._overall({}) == "✗"


class TestResume:
    def test_lignes_et_compteurs(self):
        b = enr.BilanEnrich(
            traites=3,
            resultats=[
                {"title": "Court", "results": {"reccobeats": True}},
                {
                    "title": "Un titre vraiment beaucoup trop long pour tenir",
                    "results": {"deezer": False},
                },
                {"title": "Crash", "results": {"songbpm": None}},
                {"title": "Déjà", "results": {"getsongbpm": "not_needed"}},
            ],
        )
        b.interrompu("arrêt")
        txt = enr.resume(b, enr.OptionsEnrich(force_update=True), desactives=2)
        assert "✅ Mode force update activé" in txt
        assert "✅ 1 réussi(s) · ❌ 2 échec(s)" in txt
        assert "✓ Court\n  RC:✓\n" in txt
        assert "✗ Un titre vraiment beaucoup ..." in txt  # 27 chars + "..."
        assert "? Crash\n  SB:?" in txt
        assert "- Déjà\n  GS:-" in txt
        assert "2 morceaux désactivés ignorés" in txt
        assert txt.endswith("⚠️ Run INCOMPLET : arrêt")

    def test_sans_detail_ni_option(self):
        b = enr.BilanEnrich(traites=1, resultats=[{"title": "X", "results": {}}])
        txt = enr.resume(b, enr.OptionsEnrich())
        assert "✗ X\n" in txt and "force update" not in txt
