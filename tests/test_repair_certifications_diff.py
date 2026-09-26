"""Comparaison avant/après de `scripts/repair_certifications.py` (2026-09-26)."""

import importlib.util

import pytest


@pytest.fixture(scope="module")
def rc():
    spec = importlib.util.spec_from_file_location("rc", "scripts/repair_certifications.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _e(**kw):
    base = {"body": "RIAA", "artist_name": "X", "title": "T", "certification": "Gold"}
    base["certification_date"] = "2020-01-01"
    base.update(kw)
    return base


def test_une_vraie_certif_et_son_echo_sont_distinctes(rc):
    """Sans le marqueur d'écho dans la clé, la vraie certif fautive passait pour
    déjà juste et n'était jamais retirée (« Feels Like Summer »)."""
    avant = [_e(), _e(echo=True, echo_de=5)]
    apres = [_e(echo=True, echo_de=5)]
    retirees, ajoutees = rc._diff(avant, apres)
    assert retirees == [_e()] and ajoutees == []


def test_rien_a_redire_quand_c_est_identique(rc):
    assert rc._diff([_e()], [_e()]) == ([], [])
