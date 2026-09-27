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
    assert duree_a_declarer([{"duration": 150, "channel": "Booska-P"}], True) == (
        150,
        "youtube_video",
    )


def test_desaccord_ne_conclut_pas():
    vids = [{"duration": 193, "channel": _TOPIC}, {"duration": 240, "channel": _TOPIC}]
    assert duree_a_declarer(vids, False) is None
    assert duree_a_declarer([{"duration": 193, "channel": _TOPIC}] * 2, False) == (193, "youtube")


def test_rangs_de_l_arbitrage():
    ordre = DISCOGRAPHY_PRIORITIES["duration"]
    assert ordre.index("youtube") < ordre.index("songbpm")
    assert ordre.index("youtube_video") == len(ordre) - 2  # ReccoBeats reste en queue
