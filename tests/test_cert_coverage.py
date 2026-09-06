"""Règle de couverture temporelle partagée par les trois validateurs.

Un mois sans certification n'est une anomalie que si l'année est assez fournie
pour qu'un mois vide soit SURPRENANT. Les trois validateurs appliquaient le même
seuil arbitraire — « au moins 12 certifications » — écrit en trois exemplaires,
et trop bas : sur une année à 16 certifications, des mois vides sont le régime
normal. Le rapport RIAA signalait ainsi 17 mois « manquants » sur 1960-1964.

Le seuil est désormais DÉRIVÉ du volume de l'année, donc progressif de lui-même :
il n'a pas la même exigence en 1960 (16 certifications) et en 2026 (1 147).
"""

import pytest

from src.utils.cert_coverage import annee_assez_dense, mois_vides_attendus


class TestEsperance:
    """12·(11/12)^n : nombre de mois vides attendus sous une répartition uniforme."""

    def test_une_annee_vide_a_douze_mois_vides(self):
        assert mois_vides_attendus(0) == 12

    def test_lesperance_decroit_avec_le_volume(self):
        valeurs = [mois_vides_attendus(n) for n in (5, 13, 29, 114, 400)]
        assert valeurs == sorted(valeurs, reverse=True)

    def test_une_annee_fournie_ne_laisse_aucun_trou_attendu(self):
        assert mois_vides_attendus(1147) < 0.001

    def test_valeur_negative_traitee_comme_zero(self):
        assert mois_vides_attendus(-3) == 12


class TestSeuilProgressif:
    """Le seuil suit le volume de l'ANNÉE, il n'est pas un nombre fixe."""

    @pytest.mark.parametrize("volume", [0, 5, 13, 16, 17, 18, 28])
    def test_les_annees_creuses_ne_sont_pas_analysees(self, volume):
        """Mesuré : SNEP 1991 (13), 1992 (18), 1993 (17), 2014 (13) — quatre
        années dont les trous s'expliquent par la seule rareté."""
        assert annee_assez_dense(volume) is False

    @pytest.mark.parametrize("volume", [29, 42, 114, 135, 1147])
    def test_les_annees_fournies_le_sont(self, volume):
        """Mesuré : SNEP 2006 (114) et 2015 (135) portent de vrais trous de
        collecte — ce sont eux qu'il ne faut PAS perdre en montant le seuil."""
        assert annee_assez_dense(volume) is True

    def test_la_bascule_est_a_moins_dun_mois_attendu(self):
        """La règle est énoncée, pas ajustée sur le résultat souhaité."""
        assert mois_vides_attendus(28) > 1.0
        assert mois_vides_attendus(29) < 1.0


class TestUnSeulReferentiel:
    """Le seuil vivait en trois exemplaires ; il n'en reste qu'un."""

    def test_aucun_validateur_ne_redefinit_le_seuil(self):
        import pathlib

        for module in ("riaa_validator", "brma_validator", "snep_validator"):
            source = pathlib.Path(f"src/utils/{module}.py").read_text(encoding="utf-8")
            assert "MEANINGFUL_YEAR_THRESHOLD" not in source, module
            assert "annee_assez_dense" in source, module
