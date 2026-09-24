"""Groupe contre collectif : les deux natures ne se lisent pas pareil.

Décision utilisateur du 2026-09-08, et c'est la seule chose qui distingue
vraiment les deux :

  · un **groupe** (IAM, L'Or du Commun, Bavoog Avers) apporte TOUS ses morceaux
    à chacun de ses membres ;
  · un **collectif** (L'Animalerie) n'apporte que les morceaux où le membre est
    PRÉSENT — à l'écriture, à la production ou à la performance. Un collectif
    est une maison, pas une formation : tout le monde n'y travaille pas toujours
    ensemble, et il peut abriter un groupe plus petit.
"""

from src.models import Artist, ArtistRelation, Credit, CreditRole, Track


def _artiste(data_manager, nom: str) -> Artist:
    a = Artist(name=nom)
    a.id = data_manager.save_artist(a)
    return a


def _rel(nom, kind="member_of", **kw):
    return ArtistRelation(related_name=nom, kind=kind, **kw)


def _morceau(data_manager, artiste, titre, credits=(), featured=None):
    t = Track(title=titre, artist=artiste, featured_artists=featured)
    t.credits = [Credit(name=n, role=r) for n, r in credits]
    data_manager.save_track(t)
    return t


class TestGroupe:
    def test_un_groupe_apporte_TOUS_ses_morceaux(self, data_manager):
        iam = _artiste(data_manager, "IAM")
        shurikn = _artiste(data_manager, "Shurik'N")
        _morceau(data_manager, iam, "Petit frère")
        _morceau(data_manager, iam, "Demain c'est loin")
        data_manager.record_artist_relations(shurikn.id, [_rel("IAM", formation="groupe")])

        titres = {t.title for t in data_manager.discographie_reunie(shurikn)}
        assert titres == {"Petit frère", "Demain c'est loin"}

    def test_le_groupe_ne_recupere_pas_le_solo_du_membre(self, data_manager):
        iam = _artiste(data_manager, "IAM")
        shurikn = _artiste(data_manager, "Shurik'N")
        _morceau(data_manager, shurikn, "Où je vis")
        data_manager.record_artist_relations(iam.id, [_rel("Shurik'N", kind="has_member")])

        assert data_manager.discographie_reunie(iam) == []


class TestCollectif:
    def test_seuls_les_morceaux_ou_le_membre_est_la(self, data_manager):
        animalerie = _artiste(data_manager, "L'Animalerie")
        lucio = _artiste(data_manager, "Lucio Bukowski")
        _morceau(data_manager, animalerie, "Avec lui", [("Lucio Bukowski", CreditRole.WRITER)])
        _morceau(data_manager, animalerie, "Sans lui", [("Quelqu'un d'autre", CreditRole.WRITER)])
        data_manager.record_artist_relations(
            lucio.id, [_rel("L'Animalerie", formation="collectif")]
        )

        titres = {t.title for t in data_manager.discographie_reunie(lucio)}
        assert titres == {"Avec lui"}

    def test_ecriture_production_ET_performance_comptent(self, data_manager):
        collectif = _artiste(data_manager, "Collectif")
        membre = _artiste(data_manager, "Membre")
        _morceau(data_manager, collectif, "Écrit", [("Membre", CreditRole.WRITER)])
        _morceau(data_manager, collectif, "Produit", [("Membre", CreditRole.PRODUCER)])
        _morceau(data_manager, collectif, "Chanté", [("Membre", CreditRole.FEATURED)])
        _morceau(data_manager, collectif, "Joué", [("Membre", CreditRole.PIANO)])
        data_manager.record_artist_relations(membre.id, [_rel("Collectif", formation="collectif")])

        titres = {t.title for t in data_manager.discographie_reunie(membre)}
        assert titres == {"Écrit", "Produit", "Chanté", "Joué"}

    def test_avoir_seulement_MIXE_ne_suffit_pas(self, data_manager):
        """La limite fixée : les métiers du son, le label et la vidéo n'entrent
        pas. Quelqu'un qui a mixé un morceau du collectif ne l'a pas dans SA
        discographie."""
        collectif = _artiste(data_manager, "Collectif")
        membre = _artiste(data_manager, "Membre")
        _morceau(data_manager, collectif, "Mixé", [("Membre", CreditRole.MIXING_ENGINEER)])
        _morceau(data_manager, collectif, "Labellisé", [("Membre", CreditRole.LABEL)])
        _morceau(data_manager, collectif, "Clippé", [("Membre", CreditRole.VIDEO_DIRECTOR)])
        data_manager.record_artist_relations(membre.id, [_rel("Collectif", formation="collectif")])

        assert data_manager.discographie_reunie(membre) == []

    def test_le_membre_reconnu_dans_une_LISTE_de_featurings(self, data_manager):
        """`featured_artists` est une liste — il faut y reconnaître un nom sans
        qu'« IAM » ne matche « Williams », d'où les mots entiers."""
        collectif = _artiste(data_manager, "Collectif")
        membre = _artiste(data_manager, "Josman")
        _morceau(data_manager, collectif, "En feat", featured="S.Pri Noir, Eazy Dew, 3010, Josman")
        data_manager.record_artist_relations(membre.id, [_rel("Collectif", formation="collectif")])

        assert [t.title for t in data_manager.discographie_reunie(membre)] == ["En feat"]

    def test_un_homonyme_partiel_nentre_pas(self, data_manager):
        collectif = _artiste(data_manager, "Collectif")
        membre = _artiste(data_manager, "IAM")
        _morceau(data_manager, collectif, "Piège", [("Robbie Williams", CreditRole.WRITER)])
        data_manager.record_artist_relations(membre.id, [_rel("Collectif", formation="collectif")])

        assert data_manager.discographie_reunie(membre) == []

    def test_un_alias_confirme_elargit_la_reconnaissance(self, data_manager):
        """Un membre est crédité tantôt sous son nom, tantôt sous un alias."""
        collectif = _artiste(data_manager, "Collectif")
        membre = _artiste(data_manager, "Shurik'N")
        _morceau(data_manager, collectif, "Sous alias", [("Chien de la casse", CreditRole.WRITER)])
        data_manager.record_artist_relations(
            membre.id,
            [_rel("Collectif", formation="collectif"), _rel("Chien de la casse", kind="alias")],
        )

        assert [t.title for t in data_manager.discographie_reunie(membre)] == ["Sous alias"]


class TestGroupeIssuDunCollectif:
    def test_deux_liens_directs_et_aucune_transitivite(self, data_manager):
        """Bavoog Avers, quatuor issu de L'Animalerie : être dans le quatuor ne
        met personne sur tous les morceaux du collectif."""
        animalerie = _artiste(data_manager, "L'Animalerie")
        bavoog = _artiste(data_manager, "Bavoog Avers")
        membre = _artiste(data_manager, "Membre")
        _morceau(data_manager, bavoog, "Titre du quatuor")
        _morceau(data_manager, animalerie, "Avec lui", [("Membre", CreditRole.WRITER)])
        _morceau(data_manager, animalerie, "Sans lui", [("Autre", CreditRole.WRITER)])
        data_manager.record_artist_relations(
            membre.id,
            [
                _rel("Bavoog Avers", formation="groupe"),
                _rel("L'Animalerie", formation="collectif"),
            ],
        )

        titres = {t.title for t in data_manager.discographie_reunie(membre)}
        assert titres == {"Titre du quatuor", "Avec lui"}


class TestAucunDoubleComptage:
    def test_un_morceau_atteignable_par_deux_chemins_napparait_quune_fois(self, data_manager):
        """Sinon streams et certifications doubleraient à l'affichage — le
        défaut que toute cette conception cherche à éviter."""
        collectif = _artiste(data_manager, "Collectif")
        membre = _artiste(data_manager, "Membre")
        _morceau(data_manager, collectif, "Commun", [("Membre", CreditRole.WRITER)])
        data_manager.record_artist_relations(
            membre.id,
            [
                _rel("Collectif", formation="collectif"),
                _rel("Collectif", kind="has_member", formation="collectif"),
            ],
        )

        assert len(data_manager.discographie_reunie(membre)) == 1

    def test_reunir_ne_cree_aucune_ligne_en_base(self, data_manager):
        iam = _artiste(data_manager, "IAM")
        shurikn = _artiste(data_manager, "Shurik'N")
        _morceau(data_manager, iam, "Petit frère")
        data_manager.record_artist_relations(shurikn.id, [_rel("IAM", formation="groupe")])

        data_manager.discographie_reunie(shurikn)

        with data_manager.engine.connect() as conn:
            assert conn.exec_driver_sql("SELECT COUNT(*) FROM tracks").scalar() == 1


class TestPropagationDeLaNature:
    def test_la_nature_dun_membre_est_proposee_pour_le_suivant(self, data_manager):
        """La nature appartient à la FORMATION : L'Animalerie est un collectif
        pour tout le monde. La pré-remplir empêche la divergence par oubli."""
        a = _artiste(data_manager, "Membre A")
        data_manager.record_artist_relations(a.id, [_rel("L'Animalerie", formation="collectif")])

        assert data_manager.nature_connue_pour("L'Animalerie") == "collectif"
        assert data_manager.nature_connue_pour("l’animalerie") == "collectif"

    def test_aucune_nature_connue(self, data_manager):
        assert data_manager.nature_connue_pour("Inconnue") is None
        assert data_manager.nature_connue_pour("") is None


class TestSuppressionArtiste:
    def test_supprimer_un_artiste_emporte_ses_liens_DANS_LES_DEUX_SENS(self, data_manager):
        """Sinon la discographie réunie d'un coéquipier irait chercher un
        artiste qui n'existe plus."""
        _artiste(data_manager, "IAM")
        shurikn = _artiste(data_manager, "Shurik'N")
        data_manager.record_artist_relations(shurikn.id, [_rel("IAM", formation="groupe")])

        data_manager.delete_artist("IAM")

        with data_manager.engine.connect() as conn:
            assert conn.exec_driver_sql("SELECT COUNT(*) FROM artist_relations").scalar() == 0
        assert data_manager.ids_discographie_reunie(shurikn.id) == [shurikn.id]

    def test_supprimer_un_artiste_emporte_les_videos_de_ses_morceaux(self, data_manager):
        """e20 : même absence de cascade FK, mêmes orphelines."""
        from src.models import TrackVideo

        a = _artiste(data_manager, "Artiste")
        t = _morceau(data_manager, a, "Titre")
        data_manager.record_track_videos(t.id, [TrackVideo(video_id="aaaaaaaaaaa")])

        data_manager.delete_artist("Artiste")

        with data_manager.engine.connect() as conn:
            assert conn.exec_driver_sql("SELECT COUNT(*) FROM track_videos").scalar() == 0


class TestMarquageViaGroup:
    """Un morceau qui vient d'ailleurs est MARQUÉ.

    Sans ça, le tableau ne distinguerait plus ce que l'artiste a sorti de ce
    que sa formation a sorti — il mentirait par omission. Le champ est
    transitoire : le même morceau est « via IAM » chez Shurik'N et rien du tout
    chez IAM.
    """

    def test_les_morceaux_propres_ne_sont_pas_marques(self, data_manager):
        shurikn = _artiste(data_manager, "Shurik'N")
        _morceau(data_manager, shurikn, "Où je vis")
        (lu,) = data_manager.discographie_reunie(shurikn)
        assert lu.via_group is None

    def test_les_morceaux_du_groupe_portent_son_nom(self, data_manager):
        iam = _artiste(data_manager, "IAM")
        shurikn = _artiste(data_manager, "Shurik'N")
        _morceau(data_manager, iam, "Petit frère")
        data_manager.record_artist_relations(shurikn.id, [_rel("IAM", formation="groupe")])

        (lu,) = data_manager.discographie_reunie(shurikn)
        assert lu.via_group == "IAM"

    def test_les_morceaux_du_collectif_aussi(self, data_manager):
        collectif = _artiste(data_manager, "L'Animalerie")
        membre = _artiste(data_manager, "Membre")
        _morceau(data_manager, collectif, "Avec lui", [("Membre", CreditRole.WRITER)])
        data_manager.record_artist_relations(
            membre.id, [_rel("L'Animalerie", formation="collectif")]
        )

        (lu,) = data_manager.discographie_reunie(membre)
        assert lu.via_group == "L'Animalerie"

    def test_le_meme_morceau_nest_PAS_marque_chez_son_propre_artiste(self, data_manager):
        """Le marquage décrit une LECTURE, pas le morceau."""
        iam = _artiste(data_manager, "IAM")
        shurikn = _artiste(data_manager, "Shurik'N")
        _morceau(data_manager, iam, "Petit frère")
        data_manager.record_artist_relations(shurikn.id, [_rel("IAM", formation="groupe")])

        assert data_manager.discographie_reunie(shurikn)[0].via_group == "IAM"
        assert data_manager.discographie_reunie(iam)[0].via_group is None


class TestJointureTardive:
    """Le groupe entre en base APRÈS le lien — c'est le cas NORMAL.

    On confirme presque toujours « Swing est membre de L'Or du Commun » avant
    d'avoir ajouté le groupe. Constaté en vrai le 2026-09-08 : les deux liens
    confirmés portaient `related_artist_id = NULL`, la discographie ne
    s'élargissait pas, et rien ne le disait.
    """

    def test_le_lien_prend_vie_des_que_le_groupe_entre_en_base(self, data_manager):
        swing = _artiste(data_manager, "Swing")
        data_manager.record_artist_relations(swing.id, [_rel("L’Or du Commun", formation="groupe")])
        assert data_manager.ids_discographie_reunie(swing.id) == [swing.id]

        # Le groupe est ajouté ensuite, SANS re-confirmer quoi que ce soit.
        groupe = _artiste(data_manager, "L'Or du Commun")
        _morceau(data_manager, groupe, "Trèfle d'Or")

        assert data_manager.ids_discographie_reunie(swing.id) == [swing.id, groupe.id]
        assert [t.title for t in data_manager.discographie_reunie(swing)] == ["Trèfle d'Or"]

    def test_la_jointure_tardive_passe_par_le_nom_NORMALISE(self, data_manager):
        """Le lien dit « L’Or du Commun » (apostrophe typographique de
        MusicBrainz), la base « L'Or du Commun » : une égalité brute laisserait
        le lien mort à côté de l'artiste."""
        swing = _artiste(data_manager, "Swing")
        data_manager.record_artist_relations(swing.id, [_rel("L’Or du Commun", formation="groupe")])
        groupe = _artiste(data_manager, "L'Or Du Commun")

        (lu,) = data_manager.get_artist_relations(swing.id)
        assert lu.related_artist_id == groupe.id

    def test_un_groupe_toujours_absent_reste_sans_identifiant(self, data_manager):
        swing = _artiste(data_manager, "Swing")
        data_manager.record_artist_relations(swing.id, [_rel("Inconnu", formation="groupe")])
        (lu,) = data_manager.get_artist_relations(swing.id)
        assert lu.related_artist_id is None


def _date(data_manager, track, valeur, source="genius"):
    """Pose une date AVEC SA PRÉCISION, comme le fait un producteur."""
    from src.enrichment.observation import Observation

    data_manager.record_discography_observations(
        track.id, [Observation(field="release_date", value=valeur, source=source)]
    )


class TestGroupeDate:
    """2026-09-24 : un groupe daté n'apporte que ce qui est sorti pendant
    l'appartenance. Les années charnière exigent une PREUVE de présence : un
    crédit, ou le disque (crédité sur au moins la moitié de ses titres)."""

    def _booba_lunatic(self, data_manager, fin="2003", debut="1994"):
        lunatic = _artiste(data_manager, "Lunatic")
        booba = _artiste(data_manager, "Booba")
        data_manager.record_artist_relations(
            booba.id,
            [_rel("Lunatic", formation="groupe", begin_date=debut, end_date=fin)],
        )
        return booba, lunatic

    def test_un_groupe_sans_date_apporte_tout(self, data_manager):
        lunatic = _artiste(data_manager, "Lunatic")
        booba = _artiste(data_manager, "Booba")
        data_manager.record_artist_relations(booba.id, [_rel("Lunatic", formation="groupe")])
        _date(data_manager, _morceau(data_manager, lunatic, "Bien après"), "2015-01-01")

        assert {t.title for t in data_manager.discographie_reunie(booba)} == {"Bien après"}
        assert booba.hors_periode == {}

    def test_dedans_garde_dehors_ecarte_et_se_compte(self, data_manager):
        booba, lunatic = self._booba_lunatic(data_manager)
        _date(data_manager, _morceau(data_manager, lunatic, "Pendant"), "1999-06-01")
        _date(data_manager, _morceau(data_manager, lunatic, "Après"), "2006-06-01")
        _morceau(data_manager, lunatic, "Sans date")

        titres = {t.title for t in data_manager.discographie_reunie(booba)}
        assert titres == {"Pendant", "Sans date"}
        assert booba.hors_periode == {"Lunatic": 1}

    def test_charniere_gardee_seulement_avec_un_credit(self, data_manager):
        booba, lunatic = self._booba_lunatic(data_manager)
        avec = _morceau(data_manager, lunatic, "Avec", [("Booba", CreditRole.WRITER)])
        sans = _morceau(data_manager, lunatic, "Sans", [("Ali", CreditRole.WRITER)])
        _date(data_manager, avec, "2003-09-01")
        _date(data_manager, sans, "2003-09-01")

        assert {t.title for t in data_manager.discographie_reunie(booba)} == {"Avec"}

    def test_une_annee_seule_au_1er_janvier_n_est_pas_un_jour(self, data_manager):
        """La colonne vaut 2003-01-01, mais l'observation dit « 2003 » : face à
        un départ au 27 mars, c'est une charnière, pas un morceau « dedans »."""
        booba, lunatic = self._booba_lunatic(data_manager, fin="2003-03-27")
        _date(data_manager, _morceau(data_manager, lunatic, "Année seule"), "2003")

        assert data_manager.discographie_reunie(booba) == []

    def _album(self, data_manager, lunatic, credites, total, date="2003-10-01"):
        for i in range(total):
            credits = (
                [("Booba", CreditRole.WRITER)] if i < credites else [("Ali", CreditRole.WRITER)]
            )
            t = Track(title=f"Titre {i}", artist=lunatic, album="Mauvais Œil")
            t.credits = [Credit(name=n, role=r) for n, r in credits]
            data_manager.save_track(t)
            _date(data_manager, t, date)

    def test_le_disque_prouve_la_presence_au_dela_de_la_moitie(self, data_manager):
        booba, lunatic = self._booba_lunatic(data_manager)
        self._album(data_manager, lunatic, credites=6, total=10)

        assert len(data_manager.discographie_reunie(booba)) == 10
        assert booba.hors_periode == {}

    def test_sous_la_moitie_seuls_les_titres_credites(self, data_manager):
        booba, lunatic = self._booba_lunatic(data_manager)
        self._album(data_manager, lunatic, credites=3, total=10)

        assert len(data_manager.discographie_reunie(booba)) == 3
        assert booba.hors_periode == {"Lunatic": 7}

    def test_un_single_de_deux_titres_ne_prouve_rien(self, data_manager):
        booba, lunatic = self._booba_lunatic(data_manager)
        self._album(data_manager, lunatic, credites=1, total=2)

        assert len(data_manager.discographie_reunie(booba)) == 1

    def test_un_collectif_n_est_jamais_filtre_par_date(self, data_manager):
        collectif = _artiste(data_manager, "501 Posse")
        solaar = _artiste(data_manager, "MC Solaar")
        data_manager.record_artist_relations(
            solaar.id,
            [_rel("501 Posse", formation="collectif", begin_date="1988", end_date="1995")],
        )
        t = _morceau(data_manager, collectif, "Tard", [("MC Solaar", CreditRole.WRITER)])
        _date(data_manager, t, "2010-01-01")

        assert {t.title for t in data_manager.discographie_reunie(solaar)} == {"Tard"}
