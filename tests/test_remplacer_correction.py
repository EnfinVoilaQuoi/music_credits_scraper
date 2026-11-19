"""Une correction humaine se remplace (2026-11-19) : l'original désigné du
live « Come Again (feat. Big Red) » passe de la version studio au remix."""

import pytest

from src.utils import corrections_fiches as cf


def test_la_valeur_d_une_correction_se_remplace(data_manager):
    fiche = {"titre": "Come Again (feat. Big Red) (Live)", "album": "La der"}
    cf.memoriser_contenus("Suprême NTM", fiche, [{"genius_id": 898195}])
    assert cf.remplacer_correction("Suprême NTM", fiche, "contient", [{"genius_id": 4668643}])
    (entree,) = cf.corrections_en_base()["Suprême NTM"]
    assert entree["contient"] == [{"genius_id": 4668643}]


def test_sans_correction_consignee_rien(data_manager):
    assert not cf.remplacer_correction("X", {"genius_id": 1}, "version_de", {"genius_id": 2})


def test_une_action_inconnue_est_refusee(data_manager):
    with pytest.raises(ValueError):
        cf.remplacer_correction("X", {"genius_id": 1}, "inventee", 1)
