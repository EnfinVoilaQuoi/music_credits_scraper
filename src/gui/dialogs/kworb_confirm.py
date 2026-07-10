"""Confirmation des rapprochements Kworb incertains"""
import re

import customtkinter as ctk
from tkinter import messagebox


# Descripteurs entre parenthèses : indice « même morceau » vs « version différente »
_SAME_HINTS = ('intro', 'outro', 'interlude', 'skit', 'prelude', 'prélude')
_DIFF_HINTS = ('acoustic', 'acoustique', 'remix', 'live', 'freestyle', 'instrumental',
               'edit', 'version', 'demo', 'rmx', 'club', 'radio', 'sped', 'slowed')

def confirm_kworb_suggestions(app, suggestions, kworb_date_str):
    """Confirme/rejette les rapprochements Kworb incertains (mémorisés).
    Indice : « (Intro/Outro/Interlude) » = souvent le même morceau ;
    « (Acoustic/Remix/Live/Freestyle) » = version différente → ne pas lier."""
    from datetime import datetime as _dt
    try:
        from src.utils.kworb_links_manager import KworbLinksManager
        links = KworbLinksManager()
    except Exception:
        return
    updated_at = None
    if kworb_date_str:
        try:
            updated_at = _dt.fromisoformat(kworb_date_str)
        except Exception:
            updated_at = None

    dlg = ctk.CTkToplevel(app.root)
    dlg.title("Rapprochements Kworb à confirmer")
    dlg.geometry("640x560")
    dlg.transient(app.root)
    dlg.grab_set()

    ctk.CTkLabel(dlg, text="Ces titres Kworb ressemblent à un morceau en base.\n"
                 "Coche ceux qui sont bien LE MÊME morceau (ta réponse est mémorisée).",
                 justify="left").pack(padx=15, pady=(12, 6), anchor="w")

    scroll = ctk.CTkScrollableFrame(dlg, height=380)
    scroll.pack(fill="both", expand=True, padx=12, pady=6)

    vars_by_sugg = []
    for s in suggestions:
        desc = (re.search(r'[\(\[]([^)\]]+)[)\]]', s['kworb_title']) or [None, ''])[1].lower()
        db_desc = (re.search(r'[\(\[]([^)\]]+)[)\]]', s['db_title']) or [None, ''])[1].lower()
        blob = f"{desc} {db_desc}"
        if any(h in blob for h in _DIFF_HINTS):
            hint, default = "⚠️ version différente probable", False
        elif any(h in blob for h in _SAME_HINTS):
            hint, default = "✓ même morceau probable", True
        else:
            hint, default = "à vérifier", False

        row = ctk.CTkFrame(scroll)
        row.pack(fill="x", pady=4)
        var = ctk.BooleanVar(value=default)
        ctk.CTkCheckBox(row, text="", variable=var, width=28).pack(side="left", padx=(6, 0))
        txt = (f"Kworb « {s['kworb_title']} »\n→ base « {s['db_title']} »   "
               f"({s['score']:.0%})   {s['streams']:,} streams".replace(",", " ")
               + f"\n   {hint}")
        ctk.CTkLabel(row, text=txt, justify="left", anchor="w").pack(
            side="left", fill="x", expand=True, padx=6, pady=4)
        vars_by_sugg.append((s, var))

    def _apply():
        n_ok = 0
        for s, var in vars_by_sugg:
            if var.get():
                if app.data_manager.update_track_spotify_streams(
                        s['track_id'], s['streams'], s['daily'], updated_at=updated_at):
                    links.confirm(app.current_artist.name, s['kworb_title'], s['track_id'])
                    n_ok += 1
            else:
                links.reject(app.current_artist.name, s['kworb_title'])
        dlg.destroy()
        app._reload_tracks_and_refresh()
        messagebox.showinfo("Rapprochements Kworb",
                            f"{n_ok} lié(s), {len(vars_by_sugg) - n_ok} rejeté(s).\n"
                            "Décisions mémorisées pour les prochains runs.")

    btns = ctk.CTkFrame(dlg, fg_color="transparent")
    btns.pack(pady=12)
    ctk.CTkButton(btns, text="Appliquer", command=_apply).pack(side="left", padx=5)
    ctk.CTkButton(btns, text="Plus tard", fg_color="gray",
                  command=dlg.destroy).pack(side="left", padx=5)
