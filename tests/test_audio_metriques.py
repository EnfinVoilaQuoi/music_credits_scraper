"""`src/audio/metriques` : les cinq catégories MIREX de comparaison de tonalités."""

import pytest

from src.audio.metriques import (
    CATEGORIES,
    EXACTE,
    FAUSSE,
    PARALLELE,
    QUINTE,
    RELATIVE,
    categorie_tonalite,
    score_mirex,
)

DO_MAJ = (0, 1)
LA_MIN = (9, 0)


@pytest.mark.parametrize(
    ("estime", "reference", "attendu"),
    [
        (DO_MAJ, DO_MAJ, EXACTE),
        ((7, 1), DO_MAJ, QUINTE),  # Sol majeur : quinte au-dessus
        ((5, 1), DO_MAJ, QUINTE),  # Fa majeur : quinte au-dessous
        (LA_MIN, DO_MAJ, RELATIVE),  # relative mineure de Do
        (DO_MAJ, LA_MIN, RELATIVE),  # symétrique : Do majeur pour La mineur
        ((0, 0), DO_MAJ, PARALLELE),  # Do mineur
        ((2, 1), DO_MAJ, FAUSSE),  # Ré majeur : un ton, même mode
        ((4, 0), DO_MAJ, FAUSSE),  # Mi mineur : mode inversé, pas la relative
        ((7, 1), (4, 0), RELATIVE),  # Sol majeur pour Mi mineur
        ((3, 1), LA_MIN, FAUSSE),  # Mi♭ majeur : mode inversé mais pas la relative
        ((11, 0), DO_MAJ, FAUSSE),  # Si mineur
    ],
)
def test_categories(estime, reference, attendu):
    assert categorie_tonalite(estime, reference) == attendu


def test_bareme_mirex_decroissant():
    scores = [score_mirex(c) for c in CATEGORIES]
    assert scores == sorted(scores, reverse=True)
    assert score_mirex(EXACTE) == 1.0
    assert score_mirex(FAUSSE) == 0.0


def test_pitch_class_modulo_12():
    assert categorie_tonalite((12, 1), DO_MAJ) == EXACTE
    assert categorie_tonalite((-5, 1), DO_MAJ) == QUINTE
