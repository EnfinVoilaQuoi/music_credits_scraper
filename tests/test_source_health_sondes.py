"""Santé des sources — les décisions des sondes MusicBrainz (client remplacé),
`_run_probe` face à une sonde qui lève, et `load_health` sur un fichier cassé.

La sonde Spotify web (patchright) reste hors périmètre : pilotage navigateur.
"""

import requests

from src.utils import source_health as sh
from tests.test_source_health import _spec


class _MB:
    def __init__(self, detail=None, resultats=None, leve=None):
        self.detail, self.resultats, self.leve = detail, resultats, leve
        self.closed = False

    def details_artiste(self, mbid, inc=""):
        if self.leve:
            raise self.leve
        return self.detail

    def rechercher_artiste(self, nom, limite=5):
        if self.leve:
            raise self.leve
        return self.resultats

    def close(self):
        self.closed = True


def _brancher(monkeypatch, api):
    monkeypatch.setattr("src.api.musicbrainz_api.MusicBrainzAPI", lambda: api)
    return api


class TestSondeMusicBrainzRapide:
    def test_lookup_qui_leve_est_un_constat_et_le_client_est_ferme(self, monkeypatch):
        api = _brancher(monkeypatch, _MB(leve=RuntimeError("dns")))
        assert sh._probe_musicbrainz_rapide() == ["lookup impossible : dns"] and api.closed

    def test_none_est_un_503_de_cadence(self, monkeypatch):
        _brancher(monkeypatch, _MB(detail=None))
        assert sh._probe_musicbrainz_rapide()[0].startswith("503")

    def test_sans_name_est_un_schema_change(self, monkeypatch):
        _brancher(monkeypatch, _MB(detail={"id": "x"}))
        assert "name" in sh._probe_musicbrainz_rapide()[0]

    def test_ok(self, monkeypatch):
        _brancher(monkeypatch, _MB(detail={"name": "IAM"}))
        assert sh._probe_musicbrainz_rapide() == []


class TestSondeMusicBrainz:
    def test_recherche_qui_leve(self, monkeypatch):
        _brancher(monkeypatch, _MB(leve=RuntimeError("503")))
        assert sh._probe_musicbrainz() == ["recherche impossible : 503"]

    def test_zero_candidat(self, monkeypatch):
        _brancher(monkeypatch, _MB(resultats=[]))
        assert sh._probe_musicbrainz()[0].startswith("0 candidat")

    def test_aucun_exact(self, monkeypatch):
        _brancher(monkeypatch, _MB(resultats=[{"name": "IAM Orchestra"}]))
        assert "exactement" in sh._probe_musicbrainz()[0]

    def test_ok(self, monkeypatch):
        _brancher(monkeypatch, _MB(resultats=[{"name": "IAM"}]))
        assert sh._probe_musicbrainz() == []


class TestRunProbe:
    def test_reseau_et_exception_quelconque_rendent_broken(self):
        def reseau():
            raise requests.ConnectionError("coupé")

        def bug():
            raise KeyError("champ")

        st = sh._run_probe(_spec(), reseau, "full")
        assert st.status == "broken" and st.message.startswith("réseau :")
        st = sh._run_probe(_spec(), bug, "full")
        assert st.status == "broken" and st.message.startswith("erreur :")

    def test_sonde_sautee_rend_unknown(self):
        def saute():
            raise sh.ProbeSkipped("clé absente")

        st = sh._run_probe(_spec(), saute, "full")
        assert st.status == "unknown" and st.message == "clé absente"


class TestLoadHealth:
    def test_fichier_illisible_rend_vide(self, monkeypatch, tmp_path):
        f = tmp_path / "h.json"
        f.write_text("{pas du json", encoding="utf-8")
        monkeypatch.setattr(sh, "HEALTH_FILE", f)
        assert sh.load_health() == {}
        monkeypatch.setattr(sh, "HEALTH_FILE", tmp_path / "absent.json")
        assert sh.load_health() == {}
