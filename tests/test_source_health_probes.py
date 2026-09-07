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


class TestSondeBpi:
    """La sonde BPI emprunte le VRAI transport, et sa valeur est de virer au ROUGE.

    Une sonde qui taperait simplement l'URL rendrait 200 même parseur mort : sans
    l'en-tête `HX-Request`, le site sert la coquille de l'application, sans une
    seule ligne. Ces tests vérifient donc surtout les échecs — le chemin nominal
    ne prouve rien à lui seul.
    """

    ENTETES = (
        "Artist",
        "Title",
        "Award",
        "Format",
        "Corporate Group/Label",
        "Latest Certification",
        "Released",
    )

    def _page(self, entetes=None, lignes=1):
        th = "".join(f"<th>{n}</th>" for n in (entetes or self.ENTETES))
        tr = (
            '<tr hx-get="/format/2/artist/1/title/1">'
            "<td>A</td><td>T</td><td><span>Gold</span></td><td>Single</td>"
            "<td>LABEL</td><td>24.01.2020</td><td>01.01.2019</td></tr>"
        ) * lignes
        return f"<table><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table>"

    def _reponse(self, monkeypatch, texte, status=200):
        capture = {}

        class R:
            status_code = status
            text = texte

        def faux_get(url, **kw):
            capture["url"] = url
            capture["headers"] = kw.get("headers", {})
            return R()

        monkeypatch.setattr("httpx.get", faux_get)
        return capture

    def test_page_saine_aucune_anomalie(self, monkeypatch):
        capture = self._reponse(monkeypatch, self._page())
        assert sh._probe_bpi() == []
        assert (
            capture["headers"].get("HX-Request") == "true"
        ), "sans cet en-tête la sonde mesurerait une coquille vide"

    def test_sans_tableau_c_est_ROUGE(self, monkeypatch):
        """Ce que rend le site quand l'en-tête HX est ignoré, ou après refonte."""
        self._reponse(monkeypatch, "<div>coquille de l'application</div>")
        anomalies = sh._probe_bpi()
        assert anomalies and "en-tête" in anomalies[0]

    def test_un_entete_renomme_c_est_ROUGE(self, monkeypatch):
        entetes = list(self.ENTETES[:-1]) + ["Release Date"]
        self._reponse(monkeypatch, self._page(entetes=entetes))
        anomalies = sh._probe_bpi()
        assert anomalies and "Released" in anomalies[0]

    def test_entetes_bons_mais_aucune_ligne_extraite_c_est_ROUGE(self, monkeypatch):
        vide = self._page(lignes=0).replace("<tbody></tbody>", "<tbody><tr><td>x</td></tr></tbody>")
        self._reponse(monkeypatch, vide)
        assert sh._probe_bpi() == ["en-têtes corrects mais aucune ligne extraite"]

    def test_un_statut_non_200_est_rapporte(self, monkeypatch):
        self._reponse(monkeypatch, "", status=503)
        assert sh._probe_bpi() == ["HTTP 503"]

    def test_une_erreur_reseau_ne_leve_pas(self, monkeypatch):
        import httpx

        def faux_get(*a, **k):
            raise httpx.ConnectError("boom")

        monkeypatch.setattr("httpx.get", faux_get)
        anomalies = sh._probe_bpi()
        assert anomalies and "ConnectError" in anomalies[0]


class TestSondeBrmaComplete:
    """La sonde RAPIDE de BRMA ne voit que le domaine (403 anti-bot attendu) ;
    seule la complète lit une page et peut donc constater un parseur cassé."""

    def _fetch(self, monkeypatch, soup):
        monkeypatch.setattr("src.scrapers.ultratop_fetch.fetch_ultratop_soup", lambda y, c: soup)

    def test_page_lisible_aucune_anomalie(self, monkeypatch):
        from bs4 import BeautifulSoup

        html = (
            '<div style="display:table-row">'
            '<div class="chart_title"><a href="/x">A<br>T</a></div>'
            '<div class="company">01/01/2021: Or</div></div>'
        )
        self._fetch(monkeypatch, BeautifulSoup(html, "html.parser"))
        assert sh._probe_brma_full() == []

    def test_page_non_rendue_est_un_SKIP_pas_un_rouge(self, monkeypatch):
        """Cloudflare non résolu n'est pas un parseur cassé : on ne mesure rien,
        et peindre en rouge ce qu'on n'a pas mesuré est le piège du projet."""
        self._fetch(monkeypatch, None)
        with pytest.raises(sh.ProbeSkipped):
            sh._probe_brma_full()

    def test_aucune_ligne_sur_une_annee_revolue_est_ROUGE(self, monkeypatch):
        from bs4 import BeautifulSoup

        self._fetch(monkeypatch, BeautifulSoup("<div>rien</div>", "html.parser"))
        anomalies = sh._probe_brma_full()
        assert anomalies and "aucune ligne" in anomalies[0]

    def test_le_recours_au_repli_semantique_est_SIGNALE(self, monkeypatch):
        """Le scrape continue, mais le gabarit a changé : il faut le savoir."""
        from bs4 import BeautifulSoup

        html = (
            '<div class="row">'
            '<div class="chart_title"><a href="/x">A<br>T</a></div>'
            '<div class="company">01/01/2021: Or</div></div>'
        )
        self._fetch(monkeypatch, BeautifulSoup(html, "html.parser"))
        anomalies = sh._probe_brma_full()
        assert anomalies and "repli sémantique" in anomalies[0]
