"""Tests des helpers purs de cert_matcher (normalisation dates/niveaux, ranking).

Le chargement des CSV (SNEP/BRMA/RIAA) et le raccordement morceau/album vivent
dans `test_cert_matcher_loading.py` : ils ne dépendent PAS des fichiers de data/
(`DATA_PATH` est monkeypatché sur un `tmp_path`), contrairement à ce que cette
note affirmait avant le 2026-09-03.
"""

import pytest

from src.utils.cert_matcher import CertMatcher, _riaa_level, _to_iso_date


class TestToIsoDate:
    @pytest.mark.parametrize(
        ("entree", "attendu"),
        [
            ("October 17, 2017", "2017-10-17"),
            ("Oct 17, 2017", "2017-10-17"),
            ("10/17/2017", "2017-10-17"),
            ("2017-10-17", "2017-10-17"),  # déjà ISO : inchangé
            ("", ""),
            ("None", ""),
        ],
    )
    def test_conversions(self, entree, attendu):
        assert _to_iso_date(entree) == attendu

    def test_format_inconnu_laisse_tel_quel(self):
        assert _to_iso_date("17 octobre 2017") == "17 octobre 2017"


class TestRiaaLevel:
    @pytest.mark.parametrize(
        ("entree", "attendu"),
        [
            ("4x Multi-Platinum", "4x Platinum"),
            ("2X MULTI-PLATINUM", "2x Platinum"),
            ("Multi-Platinum", "Platinum"),
            ("Gold", "Gold"),
            ("Diamond", "Diamond"),
            ("", ""),
        ],
    )
    def test_normalisation(self, entree, attendu):
        assert _riaa_level(entree) == attendu


class TestLevelRank:
    """_level_rank n'utilise pas self → appel non lié (évite le chargement CSV)."""

    def _rank(self, level: str) -> float:
        return CertMatcher._level_rank(None, level)

    def test_ordre_des_paliers(self):
        # Plus petit = plus prestigieux
        assert self._rank("Quadruple Diamant") < self._rank("Diamant")
        assert self._rank("Diamant") < self._rank("Platine")
        assert self._rank("Platine") < self._rank("Or")

    def test_equivalence_fr_en(self):
        assert self._rank("Diamant") == self._rank("Diamond")
        assert self._rank("Or") == self._rank("Gold")
        assert self._rank("Platine") == self._rank("Platinum")

    def test_multiplicateurs_nx(self):
        # "4x Platinum" : un cran au-dessus du palier simple, sous le palier supérieur
        assert self._rank("4x Platinum") < self._rank("Platinum")
        assert self._rank("4x Platinum") > self._rank("Diamant")

    def test_niveau_inconnu_relegue_en_fin(self):
        assert self._rank("Ruby") == 99.0


class TestProgrammeLatin:
    """RIAA Latin est un CORPS distinct, avec son échelle.

    Depuis 2000, la RIAA décerne aussi des awards latins (contenu à ≥ 51 % en
    espagnol) dont les seuils n'ont rien à voir : Platino 60 000 unités contre
    1 000 000 pour Platinum. Les ranger sous le même corps revenait à présenter
    comme comparables des récompenses qui ne le sont pas — et l'ancien scraper
    aggravait le tout en lisant le badge latin `la_61` comme « 61x Platinum ».
    """

    def _rank(self, level: str) -> float:
        return CertMatcher._level_rank(None, level)

    def test_le_vocabulaire_latin_est_classe(self):
        """Sans cela, « Oro » et « 61x Platino » valaient 99.0 : le rang le plus
        BAS, c'est-à-dire relégués derrière n'importe quel niveau connu."""
        assert self._rank("Oro") != 99.0
        assert self._rank("61x Platino") != 99.0

    def test_meme_ordre_interne_que_les_autres_echelles(self):
        assert self._rank("Diamante") < self._rank("Platino")
        assert self._rank("Platino") < self._rank("Oro")
        assert self._rank("4x Platino") < self._rank("Platino")

    def test_le_rang_ne_compare_PAS_les_echelles(self):
        """Le rang ne sert qu'à trier des certifs à l'intérieur d'un corps.

        Platino et Platinum le partagent sans valoir la même chose : c'est
        `riaa_units` qui porte la comparaison, et le TRI qui sépare les corps.
        """
        from src.utils.cert_normalize import riaa_units

        assert self._rank("Platino") == self._rank("Platinum")
        assert riaa_units("Platino") < riaa_units("Platinum")

    def test_le_tri_range_le_latin_apres_le_classique(self):
        """Même pays « US », deux corps : le classique passe devant.

        On trie avec la clé de PRODUCTION (`_cle_de_tri`) : la réécrire ici
        ferait un test qui ne vérifie que lui-même.
        """
        matcher = CertMatcher.__new__(CertMatcher)
        certifs = [
            {"certification": "Platino", "certification_date": "2020-01-01", "body": "RIAA Latin"},
            {"certification": "Gold", "certification_date": "2024-01-01", "body": "RIAA"},
        ]
        for c in certifs:
            c["country"] = "US"

        certifs.sort(key=matcher._cle_de_tri)

        # Sans le cran « corps », le Platino (rang 7) passerait DEVANT le Gold
        # (rang 10) : 60 000 unités présentées avant 500 000.
        assert [c["body"] for c in certifs] == ["RIAA", "RIAA Latin"]

    def test_le_tri_ne_bouscule_pas_les_pays(self):
        """Le cran « corps » ne doit pas prendre le pas sur le pays."""
        matcher = CertMatcher.__new__(CertMatcher)
        certifs = [
            {
                "certification": "Oro",
                "certification_date": "2020-01-01",
                "body": "RIAA Latin",
                "country": "US",
            },
            {
                "certification": "Or",
                "certification_date": "2024-01-01",
                "body": "SNEP",
                "country": "FR",
            },
        ]
        certifs.sort(key=matcher._cle_de_tri)

        assert [c["country"] for c in certifs] == ["FR", "US"]
