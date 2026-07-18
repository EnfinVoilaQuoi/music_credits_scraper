"""Récupération des morceaux de l'artiste (API Genius) en thread"""

from tkinter import messagebox

import customtkinter as ctk

from src.gui.dialogs import report
from src.gui.workers.lifecycle import run_worker, stop_requested
from src.utils.logger import get_logger

logger = get_logger(__name__)


def get_tracks(app):
    """Récupère les morceaux de l'artiste - VERSION AVEC FEATURES"""
    if not app.current_artist:
        return

    # Inclure les features
    dialog = ctk.CTkToplevel(app.root)
    dialog.title("Options de récupération")
    dialog.geometry("480x940")

    # Centrer la fenêtre
    dialog.update_idletasks()
    x = (dialog.winfo_screenwidth() // 2) - (240)
    y = (dialog.winfo_screenheight() // 2) - (470)
    dialog.geometry(f"480x940+{x}+{y}")

    dialog.lift()
    dialog.focus_force()
    dialog.grab_set()

    # Variables pour les options
    include_features_var = ctk.BooleanVar(value=True)  # Par défaut, inclure les features
    prefill_var = ctk.BooleanVar(value=True)  # Appel API album + Spotify/YouTube (media)
    include_secondary_var = ctk.BooleanVar(value=False)  # Rôles secondaires (Additional Voices…)
    respect_deleted_var = ctk.BooleanVar(value=True)  # Ne pas réajouter les morceaux supprimés
    download_images_var = ctk.BooleanVar(value=True)  # Télécharger photos/covers/vignettes (Media)

    # Interface
    ctk.CTkLabel(
        dialog, text="Options de récupération des morceaux", font=("Arial", 16, "bold")
    ).pack(pady=15)

    # Checkbox pour les features
    features_frame = ctk.CTkFrame(dialog)
    features_frame.pack(fill="x", padx=20, pady=15)

    ctk.CTkCheckBox(
        features_frame,
        text="Inclure les morceaux où l'artiste est en featuring",
        variable=include_features_var,
        font=("Arial", 12),
    ).pack(anchor="w", padx=15, pady=12)

    ctk.CTkLabel(
        features_frame,
        text="✓ Recommandé : permet de récupérer plus de morceaux",
        text_color="gray",
        font=("Arial", 10),
    ).pack(anchor="w", padx=15, pady=(0, 8))

    # Checkbox pour l'appel API album + media (Spotify/YouTube/relations)
    prefill_frame = ctk.CTkFrame(dialog)
    prefill_frame.pack(fill="x", padx=20, pady=(0, 5))

    ctk.CTkCheckBox(
        prefill_frame,
        text="Récupérer album + Spotify/YouTube (API media)",
        variable=prefill_var,
        font=("Arial", 12),
    ).pack(anchor="w", padx=15, pady=12)

    ctk.CTkLabel(
        prefill_frame,
        text="⚡ Appel API détail par morceau primaire (album, Spotify ID, lien YouTube, relations).\n"
        "Décocher = liste seule (plus rapide, le scrape rattrapera).",
        text_color="gray",
        font=("Arial", 10),
        justify="left",
    ).pack(anchor="w", padx=15, pady=(0, 8))

    # Checkbox pour les rôles secondaires (Additional Voices, chœurs…)
    secondary_frame = ctk.CTkFrame(dialog)
    secondary_frame.pack(fill="x", padx=20, pady=(0, 5))

    ctk.CTkCheckBox(
        secondary_frame,
        text="Inclure les rôles secondaires (chœurs, Additional Voices…)",
        variable=include_secondary_var,
        font=("Arial", 12),
    ).pack(anchor="w", padx=15, pady=12)

    ctk.CTkLabel(
        secondary_frame,
        text="🎙️ Vérifie chaque candidat au détail (id exact) → garde la vraie contribution\n"
        "avec son rôle, écarte les homonymes. Quelques appels API en plus.",
        text_color="gray",
        font=("Arial", 10),
        justify="left",
    ).pack(anchor="w", padx=15, pady=(0, 8))

    # Checkbox : ne pas réajouter les morceaux supprimés
    deleted_frame = ctk.CTkFrame(dialog)
    deleted_frame.pack(fill="x", padx=20, pady=(0, 5))

    ctk.CTkCheckBox(
        deleted_frame,
        text="Ne pas réajouter les morceaux supprimés",
        variable=respect_deleted_var,
        font=("Arial", 12),
    ).pack(anchor="w", padx=15, pady=12)

    _deleted_count = 0
    try:
        if app.current_artist:
            _deleted_count = len(
                app.deleted_tracks_manager.load_deleted_ids(app.current_artist.name)
            )
    except Exception:
        _deleted_count = 0
    ctk.CTkLabel(
        deleted_frame,
        text=f"🗂️ Respecte l'historique des suppressions ({_deleted_count} morceau(x) mémorisé(s)).\n"
        "Décocher = autorise leur retour à cet import.",
        text_color="gray",
        font=("Arial", 10),
        justify="left",
    ).pack(anchor="w", padx=15, pady=(0, 8))

    # Checkbox : télécharger les images (chantier « Media »)
    images_frame = ctk.CTkFrame(dialog)
    images_frame.pack(fill="x", padx=20, pady=(0, 5))

    ctk.CTkCheckBox(
        images_frame,
        text="Télécharger les images (photos, covers, vignettes)",
        variable=download_images_var,
        font=("Arial", 12),
    ).pack(anchor="w", padx=15, pady=12)

    ctk.CTkLabel(
        images_frame,
        text="🖼️ Photos d'artistes, pochettes et vignettes YouTube → data/images/.\n"
        "Idempotent (quasi-gratuit au 2ᵉ passage). Décocher = plus rapide.",
        text_color="gray",
        font=("Arial", 10),
        justify="left",
    ).pack(anchor="w", padx=15, pady=(0, 8))

    # Nombre maximum de morceaux
    max_songs_frame = ctk.CTkFrame(dialog)
    max_songs_frame.pack(fill="x", padx=20, pady=15)

    ctk.CTkLabel(max_songs_frame, text="Nombre maximum de morceaux:", font=("Arial", 12)).pack(
        anchor="w", padx=15, pady=(12, 5)
    )

    max_songs_entry = ctk.CTkEntry(max_songs_frame, width=100, placeholder_text="200")
    max_songs_entry.pack(anchor="w", padx=15, pady=(0, 12))
    max_songs_entry.insert(0, "200")

    # Info supplémentaire
    info_frame = ctk.CTkFrame(dialog)
    info_frame.pack(fill="x", padx=20, pady=15)

    info_text = """ℹ️ Les morceaux en featuring seront marqués avec 🎤
⚡ L'album et la date seront récupérés automatiquement via l'API
🔍 Le scraping ne sera utilisé que pour les crédits détaillés"""

    ctk.CTkLabel(
        info_frame, text=info_text, font=("Arial", 9), text_color="gray", justify="left"
    ).pack(anchor="w", padx=15, pady=10)

    # Boutons
    button_frame = ctk.CTkFrame(dialog)
    button_frame.pack(fill="x", padx=20, pady=20)

    def start_retrieval(update_only: bool = False):
        try:
            max_songs = int(max_songs_entry.get())
            if max_songs <= 0:
                max_songs = 300
        except ValueError:
            max_songs = 300

        include_features = include_features_var.get()
        prefill = prefill_var.get()
        include_secondary = include_secondary_var.get()
        respect_deleted = respect_deleted_var.get()
        download_images = download_images_var.get()
        dialog.destroy()
        start_track_retrieval(
            app,
            max_songs,
            include_features,
            prefill=prefill,
            update_only=update_only,
            include_secondary=include_secondary,
            respect_deleted=respect_deleted,
            download_images=download_images,
        )

    def cancel():
        dialog.destroy()

    ctk.CTkButton(
        button_frame,
        text="🎵 Récupérer",
        command=lambda: start_retrieval(update_only=False),
        width=120,
        height=35,
    ).pack(side="left", padx=6)
    ctk.CTkButton(
        button_frame,
        text="🔄 Mettre à jour",
        command=lambda: start_retrieval(update_only=True),
        fg_color="#2A8C4A",
        hover_color="#23733D",
        width=130,
        height=35,
    ).pack(side="left", padx=6)
    ctk.CTkButton(button_frame, text="❌ Annuler", command=cancel, width=90, height=35).pack(
        side="right", padx=6
    )


def start_track_retrieval(
    app,
    max_songs: int,
    include_features: bool,
    prefill: bool = True,
    update_only: bool = False,
    include_secondary: bool = False,
    respect_deleted: bool = True,
    download_images: bool = True,
):
    """Lance la récupération des morceaux avec les options choisies.

    prefill: appeler l'API détail (album + Spotify/YouTube media + relations).
    update_only: mode MàJ — n'appelle l'API media/album QUE pour les nouveaux
        titres (les genius_id déjà en base sont exclus du prefill).
    include_secondary: inclure les rôles secondaires (vérif détail + secondary_role).
    respect_deleted: ignorer les morceaux dont le genius_id est dans l'historique
        des suppressions (ne pas les réajouter).
    download_images: chantier « Media » — télécharger photos/covers/vignettes en
        fin de récupération (idempotent).
    """
    app.get_tracks_button.configure(state="disabled", text="Récupération...")

    # Message de progression plus informatif
    features_text = "avec features" if include_features else "sans features"
    mode_text = "MàJ" if update_only else "complet"
    app.progress_label.configure(
        text=f"Récupération {mode_text} de max {max_songs} morceaux ({features_text})..."
    )

    def get_tracks():
        try:
            # ✅ BACKUP AUTOMATIQUE avant récupération
            from src.utils.database_backup import get_backup_manager

            backup_manager = get_backup_manager()
            backup_path = backup_manager.create_backup("before_fetch_tracks")
            if backup_path:
                logger.info(f"💾 Backup créé: {backup_path.name}")

            logger.info(
                f"Début récupération: max_songs={max_songs}, include_features={include_features}"
            )

            # ✅ NOUVEAU : Charger les tracks existants AVANT la récupération
            existing_tracks = {}
            existing_by_title = {}  # Index par titre (insensible à la casse)
            if app.current_artist.tracks:
                for track in app.current_artist.tracks:
                    # Index principal : genius_id
                    if track.genius_id:
                        existing_tracks[track.genius_id] = track

                    # Index secondaire : (title, album) normalisé
                    key = (track.title.lower().strip(), (track.album or "").lower().strip())
                    existing_tracks[key] = track

                    # ✅ NOUVEAU : Index par titre seul pour détecter doublons de casse
                    title_key = track.title.lower().strip()
                    if title_key not in existing_by_title:
                        existing_by_title[title_key] = []
                    existing_by_title[title_key].append(track)

            logger.info(
                f"📦 {len(app.current_artist.tracks)} morceaux déjà en base avant récupération"
            )

            # Mode MàJ : exclure du prefill (album/media) les titres dont les
            # données media sont DÉJÀ en base. Exclure tous les genius_id connus
            # (ancien comportement) empêchait de rattraper les titres récupérés
            # avant la persistance de youtube_url/album (cf. JOURNAL 2026-07-02).
            known_genius_ids = None
            if update_only and app.current_artist.tracks:
                # Un lien YouTube 'search_auto' (recherche persistée) ne compte
                # PAS comme complet : l'appel Genius doit pouvoir le remplacer
                # par le lien officiel (genius_media).
                known_genius_ids = {
                    t.genius_id
                    for t in app.current_artist.tracks
                    if t.genius_id
                    and (t.album or t.album_override)
                    and t.spotify_id
                    and t.youtube_url
                    and t.youtube_url_source != "search_auto"
                }
                n_retry = sum(1 for t in app.current_artist.tracks if t.genius_id) - len(
                    known_genius_ids
                )
                logger.info(
                    f"🔄 MàJ : {len(known_genius_ids)} titres complets exclus du prefill API, "
                    f"{n_retry} connus mais incomplets (album/Spotify/YouTube) à re-tenter"
                )

            # Récupérer les morceaux via l'API avec l'option features
            new_tracks = app.genius_api.get_artist_songs(
                app.current_artist,
                max_songs=max_songs,
                include_features=include_features,
                prefill=prefill,
                known_genius_ids=known_genius_ids,
                include_secondary=include_secondary,
            )

            # Historique des suppressions : ne pas réajouter les morceaux supprimés
            if new_tracks:
                deleted_ids = app.deleted_tracks_manager.load_deleted_ids(app.current_artist.name)
                if deleted_ids:

                    def _gid_int(t):
                        try:
                            return int(t.genius_id) if t.genius_id else None
                        except (TypeError, ValueError):
                            return None

                    if respect_deleted:
                        before = len(new_tracks)
                        new_tracks = [t for t in new_tracks if _gid_int(t) not in deleted_ids]
                        skipped = before - len(new_tracks)
                        if skipped:
                            logger.info(
                                f"🗂️ {skipped} morceau(x) supprimé(s) ignoré(s) (historique)"
                            )
                    else:
                        # Réautorisés à cet import → purge de l'historique
                        for t in new_tracks:
                            gid = _gid_int(t)
                            if gid in deleted_ids:
                                app.deleted_tracks_manager.remove_deleted(
                                    app.current_artist.name, gid
                                )

            if new_tracks:
                # ✅ MERGE : Combiner les nouveaux tracks avec les existants
                new_count = 0
                updated_count = 0
                duplicates_avoided = 0

                for track in new_tracks:
                    if stop_requested():
                        logger.info("⏹️ Fermeture demandée — dédup interrompue")
                        break
                    # ✅ DÉTECTION MULTI-NIVEAUX DES DOUBLONS
                    existing_track = None

                    # Niveau 1 : Par genius_id (le plus fiable)
                    if track.genius_id and track.genius_id in existing_tracks:
                        existing_track = existing_tracks[track.genius_id]
                        updated_count += 1

                    # Niveau 2 : Par (title, album) exact
                    elif not existing_track:
                        key = (track.title.lower().strip(), (track.album or "").lower().strip())
                        if key in existing_tracks:
                            existing_track = existing_tracks[key]
                            updated_count += 1

                    # ✅ Niveau 3 : Par titre seul (détection doublons de casse)
                    if not existing_track:
                        title_key = track.title.lower().strip()
                        if title_key in existing_by_title:
                            candidates = existing_by_title[title_key]

                            # Si plusieurs versions, prendre la plus complète
                            if len(candidates) > 1:
                                logger.warning(
                                    f"⚠️ Doublon de casse détecté pour '{track.title}': {len(candidates)} versions"
                                )
                                duplicates_avoided += 1

                            # Prendre la première (ou la plus complète si on veut optimiser)
                            best_candidate = max(
                                candidates,
                                key=lambda t: (
                                    bool(t.album),
                                    bool(t.bpm),
                                    bool(t.lyrics),
                                    len(t.credits),
                                ),
                            )
                            existing_track = best_candidate
                            updated_count += 1
                        else:
                            new_count += 1

                    # Si le morceau existe, fusionner les données
                    if existing_track:
                        # Préserver les données enrichies existantes quand le track
                        # fraîchement récupéré de l'API ne les porte pas (l'API Genius
                        # ne fournit ni BPM, ni tonalité, ni paroles, ni certifs).
                        # (Avant 2026-07-13 : musical_key/lyrics/certifs étaient gardés
                        # par `not hasattr(track, …)` toujours faux → préservation morte.
                        # Alignés ici sur le pattern correct de bpm/credits.)
                        if not track.bpm and existing_track.bpm:
                            track.bpm = existing_track.bpm
                        if not track.musical_key and existing_track.musical_key:
                            track.musical_key = existing_track.musical_key
                        if not track.lyrics and existing_track.lyrics:
                            track.lyrics = existing_track.lyrics
                            track.has_lyrics = existing_track.has_lyrics
                        if not track.credits and existing_track.credits:
                            track.credits = existing_track.credits
                        if not track.certifications and existing_track.certifications:
                            track.certifications = existing_track.certifications
                        # Préserver l'ID de la base de données
                        track.id = existing_track.id

                # E7h : rematch des certifications depuis les CSV clean (matcher
                # en mémoire, offline, rapide) AVANT le save — pose
                # track.certifications/album_certifications à jour. Autorité = les
                # CSV clean ; écrase la préservation ci-dessus (certifs = données
                # dérivées, pas saisies). Défensif : n'interrompt pas la récup.
                try:
                    from src.utils.cert_matcher import get_cert_matcher
                    from src.utils.certification_enricher import apply_certifications

                    apply_certifications(app.current_artist, new_tracks, get_cert_matcher())
                except Exception as e:
                    logger.warning(f"Rematch certifications échoué: {e}")

                # Media 6 : télécharger les images AVANT la boucle save. apply_images
                # MUTE les tracks (cover_path/yt_thumbnail_path) + l'artiste
                # (image_path) que le save existant persiste ensuite. Idempotent,
                # `should_stop` respecté. Défensif : n'interrompt pas la récup.
                if download_images:
                    try:
                        from src.api.deezer_api import DeezerAPI
                        from src.utils.media_enricher import apply_images

                        media_report = apply_images(
                            app.current_artist,
                            new_tracks,
                            deezer=DeezerAPI(),
                            genius=app.genius_api,
                            should_stop=stop_requested,
                        )
                        if app.current_artist.image_path:
                            app.data_manager.set_artist_image_path(
                                app.current_artist.id, app.current_artist.image_path
                            )
                        logger.info(f"🖼️ Images: {media_report.total_downloaded()} téléchargée(s)")
                    except Exception as e:
                        logger.warning(f"Téléchargement des images échoué: {e}")

                # Sauvegarder dans la base
                saved_count = 0
                for track in new_tracks:
                    if stop_requested():
                        logger.info(
                            "⏹️ Fermeture demandée — sauvegarde interrompue entre deux morceaux"
                        )
                        break
                    try:
                        app.data_manager.save_track(track)
                        saved_count += 1
                    except Exception as e:
                        logger.warning(f"Erreur sauvegarde {track.title}: {e}")

                # ✅ CORRECTION : Recharger TOUS les tracks depuis la base après sauvegarde
                app.current_artist.tracks = app.data_manager.get_artist_tracks(
                    app.current_artist.id
                )
                app.tracks = app.current_artist.tracks

                logger.info(
                    f"✅ Merge terminé : {new_count} nouveaux, {updated_count} mis à jour, {saved_count} sauvegardés, {duplicates_avoided} doublons évités"
                )

                # Analyser les résultats
                featuring_count = sum(1 for t in app.current_artist.tracks if t.is_featuring)
                api_albums = sum(1 for t in new_tracks if t.album)
                api_dates = sum(1 for t in new_tracks if t.release_date)

                # Message de succès détaillé
                success_msg = (
                    f"✅ {len(new_tracks)} morceaux récupérés pour {app.current_artist.name}"
                )
                success_msg += f"\n🆕 {new_count} nouveaux morceaux"
                success_msg += f"\n🔄 {updated_count} morceaux mis à jour"

                if duplicates_avoided > 0:
                    success_msg += f"\n🚫 {duplicates_avoided} doublons évités"

                if featuring_count > 0:
                    success_msg += f"\n🎤 {featuring_count} morceaux en featuring (total)"

                success_msg += f"\n💿 {api_albums} albums récupérés via l'API"
                success_msg += f"\n📅 {api_dates} dates de sortie récupérées via l'API"
                success_msg += f"\n💾 {saved_count} morceaux sauvegardés en base"
                success_msg += f"\n📊 Total en base : {len(app.current_artist.tracks)} morceaux"

                app.root.after(0, app._update_artist_info)
                app.root.after(
                    0,
                    lambda m=success_msg: report.show_scrollable_report(
                        app, "Récupération terminée", m
                    ),
                )

                logger.info(
                    f"Récupération terminée avec succès: {len(new_tracks)} nouveaux, total: {len(app.current_artist.tracks)} morceaux"
                )

            else:
                app.root.after(
                    0,
                    lambda: messagebox.showwarning(
                        "Attention",
                        "Aucun morceau trouvé.\n\nVérifiez le nom de l'artiste ou essayez avec les features activées.",
                    ),
                )
                logger.warning("Aucun morceau trouvé")

        except Exception as e:
            error_msg = str(e) if str(e) else "Erreur inconnue lors de la récupération"
            logger.error(f"Erreur lors de la récupération des morceaux: {error_msg}")
            app.root.after(
                0,
                lambda: messagebox.showerror(
                    "Erreur", f"Erreur lors de la récupération:\n{error_msg}"
                ),
            )
        finally:
            app.root.after(
                0, lambda: app.get_tracks_button.configure(state="normal", text="Discographie")
            )
            app.root.after(0, lambda: app.progress_label.configure(text=""))

    run_worker(get_tracks, name="retrieval")


# ✅ AJOUT DES MÉTHODES MANQUANTES POUR FONCTIONNALITÉS EXISTANTES
