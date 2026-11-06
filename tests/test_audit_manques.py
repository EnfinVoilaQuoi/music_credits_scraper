"""Ce que l'audit ne voyait pas (point du 2026-11-06) : colonnes sans provenance,
ID Spotify sans compteur, fiches sans participation, liens jamais mesurés,
orphelins de l'historique d'usage ; et deux faux positifs (commentaires, sessions).
"""

from contextlib import closing

import pytest
from sqlalchemy import text

from src.enrichment.observation import Observation
from src.enrichment.reconcile import DISCOGRAPHY_PRIORITIES, resoudre_duree
from src.models import Artist, Track
from src.services import audit as au
from src.services.revue import INFORMATION, RUN
from src.utils.duree_youtube import court_par_nature


def _artiste(dm, nom="Eminem"):
    a = Artist(name=nom)
    a.id = dm.save_artist(a)
    return a


def _fiche(dm, artiste, titre="Still D.R.E.", **champs):
    t = Track(title=titre, artist=artiste)
    t.genius_id = champs.pop("genius_id", None)
    t.id = dm.save_track(t)
    if champs:
        sets = ", ".join(f"{c} = :{c}" for c in champs)
        _sql(dm, f"UPDATE tracks SET {sets} WHERE id = :t", t=t.id, **champs)
    return t


def _sql(dm, requete, **params):
    with dm.engine.begin() as conn:
        conn.execute(text(requete), params)


def _cas(dm, juge):
    with closing(au._connexion(dm.db_path)) as c:
        return juge(c, au._pistes(c, None))


class TestColonneSansProvenance:
    def test_colonne_remplie_sans_observation(self, data_manager):
        t = _fiche(data_manager, _artiste(data_manager), duration=246, spotify_id="abc")
        cas = _cas(data_manager, au.colonnes_sans_provenance)
        assert [(x.nature, x.pistes) for x in cas] == [(RUN, [t.id])]
        assert "duration sans observation (un ID de catalogue)" in cas[0].libelle

    def test_une_observation_suffit(self, data_manager):
        t = _fiche(data_manager, _artiste(data_manager))
        data_manager.record_discography_observations(t.id, [Observation("duration", 200, "deezer")])
        assert not _cas(data_manager, au.colonnes_sans_provenance)

    def test_sans_catalogue_le_libelle_le_dit(self, data_manager):
        _fiche(data_manager, _artiste(data_manager), isrc="FR0000000001")
        assert "(aucun ID)" in _cas(data_manager, au.colonnes_sans_provenance)[0].libelle


class TestIdSpotifySansCompteur:
    def test_jamais_lue_et_lue_sans_compteur(self, data_manager):
        a = _artiste(data_manager)
        jamais = _fiche(data_manager, a, "A", spotify_id="sp1")
        lue = _fiche(data_manager, a, "B", spotify_id="sp2")
        data_manager.upsert_observations(lue.id, [Observation("duration", 200, "spotify_web")])
        cas = {x.pistes[0]: x for x in _cas(data_manager, au.ids_spotify_sans_compteur)}
        assert cas[jamais.id].nature == RUN and "jamais lue" in cas[jamais.id].libelle
        assert "page lue, sans compteur" in cas[lue.id].libelle

    def test_un_compteur_ou_un_refus_de_compteur_n_en_est_pas_un(self, data_manager):
        a = _artiste(data_manager)
        _fiche(data_manager, a, "A", spotify_id="sp1", spotify_streams=10)
        _fiche(data_manager, a, "B", spotify_id="sp2")
        _sql(
            data_manager,
            "INSERT INTO refus (cle_fiche, artiste, nature, valeur) "
            "VALUES ('k', 'Eminem', 'compteur_spotify', 'sp2')",
        )
        assert not _cas(data_manager, au.ids_spotify_sans_compteur)


class TestFicheSansParticipation:
    def _credit(self, dm, track_id, nom):
        _sql(
            dm,
            "INSERT INTO credits (track_id, name, role, source) VALUES (:t, :n, 'Writer', 'genius')",
            t=track_id,
            n=nom,
        )

    def _alias(self, dm, artiste, nom, kind="alias", status="confirmed"):
        _sql(
            dm,
            "INSERT INTO artist_relations (artist_id, related_name, kind, source, status) "
            "VALUES (:a, :n, :k, 'test', :s)",
            a=artiste.id,
            n=nom,
            k=kind,
            s=status,
        )

    def _marquee(self, dm, artiste):
        return _fiche(dm, artiste, is_featuring=0, primary_artist_name="Dr. Dre")

    def test_le_morceau_d_un_autre_pour_information(self, data_manager):
        t = self._marquee(data_manager, _artiste(data_manager))
        cas = _cas(data_manager, au.fiches_sans_participation)
        assert [(x.nature, x.pistes) for x in cas] == [(INFORMATION, [t.id])]

    def test_credite_sous_une_graphie_il_n_y_est_pas(self, data_manager):
        t = self._marquee(data_manager, _artiste(data_manager))
        self._credit(data_manager, t.id, "Eminem (2)")
        assert not _cas(data_manager, au.fiches_sans_participation)

    def test_credite_sous_un_alias_confirme_il_n_y_est_pas(self, data_manager):
        a = _artiste(data_manager)
        self._alias(data_manager, a, "Slim Shady")
        t = self._marquee(data_manager, a)
        self._credit(data_manager, t.id, "Slim Shady")
        assert not _cas(data_manager, au.fiches_sans_participation)

    def test_un_alias_trop_court_ne_vaut_pas_credit(self, data_manager):
        """« M » et « Em » ne désignent personne : « Still D.R.E. » y échappait."""
        a = _artiste(data_manager)
        self._alias(data_manager, a, "Em")
        t = self._marquee(data_manager, a)
        self._credit(data_manager, t.id, "Em Rusciano")
        assert len(_cas(data_manager, au.fiches_sans_participation)) == 1

    def test_un_alias_seulement_propose_ne_vaut_pas_credit(self, data_manager):
        a = _artiste(data_manager)
        self._alias(data_manager, a, "Slim Shady", status="proposed")
        t = self._marquee(data_manager, a)
        self._credit(data_manager, t.id, "Slim Shady")
        assert len(_cas(data_manager, au.fiches_sans_participation)) == 1

    def test_sa_formation_n_y_est_pas(self, data_manager):
        a = _artiste(data_manager)
        self._alias(data_manager, a, "Dr. Dre", kind="member_of")
        self._marquee(data_manager, a)
        assert not _cas(data_manager, au.fiches_sans_participation)


class TestLienJamaisMesure:
    _URL = "https://www.youtube.com/watch?v=abcdefghijk"

    def test_sans_ligne_de_cette_video(self, data_manager):
        _fiche(data_manager, _artiste(data_manager), youtube_url=self._URL)
        cas = _cas(data_manager, au.liens_jamais_mesures)
        assert [x.nature for x in cas] == [INFORMATION] and "abcdefghijk" in cas[0].libelle

    def test_la_ligne_de_la_video_suffit(self, data_manager):
        t = _fiche(data_manager, _artiste(data_manager), youtube_url=self._URL)
        _sql(
            data_manager,
            "INSERT INTO track_videos (track_id, video_id, url, views) "
            f"VALUES (:t, 'abcdefghijk', '{self._URL}', 5)",
            t=t.id,
        )
        assert not _cas(data_manager, au.liens_jamais_mesures)


class TestOrphelins:
    def test_l_historique_d_usage_d_une_fiche_disparue(self, data_manager):
        _sql(
            data_manager,
            "INSERT INTO source_usage_failures (source_key, issue, track_id) "
            "VALUES ('spotify_embed', 'timeout', 99999)",
        )
        with closing(au._connexion(data_manager.db_path)) as c:
            assert [x.libelle for x in au.orphelins(c)] == [
                "source_usage_failures : 1 ligne(s) sans fiche"
            ]

    def test_le_rattrapage_garde_la_ligne_sans_la_fiche(self, data_manager):
        _sql(
            data_manager,
            "INSERT INTO source_usage_failures (source_key, issue, track_id) "
            "VALUES ('spotify_embed', 'timeout', 99999)",
        )
        assert len(data_manager.echecs_d_usage_orphelins()) == 1
        assert data_manager.dissocier_echecs_d_usage_orphelins() == 1
        assert data_manager.echecs_d_usage_orphelins() == []
        with data_manager.engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM source_usage_failures")).scalar() == 1

    def test_un_echec_sans_fiche_n_en_est_pas_un(self, data_manager):
        _sql(
            data_manager,
            "INSERT INTO source_usage_failures (source_key, issue) VALUES ('deezer', 'timeout')",
        )
        with closing(au._connexion(data_manager.db_path)) as c:
            assert not au.orphelins(c)

    @pytest.mark.parametrize("geste", ["supprimer", "fusionner"])
    def test_supprimer_ou_fusionner_garde_l_historique_sans_la_fiche(self, data_manager, geste):
        """Un historique d'usage survit à sa fiche : il ne la désigne simplement plus."""
        a = _artiste(data_manager)
        garde, t = _fiche(data_manager, a, "A"), _fiche(data_manager, a, "A bis")
        _sql(
            data_manager,
            "INSERT INTO source_usage_failures (source_key, issue, track_id) "
            "VALUES ('spotify_embed', 'timeout', :t)",
            t=t.id,
        )
        if geste == "supprimer":
            data_manager.delete_track(t.id)
        else:
            data_manager.merge_tracks(garde.id, t.id)
        with data_manager.engine.connect() as conn:
            lignes = conn.execute(text("SELECT track_id FROM source_usage_failures")).all()
        assert [r[0] for r in lignes] == [None]

    def test_supprimer_un_artiste_garde_l_historique_sans_la_fiche(self, data_manager):
        a = _artiste(data_manager)
        t = _fiche(data_manager, a)
        _sql(
            data_manager,
            "INSERT INTO source_usage_failures (source_key, issue, track_id) "
            "VALUES ('spotify_embed', 'timeout', :t)",
            t=t.id,
        )
        data_manager.delete_artist(a.id)
        with closing(au._connexion(data_manager.db_path)) as c:
            assert not au.orphelins(c)

    def test_le_journal_d_une_page_supprimee_n_est_pas_un_orphelin(self, data_manager):
        """« Rétablir » n'y relit que `annulation`, jamais `track_id`."""
        a = _artiste(data_manager)
        _sql(
            data_manager,
            "INSERT INTO revue_corrections (artist_id, detecteur, cle, track_id, action, annulation) "
            """VALUES (:a, 'annotee', 'k', 99999, 'regle:supprimer_non_musique', """
            """'{"type": "page_supprimee", "genius_id": 1, "titre": "X"}')""",
            a=a.id,
        )
        with closing(au._connexion(data_manager.db_path)) as c:
            assert not au.orphelins(c)

    def test_une_autre_correction_sans_fiche_en_est_un(self, data_manager):
        a = _artiste(data_manager)
        _sql(
            data_manager,
            "INSERT INTO revue_corrections (artist_id, detecteur, cle, track_id, action) "
            "VALUES (:a, 'x', 'k', 99999, 'regle:y')",
            a=a.id,
        )
        with closing(au._connexion(data_manager.db_path)) as c:
            assert [x.libelle for x in au.orphelins(c)] == [
                "revue_corrections : 1 ligne(s) sans fiche"
            ]


class TestCommentaire:
    @pytest.mark.parametrize(
        "titre", ["PA Nights - Commentary", "Alright (Commentary)", "Now (Commentary Version)"]
    )
    def test_un_commentaire_est_court_par_nature(self, titre):
        assert court_par_nature(titre)

    def test_un_morceau_n_en_est_pas_un(self):
        assert not court_par_nature("Commentaries")
        assert not court_par_nature("PA Nights")

    def test_le_verdict_le_lit_aussi(self):
        """`court_par_nature` est partagé avec `resoudre_duree` : une vidéo de 15 s
        fait la durée d'un commentaire, pas celle d'un morceau."""
        obs = [Observation("duration", 15, "youtube_video")]
        ordre = DISCOGRAPHY_PRIORITIES["duration"]
        assert resoudre_duree(obs, ordre, "PA Nights - Commentary").value == 15
        assert resoudre_duree(obs, ordre, "PA Nights") is None


class TestDisqueDeSessions:
    @staticmethod
    def _p(i, titre, album):
        return {
            "id": i,
            "title": titre,
            "artist_id": 1,
            "artiste": "Kery James",
            "album": album,
            "genius_id": None,
            "spotify_id": None,
        }

    def test_une_exclusivite_d_une_plateforme_n_est_pas_un_homonyme(self):
        pistes = [
            self._p(i, f"{t} (Deezer Session)", "Deezer Session")
            for i, t in enumerate(("Racailles", "Musique nègre", "Vivre ou mourir ensemble"))
        ]
        assert au.disques_homonymes(pistes) == []

    def test_un_disque_d_homonyme_seulement_live_reste_signale(self):
        pistes = [self._p(i, f"Concerto {i} (Live)", "Flute Concertos") for i in range(3)]
        assert [c.famille for c in au.disques_homonymes(pistes)] == ["disque_homonyme"]

    def test_une_seule_piste_hors_session_suffit(self):
        pistes = [
            self._p(0, "A (Deezer Session)", "Deezer Session"),
            self._p(1, "B (Deezer Session)", "Deezer Session"),
            self._p(2, "C", "Deezer Session"),
        ]
        assert [c.famille for c in au.disques_homonymes(pistes)] == ["disque_homonyme"]


def test_les_familles_sont_declarees_et_hors_critere():
    for famille in (
        "colonne_sans_provenance",
        "id_spotify_sans_compteur",
        "fiche_sans_participation",
        "lien_jamais_mesure",
    ):
        assert famille in au.FAMILLES and famille not in au.INVARIANTS
