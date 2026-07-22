"""Tests de music_theory — conversions key/mode.

Site du bug historique de mapping Key & Mode (cf. CLAUDE.md) : ces tests
verrouillent le format CANONIQUE de `musical_key` (français, composite
enharmonique) et la convergence des notations d'entrée (US/FR/Unicode).
"""

import pytest

from src.utils.music_theory import (
    key_mode_to_french,
    key_mode_to_french_from_string,
    musical_key_to_pitch_mode,
    normalize_musical_key,
    note_to_pitch_class,
    parse_mode,
)


class TestNoteToPitchClass:
    @pytest.mark.parametrize(
        ("note", "pc"),
        [
            # Notation anglaise
            ("C", 0),
            ("F#", 6),
            ("Bb", 10),
            ("B", 11),
            # Notation française (avec et sans accents)
            ("Do", 0),
            ("Ré", 2),
            ("Re", 2),
            ("Sol#", 8),
            ("Sib", 10),
            # Unicode ♯/♭
            ("G♯", 8),
            ("B♭", 10),
            # Composites enharmoniques : la 1re partie suffit
            ("C#/Db", 1),
            ("G♯/A♭", 8),
            ("Do#/Réb", 1),
            # Entiers et chaînes numériques
            (7, 7),
            (0, 0),
            ("7", 7),
            # Suffixe mineur collé ("EM" = E minor)
            ("Em", 4),
            ("F#m", 6),
        ],
    )
    def test_notes_reconnues(self, note, pc):
        assert note_to_pitch_class(note) == pc

    @pytest.mark.parametrize("invalide", [None, "", "H", "Z#", 12, -1, "12", "n'importe quoi"])
    def test_entrees_invalides(self, invalide):
        assert note_to_pitch_class(invalide) is None


class TestParseMode:
    @pytest.mark.parametrize(
        ("mode", "attendu"),
        [
            ("major", 1),
            ("majeur", 1),
            ("MAJ", 1),
            ("minor", 0),
            ("mineur", 0),
            ("min", 0),
            ("1", 1),
            ("0", 0),
            (1, 1),
            (0, 0),
        ],
    )
    def test_modes_reconnus(self, mode, attendu):
        assert parse_mode(mode) == attendu

    @pytest.mark.parametrize("invalide", [None, "", "dorien", 2, -1])
    def test_modes_invalides(self, invalide):
        assert parse_mode(invalide) is None


class TestKeyModeToFrench:
    @pytest.mark.parametrize(
        ("key", "mode", "attendu"),
        [
            (0, 1, "Do majeur"),
            (2, 0, "Ré mineur"),
            # Touches noires : composite enharmonique canonique
            (1, 0, "Do#/Réb mineur"),
            (8, 1, "Sol#/Lab majeur"),
        ],
    )
    def test_format_canonique(self, key, mode, attendu):
        assert key_mode_to_french(key, mode) == attendu

    def test_convergence_enharmonique_depuis_strings(self):
        # "C#" et "Db" doivent donner LA MÊME chaîne canonique
        assert key_mode_to_french_from_string("C#", "major") == "Do#/Réb majeur"
        assert key_mode_to_french_from_string("Db", "major") == "Do#/Réb majeur"

    def test_from_string_entrees_variees(self):
        assert key_mode_to_french_from_string("A", "minor") == "La mineur"
        assert key_mode_to_french_from_string("7", 1) == "Sol majeur"

    def test_from_string_invalide_retourne_none(self):
        # Ne pas polluer musical_key avec des valeurs non interprétables
        assert key_mode_to_french_from_string("???", "major") is None
        assert key_mode_to_french_from_string("C", "dorien") is None


class TestNormalizeMusicalKey:
    @pytest.mark.parametrize(
        ("entree", "attendu"),
        [
            ("G♯/A♭ majeur", "Sol#/Lab majeur"),
            ("A minor", "La mineur"),
            ("Do# majeur", "Do#/Réb majeur"),
            ("Do majeur", "Do majeur"),  # déjà canonique : inchangé
        ],
    )
    def test_renormalisation(self, entree, attendu):
        assert normalize_musical_key(entree) == attendu

    @pytest.mark.parametrize("invalide", [None, "", "Do", "n'importe quoi du tout", 42])
    def test_non_interpretable_retourne_none(self, invalide):
        assert normalize_musical_key(invalide) is None


class TestMusicalKeyToPitchMode:
    """Inverse de key_mode_to_french (rétro-dérivation E7-D2 des orphelins)."""

    @pytest.mark.parametrize(
        ("entree", "attendu"),
        [
            ("Si mineur", (11, 0)),
            ("Sol majeur", (7, 1)),
            ("Do#/Réb mineur", (1, 0)),  # composite : 1re enharmonie
            ("La#/Sib mineur", (10, 0)),
            ("A minor", (9, 0)),  # notation anglaise tolérée
        ],
    )
    def test_decomposition(self, entree, attendu):
        assert musical_key_to_pitch_mode(entree) == attendu

    @pytest.mark.parametrize("valeur", ["Si mineur", "Do#/Réb majeur", "Sol#/Lab mineur"])
    def test_roundtrip_avec_key_mode_to_french(self, valeur):
        pc, mode = musical_key_to_pitch_mode(valeur)
        assert key_mode_to_french(pc, mode) == valeur

    @pytest.mark.parametrize("invalide", [None, "", "Do", "n'importe quoi", 42])
    def test_non_interpretable_retourne_none(self, invalide):
        assert musical_key_to_pitch_mode(invalide) is None
