"""Prises live « absentes » d'un disque propre (demande utilisateur 2026-11-25).

Cas réels de la simulation du 2026-11-25 : IAM *Live au Dôme de Marseille*,
Casseurs Flowters *Live Tour 2016*, NTM, Mac Miller *Live From Space*."""

from src.models import Artist, Track
from src.services import ecarts_deezer as ed
from src.services import politiques_revue as pol
from src.services import revue
from src.utils.version_heritage import socle_parmi


def _t(titre, tid=1, **kw):
    t = Track(title=titre, artist=Artist(name="IAM"))
    t.id = tid
    t.durees_editions = ()
    for k, v in kw.items():
        setattr(t, k, v)
    return t


def _juger(titre, *fiches, isrcs=None, artist_id=7, detecteur="deezer_absent", **ecart):
    preuves = {
        "ecart": {
            "nature": "absent",
            "piste": {"id": 9, "title": titre, "isrc": "FRY680400229", "duration": 234},
            "album": {"id": 1, "title": "Live au Dôme de Marseille", "artist_id": artist_id},
            **ecart,
        }
    }
    cas = revue.Cas(detecteur, None, titre, "", 0, preuves, "k")
    return pol._live_sans_original(cas, revue.contexte(fiches, {}, []), isrcs or {}, {7})


class TestLiveSansOriginal:
    def test_la_fiche_se_cree(self):
        for detecteur in ("deezer_absent", "deezer_album_absent"):
            arb = _juger("Le Barème (Live)", detecteur=detecteur)
            assert arb.action == "deezer_appliquer"
            assert (detecteur, arb.action) in pol.REGLES_DEEZER

    def test_un_titre_studio_reste_hors_de_la_regle(self):
        """« Live de la base » : le mot « live » n'est pas une version."""
        assert _juger("Live de la base") is None

    def test_le_disque_d_un_autre_reste_a_l_humain(self):
        assert _juger("Le Barème (Live)", artist_id=8) is None

    def test_un_isrc_connu_ou_une_page_genius_reste_a_l_humain(self):
        assert _juger("Le Barème (Live)", isrcs={"FRY680400229": "x"}) is None
        assert _juger("Le Barème (Live)", genius={"id": 3}) is None

    def test_une_fiche_qui_porte_deja_la_version(self):
        assert _juger("Le Barème (Live)", _t("Le barème (Live)", duration=90)) is None

    def test_la_meme_prise_du_disque_sous_un_autre_titre(self):
        """« BDE (Best Day Ever) (Live) » 322 s = « Best Day Ever (Live) » 323 s."""
        fiche = _t("Barème, le (Live)", duration=235, album="Live au Dôme de Marseille")
        assert _juger("Le Barème (Live)", fiche) is None
        fiche.album = "Anthologie"
        assert _juger("Le Barème (Live)", fiche) is not None

    def test_un_medley_s_annonce(self):
        assert _juger("Medley 1 (A / B) (Live)") is not None
        assert _juger("Medley: Check the Flow / Respire (Live)") is not None
        assert _juger("Check the Flow / Respire (Live)") is None

    def test_un_titre_generique_reste_a_l_humain(self):
        assert _juger("Intro (Live)") is None


class TestHoraire:
    def test_l_original_a_l_horaire_pres(self):
        original = _t("06h16 - Des histoires à raconter", 5)
        assert socle_parmi("Des histoires à raconter (Live)", [original]) is original

    def test_le_titre_exact_passe_avant(self):
        exact = _t("Inachevés", 5)
        assert socle_parmi("Inachevés (Live)", [_t("06h16 - Inachevés", 6), exact]) is exact

    def test_deux_horaires_sont_ambigus(self):
        fiches = [_t("06h16 - Suite", 5), _t("07h16 - Suite", 6)]
        assert socle_parmi("Suite (Live)", fiches) is None

    def test_la_fiche_creee_nait_reliee(self, data_manager):
        artiste = Artist(name="Casseurs Flowters")
        data_manager.save_artist(artiste)
        original = Track(title="06h16 - Des histoires à raconter", artist=artiste)
        data_manager.save_track(original)
        album = ed.AlbumDeezer(
            id=1,
            title="Live Tour 2016",
            artist_id=7,
            artist_name="Casseurs Flowters",
            record_type="album",
            release_date="2016-10-28",
            nb_tracks=15,
        )
        piste = ed.PisteDeezer(
            id=9, title="Des histoires à raconter (Live)", isrc="FR6F31601800", duration=242
        )
        ed.creer_lignes(data_manager, artiste, [ed.Ecart("absent", album, piste)])
        fiches = {t.title: t for t in data_manager.get_artist_tracks(artiste.id)}
        relations = fiches["Des histoires à raconter (Live)"].relationships
        assert [r["track_id"] for r in relations] == [fiches[original.title].id]
