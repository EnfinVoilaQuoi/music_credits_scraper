"""Logique pure du dialog de fusion (src/gui/dialogs/merge_tracks) — sans GUI.

Verrouille la régression Phase 5 : les champs passés en sous-objets (audio.*,
lyrics.*) doivent redevenir visibles à la détection de conflits ET à la recopie.
Avant le fix, `getattr(t, "bpm")` plat renvoyait None → conflit jamais détecté.
"""

from src.gui.dialogs.merge_tracks import _FILL_ONLY, _find_conflicts, _get, _set
from src.models.artist import Artist
from src.models.track import Track


def _track(**audio_lyrics):
    t = Track(title="Solo", artist=Artist(name="X"))
    for path, value in audio_lyrics.items():
        _set(t, path.replace("__", "."), value)
    return t


def test_get_set_niche():
    t = Track(title="Solo")
    _set(t, "audio.bpm", 120)
    assert _get(t, "audio.bpm") == 120
    assert t.audio.bpm == 120  # écrit sur le VRAI sous-objet, pas un attribut orphelin


def test_conflit_bpm_detecte():
    # Deux BPM remplis et différents → conflit (sous-objet audio, régression Phase 5).
    a = _track(audio__bpm=120)
    b = _track(audio__bpm=122)
    labels = [c[0] for c in _find_conflicts(a, b)]
    assert "BPM" in labels


def test_conflit_lyrics_synced_detecte():
    a = _track(lyrics__synced="[00:01.00] a")
    b = _track(lyrics__synced="[00:02.00] b")
    labels = [c[0] for c in _find_conflicts(a, b)]
    assert "Paroles synchronisées" in labels


def test_pas_de_conflit_si_une_seule_valeur():
    # Une seule fiche a le BPM → pas un conflit (l'autre est complétée en silence).
    a = _track(audio__bpm=120)
    b = _track()
    assert _find_conflicts(a, b) == []


def test_fill_only_paths_valides():
    # Tous les chemins _FILL_ONLY résolvent sur un Track réel (pas d'attribut fantôme).
    t = Track(title="Solo")
    for path in _FILL_ONLY:
        _get(t, path)  # ne doit pas lever


def test_la_fusion_du_dialogue_est_consignee(monkeypatch):
    # Règle « une fusion se consigne » (2026-11-19) : le dialogue CTk notait une
    # suppression (`add_deleted`) au lieu de la fusion.
    from types import SimpleNamespace

    import src.utils.corrections_fiches as corrections_fiches
    import src.utils.database_backup as database_backup
    from src.gui.dialogs import merge_tracks

    class _Backup:
        def __init__(self, **_):
            pass

        def create_backup(self, _nom):
            return None

    consignees = []
    monkeypatch.setattr(database_backup, "DatabaseBackupManager", _Backup)
    monkeypatch.setattr(
        corrections_fiches, "memoriser_fusion", lambda *args: consignees.append(args)
    )
    artiste = Artist(name="X")
    gardee = Track(title="Solo", artist=artiste, genius_id=1)
    gardee.id = 10
    absorbee = Track(title="Solo (Clean)", artist=artiste, genius_id=2)
    absorbee.id = 11
    artiste.tracks = [gardee, absorbee]
    fusions = []
    app = SimpleNamespace(
        current_artist=artiste,
        data_manager=SimpleNamespace(
            db_path="base.db",
            save_track=lambda _t: None,
            merge_tracks=lambda garde, absorbe: fusions.append((garde, absorbe)) or True,
        ),
        deleted_tracks_manager=SimpleNamespace(
            add_deleted=lambda *_: (_ for _ in ()).throw(AssertionError("suppression notée"))
        ),
        selected_tracks=set(),
        _populate_tracks_table=lambda: None,
        _update_artist_info=lambda: None,
    )

    merge_tracks._do_merge(app, gardee, absorbee)

    assert fusions == [(10, 11)]
    assert consignees == [("X", {"genius_id": 2}, {"genius_id": 1})]
    assert artiste.tracks == [gardee]
