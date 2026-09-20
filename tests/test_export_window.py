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


@pytest.fixture()
def _display_available():
    try:
        import tkinter

        tkinter.Tk().destroy()
    except Exception:
        pytest.skip("pas d'affichage disponible")


@pytest.mark.usefixtures("_display_available", "_no_therapie_studio")
def test_export_window_sans_studio():
    """La fenetre se construit avec l'onglet Export Brut seul."""
    import customtkinter as ctk

    from src.gui.windows.export_window import ExportWindow

    root = ctk.CTk()
    try:

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
    finally:
        root.destroy()


@pytest.mark.usefixtures("_display_available")
def test_export_window_avec_install_tabs_factice():
    """Un install_tabs factice ajoute un onglet."""
    import customtkinter as ctk

    from src.gui.windows.export_window import ExportWindow

    installed = []

    def fake_install(tabview, app, window):
        tabview.add("Test Studio")
        installed.append(True)

    root = ctk.CTk()
    try:

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
        win._on_close()
    finally:
        root.destroy()
