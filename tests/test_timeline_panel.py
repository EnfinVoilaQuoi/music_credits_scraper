"""L'onglet « Timeline » d'Export studio (`src/gui/panels/timeline_panel.py`).

Tk est instancié (skip sans affichage) : on vérifie que les rangées reflètent
les candidats, que le compteur pilote le bouton, que le switch d'ordre conserve
les saisies, et que « Mémoriser » puis `refresh()` reprend la sélection. Le
moteur n'est pas retesté ici ; les overrides vont dans `tmp_path`.
"""

import pytest

from src.dataviz import timeline as tl
from src.dataviz import timeline_overrides_io as ovr
from tests.test_timeline import _corpus


class _App:
    def __init__(self, tracks):
        from src.models.artist import Artist

        self.current_artist = Artist(id=7, name="Isha", tracks=tracks)
        self.disabled = set()

    def _is_track_disabled(self, track):
        return track.id in self.disabled


@pytest.fixture(scope="module")
def racine():
    """UNE racine Tk pour le module : recréer Tk après `destroy()` dans le même
    process rate par intermittence (« Can't find a usable init.tcl »), et un
    skip aléatoire est pire qu'un skip franc."""
    ctk = pytest.importorskip("customtkinter")
    try:
        root = ctk.CTk()
    except Exception as exc:  # noqa: BLE001 — pas d'affichage (CI headless)
        pytest.skip(f"aucun affichage disponible : {exc!r}")
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def panel(racine, tmp_path, monkeypatch):
    import customtkinter as ctk

    from src.gui.panels.timeline_panel import TimelinePanel

    monkeypatch.setattr(ovr, "overrides_path", lambda: tmp_path / "timeline_overrides.json")
    monkeypatch.setattr(tl, "resolve_cover", lambda rel: None)
    app = _App(_corpus())
    parent = ctk.CTkFrame(racine)
    p = TimelinePanel(parent, app, safe_after=lambda fn: fn())
    yield p
    parent.destroy()


def test_refresh_builds_rows_and_default_selection(panel):
    panel.refresh()
    assert panel.rows, "aucune rangée"
    assert set(panel.rows) == {c.key for c in panel._candidates}
    checked = {k for k, r in panel.rows.items() if r.checked.get()}
    # 12 candidats seulement dans la miniature → 3 pages, tout coché.
    assert panel._pages() == 3
    assert len(checked) == 12
    assert panel.counter_label.cget("text") == "12/12"
    assert panel.generate_button.cget("state") == "normal"
    assert panel.rows["album:album b"].line1.get() == "Album avec **Limsa**"


def test_counter_disables_generate_when_off(panel):
    panel.refresh()
    panel.rows["album:album a"].checked.set(False)
    panel._update_counter()
    assert panel.counter_label.cget("text") == "11/12"
    assert panel.generate_button.cget("state") == "disabled"


def test_order_switch_keeps_edits(panel):
    panel.refresh()
    panel.rows["track:16"].line1.set("Feat avec **Georgio** (XX5)")
    panel.order_switch.set("Par date")
    panel._regrid()
    assert panel.rows["track:16"].line1.get() == "Feat avec **Georgio** (XX5)"
    rows_by_y = sorted(panel.rows.values(), key=lambda r: r.frame.grid_info()["row"])
    dates = [r.candidate.date for r in rows_by_y]
    assert dates == sorted(dates)
    # Par date : pas de séparateur ; proposés : un trait entre projets et morceaux.
    assert not panel.separator.grid_info()
    panel.order_switch.set("Proposés")
    panel._regrid()
    sep_row = panel.separator.grid_info()["row"]
    before = [r for r in panel.rows.values() if r.frame.grid_info()["row"] < sep_row]
    after = [r for r in panel.rows.values() if r.frame.grid_info()["row"] > sep_row]
    assert before and all(r.candidate.kind != "track" for r in before)
    assert after and all(r.candidate.kind == "track" for r in after)
    free_row = panel.separator_freestyle.grid_info()["row"]
    assert all(r.candidate.is_freestyle for r in after if r.frame.grid_info()["row"] > free_row)
    assert all(not r.candidate.is_freestyle for r in after if r.frame.grid_info()["row"] < free_row)


def test_save_then_refresh_restores_selection(panel, tmp_path):
    panel.refresh()
    panel.rows["track:17"].checked.set(False)
    panel.rows["album:album a"].line2.set("**Album A (édition test)**")
    panel.rows["album:album a"].size.set("petit")
    panel.rows["album:album a"].disc_side.set("gauche")
    panel.rows["album:album a"].background.set("2")
    panel.save()
    assert (tmp_path / "timeline_overrides.json").exists()
    panel.refresh()
    assert not panel.rows["track:17"].checked.get()
    assert panel.rows["album:album a"].line2.get() == "**Album A (édition test)**"
    assert panel.rows["album:album a"].size.get() == "petit"
    assert panel.rows["album:album a"].disc_side.get() == "gauche"
    assert panel.rows["album:album a"].background.get() == "2"
    assert "mémorisée" in panel.title_label.cget("text")


def test_disabled_track_listed_not_checked_and_search_adds_rows(panel):
    panel.app.disabled = {15}
    panel.refresh()
    row = panel.rows["track:15"]
    assert row.candidate.disabled and not row.checked.get()
    # « ➕ Ajouter » : un titre hors liste (aucun ici : tout est déjà affiché)
    # → message, puis un titre inconnu → message aussi, sans nouvelle rangée.
    before = len(panel.rows)
    panel.search_var.set("zzz-introuvable")
    panel.add_by_search()
    assert len(panel.rows) == before
    assert "Aucun" in panel.status_label.cget("text")


def test_disc_side_menu_disabled_without_cert(panel):
    panel.refresh()
    frame_a = panel.rows["album:album a"].frame
    frame_b = panel.rows["album:album b"].frame
    menus_a = [w for w in frame_a.winfo_children() if type(w).__name__ == "CTkOptionMenu"]
    menus_b = [w for w in frame_b.winfo_children() if type(w).__name__ == "CTkOptionMenu"]
    assert menus_a[1].cget("state") == "normal"
    assert menus_b[1].cget("state") == "disabled"


def test_missing_memorised_key_is_shown_greyed(panel, tmp_path):
    ovr.save_override(
        "Isha",
        entries=[tl.EntryChoice("album:disparu", "Album", "**Disparu**", "grand")],
        pages=3,
    )
    panel.refresh()
    row = panel.rows["album:disparu"]
    assert row.candidate is None and not row.checked.get()
    assert panel.counter_label.cget("text") == "0/12"
