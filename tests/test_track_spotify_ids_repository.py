"""Table `track_spotify_ids` (e23) : l'écrivain dédié, l'additivité, la fusion.

Un morceau a couramment PLUSIEURS identifiants Spotify — le single et l'album,
une réédition. Le pluriel était déjà écrit et testé côté objet
(`Track.spotify_ids`, `add_spotify_id`) et affiché par la GUI (« (N versions) »),
mais aucune table ne le portait : la liste mourait au `save_track`, et
« accepter un ID alternatif » dégénérait en « écraser celui de l'autre ligne ».

Ces tests gèlent ce que la table apporte : deux IDs coexistent, aucune passe
n'efface ce qu'une autre a trouvé, une fusion de doublons les RÉUNIT, et — le
plus important — **on ne somme jamais deux éditions** (elles portent les mêmes
compteurs, cf. JOURNAL 2026-09-05 : 99 M au lieu de 50 M sur « Bitume Caviar »).
"""

from src.models import Artist, Track, TrackSpotifyId

_ID_SINGLE = "3VXzVGAWFSrH47dBtTOPws"
_ID_ALBUM = "5go793BaOjzfop2JwctQ0r"


def _artiste(data_manager, name="Artiste Spotify") -> Artist:
    artist = Artist(name=name)
    artist.id = data_manager.save_artist(artist)
    return artist


def _morceau(data_manager, artist, titre="Magot", spotify_id=None) -> Track:
    track = Track(title=titre, artist=artist)
    track.spotify_id = spotify_id
    data_manager.save_track(track)
    return track


class TestRecordTrackSpotifyIds:
    def test_deux_ids_coexistent_sur_un_morceau(self, data_manager):
        artist = _artiste(data_manager)
        track = _morceau(data_manager, artist, spotify_id=_ID_ALBUM)

        data_manager.record_track_spotify_ids(
            track.id,
            [
                TrackSpotifyId(spotify_id=_ID_SINGLE, source="scraper"),
                TrackSpotifyId(spotify_id=_ID_ALBUM, source="genius_media"),
            ],
        )

        entrees = data_manager.get_track_spotify_ids(track.id)
        assert [e.spotify_id for e in entrees] == [_ID_ALBUM, _ID_SINGLE]  # principal d'abord
        assert {e.source for e in entrees} == {"scraper", "genius_media"}

    def test_le_principal_recopie_la_colonne(self, data_manager):
        """`is_primary` ne tranche pas de son côté : il matérialise
        `tracks.spotify_id`, seul verdict de « l'ID qu'on ouvre »."""
        artist = _artiste(data_manager)
        track = _morceau(data_manager, artist, spotify_id=_ID_ALBUM)

        data_manager.record_track_spotify_ids(
            track.id,
            [
                TrackSpotifyId(spotify_id=_ID_SINGLE, source="scraper"),
                TrackSpotifyId(spotify_id=_ID_ALBUM, source="genius_media"),
            ],
        )

        entrees = {e.spotify_id: e for e in data_manager.get_track_spotify_ids(track.id)}
        assert entrees[_ID_ALBUM].is_primary is True
        assert entrees[_ID_SINGLE].is_primary is False

    def test_le_drapeau_suit_la_colonne_sur_TOUTES_les_lignes(self, data_manager):
        """`is_primary` recopie la colonne : il la suit partout, pas seulement
        sur les lignes que la passe courante écrit.

        Sans cette re-synchronisation, une ligne marquée principale survit à un
        changement de colonne et devient un second verdict contredisant le
        premier — 19 cas constatés sur la base réelle le 2026-09-08.
        """
        artist = _artiste(data_manager)
        track = _morceau(data_manager, artist, spotify_id=_ID_SINGLE)
        data_manager.record_track_spotify_ids(
            track.id, [TrackSpotifyId(spotify_id=_ID_SINGLE, source="kworb")]
        )
        assert data_manager.get_track_spotify_ids(track.id)[0].is_primary is True

        # La colonne change — par n'importe quelle voie. Elle est modifiée ici
        # en SQL direct, et c'est délibéré : depuis le lot A-ter, `save_track`
        # ne PEUT plus la remplacer (« le premier renseigne, personne ne
        # remplace »). L'invariant testé n'est pas « save_track se comporte
        # bien », c'est « le drapeau suit la colonne, quelle qu'en soit la
        # cause » — il doit tenir pour les voies qui restent, et pour celles
        # qu'on écrira demain.
        from sqlalchemy import text

        with data_manager.engine.begin() as conn:
            conn.execute(
                text("UPDATE tracks SET spotify_id = :sid WHERE id = :tid"),
                {"sid": _ID_ALBUM, "tid": track.id},
            )
        data_manager.record_track_spotify_ids(
            track.id, [TrackSpotifyId(spotify_id=_ID_ALBUM, source="genius_media")]
        )

        etats = {e.spotify_id: e.is_primary for e in data_manager.get_track_spotify_ids(track.id)}
        assert etats == {_ID_ALBUM: True, _ID_SINGLE: False}

    def test_une_seconde_passe_najoute_pas_de_doublon(self, data_manager):
        artist = _artiste(data_manager)
        track = _morceau(data_manager, artist)
        entree = TrackSpotifyId(spotify_id=_ID_SINGLE, source="scraper")

        data_manager.record_track_spotify_ids(track.id, [entree])
        data_manager.record_track_spotify_ids(track.id, [entree])

        assert len(data_manager.get_track_spotify_ids(track.id)) == 1

    def test_une_passe_muette_neface_pas_ce_quune_autre_a_trouve(self, data_manager):
        """Additivité : aucun producteur ne connaît la liste complète — Genius
        en donne un, Kworb un autre. Une écriture autoritative ferait perdre à
        chaque passe ce que les autres ont vu."""
        artist = _artiste(data_manager)
        track = _morceau(data_manager, artist)

        data_manager.record_track_spotify_ids(
            track.id, [TrackSpotifyId(spotify_id=_ID_SINGLE, source="genius_media")]
        )
        data_manager.record_track_spotify_ids(
            track.id, [TrackSpotifyId(spotify_id=_ID_ALBUM, source="kworb")]
        )

        assert {e.spotify_id for e in data_manager.get_track_spotify_ids(track.id)} == {
            _ID_SINGLE,
            _ID_ALBUM,
        }

    def test_une_provenance_connue_ne_se_fait_pas_effacer_par_un_none(self, data_manager):
        artist = _artiste(data_manager)
        track = _morceau(data_manager, artist)

        data_manager.record_track_spotify_ids(
            track.id, [TrackSpotifyId(spotify_id=_ID_SINGLE, source="genius_media")]
        )
        data_manager.record_track_spotify_ids(
            track.id, [TrackSpotifyId(spotify_id=_ID_SINGLE, source=None)]
        )

        (entree,) = data_manager.get_track_spotify_ids(track.id)
        assert entree.source == "genius_media"


class TestForgetTrackSpotifyId:
    def test_le_retrait_est_explicite(self, data_manager):
        artist = _artiste(data_manager)
        track = _morceau(data_manager, artist)
        data_manager.record_track_spotify_ids(
            track.id, [TrackSpotifyId(spotify_id=_ID_SINGLE, source="scraper")]
        )

        assert data_manager.forget_track_spotify_id(track.id, _ID_SINGLE) is True
        assert data_manager.get_track_spotify_ids(track.id) == []

    def test_oublier_un_id_inconnu_ne_ment_pas(self, data_manager):
        artist = _artiste(data_manager)
        track = _morceau(data_manager, artist)
        assert data_manager.forget_track_spotify_id(track.id, _ID_SINGLE) is False


class TestCycleDeVie:
    def test_la_suppression_dun_morceau_nettoie_ses_ids(self, data_manager):
        artist = _artiste(data_manager)
        track = _morceau(data_manager, artist)
        data_manager.record_track_spotify_ids(
            track.id, [TrackSpotifyId(spotify_id=_ID_SINGLE, source="scraper")]
        )

        data_manager.delete_track(track.id)

        with data_manager.engine.connect() as conn:
            from sqlalchemy import text

            reste = conn.execute(
                text("SELECT COUNT(*) FROM track_spotify_ids WHERE track_id = :tid"),
                {"tid": track.id},
            ).scalar()
        assert reste == 0

    def test_la_fusion_reunit_les_ids(self, data_manager):
        """Deux doublons portent souvent chacun une édition différente : faire
        CHOISIR perdrait la reconnaissance de l'autre à la récolte croisée."""
        artist = _artiste(data_manager)
        garde = _morceau(data_manager, artist, titre="Magot", spotify_id=_ID_ALBUM)
        doublon = _morceau(data_manager, artist, titre="magot", spotify_id=_ID_SINGLE)
        data_manager.record_track_spotify_ids(
            garde.id, [TrackSpotifyId(spotify_id=_ID_ALBUM, source="genius_media")]
        )
        data_manager.record_track_spotify_ids(
            doublon.id, [TrackSpotifyId(spotify_id=_ID_SINGLE, source="kworb")]
        )

        assert data_manager.merge_tracks(garde.id, doublon.id) is True

        entrees = data_manager.get_track_spotify_ids(garde.id)
        assert [e.spotify_id for e in entrees] == [_ID_ALBUM, _ID_SINGLE]
        # Après réunion, un seul principal : celui de la colonne du survivant.
        assert [e.is_primary for e in entrees] == [True, False]


class TestLectureParArtiste:
    def test_la_liste_est_peuplee_a_la_lecture(self, data_manager):
        """C'est ce qui RANIME `Track.spotify_ids` et le sélecteur de version de
        la fiche morceau, écrits de longue date et jamais alimentés."""
        artist = _artiste(data_manager)
        track = _morceau(data_manager, artist, spotify_id=_ID_ALBUM)
        data_manager.record_track_spotify_ids(
            track.id,
            [
                TrackSpotifyId(spotify_id=_ID_ALBUM, source="genius_media"),
                TrackSpotifyId(spotify_id=_ID_SINGLE, source="scraper"),
            ],
        )

        (relu,) = data_manager.get_artist_tracks(artist.id)
        assert relu.spotify_ids == [_ID_ALBUM, _ID_SINGLE]
        assert [e.source for e in relu.spotify_id_entries] == ["genius_media", "scraper"]
        # Peuplée à la LECTURE : rien n'est en attente d'écriture.
        assert relu._spotify_ids_pending == []


class TestIndexGlobalParSpotifyId:
    def test_toutes_les_lignes_dun_id_sont_rendues(self, data_manager):
        """Une LISTE, pas une paire. L'ancienne compréhension de dict, sur un
        SELECT sans ORDER BY, ne gardait qu'une ligne par ID — la ligne perdante
        n'avait donc jamais d'observation, restait éternellement « périmée » et
        consommait une page du plafond à chaque run pour rien.

        Le cas visé n'est PAS couvert par le partage entre lignes sœurs : ces
        deux morceaux ont des `genius_id` différents.
        """
        swing = _artiste(data_manager, "Swing")
        sch = _artiste(data_manager, "SCH")
        a = _morceau(data_manager, swing, titre="Rentre dans le Cercle", spotify_id=_ID_SINGLE)
        b = _morceau(data_manager, sch, titre="13 Organisé", spotify_id=_ID_SINGLE)

        carte = data_manager.get_track_ids_by_spotify_id()

        assert sorted(carte[_ID_SINGLE]) == sorted([(a.id, swing.id), (b.id, sch.id)])

    def test_un_id_alternatif_est_reconnu_lui_aussi(self, data_manager):
        """La carte lit les DEUX magasins : la colonne (l'ID principal, seul
        écrit par `save_track`) et la table (les éditions alternatives)."""
        artist = _artiste(data_manager)
        track = _morceau(data_manager, artist, spotify_id=_ID_ALBUM)
        data_manager.record_track_spotify_ids(
            track.id, [TrackSpotifyId(spotify_id=_ID_SINGLE, source="scraper")]
        )

        carte = data_manager.get_track_ids_by_spotify_id()

        assert carte[_ID_ALBUM] == [(track.id, artist.id)]
        assert carte[_ID_SINGLE] == [(track.id, artist.id)]

    def test_qui_revendique_un_id_se_lit_sur_toute_la_base(self, data_manager):
        swing = _artiste(data_manager, "Swing")
        sch = _artiste(data_manager, "SCH")
        _morceau(data_manager, swing, titre="Rentre dans le Cercle", spotify_id=_ID_SINGLE)
        _morceau(data_manager, sch, titre="13 Organisé", spotify_id=_ID_SINGLE)

        titres = {r["title"] for r in data_manager.lignes_du_spotify_id(_ID_SINGLE)}
        assert titres == {"Rentre dans le Cercle", "13 Organisé"}


class TestBackfillDeLaColonne:
    def test_un_backfill_indexe_meme_quand_la_colonne_est_prise(self, data_manager):
        """« Il en existe déjà un » ne veut pas dire « celui-ci est faux » : un
        morceau a plusieurs éditions. L'ancienne version perdait purement et
        simplement un second ID découvert par Kworb."""
        artist = _artiste(data_manager)
        track = _morceau(data_manager, artist, spotify_id=_ID_ALBUM)

        data_manager.update_track_spotify_id(track.id, _ID_SINGLE)

        with data_manager.engine.connect() as conn:
            from sqlalchemy import text

            colonne = conn.execute(
                text("SELECT spotify_id FROM tracks WHERE id = :tid"), {"tid": track.id}
            ).scalar()
        assert colonne == _ID_ALBUM  # l'ID principal n'est jamais écrasé
        # L'édition découverte est INDEXÉE au lieu d'être jetée. `_ID_ALBUM` n'est
        # pas dans la table : `save_track` ne l'écrit pas (écrivain dédié), il
        # vit dans la colonne — et c'est pourquoi l'index global lit les DEUX
        # magasins.
        assert {e.spotify_id for e in data_manager.get_track_spotify_ids(track.id)} == {_ID_SINGLE}
        assert set(data_manager.get_track_ids_by_spotify_id()) >= {_ID_ALBUM, _ID_SINGLE}


class TestPageTitleSurvitAuSave:
    def test_le_titre_de_page_survit_a_un_save_sur_ligne_existante(self, data_manager):
        """`spotify_page_title` était dans l'INSERT et ABSENTE de l'UPDATE — or à
        l'enrichissement la ligne existe DÉJÀ. Résultat mesuré avant correction :
        0 ligne renseignée sur 2 116, et une fiche morceau qui DEVINAIT la
        provenance de l'ID à partir de cette colonne toujours vide.
        """
        artist = _artiste(data_manager)
        track = _morceau(data_manager, artist)  # 1er save : la ligne naît

        track.spotify_page_title = "Magot - song by Artiste | Spotify"
        data_manager.save_track(track)  # 2e save : c'est un UPDATE

        with data_manager.engine.connect() as conn:
            from sqlalchemy import text

            stocke = conn.execute(
                text("SELECT spotify_page_title FROM tracks WHERE id = :tid"), {"tid": track.id}
            ).scalar()
        assert stocke == "Magot - song by Artiste | Spotify"
