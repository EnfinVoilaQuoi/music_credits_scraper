"""Table `artist_relations` (e22) : l'écrivain dédié et l'union à la lecture.

Le lot 3 tient dans une phrase : la discographie d'un membre inclut celle de ses
formations, et on l'obtient en élargissant la LECTURE — jamais en recopiant des
morceaux. `UNIQUE(title, artist_id)` l'interdirait de toute façon, et cela
doublerait streams et certifications.
"""

from src.models import Artist, ArtistRelation, Track


def _artiste(data_manager, nom: str) -> Artist:
    a = Artist(name=nom)
    a.id = data_manager.save_artist(a)
    return a


def _rel(nom, kind="member_of", **kw):
    return ArtistRelation(related_name=nom, kind=kind, **kw)


class TestEcrivainDedie:
    def test_un_lien_confirme_se_relit(self, data_manager):
        shurikn = _artiste(data_manager, "Shurik'N")
        data_manager.record_artist_relations(
            shurikn.id, [_rel("IAM", source="musicbrainz", begin_date="1989")]
        )

        (lu,) = data_manager.get_artist_relations(shurikn.id)
        assert (lu.related_name, lu.kind, lu.source) == ("IAM", "member_of", "musicbrainz")
        assert lu.begin_date == "1989"

    def test_le_lien_vaut_meme_si_le_groupe_nest_pas_en_base(self, data_manager):
        """Cas COURANT au départ : on apprend qu'un artiste est membre d'un
        groupe qu'on n'a pas encore. Le lien reste une information."""
        shurikn = _artiste(data_manager, "Shurik'N")
        data_manager.record_artist_relations(shurikn.id, [_rel("IAM")])

        (lu,) = data_manager.get_artist_relations(shurikn.id)
        assert lu.related_artist_id is None

    def test_le_groupe_ajoute_ensuite_est_rattache_a_la_confirmation_suivante(self, data_manager):
        shurikn = _artiste(data_manager, "Shurik'N")
        data_manager.record_artist_relations(shurikn.id, [_rel("IAM")])
        iam = _artiste(data_manager, "IAM")

        data_manager.record_artist_relations(shurikn.id, [_rel("IAM")])

        (lu,) = data_manager.get_artist_relations(shurikn.id)
        assert lu.related_artist_id == iam.id

    def test_le_rattachement_passe_par_le_nom_NORMALISE(self, data_manager):
        """MusicBrainz écrit « Shurik’n », notre base « Shurik'N » : une égalité
        brute laisserait le lien orphelin alors que l'artiste est là."""
        iam = _artiste(data_manager, "IAM")
        _artiste(data_manager, "Shurik'N")
        data_manager.record_artist_relations(iam.id, [_rel("Shurik’n", kind="has_member")])

        (lu,) = data_manager.get_artist_relations(iam.id)
        assert lu.related_artist_id is not None

    def test_rejouer_ne_duplique_pas(self, data_manager):
        a = _artiste(data_manager, "Swing")
        data_manager.record_artist_relations(a.id, [_rel("L'Or du Commun")])
        data_manager.record_artist_relations(a.id, [_rel("L'Or du Commun", source="discogs")])

        (lu,) = data_manager.get_artist_relations(a.id)
        assert lu.source == "discogs"  # la confirmation la plus récente fait foi

    def test_les_deux_sens_coexistent(self, data_manager):
        """`member_of` et `has_member` sont deux faits distincts, et on n'écrit
        que le point de vue OBSERVÉ — déduire l'inverse fabriquerait une donnée
        que personne n'a confirmée."""
        a = _artiste(data_manager, "Shurik'N")
        data_manager.record_artist_relations(a.id, [_rel("IAM"), _rel("IAM", kind="has_member")])
        assert len(data_manager.get_artist_relations(a.id)) == 2

    def test_un_lien_sans_nom_est_ignore(self, data_manager):
        a = _artiste(data_manager, "Swing")
        assert data_manager.record_artist_relations(a.id, [_rel("")]) == 0

    def test_oublier_un_lien(self, data_manager):
        a = _artiste(data_manager, "Swing")
        data_manager.record_artist_relations(a.id, [_rel("L'Or du Commun")])

        assert data_manager.forget_artist_relation(a.id, "L'Or du Commun", "member_of") is True
        assert data_manager.get_artist_relations(a.id) == []
        assert data_manager.forget_artist_relation(a.id, "L'Or du Commun", "member_of") is False


class TestUnionALaLecture:
    def test_les_ids_du_membre_incluent_ses_groupes(self, data_manager):
        iam = _artiste(data_manager, "IAM")
        shurikn = _artiste(data_manager, "Shurik'N")
        data_manager.record_artist_relations(shurikn.id, [_rel("IAM")])

        assert data_manager.ids_discographie_reunie(shurikn.id) == [shurikn.id, iam.id]

    def test_lartiste_est_toujours_en_tete(self, data_manager):
        iam = _artiste(data_manager, "IAM")
        shurikn = _artiste(data_manager, "Shurik'N")
        data_manager.record_artist_relations(shurikn.id, [_rel("IAM")])
        assert data_manager.ids_discographie_reunie(shurikn.id)[0] == shurikn.id
        assert iam.id in data_manager.ids_discographie_reunie(shurikn.id)

    def test_un_GROUPE_ne_recupere_PAS_le_solo_de_ses_membres(self, data_manager):
        """IAM n'est pas l'auteur des albums solo de Shurik'N. Seul `member_of`
        élargit ; `has_member` décrit, il n'hérite pas."""
        iam = _artiste(data_manager, "IAM")
        _artiste(data_manager, "Shurik'N")
        data_manager.record_artist_relations(iam.id, [_rel("Shurik'N", kind="has_member")])

        assert data_manager.ids_discographie_reunie(iam.id) == [iam.id]

    def test_un_groupe_absent_de_la_base_nelargit_rien(self, data_manager):
        shurikn = _artiste(data_manager, "Shurik'N")
        data_manager.record_artist_relations(shurikn.id, [_rel("IAM")])
        assert data_manager.ids_discographie_reunie(shurikn.id) == [shurikn.id]

    def test_pas_de_transitivite(self, data_manager):
        """Un seul niveau, volontairement : membre d'un groupe dont un membre
        est dans un autre groupe n'hérite pas du troisième."""
        _artiste(data_manager, "Groupe C")
        b = _artiste(data_manager, "Groupe B")
        a = _artiste(data_manager, "Artiste A")
        data_manager.record_artist_relations(a.id, [_rel("Groupe B")])
        data_manager.record_artist_relations(b.id, [_rel("Groupe C")])

        assert data_manager.ids_discographie_reunie(a.id) == [a.id, b.id]

    def test_aucune_ligne_de_morceau_nest_dupliquee(self, data_manager):
        """La vérification qui compte : réunir des discographies ne crée AUCUN
        morceau. Le double comptage des streams et des certifications vient
        toujours d'une ligne recopiée."""
        iam = _artiste(data_manager, "IAM")
        shurikn = _artiste(data_manager, "Shurik'N")
        data_manager.save_track(Track(title="Petit frère", artist=iam))
        data_manager.record_artist_relations(shurikn.id, [_rel("IAM")])

        ids = data_manager.ids_discographie_reunie(shurikn.id)
        morceaux = [t for i in ids for t in data_manager.get_artist_tracks(i)]

        with data_manager.engine.connect() as conn:
            total = conn.exec_driver_sql("SELECT COUNT(*) FROM tracks").scalar()
        assert total == 1  # une seule LIGNE en base…
        assert [t.title for t in morceaux] == ["Petit frère"]  # …vue par le membre


class TestEcrivainsDediesArtiste:
    """`save_artist` ne doit pas se mettre à écrire les relations — même règle
    que les certifications et les vidéos."""

    def test_save_artist_ne_mentionne_pas_la_table(self):
        import inspect

        from src.utils.artist_repository import ArtistRepository

        assert "artist_relations" not in inspect.getsource(ArtistRepository.save_artist)

    def test_lecrivain_dedie_existe(self):
        from src.utils.artist_repository import ArtistRepository

        assert callable(ArtistRepository.record_artist_relations)
        assert callable(ArtistRepository.forget_artist_relation)
