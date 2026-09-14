"""ReccoBeats — les jumeaux ASYNC, ceux que l'app emprunte réellement.

`test_reccobeats_api.py` ne couvrait que la voie sync ; la voie async
(`get_track_info_async`, `get_track_info_by_isrc_async` et leurs briques)
était à zéro — le symptôme d'asymétrie du 2026-09-05. Mêmes cas, même
cache, mêmes verdicts : chaîne complète, absence mémorisée, statuts
non-200, JSON illisible, panne réseau, crash consigné.
"""

import asyncio

import httpx
import pytest

from src.observability import source_usage
from src.observability.issues import IssueKind
from tests import test_reccobeats_api as _tra

client = _tra.client  # fixture du module voisin, réexposée sous son nom
_Reponse = _tra._Reponse


class _Http:
    """Sert les réponses dans l'ordre ; mémorise les URL demandées."""

    def __init__(self, *reponses):
        self.reponses = list(reponses)
        self.vues = []

    async def get(self, url, params=None, headers=None, timeout=None):
        self.vues.append((url, params))
        rep = self.reponses.pop(0) if self.reponses else _Reponse({}, statut=500)
        if isinstance(rep, Exception):
            raise rep
        return rep


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _capteur():
    source_usage.reset()
    yield
    source_usage.reset()


_TRACK = {"content": [{"id": "r1", "trackTitle": "T", "durationMs": 228_000}]}
_FEATS = {"tempo": 142.0, "key": 0, "mode": 1}


class TestParSpotifyId:
    def test_chaine_complete_et_cache(self, client):
        http = _Http(_Reponse(_TRACK), _Reponse(_FEATS))
        res = _run(client.get_track_info_async(http, "sp1"))
        assert res["success"] and res["duration"] == 228 and res["bpm"] == 142.0
        assert [u.rsplit("/", 1)[-1] for u, _ in http.vues] == ["track", "audio-features"]
        # Second appel : servi par le cache, aucune requête.
        assert _run(client.get_track_info_async(http, "sp1"))["bpm"] == 142.0
        assert len(http.vues) == 2

    def test_id_inconnu_est_absent_et_memorise(self, client):
        http = _Http(_Reponse({"content": []}))
        assert _run(client.get_track_info_async(http, "sp2")) is None
        assert [v.issue for v in source_usage.flush()] == [IssueKind.ABSENT]
        # L'absence fraîche n'est pas redemandée.
        assert _run(client.get_track_info_async(http, "sp2")) is None
        assert len(http.vues) == 1

    @pytest.mark.parametrize("statut", [404, 429, 500])
    def test_statuts_non_200(self, client, statut):
        rep = _Reponse({}, statut=statut)
        rep.text = "erreur"
        assert _run(client.get_track_from_reccobeats_async(_Http(rep), "sp")) is None

    def test_panne_reseau_et_json_illisible(self, client):
        assert (
            _run(client.get_track_from_reccobeats_async(_Http(httpx.ConnectError("x")), "sp"))
            is None
        )
        assert (
            _run(client.get_track_from_reccobeats_async(_Http(_Reponse(ValueError("json"))), "sp"))
            is None
        )

    def test_audio_features_en_echec_laissent_le_resultat_sans_bpm(self, client):
        http = _Http(_Reponse(_TRACK), _Reponse({}, statut=500))
        res = _run(client.get_track_info_async(http, "sp3"))
        assert res["success"] and "bpm" not in res
        assert (
            _run(client.get_track_audio_features_async(_Http(httpx.ReadTimeout("t")), "r")) is None
        )

    def test_crash_est_consigne(self, client, monkeypatch):
        async def casse(http, sid):
            raise RuntimeError("bug")

        monkeypatch.setattr(client, "get_track_from_reccobeats_async", casse)
        assert _run(client.get_track_info_async(_Http(), "sp4")) is None
        assert [v.issue for v in source_usage.flush()] == [IssueKind.CRASH]


class TestParIsrc:
    def test_pressing_le_plus_populaire_puis_features(self, client):
        http = _Http(
            _Reponse({"content": [{"id": "a", "popularity": 1}, {"id": "b", "popularity": 99}]}),
            _Reponse(_FEATS),
        )
        res = _run(client.get_track_info_by_isrc_async(http, "FR1234500001"))
        assert res["id"] == "b" and res["bpm"] == 142.0 and res["source"] == "reccobeats_isrc"
        assert _run(client.get_track_info_by_isrc_async(http, "FR1234500001"))["bpm"] == 142.0
        assert len(http.vues) == 2  # cache

    def test_isrc_vide_ou_inconnu(self, client):
        assert _run(client.get_track_info_by_isrc_async(_Http(), "")) is None
        http = _Http(_Reponse({"content": []}))
        assert _run(client.get_track_info_by_isrc_async(http, "FR1")) is None
        # Un ISRC inconnu est `absent` — pas un OK par aller-retour (2026-09-15).
        assert [v.issue for v in source_usage.flush()] == [IssueKind.ABSENT]
        assert _run(client.get_track_info_by_isrc_async(http, "FR1")) is None  # absence mémorisée
        assert len(http.vues) == 1

    def test_statut_non_200_reseau_et_json(self, client):
        assert _run(client.get_track_by_isrc_async(_Http(_Reponse({}, statut=429)), "FR")) is None
        assert _run(client.get_track_by_isrc_async(_Http(httpx.ConnectError("x")), "FR")) is None
        assert _run(client.get_track_by_isrc_async(_Http(_Reponse(ValueError("j"))), "FR")) is None

    def test_crash_rend_none(self, client, monkeypatch):
        async def casse(http, isrc):
            raise RuntimeError("bug")

        monkeypatch.setattr(client, "get_track_by_isrc_async", casse)
        assert _run(client.get_track_info_by_isrc_async(_Http(), "FR2")) is None
        assert [v.issue for v in source_usage.flush()] == [IssueKind.CRASH]

    def test_jumeau_sync_meme_verdict_absent(self, client, monkeypatch):
        monkeypatch.setattr(client, "get_track_by_isrc", lambda isrc: None)
        assert client.get_track_info_by_isrc("FR3") is None
        assert [v.issue for v in source_usage.flush()] == [IssueKind.ABSENT]


class TestCycleDeVie:
    def test_context_manager_ferme_la_session(self, client):
        ferme = []
        client.recco_session = type("S", (), {"close": lambda self: ferme.append(True)})()
        with client as c:
            assert c is client
        assert ferme == [True]

    def test_close_ne_leve_jamais(self, client):
        def casse(self):
            raise RuntimeError("déjà fermée")

        client.recco_session = type("S", (), {"close": casse})()
        client.close()

    def test_sauvegarde_du_cache_en_echec_est_loggee(self, client, monkeypatch, caplog):
        def casse(*a, **k):
            raise OSError("disque")

        monkeypatch.setattr("builtins.open", casse)
        with caplog.at_level("ERROR"):
            client._save_cache()
        assert any("Erreur sauvegarde cache" in r.message for r in caplog.records)
