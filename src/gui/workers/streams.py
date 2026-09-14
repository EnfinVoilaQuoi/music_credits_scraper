"""Mise à jour des streams Spotify (Kworb / pages web) / YouTube Music — adaptateur GUI.

La boucle par source, l'épinglage du canal YTM et le résumé vivent dans
`src/services/streams`, partagé avec la CLI. Ici : le dialog, le worker, la
barre de progression, le rechargement de la vue et le dialog de confirmation
Kworb (le service ne choisit jamais à la place de l'utilisateur).
"""

from tkinter import messagebox

import customtkinter as ctk

from src.concurrency.lifecycle import run_worker, stop_requested
from src.enrichment.providers.streams import StreamsProvider
from src.gui.dialogs import kworb_confirm, report
from src.observability import source_usage
from src.observability.registry import Flow
from src.services import streams as streams_service
from src.services.runtime import Hooks
from src.utils.logger import get_logger

logger = get_logger(__name__)


def ids_des_morceaux_coches(artist, indices) -> set[int]:
    """Identifiants des morceaux cochés dans la vue.

    `app.selected_tracks` porte des INDEX de la liste affichée, pas des
    identifiants : les passer tels quels aux updaters filtrerait sur des
    numéros de ligne, c'est-à-dire sur les mauvais morceaux. Un index hors
    limites (vue rechargée entre-temps) ou un morceau jamais enregistré (pas
    d'`id`) est écarté plutôt que de faire échouer le lancement.
    """
    if not artist or not indices:
        return set()
    tracks = artist.tracks or []
    retenus = set()
    for i in indices:
        if 0 <= i < len(tracks) and tracks[i].id is not None:
            retenus.add(tracks[i].id)
    return retenus


def start_streams_update(app):
    """Ouvre le dialog de récupération des streams Spotify + YouTube Music."""
    if not app.current_artist:
        return

    dialog = ctk.CTkToplevel(app.root)
    dialog.title("Nb Streams")
    dialog.geometry("460x470")
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

    # Limiter aux morceaux cochés : le quota YouTube et les écritures ne portent
    # que sur eux. Le parcours du canal reste entier (le gate d'identité en a
    # besoin) — c'est pourquoi la case ne dit pas « ne traiter que ».
    coches = ids_des_morceaux_coches(app.current_artist, app.selected_tracks)
    limiter_var = ctk.BooleanVar(value=False)
    case_limiter = ctk.CTkCheckBox(
        dialog,
        text=f"Limiter aux {len(coches)} morceau(x) coché(s) — YouTube Music",
        variable=limiter_var,
    )
    case_limiter.pack(anchor="w", padx=40, pady=(12, 4))
    if not coches:
        case_limiter.configure(
            state="disabled", text="Limiter aux morceaux cochés — aucun n'est coché"
        )

    # Canal YTM épinglé (résout les homonymes : @handle, lien ou UC...)
    ctk.CTkLabel(
        dialog, text="Canal YTM (optionnel — @handle, lien ou UC...) :", font=ctk.CTkFont(size=11)
    ).pack(anchor="w", padx=40, pady=(10, 2))
    ytm_channel_entry = ctk.CTkEntry(dialog, width=330, placeholder_text="@ISHAOfficiel")
    ytm_channel_entry.pack(padx=40, anchor="w")

    # Seul un canal MANUEL pré-remplit le champ. Un canal `inferred` affiché à
    # l'identique se lisait comme une saisie de l'utilisateur : d'un artiste à
    # l'autre, le champ semblait garder la même valeur « collée » — alors que
    # les canaux étaient bien distincts, mais déduits. Il s'affiche donc en
    # indication grisée, avec de quoi l'oublier.
    stored, stored_source = app.data_manager.get_artist_ytm_channel_info(app.current_artist.id)
    if stored and stored_source == "manual":
        ytm_channel_entry.insert(0, stored)
    elif stored:
        ligne_deduit = ctk.CTkFrame(dialog, fg_color="transparent")
        ligne_deduit.pack(anchor="w", padx=40, pady=(4, 0))
        ctk.CTkLabel(
            ligne_deduit,
            text=f"↳ canal déduit : {stored}",
            text_color="gray",
            font=ctk.CTkFont(size=10),
        ).pack(side="left")

        def oublier_canal():
            app.data_manager.clear_artist_ytm_channel(app.current_artist.id)
            ligne_deduit.destroy()
            logger.info(f"🗑️ Canal YTM déduit oublié pour '{app.current_artist.name}'")

        ctk.CTkButton(
            ligne_deduit,
            text="🗑️ Oublier ce canal",
            width=140,
            height=22,
            font=ctk.CTkFont(size=10),
            fg_color="gray30",
            hover_color="gray40",
            command=oublier_canal,
        ).pack(side="left", padx=(8, 0))

    def launch():
        fetch_kworb = kworb_var.get()
        # La case « tous les morceaux » implique la page artiste : le crawl
        # complet commence par elle de toute façon.
        fetch_full = spotify_full_var.get()
        fetch_light = spotify_light_var.get() or fetch_full
        fetch_ytm = ytm_var.get()
        ytm_channel_raw = ytm_channel_entry.get().strip()
        track_ids = coches if (limiter_var.get() and coches) else None
        dialog.destroy()
        if fetch_kworb or fetch_light or fetch_ytm:
            run_streams_update(
                app,
                fetch_kworb,
                fetch_ytm,
                ytm_channel_raw,
                fetch_light,
                fetch_full,
                track_ids=track_ids,
            )

    ctk.CTkButton(dialog, text="Lancer", command=launch, width=120).pack(pady=18)


def run_streams_update(
    app,
    fetch_kworb: bool,
    fetch_ytm: bool,
    ytm_channel_raw: str = "",
    fetch_spotify_web: bool = False,
    spotify_full_crawl: bool = False,
    track_ids=None,
):
    """Lance la récupération des streams dans un thread daemon.

    `fetch_spotify_web` ouvre la page artiste (auditeurs mensuels + top 10, UNE
    page) ; `spotify_full_crawl` y ajoute les pages titre et les albums, soit
    environ une page par morceau — d'où deux drapeaux et non un.

    `track_ids` restreint le volet YouTube (streams YTM et vues) aux morceaux
    cochés. Les morceaux DÉSACTIVÉS en sont retirés (2026-09-14 : la GUI ne
    les filtrait pas ici, seule fenêtre à les ignorer).
    """
    if hasattr(app, "streams_button"):
        app.streams_button.configure(state="disabled")
    artist = app.current_artist
    options = streams_service.OptionsStreams(
        kworb=fetch_kworb,
        spotify_web=fetch_spotify_web,
        spotify_full=spotify_full_crawl,
        ytm=fetch_ytm,
        ytm_channel=ytm_channel_raw,
        track_ids=streams_service.track_ids_actifs(app.runtime, artist, track_ids),
    )

    def progress(courant, total, libelle, tache=""):
        app.root.after(0, lambda: app.progress_label.configure(text=f"{tache} en cours..."))

    def confirmer_kworb(suggestions, kworb_date):
        # Rapprochements INCERTAINS (ex. Kworb « Matrix » ≈ base « Matrix
        # (Intro) ») : dialogue de confirmation + mémoire.
        app.root.after(
            0,
            lambda s=suggestions, d=kworb_date: kworb_confirm.confirm_kworb_suggestions(app, s, d),
        )

    def run():
        try:
            app.root.after(0, app._show_progress_bar)
            # Le provider est instancié ICI (point de patch des tests) et
            # fermé par le service.
            bilan = streams_service.run(
                app.runtime,
                artist,
                options,
                Hooks(
                    progress=progress, should_stop=stop_requested, confirmer_kworb=confirmer_kworb
                ),
                provider=StreamsProvider(),
            )
            texte = streams_service.resume(bilan, options)
            app.root.after(0, lambda: report.show_scrollable_report(app, "Nb Streams", texte))
            # RECHARGER depuis la base avant de réafficher : les MàJ streams
            # écrivent en DB via leurs propres objets — les tracks en mémoire
            # de la GUI ne voient rien sans reload (streams "invisibles").
            app.root.after(0, app._reload_tracks_and_refresh)
        except Exception as e:
            err_msg = f"Erreur inattendue : {e}"
            app.root.after(0, lambda: messagebox.showerror("Erreur Streams", err_msg))
        finally:
            app.root.after(0, app._hide_progress_bar)
            app.root.after(0, app._update_buttons_state)

    def run_observe():
        with source_usage.run_scope(Flow.STREAMS, artist_id=artist.id, artist_name=artist.name):
            return run()

    run_worker(run_observe, name="streams")
