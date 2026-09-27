"""Panneau « À trancher » : détecteurs PURS (`src/services/revue`, 2026-09-27)."""

from types import SimpleNamespace

from src.enrichment.observation import Observation
from src.models import Artist, Track
from src.models.track import Credit, CreditRole, TrackVideo
from src.services import revue

_A = Artist(name="A2H")


def _t(titre, tid=1, **kw):
    t = Track(title=titre, artist=_A)
    t.id = tid
    for k, v in kw.items():
        setattr(t, k, v)
    return t


def _ctx(*tracks, obs=None, relations=None):
    return revue.contexte(tracks, obs, relations)


class TestDurees:
    def test_songbpm_dementi_par_youtube(self):
        t = _t("Blues (Live at AK Studios)", durations_observees={"youtube": 303, "songbpm": 176})
        motif, preuves = revue.duree_songbpm_dementie(t)
        assert "176" in motif and preuves == {"youtube": 303, "songbpm": 176}

    def test_ecart_d_encodage_tolere(self):
        # Louisville : 197 s / 193 s — dans la marge, la durée ne tranche pas.
        t = _t("Louisville", durations_observees={"youtube": 197, "songbpm": 193})
        assert revue.duree_songbpm_dementie(t) is None

    def test_id_spotify_dementi(self):
        t = _t("X", spotify_id="SP", durations_observees={"youtube": 200, "reccobeats": 150})
        assert "autre enregistrement" in revue.duree_spotify_dementie(t)[0]

    def test_sans_audio_youtube_rien(self):
        t = _t("X", spotify_id="SP", durations_observees={"reccobeats": 150})
        assert revue.duree_spotify_dementie(t) is None

    def test_generique_meme_duree(self):
        a, b = _t("Intro", 1, duration=94), _t("Outro", 2, duration=94)
        c = _t("Matrix (Intro)", 3, duration=94)  # le morceau Matrix, pas une intro
        ctx = _ctx(a, b, c)
        assert "« Outro »" in revue.generique_meme_duree(a, ctx)[0]
        assert revue.generique_meme_duree(c, ctx) is None


class TestMesures:
    def _obs(self, **par_source):
        return [
            Observation(champ, valeur, source)
            for source, champs in par_source.items()
            for champ, valeur in champs.items()
        ]

    def test_bpm_en_desaccord(self):
        t = _t("Lunatic")
        ctx = _ctx(t, obs={1: self._obs(getsongbpm={"bpm": 83}, reccobeats={"bpm": 130})})
        assert "getsongbpm 83" in revue.bpm_desaccord(t, ctx)[0]

    def test_octave_et_repli_ne_sont_pas_un_desaccord(self):
        t = _t("X")
        obs = self._obs(songbpm={"bpm": 70}, reccobeats={"bpm": 140}, heritage={"bpm": 99})
        assert revue.bpm_desaccord(t, _ctx(t, obs={1: obs})) is None

    def test_tonalite_relative_ou_incomplete_non_signalee(self):
        t = _t("X")
        # do majeur (0,1) et la mineur (9,0) : même armure ; SongBPM sans mode.
        obs = self._obs(
            getsongbpm={"key": 0, "mode": 1}, reccobeats={"key": 9, "mode": 0}, songbpm={"key": 4}
        )
        assert revue.tonalite_desaccord(t, _ctx(t, obs={1: obs})) is None

    def test_tonalite_en_desaccord(self):
        t = _t("X")
        obs = self._obs(getsongbpm={"key": 11, "mode": 1}, reccobeats={"key": 4, "mode": 0})
        assert "si majeur" in revue.tonalite_desaccord(t, _ctx(t, obs={1: obs}))[0]

    def test_version_aux_mesures_de_l_original(self):
        o, v = _t("Louisville", 1), _t("Louisville (Remix)", 2)
        i = _t("Louisville (Instrumental)", 3)
        mesures = self._obs(songbpm={"bpm": 142, "duration": 196})
        ctx = _ctx(o, v, i, obs={1: mesures, 2: mesures, 3: mesures})
        assert "« Louisville »" in revue.songbpm_copie_de_l_original(v, ctx)[0]
        # Un instrumental a le même beat : attendu (décision utilisateur).
        assert revue.songbpm_copie_de_l_original(i, ctx) is None


class TestLrc:
    def _avec(self, lrc):
        t = _t("X")
        t.lyrics.text = " ".join(f"mot{i}" for i in range(20))
        t.lyrics.source = "genius"
        t.lyrics.synced = lrc
        t.lyrics.synced_source = "YouTube Music"
        return t

    def test_tranche_ambigue_signalee(self):
        lrc = (
            " ".join(f"mot{i}" for i in range(10)) + " " + " ".join(f"autre{i}" for i in range(10))
        )
        assert "50%" in revue.lrc_douteux(self._avec(lrc))[0]

    def test_lrc_confirme_ou_dementi_non_signale(self):
        assert revue.lrc_douteux(self._avec(" ".join(f"mot{i}" for i in range(20)))) is None
        assert revue.lrc_douteux(self._avec(" ".join(f"autre{i}" for i in range(20)))) is None


class TestDoublons:
    def test_casse_et_ponctuation(self):
        a, b = _t("Pour de vrai", 1), _t("Pour de Vrai", 2)
        assert "Pour de Vrai" in revue.doublon_de_titre(a, _ctx(a, b))[0]

    def test_une_version_n_est_pas_un_doublon(self):
        a, b = _t("Heartless", 1), _t("Heartless (Live)", 2)
        assert revue.doublon_de_titre(a, _ctx(a, b)) is None

    def test_autre_interprete_ou_role_secondaire(self):
        a = _t("Heartless", 1)
        feat = _t("Heartless", 2, is_featuring=True, primary_artist_name="The Fray")
        second = _t("Heartless", 3, secondary_role="Writer")
        assert revue.doublon_de_titre(a, _ctx(a, feat, second)) is None

    def test_meme_page_genius_n_est_pas_un_doublon(self):
        a, b = _t("Boss", 1, genius_id=7), _t("BOSS", 2, genius_id=7)
        assert revue.doublon_de_titre(a, _ctx(a, b)) is None


class TestVideos:
    def _v(self, titre, vues=10):
        return TrackVideo(video_id="vid", title=titre, views=vues)

    def test_video_d_un_autre_signalee_avec_ses_vues(self):
        t = _t("Heartless", videos=[self._v("Dermot Kennedy - Heartless Lyrics", 918_543)])
        t2 = _t("TOTAL 90", videos=[self._v("LA PAIX", 2027)])
        assert revue.video_etrangere(t) is None  # « heartless » est nommé
        motif, preuves = revue.video_etrangere(t2)
        assert "LA PAIX" in motif and preuves["impact"] == 2027

    def test_graphies_et_parenthese_de_titre(self):
        for titre, video in (
            ("N°10", "BOOBA - N° 10"),
            ("Fœtus", "Booba - Foetus"),
            ("Pursuit of Happiness (Nightmare)", "Kid Cudi - Pursuit Of Happiness ft. MGMT"),
        ):
            assert revue.video_etrangere(_t(titre, videos=[self._v(video)])) is None

    def test_la_parenthese_d_un_titre_generique_compte(self):
        t = _t("Intro (A2)", videos=[self._v("Booba - Intro")])
        assert revue.video_etrangere(t) is not None

    def test_vues_youtube_anormales(self):
        t = _t("Cruella")
        t.streams.spotify_streams, t.streams.ytm_streams = 316_410, 5_552_707
        assert "×17" in revue.vues_youtube_anormales(t)[0]


class TestPagesEtCredits:
    def test_certif_avant_la_sortie(self):
        t = _t("Temps mort 2.0", release_date="2015-04-13")
        t.certs.entries = [
            {
                "body": "SNEP",
                "certification": "Or",
                "certification_date": "2003-01-21",
                "title": "TEMPS MORT",
            }
        ]
        assert "avant la sortie" in revue.certif_avant_sortie(t)[0]

    def test_page_annotee_sans_trace(self):
        t = _t("Yam", genius_url="https://genius.com/Kanye-west-yam-annotated")
        assert revue.page_annotee_sans_trace(t)
        assert (
            revue.page_annotee_sans_trace(_t("X", genius_url=t.genius_url, spotify_id="S")) is None
        )

    def test_credits_api_non_confirmes(self):
        from datetime import datetime

        t = _t("X", last_scraped=datetime(2026, 9, 26))
        t.credits = [Credit("Kid Cudi", CreditRole.PROGRAMMER, source="genius_api")]
        assert "Kid Cudi" in revue.credits_api_seuls(t)[0]
        t.last_scraped = None  # page pas lue : ils sont provisoires, pas suspects
        assert revue.credits_api_seuls(t) is None


def test_liens_proposes_au_niveau_artiste():
    rel = SimpleNamespace(related_name="Panama Bende", kind="member_of", detail=None)
    cas = revue.detecter(_ctx(relations=[rel]), detecteurs=())
    assert [(c.detecteur, c.morceau, c.track_id) for c in cas] == [
        ("liens_proposes", "Panama Bende", None)
    ]


def test_detecter_trie_par_impact():
    a, b = _t("Pour de vrai", 1), _t("Pour de Vrai", 2)
    a.streams.spotify_streams, b.streams.spotify_streams = 10, 1000
    doublon = [d for d in revue.DETECTEURS if d.code == "doublon"]
    cas = revue.detecter(_ctx(a, b), detecteurs=doublon, detecteurs_artiste=())
    assert [c.track_id for c in cas] == [2, 1]
    assert revue.par_detecteur(cas) == {"doublon": 2}


def test_analyser_lit_le_contexte(data_manager):
    a = Artist(name="Booba")
    a.id = data_manager.save_artist(a)
    t = Track(title="Lunatic", artist=a)
    t.observations += [Observation("bpm", 83, "getsongbpm"), Observation("bpm", 130, "songbpm")]
    data_manager.save_track(t)
    a.tracks = data_manager.get_artist_tracks(a.id)
    assert [c.detecteur for c in revue.analyser(data_manager, a)] == ["bpm"]


def test_fenetre_se_construit(racine_tk):
    """La fenêtre affiche les cas et filtre par détecteur (lecture seule)."""
    from src.gui.windows.a_trancher import ATrancherWindow

    a, b = _t("Pour de vrai", 1), _t("Pour de Vrai", 2)
    artiste = Artist(name="A2H")
    artiste.id = 1
    artiste.tracks = [a, b]
    dm = SimpleNamespace(
        get_artist_observations=lambda _id: {}, get_artist_relations=lambda _id, status: []
    )
    app = SimpleNamespace(root=racine_tk, data_manager=dm, _show_track_details_for_track=print)
    w = ATrancherWindow(app, artiste)
    try:
        assert len(w.cas) == 2
        w.filtre.set(next(k for k, v in w.choix.items() if v == "doublon"))
        w._afficher()
        assert len(w.liste.winfo_children()) == 2
    finally:
        w.destroy()
