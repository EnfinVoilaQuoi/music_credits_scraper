"""Le morceau d'un autre sur un disque que l'artiste SIGNE compte dans ses totaux, sans
le présenter comme interprète ; sur le disque d'un autre, non (décision 2026-11-07)."""

import json
import sqlite3
import zlib
from contextlib import closing

import pytest

from src.enrichment.observation import Observation
from src.models import Artist, ReleaseObservation, Track
from src.presentation.filtres import Filtre
from src.presentation.table import LIBELLE_PARTICIPATION
from src.utils.certification_enricher import _qui_chercher
from src.utils.participation import (
    Participation,
    compte_comme_sien,
    participation,
    signe_son_disque,
)
from src.utils.streams_calculator import morceaux_comptes


class TestSigneSonDisque:
    @pytest.mark.parametrize(
        "credit, attendu",
        [
            ("Vald", True),
            ("ISHA", True),
            ("Lucio Bukowski, Oster Lapwass & Anton Serra", True),
            ("Dr. Dre", False),
            # Nommé APRÈS le collectif : son disque à lui reste celui du collectif.
            ("JACKBOYS & Travis Scott", False),
            ("Clinton Sparks, Vald", False),
            ("Multi-interprètes", False),
            (None, False),
        ],
    )
    def test_la_tete_du_credit_seule(self, credit, attendu):
        noms = ("Vald", "ISHA", "Lucio Bukowski", "Travis Scott")
        assert signe_son_disque([("Un disque", credit)], noms) is attendu

    def test_un_alias_confirme_vaut_le_nom(self):
        assert signe_son_disque([("Graduation", "Ye")], ("Kanye West", "Ye"))

    def test_autre_mot_que_le_nom_entier_refuse(self):
        assert not signe_son_disque([("Disque", "Misha Van Der Werf")], ("Isha",))

    def test_la_compilation_presentee_n_est_pas_son_disque(self):
        noms = ("Eminem",)
        assert not signe_son_disque([("Eminem Presents the Re-Up", "Eminem")], noms)
        assert signe_son_disque([("Encore", "Eminem")], noms)

    def test_une_seule_signature_suffit(self):
        signatures = [("...", "Mani Deiz"), ("...", "Lucio Bukowski")]
        assert signe_son_disque(signatures, ("Lucio Bukowski",))


def _fiche(principal="Sirius", signatures=()):
    t = Track(title="Champignon", is_featuring=False)
    t.primary_artist_name = principal
    t.signatures_disque = list(signatures)
    return t


class TestParticipation:
    NOMS = ("Vald",)

    def test_sur_son_disque_compte_sur_le_disque_d_un_autre_non(self):
        sien = _fiche(signatures=[("NQNT33", "Vald")])
        autre = _fiche(signatures=[("Confused?", "Dr. Dre")])
        assert participation(sien, self.NOMS) is Participation.SUR_SON_DISQUE
        assert participation(autre, self.NOMS) is Participation.SUR_LE_DISQUE
        assert morceaux_comptes([sien, autre], self.NOMS) == [sien]

    def test_sans_signature_la_fiche_reste_hors_des_totaux(self):
        assert participation(_fiche(), self.NOMS) is Participation.SUR_LE_DISQUE

    def test_son_propre_morceau_reste_principal(self):
        t = _fiche(principal="Vald", signatures=[("NQNT33", "Vald")])
        assert participation(t, self.NOMS) is Participation.PRINCIPAL

    def test_compte_mais_sans_certif_ni_interprete(self):
        assert compte_comme_sien(Participation.SUR_SON_DISQUE)
        t = _fiche(signatures=[("NQNT33", "Vald")])
        assert _qui_chercher(t, "Vald")[0] is None
        assert "sans y interpréter" in LIBELLE_PARTICIPATION[Participation.SUR_SON_DISQUE]

    def test_filtre_avec_les_morceaux_de_l_artiste(self):
        assert not Filtre(principal=False).categorie_visible(Participation.SUR_SON_DISQUE)
        assert Filtre(secondaire=False).categorie_visible(Participation.SUR_SON_DISQUE)


def _base(dm, nom="Vald"):
    a = Artist(name=nom)
    a.id = dm.save_artist(a)
    t = Track(title="Champignon", artist=a, genius_id=11, is_featuring=False)
    t.primary_artist_name = "Sirius"
    t.id = dm.save_track(t)
    return a, t


class TestChargement:
    def test_la_parution_creditee_signe(self, data_manager):
        a, t = _base(data_manager)
        data_manager.record_release_observations(
            t.id,
            [
                ReleaseObservation(
                    title="NQNT33",
                    source="deezer",
                    external_release_id=1,
                    external_track_id=2,
                    credited_artist_name="Vald",
                    scope="own",
                    confidence="identified",
                )
            ],
        )
        fiche = data_manager.get_artist_tracks(a.id)[0]
        assert ("NQNT33", "Vald") in fiche.signatures_disque
        assert participation(fiche, ("Vald",)) is Participation.SUR_SON_DISQUE

    def test_l_album_genius_signe(self, data_manager):
        a, t = _base(data_manager)
        data_manager.upsert_observations(t.id, [Observation("album_artist", "Vald", "genius")])
        fiche = data_manager.get_artist_tracks(a.id)[0]
        assert participation(fiche, ("Vald",)) is Participation.SUR_SON_DISQUE

    def test_une_signature_ne_touche_aucune_colonne(self, data_manager):
        a, t = _base(data_manager)
        avant = data_manager.get_artist_tracks(a.id)[0]
        data_manager.upsert_observations(t.id, [Observation("album_artist", "Vald", "genius")])
        apres = data_manager.get_artist_tracks(a.id)[0]
        assert (avant.album, avant.duration, avant.isrc) == (
            apres.album,
            apres.duration,
            apres.isrc,
        )


class TestAudit:
    def test_la_famille_ne_montre_que_ce_qui_sort_des_totaux(self, data_manager):
        from src.services.audit import _pistes, fiches_sans_participation

        a, t = _base(data_manager)
        with closing(sqlite3.connect(data_manager.db_path)) as c:
            c.row_factory = sqlite3.Row
            assert [k.pistes for k in fiches_sans_participation(c, _pistes(c, None))] == [[t.id]]
        data_manager.upsert_observations(t.id, [Observation("album_artist", "Vald", "genius")])
        with closing(sqlite3.connect(data_manager.db_path)) as c:
            c.row_factory = sqlite3.Row
            assert fiches_sans_participation(c, _pistes(c, None)) == []


class TestLecture:
    def test_la_page_lue_note_qui_signe_l_album(self):
        from src.api.genius_api import GeniusAPI

        t = Track(title="X")
        GeniusAPI.noter_signature_album(t, {"name": "NQNT33", "artist": {"name": "Vald"}})
        GeniusAPI.noter_signature_album(t, {"name": "Sans artiste"})
        GeniusAPI.noter_signature_album(t, None)
        assert [(o.field, o.value, o.source) for o in t.observations] == [
            ("album_artist", "Vald", "genius")
        ]

    def test_une_piste_de_tracklist_aussi(self):
        from src.services.tracklists_genius import piste_depuis_tracklist

        a = Artist(name="MC Solaar", genius_id=5, id=1)
        entree = {
            "number": 1,
            "song": {
                "id": 9,
                "title": "Aubade",
                "url": "u",
                "primary_artist": {"id": 6, "name": "Jimmy Jay"},
                "primary_artists": [{"id": 6, "name": "Jimmy Jay"}],
                "featured_artists": [],
            },
        }
        album = {"id": 3, "name": "Prose Combat", "artist_name": "MC Solaar"}
        t = piste_depuis_tracklist(entree, album, a)
        assert ("album_artist", "MC Solaar") in [(o.field, o.value) for o in t.observations]


def _brut(chemin, lectures):
    with closing(sqlite3.connect(chemin)) as c:
        c.execute(
            "CREATE TABLE brut (id INTEGER PRIMARY KEY, source TEXT, requete TEXT, corps BLOB, "
            "premiere_lecture TEXT, derniere_lecture TEXT)"
        )
        for requete, song in lectures:
            corps = zlib.compress(json.dumps({"response": {"song": song}}).encode())
            c.execute(
                "INSERT INTO brut (source, requete, corps, premiere_lecture, derniere_lecture) "
                "VALUES ('genius_api', ?, ?, 'a', 'a')",
                (requete, corps),
            )
        c.commit()


class TestRattrapage:
    def test_signatures_du_brut_la_derniere_gagne(self):
        import scripts.signer_albums_genius as script

        def corps(nom):
            song = {"album": {"id": 3, "artist": {"name": nom}}}
            return json.dumps({"response": {"song": song}}).encode()

        pages, albums = script.signatures_du_brut(
            [
                ("https://api.genius.com/songs/11", corps("Ancien")),
                ("https://api.genius.com/songs/11?text_format=plain", corps("Vald")),
                ("https://api.genius.com/albums/3/tracks?page=1", corps("Tracklist")),
                ("https://api.genius.com/songs/12", b"illisible"),
            ]
        )
        assert pages == {11: "Vald"} and albums == {3: "Vald"}

    def test_a_blanc_puis_applique_puis_la_fiche_ne_ressort_plus(
        self, data_manager, tmp_path, capsys
    ):
        import scripts.signer_albums_genius as script

        a, t = _base(data_manager)
        magasin = tmp_path / "brut.db"
        song = {"album": {"id": 3, "artist": {"name": "Vald"}}}
        _brut(magasin, [("https://api.genius.com/songs/11", song)])
        args = ["--base", str(data_manager.db_path), "--brut", str(magasin)]

        assert script.main(args) == 0
        assert "1 signatures à noter" in capsys.readouterr().out
        fiche = data_manager.get_artist_tracks(a.id)[0]
        assert participation(fiche, ("Vald",)) is Participation.SUR_LE_DISQUE

        assert script.main([*args, "--apply"]) == 0
        fiche = data_manager.get_artist_tracks(a.id)[0]
        assert participation(fiche, ("Vald",)) is Participation.SUR_SON_DISQUE

        assert script.main(args) == 0
        assert "0 signatures à noter sur 0 fiches" in capsys.readouterr().out

    def test_sans_trace_dans_le_brut_rien_n_est_ecrit(self, data_manager, tmp_path, capsys):
        import scripts.signer_albums_genius as script

        a, _t = _base(data_manager)
        magasin = tmp_path / "brut.db"
        _brut(magasin, [])
        args = ["--base", str(data_manager.db_path), "--brut", str(magasin), "--apply"]
        assert script.main(args) == 0
        assert "inconnu" in capsys.readouterr().out
        assert data_manager.get_artist_tracks(a.id)[0].signatures_disque == []
