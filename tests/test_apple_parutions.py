"""Apple Music par le code-barres (étape Identité, 2026-10-15).

Réponse iTunes RÉELLE (fixture `itunes/lookup_upc_civilisation.json`) : l'UPC
de *Civilisation Édition Ultime* rend aussi *Civilisation* 2021 — seul l'album
au bon nombre de pistes est rattaché.
"""

from src.models import Artist, ReleaseObservation, Track
from src.services.apple_parutions import (
    SOURCE_PROPOSITION,
    propositions_par_place,
    rattacher_parutions_apple,
)
from src.services.ecarts_deezer import AlbumDeezer
from src.utils.apple_identity import CHAMP_APPLE_PROPOSE
from tests.conftest import load_fixture_json

_UPC = "3596974281424"


class _ITunes:
    def __init__(self, resultats):
        self.resultats, self.appels = resultats, []

    def lookup_code_barres(self, upc, boutique="fr"):
        self.appels.append(upc)
        return self.resultats


def _piste(tid, disque, numero, nom, ms):
    return {
        "trackId": tid,
        "discNumber": disque,
        "trackNumber": numero,
        "trackName": nom,
        "trackTimeMillis": ms,
    }


class TestPropositionsParPlace:
    def _fiche(self, titre, duree=None, apple=None):
        t = Track(title=titre, artist=Artist(name="OrelSan"))
        t.duration, t.apple_music_id = duree, apple
        return t

    def test_meme_place_et_meme_titre(self):
        fiches = {1: self._fiche("Shonen")}
        out = propositions_par_place({1: (2, 1)}, [_piste(9, 2, 1, "Shonen", 153000)], fiches)
        assert out == {1: "9"}

    def test_meme_place_titre_autre_mais_meme_duree(self):
        fiches = {1: self._fiche("CP_001_Intro", duree=161)}
        pistes = [_piste(9, 1, 1, "Intro Civilisation perdue", 161400)]
        assert propositions_par_place({1: (1, 1)}, pistes, fiches) == {1: "9"}

    def test_meme_place_rien_ne_concorde(self):
        fiches = {1: self._fiche("Autre chose", duree=100)}
        assert (
            propositions_par_place({1: (1, 1)}, [_piste(9, 1, 1, "Shonen", 153000)], fiches) == {}
        )

    def test_une_fiche_qui_a_deja_son_id_n_est_pas_concernee(self):
        fiches = {1: self._fiche("Shonen", apple="777")}
        assert propositions_par_place({1: (2, 1)}, [_piste(9, 2, 1, "Shonen", 1)], fiches) == {}


def _parution(dm, artiste, titre, place):
    t = Track(title=titre, artist=artiste)
    t.id = dm.save_track(t)
    dm.record_release_observations(
        t.id,
        [
            ReleaseObservation(
                title="Civilisation Édition Ultime",
                source="deezer",
                external_release_id=366967207,
                upc=_UPC,
                disc_number=place[0],
                track_number=place[1],
            )
        ],
    )
    return t


def _album():
    deezer = load_fixture_json("deezer/album_civilisation.json")
    return AlbumDeezer.depuis(deezer, [])


def test_l_album_apple_rejoint_la_parution_et_propose_les_id(data_manager):
    artiste = Artist(name="OrelSan")
    artiste.id = data_manager.save_artist(artiste)
    shonen = _parution(data_manager, artiste, "Shonen", (2, 1))
    artiste.tracks = data_manager.get_artist_tracks(artiste.id)
    resultats = load_fixture_json("itunes/lookup_upc_civilisation.json")["results"]
    itunes = _ITunes(resultats)

    bilan = rattacher_parutions_apple(data_manager, artiste, [_album()], client=itunes)

    assert (bilan.albums_lus, bilan.rattaches, bilan.propositions) == (1, 1, 1)
    (parution,) = data_manager.get_track_releases(shonen.id)
    collection, vu = data_manager.etat_parution_apple(parution["id"])
    ultime = next(r for r in resultats if r.get("collectionName") == "Civilisation Édition Ultime")
    assert collection == str(ultime["collectionId"]) and vu is not None
    obs = data_manager.get_artist_observations(artiste.id)[shonen.id]
    (prop,) = [o for o in obs if o.field == CHAMP_APPLE_PROPOSE]
    piste = next(
        r
        for r in resultats
        if r.get("collectionId") == ultime["collectionId"] and r.get("trackName") == "Shonen"
    )
    assert (prop.value, prop.source) == (str(piste["trackId"]), SOURCE_PROPOSITION)

    # Rejoué : l'album est déjà rattaché, aucune nouvelle requête.
    rattacher_parutions_apple(data_manager, artiste, [_album()], client=itunes)
    assert itunes.appels == [_UPC]


def test_ambigu_ou_inconnu_est_date_sans_rattacher(data_manager):
    artiste = Artist(name="OrelSan")
    artiste.id = data_manager.save_artist(artiste)
    shonen = _parution(data_manager, artiste, "Shonen", (2, 1))
    artiste.tracks = data_manager.get_artist_tracks(artiste.id)
    album = _album()
    album.nb_tracks = 99  # aucun des deux albums iTunes n'a 99 pistes
    resultats = load_fixture_json("itunes/lookup_upc_civilisation.json")["results"]
    itunes = _ITunes(resultats)

    bilan = rattacher_parutions_apple(data_manager, artiste, [album], client=itunes)

    assert (bilan.rattaches, bilan.ambigus, bilan.propositions) == (0, 1, 0)
    (parution,) = data_manager.get_track_releases(shonen.id)
    collection, vu = data_manager.etat_parution_apple(parution["id"])
    assert collection is None and vu is not None
    # Daté : pas redemandé avant expiration.
    rattacher_parutions_apple(data_manager, artiste, [album], client=itunes)
    assert itunes.appels == [_UPC]


def test_la_chaine_complete_jusqu_a_3c(data_manager):
    """La proposition est jugée par 3c (`fiche_concorde`) ; l'ID écrit, la fiche
    iTunes de la piste rejoint la MÊME parution (identifiant posé par le
    code-barres) au lieu d'en ouvrir une seconde."""
    from types import SimpleNamespace

    from src.services.identite import verifier_apple_music

    artiste = Artist(name="OrelSan")
    artiste.id = data_manager.save_artist(artiste)
    shonen = _parution(data_manager, artiste, "Shonen", (2, 1))
    artiste.tracks = data_manager.get_artist_tracks(artiste.id)
    resultats = load_fixture_json("itunes/lookup_upc_civilisation.json")["results"]
    rattacher_parutions_apple(data_manager, artiste, [_album()], client=_ITunes(resultats))

    ultime = next(r for r in resultats if r.get("collectionName") == "Civilisation Édition Ultime")
    fiche = next(
        r
        for r in resultats
        if r.get("collectionId") == ultime["collectionId"] and r.get("trackName") == "Shonen"
    )

    class _Lookup:
        def lookup(self, ids):
            return {str(fiche["trackId"]): fiche}

    artiste.tracks = data_manager.get_artist_tracks(artiste.id)
    retenus, _ = verifier_apple_music(
        SimpleNamespace(data_manager=data_manager), artiste, client=_Lookup()
    )
    assert retenus == 1
    (relu,) = data_manager.get_artist_tracks(artiste.id)
    assert str(relu.apple_music_id) == str(fiche["trackId"])
    assert len(data_manager.get_track_releases(shonen.id)) == 1
