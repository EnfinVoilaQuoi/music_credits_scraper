"""Étape 3 (2026-09-24) : corrections de fiches décidées à la main, et leur MÉMOIRE.

Le fichier `data/corrections/fiches.json` est relu par trois producteurs : le
gate Spotify (IDs retirés), `save_track` (crédits retirés) et l'import
discographie (pages fusionnées). Corriger la base sans cette mémoire ne tenait
qu'un run.
"""

import importlib.util
import json

import pytest

from src.models import Artist, Credit, CreditRole, Track
from src.utils import corrections_fiches as cf
from src.utils.spotify_identity import valider_identite


@pytest.fixture
def fichier(tmp_path, monkeypatch):
    chemin = tmp_path / "fiches.json"
    monkeypatch.setattr(cf, "FICHIER", chemin)

    def ecrire(donnees):
        chemin.write_text(json.dumps(donnees, ensure_ascii=False), encoding="utf-8")

    return ecrire


def _t(titre, artiste="A2H", genius_id=None, album=None, **kw):
    t = Track(title=titre, artist=Artist(name=artiste), album=album, **kw)
    t.genius_id = genius_id
    return t


class TestDesignation:
    def test_par_genius_id(self):
        assert cf.designe({"genius_id": 5}, genius_id=5, titre="x", album=None)
        assert not cf.designe({"genius_id": 5}, genius_id=6, titre="x", album=None)

    def test_par_titre_et_album_pour_une_fiche_deezer(self):
        d = {"titre": "Le cœur des filles (Unplugged)", "album": "Unplugged"}
        assert cf.designe(
            d, genius_id=None, titre="Le cœur des filles (Unplugged)", album="Unplugged"
        )
        assert not cf.designe(d, genius_id=None, titre="Le cœur des filles (Unplugged)", album="X")


def test_fichier_absent_ou_illisible_vaut_aucune_correction(fichier, tmp_path):
    assert cf.ids_refuses(_t("x")) == set()
    cf.FICHIER.write_text("{pas du json", encoding="utf-8")
    assert cf.ids_refuses(_t("x")) == set()


def test_memoires(fichier):
    fichier(
        {
            "A2H": [
                {
                    "fiche": {"genius_id": 1},
                    "retirer_id_spotify": ["SP1"],
                    "retirer_credits": [{"nom": "Matthieu Cabaret", "role": "Composer"}],
                },
                {"fiche": {"genius_id": 2}, "fusionner_dans": {"genius_id": 1}},
            ]
        }
    )
    fiche = _t("Le cœur des filles", genius_id=1)
    assert cf.ids_refuses(fiche) == {"SP1"}
    assert cf.credits_refuses(fiche) == {("matthieu cabaret", "Composer")}
    assert cf.genius_ids_absorbes("A2H") == {2}
    assert cf.ids_refuses(_t("Autre", genius_id=9)) == set()


def test_le_gate_refuse_un_id_retire_a_la_main(fichier):
    """Sinon Kworb ou le scraper le reposent au run suivant."""
    fichier({"Kanye West": [{"fiche": {"genius_id": 10723419}, "retirer_id_spotify": ["SPX"]}]})
    forever = _t("FOREVER", "Kanye West", genius_id=10723419)
    identite = {"name": "FOREVER", "artists": ["Kanye West"], "duration": None}
    assert valider_identite(forever, "SPX", lambda sid: identite) is False
    assert valider_identite(forever, "SPY", lambda sid: identite) is True


def test_save_track_n_ecrit_pas_un_credit_retire(fichier, data_manager):
    """Genius ressert « Matthieu Cabaret — Composer » (le compositing du clip)."""
    fichier(
        {
            "A2H": [
                {
                    "fiche": {"genius_id": 5963354},
                    "retirer_credits": [{"nom": "Matthieu Cabaret", "role": "Composer"}],
                }
            ]
        }
    )
    a = Artist(name="A2H")
    a.id = data_manager.save_artist(a)
    t = Track(title="Le cœur des filles", artist=a)
    t.genius_id = 5963354
    t.credits = [
        Credit(name="Matthieu Cabaret", role=CreditRole.COMPOSER, source="genius"),
        Credit(name="Clyde Bessi", role=CreditRole.PRODUCER, source="genius"),
    ]
    data_manager.save_track(t)
    (relu,) = data_manager.get_artist_tracks(a.id)
    assert [c.name for c in relu.credits] == ["Clyde Bessi"]


def test_forget_credit(data_manager):
    a = Artist(name="A2H")
    a.id = data_manager.save_artist(a)
    t = Track(title="X", artist=a)
    t.credits = [Credit(name="Alice Bouet", role=CreditRole.VIDEO_DIRECTOR, source="genius")]
    tid = data_manager.save_track(t)
    assert data_manager.forget_credit(tid, "alice bouet", "Video Director") == 1
    assert data_manager.get_artist_tracks(a.id)[0].credits == []


# ── Départage d'un ID partagé (scripts/appliquer_corrections_fiches.py) ─────


def _script():
    spec = importlib.util.spec_from_file_location(
        "appliquer_corrections_fiches", "scripts/appliquer_corrections_fiches.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestDepartage:
    def test_l_artiste_principal_departage(self):
        """Même titre, même durée (venue de l'ID) : c'est l'artiste crédité par
        Spotify qui dit à qui est l'ID."""
        mod = _script()
        a2h = _t("Pour de vrai", duration=240)
        eech = _t("Pour de Vrai", duration=240, is_featuring=True, primary_artist_name="Eech")
        identite = {"name": "Pour de vrai", "artists": ["A2h"], "duration": 241}
        assert mod.score_identite(a2h, identite) > mod.score_identite(eech, identite)

    def test_la_casse_du_titre_departage_en_dernier(self):
        mod = _script()
        ancien = _t("outside", "Travis Scott", duration=176)
        recent = _t("OUTSIDE", "Travis Scott", duration=176)
        identite = {"name": "outside", "artists": ["Travis Scott"], "duration": 176}
        assert mod.score_identite(ancien, identite) > mod.score_identite(recent, identite)

    def test_egalite_parfaite_ne_tranche_pas(self):
        mod = _script()
        a = _t("Intro", "SDM", duration=100)
        b = _t("Intro", "SDM", duration=100)
        a.spotify_id = b.spotify_id = "SP"
        identite = {"name": "Intro", "artists": ["SDM"], "duration": 100}
        ((sid, perdant, motif),) = mod.departager(a, b, lambda s: identite)
        assert perdant is None and "à trancher" in motif


def test_la_designation_par_titre_est_exacte():
    """Normalisée, « Boss » désignait aussi « BOSS » : une fois « Boss » fusionnée,
    une relance aurait fusionné « BOSS » dans elle-même, donc supprimée."""
    d = {"titre": "Boss", "album": "Échecs positifs"}
    assert cf.designe(d, genius_id=None, titre="Boss", album="Échecs positifs")
    assert not cf.designe(d, genius_id=638651, titre="BOSS", album="Échecs positifs")


def test_le_premier_credite_departage():
    """« Selfish » : Spotify crédite Slum Village, John Legend, Kanye West — la
    fiche de Slum Village garde l'ID, pas celle de DONDA 2."""
    mod = _script()
    slum = _t("Selfish", "Kanye West", is_featuring=True, primary_artist_name="Slum Village")
    donda = _t("Selfish", "Kanye West")
    identite = {"name": "Selfish", "artists": ["Slum Village", "John Legend", "Kanye West"]}
    assert mod.score_identite(slum, identite) > mod.score_identite(donda, identite)
