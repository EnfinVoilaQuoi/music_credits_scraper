"""Le passage à blanc de la réattribution des LRC (2026-10-30, non codée)."""

from scripts.lister_lrc_a_reattribuer import (
    CONTRE,
    OK,
    SANS_AUDIO,
    SANS_DUREE,
    cible_unique,
    temoin,
)


def test_une_seule_fiche_confirme_et_les_autres_sont_etrangeres():
    assert cible_unique({1: 0.9, 2: 0.3}) == 1
    assert cible_unique({1: 0.9, 2: 0.45}) is None  # une autre fiche à 0,4 ou plus
    assert cible_unique({1: 0.9, 2: 0.7}) is None
    assert cible_unique({1: 0.5}) is None


def test_le_temoin_est_la_duree_de_la_fiche_cible():
    assert temoin(238, 237, 203) == OK  # « La Cinquième Saison »
    assert temoin(230, 361, 212) == CONTRE  # « Trippin' On Me » ← « Momma Be Trippin »
    assert temoin(213, 214, 220) == CONTRE  # le LRC court au-delà de la fiche
    assert temoin(None, 146, 125) == SANS_DUREE
    assert temoin(200, None, 150) == SANS_AUDIO
