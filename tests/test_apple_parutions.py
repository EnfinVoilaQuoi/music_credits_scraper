"""Apple Music par le code-barres (étape Identité, 2026-10-15).

Réponse iTunes RÉELLE (fixture `itunes/lookup_upc_civilisation.json`) : l'UPC
de *Civilisation Édition Ultime* rend aussi *Civilisation* 2021 — seul l'album
au bon nombre de pistes est rattaché.
"""

from src.api.itunes_api import album_du_code_barres
from src.api.musicbrainz_api import albums_lies
from src.models import Artist, ReleaseObservation, Track
from src.services.apple_parutions import (
    SOURCE_PROPOSITION,
    album_designe_par_musicbrainz,
    explicite_concorde,
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


class _MB:
    def __init__(self, parutions, panne=None):
        self.parutions, self.panne, self.appels = parutions, panne, []

    def parutions_par_code_barres(self, upc):
        self.appels.append(upc)
        if self.panne:
            raise self.panne
        return self.parutions


def _parutions_mb(*noms):
    """`parutions_par_code_barres` rejoué depuis les url-rels RÉELLES."""
    return [{"albums": albums_lies(load_fixture_json(f"musicbrainz/{n}"))} for n in noms]


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


def test_oublier_parution_apple_garde_la_date(data_manager):
    """SCH « Loup noir » (2026-10-30) : l'album Apple de « Loup noir (A COLORS
    SHOW) » noté sur la parution du single ordinaire — retiré à la main, le
    constat reste daté."""
    artiste = Artist(name="OrelSan")
    artiste.id = data_manager.save_artist(artiste)
    shonen = _parution(data_manager, artiste, "Shonen", (2, 1))
    (parution,) = data_manager.get_track_releases(shonen.id)
    data_manager.noter_parution_apple(parution["id"], artiste.id, 42)

    assert data_manager.oublier_parution_apple(parution["id"], 42)
    collection, vu = data_manager.etat_parution_apple(parution["id"])
    assert collection is None and vu is not None
    assert not data_manager.oublier_parution_apple(parution["id"], 42)


def test_ambigu_ou_inconnu_est_date_sans_rattacher(data_manager):
    artiste = Artist(name="OrelSan")
    artiste.id = data_manager.save_artist(artiste)
    shonen = _parution(data_manager, artiste, "Shonen", (2, 1))
    artiste.tracks = data_manager.get_artist_tracks(artiste.id)
    album = _album()
    album.nb_tracks = 99  # aucun des deux albums iTunes n'a 99 pistes
    resultats = load_fixture_json("itunes/lookup_upc_civilisation.json")["results"]
    itunes = _ITunes(resultats)

    mb = _MB([])  # MusicBrainz ne connaît pas le code-barres : rien ne départage

    bilan = rattacher_parutions_apple(data_manager, artiste, [album], client=itunes, mb=mb)

    assert (bilan.rattaches, bilan.ambigus, bilan.propositions) == (0, 1, 0)
    assert mb.appels == [_UPC]
    (parution,) = data_manager.get_track_releases(shonen.id)
    collection, vu = data_manager.etat_parution_apple(parution["id"])
    assert collection is None and vu is not None
    # Daté : pas redemandé avant expiration.
    rattacher_parutions_apple(data_manager, artiste, [album], client=itunes, mb=mb)
    assert itunes.appels == [_UPC] and mb.appels == [_UPC]


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


class TestDepartageMusicBrainz:
    """iTunes ambigu, MusicBrainz départage (2026-10-30). Réponses RÉELLES du
    brut : *Graduation* (explicite et clean, 14 pistes chacun) et *VULTURES 2*
    (MusicBrainz lie l'édition clean à l'album Deezer explicite)."""

    GRADUATION_EXPLICITE = "1451901307"
    VULTURES_CLEAN = "1761882317"

    def _graduation(self):
        resultats = load_fixture_json("itunes/lookup_upc_graduation.json")["results"]
        album = AlbumDeezer.depuis(load_fixture_json("deezer/album_graduation.json"), [])
        return resultats, album

    def test_itunes_seul_est_ambigu(self):
        resultats, album = self._graduation()
        assert album_du_code_barres(resultats, album.nb_tracks) is None

    def test_musicbrainz_designe_l_edition_explicite(self):
        resultats, album = self._graduation()
        assert album.explicit_lyrics is True
        trouve = album_designe_par_musicbrainz(
            resultats, _parutions_mb("release_rels_graduation.json"), album.id, True
        )
        assert str(trouve["collection"]["collectionId"]) == self.GRADUATION_EXPLICITE
        assert len(trouve["pistes"]) == 14
        assert trouve["pistes"][0]["trackName"] == "Good Morning"

    def test_parution_qui_ne_lie_pas_notre_album_deezer(self):
        resultats, album = self._graduation()
        parutions = _parutions_mb("release_rels_graduation.json")
        assert album_designe_par_musicbrainz(resultats, parutions, 1, True) is None

    def test_album_apple_absent_des_resultats_itunes(self):
        resultats, album = self._graduation()
        parutions = [{"albums": {"deezer": [str(album.id)], "apple_music": ["999"]}}]
        assert album_designe_par_musicbrainz(resultats, parutions, album.id, True) is None

    def test_deux_albums_apple_ne_departagent_rien(self):
        resultats, album = self._graduation()
        parutions = [
            {"albums": {"deezer": [str(album.id)], "apple_music": ["1451901307", "1442845779"]}}
        ]
        assert album_designe_par_musicbrainz(resultats, parutions, album.id, True) is None

    def test_edition_clean_pour_un_album_deezer_explicite_refusee(self):
        resultats = load_fixture_json("itunes/lookup_upc_vultures2.json")["results"]
        album = AlbumDeezer.depuis(load_fixture_json("deezer/album_vultures2.json"), [])
        parutions = _parutions_mb("release_rels_vultures2_a.json", "release_rels_vultures2_b.json")
        assert album.explicit_lyrics is True
        assert album_designe_par_musicbrainz(resultats, parutions, album.id, True) is None
        # Sans le témoin explicite, c'est bien l'édition clean que MusicBrainz lie.
        trouve = album_designe_par_musicbrainz(resultats, parutions, album.id, None)
        assert str(trouve["collection"]["collectionId"]) == self.VULTURES_CLEAN

    def test_explicite_concorde(self):
        assert explicite_concorde("explicit", True)
        assert explicite_concorde("cleaned", False)
        assert explicite_concorde("notExplicit", False)
        assert not explicite_concorde("cleaned", True)
        assert not explicite_concorde("notExplicit", True)
        assert not explicite_concorde("explicit", False)
        assert explicite_concorde(None, True) and explicite_concorde("explicit", None)

    def _scenario(self, dm):
        resultats, album = self._graduation()
        artiste = Artist(name="Kanye West")
        artiste.id = dm.save_artist(artiste)
        t = Track(title="Good Morning", artist=artiste)
        t.id = dm.save_track(t)
        dm.record_release_observations(
            t.id,
            [
                ReleaseObservation(
                    title="Graduation",
                    source="deezer",
                    external_release_id=album.id,
                    upc=album.upc,
                    disc_number=1,
                    track_number=1,
                )
            ],
        )
        artiste.tracks = dm.get_artist_tracks(artiste.id)
        return artiste, t, resultats, album

    def test_rattache_et_propose_par_la_place(self, data_manager):
        artiste, t, resultats, album = self._scenario(data_manager)
        mb = _MB(_parutions_mb("release_rels_graduation.json"))

        bilan = rattacher_parutions_apple(
            data_manager, artiste, [album], client=_ITunes(resultats), mb=mb
        )

        assert (bilan.rattaches, bilan.departages_mb, bilan.ambigus) == (1, 1, 0)
        assert bilan.propositions == 1 and mb.appels == [album.upc]
        (parution,) = data_manager.get_track_releases(t.id)
        assert data_manager.etat_parution_apple(parution["id"])[0] == self.GRADUATION_EXPLICITE
        obs = data_manager.get_artist_observations(artiste.id)[t.id]
        (prop,) = [o for o in obs if o.field == CHAMP_APPLE_PROPOSE]
        assert prop.value == "1451901848"  # « Good Morning » de l'édition explicite

    def test_panne_musicbrainz_ne_date_rien(self, data_manager):
        artiste, t, resultats, album = self._scenario(data_manager)
        mb = _MB([], panne=ConnectionError("MusicBrainz saturé"))

        bilan = rattacher_parutions_apple(
            data_manager, artiste, [album], client=_ITunes(resultats), mb=mb
        )

        assert (bilan.rattaches, bilan.ambigus) == (0, 0) and len(bilan.erreurs) == 1
        (parution,) = data_manager.get_track_releases(t.id)
        assert data_manager.etat_parution_apple(parution["id"]) == (None, None)
