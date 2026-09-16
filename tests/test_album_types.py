"""Nature des disques (`src/enrichment/album_types.py`) : pur + coroutine, réseau mocké.

Le cas mesuré qui justifie le garde-fou : « Drôle d'oiseau » a chez Deezer une
fiche `single` d'1 titre (le morceau homonyme) ET un EP de 9 titres — le hit
d'un morceau peut désigner l'édition single, qui ne doit pas qualifier l'album.
"""

import asyncio
from collections import Counter

import httpx

from src.api.async_http import AsyncHttpSession
from src.api.deezer_api import DeezerAPI
from src.concurrency.rate_limiter import DomainRateLimiter
from src.enrichment import album_types as at
from src.enrichment.context import EnrichmentContext
from src.enrichment.providers.deezer import DeezerProvider
from src.models.track import Track


def _http(handler) -> AsyncHttpSession:
    return AsyncHttpSession(transport=httpx.MockTransport(handler), limiter=DomainRateLimiter(0.0))


def _track(tid, album, deezer_album_id=None):
    t = Track(id=tid, title=f"t{tid}", album=album)
    t._deezer_album_id = deezer_album_id
    return t


# ── Briques pures ────────────────────────────────────────────────────────────


def test_libelles():
    assert at.libelle_record_type("ep") == "EP"
    assert at.libelle_record_type("EP") == "EP"
    assert at.libelle_record_type("compile") == "Compilation"
    assert at.libelle_record_type(None) == "Album"
    assert at.libelle_record_type("mixtape") == "Album"


def test_fiche_album_normalises_and_rejects_garbage():
    f = at.fiche_album({"id": 9, "title": "X", "record_type": "EP", "nb_tracks": "9"})
    assert f == at.FicheAlbum(9, "X", "ep", 9)
    assert (
        at.fiche_album({"id": 9, "record_type": "mixtape", "nb_tracks": None}).record_type is None
    )
    assert at.fiche_album({"error": {"type": "DataException"}}) is None
    assert at.fiche_album(None) is None


def test_regrouper_counts_all_tracks_and_ids():
    tracks = [
        _track(1, "A", 10),
        _track(2, "A", 10),
        _track(3, "A", 11),
        _track(4, "A"),
        _track(5, "", 5),
    ]
    g = at.regrouper(tracks)
    assert list(g) == ["A"]
    assert g["A"].nb_morceaux == 4 and g["A"].ids == Counter({10: 2, 11: 1})


def test_choisir_id_majority_then_smallest():
    assert at.choisir_id(Counter({10: 2, 11: 1})) == 10
    assert at.choisir_id(Counter({11: 1, 10: 1})) == 10
    assert at.choisir_id(Counter()) is None


def test_verdict_rejects_single_edition_of_an_album_track():
    single = at.FicheAlbum(1, "Drôle d'oiseau", "single", 1)
    ep = at.FicheAlbum(2, "Drôle d'oiseau", "ep", 9)
    assert at.verdict(single, 9) == (None, "fiche single (1 piste) pour 9 morceaux")
    assert at.verdict(ep, 9) == ("ep", None)
    # Un vrai single (1 morceau chez nous aussi) passe.
    assert at.verdict(single, 1) == ("single", None)
    assert at.verdict(None, 3) == (None, "fiche absente")
    assert at.verdict(at.FicheAlbum(3, "x", None, 4), 4) == (None, "record_type inconnu")


# ── Coroutine d'orchestration ────────────────────────────────────────────────


class _Client:
    def __init__(self, fiches):
        self.fiches = fiches
        self.calls: list[int] = []

    async def get_album_async(self, http, album_id):
        self.calls.append(album_id)
        return self.fiches.get(album_id)


def test_types_albums_deezer_end_to_end():
    tracks = [
        _track(1, "Drôle d'oiseau", 2),
        _track(2, "Drôle d'oiseau", 2),
        _track(3, "Drôle d'oiseau", 1),  # le hit single du titre homonyme
        _track(4, "Labrador bleu", 3),
        _track(5, "Vas-y chante"),  # aucun hit
        _track(6, "Bitume Caviar", 4),
    ]
    client = _Client(
        {
            2: {"id": 2, "title": "Drôle d'oiseau", "record_type": "ep", "nb_tracks": 9},
            3: {"id": 3, "title": "Labrador bleu", "record_type": "album", "nb_tracks": 15},
            4: {"id": 4, "title": "Bitume Caviar", "record_type": "album", "nb_tracks": 11},
        }
    )
    ecrits = []

    def ecrire(title, rt, album_id):
        ecrits.append((title, rt, album_id))
        return title != "Bitume Caviar"  # saisie manuelle conservée là-bas

    deja = {"Labrador bleu": {"record_type": "album", "deezer_album_id": 3}}
    bilan = asyncio.run(at.types_albums_deezer(client, None, tracks, deja, ecrire))
    assert client.calls == [4, 2]  # tri par titre ; Labrador déjà connu → pas redemandé
    assert ecrits == [("Bitume Caviar", "album", 4), ("Drôle d'oiseau", "ep", 2)]
    assert bilan.renseignes == 1 and bilan.ignores == 2
    assert bilan.motifs == [
        "Bitume Caviar : saisie manuelle conservée",
        "Vas-y chante : aucun hit Deezer",
    ]
    # `force` redemande la fiche connue.
    client.calls.clear()
    asyncio.run(at.types_albums_deezer(client, None, tracks, deja, ecrire, force=True))
    assert 3 in client.calls


def test_get_album_async_reads_fiche_and_absent():
    def handler(request):
        if request.url.path.endswith("/album/2"):
            return httpx.Response(200, json={"id": 2, "record_type": "ep", "nb_tracks": 9})
        return httpx.Response(200, json={"error": {"type": "DataException", "message": "no data"}})

    api = DeezerAPI()
    assert asyncio.run(api.get_album_async(_http(handler), 2))["record_type"] == "ep"
    assert asyncio.run(api.get_album_async(_http(handler), 999)) is None


def test_provider_stashes_album_id_without_marking_updated():
    class _Api:
        def enrich_track(self, **kw):
            return {
                "success": True,
                "data": {"deezer_album_id": 77},
                "verifications": {},
                "raw_data": {},
            }

    track = Track(id=1, title="x", album="A")
    ctx = EnrichmentContext()
    updated = DeezerProvider(_Api()).enrich(track, ctx)
    assert track._deezer_album_id == 77
    assert updated is False


# ── Repli : recherche d'album par (artiste, titre) ───────────────────────────


def test_choisir_fiche_recherche_exact_title_multitrack_fullest():
    hits = [
        {"id": 1, "title": "Drôle d'oiseau", "record_type": "single", "nb_tracks": 1},
        {"id": 2, "title": "Drôle d'oiseau", "record_type": "ep", "nb_tracks": 9},
        {"id": 3, "title": "Drôle d'oiseau (Live)", "record_type": "album", "nb_tracks": 12},
        {"id": 4, "title": "Bitume Caviar (vol.1)", "record_type": "album", "nb_tracks": 11},
        {"id": 5, "title": "Bitume Caviar (vol.1)", "record_type": "album", "nb_tracks": 15},
    ]
    assert at.choisir_fiche_recherche(hits, "Drôle d’oiseau", 9).id == 2
    assert at.choisir_fiche_recherche(hits, "Drôle d'oiseau", 1).id == 2  # la plus fournie
    assert at.choisir_fiche_recherche(hits, "Bitume Caviar (vol.1)", 12).id == 5
    assert at.choisir_fiche_recherche(hits, "Inconnu", 3) is None
    # Graphies Deezer/Genius : « , Vol. 1 » vs « Vol.1 » (mesuré sur Isha).
    lva = [{"id": 7, "title": "La Vie Augmente, Vol. 1", "record_type": "ep", "nb_tracks": 10}]
    assert at.choisir_fiche_recherche(lva, "La vie augmente Vol.1", 10).id == 7
    assert at.choisir_fiche_recherche(lva, "La vie augmente Vol.2", 10) is None
    assert at.choisir_fiche_recherche([], "x", 3) is None


class _ClientRecherche(_Client):
    def __init__(self, fiches, recherche):
        super().__init__(fiches)
        self.recherche = recherche
        self.searches: list[str] = []

    async def search_album_async(self, http, artist, title):
        self.searches.append(title)
        return self.recherche.get(title, [])


def test_types_albums_falls_back_to_album_search():
    tracks = [_track(1, "LVA 1"), _track(2, "LVA 1"), _track(3, "Inconnu")]
    client = _ClientRecherche(
        {},
        {"LVA 1": [{"id": 9, "title": "LVA 1", "record_type": "ep", "nb_tracks": 10}]},
    )
    ecrits = []
    bilan = asyncio.run(
        at.types_albums_deezer(
            client, None, tracks, {}, lambda *a: ecrits.append(a) or True, artist_name="Isha"
        )
    )
    assert client.searches == ["Inconnu", "LVA 1"] and client.calls == []
    assert ecrits == [("LVA 1", "ep", 9)]
    assert bilan.motifs == ["Inconnu : aucune fiche Deezer à ce titre"]
    # Sans nom d'artiste, pas de repli possible.
    bilan = asyncio.run(at.types_albums_deezer(client, None, tracks, {}, lambda *a: True))
    assert bilan.motifs == ["Inconnu : aucun hit Deezer", "LVA 1 : aucun hit Deezer"]
    # Déjà qualifié via le repli → pas redemandé.
    deja = {"LVA 1": {"record_type": "ep", "deezer_album_id": 9}}
    client.searches.clear()
    asyncio.run(
        at.types_albums_deezer(client, None, tracks, deja, lambda *a: True, artist_name="Isha")
    )
    assert client.searches == ["Inconnu"]


def test_search_album_async_falls_back_to_free_query():
    seen = []

    def handler(request):
        seen.append(request.url.params.get("q"))
        if request.url.params.get("q", "").startswith("artist:"):
            return httpx.Response(200, json={"data": [], "total": 0})
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": 1,
                        "title": "La Vie Augmente, Vol. 1",
                        "record_type": "ep",
                        "nb_tracks": 10,
                    }
                ]
            },
        )

    hits = asyncio.run(
        DeezerAPI().search_album_async(_http(handler), "Isha", "La vie augmente Vol.1")
    )
    assert [h["id"] for h in hits] == [1]
    assert seen == ['artist:"Isha" album:"La vie augmente Vol.1"', "Isha La vie augmente Vol.1"]
