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
    def _avec(self, lrc, titre="X", n=50):
        t = _t(titre)
        t.lyrics.text = " ".join(f"mot{i}" for i in range(n))
        t.lyrics.source = "genius"
        t.lyrics.synced = lrc
        t.lyrics.synced_source = "YouTube Music"
        return t

    MOITIE = " ".join(f"mot{i}" for i in range(25)) + " " + " ".join(f"autre{i}" for i in range(25))

    def test_tranche_ambigue_signalee(self):
        assert "50%" in revue.lrc_douteux(self._avec(self.MOITIE))[0]

    def test_lrc_confirme_ou_dementi_non_signale(self):
        assert revue.lrc_douteux(self._avec(" ".join(f"mot{i}" for i in range(50)))) is None
        assert revue.lrc_douteux(self._avec(" ".join(f"autre{i}" for i in range(50)))) is None

    def test_memes_mots_dans_le_desordre_dementi(self):
        """Les bigrammes : mêmes mots, aucune paire commune — démenti, pas douteux."""
        lrc = (
            " ".join(f"mot{i}" for i in range(49, 24, -1))
            + " "
            + " ".join(f"autre{i}" for i in range(25))
        )
        assert revue.lrc_douteux(self._avec(lrc)) is None

    def test_paroles_genius_trop_courtes_non_jugeables(self):
        """Un extrait Genius (« Good Ass Job » : 12 mots) ne permet pas de douter."""
        assert revue.lrc_douteux(self._avec(self.MOITIE, n=20)) is None

    def test_sessions_live_a_part(self):
        for titre in (
            "Grünt #33",
            "Freestyle Skyrock #1",
            "Freestyle Couvre Feu (OKLM)",
            "Loup noir - A COLORS SHOW",
            "Runaway (Live at London Wireless Festival 2014)",
        ):
            t = self._avec(self.MOITIE, titre=titre)
            assert revue.lrc_douteux(t) is None, titre
            assert "50%" in revue.lrc_session_live(t)[0], titre
        # « OKLM » et « Girls, Sounds & Colors » sont des titres, pas des sessions.
        for titre in ("OKLM", "Girls, Sounds & Colors", "Planète rap"):
            assert not revue.session_live(_t(titre)), titre


def _fiche_paroles(titre, tid, paroles, lrc=None, genius_id=None):
    t = _t(titre, tid, genius_id=genius_id)
    t.lyrics.text, t.lyrics.source = paroles, "genius"
    t.lyrics.synced = lrc
    return t


ORIGINAL = " ".join(f"ligne{i} mot{i}" for i in range(30))
DEMO = (
    " ".join(f"demo{i} texte{i}" for i in range(20))
    + " "
    + " ".join(f"ligne{i} mot{i}" for i in range(4))
)


class TestLrcAutreFiche:
    def test_le_lrc_de_l_original_sur_la_demo(self):
        """« Ghost Town (Demo) » portait le LRC de *Ghost Town*."""
        o = _fiche_paroles("Ghost Town", 1, ORIGINAL)
        d = _fiche_paroles("Ghost Town (Demo)", 2, DEMO, lrc=ORIGINAL)
        ctx = _ctx(o, d)
        motif, preuves = revue.lrc_d_une_autre_fiche(d, ctx)
        assert "« Ghost Town »" in motif and preuves["autre"] == 1
        # Formel : il n'est pas AUSSI proposé en douteux.
        assert revue.lrc_douteux(d, ctx) is None

    def test_son_propre_lrc_n_est_pas_signale(self):
        o = _fiche_paroles("Ghost Town", 1, ORIGINAL, lrc=ORIGINAL)
        d = _fiche_paroles("Ghost Town (Demo)", 2, DEMO)
        assert revue.lrc_d_une_autre_fiche(o, _ctx(o, d)) is None

    def test_memes_paroles_ou_meme_page_ne_comptent_pas(self):
        """Une version héritée (mêmes paroles) ou une ligne de la même page
        Genius n'est pas « une autre fiche »."""
        d = _fiche_paroles("Ghost Town (Demo)", 2, DEMO, lrc=ORIGINAL, genius_id=7)
        heritee = _fiche_paroles("Ghost Town (Live)", 3, DEMO)
        soeur = _fiche_paroles("Ghost Town", 4, ORIGINAL, genius_id=7)
        assert revue.lrc_d_une_autre_fiche(d, _ctx(d, heritee, soeur)) is None

    def test_un_extrait_contenu_dans_le_lrc_n_en_est_pas_l_auteur(self):
        """« Manque de sommeil » (barely afk) sample huit lignes du refrain de
        *way back* : le LRC entier de *way back (Live)* y fait 100 % dans le
        sens de l'extrait, mais l'extrait n'en explique qu'une petite part."""
        refrain = " ".join(f"ligne{i} mot{i}" for i in range(22))
        live_propre = refrain + " " + " ".join(f"live{i} cri{i}" for i in range(16))
        lrc = refrain + " " + " ".join(f"studio{i} vers{i}" for i in range(60))
        extrait = _fiche_paroles("Manque de sommeil", 1, refrain)
        live = _fiche_paroles("way back (Live)", 2, live_propre, lrc=lrc)
        ctx = _ctx(extrait, live)
        trouve = revue._lrc_autre_candidat(live, ctx)
        assert trouve is not None and trouve[0] is extrait
        assert revue.lrc_d_une_autre_fiche(live, ctx) is None

    def test_detecteur_formel(self):
        assert "lrc_autre_fiche" in revue.CODES_FORMELS
        assert "certif_autre_titre" in revue.CODES_FORMELS
        assert "lrc_douteux" not in revue.CODES_FORMELS


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

    def test_le_sigle_du_titre_le_nomme(self):
        """2026-09-28, relevé en échantillon : « LMLVSB », « BBHMM », « TGIF ».
        Seulement à partir de quatre mots, et comme mot ENTIER."""
        for titre, video in (
            ("La mort leur va si bien (Twinsmatic Remix)", "Booba - LMLVSB (twinsmatic Remix)"),
            ("Bitch Betta Have My Money", "Rihanna - BBHMM (Safaree)"),
            ("T.G.I.F.", "Kid Cudi - TGIF ft. Chip Tha Ripper"),
        ):
            assert revue.video_etrangere(_t(titre, videos=[self._v(video)])) is None
        # Trois mots : un sigle de trois lettres se trouve partout.
        assert revue.video_etrangere(_t("Mon Petit Chat", videos=[self._v("MPC live")])) is not None

    def test_freestyle_d_emission_titre_alternatif_et_graphie(self):
        """2026-09-28 : 42 faux positifs sur 294 relevés et relus un à un."""
        for titre, video in (
            (
                "Gros freestyle de L'Entourage en live dans Planète Rap !",
                "L'Entourage - Freestyle [Part. 1] #PlanèteRap",
            ),
            ("Tim Westwood Freestyle", "Kid Cudi freestyle - Westwood"),
            ("Bigger Than You (Do It Alone)", "Kid Cudi-Do It Alone"),
            ("Intro (Table d'écoute)", "Table d'écoute"),
            ("Vu D'Ici", "Vue d'ici (feat. Diam's, Eloquence)"),
        ):
            assert revue.video_etrangere(_t(titre, videos=[self._v(video)])) is None, titre

    def test_freestyle_sans_le_mot_et_parties_de_medley(self):
        """2026-09-29 : 18 faux positifs de plus, relus un à un."""
        for titre, video in (
            ("A Million and One Freestyle", "Kanye West - A Million and One [FULL]"),
            ("Freestyle chez Lapwass", "LUCIO BUKOwSKI CHEZ OSTER LAPWASS"),
            ("Je perds mon temps/Freestyle Quai 54", "Booba - J'perds mon temps"),
            ("Space X / Alien", "Kanye West - Alien (Audio)"),
        ):
            assert revue.video_etrangere(_t(titre, videos=[self._v(video)])) is None, titre
        # Sans le mot « freestyle », le reste doit encore nommer la vidéo.
        t = _t("A Million and One Freestyle", videos=[self._v("Kanye West - Stronger")])
        assert revue.video_etrangere(t) is not None

    def test_un_nom_de_session_n_est_pas_un_titre_alternatif(self):
        """« Tiny Desk Home » nomme une session : la vidéo du Tiny Desk d'un
        AUTRE artiste reste signalée."""
        t = _t(
            "Temptations (Tiny Desk Home)",
            videos=[self._v("Ty Dolla $ign: Tiny Desk (Home) Concert")],
        )
        assert revue.video_etrangere(t) is not None

    def test_une_graphie_proche_exige_trois_mots(self):
        assert revue.video_etrangere(_t("Dreams", videos=[self._v("Drama")])) is not None

    def test_la_parenthese_d_un_titre_generique_compte(self):
        t = _t("Intro (A2)", videos=[self._v("Booba - Intro")])
        assert revue.video_etrangere(t) is not None
        # … sauf quand c'est la vidéo que la page Genius du morceau désigne.
        v = TrackVideo(video_id="vid", title="Booba - Intro", views=10, source="genius_media")
        assert revue.video_etrangere(_t("Intro (A2)", videos=[v])) is None
        v = TrackVideo(video_id="vid", title="Booba - Garcimore", source="genius_media")
        assert revue.video_etrangere(_t("Intro (A2)", videos=[v])) is not None

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
        # Un AUTRE titre : formel, pas « à trancher ».
        assert revue.certif_avant_sortie(t) is None
        assert "un autre titre" in revue.certif_d_un_autre_titre(t)[0]

    def test_sous_titre_de_la_certif_ou_descripteur_de_la_fiche(self):
        """Une parenthèse de PLUS côté certif est un sous-titre (le bon morceau,
        Kid Cudi « Day 'N' Nite » ← « DAY 'N' NITE (NIGHTMARE) ») ; côté fiche,
        une version qui a reçu la certif de l'original."""
        oeuvre = revue.certif_d_une_autre_oeuvre
        assert not oeuvre(_t("Day ‘N’ Nite"), {"title": "DAY 'N' NITE (NIGHTMARE)"})
        assert oeuvre(_t("Jesus Walks (Orchestral)"), {"title": "JESUS WALKS"})
        assert oeuvre(
            _t("Pursuit Of Happiness (Nightmare) (Prime Day Show)"),
            {"title": "PURSUIT OF HAPPINESS (NIGHTMARE)"},
        )
        assert oeuvre(_t("FATHER"), {"title": "FATHER STRETCH MY HANDS PT. 1"})
        assert not oeuvre(_t("Alors on danse"), {"title": "ALORS ON DANSE"})

    def test_certif_avant_la_sortie_meme_titre(self):
        """Même titre : c'est la date de sortie qui est suspecte — à trancher."""
        t = _t("Day 'N' Nite", release_date="2020-04-17")
        t.certs.entries = [
            {
                "body": "BRMA",
                "certification": "Or",
                "certification_date": "2009-04-03",
                "title": "DAY 'N' NITE",
            }
        ]
        assert "avant la sortie" in revue.certif_avant_sortie(t)[0]
        assert revue.certif_d_un_autre_titre(t) is None

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
    # Deux PAIRES : un cas par paire (2026-09-28), porté par la fiche d'id le
    # plus bas.
    a, b = _t("Pour de vrai", 1), _t("Pour de Vrai", 2)
    c, d = _t("Autre", 3), _t("AUTRE", 4)
    a.streams.spotify_streams, c.streams.spotify_streams = 10, 1000
    doublon = [x for x in revue.DETECTEURS if x.code == "doublon"]
    cas = revue.detecter(_ctx(a, b, c, d), detecteurs=doublon, detecteurs_artiste=())
    assert [x.track_id for x in cas] == [3, 1]
    assert revue.par_detecteur(cas) == {"doublon": 2}


def test_un_doublon_ne_fait_qu_un_cas_et_deux_disques_distincts_aucun():
    a, b = _t("Intro", 1, album="The College Dropout"), _t("Intro", 2, album="Graduation")
    assert revue.doublon_de_titre(a, _ctx(a, b)) is None
    c, d = _t("I CAN'T WAIT", 3, album="BULLY"), _t("I CAN'T WAIT", 4, album="BULLY - DELUXE")
    ctx = _ctx(c, d)
    assert revue.doublon_de_titre(c, ctx) is not None and revue.doublon_de_titre(d, ctx) is None


def _booba_lunatic(data_manager):
    a = Artist(name="Booba")
    a.id = data_manager.save_artist(a)
    t = Track(title="Lunatic", artist=a)
    # Deux sources FIABLES en désaccord (2026-09-28 : GetSongBPM seul contre une
    # autre s'explique et n'est plus montré — cf. `TestDesaccordsExpliques`).
    t.observations += [Observation("bpm", 83, "reccobeats"), Observation("bpm", 130, "songbpm")]
    data_manager.save_track(t)
    a.tracks = data_manager.get_artist_tracks(a.id)
    return a


def test_analyser_lit_le_contexte(data_manager):
    a = _booba_lunatic(data_manager)
    r = revue.analyser(data_manager, a)
    assert [c.detecteur for c in r.actifs] == ["bpm"] and r.masques == []


class TestMemoireDesVerdicts:
    """Étape 3 : un cas tranché ne revient plus… tant que ses preuves tiennent."""

    def test_normal_puis_retabli(self, data_manager):
        a = _booba_lunatic(data_manager)
        (cas,) = revue.analyser(data_manager, a).actifs
        data_manager.trancher_cas(a.id, cas.detecteur, cas.cle, morceau=cas.morceau)
        r = revue.analyser(data_manager, a)
        assert r.actifs == [] and [c.cle for c in r.masques] == [cas.cle]
        assert data_manager.annuler_verdict(a.id, cas.detecteur, cas.cle)
        assert len(revue.analyser(data_manager, a).actifs) == 1

    def test_de_nouvelles_preuves_font_revenir_le_cas(self, data_manager):
        a = _booba_lunatic(data_manager)
        (cas,) = revue.analyser(data_manager, a).actifs
        data_manager.trancher_cas(a.id, cas.detecteur, cas.cle)
        t = a.tracks[0]
        data_manager.upsert_observations(t.id, [Observation("bpm", 95, "reccobeats")])
        a.tracks = data_manager.get_artist_tracks(a.id)
        assert len(revue.analyser(data_manager, a).actifs) == 1

    def test_cle_stable_hors_valeurs_volatiles(self):
        t = _t("Cruella", genius_id=42)
        t.streams.spotify_streams, t.streams.ytm_streams = 316_410, 5_552_707
        (c1,) = revue.detecter(_ctx(t), detecteurs_artiste=())
        t.streams.ytm_streams = 6_000_000  # les vues montent : même cas
        (c2,) = revue.detecter(_ctx(t), detecteurs_artiste=())
        assert c1.cle == c2.cle and c1.cle.startswith("g42|")


class TestSignalementsDesRuns:
    def test_un_run_remplace_ses_signalements(self, data_manager):
        a = _booba_lunatic(data_manager)
        t = a.tracks[0]
        sig = revue.Cas("kworb_variante", t.id, t.title, "variante suspecte", 5, {"id": "S1"}, "c1")
        data_manager.remplacer_signalements(a.id, "kworb_variante", [sig])
        codes = [c.detecteur for c in revue.analyser(data_manager, a).actifs]
        assert sorted(codes) == ["bpm", "kworb_variante"]
        data_manager.remplacer_signalements(a.id, "kworb_variante", [])
        assert [c.detecteur for c in revue.analyser(data_manager, a).actifs] == ["bpm"]

    def test_signalement_d_une_fiche_disparue_ignore(self):
        s = {
            "detecteur": "x",
            "track_id": 99,
            "morceau": "M",
            "motif": "",
            "impact": 0,
            "preuves": {},
            "cle": "k",
        }
        assert revue._cas_des_signalements([s], {1, 2}) == []


def test_fenetre_se_construit(racine_tk):
    """La fenêtre affiche les cas, filtre par détecteur, et « ✓ Normal » les masque."""
    from src.gui.windows.a_trancher import ATrancherWindow

    a, b = _t("Pour de vrai", 1), _t("Pour de Vrai", 2)
    c, d = _t("Autre", 3), _t("AUTRE", 4)
    artiste = Artist(name="A2H")
    artiste.id = 1
    artiste.tracks = [a, b, c, d]
    tranches = []
    dm = SimpleNamespace(
        get_artist_observations=lambda _id: {},
        get_artist_relations=lambda _id, status: [],
        signalements_revue=lambda _id: [],
        verdicts_revue=lambda _id: {},
        trancher_cas=lambda aid, det, cle, **kw: tranches.append((det, cle)),
        annuler_verdict=lambda aid, det, cle: True,
        corrections_revue=lambda _id: [
            {
                "id": 9,
                "detecteur": "lrc_autre_fiche",
                "cle": "k",
                "track_id": 1,
                "morceau": "Pour de vrai",
                "motif": "LRC de « X »",
                "compte_rendu": "LRC retiré de « Pour de vrai »",
                "applied_at": "2026-09-27 21:00",
                "annulation": {"type": "lrc"},
            }
        ],
    )
    app = SimpleNamespace(root=racine_tk, data_manager=dm, _show_track_details_for_track=print)
    w = ATrancherWindow(app, artiste)
    try:
        assert len(w.cas) == 2
        w.filtre.set(next(k for k, v in w.choix.items() if v == "doublon"))
        w._afficher()
        assert len(w.liste.winfo_children()) == 2
        w._normal(w.cas[0])
        assert len(w.revue.actifs) == 1 and len(w.revue.masques) == 1 and len(tranches) == 1
        w._changer_vue(next(k for k, v in w._libelles_vues().items() if v == "normaux"))
        w._retablir(w.cas[0])
        assert len(w.revue.actifs) == 2
        # La vue du journal des corrections automatiques, avec « ↩ Rétablir ».
        w._changer_vue(next(k for k, v in w._libelles_vues().items() if v == "journal"))
        assert w.vue == "journal" and len(w.liste.winfo_children()) == 1
        assert w.bouton_surs.cget("state") == "disabled"  # aucun cas formel ici
    finally:
        w.destroy()


class TestSignalementsKworb:
    """`services/streams` enregistre ce que Kworb a trouvé, sur passage RÉUSSI."""

    def _dm(self):
        ecrits = {}
        return ecrits, SimpleNamespace(
            remplacer_signalements=lambda aid, code, cas: ecrits.__setitem__(code, cas)
        )

    def test_passage_reussi_remplace_chaque_detecteur(self):
        from src.services.streams import _signalements_kworb

        ecrits, dm = self._dm()
        t = _t("Heartless (Remix)", genius_id=5)
        _signalements_kworb(
            dm,
            _A,
            {
                "a_trancher": [
                    {
                        "detecteur": "kworb_variante",
                        "track": t,
                        "morceau": t.title,
                        "motif": "autre version ?",
                        "preuves": {"spotify_id": "SPH"},
                        "impact": 2109,
                    }
                ]
            },
        )
        assert set(ecrits) == set(revue.CODES_KWORB)  # les vides effacent l'ancien
        (cas,) = ecrits["kworb_variante"]
        assert cas.track_id == t.id and cas.cle.startswith("g5|")

    def test_echec_n_efface_rien(self):
        from src.services.streams import _signalements_kworb

        ecrits, dm = self._dm()
        _signalements_kworb(dm, _A, {"error": "réseau"})
        assert ecrits == {}


def test_audit_spotify_enregistre_puis_retire():
    ecrits = {}
    dm = SimpleNamespace(
        remplacer_signalements=lambda aid, code, cas: ecrits.__setitem__(code, cas) or len(cas)
    )
    t = _t("Mouton noir", 1, genius_id=9)
    artiste = Artist(name="Swing")
    artiste.id, artiste.tracks = 4, [t]
    ecart = {
        "track_id": 1,
        "titre": "Mouton noir",
        "spotify_id": "SPM",
        "artiste_etranger": True,
        "motif": "artiste étranger",
        "spotify": {"name": "Dessine-moi un mouton", "artists": ["Mylène Farmer"]},
    }
    assert revue.enregistrer_audit_spotify(dm, artiste, [ecart]) == 1
    (cas,) = ecrits["spotify_audit"]
    assert cas.motif.startswith("🚨") and "Dessine-moi un mouton" in cas.motif
    assert revue.enregistrer_audit_spotify(dm, artiste, [ecart], retires=[ecart]) == 0


class TestDoublonParLrc:
    def test_versions_soeurs_envoyees_aux_doublons(self):
        jeezy = " ".join(f"ligne{i} mot{i}" for i in range(30))
        roc = (
            " ".join(f"ligne{i} mot{i}" for i in range(14))
            + " "
            + " ".join(f"roc{i} texte{i}" for i in range(16))
        )
        j = _fiche_paroles("Can't Tell Me Nothing (Jeezy Remix)", 1, jeezy)
        r = _fiche_paroles("Can't Tell Me Nothing (R.O.C. Remix)", 2, roc, lrc=jeezy)
        r.spotify_id = "SP"  # sur une plateforme : pas formel
        ctx = _ctx(j, r)
        motif, preuves = revue.doublon_par_lrc(r, ctx)
        assert "même morceau que « Can't Tell Me Nothing (Jeezy Remix) »" in motif
        assert preuves["autres"] == [1]
        assert revue.lrc_douteux(r, ctx) is None


class TestAutrePriseHorsPlateformes:
    """Une démo, une référence, un live inédit n'ont ni page SongBPM (catalogue
    Spotify) ni LRC à eux (2026-09-28)."""

    def _obs_songbpm(self, tid, duree, bpm):
        return {
            tid: [
                Observation("duration", str(duree), "songbpm"),
                Observation("bpm", str(bpm), "songbpm"),
            ]
        }

    def _original(self):
        o = _t("All Day", 1, duration=310)
        o.audio.bpm = 123
        return o

    def test_mesures_songbpm_de_l_original(self):
        o = self._original()
        r = _t("All Day (Kendrick Lamar Reference)", 2)
        ctx = _ctx(o, r, obs=self._obs_songbpm(2, 311, 123))
        motif, preuves = revue.songbpm_de_l_original(r, ctx)
        assert "« All Day »" in motif and preuves["original"] == 1
        assert revue.songbpm_copie_de_l_original(r, ctx) is None  # pas deux fois

    def test_une_plateforme_une_edition_ou_une_autre_duree_ne_prouvent_rien(self):
        o = self._original()
        sur_spotify = _t("All Day (Live)", 2, spotify_id="SP")
        edition = _t("All Day (Physical Version)", 3)
        autre_duree = _t("All Day (Demo)", 4)
        obs = {
            **self._obs_songbpm(2, 311, 123),
            **self._obs_songbpm(3, 311, 123),
            **self._obs_songbpm(4, 250, 123),
        }
        ctx = _ctx(o, sur_spotify, edition, autre_duree, obs=obs)
        for t in (sur_spotify, edition, autre_duree):
            assert revue.songbpm_de_l_original(t, ctx) is None, t.title

    def test_lrc_de_l_original_sur_une_demo_meme_a_refrain_commun(self):
        """Au-delà de 40 % de paires propres, la preuve reste formelle quand la
        fiche est une autre prise hors plateformes et l'autre fiche SON original."""
        original = " ".join(f"ligne{i} mot{i}" for i in range(30))
        demo = (
            " ".join(f"ligne{i} mot{i}" for i in range(14))
            + " "
            + " ".join(f"demo{i} texte{i}" for i in range(16))
        )
        o = _fiche_paroles("Hurricane", 1, original)
        d = _fiche_paroles("Hurricane (Donda Demo)", 2, demo, lrc=original)
        ctx = _ctx(o, d)
        assert revue._lrc_autre_candidat(d, ctx)[2] >= revue.PROPRE_MAX_FORMEL
        assert revue.lrc_d_une_autre_fiche(d, ctx)
        assert revue.lrc_douteux(d, ctx) is None
        # Sur une plateforme, la preuve reste formelle depuis le 2026-09-28 : le
        # LRC colle NETTEMENT mieux à l'original (100 % contre ~48 %) — la règle
        # « nette », relue sur les 22 cas réels qu'elle rend formels.
        d.spotify_id = "SP"
        ctx = _ctx(o, d)
        assert revue.lrc_d_une_autre_fiche(d, ctx)
        assert revue.lrc_douteux(d, ctx) is None


class TestVideoPartagee:
    """2026-09-28 : une vidéo portée par plusieurs fiches (« album entier » sur
    dix titres) se juge UNE fois, pas une fois par fiche."""

    @staticmethod
    def _v(vid, titre, vues=100):
        return TrackVideo(video_id=vid, title=titre, views=vues)

    def test_un_cas_par_video_avec_les_fiches_muettes(self):
        album = self._v(
            "ALB", "BEN plg - Paraît que les miracles n'existent pas (Album entier)", 5000
        )
        a = _t("Béni", 1, videos=[album])
        b = _t("Le riz et la sauce", 2, videos=[album])
        ctx = _ctx(a, b)
        assert revue.video_etrangere(a, ctx) is None and revue.video_etrangere(b, ctx) is None
        ((libelle, motif, preuves),) = revue.video_partagee(ctx)
        assert preuves["track_ids"] == [1, 2] and preuves["impact"] == 5000
        assert "portée par 2 fiches" in motif

    def test_un_clip_double_qui_nomme_ses_deux_titres_n_est_pas_signale(self):
        clip = self._v("DBL", "Booba - Donjon & 2h22")
        ctx = _ctx(_t("Donjon", 1, videos=[clip]), _t("2h22", 2, videos=[clip]))
        assert revue.video_partagee(ctx) == []

    def test_l_action_retire_la_video_de_toutes_les_fiches_muettes(self, monkeypatch):
        from src.services import revue_actions

        retraits = []
        monkeypatch.setattr(
            "src.utils.youtube_integration.reject_youtube_link",
            lambda dm, track, url, nom: retraits.append((track.id, url)),
        )
        a, b = _t("Béni", 1), _t("Le riz", 2)
        cas = revue.Cas(
            "video_partagee", None, "x", "m", 0, {"video_id": "ALB", "track_ids": [1, 2]}, "k"
        )
        (action,) = revue_actions.actions_pour(cas)
        ctx = revue_actions.ContexteAction(None, _A, {1: a, 2: b})
        assert "2 fiche(s)" in action.executer(ctx, cas)
        assert [tid for tid, _ in retraits] == [1, 2]


class TestDesaccordsExpliques:
    """2026-09-28 : un désaccord que la majorité ou la fiabilité mesurée
    explique ne demande rien (64 cas à majorité : le vote l'a TOUJOURS suivie ;
    GetSongBPM isolé dans 61 sur 64)."""

    @staticmethod
    def _t(bpm_retenu, obs, key=None, mode=None):
        t = _t("x", 1)
        t.audio.bpm, t.audio.key, t.audio.mode = bpm_retenu, key, mode
        return t, _ctx(t, obs={1: obs})

    def test_majorite_suivie(self):
        t, ctx = self._t(
            130,
            [
                Observation("bpm", 83, "getsongbpm"),
                Observation("bpm", 130, "songbpm"),
                Observation("bpm", 130, "reccobeats"),
            ],
        )
        assert revue.bpm_desaccord(t, ctx) is None

    def test_getsongbpm_seul_contre_une_source_fiable(self):
        t, ctx = self._t(
            130, [Observation("bpm", 83, "getsongbpm"), Observation("bpm", 130, "songbpm")]
        )
        assert revue.bpm_desaccord(t, ctx) is None

    def test_deux_sources_fiables_restent_signalees(self):
        t, ctx = self._t(
            130, [Observation("bpm", 83, "reccobeats"), Observation("bpm", 130, "songbpm")]
        )
        assert revue.bpm_desaccord(t, ctx) is not None

    def test_tonalite_majoritaire(self):
        t, ctx = self._t(
            None,
            [
                Observation("key", 4, "getsongbpm"),
                Observation("mode", 1, "getsongbpm"),
                Observation("key", 9, "songbpm"),
                Observation("mode", 0, "songbpm"),
                Observation("key", 9, "reccobeats"),
                Observation("mode", 0, "reccobeats"),
            ],
            key=9,
            mode=0,
        )
        assert revue.tonalite_desaccord(t, ctx) is None

    def test_songbpm_passe_devant_getsongbpm(self):
        from src.utils.bpm_vote import BPM_SOURCE_RANK

        assert (
            BPM_SOURCE_RANK["reccobeats"]
            > BPM_SOURCE_RANK["songbpm"]
            > BPM_SOURCE_RANK["getsongbpm"]
        )
