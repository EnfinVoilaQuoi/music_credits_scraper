"""Mise à jour des streams Spotify (Kworb) / YouTube Music en thread"""

from tkinter import messagebox

import customtkinter as ctk

from src.config import settings
from src.enrichment.providers.streams import StreamsProvider
from src.gui.dialogs import kworb_confirm, report
from src.gui.workers.lifecycle import run_worker, stop_requested
from src.observability import source_usage
from src.observability.registry import Flow
from src.utils.logger import get_logger

logger = get_logger(__name__)


#: Les deux sources du champ `spotify_streams`. L'ordre n'a **aucun effet sur la
#: donnée** : chacune déclare ce qu'elle a vu, et `reconcile_spotify_streams`
#: (appliqué par le repository) désigne la valeur retenue selon
#: `settings.streams_master`. On garde la maître en tête par simple courtoisie
#: d'affichage — sa progression s'affiche en premier.
_STREAM_SOURCES = ("kworb", "spotify_web")


def _stream_sources() -> tuple[str, ...]:
    master = settings.streams_master
    return (master, *(s for s in _STREAM_SOURCES if s != master))


def start_streams_update(app):
    """Ouvre le dialog de récupération des streams Spotify + YouTube Music."""
    if not app.current_artist:
        return

    dialog = ctk.CTkToplevel(app.root)
    dialog.title("Nb Streams")
    dialog.geometry("420x380")
    dialog.resizable(False, False)
    dialog.transient(app.root)
    dialog.grab_set()

    ctk.CTkLabel(
        dialog, text="Sources de streams à récupérer :", font=ctk.CTkFont(size=13, weight="bold")
    ).pack(pady=(18, 8))

    # Trois sources Spotify, de coûts TRÈS différents : les mélanger sous une
    # seule case ferait payer plusieurs minutes de crawl à qui voulait un
    # rafraîchissement de quelques secondes.
    kworb_var = ctk.BooleanVar(value=True)
    spotify_light_var = ctk.BooleanVar(value=True)
    spotify_full_var = ctk.BooleanVar(value=False)
    ytm_var = ctk.BooleanVar(value=True)

    ctk.CTkCheckBox(dialog, text="Spotify — Kworb (rapide)", variable=kworb_var).pack(
        anchor="w", padx=40, pady=4
    )
    ctk.CTkCheckBox(
        dialog,
        text="Spotify — auditeurs mensuels + top 10 (1 page)",
        variable=spotify_light_var,
    ).pack(anchor="w", padx=40, pady=4)
    ctk.CTkCheckBox(
        dialog,
        text="Spotify — tous les morceaux et albums (plusieurs minutes)",
        variable=spotify_full_var,
    ).pack(anchor="w", padx=40, pady=4)
    ctk.CTkCheckBox(dialog, text="YouTube Music", variable=ytm_var).pack(
        anchor="w", padx=40, pady=4
    )

    # Canal YTM épinglé (résout les homonymes : @handle, lien ou UC...)
    ctk.CTkLabel(
        dialog, text="Canal YTM (optionnel — @handle, lien ou UC...) :", font=ctk.CTkFont(size=11)
    ).pack(anchor="w", padx=40, pady=(10, 2))
    ytm_channel_entry = ctk.CTkEntry(dialog, width=330, placeholder_text="@ISHAOfficiel")
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
        fetch_kworb = kworb_var.get()
        # La case « tous les morceaux » implique la page artiste : le crawl
        # complet commence par elle de toute façon.
        fetch_full = spotify_full_var.get()
        fetch_light = spotify_light_var.get() or fetch_full
        fetch_ytm = ytm_var.get()
        ytm_channel_raw = ytm_channel_entry.get().strip()
        dialog.destroy()
        if fetch_kworb or fetch_light or fetch_ytm:
            run_streams_update(
                app, fetch_kworb, fetch_ytm, ytm_channel_raw, fetch_light, fetch_full
            )

    ctk.CTkButton(dialog, text="Lancer", command=launch, width=120).pack(pady=18)


def build_summary(results: dict, *, spotify_full_crawl: bool) -> str:
    """Résumé affiché après une passe « Nb Streams ».

    Pure : ne lit que le dict `results` rempli par les providers. Extraite de
    la closure du worker le 2026-09-05 — elle porte une dizaine de décisions
    (échec, abandon du gate d'identité, troncature des listes à 8 entrées,
    verdicts du canal YTM) que rien ne vérifiait.
    """
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
    if "spotify_web" in results:
        r = results["spotify_web"]
        if "error" in r:
            lines.append(f"Spotify (pages web) : ❌ {r['error']}")
        elif r.get("aborted"):
            # Gate d'identité : rien n'a été écrit, et c'est voulu.
            lines.append(f"🚨 Spotify (pages web) : {r['aborted']} — aucune écriture.")
        else:
            lines.append(
                f"Spotify (pages web) : {r.get('recorded', 0)} morceau(x) relevé(s), "
                f"{r.get('albums_totalises', 0)} album(s) totalisé(s), "
                f"{r.get('pages', 0)} page(s) ouverte(s)"
            )
            if not spotify_full_crawl:
                lines.append(
                    "   • page artiste seule — coche « tous les morceaux » "
                    "pour les streams par titre et les totaux d'album"
                )
            if r.get("monthly_listeners") is not None:
                lines.append(
                    f"   • Auditeurs mensuels : {r['monthly_listeners']:,}".replace(",", " ")
                )
            if r.get("harvested_foreign"):
                lines.append(
                    f"   • {r['harvested_foreign']} compteur(s) récolté(s) au passage "
                    "pour d'autres artistes de la base"
                )
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
    return "\n".join(lines)


def run_streams_update(
    app,
    fetch_kworb: bool,
    fetch_ytm: bool,
    ytm_channel_raw: str = "",
    fetch_spotify_web: bool = False,
    spotify_full_crawl: bool = False,
):
    """Lance la récupération des streams dans un thread daemon.

    `fetch_spotify_web` ouvre la page artiste (auditeurs mensuels + top 10, UNE
    page) ; `spotify_full_crawl` y ajoute les pages titre et les albums, soit
    environ une page par morceau — d'où deux drapeaux et non un.
    """
    if hasattr(app, "streams_button"):
        app.streams_button.configure(state="disabled")

    def run():
        # Provider streams (Chantier 3) : POSSÈDE les clients Kworb/YTM (lazy),
        # le worker garde la boucle/résumés/confirmation/saves. Le client YTM est
        # partagé entre streams YTM et vues vidéo.
        provider = StreamsProvider()
        try:
            app.root.after(0, app._show_progress_bar)
            results = {}

            demandees = {"kworb": fetch_kworb, "spotify_web": fetch_spotify_web}
            if any(demandees.values()) and not stop_requested():
                # Deux sources pour le MÊME champ. Chacune ne déclare que ce
                # qu'elle a vu ; c'est l'arbitrage (`reconcile_spotify_streams`)
                # qui désigne la valeur en colonne. Le worker n'a donc AUCUNE
                # priorité à faire respecter, et l'ordre ci-dessous ne change pas
                # le résultat — seulement l'ordre d'affichage.
                for source in _stream_sources():
                    if stop_requested() or not demandees[source]:
                        continue
                    label = "Spotify (Kworb)" if source == "kworb" else "Spotify (pages web)"
                    app.root.after(
                        0, lambda t=label: app.progress_label.configure(text=f"{t} en cours...")
                    )
                    try:
                        if source == "kworb":
                            results["spotify"] = provider.fetch_spotify(
                                app.current_artist, app.data_manager
                            )
                        else:
                            results["spotify_web"] = provider.fetch_spotify_web(
                                app.current_artist,
                                app.data_manager,
                                stop_requested=stop_requested,
                                full_crawl=spotify_full_crawl,
                            )
                    except Exception as e:
                        results["spotify" if source == "kworb" else "spotify_web"] = {
                            "error": str(e)
                        }

            if fetch_ytm and not stop_requested():
                app.root.after(
                    0, lambda: app.progress_label.configure(text="YouTube Music en cours...")
                )
                try:
                    from src.api.ytmusic_api import YTMusicAPI

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

                    results["ytm"] = provider.fetch_ytm(app.current_artist, app.data_manager)

                    # Media 5 : vues + nature (clip/show/audio) de LA vidéo — batch
                    # YT mutualisé avec les streams. SÉPARÉ de ytm_streams. Défensif :
                    # n'interrompt pas la récupération des streams en cas d'échec.
                    if not stop_requested():
                        try:
                            fresh_tracks = app.data_manager.get_artist_tracks(app.current_artist.id)
                            results["video_views"] = provider.fetch_video_views(
                                app.current_artist, fresh_tracks, app.data_manager
                            )
                        except Exception as e:
                            logger.warning(f"Vues clips échouées: {e}")
                except Exception as e:
                    results["ytm"] = {"error": str(e)}

            # Construire le message résumé
            summary_msg = build_summary(results, spotify_full_crawl=spotify_full_crawl)

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
            provider.close()
            app.root.after(0, app._hide_progress_bar)
            app.root.after(0, app._update_buttons_state)

    def run_observe():
        with source_usage.run_scope(
            Flow.STREAMS, artist_id=app.current_artist.id, artist_name=app.current_artist.name
        ):
            return run()

    run_worker(run_observe, name="streams")
