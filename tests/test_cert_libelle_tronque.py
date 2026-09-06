"""Libellés TRONQUÉS par la source à l'endroit d'un caractère accentué.

« L'empire du c » pour « L'empire du côté obscur », « LA MAIN SUR LE C » pour
« LA MAIN SUR LE CŒUR ». La coupure est dans l'export SNEP — les octets du brut
s'arrêtent là — donc rien ne la répare, et il n'existe aucun oracle : cherché le
2026-09-06 sur les 12 127 lignes, aucun titre tronqué n'a sa version complète
ailleurs dans le corpus pour la même certification. On SIGNALE, on ne devine pas.

Ce que la règle doit trancher est le VOISINAGE, pas la longueur. Les signaux
évidents ont été mesurés et écartés d'abord : « finit par un mot de 1-2 lettres »
sortait 759 titres presque tous légitimes (« BEST OF », « AS I AM », « BAD GUY »),
et filtrer par la rareté du mot final n'y changeait rien (333 candidats). Ce qui
distingue un fragment, c'est qu'un déterminant l'annonce.
"""

import pytest

from src.utils.cert_normalize import libelle_tronque


class TestFragmentApresUnDeterminant:
    """Un déterminant annonce un NOM, jamais une lettre isolée."""

    @pytest.mark.parametrize(
        "libelle",
        [
            "LA MAIN SUR LE C",
            "TU LE C",
            "LA TOUR DE M",
            "LE TOUR DE M",
            "LES LECONS DE MUSIQUE DE M",
            "LES PIEDS DEVANT LE D",
            "TOUS DANS LE M",
            "RESTER LA M",
            "Nos R",
            "EN T",
            "LA C",
            "H1ts - TOUS LES N",
        ],
    )
    def test_titres_reellement_coupes(self, libelle):
        assert libelle_tronque(libelle)

    def test_le_determinant_peut_etre_elide(self):
        assert libelle_tronque("Terra Les Voix de l'")


class TestFragmentEnMinuscules:
    @pytest.mark.parametrize(
        "libelle",
        [
            "Les plus grandes chansons du si",
            "Mon fil sur moi et mes chansons pr",
            "CUT KILLER & D.J. ABDEL pr",
            "1998 une g",
            "ALADDIN Ce r",
            "L'empire du c",
        ],
    )
    def test_titres_reellement_coupes(self, libelle):
        assert libelle_tronque(libelle)

    @pytest.mark.parametrize("mot", ["is", "on", "up", "me", "so", "go", "of"])
    def test_les_mots_courts_legitimes_ne_declenchent_pas(self, mot):
        """Sans eux, « I want to know what love is » passerait pour tronqué."""
        assert not libelle_tronque(f"I want to know what love {mot}")


class TestCeQuOnNeSignalePas:
    """Le bruit qu'il a fallu écarter, mesuré sur le corpus réel."""

    @pytest.mark.parametrize(
        "libelle",
        ["BEST OF", "AS I AM", "BAD GUY", "TOP GUN", "MASK OFF", "ALL EYES ON ME", "MY WAY"],
    )
    def test_une_fin_courte_ne_suffit_pas(self, libelle):
        assert not libelle_tronque(libelle)

    @pytest.mark.parametrize("libelle", ["T L C", "K D D", "D O D O", "A A A", "R E D"])
    def test_les_sigles_espaces_sont_des_graphies(self, libelle):
        """Aucune troncature réelle n'a cette forme : toutes gardent un mot entier."""
        assert not libelle_tronque(libelle)

    def test_un_seul_mot_n_est_jamais_juge(self):
        """Sans voisin, aucun moyen de savoir — et « M » est un vrai artiste."""
        assert not libelle_tronque("M")
        assert not libelle_tronque("Z")

    @pytest.mark.parametrize("vide", ["", "   ", None])
    def test_libelle_vide(self, vide):
        assert not libelle_tronque(vide)

    def test_une_lettre_seule_sans_determinant_avant(self):
        """« AXEL F », « X & Y » : rien n'annonce un nom, on se tait."""
        assert not libelle_tronque("AXEL F")

    def test_un_chiffre_final_n_est_pas_un_fragment(self):
        assert not libelle_tronque("KILL BILL Vol.2")
        assert not libelle_tronque("13 ORGANISÉ 2")


class TestFauxPositifsConnus:
    """Assumés, et rattrapables par la case « ✓ correct » de la fenêtre.

    La règle en sort deux sur 22 : les geler ici documente le compromis plutôt
    que de le laisser se redécouvrir.
    """

    @pytest.mark.parametrize("libelle", ["C'EST CARRÉ LE S", "Le Z"])
    def test_ils_sont_signales_et_c_est_accepte(self, libelle):
        assert libelle_tronque(libelle)
