"""Fenêtre de détails d'un morceau (extraite de MainWindow)"""

from tkinter import messagebox

# alias : un « from tkinter import ttk » local dans __init__ rend « ttk » local
# à toute la fonction → le nom module-level doit être différent
from tkinter import ttk as tkinter_ttk

import customtkinter as ctk

from src.config import YOUTUBE_PERSIST_CONFIDENCE
from src.gui import helpers
from src.models import Track
from src.utils.logger import get_logger
from src.utils.youtube_integration import youtube_integration

logger = get_logger(__name__)


class TrackDetailsWindow:
    """Fenêtre de détails d'un morceau. S'enregistre dans app.open_detail_windows
    (même sémantique que l'ancienne méthode MainWindow._show_track_details_for_track)."""

    def __init__(self, app, track: Track):
        """Construit et affiche la fenêtre de détails - VERSION AVEC DEBUG FEATURING"""
        self.app = app
        # S'assurer que track.artist existe
        if not track.artist:
            track.artist = self.app.current_artist
            logger.warning(f"⚠️ track.artist était None pour '{track.title}', réparé")

        # Si une fenêtre de détails est déjà ouverte pour ce track, la fermer d'abord
        if track.id in self.app.open_detail_windows:
            old_window, _ = self.app.open_detail_windows[track.id]
            try:
                old_window.destroy()
            except Exception:
                pass
            del self.app.open_detail_windows[track.id]

        # Créer une fenêtre de détails
        details_window = ctk.CTkToplevel(self.app.root)
        details_window.title(f"Détails - {track.title}")
        details_window.transient(self.app.root)

        # Stocker la référence de la fenêtre
        self.app.open_detail_windows[track.id] = (details_window, track)

        # Nettoyer la référence quand la fenêtre est fermée
        def on_close():
            if track.id in self.app.open_detail_windows:
                del self.app.open_detail_windows[track.id]
            details_window.destroy()

        details_window.protocol("WM_DELETE_WINDOW", on_close)

        # Agrandir la fenêtre selon le contenu
        has_lyrics = track.lyrics.text
        window_height = "900" if has_lyrics else "750"
        details_window.geometry(f"900x{window_height}")

        # === SECTION INFORMATIONS GÉNÉRALES (EN HAUT) ===
        info_frame = ctk.CTkFrame(details_window)
        info_frame.pack(fill="x", padx=10, pady=10)

        # Titre principal
        ctk.CTkLabel(info_frame, text=f"🎵 {track.title}", font=("Arial", 16, "bold")).pack(
            anchor="w", padx=10, pady=(10, 5)
        )

        # Informations de base sur deux colonnes
        basic_info_frame = ctk.CTkFrame(info_frame)
        basic_info_frame.pack(fill="x", padx=10, pady=5)

        # Colonne gauche
        left_column = ctk.CTkFrame(basic_info_frame, fg_color="transparent")
        left_column.pack(side="left", fill="both", expand=True, padx=(5, 10))

        # Gestion des features
        is_featuring = track.is_featuring
        primary_artist = track.primary_artist_name
        featured_artists = track.featured_artists
        secondary_role = track.secondary_role

        if secondary_role:
            ctk.CTkLabel(
                left_column,
                text=f"🎙️ RÔLE SECONDAIRE — {secondary_role}",
                font=("Arial", 12, "bold"),
                text_color="#C58AF0",
            ).pack(anchor="w", pady=2)
            if primary_artist:
                ctk.CTkLabel(left_column, text=f"Artiste principal: {primary_artist}").pack(
                    anchor="w", pady=1
                )
            who = track.artist.name if track.artist else self.app.current_artist.name
            ctk.CTkLabel(left_column, text=f"Contribution de: {who}").pack(anchor="w", pady=1)
        elif is_featuring:
            ctk.CTkLabel(
                left_column,
                text="🎤 MORCEAU EN FEATURING",
                font=("Arial", 12, "bold"),
                text_color="orange",
            ).pack(anchor="w", pady=2)
            if primary_artist:
                ctk.CTkLabel(left_column, text=f"Artiste principal: {primary_artist}").pack(
                    anchor="w", pady=1
                )
            featuring_name = featured_artists or (
                track.artist.name if track.artist else self.app.current_artist.name
            )
            ctk.CTkLabel(left_column, text=f"En featuring: {featuring_name}").pack(
                anchor="w", pady=1
            )
        else:
            artist_name = track.artist.name if track.artist else "Artiste inconnu"
            ctk.CTkLabel(
                left_column,
                text="🎵 MORCEAU PRINCIPAL",
                font=("Arial", 12, "bold"),
                text_color="green",
            ).pack(anchor="w", pady=2)
            ctk.CTkLabel(left_column, text=f"Artiste: {artist_name}").pack(anchor="w", pady=1)

        # Album et numéro de piste
        if track.album:
            album_text = f"Album: {track.album}"
            if track.track_number:
                album_text += f" (Piste {track.track_number})"
            ctk.CTkLabel(left_column, text=album_text).pack(anchor="w", pady=1)

        # Colonne droite
        right_column = ctk.CTkFrame(basic_info_frame, fg_color="transparent")
        right_column.pack(side="right", fill="both", expand=True, padx=(10, 5))

        # Date, BPM, durée
        if track.release_date:
            date_str = helpers.format_date(track.release_date)
            ctk.CTkLabel(right_column, text=f"📅 Date: {date_str}").pack(anchor="w", pady=1)

        if track.audio.bpm:
            bpm_text = f"🎼 BPM: {track.audio.bpm}"

            # ⭐ LOGIQUE AMÉLIORÉE pour afficher la tonalité
            musical_key = None

            # 1. Ajouter la tonalité si disponible directement
            if track.audio.musical_key:
                musical_key = track.audio.musical_key

            # 2. FALLBACK : Si musical_key n'existe pas mais key et mode existent
            elif track.audio.key and track.audio.mode:
                try:
                    from src.utils.music_theory import key_mode_to_french_from_string

                    musical_key = key_mode_to_french_from_string(track.audio.key, track.audio.mode)

                    # ⭐ BONUS : Stocker le résultat calculé pour éviter de recalculer
                    track.audio.musical_key = musical_key
                    logger.debug(
                        f"Musical key calculée et stockée pour '{track.title}': {musical_key}"
                    )
                except Exception as e:
                    logger.warning(f"Erreur conversion key/mode: {e}")

            # Ajouter la tonalité au texte BPM si disponible
            if musical_key:
                bpm_text += f" ({musical_key})"

            ctk.CTkLabel(right_column, text=bpm_text).pack(anchor="w", pady=1)

        if track.duration:
            try:
                # Gérer différents formats de duration
                if isinstance(track.duration, str):
                    # Format "MM:SS"
                    if ":" in track.duration:
                        parts = track.duration.split(":")
                        minutes = int(parts[0])
                        seconds = int(parts[1])
                    else:
                        # String représentant des secondes
                        total_seconds = int(track.duration)
                        minutes = total_seconds // 60
                        seconds = total_seconds % 60
                elif isinstance(track.duration, (int, float)):
                    # Format numérique (secondes)
                    total_seconds = int(track.duration)
                    minutes = total_seconds // 60
                    seconds = total_seconds % 60
                else:
                    # Type inattendu, skip
                    minutes = 0
                    seconds = 0

                if minutes > 0 or seconds > 0:
                    ctk.CTkLabel(right_column, text=f"⏱️ Durée: {minutes}:{seconds:02d}").pack(
                        anchor="w", pady=1
                    )
            except (ValueError, TypeError, AttributeError) as e:
                logger.warning(f"Erreur affichage durée pour '{track.title}': {e}")
                # Ne pas crasher, juste skip l'affichage de la durée

        if track.genre:
            ctk.CTkLabel(right_column, text=f"🎭 Genre: {track.genre}").pack(anchor="w", pady=1)

        # Streams estimés dans le header
        try:
            from src.utils.streams_calculator import (
                calculate_total_streams,
                format_streams,
                streams_source_label,
            )

            sp = track.streams.spotify_streams
            yt = track.streams.ytm_streams
            total_est = calculate_total_streams(sp, yt)
            if total_est:
                suffix = streams_source_label(sp, yt)
                label_text = f"📊 Streams : {format_streams(total_est)}{suffix} (estimé)"
                ctk.CTkLabel(
                    right_column,
                    text=label_text,
                    font=ctk.CTkFont(size=12, weight="bold"),
                    text_color="#4fc3f7",
                ).pack(anchor="w", pady=(4, 1))
        except Exception:
            pass

        # URL Genius (cliquable)
        if track.genius_url:
            urls_frame = ctk.CTkFrame(info_frame, fg_color="transparent")
            urls_frame.pack(anchor="w", padx=10, pady=5)

            # URL Genius - JAUNE avec bouton "Voir"
            genius_frame = ctk.CTkFrame(urls_frame, fg_color="transparent")
            genius_frame.pack(side="left", padx=(0, 20))

            ctk.CTkLabel(genius_frame, text="📝 Genius: ").pack(side="left")

            genius_label = ctk.CTkLabel(
                genius_frame,
                text="🔍 Voir",
                text_color="#FFD700",  # Jaune or (comme le logo Genius)
                cursor="hand2",
            )
            genius_label.pack(side="left")

            import webbrowser

            genius_label.bind("<Button-1>", lambda e: webbrowser.open(track.genius_url))

        # URL Spotify - GESTION DE MULTIPLES IDs (méthode du modèle : [] si aucun)
        all_spotify_ids = track.get_all_spotify_ids()

        if all_spotify_ids:
            spotify_frame = ctk.CTkFrame(urls_frame, fg_color="transparent")
            spotify_frame.pack(side="left", padx=(0, 20))

            # Label principal
            ctk.CTkLabel(spotify_frame, text="🎵 Spotify: ").pack(side="left")

            # Si plusieurs IDs, afficher un dropdown ou une liste
            if len(all_spotify_ids) > 1:
                import tkinter as tk
                from tkinter import ttk

                # Créer un Combobox pour sélectionner la version
                version_var = tk.StringVar(value=all_spotify_ids[0])

                version_combo = ttk.Combobox(
                    spotify_frame,
                    textvariable=version_var,
                    values=[f"Version {i+1}" for i in range(len(all_spotify_ids))],
                    state="readonly",
                    width=12,
                )
                version_combo.pack(side="left", padx=5)

                def open_selected_version():
                    idx = version_combo.current()
                    if 0 <= idx < len(all_spotify_ids):
                        spotify_url = (
                            f"https://open.spotify.com/intl-fr/track/{all_spotify_ids[idx]}"
                        )
                        import webbrowser

                        webbrowser.open(spotify_url)

                spotify_label = ctk.CTkLabel(
                    spotify_frame, text="▶️ Écouter", text_color="#1DB954", cursor="hand2"
                )
                spotify_label.pack(side="left")
                spotify_label.bind("<Button-1>", lambda e: open_selected_version())

                # Info tooltip
                info_label = ctk.CTkLabel(
                    spotify_frame,
                    text=f"({len(all_spotify_ids)} versions)",
                    font=("Arial", 9),
                    text_color="gray",
                )
                info_label.pack(side="left", padx=5)

                # Tooltip avec le titre de la page Spotify si disponible
                if hasattr(track, "spotify_page_title") and track.spotify_page_title:
                    spotify_tooltip_text = f"Titre Spotify:\n{track.spotify_page_title[:80]}"
                    if len(track.spotify_page_title) > 80:
                        spotify_tooltip_text += "..."

                    def show_spotify_tooltip(event):
                        tooltip = ctk.CTkToplevel()
                        tooltip.wm_overrideredirect(True)
                        tooltip.wm_geometry(f"+{event.x_root+10}+{event.y_root+10}")

                        ctk.CTkLabel(
                            tooltip,
                            text=spotify_tooltip_text,
                            fg_color="gray20",
                            corner_radius=5,
                            font=("Arial", 9),
                        ).pack(padx=5, pady=2)

                        tooltip.after(3000, tooltip.destroy)

                    spotify_label.bind("<Enter>", show_spotify_tooltip)
            else:
                # Un seul ID, affichage normal
                spotify_url = f"https://open.spotify.com/intl-fr/track/{all_spotify_ids[0]}"

                spotify_label = ctk.CTkLabel(
                    spotify_frame, text="▶️ Écouter", text_color="#1DB954", cursor="hand2"
                )
                spotify_label.pack(side="left")

                import webbrowser

                spotify_label.bind("<Button-1>", lambda e: webbrowser.open(spotify_url))

                # Tooltip avec le titre de la page Spotify si disponible
                if hasattr(track, "spotify_page_title") and track.spotify_page_title:
                    spotify_tooltip_text = f"Titre Spotify:\n{track.spotify_page_title[:80]}"
                    if len(track.spotify_page_title) > 80:
                        spotify_tooltip_text += "..."

                    def show_spotify_tooltip(event):
                        tooltip = ctk.CTkToplevel()
                        tooltip.wm_overrideredirect(True)
                        tooltip.wm_geometry(f"+{event.x_root+10}+{event.y_root+10}")

                        ctk.CTkLabel(
                            tooltip,
                            text=spotify_tooltip_text,
                            fg_color="gray20",
                            corner_radius=5,
                            font=("Arial", 9),
                        ).pack(padx=5, pady=2)

                        tooltip.after(3000, tooltip.destroy)

                    spotify_label.bind("<Enter>", show_spotify_tooltip)

        # YouTube intelligent - ROUGE
        youtube_frame = ctk.CTkFrame(urls_frame, fg_color="transparent")
        youtube_frame.pack(side="left")

        ctk.CTkLabel(youtube_frame, text="📺 YouTube: ").pack(side="left")

        # Obtenir le lien YouTube intelligent
        artist_name = track.artist.name if track.artist else self.app.current_artist.name
        release_year = helpers.get_release_year_safely(track)

        # Priorité au lien en base (Genius media, ou recherche persistée) ;
        # la recherche live ne sert que de fallback pour les rares cas sans lien Genius.
        youtube_result = youtube_integration.get_youtube_link_for_track(
            artist_name,
            track.title,
            track.album,
            release_year,
            known_url=track.youtube_url,
            known_source=track.youtube_url_source,
        )

        # Persister le lien trouvé par la recherche si la confiance est suffisante.
        # L'if interne est volontairement séparé : c'est une écriture DB dont le
        # résultat conditionne la suite, pas une simple condition.
        if (  # noqa: SIM102
            youtube_result.get("source") == "search_auto"
            and youtube_result.get("confidence", 0) >= YOUTUBE_PERSIST_CONFIDENCE
            and track.id
        ):
            if self.app.data_manager.update_track_youtube_url(
                track.id, youtube_result["url"], "search_auto"
            ):
                track.youtube_url = youtube_result["url"]
                track.youtube_url_source = "search_auto"
                youtube_result["persisted"] = True

        # Affichage selon la provenance
        _yt_source = youtube_result.get("source")
        if _yt_source == "genius_media":
            # Lien fourni par Genius (media) — prioritaire, distinct du fallback recherche
            label_text = "▶️ Voir (Genius ✓)"
            label_color = "#1DB954"  # Vert = source fiable Genius
            tooltip_text = (
                f"Lien YouTube fourni par Genius (media)\n"
                f"Titre: {youtube_result.get('title', 'N/A')}\n"
                f"URL: {youtube_result.get('url', 'N/A')}"
            )
        elif _yt_source == "search_auto" and youtube_result.get("method") == "stored":
            # Lien trouvé par recherche lors d'une session précédente, persisté en base
            label_text = "▶️ Voir (auto ✓)"
            label_color = "#FFA500"  # Orange = fiable mais pas Genius
            tooltip_text = (
                f"Lien trouvé par recherche (persisté, confiance ≥ "
                f"{YOUTUBE_PERSIST_CONFIDENCE:.0%})\n"
                f"URL: {youtube_result.get('url', 'N/A')}"
            )
        elif youtube_result["type"] == "direct":
            # Lien direct trouvé automatiquement (recherche)
            label_text = f"▶️ Voir (auto • {youtube_result['confidence']:.0%})"
            label_color = "#FF0000"  # Rouge YouTube
            tooltip_text = (
                f"Lien automatique sélectionné (recherche)\n"
                f"Titre: {youtube_result.get('title', 'N/A')}\n"
                f"Chaîne: {youtube_result.get('channel', 'N/A')}\n"
                f"Confiance: {youtube_result['confidence']:.1%}"
            )
        else:
            # URL de recherche optimisée
            label_text = "🔍 Rechercher"
            label_color = "#FF6B6B"  # Rouge plus clair pour différencier
            tooltip_text = (
                f"Recherche optimisée\n"
                f"Type: {youtube_result.get('track_type', 'inconnu')}\n"
                f"Requête: {youtube_result.get('query', 'N/A')}"
            )

        youtube_label = ctk.CTkLabel(
            youtube_frame, text=label_text, text_color=label_color, cursor="hand2"
        )
        youtube_label.pack(side="left")

        # Fonction pour ouvrir YouTube
        def open_youtube():
            youtube_integration.open_youtube_link(youtube_result)

        youtube_label.bind("<Button-1>", lambda e: open_youtube())

        # ✅ OPTIONNEL: Tooltip pour plus d'infos
        def show_tooltip(event):
            # Créer un tooltip simple
            tooltip = ctk.CTkToplevel()
            tooltip.wm_overrideredirect(True)
            tooltip.wm_geometry(f"+{event.x_root+10}+{event.y_root+10}")

            ctk.CTkLabel(
                tooltip, text=tooltip_text, fg_color="gray20", corner_radius=5, font=("Arial", 9)
            ).pack(padx=5, pady=2)

            # Détruire après 3 secondes
            tooltip.after(3000, tooltip.destroy)

        youtube_label.bind("<Enter>", show_tooltip)

        # === SYSTÈME D'ONGLETS ===
        notebook = tkinter_ttk.Notebook(details_window)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        # === ONGLET 1: CRÉDITS MUSICAUX ===
        music_credits_frame = ctk.CTkFrame(notebook)
        notebook.add(music_credits_frame, text="🎵 Crédits musicaux")

        music_credits = track.get_music_credits()

        # En-tête avec statistiques
        music_header = ctk.CTkFrame(music_credits_frame)
        music_header.pack(fill="x", padx=10, pady=10)

        ctk.CTkLabel(
            music_header,
            text=f"🎵 Crédits musicaux ({len(music_credits)})",
            font=("Arial", 14, "bold"),
        ).pack(anchor="w", padx=5, pady=5)

        if track.has_complete_credits():
            status_color = "green"
            status_text = "✅ Crédits complets"
        elif music_credits:
            status_color = "orange"
            status_text = "⚠️ Crédits partiels"
        else:
            status_color = "red"
            status_text = "❌ Aucun crédit"

        ctk.CTkLabel(music_header, text=status_text, text_color=status_color).pack(
            anchor="w", padx=5
        )

        # Zone de crédits musicaux
        music_textbox = ctk.CTkTextbox(music_credits_frame, width=850, height=400)
        music_textbox.pack(fill="both", expand=True, padx=10, pady=10)

        if music_credits:
            from collections import defaultdict

            music_credits_by_role = defaultdict(list)
            for credit in music_credits:
                music_credits_by_role[credit.role.value].append(credit)

            for role, credits in sorted(music_credits_by_role.items()):
                music_textbox.insert("end", f"\n━━━ {role} ━━━\n", "bold")
                for credit in credits:
                    source_emoji = {
                        "genius": "🎤",
                        "spotify": "🎧",
                        "discogs": "💿",
                        "lastfm": "📻",
                    }.get(credit.source, "🔗")
                    detail = f" ({credit.role_detail})" if credit.role_detail else ""
                    music_textbox.insert("end", f"{source_emoji} {credit.name}{detail}\n")
        else:
            music_textbox.insert("end", "❌ Aucun crédit musical trouvé.\n\n")
            music_textbox.insert(
                "end",
                "💡 Utilisez le bouton 'Scraper les crédits' pour récupérer les informations depuis Genius.",
            )

        music_textbox.configure(state="disabled")

        # === ONGLET 2: CRÉDITS VIDÉO ===
        video_credits = track.get_video_credits()

        if video_credits:
            video_credits_frame = ctk.CTkFrame(notebook)
            notebook.add(video_credits_frame, text=f"🎬 Crédits vidéo ({len(video_credits)})")

            # En-tête vidéo
            video_header = ctk.CTkFrame(video_credits_frame)
            video_header.pack(fill="x", padx=10, pady=10)

            ctk.CTkLabel(
                video_header,
                text=f"🎬 Crédits vidéo ({len(video_credits)})",
                font=("Arial", 14, "bold"),
            ).pack(anchor="w", padx=5, pady=5)

            ctk.CTkLabel(
                video_header, text="Équipe technique du clip vidéo", text_color="gray"
            ).pack(anchor="w", padx=5)

            # Zone de crédits vidéo
            video_textbox = ctk.CTkTextbox(video_credits_frame, width=850, height=450)
            video_textbox.pack(fill="both", expand=True, padx=10, pady=10)

            video_credits_by_role = defaultdict(list)
            for credit in video_credits:
                video_credits_by_role[credit.role.value].append(credit)

            for role, credits in sorted(video_credits_by_role.items()):
                video_textbox.insert("end", f"\n━━━ {role} ━━━\n", "bold")
                for credit in credits:
                    source_emoji = {
                        "genius": "🎤",
                        "spotify": "🎧",
                        "discogs": "💿",
                        "lastfm": "📻",
                    }.get(credit.source, "🔗")
                    detail = f" ({credit.role_detail})" if credit.role_detail else ""
                    video_textbox.insert("end", f"{source_emoji} {credit.name}{detail}\n")

            video_textbox.configure(state="disabled")

        # === ONGLET 3: SAMPLES / RELATIONS (inspiré de) ===
        samples_frame = ctk.CTkFrame(notebook)
        notebook.add(samples_frame, text="🎚️ Samples")
        samples_scroll = ctk.CTkScrollableFrame(samples_frame, width=850, height=600)
        samples_scroll.pack(fill="both", expand=True, padx=5, pady=5)
        ctk.CTkLabel(
            samples_scroll,
            text="🎚️ Inspiré de (samples • interpolations • reprises • remix • trad. FR)",
            font=("Arial", 14, "bold"),
        ).pack(anchor="w", padx=10, pady=(8, 4))
        _rels = track.relationships or []
        if not _rels:
            ctk.CTkLabel(
                samples_scroll,
                text="Aucune relation détectée.\n(Re-fetch la discographie pour les primaires, ré-enrichis le feat.)",
                text_color="gray",
                justify="left",
            ).pack(anchor="w", padx=14, pady=8)
        else:
            import webbrowser
            from collections import defaultdict as _dd

            _type_label = {
                "samples": "🎵 Sample de",
                "interpolates": "🎼 Interpole",
                "cover_of": "🎤 Reprise de",
                "remix_of": "🔁 Remix de",
                "translation_fr": "🇫🇷 Traduction FR",
            }
            _grouped = _dd(list)
            for _r in _rels:
                _grouped[_r.get("type")].append(_r)
            for _typ in ["samples", "interpolates", "cover_of", "remix_of", "translation_fr"]:
                _items = _grouped.get(_typ) or []
                if not _items:
                    continue
                ctk.CTkLabel(
                    samples_scroll,
                    text=f"━━━ {_type_label.get(_typ, _typ)} ━━━",
                    font=("Arial", 12, "bold"),
                ).pack(anchor="w", padx=10, pady=(8, 2))
                for _r in _items:
                    _txt = f"   • {_r.get('title') or '?'} — {_r.get('artist') or '?'}"
                    _url = _r.get("url")
                    _lbl = ctk.CTkLabel(
                        samples_scroll,
                        text=_txt,
                        text_color=("#1f6aa5", "#4aa3df") if _url else ("gray20", "gray70"),
                        cursor="hand2" if _url else "arrow",
                        anchor="w",
                        justify="left",
                    )
                    _lbl.pack(anchor="w", padx=14, pady=1)
                    if _url:
                        _lbl.bind("<Button-1>", lambda e, u=_url: webbrowser.open(u))

        # === ONGLET 4: PAROLES ===
        lyrics_frame = ctk.CTkFrame(notebook)
        if has_lyrics:
            notebook.add(lyrics_frame, text="📝 Paroles")

            # Créer un scrollable frame à l'intérieur du frame de l'onglet
            lyrics_scrollable = ctk.CTkScrollableFrame(lyrics_frame, width=850, height=650)
            lyrics_scrollable.pack(fill="both", expand=True, padx=5, pady=5)

            # Fonction pour copier les paroles
            def copy_lyrics():
                """Copie les paroles dans le presse-papier"""
                details_window.clipboard_clear()
                details_window.clipboard_append(track.lyrics.text)
                messagebox.showinfo("Copié", "Paroles copiées dans le presse-papier")

            # Section Anecdotes EN PREMIER si disponibles
            if track.anecdotes:
                # Header anecdotes
                anecdotes_header = ctk.CTkFrame(lyrics_scrollable)
                anecdotes_header.pack(fill="x", padx=10, pady=(10, 5))

                ctk.CTkLabel(
                    anecdotes_header, text="💡 Anecdotes & Informations", font=("Arial", 14, "bold")
                ).pack(anchor="w", padx=5, pady=5)

                # Zone de texte pour les anecdotes
                anecdotes_textbox = ctk.CTkTextbox(
                    lyrics_scrollable,
                    width=820,
                    height=60,  # Hauteur divisée par 2
                    font=("Arial", 11),
                    wrap="word",
                )
                anecdotes_textbox.pack(fill="x", padx=10, pady=5)
                anecdotes_textbox.insert("0.0", track.anecdotes)
                anecdotes_textbox.configure(state="disabled")

                # Séparateur après les anecdotes
                ctk.CTkFrame(lyrics_scrollable, height=2, fg_color="gray").pack(
                    fill="x", padx=10, pady=10
                )

            # Header "Paroles complètes" avec stats et bouton Copier (APRÈS le séparateur)
            words_count = len(track.lyrics.text.split()) if track.lyrics.text else 0
            chars_count = len(track.lyrics.text) if track.lyrics.text else 0

            lyrics_header = ctk.CTkFrame(lyrics_scrollable)
            lyrics_header.pack(fill="x", padx=10, pady=(5, 10))

            # Partie gauche : titre et stats
            left_part = ctk.CTkFrame(lyrics_header, fg_color="transparent")
            left_part.pack(side="left", fill="x", expand=True)

            ctk.CTkLabel(left_part, text="📝 Paroles complètes", font=("Arial", 14, "bold")).pack(
                anchor="w", padx=5, pady=(0, 2)
            )

            info_text = f"📊 {words_count} mots • {chars_count} caractères"
            if track.lyrics.source:
                info_text += f" • {track.lyrics.source}"
            if track.lyrics.synced:
                info_text += " • ⏱ synchronisé"
                _sy_src = track.lyrics.synced_source
                if _sy_src:
                    info_text += f" ({_sy_src})"
                _sy_conf = track.lyrics.synced_confidence
                if _sy_conf is not None and _sy_conf < 2:
                    info_text += " ⚠ à vérifier"
            if track.lyrics.scraped_at:
                date_str = helpers.format_datetime(track.lyrics.scraped_at)
                info_text += f" • Récupérées le {date_str}"

            ctk.CTkLabel(left_part, text=info_text, text_color="gray", font=("Arial", 9)).pack(
                anchor="w", padx=5
            )

            # Bouton Copier à droite
            ctk.CTkButton(
                lyrics_header, text="📋 Copier", command=copy_lyrics, width=80, height=32
            ).pack(side="right", padx=5)

            # Zone de texte pour les paroles (nettoyées sans anecdote)
            lyrics_textbox = ctk.CTkTextbox(
                lyrics_scrollable,
                width=820,
                height=350,  # Hauteur encore plus réduite
                font=("Consolas", 11),
            )
            lyrics_textbox.pack(fill="x", padx=10, pady=10)

            # Nettoyer les paroles de l'anecdote si elle existe
            clean_lyrics = track.lyrics.text
            if track.anecdotes:
                # Méthode robuste : retirer tout le texte jusqu'au premier tag [Couplet], [Partie], etc.
                import re

                # Chercher le premier tag de structure de paroles
                first_tag_match = re.search(
                    r"\[(?:Intro|Couplet|Refrain|Verse|Chorus|Bridge|Hook|Pre-Chorus|Partie|Part|Outro|Interlude)",
                    clean_lyrics,
                    re.IGNORECASE,
                )

                if first_tag_match:
                    # Commencer à partir du premier tag
                    clean_lyrics = clean_lyrics[first_tag_match.start() :].strip()
                    logger.debug("Anecdote retirée des paroles (méthode tag)")
                else:
                    # Fallback : retirer les X premiers caractères si l'anecdote est au début
                    anecdote_length = len(track.anecdotes)
                    if clean_lyrics[:200].strip().startswith(track.anecdotes[:100].strip()):
                        # Chercher le prochain double saut de ligne après l'anecdote
                        cut_point = clean_lyrics.find("\n\n", anecdote_length - 50)
                        if cut_point > 0:
                            clean_lyrics = clean_lyrics[cut_point + 2 :].strip()
                            logger.debug("Anecdote retirée des paroles (méthode longueur)")

            # Timestamps par section (si synchro YTM dispo) : injectés dans les en-têtes
            if track.lyrics.synced:
                try:
                    from src.utils.lyrics_sync import annotate_sections

                    clean_lyrics = annotate_sections(clean_lyrics, track.lyrics.synced)
                except (ValueError, TypeError, KeyError, IndexError, AttributeError) as e:
                    logger.debug(f"Annotation timestamps échouée: {e}")

            formatted_lyrics = helpers.format_lyrics_for_display(clean_lyrics)
            lyrics_textbox.insert("0.0", formatted_lyrics)
            lyrics_textbox.configure(state="disabled")

        else:
            # Onglet paroles vide avec message
            notebook.add(lyrics_frame, text="📝 Paroles")

            empty_lyrics_container = ctk.CTkFrame(lyrics_frame)
            empty_lyrics_container.pack(fill="both", expand=True, padx=50, pady=50)

            ctk.CTkLabel(
                empty_lyrics_container,
                text="📝 Aucunes paroles disponibles",
                font=("Arial", 18, "bold"),
                text_color="gray",
            ).pack(expand=True, pady=(0, 10))

            ctk.CTkLabel(
                empty_lyrics_container,
                text="Utilisez le bouton 'Scraper paroles' dans l'interface principale\npour récupérer les paroles de ce morceau",
                font=("Arial", 12),
                text_color="gray",
                justify="center",
            ).pack(expand=True)

        # === ONGLET 4: INFORMATIONS TECHNIQUES - ✅ AMÉLIORÉ AVEC DEBUG FEATURING ===
        tech_frame = ctk.CTkFrame(notebook)
        notebook.add(tech_frame, text="🔧 Technique")

        tech_textbox = ctk.CTkTextbox(tech_frame, width=850, height=450)
        tech_textbox.pack(fill="both", expand=True, padx=10, pady=10)

        tech_textbox.insert("end", "🔧 INFORMATIONS TECHNIQUES\n")
        tech_textbox.insert("end", "=" * 50 + "\n\n")

        # IDs externes
        if track.genius_id:
            tech_textbox.insert("end", f"🎤 Genius ID: {track.genius_id}\n")
        if track.spotify_id:
            tech_textbox.insert("end", f"🎧 Spotify ID: {track.spotify_id}\n")
            # Afficher le titre de la page Spotify si disponible (pour vérification)
            if hasattr(track, "spotify_page_title") and track.spotify_page_title:
                # Limiter à 50 premiers caractères pour l'affichage
                display_title = track.spotify_page_title[:50]
                if len(track.spotify_page_title) > 50:
                    display_title += "..."
                tech_textbox.insert("end", f"   📄 Titre: {display_title}\n")
        if track.discogs_id:
            tech_textbox.insert("end", f"💿 Discogs ID: {track.discogs_id}\n")
        if track.isrc:
            tech_textbox.insert("end", f"🆔 ISRC: {track.isrc}\n")

        # Popularité
        if track.popularity:
            tech_textbox.insert("end", f"📈 Popularité: {track.popularity}\n")

        # Artwork
        if track.media.artwork_url:
            tech_textbox.insert("end", f"🖼️ Artwork: {track.media.artwork_url}\n")

        # Vidéo YouTube (chantier « Media ») : nature + vues de LA vidéo
        # (distinct des streams YTM, somme audio+clip).
        if track.media.youtube_video_kind or track.media.youtube_video_views is not None:
            _kind = track.media.youtube_video_kind or "?"
            if track.media.youtube_video_views is not None:
                _views = f"{track.media.youtube_video_views:,}".replace(",", " ")
                tech_textbox.insert("end", f"🎬 Vidéo ({_kind}) : {_views} vues\n")
            else:
                tech_textbox.insert("end", f"🎬 Vidéo : {_kind}\n")

        # ── PROVENANCE DES MÉTADONNÉES ──────────────────────────────
        def _yn(v):
            return "✅" if v else "❌"

        tech_textbox.insert("end", "\n🧭 PROVENANCE\n")
        _album_api = track._album_from_api
        _album_src = "API Genius" if _album_api else ("scrape" if track.album else "—")
        tech_textbox.insert("end", f"• Album : {track.album or 'N/A'}  ({_album_src})\n")
        _rd_api = track._release_date_from_api
        _rd_src = "API Genius" if _rd_api else ("scrape" if track.release_date else "—")
        tech_textbox.insert(
            "end", f"• Date de sortie : {track.release_date or 'N/A'}  ({_rd_src})\n"
        )
        _sp_src = (
            "Genius media"
            if getattr(track, "_spotify_from_api", None)
            else (
                "scrape Spotify"
                if getattr(track, "spotify_page_title", None)
                else ("—" if not track.spotify_id else "Genius media?")
            )
        )
        tech_textbox.insert("end", f"• Spotify ID : {_yn(track.spotify_id)}  ({_sp_src})\n")
        # youtube_url persisté en DB avec sa provenance : 'genius_media' (prioritaire)
        # ou 'search_auto' (fallback recherche, persisté si confiance ≥ YOUTUBE_PERSIST_CONFIDENCE)
        _yt_url = track.youtube_url
        _yt_source_raw = track.youtube_url_source
        if _yt_url:
            _yt_src = {
                "genius_media": "Genius media",
                "search_auto": "recherche auto (persistée)",
                "manual": "saisi manuellement ✎",
            }.get(_yt_source_raw, _yt_source_raw or "Genius media (legacy)")
        else:
            _yt_src = "recherche live (fallback, non persisté)"
        tech_textbox.insert("end", f"• YouTube : {_yn(_yt_url)}  ({_yt_src})\n")
        _isrc_src = getattr(track, "_isrc_source", None) or (
            "Deezer/ReccoBeats" if track.isrc else "—"
        )
        tech_textbox.insert("end", f"• ISRC : {_yn(track.isrc)}  ({_isrc_src})\n")

        # ── PAROLES ─────────────────────────────────────────────────
        _ly = track.lyrics.text or ""
        _has_struct = any(ln.lstrip().startswith("[") for ln in _ly.splitlines())
        _has_ts = bool(track.lyrics.synced)
        _ly_src = track.lyrics.source or "—"
        tech_textbox.insert("end", "\n📝 PAROLES\n")
        tech_textbox.insert("end", f"• Texte présent : {_yn(_ly)}  (source : {_ly_src})\n")
        tech_textbox.insert("end", f"• Structure Genius [Couplet/Refrain] : {_yn(_has_struct)}\n")
        _sy_src = track.lyrics.synced_source or ("?" if _has_ts else "—")
        _sy_conf = track.lyrics.synced_confidence
        _sy_conf_txt = {
            2: "2 (croisé LRCLIB+YTM)",
            1: "1 (source unique / après départage durée)",
        }.get(_sy_conf, "—")
        tech_textbox.insert("end", f"• Timestamps synchro : {_yn(_has_ts)}  (source : {_sy_src})\n")
        tech_textbox.insert("end", f"• Confiance synchro : {_sy_conf_txt}\n")
        if _has_ts and _sy_conf is not None and _sy_conf < 2:
            tech_textbox.insert(
                "end",
                "  ⚠️ à vérifier (sources divergentes ou unique) — clic droit → ✏️ pour corriger\n",
            )

        # ── DONNÉES AUDIO (BPM / KEY / MODE) ────────────────────────
        tech_textbox.insert("end", "\n🎛️ DONNÉES AUDIO\n")
        _bpm = track.audio.bpm
        _bpm_alt = track.audio.bpm_alt
        _bpm_src = track.audio.bpm_source or "—"
        _bpm_conf = track.audio.bpm_confidence
        tech_textbox.insert("end", f"• Provenance BPM : {_bpm_src}\n")
        _conf_txt = f"{_bpm_conf}" if _bpm_conf is not None else "—"
        tech_textbox.insert("end", f"• Arbitrage (vote) : confiance {_conf_txt}\n")
        if _bpm:
            _alt_txt = (
                f" • alt half/double-time : {_bpm_alt}"
                if _bpm_alt
                else " • pas d'octave alternative"
            )
            tech_textbox.insert("end", f"• BPM réel : {_bpm}{_alt_txt}\n")
        else:
            tech_textbox.insert("end", "• BPM réel : N/A\n")
        _km_src = track.audio.key_mode_source or "—"
        tech_textbox.insert("end", f"• Source Key/Mode : {_km_src}\n")
        _rb_res = track.audio.reccobeats_resolution or "—"
        tech_textbox.insert("end", f"• Résolution ReccoBeats : {_rb_res}\n")

        # ── STREAMS & DURÉE ─────────────────────────────────────────
        tech_textbox.insert("end", "\n📦 STREAMS & DURÉE\n")
        _sp_streams = track.streams.spotify_streams
        if _sp_streams is not None:
            _sp_upd = track.streams.spotify_streams_updated
            _sp_when = f"  (maj {helpers.format_datetime(_sp_upd)})" if _sp_upd else ""
            _n = f"{_sp_streams:,}".replace(",", " ")
            tech_textbox.insert("end", f"• Spotify : {_n}{_sp_when}\n")
        else:
            tech_textbox.insert("end", "• Spotify : —\n")
        _ytm_streams = track.streams.ytm_streams
        if _ytm_streams is not None:
            _ytm_upd = track.streams.ytm_streams_updated
            _ytm_when = f"  (maj {helpers.format_datetime(_ytm_upd)})" if _ytm_upd else ""
            _n = f"{_ytm_streams:,}".replace(",", " ")
            tech_textbox.insert("end", f"• YouTube Music : {_n}{_ytm_when}\n")
        else:
            tech_textbox.insert("end", "• YouTube Music : —\n")
        _dur = track.duration
        if _dur:
            tech_textbox.insert(
                "end", f"• Durée : {int(_dur)//60}:{int(_dur)%60:02d}  ({int(_dur)}s)\n"
            )
        else:
            tech_textbox.insert("end", "• Durée : —\n")

        # ── COMPLÉTUDE (à ré-enrichir ?) ────────────────────────────
        tech_textbox.insert("end", "\n🩺 COMPLÉTUDE\n")
        _missing = []
        if not track.audio.bpm:
            _missing.append("BPM")
        if track.audio.key is None or track.audio.mode is None:
            _missing.append("Key/Mode")
        if not track.isrc:
            _missing.append("ISRC")
        if not (track.lyrics.text or ""):
            _missing.append("paroles")
        if not track.spotify_id:
            _missing.append("Spotify ID")
        if _missing:
            tech_textbox.insert(
                "end", "• ⚠️ Manque : " + ", ".join(_missing) + "  → à ré-enrichir\n"
            )
        else:
            tech_textbox.insert("end", "• ✅ Complet\n")

        # ── RELATIONS ───────────────────────────────────────────────
        _nrel = len(track.relationships or [])
        tech_textbox.insert("end", f"\n🎚️ Relations : {_nrel}\n")

        # Métadonnées de scraping
        tech_textbox.insert("end", "\n📅 HISTORIQUE\n")
        if track.last_scraped:
            tech_textbox.insert(
                "end", f"• Dernier scraping: {helpers.format_datetime(track.last_scraped)}\n"
            )
        if track.created_at:
            tech_textbox.insert("end", f"• Créé le: {helpers.format_datetime(track.created_at)}\n")
        if track.updated_at:
            tech_textbox.insert(
                "end", f"• Mis à jour le: {helpers.format_datetime(track.updated_at)}\n"
            )

        # === ONGLET 5: CERTIFICATIONS ===
        cert_frame = ctk.CTkFrame(notebook)
        notebook.add(cert_frame, text="🏆 Certifications")

        try:
            from src.utils.cert_matcher import get_cert_matcher

            matcher = get_cert_matcher()

            # Raccordement UNIFIÉ multi-pays (SNEP 🇫🇷 + BRMA 🇧🇪 + RIAA 🇺🇸)
            _extra = []
            _pan = track.primary_artist_name
            if _pan and _pan != self.app.current_artist.name:
                _extra.append(_pan)
            _fa = track.featured_artists
            if isinstance(_fa, str) and _fa:
                _extra.append(_fa)
            elif isinstance(_fa, (list, tuple)):
                _extra.extend(str(x) for x in _fa if x)
            track_certs = matcher.get_track_certifications(
                self.app.current_artist.name, track.title, extra_artists=_extra
            )
            album_certs = (
                matcher.get_album_certifications(self.app.current_artist.name, track.album)
                if track.album
                else []
            )

            if track_certs or album_certs:
                # Afficher les infos de certification
                cert_info = ctk.CTkTextbox(cert_frame, width=850, height=450)
                cert_info.pack(fill="both", expand=True, padx=10, pady=10)

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

                body_label = {
                    "SNEP": "SNEP (France)",
                    "BRMA": "BRMA (Belgique)",
                    "RIAA": "RIAA (USA)",
                }

                def _render_grouped(certs):
                    """Rend les certifs GROUPÉES PAR PAYS (ordre du matcher : FR, BE, US)."""
                    txt = ""
                    bodies = []
                    for c in certs:
                        if c.get("body") not in bodies:
                            bodies.append(c.get("body"))
                    for body in bodies:
                        group = [c for c in certs if c.get("body") == body]
                        flag = group[0].get("flag", "🏆")
                        txt += f"\n{flag} {body_label.get(body, body)}\n"
                        txt += "-" * 60 + "\n"
                        for c in group:
                            lvl = c.get("certification", "")
                            emoji = emoji_map.get(lvl, "🏆")
                            line = f"{emoji} {lvl.upper()}"
                            if c.get("title"):
                                line += f" — {c.get('title')}"
                            txt += line + "\n"
                            if c.get("release_date"):
                                txt += (
                                    f"   📅 Sortie: {helpers.format_date(c.get('release_date'))}\n"
                                )
                            txt += f"   ✅ Constat: {helpers.format_date(c.get('certification_date', 'N/A'))}\n"
                            if c.get("publisher"):
                                txt += f"   🏢 Éditeur: {c.get('publisher')}\n"
                            if c.get("detail_url"):
                                txt += f"   🔗 {c.get('detail_url')}\n"
                            if c.get("release_date") and c.get("certification_date"):
                                try:
                                    from datetime import datetime

                                    r = datetime.strptime(str(c["release_date"])[:10], "%Y-%m-%d")
                                    cc = datetime.strptime(
                                        str(c["certification_date"])[:10], "%Y-%m-%d"
                                    )
                                    d = (cc - r).days
                                    if d >= 0:
                                        txt += f"   ⏱️ Obtention: {d} j ({d // 365} an(s), {(d % 365) // 30} mois)\n"
                                except Exception:
                                    pass
                            txt += "\n"
                    return txt

                def _fmt_delay(days: int) -> str:
                    return f"{days} j ({days // 365} an(s), {(days % 365) // 30} mois)"

                cert_text = ""
                if track_certs:
                    cert_text += "🎵 CERTIFICATIONS DU MORCEAU\n" + "=" * 60 + "\n"
                    # Délai d'obtention (écart sortie→certif) via le modèle : un
                    # palier important (Or/Platine/Diamant) par ligne, + la plus
                    # haute certif si c'est un palier à multiplicateur (ex. 2× Platine).
                    _delay_lines = [
                        f"   • {lvl} : {_fmt_delay(d)}"
                        for lvl, d in track.certification_milestone_durations()
                    ]
                    _high_days = track.calculate_certification_duration()
                    _high_lvl = track.certs.level
                    if (
                        _high_days is not None
                        and _high_lvl
                        and _high_lvl not in ("Or", "Platine", "Diamant")
                    ):
                        _delay_lines.append(
                            f"   • plus haute ({_high_lvl}) : {_fmt_delay(_high_days)}"
                        )
                    if _delay_lines:
                        cert_text += "⏱️ Délai d'obtention :\n" + "\n".join(_delay_lines) + "\n"
                    cert_text += _render_grouped(track_certs)
                if album_certs:
                    cert_text += (
                        f"\n💿 CERTIFICATIONS DE L'ALBUM — {track.album}\n" + "=" * 60 + "\n"
                    )
                    cert_text += _render_grouped(album_certs)

                cert_info.insert("0.0", cert_text)
                cert_info.configure(state="disabled")
            else:
                no_cert = ctk.CTkLabel(
                    cert_frame,
                    text="❌ Aucune certification trouvée pour ce morceau ou son album",
                    font=("Arial", 14),
                )
                no_cert.pack(expand=True)
        except Exception as e:
            logger.error(f"Erreur affichage certifications: {e}", exc_info=True)
            error_label = ctk.CTkLabel(cert_frame, text=f"Erreur: {e}", text_color="red")
            error_label.pack(expand=True)

        # ✅ NOUVEAU: Debug featuring détaillé
        tech_textbox.insert("end", "\n🎤 DEBUG FEATURING:\n")
        tech_textbox.insert("end", f"• is_featuring: {track.is_featuring}\n")
        tech_textbox.insert("end", f"• primary_artist_name: {track.primary_artist_name}\n")
        tech_textbox.insert("end", f"• secondary_role: {track.secondary_role or '—'}\n")
        tech_textbox.insert("end", f"• featured_artists: {track.featured_artists}\n")
        tech_textbox.insert(
            "end", f"• track.artist.name: {track.artist.name if track.artist else 'Non défini'}\n"
        )
        tech_textbox.insert(
            "end",
            f"• current_artist.name: {self.app.current_artist.name if self.app.current_artist else 'Non défini'}\n",
        )

        # Informations de la base de données
        tech_textbox.insert("end", "\n💾 BASE DE DONNÉES:\n")
        tech_textbox.insert("end", f"• Track ID: {track.id}\n")
        tech_textbox.insert("end", f"• _album_from_api: {track._album_from_api}\n")
        tech_textbox.insert(
            "end",
            f"• _release_date_from_api: {helpers.format_date(track._release_date_from_api)}\n",
        )

        # Erreurs de scraping
        if track.scraping_errors:
            tech_textbox.insert("end", "\n❌ ERREURS DE SCRAPING:\n")
            for i, error in enumerate(track.scraping_errors, 1):
                tech_textbox.insert("end", f"{i}. {error}\n")

        tech_textbox.configure(state="disabled")

        # === ONGLET STREAMS ===
        streams_frame = ctk.CTkFrame(notebook)
        notebook.add(streams_frame, text="📊 Streams")

        # Onglet « Technique » (debug) déplacé en dernier, après Streams
        try:
            notebook.insert("end", tech_frame)
        except Exception:
            pass

        try:
            from src.utils.streams_calculator import (
                COMBINED_SHARE,
                SPOTIFY_SHARE,
                YTM_SHARE,
                calculate_total_streams,
                format_streams,
                streams_source_label,
            )

            sp = track.streams.spotify_streams
            yt = track.streams.ytm_streams
            sp_upd = track.streams.spotify_streams_updated
            yt_upd = track.streams.ytm_streams_updated
            total_est = calculate_total_streams(sp, yt)
            suffix = streams_source_label(sp, yt)

            streams_content = ctk.CTkFrame(streams_frame, fg_color="transparent")
            streams_content.pack(fill="both", expand=True, padx=30, pady=20)

            def row(label, value, bold=False, color=None):
                f = ctk.CTkFrame(streams_content, fg_color="transparent")
                f.pack(fill="x", pady=3)
                ctk.CTkLabel(
                    f,
                    text=label,
                    width=220,
                    anchor="w",
                    font=ctk.CTkFont(weight="bold" if bold else "normal"),
                ).pack(side="left")
                kw = {"text_color": color} if color else {}
                ctk.CTkLabel(
                    f,
                    text=value,
                    anchor="w",
                    font=ctk.CTkFont(weight="bold" if bold else "normal"),
                    **kw,
                ).pack(side="left")

            row("Streams Spotify :", format_streams(sp) if sp else "—")
            if sp_upd:
                row("  MaJ Spotify :", helpers.format_datetime(sp_upd) if sp_upd else "—")

            row("Streams YouTube Music :", format_streams(yt) if yt else "—")
            if yt_upd:
                row("  MaJ YouTube Music :", helpers.format_datetime(yt_upd) if yt_upd else "—")

            ctk.CTkLabel(streams_content, text="─" * 60, text_color="gray").pack(
                fill="x", pady=(10, 4)
            )

            if total_est:
                row(
                    "Streams totaux estimés :",
                    f"{format_streams(total_est)}{suffix}",
                    bold=True,
                    color="#4fc3f7",
                )
                # Détail de la formule
                if sp and yt:
                    formula = f"({format_streams(sp)} Sp + {format_streams(yt)} YT) ÷ {COMBINED_SHARE:.0%}"
                elif sp:
                    formula = f"{format_streams(sp)} Sp ÷ {SPOTIFY_SHARE:.0%} (Spotify seul)"
                else:
                    formula = f"{format_streams(yt)} YT ÷ {YTM_SHARE:.0%} (YT Music seul)"
                ctk.CTkLabel(
                    streams_content,
                    text=f"Formule : {formula}",
                    text_color="gray",
                    font=ctk.CTkFont(size=11),
                ).pack(anchor="w", pady=(0, 6))
            else:
                row("Streams totaux estimés :", "Données insuffisantes", color="gray")

            ctk.CTkLabel(
                streams_content,
                text="Parts de marché FR 2025 : Spotify ~40 %  •  YT Music ~25 %  •  Ensemble ~65 %",
                text_color="gray",
                font=ctk.CTkFont(size=10),
            ).pack(anchor="w", pady=(10, 0))

        except Exception as e:
            ctk.CTkLabel(streams_frame, text=f"Erreur : {e}", text_color="red").pack(expand=True)

        # Bouton de fermeture
        close_button = ctk.CTkButton(
            details_window, text="Fermer", command=details_window.destroy, width=100
        )
        close_button.pack(pady=10)
