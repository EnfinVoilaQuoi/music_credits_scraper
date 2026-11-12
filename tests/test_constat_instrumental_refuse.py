"""Refus du constat d'instrumental d'une PAGE Genius (nature `constat_instrumental`,
décision utilisateur 2026-11-12) : Diam's « Confessions nocturnes - Live 2006 »,
chantée avec Vitaa, que Genius marque « This song is an instrumental ». Le refus
vaut pour l'enregistrement (lignes sœurs), se lève, et l'audit voit un constat
reposé malgré lui (`refus_vivant`)."""

from src.models import Artist, Track
from src.services import audit as au
from src.utils import corrections_fiches as cf


def _live(dm):
    diams = Artist(name="Diam's")
    diams.id = dm.save_artist(diams)
    t = Track(title="Confessions nocturnes - Live 2006", artist=diams, genius_id=4242)
    t.id = dm.save_track(t)
    return t


def _refus_vivants(dm):
    return [c for c in au.auditer(dm, ["Diam's"], revue=False).cas if c.famille == "refus_vivant"]


def test_le_refus_se_pose_et_se_leve(data_manager):
    t = _live(data_manager)
    assert not cf.constat_instrumental_refuse(t)
    cf.memoriser_constat_instrumental_refuse(t)
    assert cf.constat_instrumental_refuse(t)
    assert "constat_instrumental" in cf.PAR_ENREGISTREMENT
    assert cf.oublier_constat_instrumental_refuse(t)
    assert not cf.constat_instrumental_refuse(t)


def test_refus_vivant_voit_le_constat_repose(data_manager):
    t = _live(data_manager)
    cf.memoriser_constat_instrumental_refuse(t)
    assert not _refus_vivants(data_manager)
    data_manager.constater_instrumental(t.id)
    (cas,) = _refus_vivants(data_manager)
    assert cas.pistes == [t.id] and "constat_instrumental" in cas.libelle
    data_manager.lever_constat_instrumental(t.id)
    assert not _refus_vivants(data_manager)
