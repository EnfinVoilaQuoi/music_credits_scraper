"""Tests du vote BPM (src/utils/bpm_vote) — sanitization, concordance demi/double,
réconciliation par évidence (cas documentés dans la docstring de reconcile_bpm).

Logique pure extraite de DataEnricher : on teste désormais le module bpm_vote
directement. Les asserts de SÉMANTIQUE (§8.3 JOURNAL) sont figés à l'identique.
"""

import pytest

from src.utils.bpm_vote import BpmBallot, bpm_agree, reconcile_bpm, sanitize_bpm


def _reconcile(candidates):
    return reconcile_bpm(candidates)


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
        assert sanitize_bpm(brut) == attendu

    @pytest.mark.parametrize("invalide", [None, "abc", "", 39, 221, 0, -100])
    def test_valeurs_invalides(self, invalide):
        assert sanitize_bpm(invalide) is None


class TestBpmAgree:
    def test_egalite_et_tolerance(self):
        assert bpm_agree(142, 142)
        assert bpm_agree(142, 140)  # tolérance 3
        assert not bpm_agree(142, 138)

    def test_demi_double_concordants(self):
        # 71 ≡ 142 : même tempo à l'octave près
        assert bpm_agree(71, 142)
        assert bpm_agree(142, 71)

    def test_tempos_differents(self):
        assert not bpm_agree(100, 150)


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


class TestBpmBallot:
    """Le scrutin encapsule collecte + réconciliation + écriture sur le track."""

    def test_add_ignore_les_valeurs_invalides(self):
        ballot = BpmBallot()
        ballot.add("deezer", None)
        ballot.add("deezer", "abc")
        ballot.add("deezer", 900)  # hors borne
        assert ballot.candidates == []

    def test_consensus_reached(self):
        ballot = BpmBallot()
        ballot.add("deezer", 120)
        assert ballot.consensus_reached() is False
        ballot.add("getsongbpm", 121)  # concordant
        assert ballot.consensus_reached() is True

    def test_finalize_pose_le_bpm_et_vide_le_scrutin(self):
        from src.models.track import Track

        track = Track(title="X")
        ballot = BpmBallot()
        ballot.add("deezer", 74)
        ballot.add("reccobeats", 145)
        ballot.finalize(track)
        assert track.bpm == 145
        assert track.bpm_alt == 74
        assert track.bpm_source == "reccobeats+deezer"
        assert track.bpm_confidence == 2
        assert ballot.candidates == []  # vidé après finalize

    def test_finalize_sans_candidat_ne_touche_pas_le_bpm(self):
        from src.models.track import Track

        track = Track(title="X", bpm=99)
        BpmBallot().finalize(track)
        assert track.bpm == 99
