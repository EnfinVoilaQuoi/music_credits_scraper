"""Mise à jour des streams Spotify (Kworb) / YouTube Music en thread"""

from tkinter import messagebox

import customtkinter as ctk

from src.gui.dialogs import kworb_confirm, report
from src.gui.workers.lifecycle import run_worker, stop_requested
from src.utils.logger import get_logger

logger = get_logger(__name__)


def start_streams_update(app):
    """Ouvre le dialog de récupération des streams Spotify + YouTube Music."""
    if not app.current_artist:
        return

    dialog = ctk.CTkToplevel(app.root)
    dialog.title("Nb Streams")
    dialog.geometry("380x300")
    dialog.resizable(False, False)
    dialog.transient(app.root)
    dialog.grab_set()

    ctk.CTkLabel(
        dialog, text="Sources de streams à récupérer :", font=ctk.CTkFont(size=13, weight="bold")
    ).pack(pady=(18, 8))

    spotify_var = ctk.BooleanVar(value=True)
    ytm_var = ctk.BooleanVar(value=True)

    ctk.CTkCheckBox(dialog, text="Spotify (Kworb)", variable=spotify_var).pack(
        anchor="w", padx=40, pady=4
    )
    ctk.CTkCheckBox(dialog, text="YouTube Music", variable=ytm_var).pack(
        anchor="w", padx=40, pady=4
    )

    # Canal YTM épinglé (résout les homonymes : @handle, lien ou UC...)
    ctk.CTkLabel(
        dialog, text="Canal YTM (optionnel — @handle, lien ou UC...) :", font=ctk.CTkFont(size=11)
    ).pack(anchor="w", padx=40, pady=(10, 2))
    ytm_channel_entry = ctk.CTkEntry(dialog, width=290, placeholder_text="@ISHAOfficiel")
    ytm_channel_entry.pack(padx=40, anchor="w")
    try:
        stored = (
            app.data_manager.get_artist_ytm_channel(app.current_artist.id)
            if app.current_artist
            else None
        )
        if stored:
            ytm_channel_entry.insert(0, stored)
    except Exception:
        pass

    def launch():
        fetch_spotify = spotify_var.get()
        fetch_ytm = ytm_var.get()
        ytm_channel_raw = ytm_channel_entry.get().strip()
        dialog.destroy()
        if fetch_spotify or fetch_ytm:
            run_streams_update(app, fetch_spotify, fetch_ytm, ytm_channel_raw)

    ctk.CTkButton(dialog, text="Lancer", command=launch, width=120).pack(pady=18)


def run_streams_update(app, fetch_spotify: bool, fetch_ytm: bool, ytm_channel_raw: str = ""):
    """Lance la récupération des streams dans un thread daemon."""
    if hasattr(app, "streams_button"):
        app.streams_button.configure(state="disabled")

    def run():
        try:
            app.root.after(0, app._show_progress_bar)
            results = {}

            if fetch_spotify and not stop_requested():
                app.root.after(
                    0, lambda: app.progress_label.configure(text="Spotify (Kworb) en cours...")
                )
                try:
                    from src.utils.update_kworb import update_kworb_streams

                    results["spotify"] = update_kworb_streams(app.current_artist, app.data_manager)
                except Exception as e:
                    results["spotify"] = {"error": str(e)}

            if fetch_ytm and not stop_requested():
                app.root.after(
                    0, lambda: app.progress_label.configure(text="YouTube Music en cours...")
                )
                try:
                    from src.api.ytmusic_api import YTMusicAPI
                    from src.utils.update_ytmusic import update_ytmusic_streams

                    # Épingler le canal YTM saisi (@handle, lien ou UC...)
                    if ytm_channel_raw:
                        resolved = YTMusicAPI().resolve_channel(ytm_channel_raw)
                        if resolved:
                            app.data_manager.set_artist_ytm_channel(
                                app.current_artist.id, resolved, source="manual"
                            )
                        else:
                            logger.warning(
                                f"Canal YTM non résolu: {ytm_channel_raw!r} — "
                                "recherche automatique utilisée"
                            )

                    results["ytm"] = update_ytmusic_streams(app.current_artist, app.data_manager)

                    # Media 5 : vues + nature (clip/show/audio) de LA vidéo — batch
                    # YT mutualisé avec les streams. SÉPARÉ de ytm_streams. Défensif :
                    # n'interrompt pas la récupération des streams en cas d'échec.
                    if not stop_requested():
                        try:
                            from src.utils.update_video_views import update_video_views

                            fresh_tracks = app.data_manager.get_artist_tracks(app.current_artist.id)
                            results["video_views"] = update_video_views(
                                app.current_artist, fresh_tracks, app.data_manager
                            )
                        except Exception as e:
                            logger.warning(f"Vues clips échouées: {e}")
                except Exception as e:
                    results["ytm"] = {"error": str(e)}

            # Construire le message résumé
            lines = ["Récupération terminée !\n"]
            if "spotify" in results:
                r = results["spotify"]
                if "error" in r:
                    lines.append(f"Spotify : ❌ {r['error']}")
                else:
                    lines.append(
                        f"Spotify : {r.get('matched', 0)} matchés, "
                        f"{r.get('unmatched', 0)} non matchés, "
                        f"{r.get('albums_updated', 0)} albums"
                    )
                    # Morceaux présents sur Kworb mais introuvables en base :
                    # soit un raté de matching, soit un titre absent de la
                    # discographie — les gros streams méritent un œil.
                    # Rapprochements FLOUS (coquilles/ponctuation) à vérifier :
                    # le stream est écrit mais le match n'est pas exact.
                    fuzzy = r.get("fuzzy_matched") or []
                    if fuzzy:
                        lines.append("\n≈ Rapprochés (vérifie que c'est le bon morceau) :")
                        for kw, db, score in fuzzy[:8]:
                            lines.append(f"   • Kworb « {kw} » → base « {db} » ({score:.0%})")
                        if len(fuzzy) > 8:
                            lines.append(f"   … et {len(fuzzy) - 8} autre(s) (voir logs)")

                    details = r.get("unmatched_details") or []
                    if details:
                        lines.append("\n⚠️ Sur Kworb mais pas reliés en base :")
                        for title, streams in details[:8]:
                            lines.append(f"   • {title} — {streams:,} streams".replace(",", " "))
                        if len(details) > 8:
                            lines.append(f"   … et {len(details) - 8} autre(s) (voir logs)")
            if "ytm" in results:
                r = results["ytm"]
                if "error" in r:
                    lines.append(f"YouTube Music : ❌ {r['error']}")
                else:
                    lines.append(
                        f"YouTube Music : {r.get('matched', 0)} matchés, "
                        f"{r.get('unmatched', 0)} non matchés, "
                        f"{r.get('albums_processed', 0)} albums"
                    )
                    # Verdict du gate d'identité (canal homonyme / divergent).
                    identity = r.get("identity") or {}
                    status = identity.get("status")
                    matched = identity.get("matched", 0)
                    ytm_titles = identity.get("ytm_titles", 0)
                    if status == "aborted":
                        lines.append(
                            f"🚨 Canal YTM suspect ({matched}/{ytm_titles} titres) — "
                            "rien n'a été écrit. Renseigne le champ « Canal YTM » "
                            "(@handle) dans cette fenêtre et relance."
                        )
                    elif status == "warning":
                        lines.append(
                            f"⚠️ Canal YTM manuel divergent ({matched}/{ytm_titles} titres) "
                            "— écriture maintenue (saisie manuelle prioritaire)."
                        )
            if "video_views" in results:
                v = results["video_views"]
                by_kind = v.get("by_kind") or {}
                kinds = ", ".join(f"{k}: {n}" for k, n in sorted(by_kind.items())) or "—"
                lines.append(f"Vues vidéos : {v.get('updated', 0)} mis à jour ({kinds})")
            summary_msg = "\n".join(lines)

            app.root.after(
                0, lambda m=summary_msg: report.show_scrollable_report(app, "Nb Streams", m)
            )
            # RECHARGER depuis la base avant de réafficher : les MàJ streams
            # écrivent en DB via leurs propres objets — les tracks en mémoire
            # de la GUI ne voient rien sans reload (streams "invisibles").
            app.root.after(0, app._reload_tracks_and_refresh)

            # Rapprochements INCERTAINS à confirmer (ex. Kworb « Matrix » ≈
            # base « Matrix (Intro) ») : dialogue de confirmation + mémoire.
            _sugg = (results.get("spotify") or {}).get("suggestions") or []
            _kdate = (results.get("spotify") or {}).get("kworb_updated")
            if _sugg:
                app.root.after(
                    0, lambda s=_sugg, d=_kdate: kworb_confirm.confirm_kworb_suggestions(app, s, d)
                )
        except Exception as e:
            err_msg = f"Erreur inattendue : {e}"
            app.root.after(0, lambda: messagebox.showerror("Erreur Streams", err_msg))
        finally:
            app.root.after(0, app._hide_progress_bar)
            app.root.after(0, app._update_buttons_state)

    run_worker(run, name="streams")
