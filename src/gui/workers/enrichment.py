"""Enrichissement des données (BPM, certifications, YouTube…) — flux async (Phase F2).

Premier flux migré sur la boucle asyncio unique : le batch est une coroutine
soumise via `async_loop.submit` (plus de `start_worker`). Les providers API
purs tournent en httpx partagé dans la boucle ; les scrapers Playwright sync
sur le thread dédié du run (`DataEnricher.sync_runner`) ; les saves SQLite via
`asyncio.to_thread`. La progression GUI passe toujours par `root.after`
(inchangé — thread-safe depuis la boucle comme depuis l'ancien thread).

À la fermeture de l'app, `shutdown_workers()` annule la task du batch : le
save en cours se termine dans son thread (commit SQLite atomique), puis le
`finally` de la coroutine ferme browsers/Playwright/session httpx dans le
budget global de 8 s.
"""

from tkinter import messagebox

import customtkinter as ctk

from src.concurrency import async_loop
from src.concurrency.lifecycle import stop_requested
from src.gui.dialogs import report
from src.observability import source_usage
from src.observability.registry import Flow
from src.services import enrichissement
from src.services.runtime import Hooks
from src.utils.logger import get_logger

logger = get_logger(__name__)


def start_enrichment(app):
    """Lance l'enrichissement des données depuis toutes les sources"""
    dialog = ctk.CTkToplevel(app.root)
    dialog.title("Sources d'enrichissement")
    dialog.geometry("450x750")  # Augmenté pour GetSongBPM + Deezer
    dialog.transient(app.root)
    dialog.grab_set()

    ctk.CTkLabel(dialog, text="Sélectionnez les sources à utiliser:", font=("Arial", 14)).pack(
        pady=10
    )

    # Variables pour les checkboxes
    sources_vars = {}
    # `spotify_id` n'est PAS dans cette liste, et ce n'est pas un oubli : ce
    # n'est pas à l'utilisateur de décider si le scraper sert. La fenêtre
    # l'avouait elle-même — « laisser coché suffit » — et une case dont la
    # notice dit de ne pas y toucher n'est pas un réglage, c'est un piège à
    # clic. Le source est AUTO-RÉGULÉ : `SpotifyIdProvider.gate()` saute quand
    # un identifiant valide existe déjà ou quand la voie ISRC a satisfait
    # ReccoBeats. Il est injecté de force dans `start_enrichment` (voir la
    # raison là-bas, qui n'est pas celle qu'on croit).
    sources_info = {
        "reccobeats": "ReccoBeats (BPM/Key/Mode via ISRC) 🎵",
        "getsongbpm": "GetSongBPM API (2ᵉ vote BPM/Key/Mode) 🎹",
        "songbpm": "SongBPM Scraper (départage BPM/Key) 🎼",
        "bpmfinder": "BPM Finder (dernier recours, via lien YouTube) 🎛️",
        "deezer": "Deezer (ISRC, durée, date de sortie) 🎶",
        "discogs": "Discogs (crédits supplémentaires, labels) 💿",
    }

    available = app.data_enricher.get_available_sources()

    for source, description in sources_info.items():
        var = ctk.BooleanVar(value=source in available)
        sources_vars[source] = var

        frame = ctk.CTkFrame(dialog)
        frame.pack(fill="x", padx=20, pady=5)

        checkbox = ctk.CTkCheckBox(
            frame,
            text=description,
            variable=var,
            state="normal" if source in available else "disabled",
        )
        checkbox.pack(anchor="w")

        if source not in available:
            _msg = "(API non configurée)"
            if source == "bpmfinder":
                _msg = (
                    "(non configuré : BPMFINDER_EMAIL/PASSWORD dans .env ou "
                    "variables Windows, puis relancer l'app —\n"
                    " ou lancer scripts/bpmfinder_login.py une fois)"
                )
            ctk.CTkLabel(frame, text=_msg, text_color="gray").pack(anchor="w", padx=25)

        # Info supplémentaire pour BPM Finder
        if source == "bpmfinder":
            info_text = (
                "Dernier recours si BPM/Key manquent : analyse le lien YouTube.\n"
                "Compte requis (BPMFINDER_EMAIL/PASSWORD) ; session réutilisée."
            )
            ctk.CTkLabel(frame, text=info_text, font=("Arial", 9), text_color="gray").pack(
                anchor="w", padx=25
            )

        # Info supplémentaire pour GetSongBPM
        if source == "getsongbpm":
            info_text = "API rapide. Toujours interrogée pour le 2ᵉ vote BPM (recoupe ReccoBeats).\nNécessite clé API (GETSONGBPM_API_KEY)."
            ctk.CTkLabel(frame, text=info_text, font=("Arial", 9), text_color="gray").pack(
                anchor="w", padx=25
            )

        # Info supplémentaire pour Deezer
        if source == "deezer":
            info_text = "Fournit l'ISRC (pivot pour ReccoBeats), la durée et la date.\nVérifie aussi la cohérence des métadonnées."
            ctk.CTkLabel(frame, text=info_text, font=("Arial", 9), text_color="gray").pack(
                anchor="w", padx=25
            )

    # Identité en FIN de run (2026-09-16) : hors de la boucle des providers —
    # ce n'est pas une source par morceau mais un appel par artiste, après que
    # les albums sont connus (oracle d'identité). Les alias sont PROPOSÉS, à
    # arbitrer dans « Groupes » ; rien n'est confirmé automatiquement.
    mb_frame = ctk.CTkFrame(dialog)
    mb_frame.pack(fill="x", padx=20, pady=5)
    musicbrainz_var = ctk.BooleanVar(value=True)
    ctk.CTkCheckBox(
        mb_frame,
        text="MusicBrainz (identité : alias de scène, Discogs en confirmation) 🪪",
        variable=musicbrainz_var,
    ).pack(anchor="w")
    ctk.CTkLabel(
        mb_frame,
        text="En fin de run, une fois par artiste. Les alias sont PROPOSÉS — à arbitrer "
        "dans « Groupes ».",
        font=("Arial", 9),
        text_color="gray",
    ).pack(anchor="w", padx=25)

    separator = ctk.CTkFrame(dialog, height=2, fg_color="gray")
    separator.pack(fill="x", padx=20, pady=15)

    force_frame = ctk.CTkFrame(dialog)
    force_frame.pack(fill="x", padx=20, pady=5)

    force_var = ctk.BooleanVar(value=False)
    force_checkbox = ctk.CTkCheckBox(
        force_frame,
        text="🔄 Forcer la mise à jour des données existantes",
        variable=force_var,
        font=("Arial", 12),
    )
    force_checkbox.pack(anchor="w", pady=5)

    info_label = ctk.CTkLabel(
        force_frame,
        text="Cochez pour re-scraper même si BPM/Key/Mode/Duration\nexistent déjà (utile pour corriger des données)",
        font=("Arial", 9),
        text_color="gray",
    )
    info_label.pack(anchor="w", padx=25, pady=2)

    # Séparateur
    ctk.CTkLabel(force_frame, text="", height=10).pack()

    # ⚠️ Ici vivait « 🔄 Réinitialiser les Spotify IDs », qui effaçait TOUS les
    # identifiants de l'artiste pour les re-scraper. Elle emportait les bons avec
    # les mauvais, et le re-scrape reproposait les fautifs depuis le cache : un
    # scrape complet pour revenir au même état. Le besoin — « mes identifiants
    # sont douteux, reprends-les » — est réel ; la réponse est de n'effacer que
    # ce qu'on peut MONTRER. D'où un bouton, et non une case : vérifier n'est pas
    # un réglage d'enrichissement, c'est un geste avec son propre rapport.
    def _verifier_spotify() -> None:
        from src.gui.windows.verification_spotify import show_verification_spotify

        dialog.destroy()
        show_verification_spotify(app)

    ctk.CTkButton(
        force_frame,
        text="🔍 Vérifier les identifiants Spotify",
        command=_verifier_spotify,
        font=("Arial", 12),
        fg_color="gray30",
        hover_color="gray40",
    ).pack(anchor="w", pady=5)

    ctk.CTkLabel(
        force_frame,
        text="Confronte chaque identifiant de l'artiste à ce que Spotify sert\nvraiment. N'efface QUE sur validation, ligne par ligne.",
        font=("Arial", 9),
        text_color="gray",
    ).pack(anchor="w", padx=25, pady=2)

    # Séparateur
    ctk.CTkLabel(force_frame, text="", height=10).pack()

    # Checkbox pour nettoyer les données erronées
    clear_on_failure_var = ctk.BooleanVar(value=True)
    clear_on_failure_checkbox = ctk.CTkCheckBox(
        force_frame,
        text="🗑️ Nettoyer les données si enrichissement échoue",
        variable=clear_on_failure_var,
        font=("Arial", 12),
    )
    clear_on_failure_checkbox.pack(anchor="w", pady=5)

    clear_info_label = ctk.CTkLabel(
        force_frame,
        text="Efface les BPM/Key/Mode/Duration erronés quand aucune\nsource ne trouve de nouvelles données (recommandé)",
        font=("Arial", 9),
        text_color="gray",
    )
    clear_info_label.pack(anchor="w", padx=25, pady=2)

    def start_enrichment():
        selected_sources = [s for s, var in sources_vars.items() if var.get()]
        if not selected_sources:
            messagebox.showwarning("Attention", "Sélectionnez au moins une source")
            return

        # ⚠️ `spotify_id` est ajouté de FORCE, et pas seulement parce que l'app
        # décide seule de s'en servir. `data_enricher` calcule
        # `allow_spotify_scrape=("spotify_id" not in sources)` : la présence de
        # la clé dit à ReccoBeats de NE PAS scraper l'identifiant de son côté.
        # L'omettre déclencherait donc un SECOND scrape Playwright par morceau —
        # l'inverse exact de ce qu'on cherche. Retirer cette ligne « puisque la
        # case n'existe plus » doublerait le coût du run en silence.
        selected_sources.append("spotify_id")

        force_update = force_var.get()
        clear_on_failure = clear_on_failure_var.get()

        musicbrainz = musicbrainz_var.get()

        dialog.destroy()
        run_enrichment(
            app,
            selected_sources,
            force_update=force_update,
            clear_on_failure=clear_on_failure,
            musicbrainz=musicbrainz,
        )

    ctk.CTkButton(dialog, text="Démarrer", command=start_enrichment).pack(pady=20)


def run_enrichment(
    app,
    sources: list[str],
    force_update: bool = False,
    clear_on_failure: bool = True,
    musicbrainz: bool = True,
):
    """Exécute l'enrichissement avec les sources sélectionnées.

    Adaptateur GUI : la boucle par morceau, les saves et le teardown vivent dans
    `src/services/enrichissement.run_async`, partagé avec la CLI.
    """
    if not app.selected_tracks:
        messagebox.showwarning("Attention", "Aucun morceau sélectionné")
        return

    # Les cases portent des IDENTIFIANTS : plus d'index à borner, et
    # `_is_track_disabled` juge l'OBJET.
    selected_tracks_list = [
        t
        for t in (app.current_artist.tracks or [])
        if t.id in app.selected_tracks and not app._is_track_disabled(t)
    ]
    if not selected_tracks_list:
        messagebox.showwarning("Attention", "Tous les morceaux sélectionnés sont désactivés")
        return

    options = enrichissement.OptionsEnrich(
        sources=tuple(sources),
        force_update=force_update,
        clear_on_failure=clear_on_failure,
        musicbrainz=musicbrainz,
    )
    disabled_count = len(app.selected_tracks) - len(selected_tracks_list)
    artist = app.current_artist
    app.enrich_button.configure(state="disabled", text="Enrichissement...")
    app.progress_bar.set(0)

    def update_progress(current, total, libelle, tache=""):
        progress = current / total if total else 0
        app.root.after(0, lambda: app.progress_var.set(progress))
        app.root.after(0, lambda: app.progress_label.configure(text=f"{tache}: {libelle}"))

    async def enrich_batch():
        try:
            bilan = await enrichissement.run_async(
                app.runtime,
                artist,
                selected_tracks_list,
                options,
                Hooks(progress=update_progress, should_stop=stop_requested),
            )
            texte = enrichissement.resume(bilan, options, desactives=disabled_count)
            app.root.after(
                0, lambda: report.show_scrollable_report(app, "Enrichissement terminé", texte)
            )
            app.root.after(0, app._update_artist_info)
            app.root.after(0, app._update_statistics)
            app.root.after(0, app._populate_tracks_table)
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Erreur lors de l'enrichissement: {error_msg}")
            app.root.after(
                0,
                lambda: messagebox.showerror(
                    "Erreur", f"Erreur lors de l'enrichissement: {error_msg}"
                ),
            )
        finally:
            app.root.after(
                0, lambda: app.enrich_button.configure(state="normal", text="Enrichir données")
            )
            app.root.after(0, lambda: app.progress_bar.set(0))
            app.root.after(0, lambda: app.progress_label.configure(text=""))

    async def enrich_batch_observe():
        """Le scope nomme l'artiste et le flux. Il est porté par une pile de
        process (pas une contextvar) : c'est ce qui le rend visible aussi bien
        depuis la boucle que depuis le thread du `sync_runner`."""
        with source_usage.run_scope(Flow.ENRICHMENT, artist_id=artist.id, artist_name=artist.name):
            return await enrich_batch()

    async_loop.start()  # idempotent : démarre la boucle au premier flux async
    async_loop.submit(enrich_batch_observe())
