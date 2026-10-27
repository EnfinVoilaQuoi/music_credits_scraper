"""Table `track_videos` (e20) : l'écrivain dédié, l'additivité, la fusion.

Un morceau a souvent DEUX vidéos — le clip et la version « audio » du canal
« - Topic ». Les colonnes `tracks.youtube_*` n'en portaient qu'une ; ces tests
gèlent ce que la table apporte : deux vidéos coexistent, aucune passe n'efface
ce qu'une autre a trouvé, et une fusion de doublons les RÉUNIT.
"""

import pytest
from sqlalchemy import text

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

    def test_le_rejet_retire_la_duree_tiree_des_videos(self, data_manager):
        """Cause n°4 de l'audit (2026-10-13) : la durée `youtube_video` ne dit
        pas de quelle vidéo elle vient — elle part avec le rejet, et la colonne
        retombe sur une source indépendante (ici Deezer)."""
        artist = _artiste(data_manager)
        track = _morceau(data_manager, artist)
        data_manager.record_track_videos(track.id, [TrackVideo(video_id="aaaaaaaaaaa")])
        data_manager.record_duration_observation(track.id, 157, "youtube_video")
        data_manager.record_duration_observation(track.id, 200, "deezer")
        data_manager.forget_track_video(track.id, "aaaaaaaaaaa")
        (relue,) = [t for t in data_manager.get_artist_tracks(artist.id) if t.id == track.id]
        assert "youtube_video" not in (relue.durations_observees or {})
        assert relue.duration == 200

    def test_le_rejet_de_la_seule_source_vide_la_colonne(self, data_manager):
        """2026-10-27, Kanye « DJ Aktive Mix » : la vidéo (un skit de 18 s)
        était la seule source de la durée ; rejetée, la colonne gardait 18 s,
        qu'aucune observation n'expliquait plus (7 fiches de la règle
        `video_partagee` aussi). Le retrait ôte la provenance : la colonne suit."""
        artist = _artiste(data_manager)
        track = _morceau(data_manager, artist)
        data_manager.record_track_videos(track.id, [TrackVideo(video_id="aaaaaaaaaaa")])
        data_manager.record_duration_observation(track.id, 18, "youtube")
        (relue,) = [t for t in data_manager.get_artist_tracks(artist.id) if t.id == track.id]
        assert relue.duration == 18
        data_manager.forget_track_video(track.id, "aaaaaaaaaaa")
        (relue,) = [t for t in data_manager.get_artist_tracks(artist.id) if t.id == track.id]
        assert relue.duration is None

    def test_le_rejet_ne_vide_pas_une_duree_sans_provenance_video(self, data_manager):
        """Une colonne d'avant les observations (aucune) n'est pas l'affaire
        du rejet : il ne vide que ce que SES observations portaient."""
        artist = _artiste(data_manager)
        track = _morceau(data_manager, artist)
        data_manager.record_track_videos(track.id, [TrackVideo(video_id="aaaaaaaaaaa")])
        with data_manager.engine.begin() as conn:
            conn.execute(text("UPDATE tracks SET duration = 240 WHERE id = :t"), {"t": track.id})
        data_manager.forget_track_video(track.id, "aaaaaaaaaaa")
        (relue,) = [t for t in data_manager.get_artist_tracks(artist.id) if t.id == track.id]
        assert relue.duration == 240

    def test_oublier_une_video_absente_ne_touche_pas_la_duree(self, data_manager):
        artist = _artiste(data_manager)
        track = _morceau(data_manager, artist)
        data_manager.record_duration_observation(track.id, 157, "youtube_video")
        assert data_manager.forget_track_video(track.id, "bbbbbbbbbbb") is False
        (relue,) = [t for t in data_manager.get_artist_tracks(artist.id) if t.id == track.id]
        assert (relue.durations_observees or {}).get("youtube_video") == 157


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

    def test_la_fusion_fait_tomber_le_refus_d_une_regle(self, data_manager):
        """Médine (2026-10-13) : le YTM retire « 17-Oct » de « 17 Octobre » parce
        que la fiche « 17-Oct » la porte ; fusionnées, le motif disparaît — la
        vidéo passe, et le refus ne reste pas en invariant cassé."""
        from src.utils import corrections_fiches as cf

        artist = _artiste(data_manager)
        garde = _morceau(data_manager, artist, "17 Octobre")
        doublon = _morceau(data_manager, artist, "17-Oct")
        data_manager.record_track_videos(doublon.id, [TrackVideo(video_id="o" * 11, kind="audio")])
        cf.memoriser_video_refusee(garde, "o" * 11, origine="regle:autre_version")

        assert data_manager.merge_tracks(garde.id, doublon.id)

        assert [v.video_id for v in data_manager.get_track_videos(garde.id)] == ["o" * 11]
        assert cf.videos_refusees(garde) == set()

    def test_la_fusion_respecte_un_refus_a_la_main(self, data_manager):
        from src.utils import corrections_fiches as cf

        artist = _artiste(data_manager)
        garde = _morceau(data_manager, artist, "Déluge")
        doublon = _morceau(data_manager, artist, "Deluge")
        data_manager.record_track_videos(doublon.id, [TrackVideo(video_id="m" * 11, kind="audio")])
        cf.memoriser_video_refusee(garde, "m" * 11)

        assert data_manager.merge_tracks(garde.id, doublon.id)

        assert data_manager.get_track_videos(garde.id) == []
        assert cf.videos_refusees(garde) == {"m" * 11}


class TestVuesYtmApresFusion:
    """CORRECTIFS 1.4 (2026-10-09) : le total YTM de la fiche gardée se
    RECALCULE depuis les vidéos réunies, avec la règle du run YTM."""

    @staticmethod
    def _ytm(dm, track_id):
        with dm.engine.connect() as conn:
            return conn.exec_driver_sql(
                "SELECT ytm_streams FROM tracks WHERE id = ?", (track_id,)
            ).scalar()

    def _paire(self, dm):
        artist = _artiste(dm)
        return artist, _morceau(dm, artist, "Déluge"), _morceau(dm, artist, "Deluge")

    def test_clip_et_audio_differents_se_cumulent(self, data_manager):
        _, garde, doublon = self._paire(data_manager)
        data_manager.record_track_videos(garde.id, [TrackVideo(video_id="a" * 11, views=100)])
        data_manager.record_track_videos(doublon.id, [TrackVideo(video_id="b" * 11, views=50)])

        assert data_manager.merge_tracks(garde.id, doublon.id)

        assert self._ytm(data_manager, garde.id) == 150

    def test_meme_video_comptee_une_fois_au_releve_le_plus_recent(self, data_manager):
        _, garde, doublon = self._paire(data_manager)
        data_manager.record_track_videos(garde.id, [TrackVideo(video_id="a" * 11, views=100)])
        data_manager.record_track_videos(doublon.id, [TrackVideo(video_id="a" * 11, views=150)])

        assert data_manager.merge_tracks(garde.id, doublon.id)

        (video,) = data_manager.get_track_videos(garde.id)
        assert video.views == 150 and self._ytm(data_manager, garde.id) == 150

    def test_une_video_encore_portee_par_une_autre_fiche_ne_compte_pas(self, data_manager):
        """Clip double « 2h22 / Donjon » : non attribuable, comme dans le run."""
        artist, garde, doublon = self._paire(data_manager)
        tierce = _morceau(data_manager, artist, "Donjon")
        data_manager.record_track_videos(garde.id, [TrackVideo(video_id="a" * 11, views=100)])
        data_manager.record_track_videos(doublon.id, [TrackVideo(video_id="c" * 11, views=900)])
        data_manager.record_track_videos(tierce.id, [TrackVideo(video_id="c" * 11, views=900)])

        assert data_manager.merge_tracks(garde.id, doublon.id)

        assert self._ytm(data_manager, garde.id) == 100

    def test_un_total_sans_video_derriere_ne_l_emporte_plus(self, data_manager):
        """Décision utilisateur 2026-10-12 : un total YTM vaut TOUJOURS ce que
        ses faits expliquent. Josman « Boss » gardait 68 182 vues relevées avant
        e20 sans vidéo derrière, contre la vidéo mesurée de la fiche gardée."""
        _, garde, doublon = self._paire(data_manager)
        data_manager.record_track_videos(garde.id, [TrackVideo(video_id="a" * 11, views=66)])
        with data_manager.engine.begin() as conn:  # total d'avant e20, sans aucun fait
            conn.exec_driver_sql(
                "UPDATE tracks SET ytm_streams = 68182 WHERE id = ?", (doublon.id,)
            )

        assert data_manager.merge_tracks(garde.id, doublon.id)

        assert self._ytm(data_manager, garde.id) == 66

    def test_les_compteurs_sans_video_des_deux_fiches_sont_reunis(self, data_manager):
        """Chacun une fois : même clé (même piste d'album) = même compteur."""
        _, garde, doublon = self._paire(data_manager)
        data_manager.record_ytm_compteurs(garde.id, {"_novid_A_deluge": 66})
        data_manager.record_ytm_compteurs(
            doublon.id, {"_novid_A_deluge": 66, "_novid_B_deluge": 68182}
        )

        assert data_manager.merge_tracks(garde.id, doublon.id)

        assert self._ytm(data_manager, garde.id) == 66 + 68182

    def test_la_garde_sans_lien_reprend_la_video_principale(self, data_manager):
        _, garde, doublon = self._paire(data_manager)
        data_manager.update_track_youtube_url(
            doublon.id, "https://www.youtube.com/watch?v=" + "b" * 11, "genius_media"
        )

        assert data_manager.merge_tracks(garde.id, doublon.id)

        with data_manager.engine.connect() as conn:
            url, source = conn.exec_driver_sql(
                "SELECT youtube_url, youtube_url_source FROM tracks WHERE id = ?", (garde.id,)
            ).one()
        assert url.endswith("b" * 11) and source == "genius_media"


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


def test_la_fusion_comble_l_identite_sans_rien_remplacer(data_manager):
    """2026-10-10 : une fiche Deezer absorbée par sa page Genius emportait son
    `deezer_id` (23 fusions du lot 1.1)."""
    from sqlalchemy import text

    from src.models import Artist, Track

    a = Artist(name="A2H")
    a.id = data_manager.save_artist(a)
    page = Track(title="En attendant l'album (Episode 2)", artist=a, genius_id=336500)
    page.isrc = "FR6V82386863"
    deezer = Track(title="Home Sweet Home (Episode 2)", artist=a)
    deezer.deezer_id, deezer.isrc = 76807198, "AUTRE"
    for t in (page, deezer):
        data_manager.save_track(t)
    with data_manager.engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE tracks SET deezer_url = 'https://www.deezer.com/track/76807198' "
                "WHERE id = :i"
            ),
            {"i": deezer.id},
        )
    assert data_manager.merge_tracks(page.id, deezer.id)
    with data_manager.engine.connect() as conn:
        ligne = conn.execute(
            text("SELECT deezer_id, deezer_url, isrc, genius_id FROM tracks WHERE id = :i"),
            {"i": page.id},
        ).one()
    assert tuple(ligne) == (
        76807198,
        "https://www.deezer.com/track/76807198",
        "FR6V82386863",  # celui de la fiche gardée : rien n'est remplacé
        336500,
    )


def test_la_fusion_re_arbitre_la_duree(data_manager):
    """Flynt « Un pour la plume (Version équipe) » : la fiche Deezer absorbée
    apportait cinq observations de durée, la colonne restait vide."""
    from sqlalchemy import text

    from src.enrichment.observation import Observation
    from src.models import Artist, Track

    a = Artist(name="Flynt")
    a.id = data_manager.save_artist(a)
    page = Track(title="Un pour la plume (Version équipe)", artist=a, genius_id=1)
    deezer = Track(title="1 pour la plume (version équipe)", artist=a)
    for t in (page, deezer):
        data_manager.save_track(t)
    data_manager.record_discography_observations(
        deezer.id, [Observation("duration", 256, "deezer")]
    )
    assert data_manager.merge_tracks(page.id, deezer.id)
    with data_manager.engine.connect() as conn:
        assert (
            conn.execute(text("SELECT duration FROM tracks WHERE id = :i"), {"i": page.id}).scalar()
            == 256
        )


class TestCompteursYtmSansMesure:
    """Le run YTM compte aussi des pistes d'album SANS videoId et des vidéos sans
    `viewCount` exact (compteur arrondi d'ytmusicapi). Jusqu'au 2026-10-20, il
    écrivait son TOTAL, que le recalcul suivant (depuis `track_videos`)
    remplaçait : la valeur dépendait du dernier écrivain. Ces compteurs sont
    désormais des FAITS ; `ytm_streams` n'a plus qu'un calcul."""

    @staticmethod
    def _ytm(dm, track_id):
        with dm.engine.connect() as conn:
            return conn.exec_driver_sql(
                "SELECT ytm_streams FROM tracks WHERE id = ?", (track_id,)
            ).scalar()

    def test_un_recalcul_ulterieur_ne_perd_pas_le_compteur_du_run(self, data_manager):
        artist = _artiste(data_manager)
        track = _morceau(data_manager, artist)
        # Le run : une piste d'album sans videoId (1 000) + une vidéo mesurée (500).
        data_manager.record_ytm_compteurs(track.id, {"_novid_Alb_magot": 1000})
        data_manager.record_track_videos(track.id, [TrackVideo(video_id="a" * 11, views=500)])
        assert self._ytm(data_manager, track.id) == 1500
        # Une écriture sans rapport rematérialise la fiche : le total tient.
        data_manager.update_track_video_views(track.id, 500, kind="clip")
        assert self._ytm(data_manager, track.id) == 1500

    def test_la_mesure_exacte_remplace_le_compteur_arrondi(self, data_manager):
        artist = _artiste(data_manager)
        track = _morceau(data_manager, artist)
        data_manager.record_track_videos(track.id, [TrackVideo(video_id="a" * 11)])
        data_manager.record_ytm_compteurs(track.id, {"a" * 11: 1_200_000})
        assert self._ytm(data_manager, track.id) == 1_200_000

        data_manager.record_track_videos(track.id, [TrackVideo(video_id="a" * 11, views=1_234_567)])

        assert self._ytm(data_manager, track.id) == 1_234_567

    def test_une_video_oubliee_emporte_son_compteur_arrondi(self, data_manager):
        artist = _artiste(data_manager)
        track = _morceau(data_manager, artist)
        data_manager.record_track_videos(track.id, [TrackVideo(video_id="a" * 11)])
        data_manager.record_ytm_compteurs(track.id, {"a" * 11: 1_200_000})

        data_manager.forget_track_video(track.id, "a" * 11)

        assert self._ytm(data_manager, track.id) is None

    def test_une_video_partagee_ne_compte_pas_son_compteur_arrondi(self, data_manager):
        artist = _artiste(data_manager)
        track, autre = _morceau(data_manager, artist), _morceau(data_manager, artist, "Déluge")
        data_manager.record_track_videos(autre.id, [TrackVideo(video_id="a" * 11)])
        data_manager.record_track_videos(track.id, [TrackVideo(video_id="a" * 11)])

        data_manager.record_ytm_compteurs(track.id, {"a" * 11: 900})

        assert self._ytm(data_manager, track.id) is None

    def test_un_total_qu_aucun_fait_n_explique_est_vide(self, data_manager):
        """Diam's « A quand mon heure ? » (2026-10-20) gardait les 13 357 vues
        d'une vidéo refusée : sans vidéo mesurée restante, le recalcul était
        sauté."""
        artist = _artiste(data_manager)
        track = _morceau(data_manager, artist)
        data_manager.record_track_videos(track.id, [TrackVideo(video_id="r" * 11, views=13357)])
        data_manager.record_track_videos(track.id, [TrackVideo(video_id="m" * 11)])
        assert self._ytm(data_manager, track.id) == 13357

        data_manager.forget_track_video(track.id, "r" * 11)

        assert self._ytm(data_manager, track.id) is None

    def test_une_lecture_vide_retire_le_fait(self, data_manager):
        artist = _artiste(data_manager)
        track = _morceau(data_manager, artist)
        data_manager.record_ytm_compteurs(track.id, {"_novid_Alb_magot": 1000})

        assert data_manager.record_ytm_compteurs(track.id, {}) is True

        assert self._ytm(data_manager, track.id) is None
        assert not [
            o for o in data_manager.get_observations(track.id) if o.field == "ytm_compteurs"
        ]
