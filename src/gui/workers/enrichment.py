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

import asyncio
from tkinter import messagebox

import customtkinter as ctk

from src.concurrency import async_loop
from src.gui.dialogs import report
from src.gui.workers.lifecycle import stop_requested
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
    sources_info = {
        "spotify_id": "Spotify ID Scraper (fallback automatique) 🎯",
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

        # Info supplémentaire pour spotify_id
        if source == "spotify_id":
            info_text = "Fallback : ReccoBeats résout d'abord via l'ISRC (Deezer).\nCe scraper n'est lancé que si aucun ISRC n'est trouvé — laisser coché suffit."
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

    # Checkbox pour reset Spotify ID
    reset_spotify_var = ctk.BooleanVar(value=False)
    reset_spotify_checkbox = ctk.CTkCheckBox(
        force_frame,
        text="🔄 Réinitialiser les Spotify IDs",
        variable=reset_spotify_var,
        font=("Arial", 12),
    )
    reset_spotify_checkbox.pack(anchor="w", pady=5)

    reset_info_label = ctk.CTkLabel(
        force_frame,
        text="Efface les Spotify IDs existants pour permettre\nleur re-scraping (utile si IDs incorrects)",
        font=("Arial", 9),
        text_color="gray",
    )
    reset_info_label.pack(anchor="w", padx=25, pady=2)

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

        force_update = force_var.get()
        reset_spotify_id = reset_spotify_var.get()
        clear_on_failure = clear_on_failure_var.get()

        dialog.destroy()
        run_enrichment(
            app,
            selected_sources,
            force_update=force_update,
            reset_spotify_id=reset_spotify_id,
            clear_on_failure=clear_on_failure,
        )

    ctk.CTkButton(dialog, text="Démarrer", command=start_enrichment).pack(pady=20)


def run_enrichment(
    app,
    sources: list[str],
    force_update: bool = False,
    reset_spotify_id: bool = False,
    clear_on_failure: bool = True,
):
    """Exécute l'enrichissement avec les sources sélectionnées"""
    if not app.selected_tracks:
        messagebox.showwarning("Attention", "Aucun morceau sélectionné")
        return

    selected_tracks_list = []
    for i in sorted(app.selected_tracks):
        if not app._is_track_disabled_by_index(i) and i < len(app.current_artist.tracks):
            selected_tracks_list.append(app.current_artist.tracks[i])

    if not selected_tracks_list:
        messagebox.showwarning("Attention", "Tous les morceaux sélectionnés sont désactivés")
        return

    # Reset des Spotify IDs si demandé
    if reset_spotify_id:
        for track in selected_tracks_list:
            if hasattr(track, "spotify_id") and track.spotify_id:
                old_id = track.spotify_id
                track.spotify_id = None
                logger.info(f"🔄 Spotify ID reset pour '{track.title}' (ancien: {old_id})")

    app.enrich_button.configure(state="disabled", text="Enrichissement...")
    app.progress_bar.set(0)

    def update_progress(current, total, info):
        """Callback de progression"""
        progress = current / total
        app.root.after(0, lambda: app.progress_var.set(progress))
        app.root.after(0, lambda: app.progress_label.configure(text=info))

    async def enrich_batch():
        try:
            # Ré-armer le disjoncteur BPM Finder (coupé après 3 échecs consécutifs
            # au run précédent — l'enricher vit toute la session GUI)
            app.data_enricher.reset_bpmfinder_breaker()

            # Préparer la liste complète des tracks de l'artiste pour validation
            all_artist_tracks = app.current_artist.tracks if app.current_artist else []

            # Compteurs pour le résumé
            cleaned_count = 0
            track_results = []  # Pour stocker les résultats détaillés par track

            # Enrichir chaque track individuellement
            for i, track in enumerate(selected_tracks_list):
                if stop_requested():
                    logger.info(
                        "⏹️ Fermeture demandée — enrichissement interrompu entre deux morceaux"
                    )
                    break
                update_progress(i, len(selected_tracks_list), f"Enrichissement: {track.title}")

                results = await app.data_enricher.enrich_track_async(
                    track,
                    sources=sources,
                    force_update=force_update,
                    artist_tracks=all_artist_tracks,
                    clear_on_failure=clear_on_failure,
                )

                # Compter les nettoyages
                if results.get("cleaned", False):
                    cleaned_count += 1

                # Stocker les résultats pour ce track
                track_results.append({"title": track.title, "results": results})

                # Sauvegarder après chaque enrichissement (SQLite hors boucle)
                await asyncio.to_thread(app.data_manager.save_track, track)

            disabled_count = len(app.selected_tracks) - len(selected_tracks_list)
            summary = _build_summary(
                track_results,
                treated_count=len(selected_tracks_list),
                disabled_count=disabled_count,
                force_update=force_update,
                reset_spotify_id=reset_spotify_id,
                clear_on_failure=clear_on_failure,
                cleaned_count=cleaned_count,
            )

            app.root.after(
                0, lambda s=summary: report.show_scrollable_report(app, "Enrichissement terminé", s)
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
            # Fermer les ressources des providers SUR LE THREAD SYNC DU RUN
            # (celui qui a créé les browsers — Playwright est thread-affine ;
            # ils seront recréés à la demande au batch suivant). Sans ça, un
            # browser survivait au batch et son pipe Playwright cassait à
            # l'arrêt de l'app (EPIPE cosmétique mais alarmant).
            runner = app.data_enricher.sync_runner
            try:
                await runner.run(app.data_enricher.close)
            except Exception:
                pass
            # Arrêter aussi l'instance Playwright THREAD-LOCALE du thread sync :
            # browsers fermés, le driver Node n'a plus de raison de survivre.
            try:
                await runner.run(_stop_playwright)
            except Exception:
                pass
            # Session httpx du batch (rouverte à la demande au suivant)
            try:
                await app.data_enricher.aclose_http()
            except Exception:
                pass
            app.root.after(
                0, lambda: app.enrich_button.configure(state="normal", text="Enrichir données")
            )
            app.root.after(0, lambda: app.progress_bar.set(0))
            app.root.after(0, lambda: app.progress_label.configure(text=""))

    async_loop.start()  # idempotent : démarre la boucle au premier flux async
    async_loop.submit(enrich_batch())


def _stop_playwright():
    """Arrêt de l'instance Playwright du thread appelant (helper picklable/nommé)."""
    from src.scrapers.playwright_manager import stop_playwright

    stop_playwright()


# Étiquettes courtes des sources (générique : toute source présente dans
# results est affichée, y compris bpmfinder — l'ancien code ne gérait qu'un
# sous-ensemble en dur → ligne vide si on ne cochait que BPM Finder).
_SRC_LABELS = {
    "spotify_id": "SP",
    "reccobeats": "RC",
    "getsongbpm": "GS",
    "songbpm": "SB",
    "bpmfinder": "BF",
    "deezer": "DZ",
    "discogs": "DC",
}
_META_KEYS = {"cleaned"}


def _status_char(v):
    if v == "not_needed":
        return "-"
    if v is None:
        return "?"  # crash/timeout
    return "✓" if v else "✗"


def _overall(results):
    vals = [v for k, v in results.items() if k not in _META_KEYS]
    chars = [_status_char(v) for v in vals]
    if "✓" in chars:
        return "✓"
    if "?" in chars:
        return "?"
    if chars and all(c == "-" for c in chars):
        return "-"
    return "✗"


def _build_summary(
    track_results,
    *,
    treated_count: int,
    disabled_count: int,
    force_update: bool,
    reset_spotify_id: bool,
    clear_on_failure: bool,
    cleaned_count: int,
) -> str:
    """Message de fin avec détails par morceau (pur — extrait du worker F2)."""
    summary = "Enrichissement terminé!\n\n"
    summary += f"Morceaux traités: {treated_count}\n\n"

    if force_update:
        summary += "✅ Mode force update activé\n"

    if reset_spotify_id:
        summary += "🔄 Spotify IDs réinitialisés\n"

    if clear_on_failure and cleaned_count > 0:
        summary += f"🗑️ {cleaned_count} morceau(x) nettoyé(s) (données erronées effacées)\n"

    summary += "\nDÉTAIL PAR MORCEAU:\n"
    summary += "Légende: ✓=succès | ✗=échec/absent | ?=crash/timeout | -=déjà présent\n\n"

    n_ok = n_fail = 0
    for track_result in track_results:
        title = track_result["title"]
        results = track_result["results"]
        if len(title) > 30:
            title = title[:27] + "..."

        overall = _overall(results)
        if overall == "✓":
            n_ok += 1
        elif overall in ("✗", "?"):
            n_fail += 1

        parts = [
            f"{_SRC_LABELS.get(k, k)}:{_status_char(v)}"
            for k, v in results.items()
            if k not in _META_KEYS
        ]
        detail = f"  {' | '.join(parts)}" if parts else ""
        summary += f"{overall} {title}\n{detail}\n" if detail else f"{overall} {title}\n"

    # Bilan chiffré en tête du détail
    summary = summary.replace(
        "\nDÉTAIL PAR MORCEAU:\n",
        f"\n✅ {n_ok} réussi(s) · ❌ {n_fail} échec(s)\n\nDÉTAIL PAR MORCEAU:\n",
        1,
    )

    if disabled_count > 0:
        summary += f"\n⚠️ {disabled_count} morceaux désactivés ignorés"

    return summary
