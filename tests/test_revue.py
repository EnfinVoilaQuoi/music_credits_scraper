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

    def test_deezer_et_ytm_sont_des_temoins(self):
        """« New God Flow.1 » (295 s chez Deezer) portait l'ID de *New God
        Flow* (357 s). Deezer est une autre plateforme : 6 s, c'est une édition."""
        t = _t("X", spotify_id="SP", durations_observees={"deezer": 295, "reccobeats": 357})
        assert "deezer 295 s" in revue.duree_spotify_dementie(t)[0]
        t = _t("X", spotify_id="SP", durations_observees={"deezer": 232, "spotify_web": 226})
        assert revue.duree_spotify_dementie(t) is None
        t = _t("X", spotify_id="SP", durations_observees={"ytmusic": 232, "spotify_web": 226})
        assert revue.duree_spotify_dementie(t) is not None

    def test_id_deezer_dementi(self):
        t = _t("X", deezer_id=12, durations_observees={"deezer": 150, "youtube": 200})
        motif, preuves = revue.duree_deezer_dementie(t)
        assert "autre version" in motif and preuves["deezer_id"] == 12
        t = _t("X", deezer_id=12, durations_observees={"deezer": 199, "ytmusic": 200})
        assert revue.duree_deezer_dementie(t) is None

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

    def test_une_autre_tonalite_est_une_autre_page(self):
        """2026-10-29 : « Jamm - Remix » et « Jamm », 95 bpm et 159 s chacun,
        mais 5 mineur contre 6 majeur — deux pages SongBPM, pas une copie."""
        o, v = _t("Jamm", 1), _t("Jamm - Remix", 2)
        original = self._obs(songbpm={"bpm": 95, "duration": 159, "key": 6, "mode": 1})
        remix = self._obs(songbpm={"bpm": 95, "duration": 159, "key": 5, "mode": 0})
        assert revue.songbpm_copie_de_l_original(v, _ctx(o, v, obs={1: original, 2: remix})) is None
        # Même tonalité, ou tonalité lue d'un seul côté : toujours une copie.
        meme = self._obs(songbpm={"bpm": 95, "duration": 159, "key": 6, "mode": 1})
        assert revue.songbpm_copie_de_l_original(v, _ctx(o, v, obs={1: original, 2: meme}))
        sans = self._obs(songbpm={"bpm": 95, "duration": 159})
        assert revue.songbpm_copie_de_l_original(v, _ctx(o, v, obs={1: original, 2: sans}))


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


AUTRE_TEXTE = " ".join(f"autre{i} chose{i}" for i in range(30))


def _fiche_repli(titre, tid, paroles=None, source="Source: Musixmatch", lrc=None, **kw):
    t = _t(titre, tid, **kw)
    t.lyrics.text, t.lyrics.source, t.lyrics.synced = paroles, source, lrc
    t.lyrics.synced_source = "YouTube Music" if lrc else None
    return t


class TestParolesAutreFiche:
    """Une fiche SANS paroles Genius jugeables porte un texte de repli qui est
    celui d'une autre fiche (A2H « Pardonne moi » ← « Pardonnez-moi »)."""

    def test_paroles_de_repli_d_une_autre_fiche(self):
        o = _fiche_paroles("Samuraï", 1, ORIGINAL)
        x = _fiche_repli("Interlude", 2, paroles=ORIGINAL)
        motif, preuves = revue.paroles_d_une_autre_fiche(x, _ctx(o, x))
        assert "« Samuraï »" in motif and preuves["autre"] == 1
        assert preuves["textes"] == ["paroles Source: Musixmatch"]

    def test_lrc_d_une_page_vide(self):
        """Page Genius vide : le LRC n'a aucun juge (`lrc_autre_fiche` exige
        des paroles Genius)."""
        o = _fiche_paroles("WAKE UP F1LTHY", 1, ORIGINAL)
        x = _fiche_repli("00BABTUS", 2, lrc=ORIGINAL, source=None)
        motif, preuves = revue.paroles_d_une_autre_fiche(x, _ctx(o, x))
        assert preuves["textes"] == ["LRC YouTube Music"]
        assert revue.lrc_d_une_autre_fiche(x, _ctx(o, x)) is None

    def test_avec_paroles_genius_le_lrc_reste_a_l_autre_detecteur(self):
        o = _fiche_paroles("Ghost Town", 1, ORIGINAL)
        d = _fiche_paroles("Get Off Me", 2, AUTRE_TEXTE, lrc=ORIGINAL)
        assert revue.paroles_d_une_autre_fiche(d, _ctx(o, d)) is None

    def test_relations_legitimes(self):
        """Ligne sœur, version, graphie, titre contenu, relation Genius,
        contenant : le texte commun s'explique."""
        cas = [
            ("Pardonnez-moi (Remix)", {}),  # même socle
            ("Pardonez moi", {}),  # graphie
            ("22h31 - Pardonnez-moi", {}),  # titre contenu
            ("PDM (Pardonnez-moi Remix)", {}),  # le socle de l'autre entre parenthèses
            ("Autre titre", {"genius_id": 9}),  # ligne sœur
            (
                "Autre titre",
                {"relationships": [{"type": "interpolates", "title": "Pardonnez-moi"}]},
            ),
        ]
        for titre, kw in cas:
            o = _fiche_paroles("Pardonnez-moi", 1, ORIGINAL, genius_id=9)
            x = _fiche_repli(titre, 2, paroles=ORIGINAL, **kw)
            assert revue.paroles_d_une_autre_fiche(x, _ctx(o, x)) is None, titre

    def test_une_page_de_concert_n_en_est_pas_l_auteur(self):
        """« Free Larry Hoover Benefit Concert » contient tout le concert : elle
        explique le texte d'un morceau joué sans en être un."""
        concert = _fiche_paroles("Benefit Concert", 1, ORIGINAL + " " + AUTRE_TEXTE * 2)
        x = _fiche_repli("Flashing Lights", 2, paroles=" ".join(ORIGINAL.split()[:30]))
        assert revue.paroles_d_une_autre_fiche(x, _ctx(concert, x)) is None

    def test_au_panneau_mais_pas_formel(self):
        codes = {d.code for d in revue.DETECTEURS}
        assert "paroles_autre_fiche" in codes
        assert "paroles_autre_fiche" not in revue.CODES_FORMELS
        assert "lrc_douteux" not in revue.CODES_FORMELS


class TestContreVerification:
    def test_id_spotify_d_une_autre_version(self):
        t = _t("Repose en paix (Remix)", spotify_id="SP")
        t.spotify_page_title = "Repose en paix • Booba"
        motif, preuves = revue.id_spotify_autre_version(t)
        assert "« Repose en paix »" in motif and preuves["spotify_id"] == "SP"
        t = _t("Amnesia", spotify_id="SP")
        t.spotify_page_title = "Amnesia - Notre Dame & Harry Kaze Remix • Sébastien Tellier"
        assert revue.id_spotify_autre_version(t) is not None

    def test_le_remixeur_credite_designe_le_remix(self):
        t = _t("Know No Better (BROHUG Remix)", spotify_id="SP")
        t.spotify_page_title = "Know No Better • Major Lazer, BROHUG, Travis Scott"
        assert revue.id_spotify_autre_version(t) is None
        t.spotify_page_title = "Know No Better • Major Lazer, Travis Scott"
        assert revue.id_spotify_autre_version(t) is not None

    def test_meme_id_spotify_sur_deux_pages(self):
        """Une ÉDITION Spotify de l'une, principale de l'autre."""
        a = _t("Castro", 1, spotify_id="SPA", spotify_ids=["SPA", "SP"], genius_id=10, album="A")
        b = _t("Castro", 2, spotify_id="SP", spotify_ids=["SP"], genius_id=11, album="B")
        soeur = _t(
            "Castro", 3, spotify_id="SPA", spotify_ids=["SPA", "SP"], genius_id=10, album="A"
        )
        ctx = _ctx(a, b, soeur)
        motif, preuves = revue.id_spotify_partage(a, ctx)
        assert preuves["autres"] == [2] and "deux pages Genius" in motif
        assert revue.id_spotify_partage(b, ctx) is None  # un cas par groupe
        assert revue.id_spotify_partage(soeur, _ctx(a, soeur)) is None

    def test_le_meme_id_principal_est_laisse_au_catalogue(self):
        """Deux pages au même ID PRINCIPAL : `id_catalogue_partage` les juge,
        pas de second cas « même enregistrement ? »."""
        a = _t("Forever", 1, spotify_id="SP", spotify_ids=["SP"], genius_id=10, album="A")
        b = _t("Forever", 2, spotify_id="SP", spotify_ids=["SP"], genius_id=11, album="B")
        assert revue.id_spotify_partage(a, _ctx(a, b)) is None


class TestIdCataloguePartage:
    """« Forever » de Lil Wayne et celui de Drake, 2026-10-27 : la même
    recherche de titre avait posé le même ID sur deux pages."""

    def test_un_cas_par_page_porteuse(self):
        a = _t("Famous", 1, deezer_id=126772735, genius_id=10, album="The Life of Pablo")
        b = _t("Famous", 2, deezer_id=126772735, genius_id=11)
        ctx = _ctx(a, b)
        motif, preuves = revue.id_catalogue_partage(a, ctx)
        assert preuves == {"deezer_id": 126772735, "autres": [2]} and "Deezer" in motif
        assert revue.id_catalogue_partage(b, ctx)[1]["autres"] == [1]

    def test_spotify_aussi(self):
        a = _t("Intro", 1, spotify_id="SP", genius_id=10, album="The College Dropout")
        b = _t("Intro", 2, spotify_id="SP", genius_id=11, album="Akademiks")
        assert revue.id_catalogue_partage(a, _ctx(a, b))[1]["spotify_id"] == "SP"

    def test_ni_soeurs_ni_fiche_sans_page(self):
        a = _t("Famous", 1, deezer_id=7, genius_id=10)
        soeur = _t("Famous", 2, deezer_id=7, genius_id=10)
        sans_page = _t("Famous", 3, deezer_id=7)
        assert revue.id_catalogue_partage(a, _ctx(a, soeur)) is None
        # Une fiche sans page : la famille des doublons (`doublon_equivalent`).
        assert revue.id_catalogue_partage(a, _ctx(a, sans_page)) is None

    def test_une_reedition_n_est_pas_un_partage(self):
        """Tycoon / Tycoon (Deluxe) : même titre, même album à l'édition près."""
        a = _t("TYCOON", 1, spotify_id="SP", genius_id=10, album="TYCOON", duration=200)
        b = _t("TYCOON", 2, spotify_id="SP", genius_id=11, album="TYCOON (Deluxe)", duration=201)
        assert revue.id_catalogue_partage(a, _ctx(a, b)) is None


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

    def test_paroles_distinctes_deux_morceaux(self):
        """2026-10-29 : « Famous » de *The Life of Pablo* et celui avec Playboi
        Carti — les paroles Genius ne partagent aucune phrase."""
        a, b = _t("Famous", 1, genius_id=1), _t("Famous", 2, genius_id=2)
        a.lyrics.text, a.lyrics.source = PAROLES_UN, "genius"
        b.lyrics.text, b.lyrics.source = PAROLES_DEUX, "genius"
        assert revue.doublon_de_titre(a, _ctx(a, b)) is None

    def test_memes_paroles_ou_sans_paroles_reste_a_trancher(self):
        a, b = _t("Famous", 1, genius_id=1), _t("Famous", 2, genius_id=2)
        assert revue.doublon_de_titre(a, _ctx(a, b)) is not None
        a.lyrics.text, a.lyrics.source = PAROLES_UN, "genius"
        b.lyrics.text, b.lyrics.source = PAROLES_UN + "\nEncore une ligne", "genius"
        assert revue.doublon_de_titre(a, _ctx(a, b)) is not None

    def test_paroles_hors_genius_ne_disent_rien(self):
        a, b = _t("Famous", 1, genius_id=1), _t("Famous", 2, genius_id=2)
        a.lyrics.text, a.lyrics.source = PAROLES_UN, "YouTube Music"
        b.lyrics.text, b.lyrics.source = PAROLES_DEUX, "genius"
        assert revue.doublon_de_titre(a, _ctx(a, b)) is not None


PAROLES_UN = (
    "I just wanted you to know that I been thinking of the summer nights\n"
    "Driving down the coast with the windows open singing every line\n"
    "Never thought that we would make it this far from the bottom of the city"
)
PAROLES_DEUX = (
    "Money on my mind and the diamonds on my wrist they keep on shining\n"
    "Pull up in the foreign with the gang behind me ready for the war\n"
    "Counting up the paper every morning got no time to rest at all"
)


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

    def _forte(self, titre, *videos, **kw):
        t = _t(titre, videos=list(videos), **kw)
        t.streams.spotify_streams, t.streams.ytm_streams = 18_999, 3_308_958
        return t

    def test_des_vues_que_les_videos_expliquent_ne_sont_pas_anormales(self):
        clip = TrackVideo(
            video_id="a", title="Shay feat. Booba - Cruella (Clip Officiel)", views=5_000_000
        )
        t = self._forte("Cruella", clip)
        assert revue.vues_youtube_anormales(t, _ctx(t)) is None

    def test_la_reprise_que_la_page_decrit(self):
        v = TrackVideo(
            video_id="a",
            title="Four Five Seconds - Rihanna Kanye McCartney (Nicole Cross Official Cover Video)",
            views=3_289_007,
        )
        t = self._forte("FourfiveSeconds", v, primary_artist_name="Nicole Cross")
        assert revue.vues_youtube_anormales(t, _ctx(t)) is None
        # La reprise d'un AUTRE interprète que celui de la page reste à voir.
        t = self._forte("FourfiveSeconds", v, primary_artist_name="Twenty One Two")
        assert revue.vues_youtube_anormales(t, _ctx(t)) is not None

    def test_une_autre_version_reste_a_voir(self):
        v = TrackVideo(
            video_id="a", title="IAM - Je danse le Mia (Clip officiel)", views=56_000_000
        )
        t = self._forte("Je danse le MIA (Le Terrible Funk Remix Radio Edit)", v)
        assert revue.vues_youtube_anormales(t, _ctx(t)) is not None

    def test_une_video_partagee_n_explique_rien(self):
        v = TrackVideo(video_id="a", title="Cruella", views=5_000_000)
        t, u = self._forte("Cruella", v), _t("Cruella", tid=2, videos=[v])
        assert revue.vues_youtube_anormales(t, _ctx(t, u)) is not None


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

    def test_page_annotee_sans_info_deja_montree_pour_information(self):
        """2026-10-29 : « NICE THAT » — page lue qui ne dit rien (🕳️), déjà
        montrée pour information ; pas une décision de plus."""
        from datetime import datetime

        t = _t("NICE THAT", genius_url="https://genius.com/Kanye-west-nice-that-annotated")
        t.lyrics.scraped_at = t.last_scraped = datetime(2026, 9, 26)
        t.lyrics.text, t.lyrics.source = "(...)", "genius"
        assert revue.page_sans_info(t) is not None
        assert revue.page_annotee_sans_trace(t) is None
        # Une anecdote la fait sortir de 🕳️ : elle redevient une décision.
        t.anecdotes = "Snippet joué en 2018."
        assert revue.page_annotee_sans_trace(t) is not None

    def test_credits_api_non_confirmes(self):
        from datetime import datetime

        t = _t("X", last_scraped=datetime(2026, 9, 26))
        t.credits = [Credit("Kid Cudi", CreditRole.PROGRAMMER, source="genius_api")]
        assert "Kid Cudi" in revue.credits_api_seuls(t)[0]
        t.last_scraped = None  # page pas lue : ils sont provisoires, pas suspects
        assert revue.credits_api_seuls(t) is None


class TestNatures:
    """Ce qu'un cas DEMANDE (2026-10-19) : seules les décisions attendent un
    humain ; « à faire par un run » et « pour information » sont comptés à part."""

    def _v(self, source, vid="vid"):
        return TrackVideo(video_id=vid, title="LA PAIX", views=2027, source=source)

    def _nature(self, code, *tracks):
        d = [x for x in revue.DETECTEURS if x.code == code]
        return [
            c.nature for c in revue.detecter(_ctx(*tracks), detecteurs=d, detecteurs_artiste=())
        ]

    def test_video_de_la_page_genius_deja_exclue_des_vues(self):
        assert self._nature(
            "video_etrangere", _t("TOTAL 90", videos=[self._v("genius_media")])
        ) == [revue.INFORMATION]
        # Venue de notre recherche, elle COMPTE ses vues : à trancher.
        assert self._nature("video_etrangere", _t("TOTAL 90", videos=[self._v("search_auto")])) == [
            revue.DECISION
        ]

    def test_credits_api_sans_scrape_attendent_un_run(self):
        from datetime import datetime

        t = _t("X", last_scraped=datetime(2026, 10, 13))
        t.credits = [Credit("Kid Cudi", CreditRole.PROGRAMMER, source="genius_api")]
        assert self._nature("credits_api", t) == [revue.RUN]
        # À côté de crédits scrapés : le complément gardé en 🎫, une information.
        t.credits.append(Credit("Plain Pat", CreditRole.PRODUCER, source="genius"))
        assert self._nature("credits_api", t) == [revue.INFORMATION]

    def test_video_partagee_comptee_nulle_part_ou_chez_un_seul(self):
        autre = Artist(name="Lunatic")
        autre.id, _A.id = 2, 1
        try:
            a = _t("Strass", 1, videos=[self._v("genius_media")])
            b = _t("Kiffes", 2, videos=[self._v("genius_media")])
            ctx = _ctx(a, b)
            (cas,) = revue.detecter(ctx, detecteurs=(), detecteurs_artiste=revue.DETECTEURS_ARTISTE)
            assert cas.detecteur == "video_partagee" and cas.nature == revue.INFORMATION
            # La seconde ligne est celle d'un AUTRE artiste : chacun la compte.
            b.artist = autre
            (cas,) = revue.detecter(
                _ctx(a, b), detecteurs=(), detecteurs_artiste=revue.DETECTEURS_ARTISTE
            )
            assert cas.nature == revue.DECISION
        finally:
            _A.id = None

    def test_signalement_de_run_prend_la_nature_de_son_detecteur(self):
        s = {
            "detecteur": "kworb_non_relie",
            "track_id": None,
            "morceau": "E.T.",
            "motif": "",
            "impact": 0,
            "preuves": {},
            "cle": "k",
        }
        (cas,) = revue._cas_des_signalements([s], set())
        assert cas.nature == revue.INFORMATION
        (cas,) = revue._cas_des_signalements([{**s, "detecteur": "kworb_proposition"}], set())
        assert cas.nature == revue.DECISION

    def test_une_page_sans_info_n_est_pas_une_decision(self):
        """2026-10-26 : une page qui ne porte rien ne pèse sur aucune donnée —
        trancher « poubelle ou inédit » n'y change rien. Pour information."""
        (sans_info,) = [d for d in revue.DETECTEURS if d.code == "sans_info"]
        assert sans_info.nature == revue.INFORMATION

    def test_par_nature_et_cle_inchangee(self):
        t = _t("TOTAL 90", videos=[self._v("genius_media")])
        (cas,) = revue.detecter(
            _ctx(t),
            detecteurs=[x for x in revue.DETECTEURS if x.code == "video_etrangere"],
            detecteurs_artiste=(),
        )
        assert revue.par_nature([cas]) == {revue.DECISION: 0, revue.RUN: 0, revue.INFORMATION: 1}
        # La nature n'entre pas dans la clé : un « ✓ Normal » déjà posé tient.
        assert cas.cle == revue.cle_du_cas(t, {"video_id": "vid", "titre": "LA PAIX"})


def test_liens_proposes_au_niveau_artiste():
    rel = SimpleNamespace(related_name="Panama Bende", kind="member_of", detail=None)
    cas = revue.detecter(_ctx(relations=[rel]), detecteurs=())
    assert [(c.detecteur, c.morceau, c.track_id) for c in cas] == [
        ("liens_proposes", "Panama Bende", None)
    ]


class TestLienProposeSansTrace:
    """Q1 (décision utilisateur 2026-10-31) : un lien proposé dont le nom
    n'apparaît sur aucune fiche (ni crédit, ni interprète, ni invité) est
    pour information ; il redevient une décision dès que le nom apparaît."""

    def _nature(self, nom, *tracks):
        rel = SimpleNamespace(related_name=nom, kind="alias", detail=None)
        (cas,) = revue.detecter(_ctx(*tracks, relations=[rel]), detecteurs=())
        return cas.nature

    def test_sans_trace_pour_information(self):
        t = _t("Sans lui", featured_artists="Kaaris")
        assert self._nature("Eric Kwok", t) == revue.INFORMATION

    def test_credite_decision(self):
        t = _t("Caroline", credits=[Credit(name="Claude M'Barali", role=CreditRole.WRITER)])
        assert self._nature("Claude M'barali", t) == revue.DECISION

    def test_interprete_ou_invite_decision(self):
        assert self._nature("Hustle Gang", _t("X", primary_artist_name="Hustle Gang")) == (
            revue.DECISION
        )
        t = _t("Y", featured_artists="Kid Cudi & Hustle Gang")
        assert self._nature("Hustle Gang", t) == revue.DECISION

    def test_jamais_une_sous_chaine(self):
        t = _t("Z", featured_artists="The Throne Kids")
        assert self._nature("The Throne", t) == revue.INFORMATION


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

    def test_id_partage_qui_ne_l_est_plus_ignore(self):
        """A2H 2026-10-12 : « L'histoire est simple » restait « ID porté aussi
        par « L'histoire est simple » » après la fusion des deux fiches."""
        sid = "1R5AQU9f3kh5bM2cY4KKX7"
        s = {
            "detecteur": "kworb_id_partage",
            "track_id": 1,
            "morceau": "L’histoire est simple",
            "motif": "",
            "impact": 0,
            "preuves": {"spotify_id": sid},
            "cle": "k",
        }
        seule = [_t("L’histoire est simple", 1, spotify_id=sid), _t("Pornostar", 2)]
        assert revue._cas_des_signalements([s], {1, 2}, seule) == []
        deux = [*seule, _t("L'histoire est simple (Remix)", 3, spotify_id=sid)]
        assert len(revue._cas_des_signalements([s], {1, 2, 3}, deux)) == 1

    @staticmethod
    def _deezer(detecteur, track_id, preuves):
        return {
            "detecteur": detecteur,
            "track_id": track_id,
            "morceau": "M",
            "motif": "",
            "impact": 0,
            "preuves": preuves,
            "cle": "k",
        }

    def test_piste_deezer_absente_devenue_l_id_d_une_fiche(self):
        """A2H « Yatch Music » : l'ID de « Yacht Music », perdu par une fusion
        puis rétabli — le « morceau absent » du run d'avant ne l'est plus."""
        s = self._deezer("deezer_absent", None, {"piste_deezer": 128748177})
        sans = [_t("Yacht Music", 1)]
        assert len(revue._cas_des_signalements([s], {1}, sans)) == 1
        avec = [_t("Yacht Music", 1, deezer_id=128748177)]
        assert revue._cas_des_signalements([s], {1}, avec) == []

    def test_candidat_sans_objet_quand_la_page_est_a_une_fiche(self):
        """R3 (2026-10-27) : la page proposée à #8199 était l'ID principal de
        #12092 — un rattachement à une AUTRE fiche n'est plus proposé."""
        s = self._deezer("deezer_link_candidate", 1, {"piste_deezer": 70})
        libre = [_t("A", 1), _t("B", 2)]
        assert len(revue._cas_des_signalements([s], {1, 2}, libre)) == 1
        ailleurs = [_t("A", 1), _t("B", 2, deezer_id=70)]
        assert revue._cas_des_signalements([s], {1, 2}, ailleurs) == []
        chez_elle = [_t("A", 1, deezer_id=70), _t("B", 2)]
        assert revue._cas_des_signalements([s], {1, 2}, chez_elle) == []

    def test_candidat_sans_objet_quand_la_page_est_une_edition(self):
        """3113153, édition Collector de #12092, proposée à #8199."""
        s = self._deezer("deezer_link_candidate", 1, {"piste_deezer": 70})
        fiches = [_t("A", 1), _t("B", 2)]
        assert len(revue._cas_des_signalements([s], {1, 2}, fiches)) == 1
        assert revue._cas_des_signalements([s], {1, 2}, fiches, frozenset({70})) == []

    def test_lien_a_revoir_sans_duree_qui_le_contredise(self):
        """A2H « Pardonne moi » : la durée SongBPM (268 s, celle d'un autre
        morceau) retirée, Deezer 186 s n'est plus contredit."""
        s = self._deezer(
            "deezer_link_review", 1, {"ecart": {"piste": {"id": 81385490, "duration": 186}}}
        )
        contredit = [_t("Pardonne moi", 1, durations_observees={"youtube": 268})]
        assert len(revue._cas_des_signalements([s], {1}, contredit)) == 1
        apres = [_t("Pardonne moi", 1, durations_observees={"youtube": 187})]
        assert revue._cas_des_signalements([s], {1}, apres) == []

    def test_lien_a_revoir_contredit_par_des_sources_fragiles_seules(self):
        """Kanye « FOREVER » (décision utilisateur 2026-10-21) : Deezer 136 s,
        Discogs 87 s seul contre — une source fragile ne prouve rien, le lien
        tient et le cas ne se lève plus."""
        s = self._deezer(
            "deezer_link_review", 1, {"ecart": {"piste": {"id": 2941248401, "duration": 136}}}
        )
        fragiles = [_t("FOREVER", 1, durations_observees={"discogs": 87, "songbpm": 90})]
        assert revue._cas_des_signalements([s], {1}, fragiles) == []

    def test_lien_a_revoir_delie_depuis(self):
        """A2H « Blues » : la piste du single a été remplacée par celle de
        l'album — le signalement sur l'ancienne piste n'a plus d'objet."""
        s = self._deezer(
            "deezer_link_review",
            1,
            {"piste_deezer": 537739642, "ecart": {"piste": {"duration": 181}}},
        )
        actuel = [_t("Blues", 1, deezer_id=566925602, durations_observees={"spotify_web": 175})]
        assert revue._cas_des_signalements([s], {1}, actuel) == []

    def test_une_source_fragile_seule_ne_dement_pas_un_lien_confirme(self):
        """Vote des catalogues (2026-10-13) : Deezer et l'audio YouTube d'accord,
        SongBPM seul contre — le lien n'est plus mis en doute."""
        s = self._deezer(
            "deezer_link_review", 1, {"ecart": {"piste": {"id": 81385490, "duration": 186}}}
        )
        confirme = [_t("Pardonne moi", 1, durations_observees={"songbpm": 268, "youtube": 187})]
        assert revue._cas_des_signalements([s], {1}, confirme) == []


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
        get_artist_edition_durations=lambda _id: {},
        get_artist_edition_deezer_ids=lambda _id: frozenset(),
        get_artist_relations=lambda _id, status: [],
        divergences_identite_soeurs=lambda _id: [],
        get_artist_positions=lambda _id: {},
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
        # Une vue par nature : la vue « À trancher » ne montre que les décisions.
        c.videos = [TrackVideo(video_id="v", title="LA PAIX", views=5, source="genius_media")]
        w._recalculer()
        w._changer_vue(next(k for k, v in w._libelles_vues().items() if v == "cas"))
        assert len(w.cas) == 2 and all(x.nature == revue.DECISION for x in w.cas)
        w._changer_vue(next(k for k, v in w._libelles_vues().items() if v == "info"))
        assert [x.detecteur for x in w.cas] == ["video_etrangere"]
        assert "pour information" in w.titre.cget("text")
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

    def test_l_homonyme_revient_a_l_interprete_principal_de_la_video(self):
        """2026-10-27 : le « Selfish » de Slum Village (feat. Kanye) posé aussi
        sur le « Selfish » de *DONDA 2* — la tête du titre désigne sa fiche."""
        clip = self._v("S3", "Slum Village Ft. Kanye West & John Legend - Selfish [Clip]", 900)
        donda = _t("Selfish", 1, videos=[clip], genius_id=11, album="DONDA 2")
        slum = _t("Selfish", 2, videos=[clip], genius_id=12, album="Detroit Deli")
        slum.primary_artist_name = "Slum Village"
        ((_, motif, preuves),) = revue.video_partagee(_ctx(donda, slum))
        assert preuves["garder"] == 2 and preuves["track_ids"] == [1]
        assert preuves["temoin"] == "interprète principal de la vidéo : Slum Village"
        assert "Slum Village" in motif
        cas = revue.detecter(
            _ctx(donda, slum), detecteurs=(), detecteurs_artiste=revue.DETECTEURS_ARTISTE
        )
        assert [c.nature for c in cas] == [revue.DECISION]

    def _king(self, duree_video=157, bully=None, vultures=None):
        """« KING » : la vidéo de ¥$ (157 s) posée par Genius sur la page de
        *BULLY* (126 s) et sur celle de *VULTURES 1* (157 s), 2026-10-29."""
        v = TrackVideo(video_id="K", title="KING", views=10, duration=duree_video)
        a = _t("KING", 1, videos=[v], genius_id=11, album="BULLY")
        a.durations_observees = bully if bully is not None else {"deezer": 126, "apple_music": 127}
        b = _t("KING", 2, videos=[v], genius_id=12, album="VULTURES 1")
        b.durations_observees = vultures if vultures is not None else {"deezer": 157}
        return _ctx(a, b)

    def test_la_duree_de_la_video_designe_sa_page(self):
        ((_, motif, preuves),) = revue.video_partagee(self._king())
        assert preuves["garder"] == 2 and preuves["track_ids"] == [1]
        assert preuves["temoin"] == "durée de la vidéo (157 s) = ses catalogues seuls"

    def test_la_duree_ne_dit_rien_quand_elle_ne_separe_pas(self):
        # Sans durée de la vidéo, deux pages proches, une page sans catalogue,
        # ou une durée lue par la vidéo elle-même (`youtube`) : rien.
        for ctx in (
            self._king(duree_video=None),
            self._king(bully={"deezer": 154}),
            self._king(bully={}),
            self._king(bully={"youtube": 126}),
        ):
            ((_, _, preuves),) = revue.video_partagee(ctx)
            assert "garder" not in preuves

    def _castro(self, chaine="Yo Gotti - Topic", principal_cyhi="CyHi"):
        """« Castro » : la vidéo de « Yo Gotti - Topic » posée par Genius sur la
        page de Yo Gotti et sur celle de CyHi (2026-10-29)."""
        v = TrackVideo(video_id="C", title="Castro", views=10, channel=chaine)
        a = _t("Castro", 1, videos=[v], genius_id=11, album="White Friday (CM9)")
        a.primary_artist_name = principal_cyhi
        b = _t("Castro", 2, videos=[v], genius_id=12, album="White Friday (CM9)")
        b.primary_artist_name = "Yo Gotti"
        b.album = "Castro"
        return _ctx(a, b)

    def test_la_chaine_de_l_interprete_designe_sa_page(self):
        ((_, _, preuves),) = revue.video_partagee(self._castro())
        assert preuves["garder"] == 2 and preuves["track_ids"] == [1]
        assert preuves["temoin"] == "chaîne de l'interprète : Yo Gotti - Topic"

    def test_la_chaine_ne_dit_rien_quand_elle_ne_designe_pas_une_seule_page(self):
        for ctx in (
            self._castro(chaine="Release - Topic"),  # aucun artiste
            self._castro(chaine="Johnny Dee"),  # un particulier
            self._castro(principal_cyhi="Yo Gotti"),  # deux pages à lui
        ):
            ((_, _, preuves),) = revue.video_partagee(ctx)
            assert "garder" not in preuves

    def test_chaine_et_duree_en_desaccord_l_humain(self):
        ctx = self._king()
        for t in ctx.disco:
            for v in t.videos:
                v.channel = "Kanye West - Topic"
        ctx.disco[0].primary_artist_name = "Kanye West"  # BULLY
        ctx.disco[1].primary_artist_name = "¥$"  # VULTURES 1, que la durée désigne
        ((_, _, preuves),) = revue.video_partagee(ctx)
        assert "garder" not in preuves

    def test_une_video_d_un_autre_artiste_et_d_une_autre_duree(self):
        """« Viser la victoire » d'Admiral T portait « Promis à la victoire » de
        Kery James : chaîne d'un artiste étranger ET durée que toutes celles de
        la fiche démentent (2026-10-29) — le lien part, par règle."""
        from src.services import politiques_revue

        v = TrackVideo(video_id="V", title="Promis à la victoire", channel="Kery James - Topic")
        v.duration = 257
        t = _t("Viser la victoire", 1, videos=[v], genius_id=11)
        t.primary_artist_name = "Admiral T"
        t.durations_observees = {"deezer": 303, "spotify_web": 303}
        ctx = _ctx(t)
        motif, preuves = revue.video_d_une_autre_chaine(t, ctx)
        assert "Kery James - Topic" in motif and preuves["track_ids"] == [1]
        cas = revue.Cas("video_autre_chaine", 1, "M", motif, 0, preuves, "k")
        assert politiques_revue.arbitrage_etendu(cas, t, ctx).action == "retirer_video_recherche"

    def test_une_seule_raison_ne_suffit_pas(self):
        def cas(chaine="Kery James - Topic", duree=257, durees=None, invites=None):
            v = TrackVideo(video_id="V", title="X", channel=chaine)
            v.duration = duree
            t = _t("Viser la victoire", 1, videos=[v], genius_id=11)
            t.primary_artist_name = "Admiral T"
            t.featured_artists = invites
            t.durations_observees = durees if durees is not None else {"deezer": 303}
            return revue.video_d_une_autre_chaine(t, _ctx(t))

        assert cas() is not None
        assert cas(chaine="Admiral T - Topic") is None  # son artiste
        assert cas(chaine="Release - Topic") is None  # aucun artiste
        assert cas(chaine="Johnny Dee") is None  # un particulier
        assert cas(invites="Kery James") is None  # un invité de la fiche
        assert cas(duree=None) is None  # durée de la vidéo inconnue
        assert cas(durees={}) is None  # rien à comparer
        assert cas(durees={"youtube_video": 303}) is None  # aucun catalogue lu par ID
        assert cas(duree=295) is None  # 8 s : pas assez pour un retrait
        assert cas(durees={"deezer": 303, "discogs": 258}) is None  # une source d'accord

    def test_la_recherche_dit_l_interprete_principal(self):
        vue = TrackVideo(
            video_id="B",
            title="Baby",
            recherche={"titre": "Baby", "artistes": ["Dala", "Booba"], "album": "MLJ Hills"},
        )
        booba = _t("Baby", 1, videos=[vue], genius_id=11, album="Panthéon")
        dala = _t("Baby", 2, videos=[vue], genius_id=12, album="MLJ HILLS")
        dala.primary_artist_name = "Dala"
        assert revue.proprietaire_de_la_video([booba, dala], vue) is dala

    def test_rien_ne_departage(self):
        """Un titre sans tête (« KING »), une tête qui n'est pas un artiste
        (« Moon - Kanye West, Sunday Service »), deux pages du même interprète,
        ou des invités seuls nommés : la vidéo reste à l'humain."""
        proprio = revue.proprietaire_de_la_video
        a = _t("KING", 1, genius_id=11, album="BULLY")
        b = _t("KING", 2, genius_id=12, album="VULTURES 1")
        assert proprio([a, b], self._v("K", "KING")) is None
        assert proprio([a, b], self._v("K", "Kanye West - KING")) is None
        b.primary_artist_name = "Sunday Service Choir"
        assert proprio([a, b], self._v("M", "KING - Kanye West, Sunday Service")) is None
        assert proprio([a, b], self._v("M", "Ty Dolla $ign feat. Kanye West - KING")) is None

    def test_l_interprete_en_queue_du_titre(self):
        """2026-10-29 : « Titre - Artiste » (« ON MY OWN - CONSEQUENCE ft. KID
        CUDI ») — la queue, invités coupés, désigne la page de Consequence."""
        jaden = _t("On My Own", 1, genius_id=11, album="ERYS")
        jaden.primary_artist_name = "Jaden"
        cons = _t("On My Own", 2, genius_id=12, album="Curb Certified")
        cons.primary_artist_name = "Consequence"
        vue = self._v("O", "ON MY OWN - CONSEQUENCE ft. KID CUDI [LYRICS]")
        gagnante, temoin = revue.proprietaire_et_temoin([jaden, cons], vue)
        assert gagnante is cons and "CONSEQUENCE" in temoin

    def test_le_lien_pose_a_la_main(self):
        """« Reborn » : posé à la main sur *KIDS SEE GHOSTS*, par la page
        d'album YTM sur le remix de Dirty Nice."""
        ksg = _t("Reborn", 1, genius_id=11, album="KIDS SEE GHOSTS")
        ksg.videos = [TrackVideo(video_id="R", title="Reborn", source="manual")]
        remix = _t("Reborn", 2, genius_id=12)
        remix.primary_artist_name = "Dirty Nice"
        remix.videos = [TrackVideo(video_id="R", title="Reborn", source="ytm_album")]
        assert revue.proprietaire_de_la_video([ksg, remix], ksg.videos[0]) is ksg

    def test_l_album_nomme_par_la_video(self):
        """« Low (Cruis'n Usa) » : le disque, sans son « (Deluxe) »."""
        sos = _t("Low", 1, genius_id=11, album="SOS")
        cruisn = _t("Low", 2, genius_id=12, album="Cruis’n USA (Deluxe)")
        vue = self._v("L", "Travis Scott, Jason Eric - Low (Cruis'n Usa)")
        assert revue.proprietaire_de_la_video([sos, cruisn], vue) is cruisn

    def test_l_album_vu_par_la_recherche(self):
        vue = TrackVideo(
            video_id="O", title="OUTSIDE", recherche={"titre": "OUTSIDE", "album": "JACKBOYS 2"}
        )
        jb = _t("OUTSIDE", 1, genius_id=11, album="JACKBOYS 2")
        bitt = _t("outside", 2, genius_id=12, album="Birds in the Trap Sing McKnight")
        jb.videos = bitt.videos = [vue]
        assert revue.proprietaire_de_la_video([jb, bitt], vue) is jb

    def test_la_recherche_vue_sur_une_autre_ligne(self):
        """« Double Discours - Live » : la recherche n'a vu l'album que sur la
        ligne de *Jihad* ; elle désigne la fiche live de la *Don't Panik Tape*."""
        sans = TrackVideo(video_id="D", title="Double Discours - Live", recherche={"titre": "x"})
        avec = TrackVideo(
            video_id="D", title="Double Discours - Live", recherche={"album": "Don't Panik Tape"}
        )
        jihad = _t("Double discours", 1, genius_id=11, album="Jihad", videos=[avec])
        live = _t("Double Discours - Live", 2, album="Don't Panik Tape", videos=[sans])
        assert revue.proprietaire_de_la_video([jihad, live], sans) is live

    def test_la_seule_page_nommee(self):
        vue = self._v("D", "Diddy - Intro - Featuring Kanye West & Kendrick Lamar FRESH LEAK")
        intro = _t("Intro (Kendrick Lamar Reference)", 1, genius_id=11, album="MMM")
        like = _t("Like It", 2, genius_id=12)
        for t in (intro, like):
            t.primary_artist_name = "Diddy"
        assert revue.proprietaire_de_la_video([intro, like], vue) is intro

    def test_des_temoins_en_desaccord_ne_tranchent_pas(self):
        """L'interprète désigne une page, le lien manuel une autre : à l'humain."""
        a = _t("Selfish", 1, genius_id=11, album="DONDA 2")
        b = _t("Selfish", 2, genius_id=12, album="Detroit Deli")
        b.primary_artist_name = "Slum Village"
        clip = TrackVideo(video_id="S", title="Slum Village - Selfish", source="manual")
        a.videos = [clip]
        b.videos = [TrackVideo(video_id="S", title="Slum Village - Selfish", source="genius_media")]
        assert revue.proprietaire_et_temoin([a, b], clip) == (None, None)

    def test_un_film_qui_ne_nomme_aucune_page_ne_part_nulle_part(self):
        """Le film *New Blues Old Wine* nomme l'album d'« HARMONIE », pas le
        morceau : la vidéo doit nommer la fiche gardée."""
        film = self._v("F", "B.B. Jacques - NEW BLUES OLD WINE, LE FILM - Part. I")
        h = _t("HARMONIE", 1, genius_id=11, album="NEW BLUES, OLD WINE")
        f = _t("FIANCÉE", 2, genius_id=12)
        assert revue.proprietaire_de_la_video([h, f], film) is None

    def test_raison_du_refus_de_recherche(self):
        """2026-10-29 : un artiste étranger, un autre remix se MONTRENT ; un nom
        qui contient celui de la fiche (« Iam Ike Hill » / IAM) ne prouve rien."""
        raison = revue.raison_du_refus_de_recherche
        isha = _t("Interlude", 1, album="Vas-y chante")
        assert raison(isha, {"titre": "Interlude", "artistes": ["AISHA"]}) == "artiste"
        intro = _t("Intro", 2)
        intro.primary_artist_name = "IAM"
        assert raison(intro, {"titre": "Intro", "artistes": ["Iam Ike Hill"]}) == "titre"
        remix = _t("In Common (Remix)", 3)
        remix.primary_artist_name = "Alicia Keys"
        vu = {"titre": "In Common (Kaskade Remix)", "artistes": ["Alicia Keys"]}
        assert raison(remix, vu) == "version"

    def test_un_clip_d_un_meme_disque_n_est_pas_departage(self):
        clip = self._v("C", "Kanye West - Champions")
        a = _t("Champions", 1, genius_id=11, album="Cruel Summer")
        b = _t("Champions", 2, genius_id=12, album="Cruel Summer")
        b.primary_artist_name = "Dame Dash"
        assert revue.proprietaire_de_la_video([a, b], clip) is None

    def test_un_clip_double_qui_nomme_ses_deux_titres_n_est_pas_signale(self):
        clip = self._v("DBL", "Booba - Donjon & 2h22")
        ctx = _ctx(_t("Donjon", 1, videos=[clip]), _t("2h22", 2, videos=[clip]))
        assert revue.video_partagee(ctx) == []

    def test_deux_pages_homonymes_que_la_video_nomme_sont_un_cas_sans_retrait(self):
        """« KING » de *BULLY* et de *VULTURES 1* (2026-10-29) : la vidéo les
        nomme toutes deux, rien ne dit laquelle elle est. Un cas, sans le geste
        « retirer de ces fiches », qui l'ôterait aussi de la bonne."""
        from src.services import revue_actions

        clip = self._v("K", "KING", 800)
        a = _t("KING", 1, videos=[clip], genius_id=11, album="BULLY")
        b = _t("KING", 2, videos=[clip], genius_id=12, album="VULTURES 1")
        ((_, motif, preuves),) = revue.video_partagee(_ctx(a, b))
        assert preuves["homonymes"] and preuves["track_ids"] == [1, 2]
        assert "même titre" in motif
        cas = revue.Cas("video_partagee", None, "KING", motif, 800, preuves, "k")
        assert revue_actions.actions_pour(cas) == []

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

    def test_deux_lignes_soeurs_ne_sont_pas_un_partage(self):
        """« Moon » (2026-10-29) : la discographie réunie d'un membre porte la
        ligne du groupe ET la sienne, sœurs d'une même page Genius. Une vidéo
        qu'elles portent toutes deux n'est pas partagée : aucun cas
        `video_partagee`, et la vidéo muette reste jugée par 🎬 et 📺 comme
        celle d'UNE fiche."""
        clip = self._v("M", "Concert entier", 500)
        cudi = _t("Moon", 1, videos=[clip], genius_id=11, album="Donda")
        ye = _t("Moon", 2, videos=[clip], genius_id=11, album="Donda")
        ye.artist = Artist(name="Kanye West")
        ctx = _ctx(cudi, ye)
        assert revue.video_partagee(ctx) == []
        assert revue.video_etrangere(cudi, ctx) is not None
        # Deux PAGES, elles, partagent.
        autre = _t("Moon", 3, videos=[clip], genius_id=12)
        assert revue.video_etrangere(cudi, _ctx(cudi, ye, autre)) is None

    def test_les_vues_d_une_video_portee_par_deux_soeurs_s_expliquent(self):
        clip = TrackVideo(video_id="M", title="Moon", views=100)
        a = _t("Moon", 1, videos=[clip], genius_id=11)
        b = _t("Moon", 2, videos=[clip], genius_id=11)
        assert revue._vues_expliquees(a, _ctx(a, b))
        assert not revue._vues_expliquees(a, _ctx(a, b, _t("Moon", 3, videos=[clip])))

    def _530(self, catalogues=None, source="ytm_album", titre="530"):
        """« 530 » : l'audio « ¥$ - Topic » (288 s) que la page d'album YTM de
        *VULTURES 2* pose sur « 530 » (288 s), et que Genius pose aussi sur
        « 530 (Haikou LP) », sans catalogue (2026-10-30)."""

        def audio(provenance):
            return TrackVideo(
                video_id="H",
                title="530",
                views=10,
                duration=288,
                channel="¥$ - Topic",
                source=provenance,
            )

        a = _t(titre, 1, videos=[audio(source)], genius_id=11, album="VULTURES 2")
        a.durations_observees = catalogues if catalogues is not None else {"deezer": 288}
        a.primary_artist_name = "¥$"
        b = _t("530 (Haikou LP)", 2, videos=[audio("genius_media")], genius_id=12)
        b.primary_artist_name = "¥$"
        return _ctx(a, b)

    def test_la_piste_de_l_album_ytm_designe_sa_page(self):
        ((_, _, preuves),) = revue.video_partagee(self._530())
        assert preuves["garder"] == 1 and preuves["track_ids"] == [2]
        assert preuves["temoin"].startswith("piste de l'album YouTube Music")

    def test_l_album_ytm_ne_dit_rien_sans_l_accord_des_catalogues(self):
        for ctx in (
            self._530(catalogues={}),  # rien à comparer
            self._530(catalogues={"deezer": 163}),  # une autre durée
            self._530(source="genius_media"),  # aucune piste d'album
            self._530(titre="* (Part. 1)"),  # titre fait de signes
        ):
            assert all("garder" not in p for _, _, p in revue.video_partagee(ctx))

    def test_un_cover_ne_garde_pas_l_audio_de_l_original(self):
        """« Ghost Town » : la reprise de Justice Der (ligne de Kanye, l'auteur)
        portait l'audio « Kanye West - Topic » de l'original. L'artiste de la
        ligne d'une reprise n'est pas un interprète : la chaîne désigne
        l'original."""
        audio = TrackVideo(video_id="G", title="Ghost Town", views=10, channel="Kanye West - Topic")
        ye = _t("Ghost Town", 1, videos=[audio], genius_id=11, album="ye")
        ye.artist = Artist(name="Kanye West")
        cover = _t("Ghost Town", 2, videos=[audio], genius_id=12, album="Covers")
        cover.artist = Artist(name="Kanye West")
        cover.primary_artist_name = "Justice Der"
        cover.secondary_role = "Cover"
        ((_, _, preuves),) = revue.video_partagee(_ctx(ye, cover))
        assert preuves["garder"] == 1 and preuves["track_ids"] == [2]
        cover.secondary_role = "Writer"  # un crédit d'écriture lie, lui
        ((_, _, preuves),) = revue.video_partagee(_ctx(ye, cover))
        assert "garder" not in preuves


class TestVideoTropLongue:
    """2026-10-30 : la vidéo Genius d'un concert, d'une émission, d'une
    compilation, posée sur un seul de ses morceaux (CORRECTIFS, B.B. Jacques)."""

    @staticmethod
    def _fiche(titre_video, duree_video=926, durees=None, **kw):
        v = TrackVideo(
            video_id="V",
            title=titre_video,
            views=50,
            duration=duree_video,
            source=kw.pop("source", "genius_media"),
        )
        t = _t(kw.pop("titre", "Temptations (Tiny Desk Home)"), 1, videos=[v], genius_id=11, **kw)
        t.durations_observees = durees if durees is not None else {"songbpm": 153}
        return t

    def test_le_concert_entier_qui_ne_nomme_pas_le_morceau_part(self):
        from src.services import politiques_revue

        t = self._fiche("Ty Dolla $ign: Tiny Desk (Home) Concert")
        motif, preuves = revue.video_trop_longue(t, _ctx(t))
        assert preuves["retirer"] and preuves["track_ids"] == [1]
        assert "6,1 fois" in motif
        cas = revue.Cas("video_trop_longue", 1, "M", motif, 0, preuves, "k")
        arbitrage = politiques_revue.arbitrage_etendu(cas, t, _ctx(t))
        assert arbitrage.action == "retirer_video_recherche"

    def test_nommee_elle_va_a_l_humain(self):
        from src.services import politiques_revue

        t = self._fiche("Nina Simone - Sinnerman", 621, {"discogs": 134}, titre="Sinnerman")
        motif, preuves = revue.video_trop_longue(t, _ctx(t))
        assert not preuves["retirer"] and "le nomme" in motif
        cas = revue.Cas("video_trop_longue", 1, "M", motif, 0, preuves, "k")
        assert politiques_revue.arbitrage_etendu(cas, t, _ctx(t)) is None

    def test_les_seuils(self):
        def cas(titre_video, duree_video, titre="Elixir"):
            t = self._fiche(titre_video, duree_video, {"deezer": 100}, titre=titre)
            return revue.video_trop_longue(t, _ctx(t))

        assert cas("1 son en 1H", 300) is None  # 3 × : pas au-delà
        assert cas("1 son en 1H", 301) is not None
        assert cas("Elixir (Court métrage)", 400) is None  # nommée : 4 ×
        assert cas("Elixir (Court métrage)", 401) is not None

    def test_ce_qui_n_est_pas_jugé(self):
        def cas(**kw):
            t = self._fiche("Ty Dolla $ign: Tiny Desk (Home) Concert", **kw)
            return revue.video_trop_longue(t, _ctx(t))

        assert cas(source="search_auto") is None  # pas un lien Genius
        assert cas(duree_video=None) is None  # durée de la vidéo inconnue
        assert cas(durees={}) is None  # aucune durée de la fiche
        assert cas(durees={"youtube_video": 153, "legacy": 153}) is None  # tirées des vidéos
        assert cas(durees={"songbpm": 153, "deezer": 400}) is None  # la plus longue juge
        disque = self._fiche("BEN plg - Paraît que (Album entier)", album="Paraît que")
        assert revue.video_trop_longue(disque, _ctx(disque)) is None
        medley = self._fiche("Concert", relationships=[{"type": "contient", "track_id": 2}])
        assert revue.video_trop_longue(medley, _ctx(medley)) is None

    def test_portee_par_deux_pages_elle_est_a_video_partagee(self):
        a = self._fiche("Ty Dolla $ign: Tiny Desk (Home) Concert")
        b = _t("Other", 2, videos=list(a.videos), genius_id=12)
        assert revue.video_trop_longue(a, _ctx(a, b)) is None
        soeur = _t("Temptations (Tiny Desk Home)", 3, videos=list(a.videos), genius_id=11)
        assert revue.video_trop_longue(a, _ctx(a, soeur)) is not None


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


class TestVoteDesCatalogues:
    """Les détecteurs de durée accusent la valeur que les catalogues démentent
    (2026-10-13)."""

    def test_le_temoin_isole_est_accuse_pas_l_id(self):
        t = _t(
            "Vice Versa",
            1,
            spotify_id="x",
            deezer_id=1,
            durations_observees={
                "deezer": 240,
                "spotify_web": 240,
                "apple_music": 240,
                "youtube": 277,
            },
        )
        assert revue.duree_spotify_dementie(t) is None
        assert revue.duree_deezer_dementie(t) is None
        motif, preuves = revue.duree_isolee(t)
        assert "youtube 277 s" in motif and preuves["youtube"] == 277

    def test_l_id_isole_reste_accuse(self):
        t = _t(
            "Hold On",
            1,
            spotify_id="x",
            durations_observees={"spotify_web": 285, "deezer": 201, "youtube": 201},
        )
        motif, _ = revue.duree_spotify_dementie(t)
        assert "démentie par" in motif and revue.duree_isolee(t) is None

    def test_songbpm_contre_un_audio_youtube_isole(self):
        t = _t(
            "Choc frontal",
            1,
            durations_observees={"songbpm": 137, "deezer": 137, "spotify_web": 137, "youtube": 195},
        )
        assert revue.duree_songbpm_dementie(t) is None
        assert revue.duree_isolee(t)


def test_le_temoin_d_une_edition_connue_n_est_pas_accuse():
    t = _t(
        "Pourquoi",
        1,
        durations_observees={"deezer": 226, "spotify_web": 226, "youtube": 232},
    )
    assert revue.duree_isolee(t)
    t.durees_editions = (232,)
    assert revue.duree_isolee(t) is None


class TestPageEtFichePlateforme:
    """🪞 forme « Make Her Say » (2026-10-30) : une fiche sans page Genius au
    titre de base d'une page Genius. Mesuré sur toute la base : 5 cas (4
    doublons dont la page n'a aucun identifiant, 1 session Deezer), plus 3 au
    📀 (même place d'un disque)."""

    @staticmethod
    def _f(titre, tid, duree, genius_id=None, **kw):
        t = _t(titre, tid, duration=duree, genius_id=genius_id, **kw)
        if genius_id:
            t.genius_url = f"https://genius.com/{tid}"
        return t

    def test_page_sans_identifiant_et_fiche_plateforme_meme_duree(self):
        page = self._f("Sous influence", 1, 307, genius_id=11)
        fiche = self._f("Sous influence (bonus track)", 2, 307, spotify_id="SP")
        ctx = _ctx(page, fiche)
        motif, preuves = revue.page_et_fiche_plateforme(fiche, ctx)
        assert "à fusionner vers la page" in motif and preuves["autres"] == [1]
        assert revue.page_et_fiche_plateforme(page, ctx) is None

    def test_make_her_say_durees_ecartees(self):
        page = self._f("Make Her Say (I Poke Her Face)", 1, 216, genius_id=11, spotify_id="A")
        fiche = self._f("Make Her Say", 2, 237, spotify_id="B")
        motif, _ = revue.page_et_fiche_plateforme(fiche, _ctx(page, fiche))
        assert "Make Her Say" in motif and "original" in motif

    def test_lettres_masquees_meme_titre(self):
        page = self._f("Illest Motherfucker Alive", 1, 323, genius_id=11, spotify_id="A")
        fiche = self._f("Illest Motherf**ker Alive (Album Version Edited)", 2, 503, spotify_id="B")
        assert revue.page_et_fiche_plateforme(fiche, _ctx(page, fiche))

    def test_ce_qui_n_est_pas_la_forme(self):
        page = self._f("Blues", 1, 175, genius_id=11, spotify_id="A")
        live = self._f("Blues (Acoustic)", 2, 189, spotify_id="B")  # une autre version
        partie = self._f("Blues (Part. 2)", 3, 175, spotify_id="C")  # une autre partie
        meme_id = self._f("Blues", 4, 175, spotify_id="A")  # `id_catalogue_partage`
        sans_id = self._f("Blues", 5, 175)
        ctx = _ctx(page, live, partie, meme_id, sans_id)
        for t in (live, partie, meme_id, sans_id):
            assert revue.page_et_fiche_plateforme(t, ctx) is None

    def test_autre_interprete_et_generique_d_un_autre_disque(self):
        page = self._f("Forever", 1, 251, genius_id=11, is_featuring=True)
        page.primary_artist_name = "Dom Dolla"
        fiche = self._f("Forever", 2, 251, spotify_id="B")
        intro = self._f("Intro", 3, 60, genius_id=12, album="0.9")
        autre = self._f("Intro", 4, 60, spotify_id="C", album="Lunatic")
        ctx = _ctx(page, fiche, intro, autre)
        assert revue.page_et_fiche_plateforme(fiche, ctx) is None
        assert revue.page_et_fiche_plateforme(autre, ctx) is None

    def test_meme_place_laissee_au_disque(self):
        page = self._f("Save My Soul (The CuDi Confession)", 1, 123, genius_id=11, spotify_id="A")
        fiche = self._f("Save My Soul", 2, 123, deezer_id=8)
        place = {"release_id": 9, "title": "X", "disc_number": 1, "track_number": 12}
        ctx = _ctx(page, fiche)
        ctx.positions = {
            1: [{**place, "position_source": "apple_music"}],
            2: [{**place, "position_source": "deezer"}],
        }
        assert revue.page_et_fiche_plateforme(fiche, ctx) is None
        assert revue.meme_place(page, ctx)
