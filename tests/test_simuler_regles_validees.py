"""`scripts/simuler_regles_validees.py` : le passage à blanc écrit ses pages
désactivées et supprimées dans une COPIE des magasins, jamais dans les vrais
(CORRECTIFS 2026-11-07 : une règle qui désactive ou supprime ne s'y simulait pas)."""

import json

import scripts.simuler_regles_validees as sim
import src.utils.deleted_tracks_manager as deleted
import src.utils.disabled_tracks_manager as disabled
from src.utils.deleted_tracks_manager import DeletedTracksManager
from src.utils.disabled_tracks_manager import DisabledTracksManager


def _instantane(dossier):
    return {p.relative_to(dossier): p.read_bytes() for p in dossier.rglob("*") if p.is_file()}


def test_les_vrais_magasins_ne_sont_pas_touches(tmp_path, monkeypatch):
    reel = tmp_path / "data"
    monkeypatch.setattr(disabled, "DATA_DIR", reel)
    monkeypatch.setattr(deleted, "DATA_DIR", reel)
    reel.mkdir()
    DisabledTracksManager().desactiver("Booba", [1], "page_vide", "regle:page_vide")
    DeletedTracksManager().add_deleted("Booba", 111, "Avant")
    avant = _instantane(reel)

    copie = tmp_path / "simulation"
    copie.mkdir()
    copies = sim.copier_les_magasins(reel, copie)

    # Ce que la simulation lit : l'état réel, copié.
    assert DisabledTracksManager().load_disabled_tracks("Booba") == {1}
    assert DeletedTracksManager().load_deleted_ids("Booba") == {111}
    # Ce qu'une règle écrit : la copie seulement.
    DisabledTracksManager().desactiver("Booba", [2], "non_musique", "regle:non_musique")
    DeletedTracksManager().add_deleted("Booba", 222, "Sample")
    DisabledTracksManager().desactiver("Kaaris", [3], "page_vide", "regle:page_vide")

    assert _instantane(reel) == avant
    assert set(copies) == {"disabled_tracks", "deleted_tracks"}
    assert DisabledTracksManager().load_disabled_tracks("Booba") == {1, 2}
    lu = json.loads(next(copies["deleted_tracks"].glob("booba*")).read_text(encoding="utf-8"))
    assert {e["genius_id"] for e in lu["deleted_tracks"]} == {111, 222}


def test_sans_magasin_reel_la_copie_part_vide(tmp_path, monkeypatch):
    monkeypatch.setattr(disabled, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(deleted, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()
    copie = tmp_path / "simulation"
    copie.mkdir()
    sim.copier_les_magasins(tmp_path / "data", copie)
    assert DisabledTracksManager().load_disabled_tracks("Booba") == set()
    assert not (tmp_path / "data" / "disabled_tracks").exists()
