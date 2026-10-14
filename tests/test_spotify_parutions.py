"""Spotify par la parution : MusicBrainz (garde Deezer) puis la place (2026-10-15)."""

from pathlib import Path

from src.api.musicbrainz_api import albums_lies
from src.models import Artist, ReleaseObservation, Track
from src.scrapers import spotify_web_parse as parse
from src.services.ecarts_deezer import AlbumDeezer, PisteDeezer
from src.services.spotify_parutions import (
    album_spotify_garde,
    ids_par_place,
    relier_par_parution,
)
from tests.conftest import load_fixture_json


class TestGarde:
    def test_parution_qui_lie_notre_album_deezer(self):
        """Freeze Corleone *ADC* : la parution MusicBrainz réelle lie l'album
        Deezer 486213345 et un album Spotify."""
        liens = albums_lies(load_fixture_json("musicbrainz/release_rels_adc.json"))
        parutions = [{"mbid": "m", "albums": liens}]
        assert album_spotify_garde(parutions, 486213345) == liens["spotify"][0]

    def test_autre_edition_ecartee(self):
        """Les 5 erreurs mesurées : MusicBrainz lie une AUTRE édition Deezer."""
        parutions = [{"albums": {"deezer": ["783329841"], "spotify": ["SP"]}}]
        assert album_spotify_garde(parutions, 907179762) is None

    def test_deux_albums_spotify_on_ne_choisit_pas(self):
        parutions = [
            {"albums": {"deezer": ["1"], "spotify": ["A"]}},
            {"albums": {"deezer": ["1"], "spotify": ["B"]}},
        ]
        assert album_spotify_garde(parutions, 1) is None


def _p(pid, titre, disque, numero, duree=200):
    return PisteDeezer(id=pid, title=titre, disk=disque, position=numero, duration=duree)


def _page(pistes, annonce=None, durees=None):
    return {
        "tracks": pistes,
        "announced": len(pistes) if annonce is None else annonce,
        "durations": durees or {},
    }


class TestPlace:
    def test_ordre_spotify_aligne_sur_les_disques_deezer(self):
        """« Shonen », 11ᵉ ligne Spotify, est la piste 1 du disque 2 chez Deezer."""
        deezer = [_p(i, f"CP {i}", 1, i) for i in range(1, 11)] + [_p(11, "Shonen", 2, 1)]
        page = _page([(f"S{i}", f"CP {i}") for i in range(1, 11)] + [("S11", "Shonen")])
        alignes = ids_par_place(page, deezer)
        assert alignes[(2, 1)] == "S11" and alignes[(1, 1)] == "S1"

    def test_page_incomplete_inutilisable(self):
        """La page RÉELLE de *Civilisation Édition Ultime* : 23 rendues sur 25."""
        html = (Path(__file__).parent / "fixtures/spotify_web/album_incomplet.html").read_text(
            encoding="utf-8"
        )
        page = {
            "tracks": parse.parse_album_tracks(html),
            "announced": parse.parse_album_track_count(html),
            "durations": {},
        }
        deezer = [_p(i, f"T{i}", 1, i) for i in range(1, 26)]
        assert ids_par_place(page, deezer) is None

    def test_autre_nombre_de_pistes(self):
        assert ids_par_place(_page([("S1", "A")]), [_p(1, "A", 1, 1), _p(2, "B", 1, 2)]) is None

    def test_un_dementi_et_l_album_est_laisse(self):
        deezer = [_p(1, "A", 1, 1, 100), _p(2, "B", 1, 2, 100)]
        page = _page([("S1", "A"), ("S2", "Tout autre chose")], durees={"S2": 300})
        assert ids_par_place(page, deezer) is None

    def test_titre_autre_mais_meme_duree(self):
        deezer = [_p(1, "CP_001_Intro", 1, 1, 161)]
        page = _page([("S1", "Intro Civilisation Perdue")], durees={"S1": 162})
        assert ids_par_place(page, deezer) == {(1, 1): "S1"}


class _MB:
    def __init__(self, parutions):
        self.parutions, self.appels = parutions, []

    def parutions_par_code_barres(self, upc):
        self.appels.append(upc)
        return self.parutions


def _scenario(dm, *, fiche_spotify=None):
    artiste = Artist(name="Freeze Corleone")
    artiste.id = dm.save_artist(artiste)
    t = Track(title="Intro", artist=artiste)
    t.spotify_id = fiche_spotify
    t.id = dm.save_track(t)
    dm.record_release_observations(
        t.id,
        [
            ReleaseObservation(
                title="ADC",
                source="deezer",
                external_release_id=486213345,
                upc="3701421546227",
                disc_number=1,
                track_number=1,
            )
        ],
    )
    artiste.tracks = dm.get_artist_tracks(artiste.id)
    album = AlbumDeezer(
        id=486213345,
        title="ADC",
        upc="3701421546227",
        nb_tracks=1,
        pistes=[_p(1, "Intro", 1, 1, 120)],
    )
    mb = _MB([{"albums": {"deezer": ["486213345"], "spotify": ["ALB"]}}])
    return artiste, t, album, mb


def test_chaine_complete(data_manager):
    artiste, t, album, mb = _scenario(data_manager)
    lus, valides = [], []

    def lire(ids):
        lus.append(list(ids))
        return {"ALB": _page([("SPX", "Intro")], durees={"SPX": 120})}

    def valider(track, sid):
        valides.append((track.title, sid))
        return True

    bilan = relier_par_parution(
        data_manager, artiste, [album], mb=mb, lire_albums=lire, valider=valider
    )
    assert (bilan.albums_rattaches, bilan.ids_poses) == (1, 1)
    assert valides == [("Intro", "SPX")] and lus == [["ALB"]]
    (relu,) = data_manager.get_artist_tracks(artiste.id)
    assert relu.spotify_id == "SPX"
    (parution,) = data_manager.get_track_releases(t.id)
    assert data_manager.etat_parution_spotify(parution["id"])[0] == "ALB"

    # Rejoué : album connu (pas de MusicBrainz), aucune fiche sans ID (pas de page).
    artiste.tracks = data_manager.get_artist_tracks(artiste.id)
    relier_par_parution(data_manager, artiste, [album], mb=mb, lire_albums=lire, valider=valider)
    assert mb.appels == ["3701421546227"] and lus == [["ALB"]]


def test_le_gate_a_le_dernier_mot(data_manager):
    artiste, _, album, mb = _scenario(data_manager)
    bilan = relier_par_parution(
        data_manager,
        artiste,
        [album],
        mb=mb,
        lire_albums=lambda ids: {"ALB": _page([("SPX", "Intro")])},
        valider=lambda track, sid: False,
    )
    assert (bilan.ids_poses, bilan.ids_refuses_gate) == (0, 1)
    assert data_manager.get_artist_tracks(artiste.id)[0].spotify_id is None


def test_jamais_par_dessus_un_id_existant(data_manager):
    artiste, _, album, mb = _scenario(data_manager, fiche_spotify="DEJA")
    lus = []
    bilan = relier_par_parution(
        data_manager,
        artiste,
        [album],
        mb=mb,
        lire_albums=lambda ids: lus.append(ids) or {},
        valider=lambda track, sid: True,
    )
    assert bilan.albums_rattaches == 1 and lus == []  # aucune fiche à relier : pas de page
    assert data_manager.get_artist_tracks(artiste.id)[0].spotify_id == "DEJA"


def test_garde_ecarte_une_autre_edition(data_manager):
    artiste, t, album, _ = _scenario(data_manager)
    mb = _MB([{"albums": {"deezer": ["999"], "spotify": ["AUTRE"]}}])
    bilan = relier_par_parution(
        data_manager,
        artiste,
        [album],
        mb=mb,
        lire_albums=lambda ids: {},
        valider=lambda track, sid: True,
    )
    assert (bilan.albums_rattaches, bilan.albums_ecartes_garde) == (0, 1)
    (parution,) = data_manager.get_track_releases(t.id)
    album_sp, vu = data_manager.etat_parution_spotify(parution["id"])
    assert album_sp is None and vu is not None  # daté : pas redemandé avant expiration
