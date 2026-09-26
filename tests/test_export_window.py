"""La fenetre Export se construit sans le depot prive."""

import sys
import types

import pytest


@pytest.fixture()
def _no_therapie_studio(monkeypatch):
    """Empeche l'import de therapie_studio."""
    blocker = types.ModuleType("therapie_studio")
    blocker.__path__ = []
    monkeypatch.setitem(sys.modules, "therapie_studio", blocker)
    monkeypatch.setitem(
        sys.modules,
        "therapie_studio.gui",
        types.ModuleType("therapie_studio.gui"),
    )
    mod = types.ModuleType("therapie_studio.gui.export_studio")
    monkeypatch.setitem(sys.modules, "therapie_studio.gui.export_studio", mod)


@pytest.mark.usefixtures("_no_therapie_studio")
def test_export_window_sans_studio(racine_tk):
    """La fenetre se construit avec l'onglet Export Brut seul."""
    from src.gui.windows.export_window import ExportWindow

    root = racine_tk

    class FakeApp:
        def __init__(self, r):
            self.root = r
            self.export_window = None

        def _export_data(self):
            pass

    app = FakeApp(root)
    win = ExportWindow(app)
    tabs = win.tabview._tab_dict
    assert "Export Brut" in tabs
    assert "Analyse de Projet" not in tabs
    win._on_close()


def test_export_window_avec_install_tabs_factice(racine_tk):
    """Un install_tabs factice ajoute un onglet."""
    from src.gui.windows.export_window import ExportWindow

    installed = []

    def fake_install(tabview, app, window):
        tabview.add("Test Studio")
        installed.append(True)

    root = racine_tk

    class FakeApp:
        def __init__(self, r):
            self.root = r
            self.export_window = None

        def _export_data(self):
            pass

    app = FakeApp(root)

    import unittest.mock

    with unittest.mock.patch(
        "src.gui.windows.export_window.ExportWindow._install_studio_tabs",
        lambda self: fake_install(self.tabview, self.app, self),
    ):
        win = ExportWindow(app)
    tabs = win.tabview._tab_dict
    assert "Export Brut" in tabs
    assert "Test Studio" in tabs
    assert installed
    # « Export Brut » isolé en DERNIER (décision 2026-09-15).
    assert win.tabview._name_list == ["Test Studio", "Export Brut"]
    win._on_close()
