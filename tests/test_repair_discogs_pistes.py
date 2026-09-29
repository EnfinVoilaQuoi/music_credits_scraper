"""Lot B5 : la position d'un morceau sur son disque Discogs (réparation)."""

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "repair_discogs_pistes", Path(__file__).parents[1] / "scripts" / "repair_discogs_pistes.py"
)
repair = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(repair)

PISTES = [("Intro", "1", "1:30"), ("Heartless", "2", "3:31"), ("Intro", "3", "0:45")]


def test_titre_unique():
    assert repair.position_du_morceau(PISTES, "Heartless", None) == "2"


def test_titre_ambigu_departage_par_la_duree_sinon_rien():
    assert repair.position_du_morceau(PISTES, "Intro", 46) == "3"
    assert repair.position_du_morceau(PISTES, "Intro", None) is None
    assert repair.position_du_morceau(PISTES, "Outro", None) is None
