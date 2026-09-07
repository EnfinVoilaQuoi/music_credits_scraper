"""Table `track_videos` (e20) : l'écrivain dédié, l'additivité, la fusion.

Un morceau a souvent DEUX vidéos — le clip et la version « audio » du canal
« - Topic ». Les colonnes `tracks.youtube_*` n'en portaient qu'une ; ces tests
gèlent ce que la table apporte : deux vidéos coexistent, aucune passe n'efface
ce qu'une autre a trouvé, et une fusion de doublons les RÉUNIT.
"""

import pytest

from src.models import Artist, Track, TrackVideo
from src.utils.track_repository import source_lien_retenue


def _artiste(data_manager, name="Artiste Vidéo") -> Artist:
    artist = Artist(name=name)
    artist.id = data_manager.save_artist(artist)
    return artist


def _morceau(data_manager, artist, titre="Magot") -> Track:
    track = Track(title=titre, artist=artist)
    data_manager.save_track(track)
    return track


class TestSourceLienRetenue:
    """La priorité des provenances est une fonction PURE, partagée avec la
    colonne `tracks.youtube_url` — pas une règle recopiée dans deux magasins."""

    def test_aucune_ancienne_prend_la_nouvelle(self):
        assert source_lien_retenue(None, "search_auto") == "search_auto"

    def test_aucune_nouvelle_garde_lancienne(self):
        assert source_lien_retenue("genius_media", None) == "genius_media"

    @pytest.mark.parametrize("protegee", ["manual", "genius_media"])
    def test_une_provenance_protegee_ne_se_fait_pas_deloger_par_lauto(self, protegee):
        assert source_lien_retenue(protegee, "search_auto") == protegee
        assert source_lien_retenue(protegee, "ytm_album") == protegee

    def test_un_choix_manuel_corrige_genius(self):
        assert source_lien_retenue("genius_media", "manual") == "manual"

    def test_entre_deux_ordinaires_la_plus_recente_gagne(self):
        assert source_lien_retenue("ytm_album", "search_auto") == "search_auto"


class TestRecordTrackVideos:
    def test_deux_videos_coexistent_sur_un_morceau(self, data_manager):
        artist = _artiste(data_manager)
        track = _morceau(data_manager, artist)

        data_manager.record_track_videos(
            track.id,
            [
                TrackVideo(video_id="aaaaaaaaaaa", kind="clip", source="genius_media", views=100),
                TrackVideo(video_id="bbbbbbbbbbb", kind="audio", source="ytm_album", views=300),
            ],
        )

        videos = data_manager.get_track_videos(track.id)
        assert [v.video_id for v in videos] == ["bbbbbbbbbbb", "aaaaaaaaaaa"]  # + vues d'abord
        assert {v.kind for v in videos} == {"clip", "audio"}

    def test_une_seconde_passe_najoute_pas_de_doublon(self, data_manager):
        artist = _artiste(data_manager)
        track = _morceau(data_manager, artist)
        video = TrackVideo(video_id="aaaaaaaaaaa", source="genius_media")

        data_manager.record_track_videos(track.id, [video])
        data_manager.record_track_videos(track.id, [video])

        assert len(data_manager.get_track_videos(track.id)) == 1

    def test_une_passe_partielle_neffaces_pas_ce_que_lautre_a_trouve(self, data_manager):
        """La passe des vues ignore la provenance, celle des streams ignore les
        vues : un champ à None laisse la valeur en place."""
        artist = _artiste(data_manager)
        track = _morceau(data_manager, artist)

        data_manager.record_track_videos(
            track.id, [TrackVideo(video_id="aaaaaaaaaaa", url="u", source="genius_media")]
        )
        data_manager.record_track_videos(
            track.id, [TrackVideo(video_id="aaaaaaaaaaa", kind="clip", views=42)]
        )

        (lue,) = data_manager.get_track_videos(track.id)
        assert lue.source == "genius_media"
        assert lue.url == "u"
        assert lue.kind == "clip"
        assert lue.views == 42

    def test_les_vues_ne_sont_datees_que_quand_elles_sont_ecrites(self, data_manager):
        """Sinon une passe de streams daterait d'aujourd'hui des vues qu'elle n'a
        pas relevées — la fraîcheur affichée mentirait."""
        artist = _artiste(data_manager)
        track = _morceau(data_manager, artist)

        data_manager.record_track_videos(track.id, [TrackVideo(video_id="aaaaaaaaaaa")])
        (sans_vues,) = data_manager.get_track_videos(track.id)
        assert sans_vues.views_updated is None

        data_manager.record_track_videos(track.id, [TrackVideo(video_id="aaaaaaaaaaa", views=7)])
        (avec_vues,) = data_manager.get_track_videos(track.id)
        assert avec_vues.views_updated is not None

        # Une passe sans vues ne remet PAS la date à zéro.
        data_manager.record_track_videos(
            track.id, [TrackVideo(video_id="aaaaaaaaaaa", kind="clip")]
        )
        (apres,) = data_manager.get_track_videos(track.id)
        assert apres.views_updated == avec_vues.views_updated

    def test_une_video_sans_identifiant_est_ignoree(self, data_manager):
        artist = _artiste(data_manager)
        track = _morceau(data_manager, artist)
        assert data_manager.record_track_videos(track.id, [TrackVideo(video_id="")]) == 0
        assert data_manager.get_track_videos(track.id) == []

    def test_forget_retire_la_video(self, data_manager):
        artist = _artiste(data_manager)
        track = _morceau(data_manager, artist)
        data_manager.record_track_videos(track.id, [TrackVideo(video_id="aaaaaaaaaaa")])

        assert data_manager.forget_track_video(track.id, "aaaaaaaaaaa") is True
        assert data_manager.get_track_videos(track.id) == []
        # Rejouer ne rend pas True : il n'y avait plus rien à retirer.
        assert data_manager.forget_track_video(track.id, "aaaaaaaaaaa") is False


class TestLectureAvecLesMorceaux:
    def test_get_artist_tracks_peuple_videos(self, data_manager):
        artist = _artiste(data_manager)
        track = _morceau(data_manager, artist)
        data_manager.record_track_videos(
            track.id, [TrackVideo(video_id="aaaaaaaaaaa", kind="clip")]
        )

        (lu,) = data_manager.get_artist_tracks(artist.id)
        assert [v.video_id for v in lu.videos] == ["aaaaaaaaaaa"]

    def test_un_morceau_sans_video_a_une_liste_vide(self, data_manager):
        artist = _artiste(data_manager)
        _morceau(data_manager, artist)
        (lu,) = data_manager.get_artist_tracks(artist.id)
        assert lu.videos == []


class TestSuppressionEtFusion:
    def test_delete_track_emporte_ses_videos(self, data_manager):
        artist = _artiste(data_manager)
        track = _morceau(data_manager, artist)
        data_manager.record_track_videos(track.id, [TrackVideo(video_id="aaaaaaaaaaa")])

        data_manager.delete_track(track.id)

        with data_manager.engine.connect() as conn:
            restantes = conn.exec_driver_sql(
                "SELECT COUNT(*) FROM track_videos WHERE track_id = ?", (track.id,)
            ).scalar()
        assert restantes == 0

    def test_la_fusion_reunit_les_videos_des_deux_doublons(self, data_manager):
        """Deux doublons portent souvent chacun un lien DIFFÉRENT : faire choisir
        jetterait les vues de l'autre. C'est la vérification demandée au plan."""
        artist = _artiste(data_manager)
        garde = _morceau(data_manager, artist, "Déluge")
        doublon = _morceau(data_manager, artist, "Deluge")
        data_manager.record_track_videos(
            garde.id, [TrackVideo(video_id="aaaaaaaaaaa", kind="clip")]
        )
        data_manager.record_track_videos(
            doublon.id, [TrackVideo(video_id="bbbbbbbbbbb", kind="audio")]
        )

        assert data_manager.merge_tracks(garde.id, doublon.id)

        assert sorted(v.video_id for v in data_manager.get_track_videos(garde.id)) == [
            "aaaaaaaaaaa",
            "bbbbbbbbbbb",
        ]

    def test_la_fusion_ne_duplique_pas_une_video_commune(self, data_manager):
        artist = _artiste(data_manager)
        garde = _morceau(data_manager, artist, "Déluge")
        doublon = _morceau(data_manager, artist, "Deluge")
        commune = TrackVideo(video_id="aaaaaaaaaaa", kind="clip")
        data_manager.record_track_videos(garde.id, [commune])
        data_manager.record_track_videos(doublon.id, [commune])

        assert data_manager.merge_tracks(garde.id, doublon.id)

        assert len(data_manager.get_track_videos(garde.id)) == 1


class TestTitreDeLaVideo:
    """e21 — le titre est ce qui rend VÉRIFIABLE une vidéo partagée : « Donjon
    & 2h22 » est un clip double légitime, un titre qui ne nomme qu'un morceau
    trahit un lien fautif."""

    def test_aller_retour(self, data_manager):
        artist = _artiste(data_manager)
        track = _morceau(data_manager, artist)
        data_manager.record_track_videos(
            track.id,
            [TrackVideo(video_id="aaaaaaaaaaa", title="B.B. Jacques - Donjon & 2h22")],
        )
        (lue,) = data_manager.get_track_videos(track.id)
        assert lue.title == "B.B. Jacques - Donjon & 2h22"

    def test_une_passe_sans_titre_ne_lefface_pas(self, data_manager):
        """La passe des streams ne connaît pas les titres : elle ne doit pas
        effacer ce que la passe des vues a relevé."""
        artist = _artiste(data_manager)
        track = _morceau(data_manager, artist)
        data_manager.record_track_videos(
            track.id, [TrackVideo(video_id="aaaaaaaaaaa", title="Donjon & 2h22")]
        )
        data_manager.record_track_videos(track.id, [TrackVideo(video_id="aaaaaaaaaaa", views=5)])
        (lue,) = data_manager.get_track_videos(track.id)
        assert (lue.title, lue.views) == ("Donjon & 2h22", 5)


class TestTitresInvisibles:
    """`save_track` est le point de passage UNIQUE d'un titre vers la base : le
    nettoyage y est posé, ce qui rend le doublon IMPOSSIBLE plutôt que réparable."""

    def test_un_titre_pollue_est_enregistre_propre(self, data_manager):
        artist = _artiste(data_manager)
        track = Track(title="\u200bbank", artist=artist)
        data_manager.save_track(track)

        (lu,) = data_manager.get_artist_tracks(artist.id)
        assert lu.title == "bank"

    def test_lobjet_en_memoire_est_corrige_lui_aussi(self, data_manager):
        """Sans ça, l'objet et la base divergent jusqu'au prochain rechargement."""
        artist = _artiste(data_manager)
        track = Track(title="\u200bbank", artist=artist)
        data_manager.save_track(track)
        assert track.title == "bank"

    def test_la_variante_polluee_retombe_sur_la_fiche_existante(self, data_manager):
        """LE point : c'est ainsi que le doublon Josman ne peut plus naître."""
        artist = _artiste(data_manager)
        data_manager.save_track(Track(title="bank", artist=artist))
        data_manager.save_track(Track(title="\u200b\u200bbank", artist=artist))

        tracks = data_manager.get_artist_tracks(artist.id)
        assert [t.title for t in tracks] == ["bank"]

    def test_rename_track_nettoie_aussi(self, data_manager):
        """Un titre collé depuis Genius emporte volontiers un invisible."""
        artist = _artiste(data_manager)
        track = Track(title="ancien", artist=artist)
        data_manager.save_track(track)

        assert data_manager.rename_track(track.id, "\u200bbank") is True
        (lu,) = data_manager.get_artist_tracks(artist.id)
        assert lu.title == "bank"
