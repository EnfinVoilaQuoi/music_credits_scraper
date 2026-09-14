"""Service enrichissement — cas limites de `run_async`, pont sync, résumé.

Complète `test_services_enrichissement.py` : liste vide, `run()` soumet à la
boucle unique et attend, et le texte du résumé (statuts par source, verdict
global, titre tronqué, compteurs, mode force, nettoyés, run incomplet).
"""

from types import SimpleNamespace

from src.services import enrichissement as enr
from tests.test_services_enrichissement import _run, _setup


class TestRunAsync:
    def test_sans_morceau_rend_incomplet_sans_teardown(self, monkeypatch):
        rt, _, journal = _setup(monkeypatch)
        bilan = _run(rt, [])
        assert not bilan.complete and "aucun morceau" in bilan.motif and journal == []


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
        assert enr._overall({"a": "not_needed", "cleaned": True}) == "-"
        assert enr._overall({"a": False}) == "✗"
        assert enr._overall({}) == "✗"


class TestResume:
    def test_lignes_et_compteurs(self):
        b = enr.BilanEnrich(
            traites=3,
            nettoyes=1,
            resultats=[
                {"title": "Court", "results": {"reccobeats": True, "cleaned": True}},
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
        assert "🗑️ 1 morceau(x) nettoyé(s)" in txt
        assert "✅ 1 réussi(s) · ❌ 2 échec(s)" in txt
        assert "✓ Court\n  RC:✓\n" in txt
        assert "✗ Un titre vraiment beaucoup ..." in txt  # 27 chars + "..."
        assert "? Crash\n  SB:?" in txt
        assert "- Déjà\n  GS:-" in txt
        assert "2 morceaux désactivés ignorés" in txt
        assert txt.endswith("⚠️ Run INCOMPLET : arrêt")

    def test_sans_detail_ni_option(self):
        b = enr.BilanEnrich(traites=1, resultats=[{"title": "X", "results": {"cleaned": False}}])
        txt = enr.resume(b, enr.OptionsEnrich())
        assert "✗ X\n" in txt and "force update" not in txt and "nettoyé" not in txt
