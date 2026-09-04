"""Parsers LRCLIB sur réponse réelle enregistrée + comparateurs de matching.

Fixture = réponse /get exacte (Josman — Dans le vide, 243 s).
Re-capture : scripts/capture_fixtures.py --only lrclib.
"""

import re

import pytest
import requests

from src.api.lrclib_api import (
    LRCLIBAPI,
    _artist_match,
    _best_search_hit,
    _title_match,
)
from tests.conftest import load_fixture_json

FIXTURE = "lrclib/get_exact.json"
_LRC_TIMESTAMP_RE = re.compile(r"\[\d{2}:\d{2}")


@pytest.fixture(scope="module")
def api():
    return LRCLIBAPI()  # init = session requests, aucun réseau


def test_pack_fixture(api):
    packed = api._pack(load_fixture_json(FIXTURE))
    assert packed is not None, "réponse /get non packable — format LRCLIB changé ?"
    assert packed["source"] == "LRCLIB"
    assert packed["lrclib_id"]
    assert packed["lyrics_synced"], "pas de syncedLyrics dans la fixture"
    assert _LRC_TIMESTAMP_RE.search(packed["lyrics_synced"]), "format LRC [mm:ss] absent"
    # Durée = désambiguïsateur clé du /get (tolérance ±2 s côté API)
    assert abs(int(packed["duration"]) - 243) <= 2


def test_pack_vide(api):
    assert api._pack({}) is None
    assert api._pack({"instrumental": True}) is None
    assert api._pack("pas un dict") is None


@pytest.mark.parametrize(
    ("a", "b", "minimum"),
    [
        ("Dans le vide", "Dans le vide", 1.0),
        ("Dans le vide (feat. X)", "Dans le vide", 1.0),  # parenthèses feat ignorées
        ("Intro", "Intro - Remastered 2020", 0.9),  # inclusion stricte → bonus
    ],
)
def test_title_match_fort(a, b, minimum):
    assert _title_match(a, b) >= minimum


def test_title_match_faible():
    assert _title_match("Dans le vide", "Complètement Autre Chose") < 0.72


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("Josman", "josman", 1.0),
        ("Josman", "Josman & Kev", 1.0),  # inclusion après normalisation
        ("", "Josman", 0.0),
    ],
)
def test_artist_match(a, b, expected):
    assert _artist_match(a, b) == expected


# ────────────────── sélection du candidat et stratégie /get → /search


def _cand(titre="Dans le vide", artiste="Josman", duree=243, synced="[00:01.00]a", **extra):
    hit = {
        "id": 1,
        "trackName": titre,
        "artistName": artiste,
        "duration": duree,
        "syncedLyrics": synced,
        "plainLyrics": "a",
    }
    hit.update(extra)
    return hit


class TestSelectionDuCandidat:
    """`_best_search_hit` : le titre est un SEUIL (en dessous, écarté), la durée
    un départage. C'est elle qui distingue deux versions d'un même morceau."""

    def _best(self, results, titre="Dans le vide", artiste="Josman", duree=None, synced=True):
        return _best_search_hit(results, titre, artiste, duree, synced)

    def test_titre_trop_eloigne_ecarte(self):
        assert self._best([_cand(titre="Complètement autre chose")]) is None

    def test_candidat_sans_synchro_ecarte_si_exigee(self):
        assert self._best([_cand(synced=None)]) is None

    def test_candidat_sans_synchro_accepte_si_non_exigee(self):
        assert self._best([_cand(synced=None)], synced=False) is not None

    def test_duree_exacte_prime(self):
        """±2 s = forte confiance : ce bonus doit l'emporter sur un meilleur artiste."""
        loin = _cand(id=1, duree=300)
        proche = _cand(id=2, duree=244)
        assert self._best([loin, proche], duree=243)["id"] == 2

    def test_penalite_croissante_avec_lecart(self):
        proche = _cand(id=1, duree=250)
        tres_loin = _cand(id=2, duree=600)
        assert self._best([proche, tres_loin], duree=243)["id"] == 1

    def test_sans_duree_connue_le_titre_et_lartiste_decident(self):
        bon = _cand(id=1, artiste="Josman")
        mauvais = _cand(id=2, artiste="Quelqu'un d'autre")
        assert self._best([bon, mauvais])["id"] == 1

    def test_artiste_inconnu_neutralise_le_score_dartiste(self):
        assert _best_search_hit([_cand()], "Dans le vide", None, None, True) is not None

    @pytest.mark.parametrize("results", [[], [None], ["texte"]])
    def test_resultats_inexploitables(self, results):
        assert self._best(results) is None


class TestPack:
    def test_synchro_et_brut(self, api):
        packed = api._pack(_cand())
        assert packed["lyrics_synced"] and packed["lyrics"]
        assert packed["source"] == "LRCLIB"

    def test_brut_seul(self, api):
        assert api._pack(_cand(synced=None))["lyrics_synced"] is None

    def test_instrumental_signale(self, api):
        assert api._pack(_cand(instrumental=True))["instrumental"] is True

    @pytest.mark.parametrize("obj", [None, "texte", {}, {"syncedLyrics": "", "plainLyrics": ""}])
    def test_objets_vides(self, api, obj):
        assert api._pack(obj) is None


class TestStrategieGetSynced:
    """`/get` exact d'abord (4 champs, durée ±2 s), puis `/search` synchronisé,
    puis `/search` brut. C'est l'ordre qui garantit la meilleure synchro."""

    def _stub(self, api, monkeypatch, reponses):
        """`reponses` : dict {chemin: charge} servi par `_request`."""
        vus = []

        def fake_request(path, params):
            vus.append(path)
            return reponses.get(path)

        monkeypatch.setattr(api, "_request", fake_request)
        return vus

    def test_get_exact_suffit(self, api, monkeypatch):
        vus = self._stub(api, monkeypatch, {"/get": _cand()})

        res = api.get_synced("Dans le vide", "Josman", album_name="J.O.S", duration=243)
        assert res["lyrics_synced"]
        assert vus == ["/get"]  # aucun /search

    def test_repli_sur_search_si_get_sans_synchro(self, api, monkeypatch):
        vus = self._stub(api, monkeypatch, {"/get": _cand(synced=None), "/search": [_cand()]})

        assert api.get_synced("Dans le vide", "Josman", album_name="J.O.S", duration=243)[
            "lyrics_synced"
        ]
        assert vus == ["/get", "/search"]

    def test_sans_album_on_va_directement_au_search(self, api, monkeypatch):
        """`/get` exige les 4 champs : sans album, l'appel serait perdu."""
        vus = self._stub(api, monkeypatch, {"/search": [_cand()]})

        api.get_synced("Dans le vide", "Josman", duration=243)
        assert "/get" not in vus

    def test_dernier_recours_texte_brut(self, api, monkeypatch):
        """Deux passages sur /search : le 1er exige la synchro, le 2e non."""
        self._stub(api, monkeypatch, {"/search": [_cand(synced=None)]})

        res = api.get_synced("Dans le vide", "Josman", duration=243)
        assert res is not None and res["lyrics_synced"] is None and res["lyrics"]

    def test_aucune_parole(self, api, monkeypatch):
        self._stub(api, monkeypatch, {"/search": []})
        assert api.get_synced("Dans le vide", "Josman", duration=243) is None

    @pytest.mark.parametrize(("titre", "artiste"), [("", "Josman"), ("Titre", ""), ("", "")])
    def test_champs_obligatoires(self, api, titre, artiste):
        assert api.get_synced(titre, artiste) is None


class TestRequeteHttp:
    class _Rep:
        def __init__(self, statut, charge=None):
            self.status_code = statut
            self._charge = charge
            self.headers = {}

        def json(self):
            return self._charge

    def test_reponse_200(self, api, monkeypatch):
        monkeypatch.setattr(api.session, "get", lambda *a, **k: self._Rep(200, {"id": 1}))
        assert api._request("/get", {}) == {"id": 1}

    def test_404_ne_reessaie_pas(self, api, monkeypatch):
        appels = []

        def fake_get(*a, **k):
            appels.append(1)
            return self._Rep(404)

        monkeypatch.setattr(api.session, "get", fake_get)
        assert api._request("/get", {}) is None
        assert len(appels) == 1  # TrackNotFound : inutile d'insister

    def test_5xx_reessaie_puis_abandonne(self, api, monkeypatch):
        import src.api.lrclib_api as mod

        appels = []
        monkeypatch.setattr(mod.time, "sleep", lambda *_: None)

        def fake_get(*a, **k):
            appels.append(1)
            return self._Rep(503)

        monkeypatch.setattr(api.session, "get", fake_get)
        assert api._request("/get", {}) is None
        assert len(appels) == mod.MAX_RETRIES

    def test_panne_reseau(self, api, monkeypatch):
        import src.api.lrclib_api as mod

        monkeypatch.setattr(mod.time, "sleep", lambda *_: None)

        def fake_get(*a, **k):
            raise requests.RequestException("coupure")

        monkeypatch.setattr(api.session, "get", fake_get)
        assert api._request("/get", {}) is None


class TestArtisteHeriteDuCorrectifPartage:
    """`_artist_match` vient désormais de `src/api/_text_match.py`, partagé avec
    Musixmatch. Chez LRCLIB il ne sert qu'au CLASSEMENT (le seuil d'acceptation
    porte sur le titre) : ces cas vérifient que le correctif mot-entier n'a pas
    dégradé le départage."""

    def test_lartiste_exact_est_prefere_a_lhomonyme(self):
        """Deux candidats au même titre, l'un d'un artiste dont le nom CONTIENT
        le nôtre sans être le nôtre : c'est le vrai qui doit gagner."""
        vrai = _cand(id=1, artiste="IAM")
        homonyme = _cand(id=2, artiste="Williams")
        assert _best_search_hit([homonyme, vrai], "Dans le vide", "IAM", None, True)["id"] == 1

    def test_le_duo_reste_reconnu(self):
        duo = _cand(id=1, artiste="Jul & SCH")
        etranger = _cand(id=2, artiste="Nekfeu")
        assert _best_search_hit([etranger, duo], "Dans le vide", "Jul", None, True)["id"] == 1

    def test_la_duree_prime_toujours_sur_lartiste(self):
        """Hiérarchie inchangée : ±2 s vaut +1.0, l'artiste au mieux +0.5."""
        bon_artiste_loin = _cand(id=1, artiste="Josman", duree=400)
        autre_artiste_pile = _cand(id=2, artiste="Quelqu'un", duree=243)
        gagnant = _best_search_hit(
            [bon_artiste_loin, autre_artiste_pile], "Dans le vide", "Josman", 243, True
        )
        assert gagnant["id"] == 2
