"""Golden master de `cert_normalize.normalize_text`.

Fige le comportement EXACT de la normalisation des certifs (extraite de la SNEP).
La parité inter-sources (SNEP/BRMA/RIAA) du matcher en dépend : toute
modification de la logique doit mettre à jour ces attendus en connaissance de
cause. Valeurs capturées sur l'implémentation d'origine.
"""

import pytest

from src.utils.cert_normalize import normalize_text, repair_extra_separators

CASES = [
    ("", ""),
    ("Beyoncé & Jay-Z", "BEYONCE AND JAY-Z"),  # accents + & → AND
    ("Œuvre", "OEUVRE"),  # ligature
    ("L’été", "L'ETE"),  # apostrophe courbe → droite, accents
    ("L'été", "L'ETE"),  # apostrophe droite conservée
    ("Prix: 5$", "PRIX 5S"),  # $ → S, ponctuation retirée
    ("  a   b  ", "A B"),  # espaces normalisés
    ("Café—Bar", "CAFE-BAR"),  # tiret long → '-'
    ("AC/DC", "ACDC"),  # slash retiré
    ("Hello… World", "HELLO WORLD"),  # points de l'ellipse retirés
    ("M$ money", "MS MONEY"),
    ('"Guillemets"', "GUILLEMETS"),
    ("S.O.A.B", "SOAB"),
    ("Jul feat. SCH", "JUL FEAT SCH"),
]


@pytest.mark.parametrize("brut,attendu", CASES)
def test_normalize_text_golden(brut, attendu):
    assert normalize_text(brut) == attendu


def test_normalize_none():
    assert normalize_text(None) == ""


class TestRepairExtraSeparators:
    def test_ligne_conforme_inchangee(self):
        txt = "a;b;c\n1;2;3"
        out, n = repair_extra_separators(txt)
        assert out == txt
        assert n == 0

    def test_colonne_editeur_fusionnee_et_quotee(self):
        # 4 champs pour un en-tête à 3 colonnes → le surplus fusionne dans la 3e
        txt = "artist;title;label\nX;Y;Def;Jam"
        out, n = repair_extra_separators(txt)
        assert n == 1
        assert out.splitlines()[1] == 'X;Y;"Def;Jam"'

    def test_ligne_avec_quotes_non_touchee(self):
        txt = 'a;b;c\nX;Y;"Def;Jam"'
        out, n = repair_extra_separators(txt)
        assert n == 0
