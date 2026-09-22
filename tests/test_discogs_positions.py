"""À quelles pistes un crédit Discogs s'applique-t-il ? (mesure du 2026-09-22)

Discogs crédite au niveau du DISQUE et dit à quelles pistes chaque crédit se
rapporte. Ce champ était stocké et jamais lu : 1 460 attributions en trop sur
2 094 lignes le portant.
"""

import pytest

from src.utils.discogs_positions import concerne_la_piste, positions_citees


class TestPositionsCitees:
    @pytest.mark.parametrize(
        ("tracks", "attendu"),
        [
            ("C2", {"C2"}),
            ("A5, D4", {"A5", "D4"}),
            ("2, 5", {"2", "5"}),
            ("B1 to B3", {"B1", "B2", "B3"}),
            ("A2, B1 to B3, D2", {"A2", "B1", "B2", "B3", "D2"}),
            ("1 to 3", {"1", "2", "3"}),
            ("cd1-4", {"CD1-4"}),
            (" a1 ", {"A1"}),
        ],
    )
    def test_syntaxes_reelles_du_site(self, tracks, attendu):
        assert positions_citees(tracks) == attendu

    @pytest.mark.parametrize("tracks", [None, "", "   "])
    def test_un_champ_vide_vaut_pour_TOUT_le_disque(self, tracks):
        """Le producteur exécutif, le label et le graphiste n'ont pas de piste :
        leur crédit doit rester sur tous les morceaux."""
        assert positions_citees(tracks) is None
        assert concerne_la_piste(tracks, "A1")

    @pytest.mark.parametrize("tracks", ["face B", "B3 to A1", "tout l'album"])
    def test_une_syntaxe_ILLISIBLE_ne_conclut_pas(self, tracks):
        """Refuser de conclure, jamais deviner : perdre un crédit juste parce
        qu'on n'a pas su lire une position serait pire que le défaut corrigé."""
        assert positions_citees(tracks) is None
        assert concerne_la_piste(tracks, "A1")


class TestConcerneLaPiste:
    def test_le_cas_qui_a_motive_le_lot(self):
        """« Mr Hudson — tracks: C2 » était écrit sur les 14 morceaux de
        *808s & Heartbreak*."""
        assert concerne_la_piste("C2", "C2")
        assert not concerne_la_piste("C2", "A1")

    def test_une_plage_couvre_ses_bornes(self):
        for position in ("B1", "B2", "B3"):
            assert concerne_la_piste("A2, B1 to B3, D2", position)
        assert not concerne_la_piste("A2, B1 to B3, D2", "B4")

    def test_la_casse_et_les_espaces_ne_comptent_pas(self):
        assert concerne_la_piste("A5, D4", " d4 ")

    def test_une_position_INCONNUE_garde_le_credit(self):
        """On ne sait pas où est le morceau dans le disque : on ne retire rien."""
        assert concerne_la_piste("C2", None)
        assert concerne_la_piste("C2", "")
