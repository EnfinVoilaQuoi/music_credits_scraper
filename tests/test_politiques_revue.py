"""Politiques d'arbitrage automatique « À trancher » — PROPOSÉES, non branchées
(`src/services/politiques_revue.py`, 2026-10-19). Chaque test fixe un sens
mesuré au passage à blanc, ou une garde trouvée en le relisant."""

from types import SimpleNamespace

import pytest

from src.models import Artist, Track
from src.services import politiques_revue as pol
from src.services import revue

_A = Artist(name="A2H")


def _t(titre="X", tid=1, **kw):
    t = Track(title=titre, artist=_A)
    t.id = tid
    t.durees_editions = ()
    for k, v in kw.items():
        setattr(t, k, v)
    return t


def _cas(detecteur, track=None, preuves=None):
    return revue.Cas(detecteur, track.id if track else None, "M", "", 0, preuves or {}, "k")


class TestDurees:
    juger = staticmethod(pol.politique_durees(2))

    def test_id_spotify_d_un_autre_enregistrement(self):
        t = _t(
            spotify_id="SP",
            durations_observees={
                "spotify_web": 202,
                "reccobeats": 202,
                "deezer": 224,
                "youtube": 225,
            },
        )
        assert self.juger(_cas("duree_spotify", t), t, None).action == "retirer_spotify"

    def test_un_petit_ecart_est_peut_etre_une_edition(self):
        """« Viens danser avec les Lunatic » : 178 s contre 170 s — rien n'est
        retiré ; une édition hors colonne est ✓ Normal (2026-10-27), celle qui
        tient la colonne reste à l'humain."""
        t = _t(
            spotify_id="SP",
            durations_observees={"spotify_web": 178, "deezer": 170, "youtube": 170, "ytmusic": 170},
        )
        t.duration_source = "deezer"
        assert self.juger(_cas("duree_spotify", t), t, None).action == "normal"
        t.duration_source = "spotify_web"
        assert self.juger(_cas("duree_spotify", t), t, None) is None

    def test_relecture_reccobeats_d_accord_ne_prouve_pas_l_id(self):
        """ReccoBeats lit d'abord par l'ISRC : l'ID n'est jamais retiré. Hors de
        la colonne, la durée démentie ne fausse rien : ✓ Normal ; si elle tient
        la colonne, l'action reste à définir."""
        t = _t(
            spotify_id="SP",
            durations_observees={
                "spotify_web": 78,
                "reccobeats": 275,
                "deezer": 275,
                "youtube": 276,
            },
        )
        t.duration_source = "deezer"
        assert self.juger(_cas("duree_spotify", t), t, None).action == "normal"
        t.duration_source = "spotify_web"
        assert self.juger(_cas("duree_spotify", t), t, None).action == ""

    def test_catalogue_en_desaccord_avec_lui_meme_ne_prouve_rien(self):
        """« Guess Who's Back » : page Spotify 241 s, ReccoBeats 255 s."""
        t = _t(
            deezer_id=1,
            durations_observees={
                "deezer": 243,
                "spotify_web": 241,
                "reccobeats": 255,
                "ytmusic": 256,
            },
        )
        assert self.juger(_cas("duree_deezer", t), t, None) is None

    def test_temoin_isole_hors_colonne(self):
        t = _t(
            duration=146,
            durations_observees={
                "deezer": 146,
                "spotify_web": 146,
                "apple_music": 147,
                "youtube": 337,
            },
        )
        t.duration_source = "deezer"
        assert self.juger(_cas("duree_isolee", t), t, None).action == "normal"

    def test_hit_ytm_dementi_par_l_audio_topic(self):
        """Kanye « Monster » #11401 (2026-10-29) : la piste YTM (hit d'une
        recherche de paroles) à 150 s, l'audio Topic et Apple à 379 s. L'audio,
        de la même famille au vote, est un autre fichier : il dément le hit."""
        t = _t(
            "Monster",
            duration=379,
            durations_observees={
                "apple_music": 379,
                "discogs": 378,
                "youtube": 379,
                "ytmusic": 150,
            },
        )
        t.duration_source = "apple_music"
        arbitrage = self.juger(_cas("duree_isolee", t), t, None)
        assert arbitrage.action == "normal"
        assert arbitrage.raison == "ytmusic 150 s contre 379 s"
        # Le hit qui tient la colonne reste à l'humain.
        t.duration_source = "ytmusic"
        assert self.juger(_cas("duree_isolee", t), t, None).action == ""

    def test_hit_ytm_sans_audio_topic_reste_a_un_seul_catalogue(self):
        """Sans audio Topic, Apple seul contre le hit ne suffit pas."""
        t = _t("Monster", duration=379, durations_observees={"apple_music": 379, "ytmusic": 150})
        t.duration_source = "apple_music"
        assert self.juger(_cas("duree_isolee", t), t, None) is None

    def test_hit_ytm_d_accord_avec_l_audio_n_est_pas_dementi(self):
        """Hit et audio au même fichier : une seule voix, rien ne change."""
        t = _t(
            duration=240,
            durations_observees={"deezer": 240, "youtube": 277, "ytmusic": 277},
        )
        t.duration_source = "deezer"
        assert pol._dementie(t.durations_observees, "ytmusic", (), 2) is None


class TestLiensDeezer:
    def _cas(self, t, titre, duree, isrc=None):
        ecart = {"piste": {"title": titre, "duration": duree, "isrc": isrc}}
        return _cas("deezer_link_candidate", t, {"ecart": ecart})

    def test_radio_edit_est_une_edition(self):
        t = _t("Avant de partir", durations_observees={"deezer": 325})
        arb = pol.politique_liens_deezer(self._cas(t, "Avant De Partir (Radio Edit)", 250), t, None)
        assert "ÉDITION" in arb.sens

    def test_un_temoin_dementi_ne_suffit_pas(self):
        """Flynt « J'éclaire ma ville » : Deezer 159 s = la vidéo isolée, fiche à 265 s."""
        t = _t(
            "J'éclaire ma ville",
            durations_observees={"spotify_web": 265, "apple_music": 265, "youtube": 159},
        )
        arb = pol.politique_liens_deezer(self._cas(t, "J'éclaire ma ville", 159), t, None)
        assert arb is None or arb.sens != "rattacher"

    def test_meme_isrc_meme_titre(self):
        t = _t("Playmate", isrc="FR1")
        arb = pol.politique_liens_deezer(self._cas(t, "Playmate", 200, "fr1"), t, None)
        assert arb.sens == "rattacher"

    def _clic_clic(self, titre="Clic clic", **durees):
        t = _t(
            "Clic Clic",
            4128,
            isrc="FR6V80708984",
            deezer_id=209376,
            duration=172,
            durations_observees=durees
            or {"apple_music": 172, "deezer": 172, "spotify_web": 172, "reccobeats": 172},
        )
        cas = self._cas(t, titre, 175, "FR43Q0700005")
        cas.preuves["ecart"]["piste"]["id"] = 15003630
        return cas, t

    def test_seconde_page_soutenue_rattachee_en_edition(self):
        """MC Solaar « Clic clic » (2026-10-31) : la fiche a sa page (172 s) ; le
        single de 2011, 175 s, autre ISRC, que le vote soutient par Apple et
        Spotify — une ÉDITION, ce que le motif annonçait déjà."""
        cas, t = self._clic_clic()
        arb = pol.politique_liens_deezer(cas, t, None)
        assert arb.action == "deezer_appliquer" and "ÉDITION" in arb.sens

    def test_seconde_page_d_une_seule_voix_reste_a_l_humain(self):
        cas, t = self._clic_clic(deezer=172, spotify_web=172)
        assert pol.politique_liens_deezer(cas, t, None) is None

    def test_seconde_page_d_une_version_reste_a_l_humain(self):
        cas, t = self._clic_clic(titre="Clic clic (Remix)")
        arb = pol.politique_liens_deezer(cas, t, None)
        assert arb is None or arb.action != "deezer_appliquer"


class TestNonMusique:
    """Pages Genius « Non-Music » sans trace (décision utilisateur 2026-11-01) :
    témoin les pages tranchées à la main le 2026-10-31 et les 16 d'Eminem."""

    @pytest.mark.parametrize(
        ("titre", "paroles", "nature"),
        [
            ("Come As You Are", "[Verse 1 : Sunday Service Choir]\nCome as you are", "chant"),
            ("Last Freestyle (I Met Oprah)", "[Intro: Kanye West]\nThis my last", "chant"),
            ("Brighten Rehab (Skit)", "*Crickets chirping, running, dogs barking*", "sketch"),
            ("Alfred (Outro) (Alternative Version)", "[Intro: Alfred Hitchcock]\nYou", "sketch"),
            ("2 Strong Men", "[Skit: Ken Kaniff]\nYes, um, yes, this is Ken", "sketch"),
            ("Graduation (Skit)", "", "sketch"),
            ("TAKEME2RIVER", "", "sans_texte"),
            ("Yam", "[Sample : Prynce Geno]\nLove\nLove\nLove", "texte"),
            ("I Know, I Know (Love Everyone)", " ".join(["Know, I know,"] * 24), "texte"),
            (
                "Rolling Stone Q/A Exclusive 2013",
                "Are you feeling good about the album at this point, now that it is done?\n"
                "Um, that's a tough question, man. For the most part, yeah. But I don't know.",
                "texte",
            ),
            # Des lignes courtes sans section : peut-être des paroles — l'humain.
            ("Untitled", "Yeah, I'm up three\nDouble cup and Rémy\nWe've been outside", None),
        ],
    )
    def test_nature(self, titre, paroles, nature):
        assert pol.nature_non_musique(titre, paroles) == nature

    def test_politique(self):
        t = _t("Kim’s New Man (Skit)")
        t.lyrics.text = "[Skit: Eminem mocking Kim]\nIt’s alright, just come out!"
        arb = pol.politique_non_musique(_cas("annotee", t), t, None)
        assert arb.action == "desactiver_non_musique"
        t = _t("Berzerk Explained")
        t.lyrics.text = (
            "I had met Paul, Em's manager, a few years ago and we always talked about the"
            " possibility of getting together to work on something at some point down the line"
        )
        arb = pol.politique_non_musique(_cas("annotee", t), t, None)
        assert arb.action == "supprimer_non_musique"
        assert ("annotee", arb.action) in pol.REGLES_ETENDUES


class TestFicheDuMemeDisque:
    """`deezer_absent` d'Eminem (2026-11-02) : la piste « absente » est celle
    d'une fiche de son disque — mention de bande originale, année que Genius
    ajoute à un skit récurrent, deux éditions de diffusion."""

    def _cas(self, titre, duree, disque):
        ecart = {"piste": {"title": titre, "duration": duree}, "album": {"title": disque}}
        return revue.Cas("deezer_absent", None, titre, "", 0, {"ecart": ecart}, "k")

    def _juger(self, cas, *fiches):
        return pol._rattacher_a_la_fiche_du_disque(cas, revue.contexte(list(fiches), {}))

    def test_annee_de_genius_entre_crochets(self):
        encore = _t("Em Calls Paul (Skit) [2004]", 13897, album="Encore", duration=72)
        kamikaze = _t("Em Calls Paul (Skit) [2018]", 13261, album="Kamikaze", duration=50)
        arb = self._juger(
            self._cas("Em Calls Paul (Skit)", 72, "Encore (Deluxe Version)"), encore, kamikaze
        )
        assert arb.action == "deezer_rattacher_fiche" and "#13897" in arb.raison
        arb = self._juger(self._cas("Em Calls Paul (Skit)", 49, "Kamikaze"), encore, kamikaze)
        assert "#13261" in arb.raison

    def test_mention_de_bande_originale(self):
        t = _t("The King and I", 13122, album="Curtain Call 2", duration=192)
        titre = "The King and I (From the Original Motion Picture Soundtrack ELVIS)"
        assert self._juger(self._cas(titre, 193, "Curtain Call 2"), t) is not None

    def test_deux_editions_de_diffusion(self):
        t = _t(
            "Billion Bucks (Bonus Track)", 14733, album="Eminem Presents the Re-Up", duration=222
        )
        cas = self._cas("Billion Bucks (Album Version Explicit)", 222, "Eminem Presents The Re-Up")
        assert self._juger(cas, t) is not None

    def test_autre_disque_ou_autre_duree_ou_version_jamais(self):
        bad_guys = _t("Bad Guys Always Die", 1, album="The Next Episode", duration=280)
        titre = 'Bad Guys Always Die (From "Wild Wild West" Soundtrack)'
        assert (
            self._juger(self._cas(titre, 279, "The Slim Shady LP (Expanded Edition)"), bad_guys)
            is None
        )
        encore = _t("Em Calls Paul (Skit) [2004]", 2, album="Encore", duration=72)
        assert self._juger(self._cas("Em Calls Paul (Skit)", 68, "Encore"), encore) is None
        live = _t("Lose Yourself", 3, album="Curtain Call", duration=321)
        assert self._juger(self._cas("Lose Yourself (Live)", 321, "Curtain Call"), live) is None

    def test_lettres_masquees_et_disque_d_un_generique(self):
        """The Game (2026-11-03) : l'édition « clean » de *Doctor's Advocate*
        masque « Shit » ; Genius nomme l'intro de *LAX* « Intro (LAX) »."""
        da_shit = _t("Da Shit", 15717, album="Doctor’s Advocate", duration=323)
        arb = self._juger(self._cas("Da S*** (Album Version)", 323, "Doctor's Advocate"), da_shit)
        assert arb.action == "deezer_rattacher_fiche" and "#15717" in arb.raison
        intro = _t("Intro (LAX)", 15622, album="LAX", duration=81)
        arb = self._juger(self._cas("Intro (Album Version)", 81, "LAX (iTunes Edited)"), intro)
        assert "#15622" in arb.raison
        # Le nom d'un AUTRE disque n'est pas retiré : l'intro d'ailleurs reste.
        autre = _t("Intro (The Documentary 2)", 15206, album="LAX", duration=81)
        assert self._juger(self._cas("Intro", 81, "LAX"), autre) is None

    def test_deux_fiches_possibles_l_humain(self):
        a = _t("Paul Rosenberg (Skit)", 1, album="The Eminem Show", duration=22)
        b = _t("Paul Rosenberg (Skit) [2002]", 2, album="The Eminem Show", duration=23)
        assert self._juger(self._cas("Paul Rosenberg (Skit)", 22, "The Eminem Show"), a, b) is None


class TestPisteDUnAutreArtiste:
    """The Game (2026-11-03) : *G-Code*, EP crédité à The Game, Garry et Karam
    Brar — Deezer ne crédite pas The Game sur « Sahnewal », « Lost In You »… ;
    « Shoot First » est de Big Kuza (BO de *Bosco*). Aucune fiche à créer."""

    def _cas(self, detecteur="deezer_album_absent", titre="Sahnewal (G-Code)"):
        ecart = {
            "piste": {"id": 9, "title": titre, "duration": 151},
            "album": {"id": 5, "title": "G-Code", "artist_id": 393, "artist_name": "The Game"},
        }
        return revue.Cas(detecteur, None, titre, "", 0, {"ecart": ecart}, "k")

    def _juger(self, cas, contributeurs):
        piste = {"id": 9, "contributors": [{"id": i, "name": n} for i, n in contributeurs]}
        return pol._piste_d_un_autre_artiste(cas, {393}, lambda _id: piste)

    def test_pas_creditee_pas_de_fiche(self):
        arb = self._juger(self._cas(), [(4680956, "Garry")])
        assert arb.action == "normal" and "Garry" in arb.raison

    def test_creditee_par_l_id_ou_le_nom(self):
        dope = [(393, "The Game"), (4680956, "Garry")]
        assert self._juger(self._cas(titre="Dope Shit [G-Code]"), dope) is None
        # Sous un autre profil Deezer, mais à son nom : crédité.
        assert self._juger(self._cas(), [(1, "Garry"), (2, "The Game")]) is None

    def test_sans_credits_lus_l_humain(self):
        assert self._juger(self._cas(), []) is None
        assert pol._piste_d_un_autre_artiste(self._cas(), {393}, lambda _id: None) is None

    def test_seulement_les_pistes_absentes(self):
        cas = self._cas(detecteur="deezer_version")
        assert self._juger(cas, [(4680956, "Garry")]) is None


class TestGeneriqueDeSonDisque:
    """The Game, *LAX* (2026-11-03) : « Intro » / « Outro » proposées sur LE
    disque de la fiche, seule fiche de ce titre, à la durée que l'ID Spotify
    de la fiche donne aussi — le panneau écrivait déjà « rattachement sûr »."""

    def _cas(self, t, titre, duree, disque, par="title_generique"):
        ecart = {
            "piste": {"title": titre, "duration": duree},
            "album": {"title": disque},
            "matched_by": par,
        }
        return _cas("deezer_link_candidate", t, {"ecart": ecart})

    def _intro(self, **durees):
        return _t(
            "Intro (LAX)",
            15622,
            album="LAX",
            duration=81,
            durations_observees=durees or {"spotify_web": 80, "youtube": 81},
        )

    def test_rattachee(self):
        t = self._intro()
        arb = pol._generique_de_son_disque(
            self._cas(t, "Intro", 80, "LAX"), t, revue.contexte([t], {})
        )
        assert arb.action == "deezer_appliquer" and "spotify_web" in arb.raison

    def test_sans_catalogue_qui_confirme_l_humain(self):
        """« Intro (The Documentary 2) » : seule la vidéo (59 s) témoigne."""
        t = self._intro(youtube_video=81)
        assert (
            pol._generique_de_son_disque(
                self._cas(t, "Intro", 80, "LAX"), t, revue.contexte([t], {})
            )
            is None
        )

    def test_deux_intros_du_disque_l_humain(self):
        t = self._intro()
        autre = _t("Intro", 2, album="LAX", duration=80)
        cas = self._cas(t, "Intro", 80, "LAX")
        assert pol._generique_de_son_disque(cas, t, revue.contexte([t, autre], {})) is None

    def test_rapprochement_par_le_titre_hors_regle(self):
        t = self._intro()
        cas = self._cas(t, "Intro", 80, "LAX", par="title")
        assert pol._generique_de_son_disque(cas, t, revue.contexte([t], {})) is None


class TestLienHorsDuDisque:
    """Skits récurrents d'Eminem (2026-11-02) : la parution d'un AUTRE disque,
    au même ISRC que l'ID déjà posé (lui-même celui de l'autre disque), avait
    été rattachée en édition — « Steve Berman (Skit) » de *The Eminem Show*
    sur celui de *Relapse*."""

    def _cas(self, t, titre, duree, disque, isrc=None, par="title_generique"):
        ecart = {
            "piste": {"title": titre, "duration": duree, "isrc": isrc},
            "album": {"title": disque},
            "matched_by": par,
        }
        return _cas("deezer_link_candidate", t, {"ecart": ecart})

    def _relapse(self):
        return _t(
            "Steve Berman (Skit)",
            13653,
            album="Relapse",
            isrc="USIR10211064",
            durations_observees={"deezer": 33, "reccobeats": 33},
        )

    def test_piste_d_une_autre_fiche_du_disque(self):
        t = self._relapse()
        autre = _t("Steve Berman (Skit) [2002]", 14107, album="The Eminem Show", duration=34)
        cas = self._cas(t, "Steve Berman (Skit)", 33, "The Eminem Show (Expanded Edition)")
        arb = pol.politique_liens_deezer(cas, t, revue.contexte([t, autre], {}))
        assert arb.action == "normal" and "#14107" in arb.raison

    def test_meme_isrc_ne_rattache_pas_un_generique_d_un_autre_disque(self):
        t = self._relapse()
        cas = self._cas(t, "Steve Berman (Skit)", 33, "The Eminem Show", "USIR10211064")
        arb = pol.politique_liens_deezer(cas, t, revue.contexte([t], {}))
        assert arb.action == "normal" and "autre disque" in arb.sens

    def test_titre_non_generique_d_un_autre_disque_sans_autre_fiche(self):
        """« Ken Kaniff » (non générique) sans fiche du disque : le reste de la
        politique juge, comme avant."""
        t = _t("Ken Kaniff", 14319, album="The Slim Shady LP", isrc="X1")
        cas = self._cas(t, "Ken Kaniff", 62, "The Marshall Mathers LP", "x1", par="title")
        assert pol.politique_liens_deezer(cas, t, revue.contexte([t], {})).sens == "rattacher"

    def test_titre_non_generique_d_une_autre_fiche_du_disque(self):
        t = _t("Ken Kaniff", 14319, album="The Slim Shady LP", duration=76)
        mmlp = _t("Ken Kaniff (Skit)", 14234, album="The Marshall Mathers LP", duration=62)
        mmlp2 = _t("Ken Kaniff (MMLP2)", 13405, album="The Marshall Mathers LP 2", duration=454)
        cas = self._cas(t, "Ken Kaniff", 62, "The Marshall Mathers LP", par="title")
        arb = pol.politique_liens_deezer(cas, t, revue.contexte([t, mmlp, mmlp2], {}))
        assert arb.action == "normal" and "#14234" in arb.raison

    def test_meme_disque_juge_comme_avant(self):
        t = _t("Paul (Skit)", 14323, album="The Slim Shady LP", isrc="X2")
        cas = self._cas(t, "Paul (Skit)", 15, "The Slim Shady LP (Expanded Edition)", "x2")
        assert pol.politique_liens_deezer(cas, t, revue.contexte([t], {})).sens == "rattacher"

    def test_generique_sans_album_de_la_fiche_reste_a_l_humain(self):
        t = _t("Paul (Skit)", 1, isrc="X3")
        cas = self._cas(t, "Paul (Skit)", 19, "Relapse", "x3")
        assert pol.politique_liens_deezer(cas, t, revue.contexte([t], {})) is None


def _ctx_mesures(track, champ, valeurs):
    from src.enrichment.observation import Observation

    obs = [Observation(champ, v, s) for s, v in valeurs.items()]
    return revue.contexte([track], {track.id: obs})


class TestAudio:
    def test_songbpm_seul_contre_reccobeats(self):
        t = _t()
        t.audio.bpm = 135
        ctx = _ctx_mesures(t, "bpm", {"reccobeats": 135, "songbpm": 102})
        assert pol.politique_bpm(_cas("bpm", t), t, ctx).action == "retirer_songbpm"

    def test_page_songbpm_a_la_duree_des_catalogues_est_le_meme_morceau(self):
        """Booba « Kayna (Remix) » : SongBPM 102 contre ReccoBeats 135, mais sa
        page dure 179 s comme Deezer et Apple — même enregistrement, deux
        mesures du tempo. Rien ne se retire (2026-10-26)."""
        t = _t(durations_observees={"deezer": 178, "apple_music": 179, "songbpm": 179})
        t.audio.bpm = 135
        ctx = _ctx_mesures(t, "bpm", {"reccobeats": 135, "songbpm": 102})
        assert pol.politique_bpm(_cas("bpm", t), t, ctx).action == "normal"
        t.durations_observees = {"deezer": 178, "songbpm": 240}  # une autre page
        assert pol.politique_bpm(_cas("bpm", t), t, ctx).action == "retirer_songbpm"

    def test_deux_voix_independantes_d_accord_restent_a_l_humain(self):
        """Booba « Futur » : Deezer ET GetSongBPM à 133 contre ReccoBeats 89."""
        t = _t()
        t.audio.bpm = 89
        ctx = _ctx_mesures(
            t, "bpm", {"reccobeats": 89, "songbpm": 89, "deezer": 133, "getsongbpm": 133}
        )
        assert pol.politique_bpm(_cas("bpm", t), t, ctx) is None

    def test_tonalite_songbpm_seul_contre(self):
        t = _t()
        t.audio.key, t.audio.mode = 7, 1
        cas = _cas("tonalite", t, {"tonalites": {"reccobeats": (7, 1), "songbpm": (7, 0)}})
        assert pol.politique_tonalite(cas, t, None).action == "normal"


def test_lrc_d_une_autre_prise():
    """« R.O.C. Remix » (336 s) au LRC du « Jeezy Remix », qui s'arrête à 240 s :
    celui d'une prise plus courte. Sans durée, rien ne dit à qui il est."""
    t = _t("Can't Tell Me Nothing (R.O.C. Remix)", 1, duration=336)
    t.lyrics.synced = "[00:01.00] x\n[04:00.00] y"
    autre = _t("Can't Tell Me Nothing (Jeezy Remix)", 2)
    cas = _cas("doublon_lrc", t, {"autre": 2, "bigrammes_autre": 0.91})
    assert pol.politique_lrc(cas, t, revue.contexte([t, autre])).action == "retirer_lrc"
    t.duration = None
    assert pol.politique_lrc(cas, t, revue.contexte([t, autre])) is None


def test_lrc_aux_paroles_proches_mais_a_sa_place_reste():
    """« Love Galore (Version 1) » : 96 % des mots de l'original, mais un LRC
    qui va jusqu'à 250 s sur 264 s — à sa place (2026-10-26)."""
    t = _t("Love Galore (Version 2)", 1, duration=264)
    t.lyrics.synced = "[00:01.00] x\n[04:10.00] y"
    cas = _cas("lrc_douteux", t, {"bigrammes_autre": 0.96})
    assert pol.politique_lrc(cas, t, revue.contexte([t])) is None


class TestLrcDeLAutreFiche:
    """Booba « Gyrophare* » (extrait, sans paroles ni catalogue) portait le LRC
    YouTube Music de « Garcimore » ; simulée sur la base : 16 / 19 (2026-10-31)."""

    @staticmethod
    def _paire(fin_autre=165, **kw):
        t = _t("Gyrophare", 1, **kw)
        t.lyrics.synced = "[00:30.10] Tous les jours\n[02:40.00] fin"
        autre = _t("Garcimore", 2, duration=182)
        autre.lyrics.synced = (
            f"[00:32.09] Tous les jours\n[{fin_autre // 60:02d}:{fin_autre % 60:02d}.00] fin"
        )
        cas = _cas(
            "paroles_autre_fiche", t, {"autre": 2, "part": 0.86, "textes": ["LRC YouTube Music"]}
        )
        return t, cas, revue.contexte([t, autre])

    def test_le_lrc_de_l_autre_se_retire(self):
        t, cas, ctx = self._paire()
        assert pol.arbitrage_etendu(cas, t, ctx).action == "retirer_lrc"

    def test_sans_temoin_ou_fiche_publiee_l_humain(self):
        # Le LRC de l'autre finit ailleurs : un couplet cité, peut-être.
        t, cas, ctx = self._paire(fin_autre=230)
        assert pol.politique_paroles_autre_fiche(cas, t, ctx) is None
        # Une fiche qui a sa durée ou un catalogue : son LRC peut être le sien.
        for kw in ({"duration": 160}, {"spotify_id": "SP"}, {"deezer_id": 7}):
            t, cas, ctx = self._paire(**kw)
            assert pol.politique_paroles_autre_fiche(cas, t, ctx) is None, kw

    def test_des_paroles_de_repli_restent_a_l_humain(self):
        t, cas, ctx = self._paire()
        cas.preuves["textes"] = ["LRC YouTube Music", "paroles Source: Musixmatch"]
        assert pol.politique_paroles_autre_fiche(cas, t, ctx) is None


_EMISSION = (
    "Je rappe depuis la cave avec les frères du quartier on garde la flamme\n"
    "Les micros chauffent la nuit tombe sur la ville et personne ne recule\n"
    "Le beat tourne encore on passe le relais au suivant sans jamais trembler\n"
    "Mon couplet sort du cœur les mots tapent fort comme la pluie sur le béton"
)


def _session(lrc, duree=1800):
    t = _t("Grünt #59", 1, duration=duree)
    t.lyrics.text, t.lyrics.source = _EMISSION, "genius"
    t.lyrics.synced = lrc
    return t, _cas("lrc_live", t, {"recouvrement": 0.47})


def test_lrc_live_etranger_a_la_page_et_court_se_retire():
    """Grünt : un LRC YouTube Music de 2 min pour une émission de 30 min, dont
    les phrases ne sont pas dans la page (2026-10-29)."""
    lrc = (
        "[00:01.00] Tu sais que je reste vrai la ville dort encore\n"
        "[00:40.00] Elle danse sous les néons et moi je compte les heures\n"
        "[01:50.00] Le quartier attend la pluie personne ne parle de demain"
    )
    t, cas = _session(lrc)
    assert pol.arbitrage_etendu(cas, t, revue.contexte([t])).action == "retirer_lrc"
    # Sans durée, ou presque à la durée : rien ne prouve qu'il n'est pas d'elle.
    t.duration = None
    assert pol.politique_lrc(cas, t, revue.contexte([t])) is None
    t.duration = 120
    assert pol.politique_lrc(cas, t, revue.contexte([t])) is None


def test_lrc_live_d_un_couplet_de_la_page_reste():
    """Un couplet synchronisé de l'émission : ses phrases sont dans la page."""
    lrc = (
        "[00:01.00] Je rappe depuis la cave avec les frères du quartier\n"
        "[00:40.00] Les micros chauffent la nuit tombe sur la ville\n"
        "[01:50.00] Mon couplet sort du cœur les mots tapent fort"
    )
    t, cas = _session(lrc)
    assert pol.politique_lrc(cas, t, revue.contexte([t])) is None


class TestEcartsDeezer:
    def _cas(self, detecteur, titre, artist_id=7, isrc=None, piste_id=1):
        ecart = {
            "piste": {"id": piste_id, "title": titre, "isrc": isrc},
            "album": {"artist_id": artist_id, "artist_name": "Jack Many", "title": "D"},
        }
        return _cas(detecteur, None, {"ecart": ecart})

    def test_signalement_que_le_classement_ne_produit_plus(self):
        juger = pol.politique_ecarts_deezer({}, {7}, rejoues=set())
        assert juger(self._cas("deezer_absent", "MJ"), None, None).action == "run"

    def test_disque_d_un_autre_reste_a_l_humain(self):
        juger = pol.politique_ecarts_deezer({}, {7}, rejoues={("absent", 1)})
        assert juger(self._cas("deezer_absent", "VELOUR", artist_id=9), None, None) is None

    def test_isrc_deja_en_base_et_disque_propre(self):
        juger = pol.politique_ecarts_deezer({"FR1": "Strass et paillettes"}, {7})
        arb = juger(self._cas("deezer_absent", "Strass & paillettes", isrc="fr1"), None, None)
        assert "Strass et paillettes" in arb.sens
        arb = juger(self._cas("deezer_absent", "Crazy + 6G"), None, None)
        assert arb.action == "deezer_appliquer"


class TestRegleA1:
    """A1 (décision utilisateur 2026-10-31) : l'édition de SUPPORT d'une fiche
    lui est rattachée ; la version Deezer d'un autre enregistrement qu'aucune
    fiche ne porte devient une fiche secondaire (geste de DA1)."""

    def _socle(self, titre="The King and I", tid=1, isrc="USUG12105254", **kw):
        kw.setdefault("durations_observees", {"deezer": 193})
        kw.setdefault("deezer_id", 100)
        return _t(titre, tid, isrc=isrc, duration=193, album="Curtain Call 2", **kw)

    def _juger(self, detecteur, titre, fiches, isrc=None, duree=193, socle_id=1, genius=None):
        ecart = {
            "piste": {"id": 9, "title": titre, "isrc": isrc, "duration": duree},
            "album": {"id": 5, "title": "D", "artist_id": 7, "artist_name": "Eminem"},
            "socle_id": socle_id,
            "genius": genius,
        }
        cas = _cas(detecteur, None, {"ecart": ecart})
        return pol.arbitrage_deezer(cas, None, SimpleNamespace(disco=list(fiches)), {}, {7})

    def test_meme_isrc_et_duree_lue_par_id_rattache(self):
        """Eminem, « The King and I (From the Original Motion Picture
        Soundtrack ELVIS) » : le single de la bande originale."""
        titre = "The King and I (From the Original Motion Picture Soundtrack ELVIS)"
        arb = self._juger("deezer_album_absent", titre, [self._socle()], isrc="usug12105254")
        assert arb.action == "deezer_rattacher_edition" and "ISRC" in arb.raison

    def test_sans_duree_lue_par_id_l_humain(self):
        socle = self._socle(durations_observees={"legacy": 193, "youtube": 193})
        titre = "The King and I (From the Original Motion Picture Soundtrack ELVIS)"
        assert self._juger("deezer_album_absent", titre, [socle], isrc="USUG12105254") is None

    def test_la_duree_de_la_meme_page_deezer_n_est_pas_un_temoin(self):
        """La durée Deezer d'une fiche sans page Deezer à elle (ou de LA page
        jugée) a pu être trouvée au titre : pas un témoin."""
        titre = "The King and I (From the Original Motion Picture Soundtrack ELVIS)"
        for deezer_id in (None, 9):
            socle = self._socle(deezer_id=deezer_id)
            assert self._juger("deezer_album_absent", titre, [socle], isrc="USUG12105254") is None

    def test_isrc_sur_deux_fiches_l_humain(self):
        fiches = [self._socle(), self._socle("The King and I (Remix)", tid=2)]
        titre = "The King and I (From the Original Motion Picture Soundtrack ELVIS)"
        assert self._juger("deezer_album_absent", titre, fiches, isrc="USUG12105254") is None

    def test_sans_isrc_le_titre_de_base_et_la_duree(self):
        """Deux fiches « Shake That » : la durée lue par ID désigne la bonne."""
        a = self._socle("Shake That", isrc="X1", durations_observees={"spotify_web": 275})
        b = _t("Shake That", 2)
        arb = self._juger("deezer_version", "Shake That (Radio Edit)", [a, b], isrc="X2", duree=274)
        assert arb.action == "deezer_rattacher_edition" and "#1" in arb.raison

    def test_une_version_n_a_pas_de_titre_de_base(self):
        socle = self._socle("Shake That", isrc="X1", durations_observees={"spotify_web": 275})
        arb = self._juger("deezer_version", "Shake That (Live)", [socle], isrc="X2", duree=275)
        assert arb is None or arb.action != "deezer_rattacher_edition"

    def test_live_qu_aucune_fiche_ne_porte_devient_fiche_secondaire(self):
        arb = self._juger("deezer_version", "The King and I (Live)", [self._socle()], duree=240)
        assert arb.action == "deezer_appliquer" and "secondaire" in arb.sens

    def test_version_deja_portee_l_humain(self):
        """Par l'ISRC (« The Way I Am (Danny Lohner Remix Version) », porté par
        la fiche « … (Danny Lohner Remix) »), ou par le titre."""
        remix = _t("The King and I (Live)", 2, isrc="USIR1", duration=240)
        fiches = [self._socle(), remix]
        assert self._juger("deezer_version", "The King and I (Live)", fiches, duree=240) is None
        isrc = self._juger("deezer_version", "The King and I (Live 2005)", fiches, isrc="USIR1")
        assert isrc is None or isrc.action != "deezer_appliquer"

    def test_le_live_d_un_autre_morceau_ne_porte_pas_la_version(self):
        """The Game (2026-11-03) : « Crack Music (Live at Abbey Road Studios) »
        bloquait « Dreams (Live from Dublin Version) » — même VERSION, pas le
        même morceau."""
        autre = _t("Lose Yourself (Live)", 3, duration=300)
        arb = self._juger(
            "deezer_version", "The King and I (Live)", [self._socle(), autre], duree=240
        )
        assert arb.action == "deezer_appliquer" and "secondaire" in arb.sens

    def test_version_a_la_duree_du_socle_l_humain(self):
        assert self._juger("deezer_version", "The King and I (Live)", [self._socle()]) is None

    def test_edition_d_une_version_pas_de_fiche(self):
        """« Shake That (Remix) (Album Version Explicit) » : l'édition d'un
        remix, jamais une nouvelle fiche."""
        titre = "The King and I (Remix) (Album Version Explicit)"
        assert self._juger("deezer_version", titre, [self._socle()], duree=240) is None

    def test_sans_socle_ou_avec_page_genius_l_humain(self):
        socle = self._socle()
        titre = "The King and I (Live)"
        assert self._juger("deezer_version", titre, [socle], duree=240, socle_id=None) is None
        page = self._juger("deezer_version", titre, [socle], duree=240, genius={"id": 1})
        assert page is None

    def test_instrumental_reste_sans_fiche(self):
        arb = self._juger("deezer_version", "The King and I (Instrumental)", [self._socle()])
        assert arb.action == "normal"

    def test_piste_nue_d_un_disque_live_l_humain(self, monkeypatch):
        """MC Solaar, *Le tour de la question* : Deezer omet « (Live) » ; la
        règle des pistes absentes aurait créé ou rattaché « Bouge de là » au
        titre de la fiche studio."""
        from src.utils import deezer_identity

        def juger(album):
            ecart = {
                "piste": {"id": 9, "title": "Bouge de là", "isrc": "FR1", "duration": 186},
                "album": {"id": 43852, "title": album, "artist_id": 7, "artist_name": "MC"},
            }
            cas = _cas("deezer_absent", None, {"ecart": ecart})
            return pol.arbitrage_deezer(cas, None, SimpleNamespace(disco=[]), {}, {7})

        assert juger("Le tour de la question").action == "deezer_appliquer"
        assert juger("Le tour de la question (Live)") is None
        monkeypatch.setattr(deezer_identity, "fournisseur_albums_live", lambda: {43852})
        assert juger("Le tour de la question") is None

    def test_regles_listees(self):
        for regle in (
            ("deezer_version", "deezer_rattacher_edition"),
            ("deezer_album_absent", "deezer_rattacher_edition"),
            ("deezer_version", "deezer_appliquer"),
        ):
            assert regle in pol.REGLES_DEEZER


def test_kworb_seules_les_voies_qui_ne_creent_rien():
    rendition = _cas("kworb_proposition", None, {"proposition": "rendition", "parent_track_id": 3})
    tiers = _cas("kworb_proposition", None, {"proposition": "tiers"})
    assert pol.politique_kworb(rendition, None, None).action == "kworb_rendition"
    assert pol.politique_kworb(tiers, None, None) is None


def test_id_propose_gate_et_duree_independante():
    t = _t(durations_observees={"deezer": 203, "apple_music": 203})
    cas = _cas("id_spotify_propose", t, {"spotify_id_propose": "SID"})
    lire = lambda _sid: {"name": "Gold Digger", "duration": 204}  # noqa: E731
    assert pol.politique_id_propose(lire, lambda *_: True)(cas, t, None).action == "poser_id"
    assert pol.politique_id_propose(lire, lambda *_: False)(cas, t, None).action == "refuser_id"
    jamais_lue = pol.politique_id_propose(lambda _sid: None, lambda *_: True)
    assert jamais_lue(cas, t, None) is None
    t.spotify_id = "AUTRE"  # une fiche à un autre ID n'est jamais tranchée
    assert pol.politique_id_propose(lire, lambda *_: True)(cas, t, None) is None


def test_id_partage_le_gate_designe_la_fiche():
    """Kanye : l'ID de « I CAN'T WAIT » (Deluxe) posé aussi sur « Wait », un
    autre morceau. Seule la fiche que le gate accepte garde l'ID."""
    juste = _t("I CAN'T WAIT", 1, spotify_id="SID", spotify_id_entries=[])
    autre = _t("Wait", 2, spotify_id="SID", spotify_id_entries=[])
    ctx = SimpleNamespace(disco=[juste, autre])
    juger = pol.politique_id_partage(lambda t, _sid: t is juste)
    cas_autre = _cas("kworb_id_partage", autre, {"spotify_id": "SID"})
    cas_juste = _cas("kworb_id_partage", juste, {"spotify_id": "SID"})
    assert juger(cas_autre, autre, ctx).action == "retirer_spotify"
    assert juger(cas_juste, juste, ctx).action == "normal"
    # Les deux passent, ou aucune : l'humain.
    assert pol.politique_id_partage(lambda *_: True)(cas_autre, autre, ctx) is None
    assert pol.politique_id_partage(lambda *_: False)(cas_autre, autre, ctx) is None
    # Une version parmi les porteuses : l'humain (décision 2026-10-20).
    autre.title = "Wait (Remix)"
    assert juger(cas_autre, autre, ctx) is None


class TestCatalogueDesigne:
    """« Même ID de catalogue, deux pages » (2026-10-27) : ce que sert le
    catalogue pour l'ID désigne la page qui le garde."""

    def _famous(self):
        album = _t("Famous", 1, deezer_id=126772735, genius_id=10, album="The Life of Pablo")
        autre = _t("Famous", 2, deezer_id=126772735, genius_id=11)
        return album, autre, revue.contexte([album, autre])

    _FICHE = {
        "title": "Famous",
        "artist": {"name": "Kanye West"},
        "album": {"title": "The Life Of Pablo"},
    }

    def test_la_page_designee_garde_l_id_l_autre_le_perd(self):
        album, autre, ctx = self._famous()
        lectures = []

        def lire_deezer(did):
            lectures.append(did)
            return self._FICHE

        juger = pol.politique_catalogue_designe(lambda _sid: None, lire_deezer)
        preuves = {"deezer_id": 126772735}
        a = juger(_cas("id_catalogue_partage", album, {**preuves, "autres": [2]}), album, ctx)
        b = juger(_cas("id_catalogue_partage", autre, {**preuves, "autres": [1]}), autre, ctx)
        assert (a.action, b.action) == ("normal", "delier_deezer")
        assert "par album" in a.raison and "#1" in b.raison
        assert lectures == [126772735]  # une lecture par ID, pas par porteuse

    def test_spotify_retire_l_id(self):
        drake = _t("Forever", 1, spotify_id="SP", genius_id=10, primary_artist_name="Drake")
        wayne = _t("Forever", 2, spotify_id="SP", genius_id=11, primary_artist_name="Lil Wayne")
        ctx = revue.contexte([drake, wayne])
        juger = pol.politique_catalogue_designe(
            lambda _sid: {"name": "Forever", "artists": ["Drake", "Kanye West"]}, lambda _d: None
        )
        cas = _cas("id_catalogue_partage", wayne, {"spotify_id": "SP", "autres": [1]})
        assert juger(cas, wayne, ctx).action == "retirer_spotify"
        # Par l'arbitrage branché : même sens ; illisible, rien.
        assert pol.arbitrage_catalogue(cas, wayne, ctx).action == "retirer_spotify"  # mémo du ctx
        frais = revue.contexte([drake, wayne])
        assert pol.arbitrage_catalogue(cas, wayne, frais, lambda _s: None, lambda _d: None) is None

    def test_rien_de_servi_ou_indecis_l_humain(self):
        album, autre, ctx = self._famous()
        cas = _cas("id_catalogue_partage", autre, {"deezer_id": 126772735, "autres": [1]})
        assert pol.politique_catalogue_designe(None, lambda _d: None)(cas, autre, ctx) is None
        autre.album = "The Life of Pablo"
        juger = pol.politique_catalogue_designe(None, lambda _d: self._FICHE)
        assert juger(cas, autre, revue.contexte([album, autre])) is None

    def test_une_fiche_qui_n_a_plus_l_id_n_est_pas_jugee(self):
        album, autre, ctx = self._famous()
        cas = _cas("id_catalogue_partage", autre, {"deezer_id": 126772735, "autres": [1]})
        autre.deezer_id = None
        assert (
            pol.politique_catalogue_designe(None, lambda _d: self._FICHE)(cas, autre, ctx) is None
        )


def test_kworb_remix_par_un_tiers_que_spotify_confirme():
    """« One Man Can Change The World - Rudimental Remix » : remixeur nommé,
    Kanye crédité par l'en-tête Spotify — fiche en rôle secondaire. Un remix
    sans remixeur (« 2021 Remix Extended ») ou un artiste absent des crédits
    Spotify : l'humain."""
    t = _t("One Man Can Change the World", 1)
    t.artist = Artist(name="Kanye West")
    ctx = SimpleNamespace(disco=[t])
    preuves = {
        "proposition": "tiers",
        "kind": "remix_named",
        "remixer": "Rudimental",
        "credited": ["Big Sean", "Kanye West", "John Legend", "Rudimental"],
    }
    cas = _cas("kworb_proposition", None, preuves)
    assert pol.arbitrage_etendu(cas, None, ctx).action == "kworb_tiers"
    assert (
        pol.arbitrage_etendu(
            _cas("kworb_proposition", None, {**preuves, "kind": "remix_bare", "remixer": None}),
            None,
            ctx,
        )
        is None
    )
    assert (
        pol.arbitrage_etendu(
            _cas("kworb_proposition", None, {**preuves, "credited": ["Rudimental"]}), None, ctx
        )
        is None
    )


def test_kworb_ligne_rattachee_par_le_rejeu_d_aujourd_hui():
    cas = _cas("kworb_ecartee", None, {"titre_kworb": "Slow Jamz"})
    rejoue = {"Slow Jamz": {"fiche": {"id": 1, "titre": "Slow Jamz"}, "suggestion": False}}
    assert pol.politique_kworb(cas, None, None, rejoues=rejoue).action == "run"
    rejoue["Slow Jamz"]["suggestion"] = True  # toujours une proposition : pas périmé
    assert pol.politique_kworb(cas, None, None, rejoues=rejoue) is None


def test_alias_confirme_par_deux_sources_ou_par_le_type_musicbrainz():
    def rel(nom, source, detail=None, kind="alias"):
        return SimpleNamespace(
            related_name=nom, kind=kind, source=source, detail=detail, formation=None
        )

    ctx = revue.contexte(
        [],
        relations_proposees=[
            rel("Yeezy", "discogs+musicbrainz", "Artist name"),
            rel("Chen Zen", "musicbrainz", "Artist name"),
            rel("Global", "discogs"),
            rel("Go Getters", "discogs", kind="member_of"),
        ],
    )

    def juger(nom, kind="alias"):
        return pol.politique_alias(
            _cas("liens_proposes", None, {"nom": nom, "kind": kind}), None, ctx
        )

    assert juger("Yeezy").action == "confirmer_lien"
    assert juger("Chen Zen").action == "confirmer_lien"
    assert juger("Global") is None  # page Discogs sans type : l'humain
    assert juger("Go Getters", "member_of") is None  # une formation : jamais


def test_les_regles_validees_sont_gelees():
    """Une règle n'entre ici qu'après la validation de l'utilisateur (2026-10-20)."""
    assert {
        ("duree_songbpm", "retirer_songbpm"),
        ("duree_spotify", "retirer_spotify"),
        ("duree_spotify", "normal"),
        ("duree_isolee", "normal"),
    } == pol.REGLES_VALIDEES


def test_un_retrait_exige_trois_catalogues():
    """Validé sur la variante à 3 : à 2 catalogues contre, l'ID reste."""
    t = _t(
        spotify_id="SP",
        durations_observees={"spotify_web": 202, "reccobeats": 202, "deezer": 224, "youtube": 225},
    )
    assert pol.arbitrage_valide(_cas("duree_spotify", t), t, None) is None
    t.durations_observees["apple_music"] = 224
    assert pol.arbitrage_valide(_cas("duree_spotify", t), t, None).action == "retirer_spotify"


def test_generique_retirer_songbpm_n_est_pas_valide():
    """Kanye « Skit #3 » : l'utilisateur vérifie lui-même."""
    t = _t("Skit #3", duration=31, durations_observees={"songbpm": 31})
    assert pol.politique_durees(3)(_cas("generique_duree", t), t, None).action == "retirer_songbpm"
    assert pol.arbitrage_valide(_cas("generique_duree", t), t, None) is None


def test_les_regles_ne_touchent_ni_versions_ni_generiques():
    """« On garde les cas ambigus […] à la main, intro outro interlude remix
    live » (2026-10-20) : Travis « Niagara Falls […] [ChoppedNotSlopped] »
    perdait son SongBPM au passage à blanc. Les règles VALIDÉES restent aux
    originaux ; depuis le 2026-10-29, une version démentie par 3 catalogues
    passe par `politique_songbpm_version` (`TestSongbpmVersion`)."""
    durees = {"songbpm": 207, "deezer": 252, "spotify_web": 252, "youtube": 253}
    original = _t("Niagara Falls", durations_observees=dict(durees))
    version = _t("Niagara Falls [ChoppedNotSlopped]", durations_observees=dict(durees))
    assert pol.arbitrage_valide(_cas("duree_songbpm", original), original, None).action == (
        "retirer_songbpm"
    )
    assert pol.arbitrage_valide(_cas("duree_songbpm", version), version, None) is None


@pytest.mark.parametrize(
    "titre, original",
    [
        ("Chaos", True),
        ("I Get It", True),
        ("Nuit (Interlude)", False),
        ("Niagara Falls (Foot or 2) [ChoppedNotSlopped]", False),
        ("Intro", False),
        ("Dans ma bulle - Live 2006", False),
        ("Kayna (Remix)", False),
        ("Freestyle CKO", False),
    ],
)
def test_titre_d_original(titre, original):
    assert pol.titre_d_original(titre) is original


def test_id_propose_titre_servi_et_clip():
    """« Heartless - The Voice Performance » passait le gate ; une durée de CLIP
    ne confirme rien (2026-10-20)."""
    t = _t("Heartless", durations_observees={"deezer": 208, "apple_music": 208})
    cas = _cas("id_spotify_propose", t, {"spotify_id_propose": "SID"})

    def juger(nom, durees=None):
        if durees is not None:
            t.durations_observees = durees
        return pol.arbitrage_id_propose(
            cas, t, None, lire=lambda _s: {"name": nom, "duration": 208}, gate=lambda *_: True
        )

    assert juger("Heartless").action == "poser_id"
    assert juger("Heartless - The Voice Performance") is None
    assert juger("Heartless", {"youtube_video": 208}) is None


def test_id_propose_un_refus_de_duree_dementi_par_les_catalogues_va_a_l_humain():
    """Kanye « Comfy » : le gate refusait (212 s contre 206 s en colonne,
    Deezer) alors que YTM et ReccoBeats confirment Spotify."""
    t = _t("Comfy", durations_observees={"deezer": 206, "reccobeats": 211, "ytmusic": 212})
    cas = _cas("id_spotify_propose", t, {"spotify_id_propose": "SID"})
    lire = lambda _s: {"name": "Comfy", "duration": 212}  # noqa: E731
    assert pol.arbitrage_id_propose(cas, t, None, lire=lire, gate=lambda *_: False) is None
    t.durations_observees = {"songbpm": 94}  # rien ne confirme : le refus tient
    arb = pol.arbitrage_id_propose(cas, t, None, lire=lire, gate=lambda *_: False)
    assert arb.action == "refuser_id"


class TestIdProposeVersions:
    """2026-10-31 : une VERSION passe par le gate comme à l'étape Identité. Ce
    qu'il refuse est refusé par règle ; une pose exige le MÊME titre (version
    comprise) et une durée lue par un autre identifiant. Cas réels de la base."""

    @staticmethod
    def _juger(titre, durees, servi, duree_servie, gate=True, album=None):
        t = _t(titre, album=album, durations_observees=durees)
        cas = _cas("id_spotify_propose", t, {"spotify_id_propose": "SID"})
        return pol.arbitrage_id_propose(
            cas,
            t,
            None,
            lire=lambda _s: {"name": servi, "duration": duree_servie},
            gate=lambda *_: gate,
        )

    def test_meme_version_et_catalogue_qui_confirme_pose(self):
        # Kanye « Testify (Instrumental) » (Common) : Deezer 157, audio 159.
        arb = self._juger(
            "Testify (Instrumental)", {"deezer": 157, "youtube": 159}, "Testify - Instrumental", 159
        )
        assert arb.action == "poser_id"
        # Kanye « Freestyle » (Iggy Azalea) : Deezer 152 lu par son ID.
        arb = self._juger("Freestyle", {"deezer": 152, "youtube": 153}, "Freestyle", 152)
        assert arb.action == "poser_id"

    def test_meme_version_sans_catalogue_l_humain(self):
        # Kanye « Heartless » (The Voice) : SongBPM et une vidéo seulement.
        arb = self._juger("Freestyle", {"songbpm": 152, "youtube_video": 152}, "Freestyle", 152)
        assert arb is None

    def test_un_autre_remix_n_est_jamais_pose(self):
        # Booba « Hustlin' (French Remix) » : Spotify sert le remix de JAY-Z et
        # Jeezy, YTM a trouvé le même au titre — l'humain.
        arb = self._juger(
            "Hustlin’ (French Remix)", {"songbpm": 284, "ytmusic": 285}, "Hustlin' - Remix", 284
        )
        assert arb is None

    def test_le_gate_refuse_le_freestyle(self):
        # Booba « Boulbi (Freestyle Cut Killer Show) » : Spotify sert « Boulbi »,
        # titre d'une autre fiche ; l'extrait officiel dure 86 s.
        arb = self._juger(
            "Boulbi (Freestyle Cut Killer Show)",
            {"songbpm": 243, "youtube_officiel": 86},
            "Boulbi",
            243,
            gate=False,
        )
        assert arb.action == "refuser_id"

    def test_un_refus_sur_le_seul_nom_que_ytm_dement_va_a_l_humain(self):
        # Booba « Liberation Time (Remix) » : « - Hip Hop RMX », 239 s comme YTM.
        arb = self._juger(
            "Liberation Time (Remix)",
            {"songbpm": 239, "ytmusic": 239},
            "Liberation Time - Hip Hop RMX",
            239,
            gate=False,
        )
        assert arb is None

    def test_un_titre_generique_sans_le_meme_disque_se_refuse(self):
        # Népal « Intro » (AVS) : le disque Spotify n'est pas montré le même.
        arb = self._juger("Intro", {"youtube": 141}, "Intro", 141, gate=False, album="AVS")
        assert arb.action == "refuser_id"


def test_lien_deezer_dementi_par_des_sources_fragiles_seules_est_garde():
    """Kanye « I CAN'T WAIT » : Deezer 137 s, seule la colonne SongBPM (127 s)
    le contredit — le piège de « Comfy » : on garde le lien."""
    t = _t("I CAN’T WAIT", durations_observees={"songbpm": 127})
    ecart = {"piste": {"title": "I CAN'T WAIT", "duration": 137}}
    cas = _cas("deezer_link_review", t, {"ecart": ecart, "piste_deezer": 5})
    assert pol.arbitrage_liens_deezer(cas, t, None).action == "normal"
    # Trois catalogues solides d'accord contre, plus de 10 s : on délie.
    t.durations_observees = {"spotify_web": 110, "youtube": 110, "apple_music": 111}
    assert pol.arbitrage_liens_deezer(cas, t, None).action == "delier_deezer"


@pytest.mark.parametrize(
    "titre, piste, durees, editions",
    [
        # MC Solaar, « Le tour de la question » (live) : la page du studio.
        ("Onzieme commandement", 176, {"apple_music": 202, "spotify_web": 202, "youtube": 203}, ()),
        # Kid Cudi, « A Kid Named Cudi ».
        ("The Prayer", 200, {"apple_music": 219, "spotify_web": 219, "youtube": 220}, ()),
    ],
)
def test_liens_a_revoir_delies_a_la_simulation(titre, piste, durees, editions):
    """Passage à blanc du 2026-10-29 : les liens que la famille délie, le run
    les a depuis remplacés par une page à la durée des catalogues (témoin)."""
    t = _t(titre, durations_observees=durees)
    t.durees_editions = editions
    ecart = {"piste": {"title": titre, "duration": piste}}
    cas = _cas("deezer_link_review", t, {"ecart": ecart, "piste_deezer": 5})
    assert pol.arbitrage_liens_deezer(cas, t, None).action == "delier_deezer"


@pytest.mark.parametrize(
    "titre, durees, editions",
    [
        # Kanye « Ghetto » : 3 contre 3 (Deezer, l'audio et YTM d'un côté).
        (
            "Ghetto",
            {
                "deezer": 204,
                "youtube": 205,
                "ytmusic": 205,
                "apple_music": 220,
                "spotify_web": 219,
                "songbpm": 220,
            },
            (),
        ),
        # Kid Cudi « Take 'Em High » : YTM soutient Deezer.
        (
            "Take ’Em High",
            {"deezer": 207, "ytmusic": 208, "apple_music": 179, "spotify_web": 179, "songbpm": 179},
            (),
        ),
        # Flynt « Rap théorie » : l'édition Deezer (mixtape, 198 s) explique les
        # témoins ; deux catalogues solides seulement.
        (
            "Rap théorie",
            {
                "deezer": 262,
                "legacy": 262,
                "discogs": 198,
                "spotify_web": 197,
                "youtube": 198,
                "ytmusic": 198,
            },
            (198,),
        ),
        # Kanye « Plentiful » : la métadonnée Deezer est fausse, la lecture va
        # au bout (verdict humain 2026-10-29) — 7 s, sous le seuil d'un retrait,
        # même devant trois catalogues.
        ("Plentiful", {"deezer": 181, "spotify_web": 188, "youtube": 189, "apple_music": 188}, ()),
    ],
)
def test_durees_deezer_laissees_a_l_humain_a_la_simulation(titre, durees, editions):
    """Passage à blanc du 2026-10-29 : les cas `duree_deezer` actifs de la base,
    aucun n'est délié — pas trois catalogues solides d'accord contre Deezer à
    plus de 10 s."""
    t = _t(titre, durations_observees=durees)
    t.durees_editions = editions
    cas = _cas("duree_deezer", t, {"deezer_id": 5})
    assert pol.arbitrage_liens_deezer(cas, t, None) is None


class TestIdSpotifyVersion:
    """« ID Spotify d'une autre version » (2026-10-29) : la version que la page
    nomme a sa propre fiche, à une autre durée, et Deezer confirme celle de
    l'ID — ✓ Normal ; deux versions à la même durée restent à l'humain."""

    def _cas_kpop(self, duree_original=185, deezer=273, id_original="ORIG"):
        cs = _t(
            "K-POP (Chopped & Screwed)",
            1,
            spotify_id="CS",
            spotify_ids=["CS"],
            durations_observees={"spotify_web": 273, "deezer": deezer},
        )
        original = _t("K-POP", 2, spotify_id=id_original, duration=duree_original)
        cas = _cas("id_spotify_version", cs, {"spotify_id": "CS", "spotify_sert": "K-POP"})
        return cas, cs, SimpleNamespace(disco=[cs, original])

    def test_la_version_nommee_a_sa_fiche_d_une_autre_duree(self):
        cas, cs, ctx = self._cas_kpop()
        arbitrage = pol.arbitrage_etendu(cas, cs, ctx)
        assert arbitrage.action == "normal" and "deezer 273" in arbitrage.raison

    def test_meme_duree_l_humain(self):
        """« MELTDOWN (Instrumental) » : l'instrumental dure autant."""
        cas, cs, ctx = self._cas_kpop(duree_original=270)
        assert pol.arbitrage_etendu(cas, cs, ctx) is None

    def test_sans_catalogue_qui_confirme_l_humain(self):
        cas, cs, ctx = self._cas_kpop(deezer=185)
        assert pol.arbitrage_etendu(cas, cs, ctx) is None

    def test_meme_id_ou_sans_fiche_l_humain(self):
        cas, cs, ctx = self._cas_kpop(id_original="CS")
        assert pol.arbitrage_etendu(cas, cs, ctx) is None
        assert pol.arbitrage_etendu(cas, cs, SimpleNamespace(disco=[cs])) is None

    def test_le_feat_du_titre_servi_ne_compte_pas(self):
        cas, cs, ctx = self._cas_kpop()
        cas.preuves["spotify_sert"] = "K-POP (feat. Bad Bunny & The Weeknd)"
        assert pol.arbitrage_etendu(cas, cs, ctx).action == "normal"

    def _franchise(self, original_durees):
        remix = _t(
            "FRANCHISE (REMIX)",
            4791,
            spotify_id="REMIX",
            spotify_ids=["REMIX"],
            durations_observees={"spotify_web": 207, "deezer": 207, "youtube": 208},
        )
        original = _t(
            "FRANCHISE",
            4794,
            spotify_id="ORIG",
            duration=202,
            durations_observees=original_durees,
        )
        sert = "FRANCHISE (feat. Young Thug & M.I.A.)"
        cas = _cas("id_spotify_version", remix, {"spotify_id": "REMIX", "spotify_sert": sert})
        return cas, remix, SimpleNamespace(disco=[remix, original])

    def test_ecart_court_mais_duree_de_l_original_confirmee(self):
        """Travis « FRANCHISE (REMIX) » (2026-10-31) : 5 s d'écart seulement,
        mais l'original est à 202 s chez Deezer, Spotify et ReccoBeats."""
        cas, remix, ctx = self._franchise({"deezer": 202, "spotify_web": 202, "reccobeats": 202})
        assert pol.arbitrage_etendu(cas, remix, ctx).action == "normal"

    def test_ecart_court_sans_duree_confirmee_l_humain(self):
        cas, remix, ctx = self._franchise({"deezer": 202, "youtube": 203})
        assert pol.arbitrage_etendu(cas, remix, ctx) is None


class TestGeneriqueDistincts:
    """Décision utilisateur 2026-10-29 : « Interlude » et « Outro » de Diam's,
    50 s chacun, chacun ses identifiants — une coïncidence, ✓ Normal."""

    DUREES = {"apple_music": 50, "deezer": 50, "spotify_web": 50, "youtube": 51}

    def _paire(self, **autre):
        a = _t(
            "Interlude",
            1,
            duration=50,
            isrc="FRZ1",
            deezer_id=1,
            spotify_id="S1",
            durations_observees=dict(self.DUREES),
        )
        kw = dict(
            duration=50,
            isrc="FRZ2",
            deezer_id=2,
            spotify_id="S2",
            durations_observees=dict(self.DUREES),
        )
        kw.update(autre)
        b = _t("Outro", 2, **kw)
        return (
            _cas("generique_duree", a, {"duree": 50, "autres": [2]}),
            a,
            SimpleNamespace(disco=[a, b]),
        )

    def test_identifiants_distincts_et_catalogues_d_accord(self):
        cas, a, ctx = self._paire()
        assert pol.arbitrage_etendu(cas, a, ctx).action == "normal"

    def test_un_identifiant_partage_ou_manquant_l_humain(self):
        for autre in ({"isrc": "FRZ1"}, {"deezer_id": None}, {"spotify_id": "S1"}):
            cas, a, ctx = self._paire(**autre)
            assert pol.arbitrage_etendu(cas, a, ctx) is None, autre

    def test_durees_trop_peu_soutenues_l_humain(self):
        cas, a, ctx = self._paire(durations_observees={"deezer": 50, "songbpm": 50})
        assert pol.arbitrage_etendu(cas, a, ctx) is None


class TestSongbpmVersion:
    """Décision utilisateur 2026-10-29 : les versions et titres génériques
    perdent aussi une page SongBPM que 3 catalogues démentent."""

    def test_version_dementie_par_trois_catalogues(self):
        durees = {"songbpm": 207, "deezer": 252, "spotify_web": 252, "youtube": 253}
        t = _t("Niagara Falls (Foot or 2) [ChoppedNotSlopped]", durations_observees=durees)
        assert pol.arbitrage_etendu(_cas("duree_songbpm", t), t, None).action == "retirer_songbpm"

    def test_intro_dementie(self):
        durees = {"songbpm": 203, "apple_music": 16, "deezer": 15, "spotify_web": 15, "youtube": 16}
        t = _t("Hasta La Vista (Intro)", durations_observees=durees)
        assert pol.arbitrage_etendu(_cas("duree_songbpm", t), t, None).action == "retirer_songbpm"

    def test_deux_catalogues_ou_dix_secondes_l_humain(self):
        t = _t("Live (Remix)", durations_observees={"songbpm": 207, "deezer": 252, "youtube": 253})
        assert pol.arbitrage_etendu(_cas("duree_songbpm", t), t, None) is None
        durees = {"songbpm": 127, "deezer": 137, "spotify_web": 137, "youtube": 138}
        t = _t("Wait (Remix)", durations_observees=durees)
        assert pol.arbitrage_etendu(_cas("duree_songbpm", t), t, None) is None

    def test_un_original_reste_a_la_regle_validee(self):
        durees = {"songbpm": 207, "deezer": 252, "spotify_web": 252, "youtube": 253}
        t = _t("Niagara Falls", durations_observees=durees)
        assert pol.politique_songbpm_version(_cas("duree_songbpm", t), t, None) is None


class TestSongbpmTitreContenu:
    """2026-10-29 : mesures SongBPM posées par un titre CONTENU avant la garde
    de pose `cccebc7` (« Maybe » / « Maybe So » de Kid Cudi, « Reste » /
    « reste à vivre »). Retirées de la fiche que ses catalogues démentent quand
    ceux de l'autre les confirment ; le reste à l'humain, une fois par paire."""

    MESURES = (("bpm", 140), ("key", 5), ("mode", 1), ("duration", 205))

    def _paire(self, court, long, titres=("Reste", "reste à vivre")):
        from src.enrichment.observation import Observation

        a = _t(titres[0], 10, durations_observees={**court, "songbpm": 205})
        b = _t(titres[1], 11, durations_observees={**long, "songbpm": 205})
        obs = {t.id: [Observation(c, v, "songbpm") for c, v in self.MESURES] for t in (a, b)}
        return a, b, revue.contexte([a, b], obs)

    def test_dementie_ici_confirmee_la_retiree(self):
        a, b, ctx = self._paire(
            {"spotify_web": 186, "deezer": 186, "apple_music": 186},
            {"spotify_web": 204, "deezer": 204},
        )
        motif, preuves = revue.songbpm_titre_contenu(a, ctx)
        assert preuves["retirer"] and preuves["garder"] == 11 and preuves["track_ids"] == [10]
        assert revue.songbpm_titre_contenu(b, ctx) is None  # la propriétaire garde
        assert preuves["relation"] == "autre_morceau" and preuves["champs"] == [
            "bpm",
            "key",
            "mode",
            "duration",
        ]
        arb = pol.arbitrage_etendu(_cas("songbpm_titre_contenu", a, preuves), a, ctx)
        assert arb is not None and arb.action == "retirer_champs_songbpm"

    def test_un_autre_morceau_sans_catalogue_perd_toute_la_mesure(self):
        """Décision utilisateur 2026-10-30 : « Maybe » n'a que sa vidéo (173 s),
        « Maybe So » est confirmé — deux morceaux, toute la mesure part."""
        a, b, ctx = self._paire(
            {"youtube_video": 173}, {"deezer": 205, "spotify_web": 205}, ("Maybe", "Maybe So")
        )
        motif, preuves = revue.songbpm_titre_contenu(a, ctx)
        assert preuves["retirer"] and preuves["fiche"] == "aucune"
        assert preuves["relation"] == "autre_morceau" and len(preuves["champs"]) == 4
        arb = pol.arbitrage_etendu(_cas("songbpm_titre_contenu", a, preuves), a, ctx)
        assert arb is not None and arb.action == "retirer_champs_songbpm"
        assert revue.songbpm_titre_contenu(b, ctx) is None  # la propriétaire garde

    def test_sans_catalogue_une_video_a_la_duree_de_la_page_retient(self):
        a, b, ctx = self._paire(
            {"youtube_video": 204}, {"deezer": 205, "spotify_web": 205}, ("Maybe", "Maybe So")
        )
        motif, preuves = revue.songbpm_titre_contenu(a, ctx)
        assert not preuves.get("retirer")

    @pytest.mark.parametrize(
        "court, long",
        [
            ("Extrait ULTRA", "Ultra"),
            ("Jesus Walks Snippet", "Jesus Walks (Intro)"),
        ],
    )
    def test_une_coupe_du_meme_morceau_ne_perd_que_sa_duree(self, court, long):
        """Booba « Extrait ULTRA », Kanye « Jesus Walks Snippet » (cas réels) :
        même morceau, même tempo — la durée seule part."""
        a, b, ctx = self._paire({}, {"discogs": 204}, (court, long))
        motif, preuves = revue.songbpm_titre_contenu(a, ctx)
        assert preuves["retirer"] and preuves["relation"] == "coupe"
        assert preuves["champs"] == ["duration"] and "la durée SongBPM" in motif
        arb = pol.arbitrage_etendu(_cas("songbpm_titre_contenu", a, preuves), a, ctx)
        assert arb is not None and arb.action == "retirer_champs_songbpm"
        assert "coupe" in arb.sens

    def _paroles(self, t, texte):
        t.lyrics.text, t.lyrics.source = texte, "genius"

    COUPLET = (
        "je marche seul dans la ville froide les lumieres brillent encore "
        "personne ne voit ce que je porte le silence parle plus fort que nous"
    )

    def test_une_partie_d_un_tout_ne_perd_que_sa_duree(self):
        """Booba « Civilisé, partie 1 » (vidéo 184 s) / « Civilisé » (286 s) :
        paroles communes, la partie plus courte que le tout — une coupe."""
        a, b, ctx = self._paire(
            {"youtube_video": 150}, {"deezer": 205}, ("Civilisé, partie 1", "Civilisé")
        )
        self._paroles(a, self.COUPLET)
        self._paroles(b, self.COUPLET + " et le refrain revient encore une fois")
        motif, preuves = revue.songbpm_titre_contenu(a, ctx)
        assert preuves["retirer"] and preuves["relation"] == "coupe"
        assert preuves["champs"] == ["duration"]

    def test_une_suite_aux_paroles_dementies_perd_toute_la_mesure(self):
        """Freeze Corleone « Argent noir » / « Argent noir part. 3 » : une suite,
        pas une partie (paroles d'un autre morceau)."""
        a, b, ctx = self._paire({}, {"deezer": 205}, ("Argent noir", "Argent noir part. 3"))
        self._paroles(a, self.COUPLET)
        self._paroles(
            b, "money cash flow gang dans le bloc toujours les memes freres sur la route du succes"
        )
        motif, preuves = revue.songbpm_titre_contenu(a, ctx)
        assert preuves["retirer"] and preuves["relation"] == "autre_morceau"
        assert len(preuves["champs"]) == 4

    def test_une_partie_sans_preuve_va_a_l_humain(self):
        a, b, ctx = self._paire({}, {"deezer": 205}, ("Argent noir", "Argent noir part. 3"))
        motif, preuves = revue.songbpm_titre_contenu(a, ctx)
        assert not preuves.get("retirer") and preuves["relation"] == "indécise"

    @pytest.mark.parametrize(
        "court, long",
        [
            ("Bad Mood (Demo)", "Bad Mood / Shit on You"),  # démo, titre joint
            ("My Way Home [Mixed]", "Home (Windy)"),
        ],
    )
    def test_une_version_va_a_l_humain(self, court, long):
        """Travis Scott « Bad Mood (Demo) » : le tempo peut différer."""
        a, b, ctx = self._paire({}, {"deezer": 205}, (court, long))
        motif, preuves = revue.songbpm_titre_contenu(a, ctx)
        assert not preuves.get("retirer")
        assert pol.arbitrage_etendu(_cas("songbpm_titre_contenu", a, preuves), a, ctx) is None

    def test_un_lien_genius_de_remix_va_a_l_humain(self):
        """Kanye « HIGHS AND LOWS 2 », `remix_of` « HIGHS AND LOWS »."""
        a, b, ctx = self._paire({}, {"deezer": 205}, ("HIGHS AND LOWS 2", "HIGHS AND LOWS"))
        b.genius_url = "https://genius.com/Kanye-west-highs-and-lows-lyrics"
        a.relationships = [{"type": "remix_of", "title": b.title, "url": b.genius_url}]
        motif, preuves = revue.songbpm_titre_contenu(a, ctx)
        assert not preuves.get("retirer") and preuves["relation"] == "version"

    def test_un_contenant_ne_perd_que_sa_duree(self):
        """« BULLY V1 », page du disque entier, CONTIENT « BULLY » (relation
        `contient`) : une coupe, la durée seule part."""
        a, b, ctx = self._paire({}, {"deezer": 205}, ("BULLY V1", "BULLY"))
        a.relationships = [{"type": "contient", "title": "BULLY", "track_id": 11}]
        motif, preuves = revue.songbpm_titre_contenu(a, ctx)
        assert preuves["retirer"] and preuves["champs"] == ["duration"]

    def test_un_autre_morceau_aux_paroles_communes_va_a_l_humain(self):
        a, b, ctx = self._paire({}, {"deezer": 205}, ("Tree", "That Tree"))
        self._paroles(a, self.COUPLET)
        self._paroles(b, self.COUPLET)
        motif, preuves = revue.songbpm_titre_contenu(a, ctx)
        assert not preuves.get("retirer") and preuves["relation"] == "indécise"

    def test_ecart_d_edition_l_humain(self):
        """À moins de `ECART_VERSION_S` : deux éditions d'un même enregistrement."""
        a, b, ctx = self._paire({"deezer": 199}, {"deezer": 205})
        motif, preuves = revue.songbpm_titre_contenu(a, ctx)
        assert not preuves.get("retirer")

    def test_une_duree_independante_concordante_retient(self):
        """Un catalogue contre, mais l'audio Topic à la durée de la page : l'humain."""
        a, b, ctx = self._paire({"deezer": 186, "youtube": 205}, {"deezer": 204})
        assert not revue.songbpm_titre_contenu(a, ctx)[1].get("retirer")

    def test_une_valeur_copiee_de_l_autre_n_est_pas_un_temoin(self):
        a, b, ctx = self._paire({"deezer": 186, "spotify_web": 204}, {"spotify_web": 204})
        # Sans la copie, « Reste » n'a que Deezer contre ; l'autre n'a plus rien.
        motif, preuves = revue.songbpm_titre_contenu(a, ctx)
        assert not preuves.get("retirer") and preuves["l_autre"] == "aucune"

    def test_deux_fiches_confirmees_sans_cas(self):
        """« La Récolte » et le medley qui la porte : même audio, rien à trancher."""
        a, b, ctx = self._paire({"youtube": 205, "ytmusic": 205}, {"spotify_web": 204})
        assert revue.songbpm_titre_contenu(a, ctx) is None
        assert revue.songbpm_titre_contenu(b, ctx) is None

    def test_sans_duree_songbpm_aucun_cas(self):
        """« Jail » / « Jail, Pt. 2 » : même BPM, aucune durée de page."""
        a, b, ctx = self._paire({"deezer": 186}, {"deezer": 204})
        for t in (a, b):
            ctx.obs[t.id] = [o for o in ctx.obs[t.id] if o.field != "duration"]
        assert revue.songbpm_titre_contenu(a, ctx) is None
        assert revue.songbpm_titre_contenu(b, ctx) is None

    def test_titres_sans_inclusion_ou_jumelles_hors_champ(self):
        a, b, ctx = self._paire({"deezer": 186}, {"deezer": 204}, ("Reste", "Partir"))
        assert revue.songbpm_titre_contenu(a, ctx) is None
        a, b, ctx = self._paire({"deezer": 186}, {"deezer": 204}, ("Suzy", "Suzy 2003"))
        assert revue.songbpm_titre_contenu(a, ctx) is None  # `jumelle_annee`

    def test_la_garde_de_pose_refuse_ensuite_la_meme_page(self):
        """Le refus qui tient : aucune page SongBPM n'est gardée en base, mais la
        garde `cccebc7` refuse à la fiche retirée la page de l'autre."""
        from src.utils.titre_annee import EXCLUES_SONGBPM, duree_independante

        a, b, ctx = self._paire(
            {"spotify_web": 186, "deezer": 186}, {"spotify_web": 204, "deezer": 204}
        )
        assert revue.songbpm_titre_contenu(a, ctx)[1]["retirer"]
        duree = duree_independante(a.durations_observees, sources_exclues=EXCLUES_SONGBPM)
        page = {"title": b.title, "artist": "A2H", "duration": "3:25"}
        juge = revue._juge_songbpm()
        assert juge.choisir_resultat([page], a.title, "A2H", duree_fiche=duree) is None
