"""Accesseurs `ArtistRepository` : chemin nominal ET chemin d'erreur.

Ces méthodes suivent toutes le même contrat — en cas d'échec DB, elles logguent
et rendent une valeur de repli (`None`, `False`, `{}`, `(None, None)`, `[]`)
plutôt que de laisser l'exception remonter jusqu'à la GUI. Ce contrat n'était
vérifié nulle part : les `except` correspondants étaient les lignes non
couvertes de `src/utils/artist_repository.py`.

Le couvrir est le PRÉ-REQUIS pour resserrer ces `except Exception` en
`except SQLAlchemyError` (cliquet `per-file-ignores` de `pyproject.toml`) : ce
sont ces tests qui disent que le repli fonctionne toujours après resserrage.
"""

from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from src.models import Artist


@pytest.fixture
def artiste(data_manager):
    a = Artist(name="ISHA")
    a.id = data_manager.save_artist(a)
    return a


@pytest.fixture
def moteur_casse(monkeypatch):
    """Remplace le moteur par un objet qui échoue à l'ouverture de connexion.

    C'est la panne la plus représentative (base verrouillée, fichier disparu) et
    elle atteint les deux formes utilisées par le repository : `connect()` pour
    les lectures, `begin()` pour les écritures.
    """

    class _MoteurCasse:
        def connect(self):
            raise SQLAlchemyError("base indisponible")

        def begin(self):
            raise SQLAlchemyError("base indisponible")

    def _installer(dm):
        # `DataManager.engine` est une propriété en lecture seule qui délègue à
        # `Database` : c'est là qu'il faut poser la panne.
        monkeypatch.setattr(dm._db, "engine", _MoteurCasse())

    return _installer


class TestCanalYtm:
    """Le canal YTMusic épinglé résout les homonymes : `source` distingue une
    saisie GUI (jamais écrasée) d'un vote (ré-effaçable)."""

    def test_epingler_puis_relire(self, data_manager, artiste):
        assert data_manager.set_artist_ytm_channel(artiste.id, "UC123") is True
        assert data_manager.get_artist_ytm_channel(artiste.id) == "UC123"
        assert data_manager.get_artist_ytm_channel_info(artiste.id) == ("UC123", "manual")

    def test_source_inferee_conservee(self, data_manager, artiste):
        data_manager.set_artist_ytm_channel(artiste.id, "UC456", source="inferred")
        assert data_manager.get_artist_ytm_channel_info(artiste.id) == ("UC456", "inferred")

    def test_sans_canal(self, data_manager, artiste):
        assert data_manager.get_artist_ytm_channel(artiste.id) is None
        assert data_manager.get_artist_ytm_channel_info(artiste.id) == (None, None)

    def test_artiste_inexistant(self, data_manager):
        assert data_manager.get_artist_ytm_channel(999_999) is None
        assert data_manager.get_artist_ytm_channel_info(999_999) == (None, None)

    def test_desepinglage(self, data_manager, artiste):
        """Le gate d'identité efface un canal INFÉRÉ suspect pour ne pas figer
        l'erreur : les deux colonnes repassent à NULL."""
        data_manager.set_artist_ytm_channel(artiste.id, "UC456", source="inferred")

        assert data_manager.clear_artist_ytm_channel(artiste.id) is True
        assert data_manager.get_artist_ytm_channel_info(artiste.id) == (None, None)

    def test_replis_si_la_base_est_indisponible(self, data_manager, artiste, moteur_casse):
        moteur_casse(data_manager)

        assert data_manager.get_artist_ytm_channel(artiste.id) is None
        assert data_manager.get_artist_ytm_channel_info(artiste.id) == (None, None)
        assert data_manager.set_artist_ytm_channel(artiste.id, "UC1") is False
        assert data_manager.clear_artist_ytm_channel(artiste.id) is False


class TestTotauxKworb:
    def test_ecriture_puis_relecture(self, data_manager, artiste):
        ok = data_manager.update_artist_kworb_totals(
            artiste.id, total=1234, daily=56, kworb_date="2026-01-02"
        )
        assert ok is True

        # `get_artist_details` n'expose pas les totaux Kworb : relecture directe.
        with data_manager.engine.connect() as conn:
            row = conn.execute(
                text("SELECT kworb_total_streams, kworb_daily_streams FROM artists WHERE id = :i"),
                {"i": artiste.id},
            ).first()
        assert row == (1234, 56)

    def test_les_champs_absents_ne_sont_pas_ecrases(self, data_manager, artiste):
        """`coalesce` : une MàJ partielle (seul `daily` connu) doit préserver le
        total déjà en base — sinon un run Kworb incomplet effacerait l'acquis."""
        data_manager.update_artist_kworb_totals(artiste.id, total=1000, daily=10)
        data_manager.update_artist_kworb_totals(artiste.id, daily=20)

        with data_manager.engine.connect() as conn:
            row = conn.execute(
                text("SELECT kworb_total_streams, kworb_daily_streams FROM artists WHERE id = :i"),
                {"i": artiste.id},
            ).first()
        assert row == (1000, 20)

    def test_repli_si_la_base_est_indisponible(self, data_manager, artiste, moteur_casse):
        moteur_casse(data_manager)
        assert data_manager.update_artist_kworb_totals(artiste.id, total=1) is False


class TestAuditeursMensuels:
    def test_ecriture_et_historique(self, data_manager, artiste):
        assert data_manager.update_artist_monthly_listeners(artiste.id, 10_000) is True
        assert data_manager.update_artist_monthly_listeners(artiste.id, 12_000, 3_000) is True

        historique = data_manager.get_monthly_listeners_history(artiste.id)
        assert len(historique) == 2
        assert {h["spotify_listeners"] for h in historique} == {10_000, 12_000}

    def test_historique_vide(self, data_manager, artiste):
        assert data_manager.get_monthly_listeners_history(artiste.id) == []

    def test_replis_si_la_base_est_indisponible(self, data_manager, artiste, moteur_casse):
        moteur_casse(data_manager)

        assert data_manager.update_artist_monthly_listeners(artiste.id, 1) is False
        assert data_manager.get_monthly_listeners_history(artiste.id) == []


class TestIdentifiantsEtImage:
    def test_spotify_id(self, data_manager, artiste):
        assert data_manager.update_artist_spotify_id(artiste.id, "sp0tify") is True
        assert data_manager.get_artist_by_name("ISHA").spotify_id == "sp0tify"

    def test_chemin_dimage(self, data_manager, artiste):
        assert data_manager.set_artist_image_path(artiste.id, "data/images/isha.jpg") is True

    def test_replis_si_la_base_est_indisponible(self, data_manager, artiste, moteur_casse):
        moteur_casse(data_manager)

        assert data_manager.update_artist_spotify_id(artiste.id, "x") is False
        assert data_manager.set_artist_image_path(artiste.id, "x.jpg") is False


class TestLecturesDArtiste:
    def test_artiste_absent(self, data_manager):
        assert data_manager.get_artist_by_name("Inconnu") is None

    def test_details_dun_artiste_absent(self, data_manager):
        assert data_manager.get_artist_details("Inconnu") == {}

    def test_replis_si_la_base_est_indisponible(self, data_manager, artiste, moteur_casse):
        moteur_casse(data_manager)

        assert data_manager.get_artist_by_name("ISHA") is None
        assert data_manager.get_artist_details("ISHA") == {}

    def test_suppression_dun_artiste_absent(self, data_manager):
        assert data_manager.delete_artist("Inconnu") is False

    def test_suppression_repli_si_la_base_est_indisponible(
        self, data_manager, artiste, moteur_casse
    ):
        moteur_casse(data_manager)
        assert data_manager.delete_artist("ISHA") is False


class TestDatesLibres:
    """Piège TIMESTAMP double-face : le type refuse une string en écriture et
    parse en `datetime` en lecture. Les colonnes date « libres » passent donc par
    `date_bind` (ou du `text()` non typé) — les deux formes doivent être acceptées."""

    @pytest.mark.parametrize("valeur", ["2026-01-02 03:04:05", datetime(2026, 1, 2, 3, 4, 5)])
    def test_kworb_accepte_string_et_datetime(self, data_manager, artiste, valeur):
        assert (
            data_manager.update_artist_kworb_totals(artiste.id, total=1, kworb_date=valeur) is True
        )
