"""B0 (2026-09-28) : la piste Deezer déjà liée se LIT par son id.

Le provider cherchait « {artiste} {titre} » même quand la fiche portait son
`deezer_id` ; le hit pouvait être une autre piste (édition clean, single,
compilation) et `explicit`, durée, ISRC venaient d'un autre enregistrement que
celui lié. La recherche libre ne reste qu'un repli — et seulement quand Deezer
a RÉPONDU que l'id n'existe plus.
"""

import asyncio

import pytest

from src.api.deezer_api import DeezerAPI
from src.enrichment.context import EnrichmentContext
from src.enrichment.providers.deezer import DeezerProvider
from src.models.artist import Artist
from src.models.track import Track
from src.observability import source_usage
from src.observability.issues import IssueKind, SansReponse

FICHE = {
    "id": 133165774,
    "title": "DKR",
    "duration": 176,
    "isrc": "FR8R61600004",
    "explicit_lyrics": True,
    "release_date": "2016-10-01",  # date de l'ÉDITION — jamais transmise
    "bpm": 97.5,  # jamais mesuré contre le vote — jamais transmis
    "album": {"id": 14175306, "title": "DKR"},
    "artist": {"id": 1, "name": "Booba"},
}


class _Api(DeezerAPI):
    """`_make_request[_async]` et la recherche remplacés ; le reste est réel."""

    def __init__(self, reponse_piste, *, panne=False):
        super().__init__()
        self.reponse_piste = reponse_piste
        self.panne = panne
        self.recherches = 0
        self.urls = []

    def _repondre(self, endpoint):
        self.urls.append(endpoint)
        if self.panne:
            source_usage.record_attempt("deezer", IssueKind.UNREACHABLE, detail="coupé")
            return None
        source_usage.record_attempt("deezer", IssueKind.OK)
        return self._payload_or_none(self.reponse_piste)

    def _make_request(self, endpoint, params=None):
        return self._repondre(endpoint)

    async def _make_request_async(self, http, endpoint, params=None):
        return self._repondre(endpoint)

    def search_track(self, *a, **k):
        self.recherches += 1
        return {"id": 1, "title": "DKR (Clean)", "duration": 176}

    async def search_track_async(self, http, *a, **k):
        return self.search_track()


def _appel(api, voie, **kw):
    if voie == "sync":
        return api.enrich_track("Booba", "DKR", **kw)
    return asyncio.run(api.enrich_track_async(None, "Booba", "DKR", **kw))


@pytest.fixture(params=["sync", "async"])
def voie(request):
    return request.param


def test_id_connu_lu_par_id_sans_recherche(voie):
    api = _Api(FICHE)
    r = _appel(api, voie, deezer_id=133165774)
    assert api.urls == ["track/133165774"] and api.recherches == 0
    d = r["data"]
    assert d["deezer_track_id"] == 133165774 and d["deezer_explicit_lyrics"] is True
    assert d["deezer_release_date"] is None and d["deezer_bpm"] is None


def test_id_retire_du_catalogue_repli_sur_la_recherche(voie):
    api = _Api({"error": {"code": 800, "type": "DataException", "message": "no data"}})
    r = _appel(api, voie, deezer_id=42)
    assert api.recherches == 1 and r["data"]["deezer_track_id"] == 1


def test_panne_sur_la_lecture_par_id_ne_cherche_pas_une_autre_edition(voie):
    api = _Api(FICHE, panne=True)
    with pytest.raises(SansReponse):
        _appel(api, voie, deezer_id=133165774)
    assert api.recherches == 0


def test_sans_id_la_recherche_reste_la_voie(voie):
    api = _Api(FICHE)
    _appel(api, voie)
    assert api.urls == [] and api.recherches == 1


def test_le_provider_transmet_l_id_de_la_fiche():
    vus = []

    class _Client:
        async def enrich_track_async(self, http, *a, deezer_id=None, **k):
            vus.append(deezer_id)
            return {"success": False, "error": "x"}

    track = Track(title="DKR", artist=Artist(name="Booba"))
    track.deezer_id = 133165774
    asyncio.run(DeezerProvider(_Client()).enrich_async(track, EnrichmentContext()))
    assert vus == [133165774]


def test_le_rattachement_catalogue_pose_explicit_s_il_est_vide(data_manager):
    """La piste liée le porte ; seul le provider le posait, sur SON hit."""
    from types import SimpleNamespace

    from src.services.ecarts_deezer import PisteDeezer, _renseigner_fiche

    artist = Artist(name="Booba")
    artist.id = data_manager.save_artist(artist)
    track = Track(title="DKR", artist=artist)
    data_manager.save_track(track)
    piste = PisteDeezer(id=133165774, title="DKR", explicit=True, duration=176)
    _renseigner_fiche(data_manager, SimpleNamespace(piste=piste), track)
    assert track.lyrics.explicit is True
    (relu,) = data_manager.get_artist_tracks(artist.id)
    assert relu.lyrics.explicit is True

    # « Le premier renseigne, personne ne remplace » : une édition clean liée
    # ensuite ne réécrit pas le drapeau.
    piste2 = PisteDeezer(id=1, title="DKR", explicit=False, duration=176)
    _renseigner_fiche(data_manager, SimpleNamespace(piste=piste2), relu)
    (relu,) = data_manager.get_artist_tracks(artist.id)
    assert relu.lyrics.explicit is True
