"""L'oracle d'identité Deezer : jamais le premier hit, jamais un homonyme sur une égalité.

Cas mesurés le 2026-09-21 : « Isha » rend d'abord 259696952 (5 fans) alors que
le nôtre est 1236609 ; « A2H » rend un artiste UK.
"""

import asyncio

import pytest

from src.models import Artist, Track
from src.services import deezer_identite as di


def _hit(id_, name, nb_album=1, nb_fan=1):
    return {"id": id_, "name": name, "nb_album": nb_album, "nb_fan": nb_fan}


class TestCandidatsExacts:
    def test_nom_exact_normalise_seulement(self):
        hits = [_hit(1, "ISHA"), _hit(2, "Isha"), _hit(3, "Isha Bel"), _hit(4, "Sounds of Isha")]
        assert [c.id for c in di.candidats_exacts("Isha", hits)] == [1, 2]

    def test_cle_album_jamais_vide(self):
        assert di.cle_album("…") == di.cle_album("...") == "..."
        assert di.cle_album("Bitume Caviar (vol.1)") == "bitume caviar vol1"


class TestDepartager:
    def test_candidat_unique_accepte_sans_recouvrement(self):
        (seul,) = di.candidats_exacts("Lucio Bukowski", [_hit(9, "Lucio Bukowski")])
        assert di.departager([seul]) is seul

    def test_pluralite_nette(self):
        a, b = di.CandidatDeezer(1, "Isha", recouvrement=6), di.CandidatDeezer(
            2, "ISHA", recouvrement=0
        )
        assert di.departager([a, b]) is a

    def test_egalite_ne_tranche_pas(self):
        a, b = di.CandidatDeezer(1, "A2H", recouvrement=0), di.CandidatDeezer(
            2, "A2H", recouvrement=0
        )
        assert di.departager([a, b]) is None
        a.recouvrement = b.recouvrement = 2
        assert di.departager([a, b]) is None


class _Client:
    def __init__(self, hits, albums_par_artiste, pistes=None):
        self.hits, self.albums, self.pistes = hits, albums_par_artiste, pistes or {}

    async def search_artists_async(self, http, name, limit=50):
        return self.hits

    async def get_artist_async(self, http, artist_id):
        return {"id": artist_id, "name": "x"} if artist_id in self.albums else None

    async def get_artist_albums_async(self, http, artist_id):
        return self.albums.get(artist_id, [])

    async def get_track_async(self, http, track_id):
        return self.pistes.get(track_id)


class _DM:
    def __init__(self, albums, tracks):
        self._albums, self._tracks, self.ecrit = albums, tracks, None

    def get_albums_for_artist(self, artist_id):
        return self._albums

    def get_artist_tracks(self, artist_id):
        return self._tracks

    def update_artist_deezer_id(self, artist_id, deezer_id):
        self.ecrit = deezer_id
        return True


def _artist(deezer_id=None, name="Isha"):
    a = Artist(name=name)
    a.id, a.deezer_id = 1, deezer_id
    return a


class TestResoudre:
    def test_isha_tranche_par_les_albums(self):
        client = _Client(
            [_hit(259696952, "Isha", 7, 5), _hit(1236609, "ISHA", 44, 84468)],
            {
                259696952: [{"id": 11, "title": "Dear. Me"}],
                1236609: [
                    {"id": 22, "title": "La Vie Augmente Vol. 1"},
                    {"id": 23, "title": "Bitume Caviar"},
                ],
            },
        )
        dm = _DM([{"title": "La vie augmente vol.1", "deezer_album_id": None}], [])
        t = Track(title="Durag")
        t.album = "Bitume Caviar"
        dm._tracks = [t]
        artist = _artist()

        assert asyncio.run(di.resoudre_async(client, None, dm, artist)) == 1236609
        assert dm.ecrit == 1236609 and artist.deezer_id == 1236609

    def test_id_d_album_en_base_pese_plus_que_tout(self):
        client = _Client(
            [_hit(1, "A2H"), _hit(2, "A2H")],
            {1: [{"id": 500, "title": "Zzz"}], 2: [{"id": 600, "title": "Yyy"}]},
        )
        dm = _DM([{"title": "Autre", "deezer_album_id": 600}], [])
        assert asyncio.run(di.resoudre_async(client, None, dm, _artist(name="A2H"))) == 2

    def test_egalite_remonte_les_candidats_sans_ecrire(self):
        client = _Client([_hit(1, "A2H"), _hit(2, "A2H")], {1: [], 2: []})
        dm = _DM([], [])
        with pytest.raises(di.ArtisteDeezerAmbigu) as exc:
            asyncio.run(di.resoudre_async(client, None, dm, _artist(name="A2H")))
        assert [c.id for c in exc.value.candidats] == [1, 2]
        assert dm.ecrit is None

    def test_memorise_court_circuite_la_recherche(self):
        client = _Client([], {})
        assert (
            asyncio.run(di.resoudre_async(client, None, _DM([], []), _artist(1236609))) == 1236609
        )

    def test_force_verifie_la_fiche_puis_ecrit(self):
        client = _Client([], {7: []})
        dm = _DM([], [])
        assert asyncio.run(di.resoudre_async(client, None, dm, _artist(), force_id=7)) == 7
        assert dm.ecrit == 7
        with pytest.raises(di.ArtisteDeezerAmbigu):
            asyncio.run(di.resoudre_async(client, None, dm, _artist(), force_id=99))

    def test_aucun_homonyme_exact(self):
        client = _Client([_hit(3, "Isha Bel")], {})
        with pytest.raises(di.ArtisteDeezerAmbigu):
            asyncio.run(di.resoudre_async(client, None, _DM([], []), _artist()))
