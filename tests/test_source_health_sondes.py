"""Santé des sources — les décisions des sondes MusicBrainz (client remplacé),
`_run_probe` face à une sonde qui lève, et `load_health` sur un fichier cassé.

La sonde Spotify web (patchright) reste hors périmètre : pilotage navigateur.
"""

import time

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


class TestSondeMusixmatch:
    """L'ancienne sonde voyait un 200 sur `token.get`… qui portait le jeton
    leurre : verte sur une source qui ne rendait rien (mesuré 2026-09-23)."""

    class _Resp:
        def __init__(self, env):
            self.env, self.status_code = env, 200

        def raise_for_status(self):
            pass

        def json(self):
            return self.env

    def _repondre(self, monkeypatch, env, appels):
        def get(*a, **k):
            appels.append(k.get("params", {}).get("app_id"))
            return self._Resp(env)

        monkeypatch.setattr(requests, "get", get)

    def _brancher(self, monkeypatch, tmp_path):
        import src.api.musixmatch_api as mxm

        monkeypatch.delenv("MUSIXMATCH_USER_TOKEN", raising=False)
        vrai = mxm.MusixmatchAPI
        client = vrai(token_file=tmp_path / "jeton.json")
        monkeypatch.setattr(mxm, "MusixmatchAPI", lambda: client)
        return client

    def test_vrai_jeton_ok_et_mis_en_cache(self, monkeypatch, tmp_path):
        client = self._brancher(monkeypatch, tmp_path)
        appels = []
        self._repondre(monkeypatch, {"message": {"body": {"user_token": "abc123"}}}, appels)
        assert sh._probe_musixmatch() == []
        assert appels == ["mac-ios-v2.0"]
        assert client._lire_etat()["token"] == "abc123"

    def test_jeton_leurre_est_un_constat(self, monkeypatch, tmp_path):
        self._brancher(monkeypatch, tmp_path)
        self._repondre(monkeypatch, {"message": {"body": {"user_token": "0" * 56}}}, [])
        (constat,) = sh._probe_musixmatch()
        assert "leurre" in constat

    def test_captcha_ne_conclut_pas(self, monkeypatch, tmp_path):
        self._brancher(monkeypatch, tmp_path)
        captcha = {"message": {"header": {"status_code": 401, "hint": "captcha"}}}
        self._repondre(monkeypatch, captcha, [])
        assert sh.check_fast(sh.SOURCES_BY_KEY["musixmatch"]).status == "unknown"

    def test_jeton_en_cache_aucun_appel(self, monkeypatch, tmp_path):
        """Le budget de token.get n'est pas brûlé par la sonde."""
        client = self._brancher(monkeypatch, tmp_path)
        client._save_token("abc123")
        appels = []
        self._repondre(monkeypatch, {}, appels)
        assert sh._probe_musixmatch() == [] and appels == []

    def test_sous_le_plancher_aucun_appel(self, monkeypatch, tmp_path):
        client = self._brancher(monkeypatch, tmp_path)
        client._ecrire_etat(last_token_get=time.time())
        appels = []
        self._repondre(monkeypatch, {}, appels)
        assert sh.check_fast(sh.SOURCES_BY_KEY["musixmatch"]).status == "unknown"
        assert appels == []
