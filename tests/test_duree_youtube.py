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
