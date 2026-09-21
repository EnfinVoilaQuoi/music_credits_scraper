"""Confirmation des rapprochements Kworb incertains — et des variantes (2026-09-21).

Deux familles de lignes dans le même dialogue :
  · les suggestions FLOUES historiques (« Matrix » ≈ « Matrix (Intro) ») : une
    case « même morceau ? », mémorisée par `confirm`/`reject` ;
  · les VARIANTES (dicts porteurs d'un `kind`) : un remix ou une rendition que
    `update_kworb.rapprocher` n'a pas voulu trancher seul — quatre voies
    (`services/kworb_decisions.DECISIONS`), pré-positionnées sur sa proposition,
    appliquées par `kworb_decisions.appliquer` dans un worker (une ligne créée
    ouvre une page Spotify) et mémorisées par `decide`.
"""

from tkinter import messagebox

import customtkinter as ctk

from src.concurrency.lifecycle import run_worker
from src.services import kworb_decisions
from src.utils.version_descriptors import indice_meme_morceau

#: Ordre des boutons ; « existant » n'apparaît que si un morceau en base est
#: déjà ce remix (sinon la voie n'a pas de cible).
_VOIES = ("edition", "rendition", "existant", "collab", "tiers", "ignore")


def _texte_variante(s: dict) -> str:
    streams = f"{s['streams']:,}".replace(",", " ")
    lignes = [
        f"Kworb « {s['kworb_title']} » — {streams} streams"
        + ("  (*)" if s.get("is_feature") else "")
    ]
    if s.get("parent_title"):
        lignes.append(f"   socle en base : « {s['parent_title']} »")
    if s.get("credited"):
        lignes.append("   Spotify crédite : " + ", ".join(s["credited"]))
    for m in s.get("motifs") or []:
        lignes.append(f"   • {m}")
    return "\n".join(lignes)


def confirm_kworb_suggestions(app, suggestions, kworb_date_str):
    """Confirme/rejette les rapprochements Kworb incertains (mémorisés)."""
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
    dlg.geometry("760x600")
    dlg.transient(app.root)
    dlg.grab_set()

    ctk.CTkLabel(
        dlg,
        text=(
            "Ces lignes Kworb n'ont pas de morceau en base. Une VARIANTE (Live, Radio Edit, "
            "Bonus…) se rattache au morceau souche, hors total ; un REMIX devient un morceau "
            "à part — principal (collaboration) ou rôle secondaire (remixé par un tiers).\n"
            "Ta réponse est mémorisée : elle ne sera plus redemandée."
        ),
        justify="left",
        wraplength=720,
    ).pack(padx=15, pady=(12, 6), anchor="w")

    scroll = ctk.CTkScrollableFrame(dlg, height=420)
    scroll.pack(fill="both", expand=True, padx=12, pady=6)

    cases = []  # (suggestion floue, BooleanVar)
    choix = []  # (variante, StringVar)
    for s in suggestions:
        row = ctk.CTkFrame(scroll)
        row.pack(fill="x", pady=4)
        if s.get("kind"):
            ctk.CTkLabel(row, text=_texte_variante(s), justify="left", anchor="w").pack(
                anchor="w", fill="x", padx=8, pady=(6, 2)
            )
            voies = [v for v in _VOIES if v != "existant" or s.get("existants")]
            if not s.get("parent_track_id"):
                voies = [v for v in voies if v not in ("rendition", "edition")]
            var = ctk.StringVar(
                value=s.get("proposition") if s.get("proposition") in voies else "ignore"
            )
            ctk.CTkSegmentedButton(
                row,
                values=[kworb_decisions.LIBELLES[v] for v in voies],
                variable=ctk.StringVar(value=kworb_decisions.LIBELLES[var.get()]),
                command=lambda lib, v=var: v.set(_voie_du_libelle(lib)),
            ).pack(anchor="w", padx=8, pady=(0, 6))
            choix.append((s, var))
        else:
            hint, default = indice_meme_morceau(s["kworb_title"], s["db_title"])
            var = ctk.BooleanVar(value=default)
            ctk.CTkCheckBox(row, text="", variable=var, width=28).pack(side="left", padx=(6, 0))
            txt = (
                f"Kworb « {s['kworb_title']} »\n→ base « {s['db_title']} »   "
                f"({s['score']:.0%})   {s['streams']:,} streams".replace(",", " ") + f"\n   {hint}"
            )
            ctk.CTkLabel(row, text=txt, justify="left", anchor="w").pack(
                side="left", fill="x", expand=True, padx=6, pady=4
            )
            cases.append((s, var))

    def _apply():
        n_ok = 0
        for s, var in cases:
            if var.get():
                if app.data_manager.record_spotify_streams(
                    s["track_id"],
                    s["streams"],
                    "kworb",
                    updated_at=updated_at,
                    daily_streams=s["daily"],
                ):
                    links.confirm(app.current_artist.name, s["kworb_title"], s["track_id"])
                    n_ok += 1
            else:
                links.reject(app.current_artist.name, s["kworb_title"])
        decisions = [(s, var.get()) for s, var in choix]
        dlg.destroy()

        def _appliquer_variantes():
            # Une ligne créée lit sa page titre Spotify : hors du fil Tk.
            from src.scrapers.spotify_web_scraper import lire_identite_page

            comptes = []
            for s, decision in decisions:
                try:
                    comptes.append(
                        kworb_decisions.appliquer(
                            app.data_manager,
                            app.current_artist,
                            s,
                            decision,
                            updated_at,
                            lire_page=lire_identite_page,
                            links=links,
                        )
                    )
                except Exception as e:  # noqa: BLE001 — une ligne ne bloque pas les autres
                    comptes.append(f"« {s['kworb_title']} » : ❌ {e}")
            texte = f"{n_ok} lié(s), {len(cases) - n_ok} rejeté(s)."
            if comptes:
                texte += "\n\n" + "\n".join(comptes)
            texte += "\n\nDécisions mémorisées pour les prochains runs."
            app.root.after(0, app._reload_tracks_and_refresh)
            app.root.after(0, lambda: messagebox.showinfo("Rapprochements Kworb", texte))

        if decisions:
            run_worker(_appliquer_variantes, name="kworb-decisions")
        else:
            app._reload_tracks_and_refresh()
            messagebox.showinfo(
                "Rapprochements Kworb",
                f"{n_ok} lié(s), {len(cases) - n_ok} rejeté(s).\n"
                "Décisions mémorisées pour les prochains runs.",
            )

    btns = ctk.CTkFrame(dlg, fg_color="transparent")
    btns.pack(fill="x", padx=12, pady=(4, 12))
    ctk.CTkButton(btns, text="Appliquer", command=_apply).pack(side="right", padx=6)
    ctk.CTkButton(btns, text="Plus tard", fg_color="gray", command=dlg.destroy).pack(
        side="right", padx=6
    )


def _voie_du_libelle(libelle: str) -> str:
    return next(v for v, lib in kworb_decisions.LIBELLES.items() if lib == libelle)
