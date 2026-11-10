"""Auditer une COPIE avec les pages désactivées, le brut et les images réels
(2026-11-10) : sans eux, le critère gonflait (477 contre 440 le 2026-11-09)."""

from __future__ import annotations

import pytest

import scripts.audit_donnees as script


def _data(tmp_path, *dossiers):
    for d in dossiers:
        (tmp_path / d).mkdir(parents=True)
    return tmp_path


def test_une_copie_sans_les_dossiers_lus_est_refusee(tmp_path):
    copie = tmp_path / "copie" / "music_credits.db"
    copie.parent.mkdir()
    with pytest.raises(ValueError, match="disabled_tracks, brut, images"):
        script.dossier_des_donnees(copie, None)


def test_le_data_reel_donne_est_pris(tmp_path):
    reel = _data(tmp_path / "data", *script.DOSSIERS_LUS)
    assert script.dossier_des_donnees(tmp_path / "x.db", reel) == reel


def test_un_data_incomplet_est_refuse(tmp_path):
    reel = _data(tmp_path / "data", "disabled_tracks", "images")
    with pytest.raises(ValueError, match="brut"):
        script.dossier_des_donnees(tmp_path / "x.db", reel)


def test_viser_pointe_base_pages_desactivees_brut_et_images(tmp_path, monkeypatch):
    import src.persistence.brut as brut
    import src.services.audit as audit
    import src.utils.data_manager as dm
    import src.utils.disabled_tracks_manager as dtm

    for module, nom in (
        (dm, "DATABASE_URL"),
        (dtm, "DATA_DIR"),
        (brut, "CHEMIN"),
        (audit, "DOSSIER_IMAGES"),
    ):
        monkeypatch.setattr(module, nom, getattr(module, nom))
    reel = _data(tmp_path / "data", *script.DOSSIERS_LUS)
    script.viser(tmp_path / "copie.db", reel)
    assert f"sqlite:///{(tmp_path / 'copie.db').as_posix()}" == dm.DATABASE_URL
    assert dtm.DisabledTracksManager().disabled_tracks_dir == reel / "disabled_tracks"
    assert reel / "brut" / "brut.db" == brut.CHEMIN
    assert reel / "images" == audit.DOSSIER_IMAGES


def test_donnees_sans_base_refuse():
    with pytest.raises(SystemExit, match="--base"):
        script.main(["--donnees", "x"])
