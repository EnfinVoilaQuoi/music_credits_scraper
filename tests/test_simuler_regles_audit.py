"""`scripts/simuler_regles_audit.py` : les juges PURS des règles candidates
simulées le 2026-11-09 (point critique de l'audit). Lecture seule, sans base."""

import importlib.util
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "simuler_regles_audit", RACINE / "scripts" / "simuler_regles_audit.py"
)
sim = importlib.util.module_from_spec(_spec)
sys.modules["simuler_regles_audit"] = sim
_spec.loader.exec_module(sim)


class TestFinitParLeTitre:
    def test_episode_puis_le_titre(self):
        assert sim.finit_par_le_titre("#Jesuispasséchezso : Épisode 6 / 93 Empire", "93 Empire")
        assert sim.finit_par_le_titre(
            "#Jesuispasséchezso : Episode 8 / Fais le Mouv", "Fais le mouv"
        )

    def test_le_sous_titre_seulement_dans_la_variante_affinee(self):
        long, court = "#Jesuispasséchezso : Episode 5 / Police Nationale", (
            "C’est nous les condés (Police Nationale)"
        )
        assert not sim.finit_par_le_titre(long, court)
        assert sim.finit_par_le_titre(long, court, sous_titre=True)

    @pytest.mark.parametrize(
        ("long", "court"),
        [("24/7", "7"), ("Intro / Outro", "Intro"), ("Fais le mouv", "Fais le mouv"), (None, "x")],
    )
    def test_autres_titres(self, long, court):
        assert not sim.finit_par_le_titre(long, court, sous_titre=True)


class TestVerdictB:
    def test_meme_isrc(self):
        assert sim.verdict_b("FR1", "FR1", False).startswith("doublon")

    def test_durees_a_deux_secondes(self):
        assert sim.verdict_b(None, "FR1", True).startswith("doublon")

    def test_durees_differentes(self):
        assert sim.verdict_b("FR1", "FR2", False).startswith("information")

    def test_sans_temoin(self):
        assert sim.verdict_b(None, None, None) == "indécidable"


def test_accord_des_durees():
    assert sim._accord({"deezer": 200}, {"spotify_web": 202}) == (
        True,
        "[200] / [202] (écart min 2 s)",
    )
    assert sim._accord({"deezer": 200}, {})[0] is None
    assert sim._accord({"deezer": 200}, {"apple_music": 260})[0] is False
