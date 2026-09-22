"""La PRÉCISION d'une date de sortie (lot 3, 2026-09-22).

Genius est en tête de l'ordre des sources et fabrique `datetime(année, 1, 1)`
dès que `release_date_components` n'a que l'année — **719 dates en base tombent
au 1ᵉʳ janvier**. Le suivre aveuglément fait perdre la vraie date de Deezer.

Le choix qui décide de tout : la précision est la FORME de la valeur
(`"2018"` / `"2018-05"` / `"2018-05-14"`), déclarée par le PRODUCTEUR ; la
colonne `tracks.release_date`, elle, reste toujours une date complète.
"""

from datetime import datetime

import pytest

from src.enrichment.observation import Observation
from src.enrichment.reconcile import (
    MANUAL_SOURCE,
    resoudre_date_de_sortie,
)
from src.utils import dates


class TestPrecision:
    @pytest.mark.parametrize(
        ("valeur", "attendu"),
        [
            ("2018", dates.ANNEE),
            ("2018-05", dates.MOIS),
            ("2018-05-14", dates.JOUR),
            ("2018-05-14T00:00:00Z", dates.JOUR),
            (datetime(2018, 5, 14), dates.JOUR),
            (None, None),
            ("pas une date", None),
            (12345, None),
        ],
    )
    def test_la_forme_porte_la_precision(self, valeur, attendu):
        assert dates.precision(valeur) is attendu or dates.precision(valeur) == attendu

    def test_un_datetime_vaut_TOUJOURS_le_jour(self):
        """Il ne porte pas sa précision : c'est au producteur de la déclarer.
        C'est pourquoi les 719 `YYYY-01-01` déjà en base ne sont pas
        retronqués — rien ne les distingue d'un vrai 1ᵉʳ janvier."""
        assert dates.precision(datetime(2018, 1, 1)) == dates.JOUR


class TestFormes:
    @pytest.mark.parametrize(
        ("valeur", "attendu"),
        [
            (datetime(2018, 5, 14), "2018-05-14"),
            ("2018-05-14 00:00:00", "2018-05-14"),
            ("2018-05", "2018-05"),
            ("2018", "2018"),
        ],
    )
    def test_forme_canonique_d_une_observation(self, valeur, attendu):
        assert dates.normaliser_observation(valeur) == attendu

    @pytest.mark.parametrize(
        ("valeur", "attendu"),
        [("2018", "2018-01-01"), ("2018-05", "2018-05-01"), ("2018-05-14", "2018-05-14")],
    )
    def test_la_COLONNE_reste_une_date_complete(self, valeur, attendu):
        """Contrat avec la GUI, le tri, `albums_grouping`, `artist_loader`
        (`[:4]`) et la Timeline du dépôt privé."""
        complete = dates.completer(valeur)
        assert complete == attendu and len(complete) == 10


class TestMemeJour:
    def test_tolere_les_formats_et_les_precisions(self):
        assert dates.meme_jour("2018-05-14", datetime(2018, 5, 14))
        assert dates.meme_jour("2018-05-14 00:00:00", "2018-05-14")
        assert dates.meme_jour("2018", "2018-01-01")

    def test_deux_jours_differents(self):
        assert not dates.meme_jour("2018-05-14", "2018-05-15")
        assert not dates.meme_jour(None, "2018-05-14")


class TestLaPlusAncienne:
    def test_entre_deux_editions_la_premiere_gagne(self):
        """« Loto » : single le 02/05/2018, album J.O.$ le 14/09. La colonne du
        MORCEAU porte la date de l'enregistrement."""
        assert dates.la_plus_ancienne("2018-09-14", "2018-05-02") == "2018-05-02"

    @pytest.mark.parametrize(
        ("a", "b"), [("2018", "2018-05-14"), ("2018-05-14", "2018"), ("2018-05", "2018-05-14")]
    )
    def test_une_date_MOINS_PRECISE_n_est_pas_une_date_ANTERIEURE(self, a, b):
        """« 2018-05-14 » RAFFINE « 2018 », il ne lui succède pas. Comparer les
        formes complétées ferait gagner le 1ᵉʳ janvier et perdrait le jour."""
        assert dates.la_plus_ancienne(a, b) == "2018-05-14"

    def test_des_periodes_DISJOINTES_se_departagent_par_l_anteriorite(self):
        assert dates.la_plus_ancienne("2017", "2018-05") == "2017"

    def test_une_absence_ne_gagne_jamais(self):
        assert dates.la_plus_ancienne(None, "2019-02-01") == "2019-02-01"
        assert dates.la_plus_ancienne("2019-02-01", None) == "2019-02-01"
        assert dates.la_plus_ancienne(None, None) is None


class TestResolveur:
    def test_la_PRECISION_passe_devant_l_ordre_des_sources(self):
        """Le cas qui motive le lot : Genius est premier, mais son 1ᵉʳ janvier
        est fabriqué."""
        verdict = resoudre_date_de_sortie(
            [
                Observation("release_date", "2018", "genius"),
                Observation("release_date", "2018-05-02", "deezer"),
            ]
        )
        assert verdict.value == "2018-05-02" and verdict.source == "deezer"

    def test_a_precision_EGALE_l_ordre_tranche(self):
        verdict = resoudre_date_de_sortie(
            [
                Observation("release_date", "2018-05-02", "deezer"),
                Observation("release_date", "2018-05-14", "genius"),
            ]
        )
        assert verdict.source == "genius"

    def test_manual_court_circuite_meme_moins_precis(self):
        verdict = resoudre_date_de_sortie(
            [
                Observation("release_date", "2018-05-02", "deezer"),
                Observation("release_date", "2017", MANUAL_SOURCE),
            ]
        )
        assert verdict.value == "2017" and verdict.source == MANUAL_SOURCE

    def test_legacy_ne_sert_que_SEUL(self):
        seul = resoudre_date_de_sortie([Observation("release_date", "2018-01-01", "legacy")])
        assert seul.value == "2018-01-01" and seul.source == "legacy"
        avec = resoudre_date_de_sortie(
            [
                Observation("release_date", "2018-01-01", "legacy"),
                Observation("release_date", "2018", "genius"),
            ]
        )
        assert avec.source == "genius"

    def test_aucune_observation_lisible(self):
        assert resoudre_date_de_sortie([]) is None
        assert resoudre_date_de_sortie([Observation("release_date", None, "genius")]) is None

    def test_une_source_hors_de_l_ordre_ne_fait_pas_perdre_la_donnee(self):
        verdict = resoudre_date_de_sortie([Observation("release_date", "2018-05-14", "inconnue")])
        assert verdict.value == "2018-05-14"


def test_les_DEUX_chemins_d_arbitrage_partagent_la_MEME_fonction():
    """`reconcile()` (enrichissement) et `_arbitrer_discographie` (écriture en
    base) doivent rendre le même verdict : en brancher un seul ferait dépendre
    la date du flux qui a écrit. Gel par IDENTITÉ de fonction, comme
    `test_cert_referentiels_uniques`."""
    import inspect

    from src.enrichment import reconcile as module_reconcile
    from src.utils import track_repository

    source = inspect.getsource(track_repository.TrackRepository._arbitrer_discographie)
    assert "resoudre_date_de_sortie" in source, (
        "`_arbitrer_discographie` doit appeler la stratégie de date, pas "
        "`resolve_by_priority` (dont le repli départage par la fiabilité BPM)."
    )
    assert callable(module_reconcile.resoudre_date_de_sortie)


class TestGeniusDeclareSaPrecision:
    @pytest.mark.parametrize(
        ("composants", "attendu"),
        [
            ({"year": 2018, "month": 5, "day": 14}, "2018-05-14"),
            ({"year": 2018, "month": 5}, "2018-05"),
            ({"year": 2018}, "2018"),
            ({}, None),
            ({"year": None}, None),
            ({"year": "mauvais"}, None),
        ],
    )
    def test_la_precision_reellement_connue(self, composants, attendu):
        from src.api.genius_api import GeniusAPI

        assert GeniusAPI.precision_de_la_date({"release_date_components": composants}) == attendu

    def test_sans_composants(self):
        from src.api.genius_api import GeniusAPI

        assert GeniusAPI.precision_de_la_date({}) is None
