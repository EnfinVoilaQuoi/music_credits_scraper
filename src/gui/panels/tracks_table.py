"""Vue « morceaux » : table, tri, sélection, menus contextuels, activation/désactivation.
Le Treeview lui-même appartient à MainWindow (widget partagé avec la vue albums)."""

import tkinter
from datetime import datetime
from tkinter import messagebox, simpledialog

from src.gui import helpers
from src.gui.dialogs import manual_entry, merge_tracks, report
from src.gui.panels import albums_view
from src.models import ReleaseObservation
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _index_colonne(app, nom: str) -> int | None:
    """Rang d'une colonne dans `TRACK_COLUMNS`, ou None.

    Les valeurs d'un item sont un TUPLE positionnel : écrire au rang 7 était
    juste quand la table avait 8 colonnes, et écrivait « Actif » dans la DURÉE
    depuis qu'elle en a 11 (vu à l'écran le 2026-09-22). Un rang en dur est une
    bombe à retardement dès qu'une colonne s'ajoute.
    """
    try:
        return list(app.TRACK_COLUMNS).index(nom)
    except ValueError:
        return None


def _cocher(app, index: int, item=None) -> bool:
    """Coche la ligne d'index donné. Rend False si elle n'est pas cochable
    (morceau désactivé, ou pas encore enregistré donc sans identifiant)."""
    tid = app._get_track_id_from_index(index)
    if tid is None or tid in app.disabled_tracks:
        return False
    app.selected_tracks.add(tid)
    if item is not None:
        app.tree.item(item, text="☑")
    return True


def _decocher(app, index: int, item=None) -> None:
    tid = app._get_track_id_from_index(index)
    if tid is not None:
        app.selected_tracks.discard(tid)
    if item is not None:
        app.tree.item(item, text="☐")


def _est_cochee(app, index: int) -> bool:
    tid = app._get_track_id_from_index(index)
    return tid is not None and tid in app.selected_tracks


def _case_a_cocher(app, track) -> str:
    """☑ cochée · ☐ cochable · ◌ pas encore enregistrée.

    La sélection porte des IDENTIFIANTS depuis le 2026-09-22 (elle portait des
    index, qui désignaient d'autres morceaux après un tri). Un morceau sans id
    — créé en mémoire, dont le `save_track` a échoué — devient donc incochable,
    et ça SE VOIT : avant, il se cochait et disparaissait en silence du filtre
    des workers, qui écartent les `id is None`.
    """
    if getattr(track, "id", None) is None:
        return "◌"
    return "☑" if track.id in app.selected_tracks else "☐"


def _poser_statut(app, index: int, valeurs: list) -> None:
    """Réécrit la cellule « Statut » d'une ligne — à son RANG, pas au 7ᵉ."""
    rang = _index_colonne(app, "Statut")
    tracks = app.current_artist.tracks if app.current_artist else []
    if rang is None or len(valeurs) <= rang or not 0 <= index < len(tracks):
        return
    valeurs[rang] = helpers.get_track_status_icon(tracks[index], app.disabled_tracks)


def _tuple_de_valeurs(app, valeurs: dict[str, str]) -> tuple:
    """Les valeurs d'une ligne, posées PAR NOM de colonne.

    Un tuple construit à la main fige l'ordre de `TRACK_COLUMNS` à l'endroit
    où il est écrit : la ligne de repli en portait 8 pour 11 colonnes, si bien
    que « Aucun » atterrissait dans la DURÉE et que la cellule Statut restait
    vide (vu à l'écran le 2026-09-22, même famille que le rang 7 en dur). Une
    colonne absente rend `""` ; un nom inconnu LÈVE, plutôt que de décaler tout
    silencieusement.
    """
    inconnues = set(valeurs) - set(app.TRACK_COLUMNS)
    if inconnues:
        raise KeyError(f"Colonnes inconnues : {sorted(inconnues)}")
    return tuple(valeurs.get(nom, "") for nom in app.TRACK_COLUMNS)


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
        "Statut": (70, "center"),
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

    # Les cases cochées portent des INDEX de ligne, pas des identifiants : les
    # laisser survivre à un changement d'artiste ferait pointer les mêmes
    # numéros sur d'autres morceaux — silencieusement, et jusque dans le filtre
    # « limiter aux morceaux cochés » du run de streams.
    artiste_id = app.current_artist.id if app.current_artist else None
    if getattr(app, "_selection_artiste_id", None) != artiste_id:
        app.selected_tracks.clear()
        app._selection_artiste_id = artiste_id

    if not app.current_artist or not app.current_artist.tracks:
        return

    # Charger les morceaux désactivés depuis la mémoire (IDs, pas indices)
    try:
        app.disabled_tracks = app.disabled_tracks_manager.load_disabled_tracks(
            app.current_artist.name
        )
    except (OSError, ValueError) as e:
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

            # Rôle secondaire (Additional Voices…) : marqueur distinct du feat.
            # Il est TU quand le morceau est signé par une formation dont
            # l'artiste est membre : le marqueur veut dire « il n'est ni
            # l'auteur ni l'invité, juste une petite contribution », ce qui
            # devient faux quand il fait partie du groupe — sa présence est déjà
            # expliquée par son appartenance. La donnée reste en base et dans la
            # fiche technique ; seul le marqueur du tableau se tait.
            _sec_role = None if track.membre_de_la_formation else track.secondary_role
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

            lyrics_display = helpers.format_lyrics_cell(track)

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

            # Certifications - Lire depuis track.certs.entries au lieu de l'API
            certif_display = ""
            try:
                # Vérifier si le track a des certifications stockées
                if track.certs.reelles or track.certs.echos:
                    # Plus haute certification RÉELLE (liste déjà triée) ; sans
                    # réelle, l'écho d'une version se voit, marqué ↩.
                    reelle = bool(track.certs.reelles)
                    cert_level = (track.certs.reelles or track.certs.echos)[0].get(
                        "certification", ""
                    )
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
                    if not reelle:
                        certif_display = "↩" + certif_display
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

            # Ajouter la ligne
            item_id = app.tree.insert(
                "",
                "end",
                text=_case_a_cocher(app, track),
                values=_tuple_de_valeurs(
                    app,
                    {
                        "Titre": title,
                        "Artiste principal": artist_display,
                        "Album": album,
                        "Date sortie": release_date,
                        "Crédits": credits_display,
                        "Paroles": lyrics_display,
                        "BPM": bpm,
                        "Durée": duration_display,
                        "Certif.": certif_display,
                        "Streams": streams_display,
                        "Statut": status,
                    },
                ),
                tags=(str(i),),
            )

            # Appliquer le style pour les morceaux désactivés
            if is_disabled:
                app.tree.item(item_id, tags=(str(i), "disabled"))

        except Exception as e:
            logger.error(f"Erreur ajout track idx={i}: {e}")
            # En cas d'erreur, ajouter une ligne minimale — alignée elle aussi.
            try:
                app.tree.insert(
                    "",
                    "end",
                    text="☐",
                    values=_tuple_de_valeurs(
                        app, {"Titre": track.title or f"Track {i+1}", "Statut": "⚠️"}
                    ),
                    tags=(str(i),),
                )
            except Exception:
                logger.exception("Ligne de repli impossible pour idx=%s", i)

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
                    # La PLAGE est visuelle : elle se dit en index de ligne, et
                    # chaque ligne est traduite en identifiant au moment d'être
                    # cochée. `refresh_selection_display` repeint d'un coup,
                    # plutôt qu'une recherche O(n) par ligne de la plage.
                    start = min(app.last_selected_index, index)
                    end = max(app.last_selected_index, index)
                    for i in range(start, end + 1):
                        _cocher(app, i)
                    refresh_selection_display(app)

                elif ctrl_pressed:
                    # Sélection multiple avec Ctrl (toggle)
                    if _est_cochee(app, index):
                        _decocher(app, index, item)
                    else:
                        _cocher(app, index, item)
                    app.last_selected_index = index

                else:
                    # Clic simple - toggle
                    if _est_cochee(app, index):
                        _decocher(app, index, item)
                        new_state = False
                    else:
                        new_state = _cocher(app, index, item)
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

    if app._drag_check_state and not _est_cochee(app, index):
        if _cocher(app, index, item):
            update_selection_count(app)
    elif not app._drag_check_state and _est_cochee(app, index):
        _decocher(app, index, item)
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
        except (OSError, ValueError) as e:
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
            context_menu.add_command(
                label="💿 Rattacher à une parution…",
                command=lambda: attach_track_to_release(app, index),
            )
            context_menu.add_command(
                label="💿 Retirer d'une parution…",
                command=lambda: detach_track_from_release(app, index),
            )
            context_menu.add_command(
                label="★ Définir comme album repère…",
                command=lambda: set_track_reference_release(app, index),
            )
            context_menu.add_command(
                label="✓ Confirmer une parution proposée…",
                command=lambda: confirm_release_suggestion(app, index),
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


def attach_track_to_release(app, index: int) -> None:
    """Rattachement manuel explicite, sans jamais toucher `track.album`."""
    if not app.current_artist or index >= len(app.current_artist.tracks):
        return
    track = app.current_artist.tracks[index]
    title = simpledialog.askstring(
        "Rattacher à une parution",
        "Titre de l'album ou de la compilation :",
        parent=app.root,
    )
    if not title or not title.strip():
        return
    third_party = messagebox.askyesno(
        "Nature de la parution",
        "Est-ce un disque d'un autre artiste (compilation / apparition) ?\n\n"
        "Oui : visible sous « Aussi présent sur ».\nNon : discographie principale.",
        parent=app.root,
    )
    credited = None
    if third_party:
        credited = simpledialog.askstring(
            "Artiste crédité",
            "Artiste ou organisme crédité du disque (facultatif) :",
            parent=app.root,
        )
    scope = "appearance" if third_party else "own"
    if not messagebox.askyesno(
        "Confirmer le rattachement",
        f"Rattacher « {track.title} » à « {title.strip()} »"
        + (f" — {credited}" if credited else "")
        + " ?\n\nLa fiche morceau et son album de référence ne seront pas modifiés.",
        parent=app.root,
    ):
        return
    try:
        app.data_manager.record_release_observations(
            track.id,
            [
                ReleaseObservation(
                    title=title.strip(),
                    source="manual",
                    credited_artist_name=(
                        credited.strip() if credited and credited.strip() else None
                    ),
                    scope=scope,
                    confidence="confirmed",
                )
            ],
        )
        ok = True
    except (ValueError, TypeError) as e:
        messagebox.showerror("Parution", f"Rattachement impossible : {e}", parent=app.root)
        return
    if ok:
        messagebox.showinfo("Parution", "Rattachement enregistré.", parent=app.root)
    else:
        messagebox.showerror(
            "Parution", "Rattachement impossible — voir les logs.", parent=app.root
        )


def set_track_reference_release(app, index: int) -> None:
    """Choix explicite du pointeur de compatibilité ``tracks.album``."""
    from tkinter import simpledialog

    track = app.current_artist.tracks[index]
    if not track.id:
        return
    entries = app.data_manager.get_track_releases(track.id)
    confirmed = [entry for entry in entries if entry.get("status", "confirmed") == "confirmed"]
    if not confirmed:
        messagebox.showinfo(
            "Album repère", "Aucune parution confirmée pour ce morceau.", parent=app.root
        )
        return
    choices = "\n".join(f"{entry['id']} — {entry['title']}" for entry in confirmed)
    release_id = simpledialog.askinteger(
        "Définir comme album repère",
        f"Choisir la parution qui devient l'album repère :\n{choices}",
        parent=app.root,
    )
    if release_id is None:
        return
    if release_id not in {entry["id"] for entry in confirmed}:
        messagebox.showwarning("Album repère", "Identifiant de parution invalide.", parent=app.root)
        return
    if app.data_manager.set_track_reference_release(track.id, release_id):
        track.album = next(entry["title"] for entry in confirmed if entry["id"] == release_id)
        app._reload_tracks_and_refresh()
    else:
        messagebox.showerror(
            "Album repère", "Changement impossible — voir les logs.", parent=app.root
        )


def confirm_release_suggestion(app, index: int) -> None:
    track = app.current_artist.tracks[index]
    if not track.id:
        return
    entries = [
        entry
        for entry in app.data_manager.get_track_releases(track.id)
        if entry.get("status") == "suggested"
    ]
    if not entries:
        messagebox.showinfo("Parution", "Aucune proposition à confirmer.", parent=app.root)
        return
    choices = "\n".join(f"{entry['id']} — {entry['title']}" for entry in entries)
    release_id = simpledialog.askinteger(
        "Confirmer une parution",
        f"Confirmer la proposition :\n{choices}",
        parent=app.root,
    )
    if release_id is None:
        return
    if app.data_manager.confirm_release_suggestion(track.id, release_id):
        messagebox.showinfo("Parution", "Parution confirmée.", parent=app.root)
        app._reload_tracks_and_refresh()
    else:
        messagebox.showwarning(
            "Parution", "Proposition introuvable ou déjà traitée.", parent=app.root
        )


def detach_track_from_release(app, index: int) -> None:
    """Retrait manuel avec choix explicite si l'album de référence est visé."""
    if not app.current_artist or index >= len(app.current_artist.tracks):
        return
    track = app.current_artist.tracks[index]
    entries = app.data_manager.get_track_releases(track.id)
    if not entries:
        messagebox.showinfo(
            "Parution", "Ce morceau n'est rattaché à aucune parution.", parent=app.root
        )
        return
    choices = "\n".join(f"{r['id']} — {r['title']}" for r in entries)
    release_id = simpledialog.askinteger(
        "Retirer d'une parution",
        f"Parutions de « {track.title} » :\n{choices}\n\nSaisis l'identifiant à retirer :",
        parent=app.root,
    )
    if release_id is None or not any(r["id"] == release_id for r in entries):
        return
    if not messagebox.askyesno(
        "Confirmer le retrait",
        "Retirer ce rattachement ? La fiche et ses données ne seront pas supprimées.",
        parent=app.root,
    ):
        return
    outcome = app.data_manager.unlink_track_from_release(release_id, track.id)
    if outcome == "needs_replacement":
        alternatives = [r for r in entries if r["id"] != release_id and r["scope"] == "own"]
        if alternatives:
            options = "\n".join(f"{r['id']} — {r['title']}" for r in alternatives)
            replacement = simpledialog.askinteger(
                "Album de référence",
                "Cette parution est l'album de référence. Choisis son remplacement :\n" + options,
                parent=app.root,
            )
            if replacement is not None:
                outcome = app.data_manager.unlink_track_from_release(
                    release_id, track.id, replacement_release_id=replacement
                )
        elif messagebox.askyesno(
            "Détacher le morceau",
            "Aucune autre parution principale ne peut devenir la référence. "
            "Détacher complètement l'album de référence ?",
            parent=app.root,
        ):
            outcome = app.data_manager.unlink_track_from_release(
                release_id, track.id, clear_reference=True
            )
    if outcome == "removed":
        app._reload_tracks_and_refresh()
        messagebox.showinfo("Parution", "Rattachement retiré.", parent=app.root)
    elif outcome == "needs_replacement":
        messagebox.showinfo("Parution", "Aucun retrait effectué.", parent=app.root)
    else:
        messagebox.showerror("Parution", "Retrait impossible — voir les logs.", parent=app.root)


def disable_track_with_refresh(app, index: int, item):
    """Désactive un morceau et actualise immédiatement l'affichage"""
    # Convertir l'index en track ID et ajouter
    track_id = app._get_track_id_from_index(index)
    if track_id is not None:
        app.disabled_tracks.add(track_id)
        app.selected_tracks.discard(track_id)

    # Récupérer les valeurs actuelles de l'item
    current_values = list(app.tree.item(item)["values"])

    # Mettre à jour le statut (dernière colonne)
    _poser_statut(app, index, current_values)

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
    _poser_statut(app, index, current_values)

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
        # La sélection EST déjà une liste d'identifiants (2026-09-22).
        track_ids_to_disable = set(app.selected_tracks)

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


def enable_all_tracks(app):
    """Réactive TOUS les morceaux désactivés — sur confirmation.

    Le geste est IRRÉVERSIBLE : la liste des désactivés d'un artiste est un
    travail éditorial (Grünt, freestyles, lives…) qui ne se reconstitue pas
    (mesuré le 2026-09-22 : les désactivés de Josman effacés d'un clic, sans
    un mot). Un bouton qui détruit une liste demande confirmation, et la
    confirmation NOMME ce qu'elle détruit.
    """
    if not app.disabled_tracks:
        messagebox.showinfo("Info", "Aucun morceau désactivé")
        return

    count = len(app.disabled_tracks)
    artiste = app.current_artist.name if app.current_artist else "cet artiste"
    if not messagebox.askyesno(
        "Réactiver tous",
        f"Réactiver les {count} morceau(x) désactivé(s) de {artiste} ?\n\n"
        "Cette liste ne se reconstitue pas : il faudra les redésactiver un à un.",
    ):
        return

    try:
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
            # rien < instrumental 🎹 < texte seul < texte + timestamps
            sort_key = lambda t: (
                2 if t.lyrics.present else (1 if t.lyrics.instrumental else 0),
                bool(t.lyrics.synced),
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
                    if t.certs.reelles:
                        cert_level = t.certs.reelles[0].get("certification", "")
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
            # Rien à traduire : `selected_tracks` et `disabled_tracks` portent
            # des IDENTIFIANTS, les coches survivent au tri par construction.
            app.current_artist.tracks.sort(key=sort_key, reverse=reverse)

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
            elif 0 <= index < len(app.current_artist.tracks if app.current_artist else []):
                app.tree.item(item, text=_case_a_cocher(app, app.current_artist.tracks[index]))


def select_all_tracks(app):
    """Sélectionne tous les morceaux actifs (non désactivés)"""
    if not app.current_artist or not app.current_artist.tracks:
        return

    app.selected_tracks.clear()
    for i in range(len(app.current_artist.tracks)):
        _cocher(app, i)  # écarte les désactivés ET les morceaux sans identifiant

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

            if _cocher(app, index, item):
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
        # Un morceau sans identifiant n'est pas cochable : le dire plutôt que
        # de laisser croire qu'il partira dans un run.
        sans_id = sum(
            1
            for t in (app.current_artist.tracks if app.current_artist else [])
            if getattr(t, "id", None) is None
        )
        if sans_id:
            text += f" · {sans_id} non enregistré(s)"

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
