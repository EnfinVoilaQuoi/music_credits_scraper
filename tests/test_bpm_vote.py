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

    def test_cas_1_deux_octaves_sans_consensus(self):
        # 74 + 145 : une voix contre une (Deezer en BAS) → valeur HAUTE mesurée
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

    def test_cas_3_source_isolee_gardee_telle_quelle(self):
        # 71 seul : plus de « convention rap » qui doublait (retirée le
        # 2026-10-30, 916 fiches doublées contre ce que mesurent les sources).
        bpm, alt, src, conf = _reconcile([("deezer", 71)])
        assert bpm == 71
        assert alt == 142  # l'autre octave reste proposée
        assert conf == 1

    def test_une_seule_voix_sous_90_gardee(self):
        # Isha « Gambinos » : SongBPM seul à 74 (avant : doublé, 148).
        bpm, alt, _, conf = _reconcile([("songbpm", 74)])
        assert (bpm, alt, conf) == (74, 148, 1)

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


class TestOctaveDuConsensus:
    """Deux octaves dans le cluster : le verdict suit le consensus INDÉPENDANT
    (décision utilisateur 2026-10-30, « la même convention partout »). Cas
    réels de la base ; Deezer double ~11 % de ses valeurs comparables."""

    def test_deezer_seul_en_haut_contre_reccobeats_et_songbpm(self):
        # A2H « Petit Cœur » : ReccoBeats et SongBPM ne font qu'UNE voix,
        # Deezer seul ne fait pas monter l'octave (avant : 150).
        bpm, alt, src, conf = _reconcile([("deezer", 150), ("reccobeats", 75), ("songbpm", 75)])
        assert (bpm, alt) == (75, 150)
        assert conf == 3

    def test_deux_voix_en_bas_contre_deezer(self):
        # Josman « Narcisse » : GetSongBPM 79, ReccoBeats 80, Deezer 158.
        bpm, alt, _, _ = _reconcile([("deezer", 158), ("getsongbpm", 79), ("reccobeats", 80)])
        assert (bpm, alt) == (80, 158)  # la plus fiable de l'octave basse

    def test_deezer_seul_contre_une_voix(self):
        # Flynt « Rap théorie » : une voix contre une, mais Deezer seul en
        # haut ne fait jamais monter l'octave (avant : 174).
        bpm, alt, _, _ = _reconcile([("deezer", 174), ("reccobeats", 87)])
        assert (bpm, alt) == (87, 174)

    def test_la_majorite_basse_contre_une_autre_source_que_deezer(self):
        # MC Solaar « Dix de mes disciples » : Deezer et GetSongBPM 98,
        # ReccoBeats 196 (avant : 196).
        bpm, alt, _, _ = _reconcile([("deezer", 98), ("getsongbpm", 98), ("reccobeats", 196)])
        assert (bpm, alt) == (98, 196)

    def test_reccobeats_et_songbpm_partages_ne_comptent_pas(self):
        # Kanye West « D.O.A. » : ReccoBeats 87, SongBPM 172 — leur voix est
        # aux deux octaves, elle ne compte pour aucune ; reste Deezer 86.
        bpm, _, _, _ = _reconcile([("deezer", 86), ("reccobeats", 87), ("songbpm", 172)])
        assert bpm == 87

    def test_la_majorite_haute_est_suivie(self):
        # Flynt « Un pour la plume » : trois sources à 175, Deezer à 88.
        bpm, alt, _, _ = _reconcile(
            [("deezer", 88), ("getsongbpm", 175), ("reccobeats", 175), ("songbpm", 175)]
        )
        assert (bpm, alt) == (175, 88)

    def test_deezer_accompagne_fait_monter(self):
        # Booba « Jimmy » : Deezer ET GetSongBPM 164 contre ReccoBeats 82 —
        # Deezer n'est pas seul, la majorité est haute.
        bpm, alt, _, _ = _reconcile([("deezer", 164), ("getsongbpm", 164), ("reccobeats", 82)])
        assert (bpm, alt) == (164, 82)

    def test_reccobeats_et_songbpm_une_seule_voix_sans_consensus(self):
        # Booba « Monde » : GetSongBPM 160 contre ReccoBeats et SongBPM 80 —
        # une voix contre une (et non deux contre une) : la fiabilité tranche,
        # ReccoBeats ; Deezer 107 est hors du cluster.
        bpm, alt, _, _ = _reconcile(
            [("deezer", 107), ("getsongbpm", 160), ("reccobeats", 80), ("songbpm", 80)]
        )
        assert (bpm, alt) == (80, 160)

    def test_sans_consensus_la_source_la_plus_fiable(self):
        # Josman « BIZ » : GetSongBPM 159, ReccoBeats 80 — une contre une ;
        # plus d'octave haute d'office (avant le 2026-10-30 : 159).
        bpm, alt, _, _ = _reconcile([("getsongbpm", 159), ("reccobeats", 80)])
        assert (bpm, alt) == (80, 159)

    def test_sans_consensus_la_haute_si_la_plus_fiable_y_est(self):
        # Kid Cudi « My World » : GetSongBPM 74, ReccoBeats 148.
        bpm, alt, _, _ = _reconcile([("getsongbpm", 74), ("reccobeats", 148)])
        assert (bpm, alt) == (148, 74)


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

    def test_deux_octaves_ne_sont_pas_un_consensus(self):
        # Flynt « Rap théorie » : Deezer 174, ReccoBeats 87 — le départage
        # SongBPM n'est pas sauté (avant : « consensus » de deux sources).
        ballot = BpmBallot()
        ballot.add("deezer", 174)
        ballot.add("reccobeats", 87)
        assert ballot.consensus_reached() is False

    def test_reccobeats_et_songbpm_ne_font_qu_une_voix(self):
        ballot = BpmBallot()
        ballot.add("reccobeats", 88)
        ballot.add("songbpm", 88)
        assert ballot.consensus_reached() is False
        ballot.add("getsongbpm", 88)
        assert ballot.consensus_reached() is True
