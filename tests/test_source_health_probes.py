"""Sondes par source de `source_health` — sans réseau.

`test_source_health.py` couvre le MOTEUR (dispatch, verdicts, persistance) ;
ici ce sont les sondes elles-mêmes. Chacune encode CE QUI COMPTE comme anomalie
pour sa source, sous un contrat simple : `[]` = tout va bien, `[messages]` =
cassé, `ProbeSkipped` = pas mesurable.

C'est le premier endroit qui devient faux quand un site change de structure, et
aucun test ne s'y appliquait. Le distinguo important : une clé d'API absente ou
invalide est un SKIP (`unknown`), jamais un « cassé » — la source n'y est pour
rien, et peindre en rouge ce qu'on n'a pas mesuré est le piège que le projet
s'interdit explicitement.
"""

import pytest
import requests

from src.utils import source_health as sh
from src.utils.source_health import SourceSpec, check_fast


class _Resp:
    def __init__(self, payload=None, status=200):
        self._payload = payload if payload is not None else {}
        self.status_code = status
        self.text = ""

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _get(monkeypatch, resp):
    monkeypatch.setattr(sh.requests, "get", lambda *a, **k: resp)


class TestSondeKworb:
    def _poser(self, monkeypatch, page):
        monkeypatch.setattr(
            "src.scrapers.kworb_scraper.KworbScraper",
            lambda *a, **k: type("S", (), {"scrape_songs": lambda self, i: page})(),
        )

    def test_page_ok(self, monkeypatch):
        self._poser(monkeypatch, {"entries": [{"title": "T"}]})
        assert sh._probe_kworb() == []

    def test_page_absente(self, monkeypatch):
        self._poser(monkeypatch, None)
        assert sh._probe_kworb() == ["page songs introuvable (404 / réseau)"]

    def test_zero_entree_signale_un_changement_de_structure(self, monkeypatch):
        """Une page servie mais vide est le symptôme classique d'un parseur
        cassé — pas d'une source en panne."""
        self._poser(monkeypatch, {"entries": []})
        assert "structure de table" in sh._probe_kworb()[0]


class TestSondeLrclib:
    def _poser(self, monkeypatch, hit):
        monkeypatch.setattr(
            "src.api.lrclib_api.LRCLIBAPI",
            lambda *a, **k: type("A", (), {"get_exact": lambda self, *x: hit})(),
        )

    def test_sentinelle_synchronisee(self, monkeypatch):
        self._poser(monkeypatch, {"lyrics_synced": "[00:01.00]x"})
        assert sh._probe_lrclib() == []

    def test_aucun_resultat(self, monkeypatch):
        self._poser(monkeypatch, None)
        assert sh._probe_lrclib() == ["/get sentinelle sans résultat"]

    def test_resultat_sans_timestamps(self, monkeypatch):
        """LRCLIB est la SOURCE 1 des paroles synchronisées : un résultat sans
        timestamps ne remplit pas son rôle, même si l'API répond."""
        self._poser(monkeypatch, {"lyrics": "texte brut"})
        assert sh._probe_lrclib() == ["résultat sans paroles synchronisées"]


class TestSondeDeezer:
    def _poser(self, monkeypatch, track):
        monkeypatch.setattr(
            "src.api.deezer_api.DeezerAPI",
            lambda *a, **k: type("A", (), {"get_track_by_id": lambda self, i: track})(),
        )

    def test_track_avec_duree(self, monkeypatch):
        self._poser(monkeypatch, {"duration": 240})
        assert sh._probe_deezer() == []

    def test_track_introuvable(self, monkeypatch):
        self._poser(monkeypatch, None)
        assert sh._probe_deezer() == ["track sentinelle introuvable"]

    def test_sans_duree(self, monkeypatch):
        """La durée Deezer est CANONIQUE (elle sert au match LRCLIB) : son
        absence casse la chaîne des paroles synchronisées."""
        self._poser(monkeypatch, {"title": "x"})
        assert "format JSON changé" in sh._probe_deezer()[0]


class TestSondeGetSongBPM:
    def test_cle_absente_est_un_skip(self, monkeypatch):
        """Pas de clé = pas de mesure. `ProbeSkipped` devient `unknown`, surtout
        pas « cassé » : la source n'a rien fait de mal."""
        monkeypatch.setattr(sh, "GETSONGBPM_API_KEY", "")
        with pytest.raises(sh.ProbeSkipped, match="absente"):
            sh._get_getsongbpm_json()

    def test_cle_invalide_est_un_skip(self, monkeypatch):
        monkeypatch.setattr(sh, "GETSONGBPM_API_KEY", "mauvaise")
        _get(monkeypatch, _Resp(status=401))
        with pytest.raises(sh.ProbeSkipped, match="401"):
            sh._get_getsongbpm_json()

    def test_sonde_rapide(self, monkeypatch):
        monkeypatch.setattr(sh, "GETSONGBPM_API_KEY", "ok")
        _get(monkeypatch, _Resp({"search": []}))
        assert sh._probe_getsongbpm_fast() == []

    def test_sonde_complete_ok(self, monkeypatch):
        monkeypatch.setattr(sh, "GETSONGBPM_API_KEY", "ok")
        _get(monkeypatch, _Resp({"search": [{"tempo": "123", "key_of": "F"}]}))
        assert sh._probe_getsongbpm_full() == []

    def test_sonde_complete_sans_resultat(self, monkeypatch):
        monkeypatch.setattr(sh, "GETSONGBPM_API_KEY", "ok")
        _get(monkeypatch, _Resp({"search": []}))
        assert sh._probe_getsongbpm_full() == ["recherche sentinelle sans résultat"]

    def test_resultats_sans_champ_musical(self, monkeypatch):
        """Des hits sans tempo ni tonalité : l'API répond, mais elle ne sert
        plus à rien pour nous."""
        monkeypatch.setattr(sh, "GETSONGBPM_API_KEY", "ok")
        _get(monkeypatch, _Resp({"search": [{"title": "x"}]}))
        assert "aucun objet" in sh._probe_getsongbpm_full()[0]

    def test_erreur_http_propagee(self, monkeypatch):
        """Un 500 n'est PAS un skip : la source est réellement en panne, et
        `_run_probe` doit pouvoir la classer « cassée »."""
        monkeypatch.setattr(sh, "GETSONGBPM_API_KEY", "ok")
        _get(monkeypatch, _Resp(status=500))
        with pytest.raises(requests.HTTPError):
            sh._get_getsongbpm_json()


class TestSondeGenius:
    def test_token_absent_est_un_skip(self, monkeypatch):
        monkeypatch.setattr(sh, "GENIUS_API_KEY", "")
        with pytest.raises(sh.ProbeSkipped, match="absente"):
            sh._genius_api_search()

    def test_token_invalide_est_un_skip(self, monkeypatch):
        monkeypatch.setattr(sh, "GENIUS_API_KEY", "mauvais")
        _get(monkeypatch, _Resp(status=401))
        with pytest.raises(sh.ProbeSkipped, match="401"):
            sh._genius_api_search()

    def test_sonde_rapide(self, monkeypatch):
        monkeypatch.setattr(sh, "GENIUS_API_KEY", "ok")
        _get(monkeypatch, _Resp({}))
        assert sh._probe_genius_api_fast() == []

    def test_sonde_complete_ok(self, monkeypatch):
        monkeypatch.setattr(sh, "GENIUS_API_KEY", "ok")
        _get(monkeypatch, _Resp({"response": {"hits": [{"result": {}}]}}))
        assert sh._probe_genius_api_full() == []

    def test_sonde_complete_sans_hits(self, monkeypatch):
        monkeypatch.setattr(sh, "GENIUS_API_KEY", "ok")
        _get(monkeypatch, _Resp({"response": {"hits": []}}))
        assert "format API changé" in sh._probe_genius_api_full()[0]


class TestSondeYtMusic:
    def _poser(self, monkeypatch, hits):
        monkeypatch.setattr(
            "ytmusicapi.YTMusic",
            lambda *a, **k: type("Y", (), {"search": lambda self, *x, **kw: hits})(),
        )

    def test_resultats_avec_browse_id(self, monkeypatch):
        self._poser(monkeypatch, [{"browseId": "UC1"}])
        assert sh._probe_ytmusic() == []

    def test_aucun_resultat(self, monkeypatch):
        self._poser(monkeypatch, [])
        assert sh._probe_ytmusic() == ["recherche sentinelle sans résultat"]

    def test_resultats_sans_browse_id(self, monkeypatch):
        self._poser(monkeypatch, [{"autre": "chose"}])
        assert "structure changée" in sh._probe_ytmusic()[0]


class TestSondeDiscogs:
    def test_token_absent_est_un_skip(self, monkeypatch):
        monkeypatch.delenv("DISCOGS_USER_TOKEN", raising=False)
        with pytest.raises(sh.ProbeSkipped, match="absent"):
            sh._probe_discogs()

    def test_token_invalide_est_un_skip(self, monkeypatch):
        monkeypatch.setenv("DISCOGS_USER_TOKEN", "mauvais")
        _get(monkeypatch, _Resp(status=401))
        with pytest.raises(sh.ProbeSkipped, match="401"):
            sh._probe_discogs()

    def test_resultats(self, monkeypatch):
        monkeypatch.setenv("DISCOGS_USER_TOKEN", "ok")
        _get(monkeypatch, _Resp({"results": [{"id": 1}]}))
        assert sh._probe_discogs() == []

    def test_aucun_resultat(self, monkeypatch):
        monkeypatch.setenv("DISCOGS_USER_TOKEN", "ok")
        _get(monkeypatch, _Resp({"results": []}))
        assert sh._probe_discogs() == ["recherche sentinelle sans résultat"]


class TestSondeComplete:
    def test_sonde_complete_prioritaire(self):
        spec = SourceSpec(
            key="x", label="X", full_probe=lambda: [], fast_probe=lambda: ["jamais appelée"]
        )
        assert sh.check_full(spec).status == "ok"

    def test_repli_sur_la_rapide_est_annonce(self):
        """Sans sonde complète, on retombe sur la rapide — et on le DIT, sinon
        l'utilisateur croirait le parsing vérifié alors que seule la
        joignabilité l'a été."""
        spec = SourceSpec(key="x", label="X", fast_probe=lambda: [])
        status = sh.check_full(spec)
        assert status.status == "ok"
        assert status.message.endswith("(rapide seulement)")

    def test_aucune_sonde_rend_unknown(self):
        status = check_fast(SourceSpec(key="x", label="X"))
        assert status.status == "unknown"
        assert status.message == "aucune sonde rapide"
