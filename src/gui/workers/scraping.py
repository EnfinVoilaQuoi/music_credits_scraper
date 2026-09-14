"""Scraping combiné crédits/paroles/timestamps — adaptateur GUI (Phase F5).

La logique (trois phases, resets forcés, `need_*`, sauvegarde) vit dans
`src/services/credits.run`, partagée avec la CLI. Ici : sélection des morceaux
cochés et actifs, confirmation, barre de progression, compte rendu, worker.
Le corps tourne sur un thread `run_worker` (les crawls Genius passent par le
pont `run_sync` → jamais depuis la boucle).
"""

from tkinter import messagebox

from src.concurrency.lifecycle import run_worker, stop_requested
from src.gui.dialogs import report
from src.observability import source_usage
from src.observability.registry import Flow
from src.services import credits
from src.services.runtime import Hooks
from src.utils.logger import get_logger

logger = get_logger(__name__)


def start_combined_scraping(
    app,
    scrape_genius=False,
    scrape_discogs=False,
    force_credits=False,
    scrape_lyrics=False,
    force_lyrics=False,
    lyrics_ytm=True,
    lyrics_genius=True,
    scrape_sync=None,
    sync_lrclib=True,
    sync_ytm=True,
    sync_musixmatch=False,
    force_sync=False,
):
    """
    Lance le scraping combiné des crédits (Genius/Discogs), des paroles (texte) et/ou
    des timestamps (synchro : LRCLIB / YTM / Musixmatch) avec options de mise à jour forcée.

    `scrape_sync` : None => rétro-compat (déduit de sync_lrclib/sync_ytm/sync_musixmatch).
    """
    if scrape_sync is None:
        scrape_sync = bool(sync_lrclib or sync_ytm or sync_musixmatch)
    options = credits.OptionsCredits(
        genius=scrape_genius,
        discogs=scrape_discogs,
        force_credits=force_credits,
        paroles_genius=scrape_lyrics and lyrics_genius,
        paroles_ytm=scrape_lyrics and lyrics_ytm,
        force_paroles=force_lyrics,
        sync_lrclib=scrape_sync and sync_lrclib,
        sync_ytm=scrape_sync and sync_ytm,
        sync_musixmatch=scrape_sync and sync_musixmatch,
        force_sync=force_sync,
    )

    # Filtrer les morceaux sélectionnés ET actifs
    selected_tracks_list = [
        app.current_artist.tracks[i]
        for i in sorted(app.selected_tracks)
        if not app._is_track_disabled_by_index(i)
    ]
    if not selected_tracks_list:
        messagebox.showwarning("Attention", "Tous les morceaux sélectionnés sont désactivés")
        return
    if app.is_scraping:
        messagebox.showinfo(
            "Scraping en cours", "Un scraping est déjà en cours. Veuillez patienter."
        )
        return

    disabled_count = len(app.selected_tracks) - len(selected_tracks_list)
    confirm_msg = f"Scraping de {', '.join(options.taches())}\n\n"
    confirm_msg += f"📊 Morceaux: {len(selected_tracks_list)}\n"
    if disabled_count > 0:
        confirm_msg += f"⚠️ {disabled_count} désactivés ignorés\n"
    estime = len(selected_tracks_list) * options.secondes_par_morceau()
    confirm_msg += f"\n⏱️ Temps estimé : ~{estime:.0f}s"
    if not messagebox.askyesno("Crédits & Paroles", confirm_msg):
        return

    app._show_progress_bar()
    app.is_scraping = True
    app._update_buttons_state()
    app.scrape_button.configure(state="disabled", text="Scraping...")
    app.progress_bar.set(0)
    artist = app.current_artist

    def update_progress(current, total, track_name, task=""):
        progress = current / total if total else 0
        app.root.after(0, lambda: app.progress_var.set(progress))
        task_str = f" [{task}]" if task else ""
        app.root.after(
            0,
            lambda: app.progress_label.configure(
                text=f"{current}/{total}{task_str} - {track_name[:25]}..."
            ),
        )

    def scrape():
        try:
            bilan = credits.run(
                app.runtime,
                artist,
                selected_tracks_list,
                options,
                Hooks(progress=update_progress, should_stop=stop_requested),
            )
            texte = credits.resume(bilan, options, desactives=disabled_count)
            app.root.after(0, lambda: report.show_scrollable_report(app, "Scraping terminé", texte))
            app.root.after(0, app._update_artist_info)
            app.root.after(0, app._update_statistics)
            app.root.after(0, app._refresh_detail_window_if_open)
        except Exception as err:
            error_msg = str(err) if str(err) != "None" else "Erreur inconnue lors du scraping"
            logger.error(f"Erreur lors du scraping combiné: {error_msg}", exc_info=True)
            app.root.after(
                0, lambda: messagebox.showerror("Erreur", f"Erreur lors du scraping: {error_msg}")
            )
        finally:
            app.is_scraping = False
            app.root.after(
                0, lambda: app.scrape_button.configure(state="normal", text="Crédits & Paroles")
            )
            app.root.after(0, app._hide_progress_bar)
            app.root.after(0, lambda: app.progress_label.configure(text=""))
            app.root.after(0, app._update_buttons_state)

    def scrape_observe():
        with source_usage.run_scope(Flow.ENRICHMENT, artist_id=artist.id, artist_name=artist.name):
            return scrape()

    run_worker(scrape_observe, name="scraping")
