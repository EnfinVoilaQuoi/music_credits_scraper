"""Comparateurs partagés `src/api/_text_match.py` + la garde mot-entier.

Ce module a été créé le 2026-09-04 pour tenir en UN endroit les sept fonctions
que `lrclib_api` et `musixmatch_api` portaient à l'octet près. La duplication
n'était pas qu'inélégante : elle garantissait qu'un correctif appliqué d'un côté
laisserait l'autre en l'état — exactement ce qui avait laissé diverger les deux
clés de déduplication SNEP.

Le correctif porté ici : `_artist_match` ne rend plus 1.0 pour une sous-chaîne
NUE. « IAM » est bien contenu dans « Williams », mais n'y est pas un mot.
"""

import pytest

from src.api import _text_match as tm
from src.api.lrclib_api import _artist_match as artist_match_lrclib
from src.api.musixmatch_api import _artist_match as artist_match_musixmatch
from src.utils.title_matching import contains_as_words


class TestContainsAsWords:
    @pytest.mark.parametrize(
        ("needle", "haystack"),
        [
            ("jul", "jul sch"),  # début
            ("sch", "jul sch"),  # fin
            ("sch", "jul sch officiel"),  # milieu
            ("jul sch", "jul sch officiel"),  # plusieurs mots
            ("iam", "iam"),  # égalité
            ("iam", "iam williams"),  # le vrai IAM
        ],
    )
    def test_present_comme_mot(self, needle, haystack):
        assert contains_as_words(needle, haystack) is True

    @pytest.mark.parametrize(
        ("needle", "haystack"),
        [
            ("iam", "williams"),  # LE cas : sous-chaîne, pas un mot
            ("jul", "julien"),  # préfixe collé
            ("sch", "schmitt"),
            ("williams", "iam"),  # sens inverse
            ("jul sch", "jul et sch"),  # les mots ne se suivent pas
        ],
    )
    def test_sous_chaine_nue_refusee(self, needle, haystack):
        assert contains_as_words(needle, haystack) is False

    @pytest.mark.parametrize(("needle", "haystack"), [("", "abc"), ("abc", ""), ("", "")])
    def test_valeurs_vides(self, needle, haystack):
        assert contains_as_words(needle, haystack) is False

    def test_caracteres_speciaux_echappes(self, needle="a+b", haystack="a+b c"):
        """L'aiguille est échappée : elle n'est jamais lue comme une regex."""
        assert contains_as_words(needle, haystack) is True


class TestArtistMatch:
    def test_identique(self):
        assert tm._artist_match("ISHA", "Isha") == 1.0

    def test_accents_et_ponctuation_neutralises(self):
        assert tm._artist_match("L'Or du Commun", "L’Or du Commun") == 1.0

    @pytest.mark.parametrize(
        ("a", "b"),
        [("Jul", "Jul & SCH"), ("IAM", "IAM & Akhenaton"), ("SCH", "Jul & SCH")],
    )
    def test_inclusion_legitime_conservee(self, a, b):
        """Ce que le relâchement devait servir — il le sert toujours."""
        assert tm._artist_match(a, b) == 1.0

    def test_sous_chaine_nue_ne_vaut_plus_1(self):
        assert tm._artist_match("IAM", "Williams") < 1.0

    def test_graphie_voisine_rattrapee_par_la_similarite(self):
        """La garde mot-entier fait perdre le 1.0, pas le match : `SequenceMatcher`
        garde « Alpha Wann » vs « AlphaWann » très au-dessus de tout seuil utile."""
        assert tm._artist_match("Alpha Wann", "AlphaWann") > 0.9

    def test_artistes_etrangers(self):
        assert tm._artist_match("ISHA", "Nekfeu") < 0.55

    @pytest.mark.parametrize(("a", "b"), [("", "ISHA"), ("ISHA", ""), (None, "ISHA")])
    def test_valeurs_vides(self, a, b):
        assert tm._artist_match(a, b) == 0.0


class TestUnSeulExemplaire:
    """Le point du chantier : les deux clients partagent désormais les MÊMES
    objets. Un futur correctif ne peut plus n'en toucher qu'un."""

    def test_les_deux_clients_partagent_la_fonction(self):
        assert artist_match_lrclib is tm._artist_match
        assert artist_match_musixmatch is tm._artist_match

    def test_les_helpers_de_titre_aussi(self):
        from src.api.lrclib_api import _title_match as t_lrclib
        from src.api.musixmatch_api import _title_match as t_musixmatch

        assert t_lrclib is t_musixmatch is tm._title_match


class TestNormalisation:
    @pytest.mark.parametrize(
        ("brut", "attendu"),
        [("Éléphant", "elephant"), ("A-B_C", "a b c"), ("  Deux  ", "deux"), ("", "")],
    )
    def test_norm(self, brut, attendu):
        assert tm._norm(brut) == attendu

    def test_apres_normalisation_seuls_lettres_chiffres_espaces(self):
        """C'est ce qui rend l'ancrage par limite de mot prévisible."""
        assert tm._norm("Jul & S.C.H. (2024)") == "jul s c h 2024"


class TestTitleMatch:
    def test_identique_apres_retrait_du_featuring(self):
        assert tm._title_match("Titre (feat. SCH)", "Titre") == 1.0

    def test_inclusion_forte(self):
        assert tm._title_match("Matrix", "Matrix Intro") >= 0.9

    def test_titres_etrangers(self):
        assert tm._title_match("Matrix", "Complètement autre") < 0.72
