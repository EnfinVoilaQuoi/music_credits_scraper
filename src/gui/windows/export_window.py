"""Fenetre Export -- export des donnees (JSON/CSV) et point d'accroche pour le studio.

L'onglet Export Brut vit ici. Les onglets visuels (Analyse de Projet, Timeline,
Stats) sont ajoutes par ``therapie_studio`` s'il est installe.
"""

import customtkinter as ctk

from src.utils.logger import get_logger

logger = get_logger(__name__)


class ExportWindow:
    """Fenetre CTkToplevel Export (une seule instance a la fois)."""

    def __init__(self, app):
        self.app = app
        self._stop = False

        self.window = ctk.CTkToplevel(app.root)
        self.window.title("Export")
        self.window.geometry("900x640")
        self.window.transient(app.root)
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)

        self.tabview = ctk.CTkTabview(self.window)
        self.tabview.pack(fill="both", expand=True, padx=10, pady=(10, 5))

        self._build_export_brut_tab()
        self._install_studio_tabs()

    def _build_export_brut_tab(self):
        tab_brut = self.tabview.add("Export Brut")
        ctk.CTkLabel(
            tab_brut,
            text=(
                "Export des donnees de l'artiste (discographie, credits, paroles, streams...)\n"
                "en un fichier JSON -- les morceaux desactives sont exclus."
            ),
            text_color="gray",
            justify="left",
        ).pack(anchor="w", padx=12, pady=(16, 8))
        ctk.CTkButton(
            tab_brut, text="Export JSON (donnees)...", width=220, command=self.app._export_data
        ).pack(anchor="w", padx=12)

    def _install_studio_tabs(self):
        """Tente d'accrocher les onglets du studio (depot prive)."""
        try:
            from therapie_studio.gui.export_studio import install_tabs
        except ImportError:
            install_tabs = None
        if install_tabs is not None:
            try:
                install_tabs(self.tabview, self.app, self)
            except Exception as exc:  # noqa: BLE001
                logger.error(f"therapie_studio: echec de l'accroche : {exc}")

    def refresh(self):
        """Rafraichit le contenu (albums, timeline) si le studio est installe."""
        studio = getattr(self, "_studio", None)
        if studio is not None:
            studio.refresh()

    def safe_after(self, fn):
        """Planifie ``fn`` sur le thread Tk, ignore une fenetre deja detruite."""
        try:
            self.window.after(0, fn)
        except Exception:  # noqa: BLE001
            pass

    def _on_close(self):
        self._stop = True
        self.window.destroy()
        if getattr(self.app, "export_window", None) is self:
            self.app.export_window = None


def show_export_window(app):
    """Ouvre (ou refocus) la fenetre Export."""
    existing = getattr(app, "export_window", None)
    if existing is not None:
        try:
            existing.window.deiconify()
            existing.window.lift()
            existing.window.focus_force()
            existing.refresh()
            return existing
        except Exception:  # noqa: BLE001
            pass
    app.export_window = ExportWindow(app)
    return app.export_window
