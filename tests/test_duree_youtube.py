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
        assert DISCOGRAPHY_PRIORITIES["duration"][0] == "youtube_officiel"


def test_lien_coupe():
    from src.utils.duree_youtube import lien_coupe

    assert lien_coupe(128, 319)  # extrait de compilation
    assert lien_coupe(178, 288)  # mixtape coupée
    assert not lien_coupe(312, 319)  # écart de quelques secondes
    assert not lien_coupe(340, 319)  # plus long : pas un extrait
    assert not lien_coupe(None, 319) and not lien_coupe(128, None)


def test_un_lien_coupe_est_refuse(data_manager):
    """« HLM 3 » : le lien Deezer est l'extrait de 128 s d'une compilation, la
    chaîne Lunatic publie 319 s — le lien part, refusé ; un lien juste reste."""
    from src.enrichment.observation import Observation
    from src.models import Artist, Track
    from src.utils.corrections_fiches import deezer_refuses
    from src.utils.update_ytmusic import refuser_liens_coupes

    booba = Artist(name="Booba")
    booba.id = data_manager.save_artist(booba)
    hlm = Track(title="HLM 3", artist=booba, deezer_id=504992422)
    juste = Track(title="Destinée", artist=booba, deezer_id=111)
    for t in (hlm, juste):
        data_manager.save_track(t)
    data_manager.record_discography_observations(hlm.id, [Observation("duration", 128, "deezer")])
    data_manager.record_discography_observations(juste.id, [Observation("duration", 317, "deezer")])
    fiches = {t.title: t for t in data_manager.get_artist_tracks(booba.id)}

    assert refuser_liens_coupes(data_manager, fiches["HLM 3"], 319) == 1
    assert refuser_liens_coupes(data_manager, fiches["Destinée"], 319) == 0
    apres = {t.title: t for t in data_manager.get_artist_tracks(booba.id)}
    assert apres["HLM 3"].deezer_id is None and apres["Destinée"].deezer_id == 111
    assert 504992422 in deezer_refuses(apres["HLM 3"])
