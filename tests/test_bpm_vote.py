"""Tests du vote BPM (data_enricher) — sanitization, concordance demi/double,
réconciliation par évidence (cas documentés dans la docstring de _reconcile_bpm).

Les méthodes n'utilisent self que pour des attributs de CLASSE : on les appelle
non liées (pas d'instanciation de DataEnricher, qui initialiserait les APIs).
"""

import pytest

from src.utils.data_enricher import DataEnricher


def _reconcile(candidates):
    return DataEnricher._reconcile_bpm(DataEnricher, candidates)


class TestSanitizeBpm:
    @pytest.mark.parametrize(
        ("brut", "attendu"),
        [
            (120, 120),
            ("120", 120),
            ("120.4", 120),
            (142.6, 143),  # arrondi
            (40, 40),  # borne basse incluse
            (220, 220),  # borne haute incluse
        ],
    )
    def test_valeurs_valides(self, brut, attendu):
        assert DataEnricher._sanitize_bpm(brut) == attendu

    @pytest.mark.parametrize("invalide", [None, "abc", "", 39, 221, 0, -100])
    def test_valeurs_invalides(self, invalide):
        assert DataEnricher._sanitize_bpm(invalide) is None


class TestBpmAgree:
    def test_egalite_et_tolerance(self):
        assert DataEnricher._bpm_agree(142, 142)
        assert DataEnricher._bpm_agree(142, 140)  # tolérance 3
        assert not DataEnricher._bpm_agree(142, 138)

    def test_demi_double_concordants(self):
        # 71 ≡ 142 : même tempo à l'octave près
        assert DataEnricher._bpm_agree(71, 142)
        assert DataEnricher._bpm_agree(142, 71)

    def test_tempos_differents(self):
        assert not DataEnricher._bpm_agree(100, 150)


class TestReconcileBpm:
    def test_sans_candidat(self):
        assert _reconcile([]) == (None, None, None, 0)

    def test_cas_1_deux_octaves_double_confirme(self):
        # 74 + 145 : une source confirme le double → valeur HAUTE mesurée
        bpm, alt, src, conf = _reconcile([("deezer", 74), ("reccobeats", 145)])
        assert bpm == 145
        assert alt == 74
        assert conf == 2
        assert src == "reccobeats+deezer"  # trié par fiabilité décroissante

    def test_cas_2_consensus_basse_bande_on_ne_double_pas(self):
        # 88 + 88 : consensus = vrai tempo, même sous le seuil half-time
        bpm, alt, src, conf = _reconcile([("getsongbpm", 88), ("deezer", 88)])
        assert bpm == 88
        assert alt == 176  # l'autre octave reste proposée
        assert conf == 2

    def test_cas_3_halftime_isole_double(self):
        # 71 seul, aucune preuve → convention rap : on double
        bpm, alt, src, conf = _reconcile([("deezer", 71)])
        assert bpm == 142
        assert alt == 71
        assert conf == 1

    def test_source_isolee_au_dessus_du_seuil_inchangee(self):
        bpm, alt, src, conf = _reconcile([("deezer", 120)])
        assert bpm == 120
        assert alt == 60
        assert conf == 1

    def test_le_vote_prime_sur_la_fiabilite(self):
        # 2 sources d'accord (100/101) battent 1 source plus fiable (150)
        bpm, alt, src, conf = _reconcile([("deezer", 100), ("songbpm", 101), ("reccobeats", 150)])
        assert bpm == 101  # valeur de la + fiable DU cluster gagnant
        assert conf == 2

    def test_a_egalite_de_vote_la_fiabilite_tranche(self):
        bpm, alt, src, conf = _reconcile([("deezer", 100), ("reccobeats", 150)])
        assert bpm == 150
        assert conf == 1
