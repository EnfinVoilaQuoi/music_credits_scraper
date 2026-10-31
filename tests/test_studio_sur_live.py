"""Une piste d'album LIVE n'est jamais l'enregistrement d'une fiche STUDIO (e64).

Cas d'origine, réparé à la main le 2026-10-31 : MC Solaar « Les temps
changent » (#4190, studio, *Paradisiaque*, 197 s) portait les ID Deezer
(249495), Apple (142884862) et Spotify du live de *Le tour de la question*
(219 s). Le titre Deezer du disque ne dit rien ; MusicBrainz, si (type
secondaire « Live » du release-group), iTunes aussi (« Le tour de la question
(live) »). Le juge (`studio_sur_disque_live`) sert la pose — gate Deezer,
Spotify et Apple par la place — et la famille d'audit `studio_sur_live`.
"""

import pytest

from src.api.musicbrainz_api import est_live
from src.models import Artist, Track
from src.services import audit as au
from src.services.apple_parutions import propositions_par_place
from src.utils import deezer_identity, parutions_live
from src.utils.deezer_identity import hit_concorde
from src.utils.version_descriptors import prise_live, studio_sur_disque_live

TOUR = "Le tour de la question"


class TestJuge:
    @pytest.mark.parametrize(
        ("titre", "attendu"),
        [
            ("Le tour de la question (live)", True),
            ("Les temps changent (Live)", True),
            ("Jesus Walks - Live Version", True),
            ("Les temps changent", False),
            ("Paradisiaque", False),
            ("Alive", False),
        ],
    )
    def test_prise_live(self, titre, attendu):
        assert prise_live(titre) is attendu

    def test_la_fiche_studio_sur_le_live(self):
        assert studio_sur_disque_live("Les temps changent", "Paradisiaque", TOUR, live=True)
        # Le nom iTunes suffit, sans MusicBrainz.
        assert studio_sur_disque_live("Les temps changent", "Paradisiaque", f"{TOUR} (live)")

    def test_pas_un_disque_live(self):
        assert not studio_sur_disque_live("Les temps changent", "Paradisiaque", TOUR)

    def test_la_page_genius_du_live_en_est_une(self):
        """« Galaktika » : Genius la range sur le disque live lui-même."""
        assert not studio_sur_disque_live("Galaktika", TOUR, TOUR, live=True)
        assert not studio_sur_disque_live(
            "Dans le désert", "Au tour de ma bulle", "Au Tour De Ma Bulle", live=True
        )

    def test_une_version_n_est_pas_studio(self):
        assert not studio_sur_disque_live("Les temps changent (Live)", None, TOUR, live=True)


class TestMusicBrainz:
    def test_type_secondaire_live(self):
        assert est_live({"release-group": {"primary-type": "Album", "secondary-types": ["Live"]}})
        assert not est_live({"release-group": {"primary-type": "Album"}})
        assert not est_live({})

    def test_relu_dans_le_brut(self):
        recherche = (
            b'{"releases": [{"barcode": "3700368423851", "release-group": '
            b'{"secondary-types": ["Live"]}}, {"barcode": "602435990590", "release-group": {}}]}'
        )
        live = parutions_live.live_par_code_barres(
            [("https://musicbrainz.org/ws/2/release/?query=barcode%3A3700368423851", recherche)]
        )
        assert live == {"3700368423851": True, "602435990590": False}
        albums = parutions_live.upc_des_albums_deezer(
            [
                ("https://api.deezer.com/album/43852", b'{"upc": "3700368423851"}'),
                ("https://api.deezer.com/album/43852/tracks", b'{"data": []}'),
            ]
        )
        assert albums == {43852: "3700368423851"}
        parutions = [
            {"id": 1, "upc": None, "deezer_album_ids": {43852}, "mb_live": None},
            {"id": 2, "upc": "602435990590", "deezer_album_ids": set(), "mb_live": None},
            {"id": 3, "upc": "602435990590", "deezer_album_ids": set(), "mb_live": False},
            {"id": 4, "upc": "000", "deezer_album_ids": set(), "mb_live": None},
        ]
        assert parutions_live.a_marquer(parutions, live, albums) == {1: True, 2: False}


def _hit(album_id=43852, album=TOUR, duration=219):
    return {
        "id": 249495,
        "title": "Les temps changent",
        "artist": {"id": 1, "name": "MC Solaar"},
        "album": {"id": album_id, "title": album},
        "duration": duration,
    }


class TestGateDeezer:
    def _juger(self, hit, title="Les temps changent", album="Paradisiaque"):
        return hit_concorde(hit, artist_name="MC Solaar", title=title, album=album)

    def test_album_live_selon_musicbrainz_refuse(self, monkeypatch):
        monkeypatch.setattr(deezer_identity, "fournisseur_albums_live", lambda: {43852})
        ok, motif = self._juger(_hit())
        assert not ok and "live" in motif

    def test_album_live_par_son_titre_refuse(self):
        ok, _ = self._juger(_hit(album=f"{TOUR} (Live)"))
        assert not ok

    def test_sans_preuve_du_live_le_gate_juge_comme_avant(self):
        assert self._juger(_hit())[0]

    def test_la_fiche_du_live_accepte(self, monkeypatch):
        monkeypatch.setattr(deezer_identity, "fournisseur_albums_live", lambda: {43852})
        hit = {**_hit(), "title": "Galaktika"}
        assert self._juger(hit, title="Galaktika", album=TOUR)[0]


class TestApplePropositions:
    def test_rien_pour_une_fiche_studio(self):
        fiche = Track(title="Les temps changent", artist=Artist(name="MC Solaar"))
        fiche.album, fiche.duration = "Paradisiaque", 197
        piste = {
            "trackId": 142884862,
            "discNumber": 1,
            "trackNumber": 18,
            "trackName": "Les temps changent",
            "trackTimeMillis": 219000,
        }
        places = {1: (1, 18)}
        assert propositions_par_place(places, [piste], {1: fiche}) == {1: "142884862"}
        assert propositions_par_place(places, [piste], {1: fiche}, live=True) == {}
        assert (
            propositions_par_place(places, [piste], {1: fiche}, album_apple=f"{TOUR} (live)") == {}
        )


class TestAudit:
    def _base(self, data_manager):
        a = Artist(name="MC Solaar")
        a.id = data_manager.save_artist(a)
        studio = Track(title="Les temps changent", artist=a)
        studio.album, studio.genius_id = "Paradisiaque", 1
        live = Track(title="Galaktika", artist=a)
        live.album, live.genius_id = TOUR, 2
        for t in (studio, live):
            data_manager.save_track(t)
        rid = data_manager.ensure_release(a.id, TOUR, deezer_album_id=43852)
        for t in (studio, live):
            data_manager.link_track_to_release(rid, t.id, source="deezer", matched_by="identified")
        return studio, rid

    def _cas(self, dm):
        return [c for c in au.auditer(dm, revue=False).cas if c.famille == "studio_sur_live"]

    def test_la_fiche_studio_sur_le_live_est_montree(self, data_manager):
        studio, rid = self._base(data_manager)
        assert self._cas(data_manager) == []
        assert not data_manager.parution_live(rid)
        assert data_manager.marquer_parutions_live({rid: True}) == 1
        assert data_manager.marquer_parutions_live({rid: True}) == 0
        assert data_manager.parution_live(rid)
        assert data_manager.albums_deezer_live() == {43852}
        (cas,) = self._cas(data_manager)
        assert cas.pistes == [studio.id] and "MusicBrainz" in cas.libelle
        assert "studio_sur_live" in au.FAMILLES and "studio_sur_live" not in au.INVARIANTS

    def test_noter_la_recherche_pose_le_live(self, data_manager):
        _, rid = self._base(data_manager)
        data_manager.noter_parution_musicbrainz(rid, 1, None, live=True)
        assert data_manager.parution_live(rid)
        data_manager.noter_parution_musicbrainz(rid, 1, None)
        assert data_manager.parution_live(rid)
