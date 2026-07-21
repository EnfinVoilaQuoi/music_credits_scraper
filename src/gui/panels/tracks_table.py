"""Vue « morceaux » : table, tri, sélection, menus contextuels, activation/désactivation.
Le Treeview lui-même appartient à MainWindow (widget partagé avec la vue albums)."""

import tkinter
from datetime import datetime
from tkinter import messagebox

from src.gui import helpers
from src.gui.dialogs import manual_entry, merge_tracks, report
from src.gui.panels import albums_view
from src.utils.logger import get_logger

logger = get_logger(__name__)


def configure_tree_for_tracks(app):
    """Colonnes de la vue Morceaux (vue par défaut)"""
    app.tree.configure(columns=app.TRACK_COLUMNS)
    app.tree.heading("#0", text="✓")
    app.tree.column("#0", width=50, stretch=False)
    widths = {
        "Titre": (220, "w"),
        "Artiste principal": (130, "w"),
        "Album": (160, "w"),
        "Date sortie": (80, "w"),
        "Crédits": (70, "center"),
        "Paroles": (60, "center"),
        "BPM": (90, "w"),
        "Durée": (70, "center"),
        "Certif.": (50, "center"),
        "Streams": (120, "e"),
        "Statut": (70, "w"),
    }
    for col in app.TRACK_COLUMNS:
        w, anchor = widths[col]
        app.tree.heading(col, text=col, command=lambda c=col: sort_column(app, c))
        app.tree.column(col, width=w, anchor=anchor)


def populate_tracks_table(app):
    """Remplit le tableau avec les morceaux - VERSION CORRIGÉE CRÉDITS"""
    # En vue Albums, rafraîchir la vue Albums à la place
    if getattr(app, "view_mode", "tracks") == "albums":
        albums_view.populate_albums_table(app)
        return

    # Nettoyer le tableau
    for item in app.tree.get_children():
        app.tree.delete(item)

    if not app.current_artist or not app.current_artist.tracks:
        return

    # Charger les morceaux désactivés depuis la mémoire (IDs, pas indices)
    try:
        app.disabled_tracks = app.disabled_tracks_manager.load_disabled_tracks(
            app.current_artist.name
        )
    except Exception as e:
        logger.debug(f"Pas de morceaux désactivés sauvegardés: {e}")
        app.disabled_tracks = set()

    # Ajouter les morceaux au tableau
    for i, track in enumerate(app.current_artist.tracks):
        try:
            # Déterminer si le morceau est désactivé (par ID, pas par index)
            is_disabled = app._is_track_disabled(track)

            # Formatage des valeurs
            title = track.title or f"Track {i+1}"

            # Artiste principal - gestion du featuring
            if track.is_featuring and track.primary_artist_name:
                artist_display = track.primary_artist_name
            else:
                artist_display = track.artist.name if track.artist else ""

            # Rôle secondaire (Additional Voices…) : marqueur distinct du feat
            _sec_role = track.secondary_role
            if _sec_role:
                artist_display = f"{artist_display} · 🎙️ {_sec_role}"
                title = f"🎙️ {title}"

            album = track.album or ""

            # Date de sortie - FORMAT FRANÇAIS (JJ/MM/AAAA)
            release_date = ""
            if track.release_date:
                try:
                    if isinstance(track.release_date, str):
                        # Convertir string ISO vers datetime puis vers format français
                        from datetime import datetime

                        dt = datetime.fromisoformat(
                            track.release_date.replace("Z", "+00:00").split("T")[0]
                        )
                        release_date = dt.strftime("%d/%m/%Y")
                    else:
                        # Déjà un objet datetime
                        release_date = track.release_date.strftime("%d/%m/%Y")
                except Exception:
                    release_date = (
                        str(track.release_date).split("T")[0]
                        if "T" in str(track.release_date)
                        else str(track.release_date)
                    )

            # CORRECTION: Obtenir le nombre de crédits directement
            credits_count = 0
            if track.credits:
                credits_count = len(track.credits)
            credits_display = str(credits_count)

            # Paroles : ✓ = texte, ⏱ = timestamps (paroles synchronisées) en plus
            has_lyrics_flag = track.has_lyrics
            has_sync_flag = bool(track.lyrics_synced)
            if has_lyrics_flag and has_sync_flag:
                lyrics_display = "✓⏱"
            elif has_sync_flag:
                lyrics_display = "⏱"
            elif has_lyrics_flag:
                lyrics_display = "✓"
            else:
                lyrics_display = ""

            # BPM avec tonalité - VERSION AMÉLIORÉE
            bpm = ""  # ⭐ IMPORTANT : Initialiser la variable
            if track.audio.bpm:
                bpm = str(track.audio.bpm)

                # ⭐ LOGIQUE AMÉLIORÉE pour afficher la tonalité
                musical_key = None

                # 1. Essayer musical_key directement
                if track.audio.musical_key:
                    musical_key = track.audio.musical_key

                # 2. FALLBACK : key/mode du sous-objet audio (Phase 5)
                elif track.audio.key and track.audio.mode:
                    try:
                        from src.utils.music_theory import key_mode_to_french_from_string

                        musical_key = key_mode_to_french_from_string(
                            track.audio.key, track.audio.mode
                        )

                        # ⭐ BONUS : Stocker le résultat pour la prochaine fois
                        track.audio.musical_key = musical_key
                        logger.debug(
                            f"Musical key calculée et stockée pour '{track.title}': {musical_key}"
                        )
                    except Exception as e:
                        logger.warning(f"Erreur conversion key/mode pour '{track.title}': {e}")

                # Ajouter la tonalité au BPM si disponible
                if musical_key:
                    bpm = f"{track.audio.bpm} ({musical_key})"

            # Durée du morceau
            duration_display = ""
            if track.duration:
                try:
                    # Format MM:SS ou HH:MM:SS
                    if isinstance(track.duration, str):
                        duration_display = track.duration
                    elif isinstance(track.duration, int):
                        # Durée en secondes
                        minutes = track.duration // 60
                        seconds = track.duration % 60
                        duration_display = f"{minutes}:{seconds:02d}"
                except Exception:
                    pass

            # Certifications - Lire depuis track.certifications au lieu de l'API
            certif_display = ""
            try:
                # Vérifier si le track a des certifications stockées
                if track.certifications:
                    # Prendre la plus haute certification (première dans la liste déjà triée)
                    cert_level = track.certifications[0].get("certification", "")
                    emoji_map = {
                        "Or": "🥇",
                        "Double Or": "🥇🥇",
                        "Triple Or": "🥇🥇🥇",
                        "Platine": "💿",
                        "Double Platine": "💿💿",
                        "Triple Platine": "💿💿💿",
                        "Diamant": "💎",
                        "Double Diamant": "💎💎",
                        "Triple Diamant": "💎💎💎",
                        "Quadruple Diamant": "💎💎💎💎",
                    }
                    certif_display = emoji_map.get(cert_level, "✓")
            except Exception:
                pass

            # Statut - Utiliser votre fonction existante _get_track_status_icon
            status = helpers.get_track_status_icon(track, app.disabled_tracks)

            # Streams estimés
            try:
                from src.utils.streams_calculator import (
                    calculate_total_streams,
                    format_streams,
                    streams_source_label,
                )

                sp = track.streams.spotify_streams
                yt = track.streams.ytm_streams
                streams_total = calculate_total_streams(sp, yt)
                streams_display = format_streams(streams_total, streams_source_label(sp, yt))
            except Exception:
                streams_display = ""

            # Case à cocher selon la sélection
            checkbox = "☑" if i in app.selected_tracks else "☐"

            # Ajouter la ligne
            item_id = app.tree.insert(
                "",
                "end",
                text=checkbox,
                values=(
                    title,
                    artist_display,
                    album,
                    release_date,
                    credits_display,
                    lyrics_display,
                    bpm,
                    duration_display,
                    certif_display,
                    streams_display,
                    status,
                ),
                tags=(str(i),),
            )

            # Appliquer le style pour les morceaux désactivés
            if is_disabled:
                app.tree.item(item_id, tags=(str(i), "disabled"))

        except Exception as e:
            logger.error(f"Erreur ajout track idx={i}: {e}")
            # En cas d'erreur, ajouter une ligne minimale
            try:
                app.tree.insert(
                    "",
                    "end",
                    text="☐",
                    values=(
                        track.title,
                        "",
                        "",
                        "",
                        "0",
                        "",
                        "",
                        "Aucun",  # CORRECTION: "0" pour les crédits
                    ),
                    tags=(str(i),),
                )
            except Exception:
                pass

    # Style pour morceaux désactivés
    app.tree.tag_configure("disabled", foreground="gray", background="#2a2a2a")

    # Rafraîchir l'affichage des sélections
    refresh_selection_display(app)
    update_selection_count(app)
    app._update_buttons_state()


def on_tree_click(app, event):
    """Gère les clics sur le tableau avec sélection multiple (Ctrl/Maj)"""
    if getattr(app, "view_mode", "tracks") != "tracks":
        return
    region = app.tree.identify_region(event.x, event.y)

    if region == "tree":  # Clic sur la case à cocher
        item = app.tree.identify_row(event.y)
        if item:
            tags = app.tree.item(item)["tags"]
            if tags:
                index = int(tags[0])

                # Vérifier si le morceau est désactivé
                if app._is_track_disabled_by_index(index):
                    return  # Ignorer le clic sur les morceaux désactivés

                # Gestion de la sélection multiple
                ctrl_pressed = event.state & 0x4  # Ctrl key
                shift_pressed = event.state & 0x1  # Shift key

                if shift_pressed and app.last_selected_index is not None:
                    # Sélection en plage avec Maj
                    start = min(app.last_selected_index, index)
                    end = max(app.last_selected_index, index)

                    # Sélectionner tous les morceaux dans la plage (sauf désactivés)
                    for i in range(start, end + 1):
                        if not app._is_track_disabled_by_index(i):
                            app.selected_tracks.add(i)
                            # Trouver l'item correspondant et cocher
                            for child in app.tree.get_children():
                                child_tags = app.tree.item(child)["tags"]
                                if child_tags and int(child_tags[0]) == i:
                                    app.tree.item(child, text="☑")
                                    break

                elif ctrl_pressed:
                    # Sélection multiple avec Ctrl (toggle)
                    if index in app.selected_tracks:
                        app.selected_tracks.remove(index)
                        app.tree.item(item, text="☐")
                    else:
                        app.selected_tracks.add(index)
                        app.tree.item(item, text="☑")
                    app.last_selected_index = index

                else:
                    # Clic simple - toggle
                    if index in app.selected_tracks:
                        app.selected_tracks.remove(index)
                        app.tree.item(item, text="☐")
                        new_state = False
                    else:
                        app.selected_tracks.add(index)
                        app.tree.item(item, text="☑")
                        new_state = True
                    app.last_selected_index = index

                    # Armer le cocher-glisser : maintenir le clic et glisser
                    # applique le même état aux lignes survolées
                    app._drag_check_state = new_state
                    app._drag_check_active = True

                update_selection_count(app)


def on_tree_drag(app, event):
    """Cocher-glisser : applique l'état de la première coche aux lignes survolées"""
    if getattr(app, "view_mode", "tracks") != "tracks":
        return
    if not getattr(app, "_drag_check_active", False):
        return
    if app.tree.identify_region(event.x, event.y) != "tree":
        return
    item = app.tree.identify_row(event.y)
    if not item:
        return
    tags = app.tree.item(item)["tags"]
    if not tags:
        return
    index = int(tags[0])
    if app._is_track_disabled_by_index(index):
        return

    if app._drag_check_state and index not in app.selected_tracks:
        app.selected_tracks.add(index)
        app.tree.item(item, text="☑")
        update_selection_count(app)
    elif not app._drag_check_state and index in app.selected_tracks:
        app.selected_tracks.remove(index)
        app.tree.item(item, text="☐")
        update_selection_count(app)


def on_tree_release(app, event):
    """Fin du cocher-glisser"""
    app._drag_check_active = False


def delete_track_by_index(app, index):
    """Supprime définitivement un morceau (DB + liste) après confirmation"""
    if not app.current_artist or index >= len(app.current_artist.tracks):
        return
    track = app.current_artist.tracks[index]
    if not messagebox.askyesno(
        "Supprimer le morceau",
        f"Supprimer définitivement '{track.title}' ?\n\n"
        "Le morceau, ses crédits et ses données seront effacés de la base.\n"
        "(Il pourra revenir lors d'une future récupération de discographie "
        "s'il est encore associé à l'artiste sur Genius.)",
    ):
        return
    try:
        if track.id:
            app.data_manager.delete_track(track.id)
        # Mémoriser la suppression (genius_id) pour éviter le réajout au prochain import
        try:
            app.deleted_tracks_manager.add_deleted(
                app.current_artist.name, track.genius_id, track.title
            )
        except Exception as e:
            logger.debug(f"Mémo suppression échec: {e}")
        app.current_artist.tracks.pop(index)
        # Les indices ne sont plus valides
        app.selected_tracks.clear()
        populate_tracks_table(app)
        app._update_artist_info()
        logger.info(f"🗑️ Morceau supprimé: {track.title}")
    except Exception as e:
        logger.error(f"Erreur suppression morceau: {e}")
        report.show_error(app, "Erreur", f"Impossible de supprimer le morceau: {e}")


def on_right_click(app, event):
    """Menu contextuel sur clic droit avec actualisation immédiate"""
    if getattr(app, "view_mode", "tracks") == "albums":
        albums_view.on_album_right_click(app, event)
        return
    if getattr(app, "view_mode", "tracks") != "tracks":
        return
    item = app.tree.identify_row(event.y)
    if item:
        tags = app.tree.item(item)["tags"]
        if tags:
            index = int(tags[0])

            # Créer menu contextuel
            context_menu = tkinter.Menu(app.root, tearoff=0)

            # Vérifier l'état actuel du morceau
            is_disabled = app._is_track_disabled_by_index(index)

            if is_disabled:
                context_menu.add_command(
                    label="Réactiver ce morceau",
                    command=lambda: enable_track_with_refresh(app, index, item),
                )
            else:
                context_menu.add_command(
                    label="Désactiver ce morceau",
                    command=lambda: disable_track_with_refresh(app, index, item),
                )

            context_menu.add_separator()
            context_menu.add_command(
                label="Voir les détails", command=lambda: show_track_details_by_index(app, index)
            )
            context_menu.add_command(
                label="✏️ Saisir BPM / Tonalité / Durée…",
                command=lambda: manual_entry.manual_audio_entry(app, index),
            )
            context_menu.add_command(
                label="🔗 Définir / valider le lien YouTube…",
                command=lambda: manual_entry.manual_youtube_link(app, index),
            )
            context_menu.add_command(
                label="🎚️ Analyser un fichier audio local (BPM/Key)…",
                command=lambda: manual_entry.bpmfinder_local_file(app, index),
            )
            context_menu.add_command(
                label="🏷️ Renommer le morceau…",
                command=lambda: manual_entry.rename_track(app, index),
            )
            if len(app.selected_tracks) == 2:
                context_menu.add_separator()
                context_menu.add_command(
                    label="🔀 Fusionner les 2 morceaux cochés…",
                    command=lambda: merge_tracks.merge_selected_tracks(app),
                )
            context_menu.add_separator()
            context_menu.add_command(
                label="🗑️ Supprimer définitivement",
                command=lambda: delete_track_by_index(app, index),
            )

            # Afficher le menu
            try:
                context_menu.tk_popup(event.x_root, event.y_root)
            finally:
                context_menu.grab_release()


def disable_track_with_refresh(app, index: int, item):
    """Désactive un morceau et actualise immédiatement l'affichage"""
    # Convertir l'index en track ID et ajouter
    track_id = app._get_track_id_from_index(index)
    if track_id is not None:
        app.disabled_tracks.add(track_id)
    if index in app.selected_tracks:
        app.selected_tracks.remove(index)

    # Récupérer les valeurs actuelles de l'item
    current_values = list(app.tree.item(item)["values"])

    # Mettre à jour le statut (dernière colonne)
    if len(current_values) >= 8:
        current_values[7] = "Désactivé"

    # Actualiser immédiatement l'affichage de cet item
    app.tree.item(item, text="⊘", values=current_values, tags=(str(index), "disabled"))
    app.tree.tag_configure("disabled", foreground="gray", background="#2a2a2a")

    # Sauvegarder
    if app.current_artist:
        app.disabled_tracks_manager.save_disabled_tracks(
            app.current_artist.name, app.disabled_tracks
        )

    update_selection_count(app)
    logger.info(f"Morceau désactivé: index {index}")


def enable_track_with_refresh(app, index: int, item):
    """Réactive un morceau et actualise immédiatement l'affichage"""
    # Convertir l'index en track ID et retirer
    track_id = app._get_track_id_from_index(index)
    if track_id is not None and track_id in app.disabled_tracks:
        app.disabled_tracks.remove(track_id)

    # Récupérer les valeurs actuelles de l'item
    current_values = list(app.tree.item(item)["values"])

    # Mettre à jour le statut (dernière colonne)
    if len(current_values) >= 8:
        current_values[7] = "Actif"

    # Actualiser immédiatement l'affichage de cet item
    app.tree.item(item, text="☐", values=current_values, tags=(str(index),))

    # Sauvegarder
    if app.current_artist:
        app.disabled_tracks_manager.save_disabled_tracks(
            app.current_artist.name, app.disabled_tracks
        )

    update_selection_count(app)
    logger.info(f"Morceau réactivé: index {index}")


def disable_selected_tracks(app):
    """Désactive les morceaux sélectionnés"""
    if not app.selected_tracks:
        messagebox.showwarning(
            "Aucune sélection", "Veuillez sélectionner des morceaux à désactiver"
        )
        return

    try:
        # Convertir les indices sélectionnés en IDs de tracks
        track_ids_to_disable = set()
        for index in app.selected_tracks:
            track_id = app._get_track_id_from_index(index)
            if track_id is not None:
                track_ids_to_disable.add(track_id)

        # Ajouter aux morceaux désactivés (utiliser IDs)
        app.disabled_tracks.update(track_ids_to_disable)

        # Sauvegarder
        if app.current_artist:
            app.disabled_tracks_manager.save_disabled_tracks(
                app.current_artist.name, app.disabled_tracks
            )

        # Vider la sélection
        app.selected_tracks.clear()

        # Rafraîchir l'affichage
        populate_tracks_table(app)

        logger.info(f"Morceaux désactivés: {len(app.disabled_tracks)} au total")

    except Exception as e:
        logger.error(f"Erreur lors de la désactivation: {e}")
        report.show_error(app, "Erreur", f"Impossible de désactiver les morceaux: {e}")


def enable_selected_tracks(app):
    """Réactive TOUS les morceaux désactivés"""
    if not app.disabled_tracks:
        messagebox.showinfo("Info", "Aucun morceau désactivé")
        return

    try:
        count = len(app.disabled_tracks)

        # Vider complètement les morceaux désactivés
        app.disabled_tracks.clear()

        # Sauvegarder l'état vide
        if app.current_artist:
            app.disabled_tracks_manager.save_disabled_tracks(
                app.current_artist.name, app.disabled_tracks
            )

        # Rafraîchir l'affichage
        populate_tracks_table(app)

        messagebox.showinfo("Succès", f"{count} morceau(x) réactivé(s)")
        logger.info(f"Tous les morceaux ont été réactivés ({count})")

    except Exception as e:
        logger.error(f"Erreur lors de la réactivation: {e}")
        report.show_error(app, "Erreur", f"Impossible de réactiver les morceaux: {e}")


def sort_column(app, col):
    """Trie les morceaux par colonne - VERSION SANS SÉLECTION AUTOMATIQUE"""
    if getattr(app, "view_mode", "tracks") != "tracks":
        return
    if not app.current_artist or not app.current_artist.tracks:
        return

    try:
        # Déterminer l'ordre de tri
        reverse = False
        if app.sort_column == col:
            reverse = not app.sort_reverse

        # Définir la fonction de tri
        sort_key = None
        if col == "Titre":
            # normalize_text : tri insensible aux accents (« Étoile » avec les E)
            sort_key = lambda t: helpers.normalize_text(t.title)
        elif col == "Album":
            sort_key = lambda t: helpers.normalize_text(t.album or "")
        elif col == "Artiste principal":
            sort_key = lambda t: helpers.normalize_text(
                (t.primary_artist_name or t.artist.name) if t.artist else ""
            )
        elif col == "Date sortie":
            # CORRECTION: Gérer datetime ET string
            def get_release_date(t):
                if not t.release_date:
                    return datetime.min
                if isinstance(t.release_date, str):
                    try:
                        return datetime.fromisoformat(
                            t.release_date.replace("Z", "+00:00").split("T")[0]
                        )
                    except Exception:
                        return datetime.min
                return t.release_date

            sort_key = get_release_date
        elif col == "Crédits":
            # CORRECTION: Trier par nombre de crédits
            sort_key = lambda t: len(t.credits)
        elif col == "Paroles":
            # rien < texte seul < texte + timestamps
            sort_key = lambda t: (
                bool(t.has_lyrics),
                bool(t.lyrics_synced),
            )
        elif col == "BPM":
            sort_key = lambda t: t.audio.bpm or 0
        elif col == "Durée":
            # Trier par durée en secondes
            def get_duration_seconds(t):
                if not t.duration:
                    return 0
                if isinstance(t.duration, int):
                    return t.duration
                if isinstance(t.duration, str):
                    try:
                        parts = t.duration.split(":")
                        if len(parts) == 2:
                            return int(parts[0]) * 60 + int(parts[1])
                        elif len(parts) == 3:
                            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                    except Exception:
                        pass
                return 0

            sort_key = get_duration_seconds
        elif col == "Certif.":
            # CORRECTION: Définir la fonction ET l'utiliser
            cert_order = {
                "💎💎💎💎": 1,
                "💎💎💎": 2,
                "💎💎": 3,
                "💎": 4,
                "💿💿💿": 5,
                "💿💿": 6,
                "💿": 7,
                "🥇🥇🥇": 8,
                "🥇🥇": 9,
                "🥇": 10,
                "✓": 11,
                "": 12,
            }

            def get_cert_value(t):
                try:
                    if t.certifications:
                        cert_level = t.certifications[0].get("certification", "")
                        emoji_map = {
                            "Quadruple Diamant": "💎💎💎💎",
                            "Triple Diamant": "💎💎💎",
                            "Double Diamant": "💎💎",
                            "Diamant": "💎",
                            "Triple Platine": "💿💿💿",
                            "Double Platine": "💿💿",
                            "Platine": "💿",
                            "Triple Or": "🥇🥇🥇",
                            "Double Or": "🥇🥇",
                            "Or": "🥇",
                        }
                        emoji = emoji_map.get(cert_level, "✓")
                        return cert_order.get(emoji, 12)
                    return 12
                except Exception:
                    return 12

            sort_key = get_cert_value
        elif col == "Streams":
            # Total estimé (même calcul que l'affichage) ; sans données → -1 (en bas)
            def get_streams_total(t):
                try:
                    from src.utils.streams_calculator import calculate_total_streams

                    total = calculate_total_streams(
                        t.streams.spotify_streams, t.streams.ytm_streams
                    )
                    return total if total is not None else -1
                except Exception:
                    return -1

            sort_key = get_streams_total
        elif col == "Statut":
            # CORRECTION: Trier par ordre de priorité (Complet > Incomplet > Désactivé)
            status_order = {
                "✅": 1,  # Complet en premier
                "⚠️": 2,  # Incomplet au milieu
                "❌": 3,  # Désactivé en dernier
            }

            def get_status_value(t):
                icon = helpers.get_track_status_icon(t, app.disabled_tracks)
                return status_order.get(icon, 4)  # 4 pour les icônes inconnues

            sort_key = get_status_value

        if sort_key:
            # Les morceaux désactivés sont maintenant stockés par ID, pas par index
            # donc ils restent valides même après le tri

            # Trier les morceaux
            app.current_artist.tracks.sort(key=sort_key, reverse=reverse)

            # Vider les sélections (les indices ne sont plus valides après le tri)
            app.selected_tracks.clear()

            # Les disabled_tracks utilisent maintenant des IDs de tracks
            # donc pas besoin de les restaurer - ils restent valides après le tri
            # Aucun besoin de sauvegarder car les IDs n'ont pas changé

        # Mettre à jour les variables de tri
        app.sort_column = col
        app.sort_reverse = reverse

        # Recréer l'affichage
        populate_tracks_table(app)

        # Mettre à jour l'indicateur de tri dans l'en-tête
        for column in app.tree["columns"]:
            if column == col:
                indicator = " ▲" if not reverse else " ▼"
                app.tree.heading(column, text=column + indicator)
            else:
                app.tree.heading(column, text=column)

    except Exception as e:
        logger.error(f"Erreur lors du tri: {e}")
        report.show_error(app, "Erreur de tri", str(e))


def show_track_details_by_index(app, index: int):
    """Affiche les détails d'un morceau par son index - ✅ NOUVEAU"""
    if 0 <= index < len(app.current_artist.tracks):
        track = app.current_artist.tracks[index]
        app._show_track_details_for_track(track)


def show_track_details(app, event):
    """Affiche les détails d'un morceau - VERSION CORRIGÉE AVEC DEBUG FEATURING"""
    if getattr(app, "view_mode", "tracks") != "tracks":
        return
    selection = app.tree.selection()
    if not selection:
        return

    item = selection[0]
    tags = app.tree.item(item)["tags"]
    if not tags:
        return

    track_index = int(tags[0])

    if 0 <= track_index < len(app.current_artist.tracks):
        track = app.current_artist.tracks[track_index]
        app._show_track_details_for_track(track)


def refresh_selection_display(app):
    """Met à jour l'affichage des sélections dans le tableau"""
    for item in app.tree.get_children():
        tags = app.tree.item(item)["tags"]
        if tags and len(tags) > 0:
            index = int(tags[0])

            if "disabled" in tags or app._is_track_disabled_by_index(index):
                app.tree.item(item, text="⊘")
            elif index in app.selected_tracks:
                app.tree.item(item, text="☑")
            else:
                app.tree.item(item, text="☐")


def select_all_tracks(app):
    """Sélectionne tous les morceaux actifs (non désactivés)"""
    if not app.current_artist or not app.current_artist.tracks:
        return

    app.selected_tracks.clear()

    for i in range(len(app.current_artist.tracks)):
        # Ne sélectionner que les morceaux actifs
        if not app._is_track_disabled_by_index(i):
            app.selected_tracks.add(i)

    refresh_selection_display(app)
    update_selection_count(app)


def deselect_all_tracks(app):
    """Désélectionne tous les morceaux"""
    app.selected_tracks.clear()
    refresh_selection_display(app)
    update_selection_count(app)


def check_selected_tracks(app):
    """Coche tous les morceaux actuellement en surbrillance (sélection visuelle)"""
    # Récupérer les items en surbrillance dans le Treeview
    highlighted_items = app.tree.selection()

    if not highlighted_items:
        logger.info("Aucun morceau en surbrillance")
        return

    logger.info(f"Cochage de {len(highlighted_items)} morceaux en surbrillance")
    for item in highlighted_items:
        tags = app.tree.item(item)["tags"]
        if tags:
            index = int(tags[0])

            # Vérifier que le morceau n'est pas désactivé
            if not app._is_track_disabled_by_index(index):
                # Ajouter à la sélection et cocher
                app.selected_tracks.add(index)
                app.tree.item(item, text="☑")
                logger.debug(f"Morceau {index} coché")

    update_selection_count(app)


def update_selection_count(app):
    """Met à jour l'affichage du nombre de morceaux sélectionnés"""
    if hasattr(app, "selected_count_label"):
        total = (
            len(app.current_artist.tracks)
            if app.current_artist and app.current_artist.tracks
            else 0
        )
        selected = len(app.selected_tracks)
        disabled = len(app.disabled_tracks)
        active = total - disabled

        text = f"Sélectionnés: {selected}/{active} actifs"
        if disabled > 0:
            text += f" ({disabled} désactivés)"

        app.selected_count_label.configure(text=text)


def apply_default_sort(app):
    """Tri par défaut : date de sortie, plus récent en haut."""
    if not app.current_artist or not app.current_artist.tracks:
        return
    # Astuce : _sort_column inverse l'ordre quand on re-trie la même colonne.
    # En pré-positionnant sort_reverse=False, l'appel produit un tri descendant.
    app.sort_column = "Date sortie"
    app.sort_reverse = False
    sort_column(app, "Date sortie")
