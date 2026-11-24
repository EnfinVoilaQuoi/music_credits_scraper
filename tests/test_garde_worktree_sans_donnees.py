"""Garde du worktree sans données (2026-11-23, `src/utils/donnees_lues.py`).

Dans un worktree git, `data/` est vide : une mesure y lisait un brut vide et
aucune page désactivée, SANS RIEN DIRE. La lecture lève désormais
`DonneesAbsentes`, et `DONNEES_LUES_DIR` fait lire le `data/` réel, sans y
écrire. Le worktree figé d'un lot (jonction `data` vers le vrai dossier) a le
brut : la garde s'y tait.
"""

from __future__ import annotations

import pytest

from src import config
from src.persistence import brut
from src.utils import disabled_tracks_manager as dtm
from src.utils import donnees_lues
from src.utils.donnees_lues import DonneesAbsentes


def _contenu(dossier):
    """Les fichiers de `dossier` (hors `-shm` / `-wal` qu'une lecture SQLite
    d'une base en WAL laisse à côté) et leurs octets."""
    return {
        p: p.read_bytes()
        for p in dossier.rglob("*")
        if p.is_file() and not p.name.endswith(("-shm", "-wal"))
    }


def _depot(racine, *, worktree: bool, avec_brut: bool):
    """Un dépôt factice : `.git` fichier (worktree) ou dossier, `data/` avec
    ou sans le brut."""
    racine.mkdir(parents=True, exist_ok=True)
    if worktree:
        (racine / ".git").write_text("gitdir: ailleurs\n", encoding="utf-8")
    else:
        (racine / ".git").mkdir()
    data = racine / "data"
    data.mkdir()
    if avec_brut:
        (data / "brut").mkdir()
        (data / "brut" / "brut.db").write_bytes(b"")
    return data


@pytest.fixture
def code_dans(monkeypatch):
    """Fait croire que le code tourne depuis `racine` (configuration, brut et
    gestionnaire des pages désactivées sur SON `data/`, non redirigés)."""

    def _poser(racine, data):
        monkeypatch.setattr(config, "BASE_DIR", racine)
        monkeypatch.setattr(config, "DATA_DIR", data)
        monkeypatch.setattr(config.settings, "donnees_lues_dir", "")
        chemin = data / "brut" / "brut.db"
        monkeypatch.setattr(brut, "CHEMIN", chemin)
        monkeypatch.setattr(brut, "_CHEMIN_CONFIG", chemin)
        monkeypatch.setattr(dtm, "DATA_DIR", data)
        monkeypatch.setattr(dtm, "_DATA_DIR_CONFIG", data)

    return _poser


def test_un_worktree_sans_brut_leve(tmp_path, code_dans):
    data = _depot(tmp_path / "wt", worktree=True, avec_brut=False)
    code_dans(tmp_path / "wt", data)
    with pytest.raises(DonneesAbsentes, match="DONNEES_LUES_DIR"):
        brut.requetes_gardees("deezer")
    with pytest.raises(DonneesAbsentes):
        brut.lectures("deezer", "https://api.deezer.com/track/1")
    with pytest.raises(DonneesAbsentes):
        dtm.DisabledTracksManager().load_disabled_tracks("Booba")


def test_l_ecriture_du_brut_ne_cree_pas_un_brut_vide_qui_ferait_taire_la_garde(tmp_path, code_dans):
    data = _depot(tmp_path / "wt", worktree=True, avec_brut=False)
    code_dans(tmp_path / "wt", data)
    brut.fermer()
    brut.consigner("deezer", "https://api.deezer.com/track/1", 200, b"{}")  # ne lève pas
    assert not (data / "brut" / "brut.db").exists()
    with pytest.raises(DonneesAbsentes):
        brut.requetes_gardees("deezer")


def test_le_worktree_d_un_lot_a_le_brut_et_se_tait(tmp_path, code_dans):
    """La jonction `data` du lot : le brut est là, rien ne lève."""
    data = _depot(tmp_path / "lot", worktree=True, avec_brut=True)
    code_dans(tmp_path / "lot", data)
    assert donnees_lues.dossier_des_donnees() == (data, False)
    assert brut.chemin_lu() == data / "brut" / "brut.db"
    assert dtm.DisabledTracksManager().load_disabled_tracks("Booba") == set()


def test_le_depot_principal_ne_change_pas(tmp_path, code_dans):
    data = _depot(tmp_path / "depot", worktree=False, avec_brut=False)
    code_dans(tmp_path / "depot", data)
    assert brut.requetes_gardees("deezer") == set()
    assert dtm.DisabledTracksManager().load_disabled_tracks("Booba") == set()


def test_donnees_lues_dir_fait_lire_le_data_reel_sans_y_ecrire(tmp_path, code_dans, monkeypatch):
    reel = tmp_path / "principal" / "data"
    reel.mkdir(parents=True)
    monkeypatch.setattr(brut, "CHEMIN", reel / "brut" / "brut.db")
    brut.fermer()
    brut.consigner("deezer", "https://api.deezer.com/track/1", 200, b'{"id": 1}')
    brut.fermer()
    (reel / "disabled_tracks").mkdir()
    gestionnaire = dtm.DisabledTracksManager()
    gestionnaire.disabled_tracks_dir = reel / "disabled_tracks"
    gestionnaire.desactiver("Booba", [7], "page_vide", "regle:page_vide")
    avant = _contenu(reel)

    data = _depot(tmp_path / "wt", worktree=True, avec_brut=False)
    code_dans(tmp_path / "wt", data)
    monkeypatch.setattr(config.settings, "donnees_lues_dir", str(reel))
    assert brut.chemin_lu() == reel / "brut" / "brut.db"
    assert brut.requetes_gardees("deezer") == {"https://api.deezer.com/track/1"}
    assert [x["corps"] for x in brut.lectures("deezer", "https://api.deezer.com/track/1")] == [
        b'{"id": 1}'
    ]
    lu = dtm.DisabledTracksManager()
    assert lu.load_disabled_tracks("Booba") == {7}
    with pytest.raises(DonneesAbsentes, match="lecture seule"):
        lu.desactiver("Booba", [8], "page_vide", "regle:page_vide")
    brut.consigner("deezer", "https://api.deezer.com/track/2", 200, b"{}")  # ne lève pas
    brut.fermer()
    assert _contenu(reel) == avant
    assert not (data / "brut" / "brut.db").exists()


def test_donnees_lues_dir_sans_brut_leve(tmp_path, code_dans, monkeypatch):
    data = _depot(tmp_path / "wt", worktree=True, avec_brut=False)
    code_dans(tmp_path / "wt", data)
    monkeypatch.setattr(config.settings, "donnees_lues_dir", str(tmp_path / "rien"))
    with pytest.raises(DonneesAbsentes, match="introuvable"):
        brut.chemin_lu()


def test_un_chemin_redirige_ne_passe_pas_par_la_garde(tmp_path, code_dans, monkeypatch):
    """Tests, `--donnees`, `--brut` : l'appelant a choisi son chemin."""
    data = _depot(tmp_path / "wt", worktree=True, avec_brut=False)
    code_dans(tmp_path / "wt", data)
    assert brut.requetes_gardees("deezer", tmp_path / "autre.db") == set()
    monkeypatch.setattr(brut, "CHEMIN", tmp_path / "copie" / "brut.db")
    assert brut.requetes_gardees("deezer") == set()


class TestBaseDuWorktree:
    """2026-11-24 : `rematerialiser` lancé depuis un worktree ouvrait une base
    NEUVE et vide et répondait « 0 fiche » sans erreur."""

    def test_la_base_de_la_configuration_leve(self, tmp_path):
        data = _depot(tmp_path / "wt", worktree=True, avec_brut=False)
        with pytest.raises(DonneesAbsentes):
            donnees_lues.verifier_base(data / "music_credits.db", tmp_path / "wt", data)

    def test_une_base_redirigee_ne_leve_pas(self, tmp_path):
        data = _depot(tmp_path / "wt", worktree=True, avec_brut=False)
        donnees_lues.verifier_base(tmp_path / "copie.db", tmp_path / "wt", data)

    @pytest.mark.parametrize("worktree,avec_brut", [(False, False), (True, True)])
    def test_depot_principal_ou_lot_fige_ne_levent_pas(self, tmp_path, worktree, avec_brut):
        data = _depot(tmp_path / "d", worktree=worktree, avec_brut=avec_brut)
        donnees_lues.verifier_base(data / "music_credits.db", tmp_path / "d", data)
