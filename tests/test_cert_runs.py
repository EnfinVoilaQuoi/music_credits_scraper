"""Registre des runs de certifs par artiste (`cert_runs`, 2026-11-16)."""

from datetime import datetime

from src.services import certifs
from src.utils import cert_runs


class _Lanceur:
    def __init__(self, codes=None):
        self.codes = codes or {}

    def __call__(self, cmd, tag, env=None, progres=None):
        return self.codes.get(tag.split(" ")[0], 0), f"sortie {tag}"


def _isoler(monkeypatch, tmp_path):
    monkeypatch.setattr("src.enrichment.cert_source._CERT_DIR", tmp_path)
    monkeypatch.setattr("src.utils.cert_artist.certifications", lambda noms: [])
    monkeypatch.setattr("src.utils.cert_matcher.reset_cert_matcher", lambda: None)


def test_seules_les_sources_abouties_sont_notees(monkeypatch, tmp_path):
    _isoler(monkeypatch, tmp_path)
    certifs.rechercher_artiste(["IAM"], artiste="IAM", lancer=_Lanceur({"BPI": 1}))
    runs = cert_runs.derniers_runs()
    assert set(runs) == {"IAM"}
    assert set(runs["IAM"]) == {"SNEP", "RIAA"}


def test_sans_artiste_rien_n_est_note(monkeypatch, tmp_path):
    _isoler(monkeypatch, tmp_path)
    certifs.rechercher_artiste(["IAM"], lancer=_Lanceur())
    assert cert_runs.derniers_runs() == {}


def test_un_nouveau_run_remplace_la_date_et_garde_les_autres(monkeypatch, tmp_path):
    _isoler(monkeypatch, tmp_path)
    cert_runs.noter_run("IAM", "SNEP", datetime(2026, 1, 1))
    cert_runs.noter_run("Isha", "RIAA", datetime(2026, 2, 1))
    cert_runs.noter_run("IAM", "SNEP", datetime(2026, 3, 1))
    assert cert_runs.derniers_runs() == {
        "IAM": {"SNEP": "2026-03-01T00:00:00"},
        "Isha": {"RIAA": "2026-02-01T00:00:00"},
    }


def test_un_registre_illisible_n_est_jamais_reecrit(monkeypatch, tmp_path):
    _isoler(monkeypatch, tmp_path)
    chemin = tmp_path / cert_runs.NOM_FICHIER
    chemin.write_text("{pas du json", encoding="utf-8")
    cert_runs.noter_run("IAM", "SNEP")
    assert chemin.read_text(encoding="utf-8") == "{pas du json"
    assert cert_runs.derniers_runs() == {}
