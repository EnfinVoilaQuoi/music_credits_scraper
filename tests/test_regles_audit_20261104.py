"""Les règles nées du point d'audit du 2026-11-04.

Disque d'un homonyme sur un même profil Deezer (Anton Serra le flûtiste), pistes
d'un disque Deezer dont la fiche n'a pas de durée ou une autre (Souffrance,
*Noctambus*), coupe de catalogue (« We Back »), segment de DJ mix, vues expliquées
par la publication de l'interprète principal.
"""

import pytest
from sqlalchemy import text

from src.enrichment.observation import Observation
from src.models import Artist, Track
from src.models.track import TrackVideo
from src.services import audit as au
from src.services import ecarts_deezer as ed
from src.services import politiques_revue as pol
from src.services import revue, revue_auto
from src.utils import corrections_fiches as cf

_A = Artist(name="Souffrance")


def _t(titre, tid=1, **kw):
    t = Track(title=titre, artist=_A)
    t.id = tid
    t.durees_editions = ()
    texte = kw.pop("paroles", None)
    if texte:
        t.lyrics.text = texte
    for k, v in kw.items():
        setattr(t, k, v)
    return t


class TestFicheDuDisque:
    def _juger(self, titre, duree, *fiches, disque="Noctambus"):
        ecart = {"piste": {"title": titre, "duration": duree}, "album": {"title": disque}}
        cas = revue.Cas("deezer_absent", None, titre, "", 0, {"ecart": ecart}, "k")
        ctx = revue.contexte(list(fiches), {})
        return pol._rattacher_a_la_fiche_du_disque(cas, ctx), pol._edition_du_disque(cas, ctx)

    def test_une_fiche_sans_duree_recoit_la_piste(self):
        t = _t("Matin", 16920, album="Noctambus")
        lien, edition = self._juger("Matin ( 2020 )", 56, t)
        assert lien.action == "deezer_rattacher_fiche" and "fiche sans durée" in lien.sens
        assert edition is None

    def test_une_autre_duree_est_une_edition(self):
        t = _t("Pangolin", 16912, album="Noctambus", duration=149, deezer_id=3346152831)
        lien, edition = self._juger("Pangolin ( 2020 )", 122, t)
        assert lien is None and edition.action == "deezer_edition"

    def test_la_meme_duree_reste_le_rattachement(self):
        t = _t("Pangolin", 16912, album="Noctambus", duration=122)
        lien, edition = self._juger("Pangolin ( 2020 )", 122, t)
        assert lien is not None and edition is None

    def test_des_paroles_qui_ne_tiennent_pas_font_une_edition(self):
        t = _t("Matin", 16920, album="Noctambus", paroles=" ".join(["mot"] * 500))
        lien, edition = self._juger("Matin ( 2020 )", 30, t)
        assert lien is None and edition.action == "deezer_edition"

    def test_un_autre_disque_ne_rattache_rien(self):
        t = _t("Matin", 16920, album="Klassik", duration=None)
        assert self._juger("Matin ( 2020 )", 56, t) == (None, None)

    def test_deux_fiches_au_meme_titre_vont_a_l_humain(self):
        a, b = _t("Matin", 1, album="Noctambus"), _t("Matin", 2, album="Noctambus")
        assert self._juger("Matin ( 2020 )", 56, a, b) == (None, None)

    def test_un_generique_n_est_jamais_une_edition(self):
        t = _t("Intro", 3, album="Noctambus", duration=60)
        assert self._juger("Intro", 90, t) == (None, None)


class TestDisqueEtranger:
    @staticmethod
    def _album(i, genres, pistes):
        return ed.AlbumDeezer(
            id=i,
            title=f"A{i}",
            genres=genres,
            pistes=[ed.PisteDeezer(id=i * 100 + p, title=f"T{p}") for p in range(pistes)],
        )

    @staticmethod
    def _ecarts(album):
        return [ed.Ecart(nature="absent", album=album, piste=p, coche=True) for p in album.pistes]

    def test_disque_entier_d_un_autre_genre(self):
        rap, flute = self._album(1, ["Rap/Hip Hop"], 3), self._album(2, ["Classical"], 4)
        ecarts = self._ecarts(flute)
        ed.marquer_disques_etrangers([rap, flute], ecarts)
        assert all(e.disque_etranger and not e.coche for e in ecarts)
        assert "Classical" in ecarts[0].motifs[0]

    def test_un_disque_du_meme_genre_reste_proposable(self):
        rap, autre = self._album(1, ["Rap/Hip Hop"], 3), self._album(2, ["Rap/Hip Hop"], 8)
        ecarts = self._ecarts(autre)
        ed.marquer_disques_etrangers([rap, autre], ecarts)
        assert not any(e.disque_etranger for e in ecarts)

    def test_un_disque_dont_une_piste_est_connue_n_est_pas_etranger(self):
        rap, rock = self._album(1, ["Rap/Hip Hop"], 3), self._album(2, ["Rock"], 4)
        ecarts = self._ecarts(rock)[:-1]
        ed.marquer_disques_etrangers([rap, rock], ecarts)
        assert not any(e.disque_etranger for e in ecarts)

    def test_un_single_d_un_autre_genre_n_est_pas_un_homonyme(self):
        rap, single = self._album(1, ["Rap/Hip Hop"], 3), self._album(2, ["Pop"], 1)
        ecarts = self._ecarts(single)
        ed.marquer_disques_etrangers([rap, single], ecarts)
        assert not any(e.disque_etranger for e in ecarts)

    def test_le_panneau_le_laisse_a_l_humain(self):
        ecart = {
            "piste": {"title": "Ballade", "duration": 535},
            "album": {"title": "Flute and Piano"},
            "disque_etranger": True,
        }
        cas = revue.Cas("deezer_album_absent", None, "Ballade", "", 0, {"ecart": ecart}, "k")
        juge = pol.arbitrage_deezer(cas, None, revue.contexte([], {}), {}, {1}, lambda _: None)
        assert juge is None

    def test_le_signalement_garde_la_marque(self):
        album = self._album(2, ["Classical"], 1)
        e = ed.Ecart(nature="absent", album=album, piste=album.pistes[0], disque_etranger=True)
        d = ed.ecart_vers_dict(e)
        assert d["album"]["genres"] == ["Classical"]
        assert ed.ecart_depuis(d, {}).disque_etranger


class TestRefusDeDisque:
    def test_refuser_puis_oublier(self, data_manager):
        assert cf.disques_refuses("Anton Serra") == set()
        assert cf.memoriser_disque_refuse("Anton Serra", 6294406, "Flute and Piano")
        assert not cf.memoriser_disque_refuse("Anton Serra", 6294406, "Flute and Piano")
        assert cf.disques_refuses("Anton Serra") == {6294406}
        assert cf.disques_refuses("Booba") == set()
        assert cf.oublier_disque_refuse("Anton Serra", 6294406)
        assert cf.disques_refuses("Anton Serra") == set()

    @staticmethod
    def _disque(dm, artiste, titres, *, genius=None):
        with dm.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO releases (id, artist_id, title, deezer_album_id, scope, "
                    "identity_key) VALUES (900, :a, 'Flute and Piano', 6294406, 'own', "
                    "'deezer:6294406')"
                ),
                {"a": artiste.id},
            )
        ids = []
        for i, titre in enumerate(titres):
            t = Track(title=titre, artist=artiste, album="Flute and Piano", deezer_id=100 + i)
            if genius and i == 0:
                t.genius_id = genius
            ids.append(dm.save_track(t))
            with dm.engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO release_tracks (release_id, track_id, source) "
                        "VALUES (900, :t, 'deezer')"
                    ),
                    {"t": ids[-1]},
                )
        return ids

    @staticmethod
    def _restantes(dm):
        with dm.engine.connect() as conn:
            return (
                conn.execute(text("SELECT COUNT(*) FROM tracks")).scalar(),
                conn.execute(text("SELECT COUNT(*) FROM releases WHERE id = 900")).scalar(),
            )

    def test_le_script_a_blanc_puis_apply_puis_oublier(self, data_manager, capsys):
        import scripts.retirer_disque_homonyme as script

        a = Artist(name="Anton Serra")
        a.id = data_manager.save_artist(a)
        self._disque(data_manager, a, ["Ballade", "Romance", "Suite"])
        args = ["Anton Serra", "--base", str(data_manager.db_path)]

        assert script.main(args) == 0
        assert "Flute and Piano" in capsys.readouterr().out
        assert self._restantes(data_manager) == (3, 1)

        assert script.main([*args, "--apply"]) == 0
        assert self._restantes(data_manager) == (0, 0)
        assert cf.disques_refuses("Anton Serra") == {6294406}

        assert script.main([*args, "--oublier", "6294406", "--apply"]) == 0
        assert cf.disques_refuses("Anton Serra") == set()

    def test_le_script_garde_un_disque_a_page_genius(self, data_manager, capsys):
        import scripts.retirer_disque_homonyme as script

        a = Artist(name="Anton Serra")
        a.id = data_manager.save_artist(a)
        self._disque(data_manager, a, ["Ballade", "Romance", "Suite"], genius=55)
        args = ["Anton Serra", "--base", str(data_manager.db_path), "--disque", "6294406"]
        assert script.main([*args, "--apply"]) == 0
        assert "gardé" in capsys.readouterr().out
        assert self._restantes(data_manager) == (3, 1)
        assert cf.disques_refuses("Anton Serra") == set()


class TestAuditDisqueHomonyme:
    @staticmethod
    def _p(i, album, genius=None, spotify=None):
        return {
            "id": i,
            "artist_id": 1,
            "artiste": "Anton Serra",
            "title": f"Piste {i}",
            "album": album,
            "genius_id": genius,
            "spotify_id": spotify,
        }

    def test_disque_sans_aucun_lien(self):
        cas = au.disques_homonymes([self._p(i, "Flute and Piano") for i in range(3)])
        assert [c.famille for c in cas] == ["disque_homonyme"] and cas[0].pistes == [0, 1, 2]

    @pytest.mark.parametrize("lien", [{"genius": 5}, {"spotify": "x"}])
    def test_une_fiche_liee_suffit(self, lien):
        pistes = [self._p(0, "D"), self._p(1, "D"), self._p(2, "D", **lien)]
        assert au.disques_homonymes(pistes) == []

    def test_sous_le_seuil(self):
        assert au.disques_homonymes([self._p(i, "D") for i in range(2)]) == []

    def test_famille_declaree(self):
        assert "disque_homonyme" in au.FAMILLES


class TestSegmentDeDjMix:
    def test_apple_refuse_le_segment_sur_une_fiche_normale(self):
        from src.utils.apple_identity import fiche_concorde

        t = Track(title="Lose Yourself (Drum & Bass Remix)", artist=Artist(name="Eminem"))
        fiche = {
            "artistName": "Eminem",
            "trackName": "Lose Yourself (Remix) [Mixed]",
            "collectionName": "Ushuaïa Ibiza: Sep 2, 2024 (DJ Mix)",
        }
        ok, motif = fiche_concorde(t, fiche)
        assert not ok and "titre" in motif or "DJ mix" in motif

    def test_apple_garde_le_segment_sur_une_fiche_de_segment(self):
        from src.utils.apple_identity import fiche_concorde

        t = Track(title="Big Shot (Remix) [Mixed]", artist=Artist(name="Travis Scott"))
        fiche = {
            "artistName": "Travis Scott",
            "trackName": "Big Shot (Remix) [Mixed]",
            "collectionName": "SuperNova for Brunch Bounce (DJ Mix)",
        }
        assert fiche_concorde(t, fiche)[0]

    def test_le_titre_servi_d_un_segment_n_est_pas_l_enregistrement(self):
        from src.utils.spotify_identity import meme_enregistrement_pour

        t = Track(title="Song (Remix)", artist=Artist(name="X"))
        assert meme_enregistrement_pour(t, "Song (Remix)")
        assert not meme_enregistrement_pour(t, "Song (Remix) [Mixed]")


def _video(titre, duree, chaine="", source="genius_media", video_id="V", views=1000):
    return TrackVideo(
        video_id=video_id, title=titre, duration=duree, channel=chaine, source=source, views=views
    )


class TestCoupeDeCatalogue:
    _PAROLES = " ".join(["mot"] * 727)

    def _fiche(self, *videos, duree=48, titre="We Back", principal="D12", **kw):
        return _t(
            titre,
            13686,
            duration=duree,
            paroles=kw.pop("paroles", self._PAROLES),
            videos=list(videos),
            primary_artist_name=principal,
            **kw,
        )

    def test_la_video_de_la_page_est_le_morceau_entier(self):
        t = self._fiche(_video("D12 - We Back", 251, "xBloodspillx"))
        motif, preuves = revue.coupe_de_catalogue(t, revue.contexte([t], {}))
        assert "48 s pour 727 mots" in motif
        assert preuves["duree_video"] == 251 and preuves["duree_catalogue"] == 48

    @pytest.mark.parametrize(
        "video",
        [
            # Le débit n'est pas celui d'un morceau : un film, une émission.
            _video("D12 - We Back", 800, "x"),
            # Plus de 15 min.
            _video("D12 - We Back", 1000, "x"),
            # Un clip a son propre montage.
            _video("D12 - We Back (Official Video)", 251, "x"),
            # Ne nomme pas le morceau.
            _video("D12 - Purple Pills", 251, "x"),
            # Une autre version.
            _video("D12 - We Back (Live)", 251, "x"),
            # L'interprète principal n'est nommé nulle part.
            _video("We Back", 251, "Quelqu'un"),
            # Pas la vidéo de la page.
            _video("D12 - We Back", 251, "x", source="search_auto"),
        ],
    )
    def test_les_garde_fous(self, video):
        t = self._fiche(video)
        assert revue.coupe_de_catalogue(t, revue.contexte([t], {})) is None

    def test_deux_videos_de_la_page_ne_tranchent_pas(self):
        t = self._fiche(
            _video("D12 - We Back", 251, "x"), _video("D12 - We Back", 240, "y", video_id="W")
        )
        assert revue.coupe_de_catalogue(t, revue.contexte([t], {})) is None

    def test_sous_huit_mots_par_seconde_rien(self):
        t = self._fiche(_video("D12 - We Back", 251, "x"), duree=120)
        assert revue.coupe_de_catalogue(t, revue.contexte([t], {})) is None

    def test_un_segment_de_dj_mix_n_est_pas_juge(self):
        t = self._fiche(_video("D12 - We Back", 251, "x"), titre="We Back [Mixed]")
        assert revue.coupe_de_catalogue(t, revue.contexte([t], {})) is None

    def test_formel(self):
        assert "coupe_catalogue" in revue.CODES_FORMELS
        assert "coupe_catalogue" in revue_auto._CORRECTEURS


def test_coupe_corrigee_puis_retablie(data_manager):
    a = Artist(name="D12")
    a.id = data_manager.save_artist(a)
    t = Track(title="We Back", artist=a, genius_id=53302, deezer_id=3406285301)
    t.lyrics.text, t.lyrics.source, t.lyrics.present = " ".join(["mot"] * 727), "genius", True
    t.observations.append(Observation("duration", 48, "deezer"))
    tid = data_manager.save_track(t)
    data_manager.record_track_videos(
        tid, [_video("D12 - We Back", 251, "xBloodspillx", video_id="p_Z_2vrGt4w")]
    )

    def _duree():
        with data_manager.engine.connect() as conn:
            return conn.execute(
                text("SELECT duration FROM tracks WHERE id = :t"), {"t": tid}
            ).scalar()

    assert _duree() == 48
    a.tracks = data_manager.get_artist_tracks(a.id)
    fiche = next(x for x in a.tracks if x.id == tid)
    motif, preuves = revue.coupe_de_catalogue(fiche, revue.contexte(a.tracks, {}))
    cas = revue.Cas("coupe_catalogue", tid, "We Back", motif, 0, preuves, "k")
    _, annulation = revue_auto._corriger_coupe(data_manager, fiche, cas)
    assert _duree() == 251
    editions = data_manager.get_track_editions(tid)
    assert [(e["label"], e["duration"]) for e in editions] == [("Coupe de catalogue", 48)]
    a.tracks = data_manager.get_artist_tracks(a.id)
    fiche = next(x for x in a.tracks if x.id == tid)
    # Plus rien à corriger : idempotent.
    assert revue.coupe_de_catalogue(fiche, revue.contexte(a.tracks, {})) is None

    assert annulation["type"] == "coupe"
    data_manager.retirer_duree_manuelle(tid)
    data_manager.retirer_edition(tid, annulation["label"])
    assert _duree() == 48
    assert data_manager.get_track_editions(tid) == []


class TestVuesExpliquees:
    @staticmethod
    def _fiche(titre, principal, *videos, **kw):
        return _t(titre, 1, primary_artist_name=principal, videos=list(videos), **kw)

    def test_le_remix_de_l_interprete_de_la_page(self):
        t = self._fiche(
            "Ass Like That", "Termik", _video("Eminem -  Ass Like That  (Termik Remix)", 266, "C")
        )
        assert revue._vues_expliquees(t, revue.contexte([t], {}))

    def test_le_remix_d_un_autre(self):
        t = self._fiche(
            "Ass Like That", "Eminem", _video("Eminem -  Ass Like That  (Termik Remix)", 266, "C")
        )
        assert not revue._vues_expliquees(t, revue.contexte([t], {}))

    def test_la_chaine_topic_de_l_interprete(self):
        t = self._fiche(
            "Intro (Get Rich or Die Tryin’)", "50 Cent", _video("Intro", 7, "50 Cent - Topic")
        )
        assert revue._vues_expliquees(t, revue.contexte([t], {}))

    def test_la_chaine_topic_d_un_autre(self):
        t = self._fiche(
            "Intro (Get Rich or Die Tryin’)", "50 Cent", _video("Intro", 7, "Eminem - Topic")
        )
        assert not revue._vues_expliquees(t, revue.contexte([t], {}))

    def test_une_video_de_la_recherche_ne_compte_pas(self):
        t = self._fiche(
            "Intro (Get Rich or Die Tryin’)",
            "50 Cent",
            _video("Intro", 7, "50 Cent - Topic", source="search_auto"),
        )
        assert not revue._vues_expliquees(t, revue.contexte([t], {}))

    def test_un_id_spotify_d_un_autre_titre_ne_l_explique_pas(self):
        t = self._fiche(
            "Intro (Get Rich or Die Tryin’)",
            "50 Cent",
            _video("Intro", 7, "50 Cent - Topic"),
            spotify_page_title="Heat • 50 Cent",
        )
        assert not revue._vues_expliquees(t, revue.contexte([t], {}))


class TestIsrcReemploye:
    def _juger(self, duree, **catalogues):
        from src.enrichment.providers.reccobeats import ReccoBeatsProvider

        t = _t("Compas", 17699)
        t.durations_observees = catalogues
        return ReccoBeatsProvider._isrc_d_un_autre_morceau(t, {"duration": duree})

    def test_tous_les_catalogues_le_dementent(self):
        assert self._juger(314, deezer=169, spotify_web=169, apple_music=169)

    def test_un_seul_catalogue_d_accord_suffit_a_garder(self):
        assert not self._juger(314, deezer=169, spotify_web=314)

    def test_sans_catalogue_ni_duree_rien(self):
        assert not self._juger(314, songbpm=169)
        assert not self._juger(None, deezer=169)


def test_changer_l_id_principal_chez_les_soeurs(data_manager):
    from src.models.track import TrackSpotifyId

    a, b = Artist(name="Anton Serra"), Artist(name="Lucio Bukowski")
    a.id, b.id = data_manager.save_artist(a), data_manager.save_artist(b)
    ta = Track(title="Interlubrique", artist=a, genius_id=740335, spotify_id="ANTON")
    tb = Track(title="Interlubrique", artist=b, genius_id=740335, spotify_id="LUCIO")
    ida, idb = data_manager.save_track(ta), data_manager.save_track(tb)
    for tid in (ida, idb):
        data_manager.record_track_spotify_ids(
            tid, [TrackSpotifyId(spotify_id="ANTON"), TrackSpotifyId(spotify_id="LUCIO")]
        )

    def _ids():
        with data_manager.engine.connect() as conn:
            return [
                conn.execute(
                    text("SELECT spotify_id FROM tracks WHERE id = :t"), {"t": tid}
                ).scalar()
                for tid in (ida, idb)
            ]

    assert not data_manager.changer_id_spotify_principal(ida, "INCONNU")
    assert data_manager.changer_id_spotify_principal(ida, "LUCIO")
    assert _ids() == ["LUCIO", "LUCIO"]
    connus = {e.spotify_id for e in data_manager.get_track_spotify_ids(ida)}
    assert connus == {"ANTON", "LUCIO"}


class TestRefusDIsrc:
    @staticmethod
    def _fiche(dm):
        a = Artist(name="Vald")
        a.id = dm.save_artist(a)
        t = Track(title="Compas", artist=a, genius_id=2369672, isrc="FRPET1500160")
        t.id = dm.save_track(t)
        return t

    def test_refuser_retire_et_empeche_la_repose(self, data_manager):
        from src.services import audit as module_audit

        t = self._fiche(data_manager)
        data_manager.record_discography_observations(
            t.id, [Observation("isrc", "FRPET1500160", "deezer")]
        )
        cf.memoriser_isrc_refuse(t, "frpet1500160")
        assert cf.isrcs_refuses(t) == {"FRPET1500160"}
        # Encore porté : l'invariant le voit.
        audit = module_audit.auditer(data_manager, ["Vald"], revue=False)
        assert "refus_vivant" in {c.famille for c in audit.cas}

        data_manager.clear_track_isrc(t.id)
        audit = module_audit.auditer(data_manager, ["Vald"], revue=False)
        assert "refus_vivant" not in {c.famille for c in audit.cas}
        assert cf.oublier_isrc_refuse(t, "FRPET1500160")
        assert cf.isrcs_refuses(t) == set()

    def test_les_ecarts_deezer_ne_le_reposent_pas(self, data_manager):
        t = self._fiche(data_manager)
        data_manager.clear_track_isrc(t.id)
        t.isrc = None
        cf.memoriser_isrc_refuse(t, "FRPET1500160")
        album = ed.AlbumDeezer(id=1, title="Compas")
        piste = ed.PisteDeezer(id=1435250522, title="Compas", isrc="FRPET1500160", duration=169)
        ed._renseigner_fiche(
            data_manager, ed.Ecart(nature="link", album=album, piste=piste, existing_track=t), t
        )
        with data_manager.engine.connect() as conn:
            valeur = conn.execute(
                text("SELECT isrc FROM tracks WHERE id = :t"), {"t": t.id}
            ).scalar()
        assert valeur is None


class TestMemeEnregistrementProuve:
    _PAROLES = " ".join(f"mot{i}" for i in range(200))

    def _pages(self, **autre):
        a = _t("Real Niggas Stand Up", 15749, genius_id=1, duration=238, deezer_id=1, isrc="USKO1")
        b = _t("Real n***** stand up", 16074, genius_id=2, duration=238, deezer_id=2, isrc="usko1")
        for t in (a, b):
            t.spotify_id = "5QJX"
            t.lyrics.text, t.lyrics.source = self._PAROLES, "genius"
        for k, v in autre.items():
            setattr(b, k, v)
        return a, b

    def test_toutes_les_preuves(self):
        from src.utils.catalogue_designe import meme_enregistrement_prouve

        assert meme_enregistrement_prouve(*self._pages())

    @pytest.mark.parametrize(
        "ecart",
        [
            {"isrc": "AUTRE"},
            {"isrc": None},
            {"deezer_id": 1},  # la même page Deezer : l'ISRC n'en est pas une seconde preuve
            {"deezer_id": None},
            {"duration": 241},
            {"duration": None},
        ],
    )
    def test_une_preuve_manque(self, ecart):
        from src.utils.catalogue_designe import meme_enregistrement_prouve

        assert not meme_enregistrement_prouve(*self._pages(**ecart))

    def test_d_autres_paroles(self):
        from src.utils.catalogue_designe import meme_enregistrement_prouve

        a, b = self._pages()
        b.lyrics.text = " ".join(f"autre{i}" for i in range(200))
        assert not meme_enregistrement_prouve(a, b)

    def test_des_paroles_qui_ne_viennent_pas_de_genius_ne_prouvent_rien(self):
        from src.utils.catalogue_designe import meme_enregistrement_prouve

        a, b = self._pages()
        b.lyrics.source = "ytmusic"
        assert not meme_enregistrement_prouve(a, b)

    def test_le_cas_dit_de_fusionner_et_la_regle_ne_retire_rien(self):
        a, b = self._pages()
        ctx = revue.contexte([a, b], {})
        motif, preuves = revue.id_catalogue_partage(a, ctx)
        assert "à fusionner" in motif and preuves["meme_enregistrement"]
        cas = revue.Cas("id_catalogue_partage", a.id, a.title, motif, 0, preuves, "k")

        def jamais(_id):
            raise AssertionError("aucune lecture du catalogue")

        assert pol.politique_catalogue_designe(jamais, jamais)(cas, a, ctx) is None

    def test_sans_la_preuve_la_regle_juge_comme_avant(self):
        a, b = self._pages(isrc=None)
        ctx = revue.contexte([a, b], {})
        motif, preuves = revue.id_catalogue_partage(a, ctx)
        assert "à quelle page" in motif and "meme_enregistrement" not in preuves


class TestDureeIsoleeSansCatalogueContre:
    def test_une_duree_tranchee_a_la_main_ne_fait_pas_de_cas(self):
        t = _t("We Back", 13686, duration=251)
        t.durations_observees = {"manual": 251, "deezer": 48, "ytmusic": 48}
        assert revue.duree_isolee(t) is None

    def test_des_catalogues_contre_restent_un_cas(self):
        t = _t("Vice Versa", 1, duration=240)
        t.durations_observees = {
            "youtube": 277,
            "spotify_web": 240,
            "deezer": 240,
            "apple_music": 240,
        }
        motif, _ = revue.duree_isolee(t)
        assert "démentie par" in motif
