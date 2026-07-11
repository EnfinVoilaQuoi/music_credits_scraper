"""Fenêtres utilitaires : rapport scrollable et erreurs"""

from tkinter import messagebox

import customtkinter as ctk


def show_scrollable_report(app, title: str, text: str):
    """Fenêtre de rapport à texte DÉFILANT (taille fixe) — pour les récaps
    longs (30+ morceaux) qui débordaient le messagebox non scrollable."""
    dlg = ctk.CTkToplevel(app.root)
    dlg.title(title)
    dlg.geometry("560x620")
    dlg.minsize(460, 400)
    dlg.transient(app.root)
    dlg.grab_set()

    box = ctk.CTkTextbox(dlg, wrap="word", font=("Consolas", 12))
    box.pack(fill="both", expand=True, padx=12, pady=(12, 6))
    box.insert("1.0", text)
    box.configure(state="disabled")

    btns = ctk.CTkFrame(dlg, fg_color="transparent")
    btns.pack(pady=(0, 12))

    def _copy():
        app.root.clipboard_clear()
        app.root.clipboard_append(text)

    ctk.CTkButton(btns, text="📋 Copier", width=90, fg_color="gray", command=_copy).pack(
        side="left", padx=5
    )
    ctk.CTkButton(btns, text="OK", width=90, command=dlg.destroy).pack(side="left", padx=5)


def show_error(app, title, message):
    """Affiche un message d'erreur"""
    messagebox.showerror(title, message)
