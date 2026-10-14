"""Les renditions Kworb devenues éditions (« Bonus Track », 2026-10-15)."""

import importlib.util
from pathlib import Path

from src.models import Artist, Track

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "requalifier_renditions_editions.py"


def _module():
    spec = importlib.util.spec_from_file_location("requalifier", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_seule_la_bonus_track_est_retiree(data_manager):
    artiste = Artist(name="Booba")
    artiste.id = data_manager.save_artist(artiste)
    t = Track(title="DKR", artist=artiste)
    t.id = data_manager.save_track(t)
    data_manager.record_variant_streams(t.id, "SPB", 108, 1, None, label="DKR - Bonus Track")
    data_manager.record_variant_streams(t.id, "SPL", 5, 1, None, label="DKR - Live")
    lignes = _module().a_retirer(data_manager)
    assert [r["spotify_id"] for r in lignes] == ["SPB"]
