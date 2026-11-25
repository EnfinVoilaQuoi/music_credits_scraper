"""Une correction humaine se remplace (2026-11-19) : l'original désigné du
live « Come Again (feat. Big Red) » passe de la version studio au remix."""

import pytest

from src.utils import corrections_fiches as cf


def test_la_valeur_d_une_correction_se_remplace(data_manager):
    fiche = {"titre": "Come Again (feat. Big Red) (Live)", "album": "La der"}
    cf.memoriser_contenus("Suprême NTM", fiche, [{"genius_id": 898195}])
    assert cf.remplacer_correction("Suprême NTM", fiche, "contient", [{"genius_id": 4668643}])
    (entree,) = cf.corrections_en_base()["Suprême NTM"]
    assert entree["contient"] == [{"genius_id": 4668643}]


def test_sans_correction_consignee_rien(data_manager):
    assert not cf.remplacer_correction("X", {"genius_id": 1}, "version_de", {"genius_id": 2})


def test_une_action_inconnue_est_refusee(data_manager):
    with pytest.raises(ValueError):
        cf.remplacer_correction("X", {"genius_id": 1}, "inventee", 1)


def test_une_correction_perimee_se_retire(data_manager):
    fiche = {"titre": "New God Flow", "album": None}
    cf.memoriser_contenus("Kanye West", fiche, [{"genius_id": 79864}])
    assert cf.date_de_correction("Kanye West", fiche, "contient")
    assert cf.oublier_correction("Kanye West", fiche, "contient")
    assert cf.corrections_en_base() == {}
    assert not cf.oublier_correction("Kanye West", fiche, "contient")


class TestRejeuPerime:
    """2026-11-24 : une fusion du 12/10, rejouée, a absorbé la fiche « New God
    Flow » créée à la main le 22/11."""

    def _rejouer(self, dm, decidee_le, saisie=False):
        import importlib.util
        from pathlib import Path

        from sqlalchemy import text

        from src.models import Artist, Track

        chemin = Path(__file__).parent.parent / "scripts" / "appliquer_corrections_fiches.py"
        spec = importlib.util.spec_from_file_location("acf_test", chemin)
        acf = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(acf)
        a = Artist(name="Kanye West")
        a.id = dm.save_artist(a)
        garde, perdue = Track(title="New God Flow.1", artist=a), Track(
            title="New God Flow", artist=a
        )
        garde.genius_id = 79864
        for t in (garde, perdue):
            dm.save_track(t)
        entree = {
            "fiche": {"titre": "New God Flow", "album": None},
            "fusionner_dans": {"genius_id": 79864},
        }
        if not saisie:
            with dm.engine.begin() as conn:
                cf._inserer_correction(
                    conn,
                    "Kanye West",
                    entree["fiche"],
                    "fusionner_dans",
                    entree["fusionner_dans"],
                    decidee_le,
                )
        journal = []
        acf.appliquer(acf._Contexte(dm, True), "Kanye West", entree, None, journal)
        with dm.engine.connect() as conn:
            reste = conn.execute(
                text("SELECT COUNT(*) FROM tracks WHERE id = :t"), {"t": perdue.id}
            ).scalar()
        return bool(reste), journal

    def test_une_fiche_plus_recente_que_la_decision_n_est_pas_absorbee(self, data_manager):
        reste, journal = self._rejouer(data_manager, "2020-10-12T15:30:03")
        assert reste and "APRÈS" in journal[0]

    def test_une_decision_posterieure_fusionne(self, data_manager):
        reste, _ = self._rejouer(data_manager, "2099-01-01T00:00:00")
        assert not reste

    def test_une_saisie_neuve_fusionne(self, data_manager):
        reste, _ = self._rejouer(data_manager, None, saisie=True)
        assert not reste
