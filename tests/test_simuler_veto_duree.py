"""Témoins du passage à blanc du veto de durée (`scripts/simuler_veto_duree.py`)."""

from scripts.simuler_veto_duree import CONFIRME, CONTRADICTOIRE, DEMENT, SANS_TEMOIN, juger


def test_l_audio_du_lrc_confirme_la_nouvelle_valeur():
    assert juger(277, 240, [241], None) == CONFIRME


def test_l_audio_du_lrc_dement_la_nouvelle_valeur():
    assert juger(277, 240, [276], None) == DEMENT


def test_un_lrc_qui_depasse_la_nouvelle_valeur_la_dement():
    assert juger(277, 240, [], 270) == DEMENT


def test_un_lrc_qui_depassait_l_ancienne_valeur_confirme():
    assert juger(240, 277, [], 270) == CONFIRME


def test_temoins_opposes():
    assert juger(277, 240, [241], 270) == CONTRADICTOIRE


def test_sans_temoin():
    assert juger(277, 240, [], 200) == SANS_TEMOIN
