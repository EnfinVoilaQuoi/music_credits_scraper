"""Genius `/artists/{id}/songs` — QUI entre dans la discographie.

L'API rend aussi les morceaux où l'artiste est writer/producer ou mal tagué.
Règles gelées ici : un morceau dont il n'est pas l'artiste principal n'entre
que s'il est (1) dans `featured_artists`, (2) co-principal dans
`primary_artists`, ou (3) nommé EXACTEMENT dans une page collab
(« Limsa d'Aulnay & Isha », pas « Vasjan & ISHA! ») ; sinon il est jeté —
sauf `include_secondary`, qui VÉRIFIE au détail et garde le rôle.
"""

import pytest
import requests

from src.api.genius_api import GeniusAPI
from src.models import Artist

MOI, AUTRE = 100, 200


def _song(i, primary_id, primary_name="X", **extra):
    return {
        "id": i,
        "title": f"T{i}",
        "url": f"https://genius.com/{i}",
        "primary_artist": {"id": primary_id, "name": primary_name},
        **extra,
    }


class _Client:
    def __init__(self, songs, details=None):
        self.songs, self.details = songs, details or {}
        self.detail_calls = []

    def artist_songs(self, artist_id, sort=None, per_page=None, page=1):
        return {"songs": self.songs if page == 1 else [], "next_page": 2 if page == 1 else None}

    def song(self, song_id):
        self.detail_calls.append(song_id)
        d = self.details.get(song_id)
        if isinstance(d, Exception):
            raise d
        return {"song": d or {}}


@pytest.fixture
def api(monkeypatch):
    inst = GeniusAPI.__new__(GeniusAPI)
    monkeypatch.setattr("src.api.genius_api.time.sleep", lambda *_: None)
    return inst


def _run(api, songs, **kw):
    return api._get_artist_songs_manual(
        Artist(name="Isha", genius_id=MOI), None, include_features=True, **kw
    )


class TestFiltreFeats:
    def test_principal_feat_declare_coprincipal_et_collab_par_nom(self, api):
        api.genius = _Client(
            [
                _song(1, MOI, "Isha"),
                _song(2, AUTRE, featured_artists=[{"id": MOI}]),
                _song(3, AUTRE, primary_artists=[{"id": AUTRE}, {"id": str(MOI)}]),
                _song(4, AUTRE, "Limsa d'Aulnay & Isha"),
                _song(5, AUTRE, "Vasjan & ISHA!"),  # nom ≠ exact
                _song(6, AUTRE, "Autre", writer_artists=[{"id": MOI}]),  # rôle secondaire
            ]
        )
        tracks = _run(api, None)
        assert [t.genius_id for t in tracks] == [1, 2, 3, 4]
        assert [t.is_featuring for t in tracks] == [False, True, True, True]
        assert tracks[1].primary_artist_name == "X"

    def test_sans_include_features_seuls_les_principaux(self, api):
        api.genius = _Client([_song(1, MOI), _song(2, AUTRE, featured_artists=[{"id": MOI}])])
        tracks = api._get_artist_songs_manual(
            Artist(name="Isha", genius_id=MOI), None, include_features=False
        )
        assert [t.genius_id for t in tracks] == [1]


class TestRolesSecondaires:
    def test_verifies_au_detail_et_classes(self, api):
        details = {
            6: {"primary_artists": [{"id": MOI}]},  # liste sous-déclarée → vrai primaire
            7: {"featured_artists": [{"id": MOI}]},  # vrai feat sous-déclaré
            8: {"custom_performances": [{"label": "Additional Vocals", "artists": [{"id": MOI}]}]},
            9: {"producer_artists": [{"id": MOI}]},
            10: {"writer_artists": [{"id": AUTRE}]},  # pas crédité au détail → jeté
        }
        api.genius = _Client([_song(i, AUTRE) for i in (6, 7, 8, 9, 10)], details)
        tracks = _run(api, None, include_secondary=True)
        assert [(t.genius_id, t.is_featuring, t.secondary_role) for t in tracks] == [
            (6, False, None),
            (7, True, None),
            (8, True, "Additional Vocals"),
            (9, True, "Producer"),
        ]
        assert api.genius.detail_calls == [6, 7, 8, 9, 10]

    def test_detail_en_echec_jette_le_morceau(self, api):
        api.genius = _Client([_song(6, AUTRE)], {6: AssertionError("HTTP 500")})
        assert _run(api, None, include_secondary=True) == []
        api.genius = _Client([_song(6, AUTRE)], {6: requests.ConnectionError("x")})
        assert _run(api, None, include_secondary=True) == []

    def test_verify_sans_id(self, api):
        assert api._verify_artist_credit(None, MOI) is None


class TestCollabParNom:
    @pytest.mark.parametrize(
        "primary, attendu",
        [
            ("Limsa d'Aulnay & Isha", True),
            ("Isha x Sch", True),
            ("Isha feat. Sch", True),
            ("Isha et Sch", True),
            ("Vasjan & ISHA!", False),
            ("Isha", False),  # pas une collab
            ("", False),
        ],
    )
    def test_nom_exact_dans_une_collab(self, primary, attendu):
        assert GeniusAPI._primary_is_collab_with(primary, "Isha") is attendu

    def test_collect_ids_robuste_aux_types(self):
        assert GeniusAPI._collect_artist_ids([{"id": "1"}, {"id": None}, "x", {"id": "a"}]) == {1}


class TestReseau:
    def test_client_qui_leve_rend_le_partiel(self, api):
        class _Casse(_Client):
            def artist_songs(self, *a, page=1, **k):
                if page == 2:
                    raise requests.ConnectionError("coupé")
                return {"songs": [_song(1, MOI)], "next_page": 2}

        api.genius = _Casse([])
        assert [t.genius_id for t in _run(api, None)] == [1]
