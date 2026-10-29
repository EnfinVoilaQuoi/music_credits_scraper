"""Durée d'après les vidéos YouTube (`src/utils/duree_youtube`, 2026-09-27)."""

from src.enrichment.reconcile import DISCOGRAPHY_PRIORITIES
from src.utils.duree_youtube import duree_a_declarer, iso8601_secondes

_TOPIC = "A2H - Topic"


def test_iso8601():
    assert iso8601_secondes("PT3M13S") == 193
    assert iso8601_secondes("PT1H2S") == 3602
    assert iso8601_secondes("P0D") is None and iso8601_secondes(None) is None


def test_audio_topic_prioritaire_meme_sur_une_fiche_de_plateforme():
    vids = [{"duration": 193, "channel": _TOPIC}, {"duration": 210, "channel": "A2Hsuperstar"}]
    assert duree_a_declarer(vids, hors_plateformes=False) == (193, "youtube")


def test_un_clip_ne_dit_rien_d_un_morceau_de_plateforme():
    assert duree_a_declarer([{"duration": 210, "channel": "A2Hsuperstar"}], False) is None


def test_hors_plateformes_la_video_est_la_publication():
    video = {"duration": 150, "channel": "Booska-P", "title": "Isha - Freestyle"}
    assert duree_a_declarer([video], True) == (150, "youtube_video")


def test_une_video_ordinaire_doit_etre_le_morceau():
    """CORRECTIFS 1.10 (2026-10-09) : ni extrait, ni émission, ni titre absent,
    ni vidéo que le juge de l'appelant refuse."""
    for titre in ("B.B. Jacques - Machine (Snippet)", "Booba - Hella (extrait futur single)"):
        assert duree_a_declarer([{"duration": 20, "channel": "X", "title": titre}], True) is None
    assert duree_a_declarer([{"duration": 150, "channel": "X"}], True) is None
    video = {"duration": 150, "channel": "X", "title": "Denzel Curry - Curry Wuz Here"}
    assert duree_a_declarer([video], True, recevable=lambda v: False) is None
    assert duree_a_declarer([video], True, recevable=lambda v: True) == (150, "youtube_video")


def test_desaccord_ne_conclut_pas():
    vids = [{"duration": 193, "channel": _TOPIC}, {"duration": 240, "channel": _TOPIC}]
    assert duree_a_declarer(vids, False) is None
    assert duree_a_declarer([{"duration": 193, "channel": _TOPIC}] * 2, False) == (193, "youtube")


def test_rangs_de_l_arbitrage():
    ordre = DISCOGRAPHY_PRIORITIES["duration"]
    assert ordre.index("youtube") < ordre.index("songbpm")
    assert ordre.index("youtube_video") == len(ordre) - 2  # ReccoBeats reste en queue


def test_une_video_tres_longue_ne_donne_pas_sa_duree_a_un_morceau():
    """Diam's « Morts pour rien » (2026-10-10) : 64 min d'un reportage qui
    nommait le titre. Une émission (Grünt) garde la sienne."""
    from src.utils.duree_youtube import duree_a_declarer

    reportage = [{"duration": 3823, "channel": "x", "title": "zied&Bouna mort pour rien"}]
    assert duree_a_declarer(reportage, True, titre_fiche="Morts pour rien") is None
    grunt = [{"duration": 2893, "channel": "Grünt", "title": "Grünt #33"}]
    assert duree_a_declarer(grunt, True, titre_fiche="Grünt #33") == (2893, "youtube_video")


class TestChainesDeConfiance:
    """Chaînes officielles et de leaks déclarées (`chaines_youtube`, 2026-10-13)."""

    _LUNATIC = {"duration": 319, "channel": "Lunatic", "channel_id": "UCH5-Sd-zSHIoX017k7Fa_7g"}

    @staticmethod
    def _nature(artiste):
        from src.utils.chaines_youtube import nature_de

        return lambda v: nature_de(artiste, v)

    def test_la_chaine_officielle_fait_foi_meme_devant_le_topic(self):
        """« HLM 3 » : le Topic est l'extrait de la compilation (128 s)."""
        vids = [
            {**self._LUNATIC, "title": "Lunatic - HLM 3"},
            {"duration": 128, "channel": "Lunatic - Topic", "title": "HLM 3"},
        ]
        assert duree_a_declarer(vids, False, nature=self._nature("Booba")) == (
            319,
            "youtube_officiel",
        )

    def test_un_clip_officiel_ne_fait_pas_foi(self):
        """« On contrôle la zone » : clip 4:04, CD 4:48."""
        clip = {
            "duration": 244,
            "channel": "B20baOfficiel",
            "title": "Booba - On contrôle la zone (Clip Officiel)",
        }
        assert duree_a_declarer([clip], False, nature=self._nature("Booba")) is None

    def test_une_chaine_d_un_autre_artiste_ne_compte_pas(self):
        video = {**self._LUNATIC, "title": "Lunatic - HLM 3"}
        assert duree_a_declarer([video], False, nature=self._nature("Isha")) is None

    def test_reconnue_par_son_nom_a_defaut_d_identifiant(self):
        from src.utils.chaines_youtube import OFFICIELLE, nature_de

        assert nature_de("Booba", {"channel": "b20baofficiel"}) == OFFICIELLE
        assert nature_de("Booba", {"channel": "Booba fan"}) is None

    def test_l_artiste_que_nomme_une_chaine(self):
        """e63 : « X - Topic » nomme X, sauf « Release » ; une chaîne officielle
        déclarée nomme son artiste ; une autre chaîne, personne."""
        from src.utils.chaines_youtube import artiste_de_la_chaine

        assert artiste_de_la_chaine("Yo Gotti - Topic") == "Yo Gotti"
        assert artiste_de_la_chaine("Release - Topic") is None
        assert artiste_de_la_chaine("B20baOfficiel") == "Booba"
        assert artiste_de_la_chaine("Johnny Dee") is None
        assert artiste_de_la_chaine(None) is None

    def test_une_chaine_de_leaks_nomme_autrement(self):
        """« Sky City » (*Yandhi*) : la vidéo s'appelle « City In The Sky »."""
        leak = {
            "duration": 230,
            "channel": "YANDHI",
            "channel_id": "UCHQly8VDe2KQkmM-BZ7S0qg",
            "title": "Kanye West - City In The Sky (Audio)",
        }
        kanye = self._nature("Kanye West")
        assert duree_a_declarer([leak], True, recevable=lambda v: False, nature=kanye) == (
            230,
            "youtube_video",
        )
        # Sur une fiche de plateforme, un leak ne dit rien.
        assert duree_a_declarer([leak], False, recevable=lambda v: False, nature=kanye) is None

    def test_rang_de_l_arbitrage(self):
        """Ramenée avec les vidéos ordinaires (2026-10-14) : témoin, pas référence."""
        ordre = DISCOGRAPHY_PRIORITIES["duration"]
        assert ordre.index("spotify_web") < ordre.index("youtube_officiel")
        assert ordre.index("youtube_officiel") < ordre.index("youtube_video")


class TestClips:
    """« La mention Clip officiel doit bloquer la durée » (décision utilisateur
    2026-10-13) — une captation aussi, sauf pour une fiche qui en est une."""

    def test_un_clip_ne_donne_pas_sa_duree_hors_plateformes(self):
        clip = {"duration": 252, "channel": "X", "title": "Josman - Crise (Clip Officiel)"}
        assert duree_a_declarer([clip], True) is None

    def test_une_captation_de_freestyle_est_la_publication(self):
        video = {
            "duration": 298,
            "channel": "Mouv'",
            "title": "SADEK : Freestyle (Live @ Mouv' studios)",
        }
        assert duree_a_declarer([video], True, titre_fiche="Freestyle Le Mouv") == (
            298,
            "youtube_video",
        )
        assert duree_a_declarer([video], True, titre_fiche="Khameuj") is None

    def test_est_un_clip(self):
        from src.utils.duree_youtube import est_un_clip

        assert est_un_clip("Kid Cudi - Company [Official Video]")
        assert est_un_clip("PLK - peur de me tromper (CLIP)")
        assert not est_un_clip("Qui sème le vent récolte le tempo")
        assert est_un_clip("Orelsan - Basique en LIVE !", "Basique (Acoustic)")
        assert not est_un_clip("Lunatic - Le crime paie - live rare", "Le crime paie (Live)")


class TestCataloguesEtTypeBeats:
    def test_un_catalogue_ne_vaut_que_s_il_nomme_l_artiste(self):
        from src.utils.chaines_youtube import OFFICIELLE, nature_de

        video = {"channel_id": "UCGHR77It-MJi_tPL-mACryQ", "title": "Mala - Ennemi (Audio)"}
        assert nature_de("Mala", video) == OFFICIELLE
        assert nature_de("Booba", video) is None

    def test_un_type_beat_n_est_jamais_le_morceau(self):
        video = {
            "duration": 180,
            "channel": "Addictive Music",
            "title": "[FREE] Booba Type Beat - Ipséité",
        }
        assert duree_a_declarer([video], True) is None
