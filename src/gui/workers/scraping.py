"""Scraping combiné crédits/paroles (Genius v3, Discogs) — flux async (Phase F5).

Le batch est piloté par `run_worker` : un thread daemon enregistré porte le
corps sync (batchs Genius v3, clients Discogs/LRCLIB/YTM/Musixmatch), une
coroutine soumise à la boucle unique le représente pour la coordination
d'arrêt. Les CRAWLS Genius s'exécutent nativement dans la boucle (pont F4
`run_sync` depuis le thread) : le thread ne porte plus que parsing + LLM +
saves. `stop_requested()` est testé entre deux unités, comme avant.
"""

from datetime import datetime
from tkinter import messagebox

from src.gui.dialogs import report
from src.gui.workers.lifecycle import run_worker, stop_requested
from src.scrapers.genius_scraper_v3 import GeniusScraperV3
from src.utils.logger import get_logger
from src.utils.synced_lyrics_resolver import resolve_track_synced_lyrics

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
    # Rétro-compatibilité : anciens appels sans paramètres de synchro.
    if scrape_sync is None:
        scrape_sync = bool(sync_lrclib or sync_ytm or sync_musixmatch)

    # Filtrer les morceaux sélectionnés ET actifs
    selected_tracks_list = []
    for i in sorted(app.selected_tracks):
        if not app._is_track_disabled_by_index(i):
            selected_tracks_list.append(app.current_artist.tracks[i])

    if not selected_tracks_list:
        messagebox.showwarning("Attention", "Tous les morceaux sélectionnés sont désactivés")
        return

    # Vérifier si déjà en cours
    if app.is_scraping:
        messagebox.showinfo(
            "Scraping en cours", "Un scraping est déjà en cours. Veuillez patienter."
        )
        return

    # Message de confirmation
    disabled_count = len(app.selected_tracks) - len(selected_tracks_list)
    tasks = []
    if scrape_genius or scrape_discogs:
        sources = []
        if scrape_genius:
            sources.append("Genius")
        if scrape_discogs:
            sources.append("Discogs")
        tasks.append(f"Crédits {'/'.join(sources)}{'(forcé)' if force_credits else ''}")
    if scrape_lyrics:
        tasks.append(f"Paroles{'(forcé)' if force_lyrics else ''}")
    if scrape_sync:
        sync_srcs = []
        if sync_lrclib:
            sync_srcs.append("LRCLIB")
        if sync_ytm:
            sync_srcs.append("YTM")
        if sync_musixmatch:
            sync_srcs.append("Musixmatch")
        tasks.append(f"Timestamps {'/'.join(sync_srcs)}{'(forcé)' if force_sync else ''}")

    confirm_msg = f"Scraping de {', '.join(tasks)}\n\n"
    confirm_msg += f"📊 Morceaux: {len(selected_tracks_list)}\n"
    if disabled_count > 0:
        confirm_msg += f"⚠️ {disabled_count} désactivés ignorés\n"
    time_per_track = 0
    if scrape_genius:
        time_per_track += 3
    if scrape_discogs:
        time_per_track += 2
    if scrape_lyrics:
        time_per_track += 2
    if scrape_sync:
        time_per_track += 2
    confirm_msg += f"\n⏱️ Temps estimé : ~{len(selected_tracks_list) * time_per_track:.0f}s"

    result = messagebox.askyesno("Crédits & Paroles", confirm_msg)

    if not result:
        return

    # Afficher la barre de progression
    app._show_progress_bar()
    app.is_scraping = True
    app._update_buttons_state()

    app.scrape_button.configure(state="disabled", text="Scraping...")
    app.progress_bar.set(0)

    def update_progress(current, total, track_name, task=""):
        """Callback de progression"""
        progress = current / total
        app.root.after(0, lambda: app.progress_var.set(progress))
        task_str = f" [{task}]" if task else ""
        app.root.after(
            0,
            lambda: app.progress_label.configure(
                text=f"{current}/{total}{task_str} - {track_name[:25]}..."
            ),
        )

    def scrape():
        scraper = None
        genius_credits_results = None
        discogs_credits_results = None
        lyrics_results = None
        sync_results = None

        try:
            logger.info(f"Début du scraping combiné de {len(selected_tracks_list)} morceaux")

            total_tasks = (
                (1 if scrape_genius else 0)
                + (1 if scrape_discogs else 0)
                + (1 if (scrape_lyrics or scrape_sync) else 0)
            )
            current_task = 0

            # Scraping Genius crédits
            if scrape_genius:
                current_task += 1
                logger.info(f"[{current_task}/{total_tasks}] Scraping des crédits Genius...")

                scraper = GeniusScraperV3(headless=True)

                if force_credits:
                    # Effacer les crédits Genius existants pour forcer le re-scraping
                    for track in selected_tracks_list:
                        # Garder les crédits Discogs, supprimer uniquement ceux de Genius
                        track.credits = [c for c in track.credits if c.source != "genius"]
                        track.credits_scraped_at = None

                genius_credits_results = scraper.scrape_multiple_tracks(
                    selected_tracks_list,
                    progress_callback=lambda c, t, n: update_progress(c, t, n, "Genius"),
                )

            if stop_requested():
                logger.info("⏹️ Fermeture demandée — phases restantes du scraping annulées")
                return

            # Scraping Discogs crédits
            if scrape_discogs:
                current_task += 1
                logger.info(f"[{current_task}/{total_tasks}] Scraping des crédits Discogs...")

                import os

                from src.api.discogs_api import DiscogsClient

                discogs_token = os.getenv("DISCOGS_TOKEN") or os.getenv("DISCOGS_USER_TOKEN")
                discogs_client = DiscogsClient(user_token=discogs_token)

                if force_credits:
                    # Effacer les crédits Discogs existants pour forcer le re-scraping
                    for track in selected_tracks_list:
                        # Garder les crédits Genius, supprimer uniquement ceux de Discogs
                        track.credits = [c for c in track.credits if c.source != "discogs"]

                discogs_success = 0
                discogs_failed = 0
                for i, track in enumerate(selected_tracks_list, 1):
                    if stop_requested():
                        logger.info(
                            "⏹️ Fermeture demandée — Discogs interrompu entre deux morceaux"
                        )
                        break
                    try:
                        update_progress(i, len(selected_tracks_list), track.title, "Discogs")

                        if discogs_client.enrich_track_data(track, force_update=force_credits):
                            discogs_success += 1
                        else:
                            discogs_failed += 1
                    except Exception as e:
                        logger.error(f"Erreur Discogs pour {track.title}: {e}")
                        discogs_failed += 1

                discogs_credits_results = {"success": discogs_success, "failed": discogs_failed}

            # Paroles (TEXTE structuré = Genius/YTM) et/ou TIMESTAMPS (LRCLIB/YTM/Musixmatch)
            if scrape_lyrics or scrape_sync:
                current_task += 1
                logger.info(f"[{current_task}/{total_tasks}] Récupération paroles/timestamps...")

                if stop_requested():
                    logger.info("⏹️ Fermeture demandée — phase paroles/synchro annulée")
                    return

                # Mise à jour forcée : TEXTE et SYNCHRO sont désormais indépendants.
                if force_lyrics:
                    for track in selected_tracks_list:
                        track.lyrics = None
                        track.anecdotes = None
                        track.has_lyrics = False
                        track.lyrics_scraped_at = None
                        track.lyrics_source = None
                if force_sync:
                    for track in selected_tracks_list:
                        track.lyrics_synced = None
                        track.lyrics_synced_source = None
                        track.lyrics_synced_confidence = None
                        # E7d : « force » = repartir de zéro. Purger les obs
                        # lyrics persistées, sinon une source disparue laisserait
                        # une obs stale qui ressusciterait le verdict à la lecture.
                        if track.id:
                            app.data_manager.delete_observations(track.id, "lyrics_synced")

                n_tracks = len(selected_tracks_list)

                # 1) TEXTE STRUCTURÉ : Genius (sections [Couplet : artiste]). Le batch
                #    skippe les morceaux déjà pourvus (ex. via la phase crédits Genius).
                if lyrics_genius:
                    need_text = [t for t in selected_tracks_list if not (t.has_lyrics and t.lyrics)]
                    if need_text:
                        if scraper is None:
                            scraper = GeniusScraperV3(headless=True)
                        lyrics_results = scraper.scrape_lyrics_batch(
                            selected_tracks_list,
                            progress_callback=lambda c, t, n: update_progress(
                                c, t, n, "Paroles (Genius)"
                            ),
                        )
                    for t in selected_tracks_list:
                        if t.has_lyrics and t.lyrics and not getattr(t, "lyrics_source", None):
                            t.lyrics_source = "genius"

                # 2) TIMESTAMPS (paroles synchronisées) — sources cochées dans le dialogue :
                #    SOURCE 1 = LRCLIB, SOURCE 2 = YTM, cross-check + départage par la durée
                #    (cf. lyrics_sync.compare_synced). SOURCE 3 = Musixmatch, appelée UNIQUEMENT
                #    en dernier recours quand LRCLIB et YTM n'ont rien donné (API privée, gated).
                #    YTM sert aussi de fallback TEXTE (indépendant, piloté par la section Paroles).
                #    Durée : track.duration (Deezer, canonique) sinon duration_seconds YTM (secours),
                #    car Genius ne fournit pas la durée (voir docs/api/genius-api.md).
                if scrape_sync or lyrics_ytm:
                    try:
                        # Instanciation paresseuse : seulement les clients des sources cochées.
                        lrclib = ytm = mxm = None
                        if sync_lrclib:
                            from src.api.lrclib_api import LRCLIBAPI

                            lrclib = LRCLIBAPI()
                        if sync_ytm or lyrics_ytm:
                            from src.api.ytmusic_api import YTMusicAPI

                            ytm = YTMusicAPI()
                        if sync_musixmatch:
                            from src.api.musixmatch_api import MusixmatchAPI

                            mxm = MusixmatchAPI()  # token en cache réutilisé sur tout le batch

                        n_lrclib, n_ytm, n_mxm, n_cross, n_review, n_text = 0, 0, 0, 0, 0, 0
                        for i, track in enumerate(selected_tracks_list):
                            if stop_requested():
                                logger.info(
                                    "⏹️ Fermeture demandée — synchro interrompue entre deux morceaux"
                                )
                                break
                            has_sync = bool(getattr(track, "lyrics_synced", None))
                            need_sync = scrape_sync and not (has_sync and not force_sync)
                            need_text = lyrics_ytm and not (track.has_lyrics and track.lyrics)
                            if not need_sync and not need_text:
                                continue

                            # Nom d'artiste (feat → artiste principal)
                            if getattr(track, "is_featuring", False) and getattr(
                                track, "primary_artist_name", None
                            ):
                                a_name = track.primary_artist_name
                            elif track.artist:
                                a_name = track.artist.name
                            else:
                                a_name = app.current_artist.name

                            # Résolution timestamps + texte (cœur extrait, GUI-free).
                            outcome = resolve_track_synced_lyrics(
                                track,
                                a_name,
                                lrclib=lrclib,
                                ytm=ytm,
                                mxm=mxm,
                                need_sync=need_sync,
                                need_text=need_text,
                                sync_ytm=sync_ytm,
                            )
                            track.observations.extend(outcome.observations)
                            if outcome.lyrics_synced is not None:
                                track.lyrics_synced = outcome.lyrics_synced
                                track.lyrics_synced_source = outcome.lyrics_synced_source
                                track.lyrics_synced_confidence = outcome.lyrics_synced_confidence
                                if outcome.synced_kind == "lrclib":
                                    n_lrclib += 1
                                elif outcome.synced_kind == "ytm":
                                    n_ytm += 1
                                elif outcome.synced_kind == "musixmatch":
                                    n_mxm += 1
                                if outcome.synced_is_cross:
                                    n_cross += 1
                                else:
                                    n_review += 1
                            if outcome.text is not None:
                                track.lyrics = outcome.text
                                track.has_lyrics = True
                                track.lyrics_scraped_at = datetime.now()
                                track.lyrics_source = outcome.text_source
                                n_text += 1
                            update_progress(i + 1, n_tracks, track.title, "Timestamps")
                        logger.info(
                            f"⏱ Synchro : {n_lrclib} LRCLIB, {n_ytm} YTM, {n_mxm} Musixmatch ; "
                            f"{n_cross} croisé(s), {n_review} à vérifier ; {n_text} texte(s) fallback"
                        )
                        sync_results = {
                            "lrclib": n_lrclib,
                            "ytm": n_ytm,
                            "musixmatch": n_mxm,
                            "cross": n_cross,
                            "review": n_review,
                        }
                    except Exception as e:
                        logger.warning(f"Passe synchro (timestamps) échouée: {e}")

                if lyrics_results is None:
                    n_ok = sum(1 for t in selected_tracks_list if t.has_lyrics and t.lyrics)
                    lyrics_results = {
                        "success": n_ok,
                        "failed": n_tracks - n_ok,
                        "errors": [],
                        "lyrics_scraped": n_ok,
                    }

            # Sauvegarder les données mises à jour
            for track in selected_tracks_list:
                track.artist = app.current_artist
                app.data_manager.save_track(track)

            # Afficher le résumé
            success_msg = "Scraping terminé !\n\n"

            if genius_credits_results:
                success_msg += "🎵 Crédits Genius:\n"
                success_msg += f"  - Réussis: {genius_credits_results['success']}\n"
                success_msg += f"  - Échoués: {genius_credits_results['failed']}\n"
                if genius_credits_results.get("errors"):
                    success_msg += f"  - Erreurs: {len(genius_credits_results['errors'])}\n"
                success_msg += "\n"

            if discogs_credits_results:
                success_msg += "💿 Crédits Discogs:\n"
                success_msg += f"  - Réussis: {discogs_credits_results['success']}\n"
                success_msg += f"  - Échoués: {discogs_credits_results['failed']}\n"
                success_msg += "\n"

            if lyrics_results:
                success_msg += "📝 Paroles:\n"
                success_msg += f"  - Réussis: {lyrics_results['success']}\n"
                success_msg += f"  - Échoués: {lyrics_results['failed']}\n"
                if lyrics_results.get("errors"):
                    success_msg += f"  - Erreurs: {len(lyrics_results['errors'])}\n"

            if sync_results and scrape_sync:
                success_msg += "\n⏱ Timestamps (synchro):\n"
                success_msg += f"  - LRCLIB: {sync_results['lrclib']} • YTM: {sync_results['ytm']} • Musixmatch: {sync_results['musixmatch']}\n"
                success_msg += f"  - Croisés (conf. 2): {sync_results['cross']}\n"
                if sync_results["review"]:
                    success_msg += f"  - À vérifier (conf. 1): {sync_results['review']}\n"

            if disabled_count > 0:
                success_msg += f"\n⚠️ {disabled_count} morceaux désactivés ignorés"

            app.root.after(
                0, lambda m=success_msg: report.show_scrollable_report(app, "Scraping terminé", m)
            )

            # Mettre à jour l'affichage
            app.root.after(0, app._update_artist_info)
            app.root.after(0, app._update_statistics)

            # Rafraîchir la fenêtre de détails si elle est ouverte
            app.root.after(0, app._refresh_detail_window_if_open)

        except Exception as err:
            error_msg = str(err) if str(err) != "None" else "Erreur inconnue lors du scraping"
            logger.error(f"Erreur lors du scraping combiné: {error_msg}", exc_info=True)
            app.root.after(
                0, lambda: messagebox.showerror("Erreur", f"Erreur lors du scraping: {error_msg}")
            )
        finally:
            # S'assurer que le scraper est fermé
            if scraper:
                try:
                    scraper.close()
                except Exception:
                    pass

            app.is_scraping = False
            app.root.after(
                0, lambda: app.scrape_button.configure(state="normal", text="Crédits & Paroles")
            )
            app.root.after(0, app._hide_progress_bar)
            app.root.after(0, lambda: app.progress_label.configure(text=""))
            app.root.after(0, app._update_buttons_state)

    run_worker(scrape, name="scraping")
