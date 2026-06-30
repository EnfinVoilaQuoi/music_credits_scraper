"""Interface graphique principale de l'application - VERSION AMÉLIORÉE"""
import customtkinter as ctk
import tkinter  # ✅ AJOUTÉ: Pour le menu contextuel
from tkinter import ttk, messagebox, filedialog
import threading
from typing import Optional, List
from datetime import datetime
import unicodedata

from src.config import WINDOW_WIDTH, WINDOW_HEIGHT, THEME
from src.api.genius_api import GeniusAPI
from src.scrapers.genius_scraper_v2 import GeniusScraper
from src.scrapers.genius_scraper_v3 import GeniusScraperV3
from src.utils.data_manager import DataManager
from src.utils.data_enricher import DataEnricher
from src.utils.logger import get_logger
from src.utils.youtube_integration import youtube_integration
from src.models import Artist, Track
from tkinter import ttk as tkinter_ttv
from src.utils.disabled_tracks_manager import DisabledTracksManager
from src.utils.deleted_tracks_manager import DeletedTracksManager
from src.gui.certification_update_gui import CertificationUpdateDialog


logger = get_logger(__name__)

# Configuration du thème
ctk.set_appearance_mode(THEME)
ctk.set_default_color_theme("blue")


class MainWindow:
    """Fenêtre principale de l'application"""
    
    def __init__(self):
        self.root = ctk.CTk()
        self.root.title("Music Credits Scraper")
        self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        
        # Services
        self.genius_api = GeniusAPI()
        self.data_manager = DataManager()
        self.data_enricher = DataEnricher(
            headless_reccobeats=True,
            headless_songbpm=True,
            headless_spotify_scraper=True
        )
        self.current_artist: Optional[Artist] = None
        self.tracks: List[Track] = []
        
        # Variables
        self.is_scraping = False
        self.selected_tracks = set()  # Stocker les morceaux sélectionnés
        self.disabled_tracks = set()  # Stocker les morceaux désactivés
        self.sort_column = None
        self.sort_reverse = False
        self.last_selected_index = None  # Sélection multiple
        self.disabled_tracks_manager = DisabledTracksManager()
        self.deleted_tracks_manager = DeletedTracksManager()
        self.open_detail_windows = {}  # Dict: {track_id: (window, track_object)}
        
        self._create_widgets()
        self._update_statistics()
        self.scraper = None
        self.headless_var = ctk.BooleanVar(value=True)

        # Gerer la fermeture de l'application
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
    
    def _create_widgets(self):
        """Crée tous les widgets de l'interface - VERSION RÉORGANISÉE"""
        # Frame principale
        main_frame = ctk.CTkFrame(self.root)
        main_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        # === Section recherche ===
        search_frame = ctk.CTkFrame(main_frame)
        search_frame.pack(fill="x", padx=5, pady=5)
        
        ctk.CTkLabel(search_frame, text="Artiste:", font=("Arial", 14)).pack(side="left", padx=5)
        
        self.artist_entry = ctk.CTkEntry(search_frame, width=300, placeholder_text="Nom de l'artiste")
        self.artist_entry.pack(side="left", padx=5)
        self.artist_entry.bind("<Return>", lambda e: self._search_artist())
        
        self.search_button = ctk.CTkButton(
            search_frame, 
            text="Rechercher", 
            command=self._search_artist,
            width=100
        )
        self.search_button.pack(side="left", padx=5)
        
        self.load_button = ctk.CTkButton(
            search_frame,
            text="Charger existant",
            command=self._load_existing_artist,
            width=120
        )
        self.load_button.pack(side="left", padx=5)
        
        # === Section infos artiste ===
        info_frame = ctk.CTkFrame(main_frame)
        info_frame.pack(fill="x", padx=5, pady=5)
        
        self.artist_info_label = ctk.CTkLabel(
            info_frame, 
            text="Aucun artiste sélectionné", 
            font=("Arial", 16, "bold")
        )
        self.artist_info_label.pack(pady=5)
        
        self.tracks_info_label = ctk.CTkLabel(info_frame, text="")
        self.tracks_info_label.pack()
        
        # === Section contrôles ===
        control_frame = ctk.CTkFrame(main_frame)
        control_frame.pack(fill="x", padx=5, pady=5)
        
        # 1. Récupérer les morceaux
        self.get_tracks_button = ctk.CTkButton(
            control_frame,
            text="Discographie",
            command=self._get_tracks,
            state="disabled",
            width=150
        )
        self.get_tracks_button.pack(side="left", padx=5)
        
        # 2. Crédits & Paroles (menu combiné)
        self.scrape_button = ctk.CTkButton(
            control_frame,
            text="Crédits & Paroles",
            command=self._show_scraping_menu,
            state="disabled",
            width=180,
            fg_color="#B8860B",  # Jaune foncé (DarkGoldenrod)
            hover_color="#996515",  # Jaune encore plus foncé au survol
            text_color="white"  # Texte blanc
        )
        self.scrape_button.pack(side="left", padx=5)
        
        # 5. Données additionnelles
        self.enrich_button = ctk.CTkButton(
            control_frame,
            text="Données Add.",
            command=self._start_enrichment,
            state="disabled",
            width=150,
            fg_color="#B22222",  # Rouge foncé (FireBrick)
            hover_color="#8B0000",  # Rouge très foncé (DarkRed) au survol
            text_color="white"  # Texte blanc
        )
        self.enrich_button.pack(side="left", padx=5)

        # 6. Bouton de mise à jour des certifications
        self.update_certif_button = ctk.CTkButton(
            control_frame,
            text="Certifications",
            command=self._open_certification_update,
            width=150,
            fg_color="darkgreen",
            hover_color="green"
        )
        self.update_certif_button.pack(side="left", padx=5)

        # 7. Nb Streams (Spotify + YouTube Music)
        self.streams_button = ctk.CTkButton(
            control_frame,
            text="Nb Streams",
            command=self._start_streams_update,
            state="disabled",
            width=130,
            fg_color="#1a237e",
            hover_color="#283593",
            text_color="white"
        )
        self.streams_button.pack(side="left", padx=5)

        # 8. Exporter (aligné à droite)
        self.export_button = ctk.CTkButton(
            control_frame,
            text="Exporter",
            command=self._export_data,
            state="disabled",
            width=100
        )
        self.export_button.pack(side="right", padx=5)
        
        # Progress bar
        self.progress_var = ctk.DoubleVar()
        self.progress_bar = ctk.CTkProgressBar(control_frame, variable=self.progress_var, width=200)
        self.progress_bar.pack(side="left", padx=10)  # IMPORTANT : faire le pack()
        self.progress_bar.set(0)
        self.progress_bar.pack_forget()  # Puis la cacher immédiatement

        self.progress_label = ctk.CTkLabel(control_frame, text="")
        self.progress_label.pack(side="left")
        
        # === Tableau des morceaux avec COLONNE PAROLES ===
        table_frame = ctk.CTkFrame(main_frame)
        table_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Frame pour les boutons de sélection - ✅ AMÉLIORÉE
        selection_frame = ctk.CTkFrame(table_frame)
        selection_frame.pack(fill="x", padx=5, pady=5)

        # Bouton pour cocher les morceaux sélectionnés
        ctk.CTkButton(
            selection_frame,
            text="✅",
            command=self._check_selected_tracks,
            width=35,
            font=("Arial", 12)
        ).pack(side="left", padx=(5, 2))

        ctk.CTkButton(
            selection_frame,
            text="Tout sélectionner",
            command=self._select_all_tracks,
            width=120
        ).pack(side="left", padx=5)
        
        ctk.CTkButton(
            selection_frame,
            text="Tout désélectionner",
            command=self._deselect_all_tracks,
            width=120
        ).pack(side="left", padx=5)
        
        # ✅ NOUVEAU: Boutons pour les morceaux désactivés
        ctk.CTkButton(
            selection_frame,
            text="Désactiver sélectionnés",
            command=self._disable_selected_tracks,
            width=140,
            fg_color="gray",
            hover_color="darkgray"
        ).pack(side="left", padx=5)
        
        self.selected_count_label = ctk.CTkLabel(selection_frame, text="")
        self.selected_count_label.pack(side="left", padx=20)

        # Bascule de vue Morceaux / Albums
        self.view_mode = "tracks"
        self.view_switch = ctk.CTkSegmentedButton(
            selection_frame,
            values=["Morceaux", "Albums"],
            command=self._set_view_mode,
            width=180
        )
        self.view_switch.set("Morceaux")
        self.view_switch.pack(side="right", padx=10)

        # Créer le Treeview dans un conteneur approprié
        tree_container = ctk.CTkFrame(table_frame)
        tree_container.pack(fill="both", expand=True)
        
        tree_scroll_frame = ctk.CTkFrame(tree_container)
        tree_scroll_frame.pack(fill="both", expand=True)
        
        # COLONNES AVEC COLONNE PAROLES ENTRE CRÉDITS ET BPM + DURÉE ENTRE BPM ET CERTIF
        self.TRACK_COLUMNS = ("Titre", "Artiste principal", "Album", "Date sortie", "Crédits", "Paroles", "BPM", "Durée", "Certif.", "Streams", "Statut")
        self.ALBUM_COLUMNS = ("Album", "Date sortie", "Morceaux", "Crédits", "Paroles", "Durée totale", "Streams Spotify", "Streams YTM")
        self.tree = ttk.Treeview(tree_scroll_frame, columns=self.TRACK_COLUMNS, show="tree headings", height=15)

        # Variable pour suivre l'ordre de tri
        self.sort_reverse = {}

        self._configure_tree_for_tracks()
        
        # Scrollbars
        vsb = ttk.Scrollbar(tree_scroll_frame, orient="vertical", command=self.tree.yview)
        vsb.pack(side="right", fill="y")
        
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.configure(yscrollcommand=vsb.set)
        
        hsb_frame = ctk.CTkFrame(tree_container)
        hsb_frame.pack(fill="x")
        
        hsb = ttk.Scrollbar(hsb_frame, orient="horizontal", command=self.tree.xview)
        hsb.pack(fill="x")
        self.tree.configure(xscrollcommand=hsb.set)
        
        # ✅ AMÉLIORÉ: Bindings pour sélection multiple et clic droit
        self.tree.bind("<Double-Button-1>", self._show_track_details)
        self.tree.bind("<Button-1>", self._on_tree_click)
        # Cocher-glisser : maintenir le clic sur une coche et glisser
        self.tree.bind("<B1-Motion>", self._on_tree_drag)
        self.tree.bind("<ButtonRelease-1>", self._on_tree_release)
        self.tree.bind("<Button-3>", self._on_right_click)  # Clic droit pour menu contextuel
        
        # === Section statistiques ===
        stats_frame = ctk.CTkFrame(main_frame)
        stats_frame.pack(fill="x", padx=5, pady=5)
        
        self.stats_label = ctk.CTkLabel(stats_frame, text="", font=("Arial", 12))
        self.stats_label.pack()

    # ──────────────────────────────────────────────────────────────────────
    # Bascule de vue Morceaux / Albums
    # ──────────────────────────────────────────────────────────────────────

    def _configure_tree_for_tracks(self):
        """Colonnes de la vue Morceaux (vue par défaut)"""
        self.tree.configure(columns=self.TRACK_COLUMNS)
        self.tree.heading("#0", text="✓")
        self.tree.column("#0", width=50, stretch=False)
        widths = {
            "Titre": (220, "w"), "Artiste principal": (130, "w"),
            "Album": (160, "w"), "Date sortie": (80, "w"),
            "Crédits": (70, "center"), "Paroles": (60, "center"),
            "BPM": (90, "w"), "Durée": (70, "center"),
            "Certif.": (50, "center"), "Streams": (120, "e"),
            "Statut": (70, "w"),
        }
        for col in self.TRACK_COLUMNS:
            w, anchor = widths[col]
            self.tree.heading(col, text=col, command=lambda c=col: self._sort_column(c))
            self.tree.column(col, width=w, anchor=anchor)

    def _configure_tree_for_albums(self):
        """Colonnes de la vue Albums (stats agrégées)"""
        self.tree.configure(columns=self.ALBUM_COLUMNS)
        self.tree.heading("#0", text="💿")
        self.tree.column("#0", width=40, stretch=False)
        widths = {
            "Album": (260, "w"), "Date sortie": (90, "w"),
            "Morceaux": (80, "center"), "Crédits": (70, "center"),
            "Paroles": (80, "center"), "Durée totale": (90, "center"),
            "Streams Spotify": (130, "e"), "Streams YTM": (130, "e"),
        }
        for col in self.ALBUM_COLUMNS:
            w, anchor = widths[col]
            self.tree.heading(col, text=col, command="")
            self.tree.column(col, width=w, anchor=anchor)

    def _set_view_mode(self, value):
        """Callback du sélecteur Morceaux / Albums"""
        mode = "albums" if value == "Albums" else "tracks"
        if mode == getattr(self, "view_mode", "tracks"):
            return
        self.view_mode = mode
        if mode == "albums":
            self._populate_albums_table()
        else:
            self._configure_tree_for_tracks()
            self._populate_tracks_table()

    @staticmethod
    def _normalize_album_title(s: str) -> str:
        for apo in ("’", "‘", "`", "´"):
            s = s.replace(apo, "'")
        return " ".join(s.lower().strip().split())

    def _populate_albums_table(self):
        """Remplit le tableau avec les albums et leurs stats agrégées"""
        self._configure_tree_for_albums()
        for item in self.tree.get_children():
            self.tree.delete(item)

        if not self.current_artist or not getattr(self.current_artist, 'tracks', None):
            return

        # Stats streams par album (table albums : Kworb Spotify + YTMusic)
        albums_db = {}
        try:
            for a in self.data_manager.get_albums_for_artist(self.current_artist.id):
                albums_db[self._normalize_album_title(a['title'])] = a
        except Exception as e:
            logger.debug(f"Albums DB indisponibles: {e}")

        # Grouper les morceaux par album (clé normalisée : "d'" == "d’")
        groups = {}
        for track in self.current_artist.tracks:
            album = (track.album or "").strip() or "— Singles / sans album —"
            key = self._normalize_album_title(album)
            if key not in groups:
                groups[key] = [album, []]
            groups[key][1].append(track)
        groups = {display: tracks for display, tracks in groups.values()}

        def earliest_date(tracks):
            dates = []
            for t in tracks:
                d = getattr(t, 'release_date', None)
                if isinstance(d, str):
                    try:
                        d = datetime.fromisoformat(d.split('T')[0])
                    except Exception:
                        d = None
                if d:
                    dates.append(d)
            return min(dates) if dates else None

        def fmt_streams(v):
            return f"{v:,}".replace(",", " ") if v else ""

        # Trier par date de sortie décroissante (singles en dernier)
        ordered = sorted(
            groups.items(),
            key=lambda kv: (kv[0].startswith("—"),
                            -(earliest_date(kv[1]) or datetime.min).timestamp())
        )

        for album, tracks in ordered:
            n = len(tracks)
            credits = sum(len(getattr(t, 'credits', []) or []) for t in tracks)
            lyrics = sum(1 for t in tracks
                         if getattr(t, 'lyrics', None) and str(t.lyrics).strip())
            total_sec = 0
            for t in tracks:
                d = getattr(t, 'duration', None)
                if isinstance(d, int):
                    total_sec += d
                elif isinstance(d, str) and ':' in d:
                    try:
                        parts = [int(p) for p in d.split(':')]
                        total_sec += parts[-1] + parts[-2] * 60 + \
                            (parts[-3] * 3600 if len(parts) > 2 else 0)
                    except Exception:
                        pass
            if total_sec:
                h, rem = divmod(total_sec, 3600)
                m, s = divmod(rem, 60)
                duree = f"{h}h{m:02d}" if h else f"{m}:{s:02d}"
            else:
                duree = ""

            date = earliest_date(tracks)
            date_str = date.strftime("%d/%m/%Y") if date else ""

            db = albums_db.get(self._normalize_album_title(album), {})
            sp_streams = fmt_streams(db.get('spotify_streams'))
            ytm_streams = fmt_streams(db.get('ytm_streams'))

            self.tree.insert("", "end", text="💿", values=(
                album, date_str, n, credits, f"{lyrics}/{n}", duree,
                sp_streams, ytm_streams
            ))

    def _populate_tracks_table(self):
        """Remplit le tableau avec les morceaux - VERSION CORRIGÉE CRÉDITS"""
        # En vue Albums, rafraîchir la vue Albums à la place
        if getattr(self, 'view_mode', 'tracks') == 'albums':
            self._populate_albums_table()
            return

        # Nettoyer le tableau
        for item in self.tree.get_children():
            self.tree.delete(item)

        if not self.current_artist or not getattr(self.current_artist, 'tracks', None):
            return

        # Charger les morceaux désactivés depuis la mémoire (IDs, pas indices)
        try:
            self.disabled_tracks = self.disabled_tracks_manager.load_disabled_tracks(
                self.current_artist.name
            )
        except Exception as e:
            logger.debug(f"Pas de morceaux désactivés sauvegardés: {e}")
            self.disabled_tracks = set()

        # Ajouter les morceaux au tableau
        for i, track in enumerate(self.current_artist.tracks):
            try:
                # Déterminer si le morceau est désactivé (par ID, pas par index)
                is_disabled = self._is_track_disabled(track)
                
                # Formatage des valeurs
                title = track.title or f"Track {i+1}"
                
                # Artiste principal - gestion du featuring
                if getattr(track, 'is_featuring', False) and getattr(track, 'primary_artist_name', None):
                    artist_display = track.primary_artist_name
                else:
                    artist_display = track.artist.name if track.artist else ""

                # Rôle secondaire (Additional Voices…) : marqueur distinct du feat
                _sec_role = getattr(track, 'secondary_role', None)
                if _sec_role:
                    artist_display = f"{artist_display} · 🎙️ {_sec_role}"
                    title = f"🎙️ {title}"

                album = getattr(track, 'album', '') or ""
                
                # Date de sortie - FORMAT FRANÇAIS (JJ/MM/AAAA)
                release_date = ""
                if hasattr(track, 'release_date') and track.release_date:
                    try:
                        if isinstance(track.release_date, str):
                            # Convertir string ISO vers datetime puis vers format français
                            from datetime import datetime
                            dt = datetime.fromisoformat(track.release_date.replace('Z', '+00:00').split('T')[0])
                            release_date = dt.strftime("%d/%m/%Y")
                        else:
                            # Déjà un objet datetime
                            release_date = track.release_date.strftime("%d/%m/%Y")
                    except:
                        release_date = str(track.release_date).split('T')[0] if 'T' in str(track.release_date) else str(track.release_date)
                
                # CORRECTION: Obtenir le nombre de crédits directement
                credits_count = 0
                if hasattr(track, 'credits') and track.credits:
                    credits_count = len(track.credits)
                credits_display = str(credits_count)
                
                # Paroles
                lyrics_display = "✓" if getattr(track, 'has_lyrics', False) else ""
                
                # BPM avec tonalité - VERSION AMÉLIORÉE
                bpm = ""  # ⭐ IMPORTANT : Initialiser la variable
                if hasattr(track, 'bpm') and track.bpm:
                    bpm = str(track.bpm)
                    
                    # ⭐ LOGIQUE AMÉLIORÉE pour afficher la tonalité
                    musical_key = None
                    
                    # 1. Essayer musical_key directement
                    if hasattr(track, 'musical_key') and track.musical_key:
                        musical_key = track.musical_key
                    
                    # 2. FALLBACK : Calculer à partir de key et mode
                    elif hasattr(track, 'key') and hasattr(track, 'mode') and track.key and track.mode:
                        try:
                            from src.utils.music_theory import key_mode_to_french_from_string
                            musical_key = key_mode_to_french_from_string(track.key, track.mode)
                            
                            # ⭐ BONUS : Stocker le résultat pour la prochaine fois
                            track.musical_key = musical_key
                            logger.debug(f"Musical key calculée et stockée pour '{track.title}': {musical_key}")
                        except Exception as e:
                            logger.warning(f"Erreur conversion key/mode pour '{track.title}': {e}")
                    
                    # Ajouter la tonalité au BPM si disponible
                    if musical_key:
                        bpm = f"{track.bpm} ({musical_key})"

                # Durée du morceau
                duration_display = ""
                if hasattr(track, 'duration') and track.duration:
                    try:
                        # Format MM:SS ou HH:MM:SS
                        if isinstance(track.duration, str):
                            duration_display = track.duration
                        elif isinstance(track.duration, int):
                            # Durée en secondes
                            minutes = track.duration // 60
                            seconds = track.duration % 60
                            duration_display = f"{minutes}:{seconds:02d}"
                    except:
                        pass

                # Certifications - Lire depuis track.certifications au lieu de l'API
                certif_display = ""
                try:
                    # Vérifier si le track a des certifications stockées
                    if hasattr(track, 'certifications') and track.certifications:
                        # Prendre la plus haute certification (première dans la liste déjà triée)
                        cert_level = track.certifications[0].get('certification', '')
                        emoji_map = {
                            'Or': '🥇', 'Double Or': '🥇🥇', 'Triple Or': '🥇🥇🥇',
                            'Platine': '💿', 'Double Platine': '💿💿', 'Triple Platine': '💿💿💿',
                            'Diamant': '💎', 'Double Diamant': '💎💎', 'Triple Diamant': '💎💎💎',
                            'Quadruple Diamant': '💎💎💎💎'
                        }
                        certif_display = emoji_map.get(cert_level, '✓')
                except:
                    pass
                
                # Statut - Utiliser votre fonction existante _get_track_status_icon
                status = self._get_track_status_icon(track)

                # Streams estimés
                try:
                    from src.utils.streams_calculator import (
                        calculate_total_streams, streams_source_label, format_streams)
                    sp = getattr(track, 'spotify_streams', None)
                    yt = getattr(track, 'ytm_streams', None)
                    streams_total = calculate_total_streams(sp, yt)
                    streams_display = format_streams(
                        streams_total, streams_source_label(sp, yt))
                except Exception:
                    streams_display = ""

                # Case à cocher selon la sélection
                checkbox = "☑" if i in self.selected_tracks else "☐"

                # Ajouter la ligne
                item_id = self.tree.insert(
                    "", "end",
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
                        status
                    ),
                    tags=(str(i),)
                )
                
                # Appliquer le style pour les morceaux désactivés
                if is_disabled:
                    self.tree.item(item_id, tags=(str(i), "disabled"))
                    
            except Exception as e:
                logger.error(f"Erreur ajout track idx={i}: {e}")
                # En cas d'erreur, ajouter une ligne minimale
                try:
                    self.tree.insert(
                        "", "end",
                        text="☐",
                        values=(
                            getattr(track, 'title', f"Track {i}"),
                            "", "", "", "0", "", "", "Aucun"  # CORRECTION: "0" pour les crédits
                        ),
                        tags=(str(i),)
                    )
                except:
                    pass
        
        # Style pour morceaux désactivés
        self.tree.tag_configure("disabled", foreground="gray", background="#2a2a2a")
        
        # Rafraîchir l'affichage des sélections
        self._refresh_selection_display()
        self._update_selection_count()
        self._update_buttons_state()

    def _on_tree_click(self, event):
        """Gère les clics sur le tableau avec sélection multiple (Ctrl/Maj)"""
        if getattr(self, 'view_mode', 'tracks') != 'tracks':
            return
        region = self.tree.identify_region(event.x, event.y)

        if region == "tree":  # Clic sur la case à cocher
            item = self.tree.identify_row(event.y)
            if item:
                tags = self.tree.item(item)["tags"]
                if tags:
                    index = int(tags[0])

                    # Vérifier si le morceau est désactivé
                    if self._is_track_disabled_by_index(index):
                        return  # Ignorer le clic sur les morceaux désactivés

                    # Gestion de la sélection multiple
                    ctrl_pressed = event.state & 0x4  # Ctrl key
                    shift_pressed = event.state & 0x1  # Shift key

                    if shift_pressed and self.last_selected_index is not None:
                        # Sélection en plage avec Maj
                        start = min(self.last_selected_index, index)
                        end = max(self.last_selected_index, index)

                        # Sélectionner tous les morceaux dans la plage (sauf désactivés)
                        for i in range(start, end + 1):
                            if not self._is_track_disabled_by_index(i):
                                self.selected_tracks.add(i)
                                # Trouver l'item correspondant et cocher
                                for child in self.tree.get_children():
                                    child_tags = self.tree.item(child)["tags"]
                                    if child_tags and int(child_tags[0]) == i:
                                        self.tree.item(child, text="☑")
                                        break

                    elif ctrl_pressed:
                        # Sélection multiple avec Ctrl (toggle)
                        if index in self.selected_tracks:
                            self.selected_tracks.remove(index)
                            self.tree.item(item, text="☐")
                        else:
                            self.selected_tracks.add(index)
                            self.tree.item(item, text="☑")
                        self.last_selected_index = index

                    else:
                        # Clic simple - toggle
                        if index in self.selected_tracks:
                            self.selected_tracks.remove(index)
                            self.tree.item(item, text="☐")
                            new_state = False
                        else:
                            self.selected_tracks.add(index)
                            self.tree.item(item, text="☑")
                            new_state = True
                        self.last_selected_index = index

                        # Armer le cocher-glisser : maintenir le clic et glisser
                        # applique le même état aux lignes survolées
                        self._drag_check_state = new_state
                        self._drag_check_active = True

                    self._update_selection_count()

    def _on_tree_drag(self, event):
        """Cocher-glisser : applique l'état de la première coche aux lignes survolées"""
        if getattr(self, 'view_mode', 'tracks') != 'tracks':
            return
        if not getattr(self, '_drag_check_active', False):
            return
        if self.tree.identify_region(event.x, event.y) != "tree":
            return
        item = self.tree.identify_row(event.y)
        if not item:
            return
        tags = self.tree.item(item)["tags"]
        if not tags:
            return
        index = int(tags[0])
        if self._is_track_disabled_by_index(index):
            return

        if self._drag_check_state and index not in self.selected_tracks:
            self.selected_tracks.add(index)
            self.tree.item(item, text="☑")
            self._update_selection_count()
        elif not self._drag_check_state and index in self.selected_tracks:
            self.selected_tracks.remove(index)
            self.tree.item(item, text="☐")
            self._update_selection_count()

    def _on_tree_release(self, event):
        """Fin du cocher-glisser"""
        self._drag_check_active = False

    def _delete_track_by_index(self, index):
        """Supprime définitivement un morceau (DB + liste) après confirmation"""
        if not self.current_artist or index >= len(self.current_artist.tracks):
            return
        track = self.current_artist.tracks[index]
        if not messagebox.askyesno(
            "Supprimer le morceau",
            f"Supprimer définitivement '{track.title}' ?\n\n"
            "Le morceau, ses crédits et ses données seront effacés de la base.\n"
            "(Il pourra revenir lors d'une future récupération de discographie "
            "s'il est encore associé à l'artiste sur Genius.)"
        ):
            return
        try:
            if track.id:
                self.data_manager.delete_track(track.id)
            # Mémoriser la suppression (genius_id) pour éviter le réajout au prochain import
            try:
                self.deleted_tracks_manager.add_deleted(
                    self.current_artist.name, getattr(track, 'genius_id', None), track.title
                )
            except Exception as e:
                logger.debug(f"Mémo suppression échec: {e}")
            self.current_artist.tracks.pop(index)
            # Les indices ne sont plus valides
            self.selected_tracks.clear()
            self._populate_tracks_table()
            self._update_artist_info()
            logger.info(f"🗑️ Morceau supprimé: {track.title}")
        except Exception as e:
            logger.error(f"Erreur suppression morceau: {e}")
            self._show_error("Erreur", f"Impossible de supprimer le morceau: {e}")

    def _on_right_click(self, event):
        """Menu contextuel sur clic droit avec actualisation immédiate"""
        if getattr(self, 'view_mode', 'tracks') != 'tracks':
            return
        item = self.tree.identify_row(event.y)
        if item:
            tags = self.tree.item(item)["tags"]
            if tags:
                index = int(tags[0])
                
                # Créer menu contextuel
                context_menu = tkinter.Menu(self.root, tearoff=0)

                # Vérifier l'état actuel du morceau
                is_disabled = self._is_track_disabled_by_index(index)
                
                if is_disabled:
                    context_menu.add_command(
                        label="Réactiver ce morceau",
                        command=lambda: self._enable_track_with_refresh(index, item)
                    )
                else:
                    context_menu.add_command(
                        label="Désactiver ce morceau",
                        command=lambda: self._disable_track_with_refresh(index, item)
                    )
                
                context_menu.add_separator()
                context_menu.add_command(
                    label="Voir les détails",
                    command=lambda: self._show_track_details_by_index(index)
                )
                context_menu.add_separator()
                context_menu.add_command(
                    label="🗑️ Supprimer définitivement",
                    command=lambda: self._delete_track_by_index(index)
                )
                
                # Afficher le menu
                try:
                    context_menu.tk_popup(event.x_root, event.y_root)
                finally:
                    context_menu.grab_release()

    def _disable_track_with_refresh(self, index: int, item):
        """Désactive un morceau et actualise immédiatement l'affichage"""
        # Convertir l'index en track ID et ajouter
        track_id = self._get_track_id_from_index(index)
        if track_id is not None:
            self.disabled_tracks.add(track_id)
        if index in self.selected_tracks:
            self.selected_tracks.remove(index)
        
        # Récupérer les valeurs actuelles de l'item
        current_values = list(self.tree.item(item)["values"])
        
        # Mettre à jour le statut (dernière colonne)
        if len(current_values) >= 8:
            current_values[7] = "Désactivé"
        
        # Actualiser immédiatement l'affichage de cet item
        self.tree.item(item, text="⊘", values=current_values, tags=(str(index), "disabled"))
        self.tree.tag_configure("disabled", foreground="gray", background="#2a2a2a")
        
        # Sauvegarder
        if self.current_artist:
            self.disabled_tracks_manager.save_disabled_tracks(
                self.current_artist.name, 
                self.disabled_tracks
            )
        
        self._update_selection_count()
        logger.info(f"Morceau désactivé: index {index}")

    def _enable_track_with_refresh(self, index: int, item):
        """Réactive un morceau et actualise immédiatement l'affichage"""
        # Convertir l'index en track ID et retirer
        track_id = self._get_track_id_from_index(index)
        if track_id is not None and track_id in self.disabled_tracks:
            self.disabled_tracks.remove(track_id)
        
        # Récupérer les valeurs actuelles de l'item
        current_values = list(self.tree.item(item)["values"])
        
        # Mettre à jour le statut (dernière colonne)
        if len(current_values) >= 8:
            current_values[7] = "Actif"
        
        # Actualiser immédiatement l'affichage de cet item
        self.tree.item(item, text="☐", values=current_values, tags=(str(index),))
        
        # Sauvegarder
        if self.current_artist:
            self.disabled_tracks_manager.save_disabled_tracks(
                self.current_artist.name, 
                self.disabled_tracks
            )
        
        self._update_selection_count()
        logger.info(f"Morceau réactivé: index {index}")

    def _disable_selected_tracks(self):
        """Désactive les morceaux sélectionnés"""
        if not self.selected_tracks:
            messagebox.showwarning("Aucune sélection", "Veuillez sélectionner des morceaux à désactiver")
            return

        try:
            # Convertir les indices sélectionnés en IDs de tracks
            track_ids_to_disable = set()
            for index in self.selected_tracks:
                track_id = self._get_track_id_from_index(index)
                if track_id is not None:
                    track_ids_to_disable.add(track_id)

            # Ajouter aux morceaux désactivés (utiliser IDs)
            self.disabled_tracks.update(track_ids_to_disable)

            # Sauvegarder
            if self.current_artist:
                self.disabled_tracks_manager.save_disabled_tracks(
                    self.current_artist.name,
                    self.disabled_tracks
                )

            # Vider la sélection
            self.selected_tracks.clear()

            # Rafraîchir l'affichage
            self._populate_tracks_table()

            logger.info(f"Morceaux désactivés: {len(self.disabled_tracks)} au total")

        except Exception as e:
            logger.error(f"Erreur lors de la désactivation: {e}")
            self._show_error("Erreur", f"Impossible de désactiver les morceaux: {e}")

    def _enable_selected_tracks(self):
        """Réactive TOUS les morceaux désactivés"""
        if not self.disabled_tracks:
            messagebox.showinfo("Info", "Aucun morceau désactivé")
            return
        
        try:
            count = len(self.disabled_tracks)
            
            # Vider complètement les morceaux désactivés
            self.disabled_tracks.clear()
            
            # Sauvegarder l'état vide
            if self.current_artist:
                self.disabled_tracks_manager.save_disabled_tracks(
                    self.current_artist.name, 
                    self.disabled_tracks
                )
            
            # Rafraîchir l'affichage
            self._populate_tracks_table()
            
            messagebox.showinfo("Succès", f"{count} morceau(x) réactivé(s)")
            logger.info(f"Tous les morceaux ont été réactivés ({count})")
            
        except Exception as e:
            logger.error(f"Erreur lors de la réactivation: {e}")
            self._show_error("Erreur", f"Impossible de réactiver les morceaux: {e}")

    def _sort_column(self, col):
        """Trie les morceaux par colonne - VERSION SANS SÉLECTION AUTOMATIQUE"""
        if getattr(self, 'view_mode', 'tracks') != 'tracks':
            return
        if not self.current_artist or not self.current_artist.tracks:
            return
        
        try:
            # Déterminer l'ordre de tri
            reverse = False
            if self.sort_column == col:
                reverse = not self.sort_reverse
            
            # Définir la fonction de tri
            sort_key = None
            if col == "Titre":
                sort_key = lambda t: t.title.lower()
            elif col == "Album":
                sort_key = lambda t: (t.album or "").lower()
            elif col == "Artiste principal":
                sort_key = lambda t: getattr(t, 'primary_artist_name', '') or t.artist.name if t.artist else ""
            elif col == "Date sortie":
                # CORRECTION: Gérer datetime ET string
                def get_release_date(t):
                    if not hasattr(t, 'release_date') or not t.release_date:
                        return datetime.min
                    if isinstance(t.release_date, str):
                        try:
                            return datetime.fromisoformat(t.release_date.replace('Z', '+00:00').split('T')[0])
                        except:
                            return datetime.min
                    return t.release_date
                sort_key = get_release_date
            elif col == "Crédits":
                # CORRECTION: Trier par nombre de crédits
                sort_key = lambda t: len(getattr(t, 'credits', []))
            elif col == "Paroles":
                sort_key = lambda t: getattr(t, 'has_lyrics', False)
            elif col == "BPM":
                sort_key = lambda t: getattr(t, 'bpm', 0) or 0
            elif col == "Durée":
                # Trier par durée en secondes
                def get_duration_seconds(t):
                    if not hasattr(t, 'duration') or not t.duration:
                        return 0
                    if isinstance(t.duration, int):
                        return t.duration
                    if isinstance(t.duration, str):
                        try:
                            parts = t.duration.split(':')
                            if len(parts) == 2:
                                return int(parts[0]) * 60 + int(parts[1])
                            elif len(parts) == 3:
                                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                        except:
                            pass
                    return 0
                sort_key = get_duration_seconds
            elif col == "Certif.":
                # CORRECTION: Définir la fonction ET l'utiliser
                cert_order = {
                    '💎💎💎💎': 1, '💎💎💎': 2, '💎💎': 3, '💎': 4,
                    '💿💿💿': 5, '💿💿': 6, '💿': 7,
                    '🥇🥇🥇': 8, '🥇🥇': 9, '🥇': 10,
                    '✓': 11, '': 12
                }
                def get_cert_value(t):
                    try:
                        if hasattr(t, 'certifications') and t.certifications:
                            cert_level = t.certifications[0].get('certification', '')
                            emoji_map = {
                                'Quadruple Diamant': '💎💎💎💎', 'Triple Diamant': '💎💎💎',
                                'Double Diamant': '💎💎', 'Diamant': '💎',
                                'Triple Platine': '💿💿💿', 'Double Platine': '💿💿', 'Platine': '💿',
                                'Triple Or': '🥇🥇🥇', 'Double Or': '🥇🥇', 'Or': '🥇'
                            }
                            emoji = emoji_map.get(cert_level, '✓')
                            return cert_order.get(emoji, 12)
                        return 12
                    except:
                        return 12
                sort_key = get_cert_value
            elif col == "Statut":
                # CORRECTION: Trier par ordre de priorité (Complet > Incomplet > Désactivé)
                status_order = {
                    '✅': 1,  # Complet en premier
                    '⚠️': 2,  # Incomplet au milieu
                    '❌': 3   # Désactivé en dernier
                }
                def get_status_value(t):
                    icon = self._get_track_status_icon(t)
                    return status_order.get(icon, 4)  # 4 pour les icônes inconnues
                sort_key = get_status_value
            
            if sort_key:
                # Les morceaux désactivés sont maintenant stockés par ID, pas par index
                # donc ils restent valides même après le tri

                # Trier les morceaux
                self.current_artist.tracks.sort(key=sort_key, reverse=reverse)

                # Vider les sélections (les indices ne sont plus valides après le tri)
                self.selected_tracks.clear()

                # Les disabled_tracks utilisent maintenant des IDs de tracks
                # donc pas besoin de les restaurer - ils restent valides après le tri
                # Aucun besoin de sauvegarder car les IDs n'ont pas changé
            
            # Mettre à jour les variables de tri
            self.sort_column = col
            self.sort_reverse = reverse
            
            # Recréer l'affichage
            self._populate_tracks_table()
            
            # Mettre à jour l'indicateur de tri dans l'en-tête
            for column in self.tree["columns"]:
                if column == col:
                    indicator = " ▲" if not reverse else " ▼"
                    self.tree.heading(column, text=column + indicator)
                else:
                    self.tree.heading(column, text=column)
                    
        except Exception as e:
            logger.error(f"Erreur lors du tri: {e}")
            self._show_error("Erreur de tri", str(e))

    def _show_track_details_by_index(self, index: int):
        """Affiche les détails d'un morceau par son index - ✅ NOUVEAU"""
        if 0 <= index < len(self.current_artist.tracks):
            track = self.current_artist.tracks[index]
            self._show_track_details_for_track(track)

    def _show_track_details(self, event):
        """Affiche les détails d'un morceau - VERSION CORRIGÉE AVEC DEBUG FEATURING"""
        if getattr(self, 'view_mode', 'tracks') != 'tracks':
            return
        selection = self.tree.selection()
        if not selection:
            return
        
        item = selection[0]
        tags = self.tree.item(item)["tags"]
        if not tags:
            return
            
        track_index = int(tags[0])
        
        if 0 <= track_index < len(self.current_artist.tracks):
            track = self.current_artist.tracks[track_index]
            self._show_track_details_for_track(track)

    def _show_track_details_for_track(self, track: Track):
        """Affiche les détails d'un morceau - VERSION AVEC DEBUG FEATURING"""
        # S'assurer que track.artist existe
        if not track.artist:
            track.artist = self.current_artist
            logger.warning(f"⚠️ track.artist était None pour '{track.title}', réparé")

        # Si une fenêtre de détails est déjà ouverte pour ce track, la fermer d'abord
        if track.id in self.open_detail_windows:
            old_window, _ = self.open_detail_windows[track.id]
            try:
                old_window.destroy()
            except:
                pass
            del self.open_detail_windows[track.id]

        # Créer une fenêtre de détails
        details_window = ctk.CTkToplevel(self.root)
        details_window.title(f"Détails - {track.title}")
        details_window.transient(self.root)

        # Stocker la référence de la fenêtre
        self.open_detail_windows[track.id] = (details_window, track)

        # Nettoyer la référence quand la fenêtre est fermée
        def on_close():
            if track.id in self.open_detail_windows:
                del self.open_detail_windows[track.id]
            details_window.destroy()

        details_window.protocol("WM_DELETE_WINDOW", on_close)
        
        # Agrandir la fenêtre selon le contenu
        has_lyrics = hasattr(track, 'lyrics') and track.lyrics
        window_height = "900" if has_lyrics else "750"
        details_window.geometry(f"900x{window_height}")
        
        # === SECTION INFORMATIONS GÉNÉRALES (EN HAUT) ===
        info_frame = ctk.CTkFrame(details_window)
        info_frame.pack(fill="x", padx=10, pady=10)
        
        # Titre principal
        ctk.CTkLabel(info_frame, text=f"🎵 {track.title}", 
                    font=("Arial", 16, "bold")).pack(anchor="w", padx=10, pady=(10, 5))
        
        # Informations de base sur deux colonnes
        basic_info_frame = ctk.CTkFrame(info_frame)
        basic_info_frame.pack(fill="x", padx=10, pady=5)
        
        # Colonne gauche
        left_column = ctk.CTkFrame(basic_info_frame, fg_color="transparent")
        left_column.pack(side="left", fill="both", expand=True, padx=(5, 10))
        
        # Gestion des features
        is_featuring = getattr(track, 'is_featuring', False)
        primary_artist = getattr(track, 'primary_artist_name', None)
        featured_artists = getattr(track, 'featured_artists', None)
        secondary_role = getattr(track, 'secondary_role', None)

        if secondary_role:
            ctk.CTkLabel(left_column, text=f"🎙️ RÔLE SECONDAIRE — {secondary_role}",
                        font=("Arial", 12, "bold"), text_color="#C58AF0").pack(anchor="w", pady=2)
            if primary_artist:
                ctk.CTkLabel(left_column, text=f"Artiste principal: {primary_artist}").pack(anchor="w", pady=1)
            who = track.artist.name if track.artist else self.current_artist.name
            ctk.CTkLabel(left_column, text=f"Contribution de: {who}").pack(anchor="w", pady=1)
        elif is_featuring:
            ctk.CTkLabel(left_column, text="🎤 MORCEAU EN FEATURING",
                        font=("Arial", 12, "bold"), text_color="orange").pack(anchor="w", pady=2)
            if primary_artist:
                ctk.CTkLabel(left_column, text=f"Artiste principal: {primary_artist}").pack(anchor="w", pady=1)
            featuring_name = featured_artists or (track.artist.name if track.artist else self.current_artist.name)
            ctk.CTkLabel(left_column, text=f"En featuring: {featuring_name}").pack(anchor="w", pady=1)
        else:
            artist_name = track.artist.name if track.artist else 'Artiste inconnu'
            ctk.CTkLabel(left_column, text="🎵 MORCEAU PRINCIPAL", 
                        font=("Arial", 12, "bold"), text_color="green").pack(anchor="w", pady=2)
            ctk.CTkLabel(left_column, text=f"Artiste: {artist_name}").pack(anchor="w", pady=1)
        
        # Album et numéro de piste
        if track.album:
            album_text = f"Album: {track.album}"
            if hasattr(track, 'track_number') and track.track_number:
                album_text += f" (Piste {track.track_number})"
            ctk.CTkLabel(left_column, text=album_text).pack(anchor="w", pady=1)
        
        # Colonne droite
        right_column = ctk.CTkFrame(basic_info_frame, fg_color="transparent")
        right_column.pack(side="right", fill="both", expand=True, padx=(10, 5))
        
        # Date, BPM, durée
        if track.release_date:
            date_str = self._format_date(track.release_date)
            ctk.CTkLabel(right_column, text=f"📅 Date: {date_str}").pack(anchor="w", pady=1)
        
        if track.bpm:
            bpm_text = f"🎼 BPM: {track.bpm}"
            
            # ⭐ LOGIQUE AMÉLIORÉE pour afficher la tonalité
            musical_key = None
            
            # 1. Ajouter la tonalité si disponible directement
            if hasattr(track, 'musical_key') and track.musical_key:
                musical_key = track.musical_key
            
            # 2. FALLBACK : Si musical_key n'existe pas mais key et mode existent
            elif hasattr(track, 'key') and hasattr(track, 'mode') and track.key and track.mode:
                try:
                    from src.utils.music_theory import key_mode_to_french_from_string
                    musical_key = key_mode_to_french_from_string(track.key, track.mode)
                    
                    # ⭐ BONUS : Stocker le résultat calculé pour éviter de recalculer
                    track.musical_key = musical_key
                    logger.debug(f"Musical key calculée et stockée pour '{track.title}': {musical_key}")
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
                    if ':' in track.duration:
                        parts = track.duration.split(':')
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
                    ctk.CTkLabel(right_column, text=f"⏱️ Durée: {minutes}:{seconds:02d}").pack(anchor="w", pady=1)
            except (ValueError, TypeError, AttributeError) as e:
                logger.warning(f"Erreur affichage durée pour '{track.title}': {e}")
                # Ne pas crasher, juste skip l'affichage de la durée
        
        if track.genre:
            ctk.CTkLabel(right_column, text=f"🎭 Genre: {track.genre}").pack(anchor="w", pady=1)

        # Streams estimés dans le header
        try:
            from src.utils.streams_calculator import (
                calculate_total_streams, streams_source_label, format_streams)
            sp = getattr(track, 'spotify_streams', None)
            yt = getattr(track, 'ytm_streams', None)
            total_est = calculate_total_streams(sp, yt)
            if total_est:
                suffix = streams_source_label(sp, yt)
                label_text = f"📊 Streams : {format_streams(total_est)}{suffix} (estimé)"
                ctk.CTkLabel(
                    right_column, text=label_text,
                    font=ctk.CTkFont(size=12, weight="bold"),
                    text_color="#4fc3f7"
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
                cursor="hand2"
            )
            genius_label.pack(side="left")
            
            import webbrowser
            genius_label.bind("<Button-1>", lambda e: webbrowser.open(track.genius_url))
            
        # URL Spotify - GESTION DE MULTIPLES IDs
        if hasattr(track, 'get_all_spotify_ids'):
            all_spotify_ids = track.get_all_spotify_ids()
        elif hasattr(track, 'spotify_ids') and track.spotify_ids:
            all_spotify_ids = track.spotify_ids
        elif hasattr(track, 'spotify_id') and track.spotify_id:
            all_spotify_ids = [track.spotify_id]
        else:
            all_spotify_ids = []

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
                    width=12
                )
                version_combo.pack(side="left", padx=5)
                
                def open_selected_version():
                    idx = version_combo.current()
                    if 0 <= idx < len(all_spotify_ids):
                        spotify_url = f"https://open.spotify.com/intl-fr/track/{all_spotify_ids[idx]}"
                        import webbrowser
                        webbrowser.open(spotify_url)
                
                spotify_label = ctk.CTkLabel(
                    spotify_frame, 
                    text="▶️ Écouter",
                    text_color="#1DB954",
                    cursor="hand2"
                )
                spotify_label.pack(side="left")
                spotify_label.bind("<Button-1>", lambda e: open_selected_version())

                # Info tooltip
                info_label = ctk.CTkLabel(
                    spotify_frame,
                    text=f"({len(all_spotify_ids)} versions)",
                    font=("Arial", 9),
                    text_color="gray"
                )
                info_label.pack(side="left", padx=5)

                # Tooltip avec le titre de la page Spotify si disponible
                if hasattr(track, 'spotify_page_title') and track.spotify_page_title:
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
                            font=("Arial", 9)
                        ).pack(padx=5, pady=2)

                        tooltip.after(3000, tooltip.destroy)

                    spotify_label.bind("<Enter>", show_spotify_tooltip)
            else:
                # Un seul ID, affichage normal
                spotify_url = f"https://open.spotify.com/intl-fr/track/{all_spotify_ids[0]}"

                spotify_label = ctk.CTkLabel(
                    spotify_frame,
                    text="▶️ Écouter",
                    text_color="#1DB954",
                    cursor="hand2"
                )
                spotify_label.pack(side="left")

                import webbrowser
                spotify_label.bind("<Button-1>", lambda e: webbrowser.open(spotify_url))

                # Tooltip avec le titre de la page Spotify si disponible
                if hasattr(track, 'spotify_page_title') and track.spotify_page_title:
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
                            font=("Arial", 9)
                        ).pack(padx=5, pady=2)

                        tooltip.after(3000, tooltip.destroy)

                    spotify_label.bind("<Enter>", show_spotify_tooltip)

        # YouTube intelligent - ROUGE
        youtube_frame = ctk.CTkFrame(urls_frame, fg_color="transparent")
        youtube_frame.pack(side="left")

        ctk.CTkLabel(youtube_frame, text="📺 YouTube: ").pack(side="left")

        # Obtenir le lien YouTube intelligent
        artist_name = track.artist.name if track.artist else self.current_artist.name
        release_year = self.get_release_year_safely(track)

        # Priorité au lien YouTube fourni par Genius (media), recherche en fallback
        _genius_yt = getattr(track, 'youtube_url', None)
        if _genius_yt:
            youtube_result = {
                'type': 'direct',
                'url': _genius_yt,
                'confidence': 1.0,
                'title': track.title,
                'channel': 'Genius (media)',
                'source': 'genius_media',
            }
        else:
            youtube_result = youtube_integration.get_youtube_link_for_track(
                artist_name, track.title, track.album, release_year
            )

        # Affichage selon le type de résultat
        if youtube_result.get('source') == 'genius_media':
            # Lien fourni par Genius (media) — prioritaire, distinct du fallback recherche
            label_text = "▶️ Voir (Genius ✓)"
            label_color = "#1DB954"  # Vert = source fiable Genius
            tooltip_text = (f"Lien YouTube fourni par Genius (media)\n"
                            f"Titre: {youtube_result.get('title', 'N/A')}\n"
                            f"URL: {youtube_result.get('url', 'N/A')}")
        elif youtube_result['type'] == 'direct':
            # Lien direct trouvé automatiquement (recherche)
            label_text = f"▶️ Voir (auto • {youtube_result['confidence']:.0%})"
            label_color = "#FF0000"  # Rouge YouTube
            tooltip_text = (f"Lien automatique sélectionné (recherche)\n"
                            f"Titre: {youtube_result.get('title', 'N/A')}\n"
                            f"Chaîne: {youtube_result.get('channel', 'N/A')}\n"
                            f"Confiance: {youtube_result['confidence']:.1%}")
        else:
            # URL de recherche optimisée
            label_text = "🔍 Rechercher"
            label_color = "#FF6B6B"  # Rouge plus clair pour différencier
            tooltip_text = (f"Recherche optimisée\n"
                            f"Type: {youtube_result.get('track_type', 'inconnu')}\n"
                            f"Requête: {youtube_result.get('query', 'N/A')}")

        youtube_label = ctk.CTkLabel(
            youtube_frame,
            text=label_text,
            text_color=label_color,
            cursor="hand2"
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
                tooltip, 
                text=tooltip_text,
                fg_color="gray20",
                corner_radius=5,
                font=("Arial", 9)
            ).pack(padx=5, pady=2)
            
            # Détruire après 3 secondes
            tooltip.after(3000, tooltip.destroy)
        
        youtube_label.bind("<Enter>", show_tooltip)
        
        # === SYSTÈME D'ONGLETS ===
        notebook = tkinter_ttv.Notebook(details_window)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)
        
        # === ONGLET 1: CRÉDITS MUSICAUX ===
        music_credits_frame = ctk.CTkFrame(notebook)
        notebook.add(music_credits_frame, text=f"🎵 Crédits musicaux")
        
        music_credits = track.get_music_credits()
        
        # En-tête avec statistiques
        music_header = ctk.CTkFrame(music_credits_frame)
        music_header.pack(fill="x", padx=10, pady=10)
        
        ctk.CTkLabel(music_header, 
                    text=f"🎵 Crédits musicaux ({len(music_credits)})", 
                    font=("Arial", 14, "bold")).pack(anchor="w", padx=5, pady=5)
        
        if track.has_complete_credits():
            status_color = "green"
            status_text = "✅ Crédits complets"
        elif music_credits:
            status_color = "orange"
            status_text = "⚠️ Crédits partiels"
        else:
            status_color = "red"
            status_text = "❌ Aucun crédit"
        
        ctk.CTkLabel(music_header, text=status_text, text_color=status_color).pack(anchor="w", padx=5)
        
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
                    source_emoji = {"genius": "🎤", "spotify": "🎧", "discogs": "💿", "lastfm": "📻"}.get(credit.source, "🔗")
                    detail = f" ({credit.role_detail})" if credit.role_detail else ""
                    music_textbox.insert("end", f"{source_emoji} {credit.name}{detail}\n")
        else:
            music_textbox.insert("end", "❌ Aucun crédit musical trouvé.\n\n")
            music_textbox.insert("end", "💡 Utilisez le bouton 'Scraper les crédits' pour récupérer les informations depuis Genius.")
        
        music_textbox.configure(state="disabled")
        
        # === ONGLET 2: CRÉDITS VIDÉO ===
        video_credits = track.get_video_credits()
        
        if video_credits:
            video_credits_frame = ctk.CTkFrame(notebook)
            notebook.add(video_credits_frame, text=f"🎬 Crédits vidéo ({len(video_credits)})")
            
            # En-tête vidéo
            video_header = ctk.CTkFrame(video_credits_frame)
            video_header.pack(fill="x", padx=10, pady=10)
            
            ctk.CTkLabel(video_header, 
                        text=f"🎬 Crédits vidéo ({len(video_credits)})", 
                        font=("Arial", 14, "bold")).pack(anchor="w", padx=5, pady=5)
            
            ctk.CTkLabel(video_header, 
                        text="Équipe technique du clip vidéo", 
                        text_color="gray").pack(anchor="w", padx=5)
            
            # Zone de crédits vidéo
            video_textbox = ctk.CTkTextbox(video_credits_frame, width=850, height=450)
            video_textbox.pack(fill="both", expand=True, padx=10, pady=10)
            
            video_credits_by_role = defaultdict(list)
            for credit in video_credits:
                video_credits_by_role[credit.role.value].append(credit)
            
            for role, credits in sorted(video_credits_by_role.items()):
                video_textbox.insert("end", f"\n━━━ {role} ━━━\n", "bold")
                for credit in credits:
                    source_emoji = {"genius": "🎤", "spotify": "🎧", "discogs": "💿", "lastfm": "📻"}.get(credit.source, "🔗")
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
            font=("Arial", 14, "bold")
        ).pack(anchor="w", padx=10, pady=(8, 4))
        _rels = getattr(track, 'relationships', None) or []
        if not _rels:
            ctk.CTkLabel(
                samples_scroll,
                text="Aucune relation détectée.\n(Re-fetch la discographie pour les primaires, ré-enrichis le feat.)",
                text_color="gray", justify="left"
            ).pack(anchor="w", padx=14, pady=8)
        else:
            import webbrowser
            from collections import defaultdict as _dd
            _type_label = {
                'samples': '🎵 Sample de', 'interpolates': '🎼 Interpole',
                'cover_of': '🎤 Reprise de', 'remix_of': '🔁 Remix de',
                'translation_fr': '🇫🇷 Traduction FR',
            }
            _grouped = _dd(list)
            for _r in _rels:
                _grouped[_r.get('type')].append(_r)
            for _typ in ['samples', 'interpolates', 'cover_of', 'remix_of', 'translation_fr']:
                _items = _grouped.get(_typ) or []
                if not _items:
                    continue
                ctk.CTkLabel(samples_scroll, text=f"━━━ {_type_label.get(_typ, _typ)} ━━━",
                             font=("Arial", 12, "bold")).pack(anchor="w", padx=10, pady=(8, 2))
                for _r in _items:
                    _txt = f"   • {_r.get('title') or '?'} — {_r.get('artist') or '?'}"
                    _url = _r.get('url')
                    _lbl = ctk.CTkLabel(samples_scroll, text=_txt,
                                        text_color=("#1f6aa5", "#4aa3df") if _url else ("gray20", "gray70"),
                                        cursor="hand2" if _url else "arrow", anchor="w", justify="left")
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
                details_window.clipboard_append(track.lyrics)
                messagebox.showinfo("Copié", "Paroles copiées dans le presse-papier")

            # Section Anecdotes EN PREMIER si disponibles
            if hasattr(track, 'anecdotes') and track.anecdotes:
                # Header anecdotes
                anecdotes_header = ctk.CTkFrame(lyrics_scrollable)
                anecdotes_header.pack(fill="x", padx=10, pady=(10, 5))

                ctk.CTkLabel(anecdotes_header,
                            text="💡 Anecdotes & Informations",
                            font=("Arial", 14, "bold")).pack(anchor="w", padx=5, pady=5)

                # Zone de texte pour les anecdotes
                anecdotes_textbox = ctk.CTkTextbox(
                    lyrics_scrollable,
                    width=820,
                    height=60,  # Hauteur divisée par 2
                    font=("Arial", 11),
                    wrap="word"
                )
                anecdotes_textbox.pack(fill="x", padx=10, pady=5)
                anecdotes_textbox.insert("0.0", track.anecdotes)
                anecdotes_textbox.configure(state="disabled")

                # Séparateur après les anecdotes
                ctk.CTkFrame(lyrics_scrollable, height=2, fg_color="gray").pack(fill="x", padx=10, pady=10)

            # Header "Paroles complètes" avec stats et bouton Copier (APRÈS le séparateur)
            words_count = len(track.lyrics.split()) if track.lyrics else 0
            chars_count = len(track.lyrics) if track.lyrics else 0

            lyrics_header = ctk.CTkFrame(lyrics_scrollable)
            lyrics_header.pack(fill="x", padx=10, pady=(5, 10))

            # Partie gauche : titre et stats
            left_part = ctk.CTkFrame(lyrics_header, fg_color="transparent")
            left_part.pack(side="left", fill="x", expand=True)

            ctk.CTkLabel(left_part,
                        text=f"📝 Paroles complètes",
                        font=("Arial", 14, "bold")).pack(anchor="w", padx=5, pady=(0, 2))

            info_text = f"📊 {words_count} mots • {chars_count} caractères"
            if getattr(track, 'lyrics_source', None):
                info_text += f" • {track.lyrics_source}"
            if getattr(track, 'lyrics_synced', None):
                info_text += " • ⏱ synchronisé"
            if hasattr(track, 'lyrics_scraped_at') and track.lyrics_scraped_at:
                date_str = self._format_datetime(track.lyrics_scraped_at)
                info_text += f" • Récupérées le {date_str}"

            ctk.CTkLabel(left_part, text=info_text, text_color="gray", font=("Arial", 9)).pack(anchor="w", padx=5)

            # Bouton Copier à droite
            ctk.CTkButton(lyrics_header, text="📋 Copier", command=copy_lyrics, width=80, height=32).pack(side="right", padx=5)

            # Zone de texte pour les paroles (nettoyées sans anecdote)
            lyrics_textbox = ctk.CTkTextbox(
                lyrics_scrollable,
                width=820,
                height=350,  # Hauteur encore plus réduite
                font=("Consolas", 11)
            )
            lyrics_textbox.pack(fill="x", padx=10, pady=10)

            # Nettoyer les paroles de l'anecdote si elle existe
            clean_lyrics = track.lyrics
            if hasattr(track, 'anecdotes') and track.anecdotes:
                # Méthode robuste : retirer tout le texte jusqu'au premier tag [Couplet], [Partie], etc.
                import re
                # Chercher le premier tag de structure de paroles
                first_tag_match = re.search(r'\[(?:Intro|Couplet|Refrain|Verse|Chorus|Bridge|Hook|Pre-Chorus|Partie|Part|Outro|Interlude)', clean_lyrics, re.IGNORECASE)

                if first_tag_match:
                    # Commencer à partir du premier tag
                    clean_lyrics = clean_lyrics[first_tag_match.start():].strip()
                    logger.debug("Anecdote retirée des paroles (méthode tag)")
                else:
                    # Fallback : retirer les X premiers caractères si l'anecdote est au début
                    anecdote_length = len(track.anecdotes)
                    if clean_lyrics[:200].strip().startswith(track.anecdotes[:100].strip()):
                        # Chercher le prochain double saut de ligne après l'anecdote
                        cut_point = clean_lyrics.find('\n\n', anecdote_length - 50)
                        if cut_point > 0:
                            clean_lyrics = clean_lyrics[cut_point + 2:].strip()
                            logger.debug("Anecdote retirée des paroles (méthode longueur)")

            # Timestamps par section (si synchro YTM dispo) : injectés dans les en-têtes
            if getattr(track, 'lyrics_synced', None):
                try:
                    from src.utils.lyrics_sync import annotate_sections
                    clean_lyrics = annotate_sections(clean_lyrics, track.lyrics_synced)
                except Exception as e:
                    logger.debug(f"Annotation timestamps échouée: {e}")

            formatted_lyrics = self._format_lyrics_for_display(clean_lyrics)
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
                text_color="gray"
            ).pack(expand=True, pady=(0, 10))
            
            ctk.CTkLabel(
                empty_lyrics_container, 
                text="Utilisez le bouton 'Scraper paroles' dans l'interface principale\npour récupérer les paroles de ce morceau",
                font=("Arial", 12),
                text_color="gray",
                justify="center"
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
            if hasattr(track, 'spotify_page_title') and track.spotify_page_title:
                # Limiter à 50 premiers caractères pour l'affichage
                display_title = track.spotify_page_title[:50]
                if len(track.spotify_page_title) > 50:
                    display_title += "..."
                tech_textbox.insert("end", f"   📄 Titre: {display_title}\n")
        if track.discogs_id:
            tech_textbox.insert("end", f"💿 Discogs ID: {track.discogs_id}\n")
        if getattr(track, 'isrc', None):
            tech_textbox.insert("end", f"🆔 ISRC: {track.isrc}\n")

        # Popularité
        if hasattr(track, 'popularity') and track.popularity:
            tech_textbox.insert("end", f"📈 Popularité: {track.popularity}\n")

        # Artwork
        if getattr(track, 'artwork_url', None):
            tech_textbox.insert("end", f"🖼️ Artwork: {track.artwork_url}\n")

        # ── PROVENANCE DES MÉTADONNÉES ──────────────────────────────
        def _yn(v):
            return "✅" if v else "❌"
        tech_textbox.insert("end", "\n🧭 PROVENANCE\n")
        _album_api = getattr(track, '_album_from_api', None)
        _album_src = "API Genius" if _album_api else ("scrape" if track.album else "—")
        tech_textbox.insert("end", f"• Album : {track.album or 'N/A'}  ({_album_src})\n")
        _rd_api = getattr(track, '_release_date_from_api', None)
        _rd_src = "API Genius" if _rd_api else ("scrape" if track.release_date else "—")
        tech_textbox.insert("end", f"• Date de sortie : {track.release_date or 'N/A'}  ({_rd_src})\n")
        _sp_src = "Genius media" if getattr(track, '_spotify_from_api', None) else (
            "scrape Spotify" if getattr(track, 'spotify_page_title', None) else (
                "—" if not track.spotify_id else "Genius media?"))
        tech_textbox.insert("end", f"• Spotify ID : {_yn(track.spotify_id)}  ({_sp_src})\n")
        # youtube_url n'est écrit que par Genius media ; la recherche reste un fallback live (non persisté)
        _yt_src = "Genius media" if getattr(track, 'youtube_url', None) else "recherche live (fallback)"
        tech_textbox.insert("end", f"• YouTube : {_yn(getattr(track, 'youtube_url', None))}  ({_yt_src})\n")
        _isrc_src = getattr(track, '_isrc_source', None) or ("Deezer/ReccoBeats" if getattr(track, 'isrc', None) else "—")
        tech_textbox.insert("end", f"• ISRC : {_yn(getattr(track, 'isrc', None))}  ({_isrc_src})\n")

        # ── PAROLES ─────────────────────────────────────────────────
        _ly = track.lyrics or ""
        _has_struct = any(ln.lstrip().startswith('[') for ln in _ly.splitlines())
        _has_ts = bool(getattr(track, 'lyrics_synced', None))
        _ly_src = getattr(track, 'lyrics_source', None) or "—"
        tech_textbox.insert("end", "\n📝 PAROLES\n")
        tech_textbox.insert("end", f"• Texte présent : {_yn(_ly)}  (source : {_ly_src})\n")
        tech_textbox.insert("end", f"• Structure Genius [Couplet/Refrain] : {_yn(_has_struct)}\n")
        tech_textbox.insert("end", f"• Timestamps YTM : {_yn(_has_ts)}\n")

        # ── DONNÉES AUDIO (BPM / KEY / MODE) ────────────────────────
        tech_textbox.insert("end", "\n🎛️ DONNÉES AUDIO\n")
        _bpm = getattr(track, 'bpm', None)
        _bpm_alt = getattr(track, 'bpm_alt', None)
        _bpm_src = getattr(track, 'bpm_source', None) or "—"
        _bpm_conf = getattr(track, 'bpm_confidence', None)
        tech_textbox.insert("end", f"• Provenance BPM : {_bpm_src}\n")
        _conf_txt = f"{_bpm_conf}" if _bpm_conf is not None else "—"
        tech_textbox.insert("end", f"• Arbitrage (vote) : confiance {_conf_txt}\n")
        if _bpm:
            _alt_txt = f" • alt half/double-time : {_bpm_alt}" if _bpm_alt else " • pas d'octave alternative"
            tech_textbox.insert("end", f"• BPM réel : {_bpm}{_alt_txt}\n")
        else:
            tech_textbox.insert("end", "• BPM réel : N/A\n")
        _km_src = getattr(track, 'key_mode_source', None) or "—"
        tech_textbox.insert("end", f"• Source Key/Mode : {_km_src}\n")
        _rb_res = getattr(track, 'reccobeats_resolution', None) or "—"
        tech_textbox.insert("end", f"• Résolution ReccoBeats : {_rb_res}\n")

        # ── STREAMS & DURÉE ─────────────────────────────────────────
        tech_textbox.insert("end", "\n📦 STREAMS & DURÉE\n")
        _sp_streams = getattr(track, 'spotify_streams', None)
        if _sp_streams is not None:
            _sp_upd = getattr(track, 'spotify_streams_updated', None)
            _sp_when = f"  (maj {self._format_datetime(_sp_upd)})" if _sp_upd else ""
            _n = f"{_sp_streams:,}".replace(",", " ")
            tech_textbox.insert("end", f"• Spotify : {_n}{_sp_when}\n")
        else:
            tech_textbox.insert("end", "• Spotify : —\n")
        _ytm_streams = getattr(track, 'ytm_streams', None)
        if _ytm_streams is not None:
            _ytm_upd = getattr(track, 'ytm_streams_updated', None)
            _ytm_when = f"  (maj {self._format_datetime(_ytm_upd)})" if _ytm_upd else ""
            _n = f"{_ytm_streams:,}".replace(",", " ")
            tech_textbox.insert("end", f"• YouTube Music : {_n}{_ytm_when}\n")
        else:
            tech_textbox.insert("end", "• YouTube Music : —\n")
        _dur = getattr(track, 'duration', None)
        if _dur:
            tech_textbox.insert("end", f"• Durée : {int(_dur)//60}:{int(_dur)%60:02d}  ({int(_dur)}s)\n")
        else:
            tech_textbox.insert("end", "• Durée : —\n")

        # ── COMPLÉTUDE (à ré-enrichir ?) ────────────────────────────
        tech_textbox.insert("end", "\n🩺 COMPLÉTUDE\n")
        _missing = []
        if not getattr(track, 'bpm', None):
            _missing.append("BPM")
        if getattr(track, 'key', None) is None or getattr(track, 'mode', None) is None:
            _missing.append("Key/Mode")
        if not getattr(track, 'isrc', None):
            _missing.append("ISRC")
        if not (track.lyrics or ""):
            _missing.append("paroles")
        if not getattr(track, 'spotify_id', None):
            _missing.append("Spotify ID")
        if _missing:
            tech_textbox.insert("end", "• ⚠️ Manque : " + ", ".join(_missing) + "  → à ré-enrichir\n")
        else:
            tech_textbox.insert("end", "• ✅ Complet\n")

        # ── RELATIONS ───────────────────────────────────────────────
        _nrel = len(getattr(track, 'relationships', None) or [])
        tech_textbox.insert("end", f"\n🎚️ Relations : {_nrel}\n")

        # Métadonnées de scraping
        tech_textbox.insert("end", "\n📅 HISTORIQUE\n")
        if track.last_scraped:
            tech_textbox.insert("end", f"• Dernier scraping: {self._format_datetime(track.last_scraped)}\n")
        if track.created_at:
            tech_textbox.insert("end", f"• Créé le: {self._format_datetime(track.created_at)}\n")
        if track.updated_at:
            tech_textbox.insert("end", f"• Mis à jour le: {self._format_datetime(track.updated_at)}\n")
        
        # === ONGLET 5: CERTIFICATIONS ===
        cert_frame = ctk.CTkFrame(notebook)
        notebook.add(cert_frame, text="🏆 Certifications")

        try:
            from src.utils.cert_matcher import get_cert_matcher
            matcher = get_cert_matcher()

            # Raccordement UNIFIÉ multi-pays (SNEP 🇫🇷 + BRMA 🇧🇪 + RIAA 🇺🇸)
            _extra = []
            _pan = getattr(track, 'primary_artist_name', None)
            if _pan and _pan != self.current_artist.name:
                _extra.append(_pan)
            _fa = getattr(track, 'featured_artists', None)
            if isinstance(_fa, str) and _fa:
                _extra.append(_fa)
            elif isinstance(_fa, (list, tuple)):
                _extra.extend(str(x) for x in _fa if x)
            track_certs = matcher.get_track_certifications(
                self.current_artist.name, track.title, extra_artists=_extra)
            album_certs = (matcher.get_album_certifications(
                self.current_artist.name, track.album) if track.album else [])

            if track_certs or album_certs:
                # Afficher les infos de certification
                cert_info = ctk.CTkTextbox(cert_frame, width=850, height=450)
                cert_info.pack(fill="both", expand=True, padx=10, pady=10)

                emoji_map = {
                    'Or': '🥇', 'Double Or': '🥇🥇', 'Triple Or': '🥇🥇🥇',
                    'Platine': '💿', 'Double Platine': '💿💿', 'Triple Platine': '💿💿💿',
                    'Diamant': '💎', 'Double Diamant': '💎💎', 'Triple Diamant': '💎💎💎',
                    'Quadruple Diamant': '💎💎💎💎'
                }

                body_label = {'SNEP': 'SNEP (France)', 'BRMA': 'BRMA (Belgique)',
                              'RIAA': 'RIAA (USA)'}

                def _render_grouped(certs):
                    """Rend les certifs GROUPÉES PAR PAYS (ordre du matcher : FR, BE, US)."""
                    txt = ""
                    bodies = []
                    for c in certs:
                        if c.get('body') not in bodies:
                            bodies.append(c.get('body'))
                    for body in bodies:
                        group = [c for c in certs if c.get('body') == body]
                        flag = group[0].get('flag', '🏆')
                        txt += f"\n{flag} {body_label.get(body, body)}\n"
                        txt += "-" * 60 + "\n"
                        for c in group:
                            lvl = c.get('certification', '')
                            emoji = emoji_map.get(lvl, '🏆')
                            line = f"{emoji} {lvl.upper()}"
                            if c.get('title'):
                                line += f" — {c.get('title')}"
                            txt += line + "\n"
                            if c.get('release_date'):
                                txt += f"   📅 Sortie: {self._format_date(c.get('release_date'))}\n"
                            txt += f"   ✅ Constat: {self._format_date(c.get('certification_date', 'N/A'))}\n"
                            if c.get('publisher'):
                                txt += f"   🏢 Éditeur: {c.get('publisher')}\n"
                            if c.get('detail_url'):
                                txt += f"   🔗 {c.get('detail_url')}\n"
                            if c.get('release_date') and c.get('certification_date'):
                                try:
                                    from datetime import datetime
                                    r = datetime.strptime(str(c['release_date'])[:10], '%Y-%m-%d')
                                    cc = datetime.strptime(str(c['certification_date'])[:10], '%Y-%m-%d')
                                    d = (cc - r).days
                                    if d >= 0:
                                        txt += f"   ⏱️ Obtention: {d} j ({d // 365} an(s), {(d % 365) // 30} mois)\n"
                                except Exception:
                                    pass
                            txt += "\n"
                    return txt

                cert_text = ""
                if track_certs:
                    cert_text += "🎵 CERTIFICATIONS DU MORCEAU\n" + "=" * 60 + "\n"
                    cert_text += _render_grouped(track_certs)
                if album_certs:
                    cert_text += f"\n💿 CERTIFICATIONS DE L'ALBUM — {track.album}\n" + "=" * 60 + "\n"
                    cert_text += _render_grouped(album_certs)

                cert_info.insert("0.0", cert_text)
                cert_info.configure(state="disabled")
            else:
                no_cert = ctk.CTkLabel(cert_frame, text="❌ Aucune certification trouvée pour ce morceau ou son album", font=("Arial", 14))
                no_cert.pack(expand=True)
        except Exception as e:
            logger.error(f"Erreur affichage certifications: {e}", exc_info=True)
            error_label = ctk.CTkLabel(cert_frame, text=f"Erreur: {e}", text_color="red")
            error_label.pack(expand=True)

        # ✅ NOUVEAU: Debug featuring détaillé
        tech_textbox.insert("end", f"\n🎤 DEBUG FEATURING:\n")
        tech_textbox.insert("end", f"• is_featuring: {getattr(track, 'is_featuring', 'Non défini')}\n")
        tech_textbox.insert("end", f"• primary_artist_name: {getattr(track, 'primary_artist_name', 'Non défini')}\n")
        tech_textbox.insert("end", f"• secondary_role: {getattr(track, 'secondary_role', None) or '—'}\n")
        tech_textbox.insert("end", f"• featured_artists: {getattr(track, 'featured_artists', 'Non défini')}\n")
        tech_textbox.insert("end", f"• track.artist.name: {track.artist.name if track.artist else 'Non défini'}\n")
        tech_textbox.insert("end", f"• current_artist.name: {self.current_artist.name if self.current_artist else 'Non défini'}\n")
        
        # Informations de la base de données
        tech_textbox.insert("end", f"\n💾 BASE DE DONNÉES:\n")
        tech_textbox.insert("end", f"• Track ID: {getattr(track, 'id', 'Non défini')}\n")
        tech_textbox.insert("end", f"• _album_from_api: {getattr(track, '_album_from_api', 'Non défini')}\n")
        tech_textbox.insert("end", f"• _release_date_from_api: {self._format_date(getattr(track, '_release_date_from_api', None))}\n")
        
        # Erreurs de scraping
        if track.scraping_errors:
            tech_textbox.insert("end", f"\n❌ ERREURS DE SCRAPING:\n")
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
                calculate_total_streams, streams_source_label, format_streams,
                SPOTIFY_SHARE, YTM_SHARE, COMBINED_SHARE)

            sp = getattr(track, 'spotify_streams', None)
            yt = getattr(track, 'ytm_streams', None)
            sp_upd = getattr(track, 'spotify_streams_updated', None)
            yt_upd = getattr(track, 'ytm_streams_updated', None)
            total_est = calculate_total_streams(sp, yt)
            suffix = streams_source_label(sp, yt)

            streams_content = ctk.CTkFrame(streams_frame, fg_color="transparent")
            streams_content.pack(fill="both", expand=True, padx=30, pady=20)

            def row(label, value, bold=False, color=None):
                f = ctk.CTkFrame(streams_content, fg_color="transparent")
                f.pack(fill="x", pady=3)
                ctk.CTkLabel(f, text=label, width=220, anchor="w",
                             font=ctk.CTkFont(weight="bold" if bold else "normal")
                             ).pack(side="left")
                kw = {"text_color": color} if color else {}
                ctk.CTkLabel(f, text=value, anchor="w",
                             font=ctk.CTkFont(weight="bold" if bold else "normal"),
                             **kw).pack(side="left")

            row("Streams Spotify :", format_streams(sp) if sp else "—")
            if sp_upd:
                row("  MaJ Spotify :", self._format_datetime(sp_upd) if sp_upd else "—")

            row("Streams YouTube Music :", format_streams(yt) if yt else "—")
            if yt_upd:
                row("  MaJ YouTube Music :", self._format_datetime(yt_upd) if yt_upd else "—")

            ctk.CTkLabel(streams_content, text="─" * 60,
                         text_color="gray").pack(fill="x", pady=(10, 4))

            if total_est:
                row("Streams totaux estimés :",
                    f"{format_streams(total_est)}{suffix}",
                    bold=True, color="#4fc3f7")
                # Détail de la formule
                if sp and yt:
                    formula = f"({format_streams(sp)} Sp + {format_streams(yt)} YT) ÷ {COMBINED_SHARE:.0%}"
                elif sp:
                    formula = f"{format_streams(sp)} Sp ÷ {SPOTIFY_SHARE:.0%} (Spotify seul)"
                else:
                    formula = f"{format_streams(yt)} YT ÷ {YTM_SHARE:.0%} (YT Music seul)"
                ctk.CTkLabel(streams_content, text=f"Formule : {formula}",
                             text_color="gray", font=ctk.CTkFont(size=11)
                             ).pack(anchor="w", pady=(0, 6))
            else:
                row("Streams totaux estimés :", "Données insuffisantes", color="gray")

            ctk.CTkLabel(
                streams_content,
                text="Parts de marché FR 2025 : Spotify ~40 %  •  YT Music ~25 %  •  Ensemble ~65 %",
                text_color="gray", font=ctk.CTkFont(size=10)
            ).pack(anchor="w", pady=(10, 0))

        except Exception as e:
            ctk.CTkLabel(streams_frame, text=f"Erreur : {e}", text_color="red").pack(expand=True)

        # Bouton de fermeture
        close_button = ctk.CTkButton(
            details_window, 
            text="Fermer", 
            command=details_window.destroy,
            width=100
        )
        close_button.pack(pady=10)
    
    def _refresh_selection_display(self):
        """Met à jour l'affichage des sélections dans le tableau"""
        for item in self.tree.get_children():
            tags = self.tree.item(item)["tags"]
            if tags and len(tags) > 0:
                index = int(tags[0])
                
                if "disabled" in tags or self._is_track_disabled_by_index(index):
                    self.tree.item(item, text="⊘")
                elif index in self.selected_tracks:
                    self.tree.item(item, text="☑")
                else:
                    self.tree.item(item, text="☐")

    def _select_all_tracks(self):
        """Sélectionne tous les morceaux actifs (non désactivés)"""
        if not self.current_artist or not self.current_artist.tracks:
            return
        
        self.selected_tracks.clear()
        
        for i in range(len(self.current_artist.tracks)):
            # Ne sélectionner que les morceaux actifs
            if not self._is_track_disabled_by_index(i):
                self.selected_tracks.add(i)
        
        self._refresh_selection_display()
        self._update_selection_count()
    
    def _deselect_all_tracks(self):
        """Désélectionne tous les morceaux"""
        self.selected_tracks.clear()
        self._refresh_selection_display()
        self._update_selection_count()

    def _check_selected_tracks(self):
        """Coche tous les morceaux actuellement en surbrillance (sélection visuelle)"""
        # Récupérer les items en surbrillance dans le Treeview
        highlighted_items = self.tree.selection()

        if not highlighted_items:
            logger.info("Aucun morceau en surbrillance")
            return

        logger.info(f"Cochage de {len(highlighted_items)} morceaux en surbrillance")
        for item in highlighted_items:
            tags = self.tree.item(item)["tags"]
            if tags:
                index = int(tags[0])

                # Vérifier que le morceau n'est pas désactivé
                if not self._is_track_disabled_by_index(index):
                    # Ajouter à la sélection et cocher
                    self.selected_tracks.add(index)
                    self.tree.item(item, text="☑")
                    logger.debug(f"Morceau {index} coché")

        self._update_selection_count()

    def _update_selection_count(self):
        """Met à jour l'affichage du nombre de morceaux sélectionnés"""
        if hasattr(self, 'selected_count_label'):
            total = len(self.current_artist.tracks) if self.current_artist and self.current_artist.tracks else 0
            selected = len(self.selected_tracks)
            disabled = len(self.disabled_tracks)
            active = total - disabled
            
            text = f"Sélectionnés: {selected}/{active} actifs"
            if disabled > 0:
                text += f" ({disabled} désactivés)"
            
            self.selected_count_label.configure(text=text)

    def _open_certification_update(self):
        """Ouvre la fenêtre de mise à jour des certifications"""
        try:
            # Initialiser le gestionnaire si disponible
            cert_manager = None
            try:
                from src.utils.certification_manager import CertificationManager
                cert_manager = CertificationManager()
            except:
                pass
            
            # Ouvrir la fenêtre (pré-remplie avec l'artiste courant + sa
            # discographie, pour l'audit des certifs orphelines)
            current_name = self.current_artist.name if self.current_artist else None
            artist_tracks = None
            artist_albums = None
            if self.current_artist and getattr(self.current_artist, 'tracks', None):
                artist_tracks = [t.title for t in self.current_artist.tracks if getattr(t, 'title', None)]
                # Noms d'albums distincts (pour l'audit des certifs d'albums)
                artist_albums = sorted({
                    t.album for t in self.current_artist.tracks
                    if getattr(t, 'album', None)
                })
            dialog = CertificationUpdateDialog(self.root, cert_manager,
                                               default_artist=current_name,
                                               artist_tracks=artist_tracks,
                                               artist_albums=artist_albums)
            dialog.transient(self.root)
            dialog.grab_set()
            
        except Exception as e:
            logger.error(f"Erreur: {e}")
            messagebox.showerror("Erreur", f"Impossible d'ouvrir la fenêtre:\n{str(e)}")

    def _export_data(self):
        """Exporte les données en JSON - ✅ MODIFIÉ POUR EXCLURE LES DÉSACTIVÉS"""
        if not self.current_artist:
            return
        
        # Demander où sauvegarder
        filepath = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialfile=f"{self.current_artist.name.replace(' ', '_').lower()}_credits.json"
        )
        
        if filepath:
            try:
                # ✅ NOUVEAU: Filtrer les morceaux désactivés avant export
                if self.disabled_tracks:
                    # Créer une copie temporaire de l'artiste sans les morceaux désactivés
                    temp_artist = Artist(
                        id=self.current_artist.id,
                        name=self.current_artist.name,
                        genius_id=self.current_artist.genius_id,
                        spotify_id=self.current_artist.spotify_id,
                        discogs_id=self.current_artist.discogs_id
                    )
                    
                    # Ajouter seulement les morceaux actifs
                    for track in self.current_artist.tracks:
                        if not self._is_track_disabled(track):
                            temp_artist.tracks.append(track)
                    
                    # Exporter l'artiste filtré
                    self.data_manager.export_to_json(temp_artist.name, filepath)
                    
                    disabled_count = len(self.disabled_tracks)
                    messagebox.showinfo("Succès", 
                        f"Données exportées vers:\n{filepath}\n\n"
                        f"✅ {len(temp_artist.tracks)} morceaux exportés\n"
                        f"⊘ {disabled_count} morceaux désactivés exclus")
                else:
                    # Export normal si aucun morceau désactivé
                    self.data_manager.export_to_json(self.current_artist.name, filepath)
                    messagebox.showinfo("Succès", f"Données exportées vers:\n{filepath}")
                    
            except Exception as e:
                error_msg = str(e)
                logger.error(f"Erreur lors de l'export: {error_msg}")
                messagebox.showerror("Erreur", f"Erreur lors de l'export: {error_msg}")

    # ✅ AJOUT DES MÉTHODES MANQUANTES (suite des autres méthodes existantes)

    @staticmethod
    def _build_genius_slug(name: str) -> str:
        """Construit le slug Genius depuis un nom d'artiste.

        Règles :
        - Tout en minuscules
        - Supprime '.' et "'"
        - Remplace les espaces par '-'
        - Première lettre en majuscule

        Ex: 'Sofiane Pamart' → 'Sofiane-pamart'
            "L'Or du Commun" → 'Lor-du-commun'
            'NWA'            → 'Nwa'
        """
        slug = name.lower()
        for ch in (".", "'", "’"):  # point, apostrophe droite, apostrophe typographique
            slug = slug.replace(ch, "")
        slug = slug.replace(" ", "-")
        if slug:
            slug = slug[0].upper() + slug[1:]
        return slug

    def _fetch_artist_from_genius_url(self, url: str, fallback_name: str) -> "Optional[Artist]":
        """Charge la page Genius d'un artiste via Playwright et extrait l'ID depuis le meta tag.

        Utilise :
            JSON.parse(document.querySelector('meta[itemprop="page_data"]').content).artist.id
        """
        scraper = None
        try:
            scraper = GeniusScraper(headless=True)
            scraper.page.goto(url, wait_until="domcontentloaded", timeout=15_000)
            result = scraper.page.evaluate("""() => {
                const meta = document.querySelector('meta[itemprop="page_data"]');
                if (!meta) return null;
                try {
                    const data = JSON.parse(meta.content);
                    if (!data.artist || !data.artist.id) return null;
                    return {id: data.artist.id, name: data.artist.name};
                } catch(e) {
                    return null;
                }
            }""")
            if result and result.get('id'):
                return Artist(
                    name=result.get('name') or fallback_name,
                    genius_id=result['id']
                )
        except Exception as e:
            logger.debug(f"Fetch artiste depuis {url} échoué: {e}")
        finally:
            if scraper:
                try:
                    scraper.close()
                except Exception:
                    pass
        return None

    def _show_artist_selection_dialog(self, candidates, artist_name: str, result_queue):
        """Dialog modal pour choisir parmi plusieurs artistes candidats Genius.

        Si l'artiste n'apparaît pas dans la liste, l'utilisateur peut entrer
        son ID Genius manuellement (visible sur genius.com/artists/NomArtiste).
        """
        import customtkinter as ctk

        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Choisir un artiste")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.lift()
        dialog.focus_force()

        def _put(value):
            result_queue.put(value)
            dialog.destroy()

        def _confirm_manual_id():
            raw = id_entry.get().strip()
            # Accepte un ID numérique ou une URL genius.com/artists/NomArtiste
            genius_id = None
            if raw.isdigit():
                genius_id = int(raw)
            elif "genius.com/artists/" in raw:
                # Extraire le slug puis résoudre via l'API
                slug = raw.split("genius.com/artists/")[-1].split("/")[0].split("?")[0]
                self._resolve_genius_slug(slug, artist_name, result_queue, dialog)
                return
            if genius_id:
                _put(Artist(name=artist_name, genius_id=genius_id))
            else:
                id_entry.configure(border_color="red")

        # ── Titre ────────────────────────────────────────────────────────────
        if candidates:
            label_text = f"Plusieurs résultats pour « {artist_name} ».\nChoisissez l'artiste :"
        else:
            label_text = f"Aucun résultat automatique pour « {artist_name} »."
        ctk.CTkLabel(
            dialog,
            text=label_text,
            font=("Arial", 13),
            justify="left"
        ).pack(padx=20, pady=(16, 8), anchor="w")

        # ── Boutons candidats ─────────────────────────────────────────────────
        for artist in candidates:
            ctk.CTkButton(
                dialog,
                text=f"{artist.name}   (ID Genius : {artist.genius_id})",
                anchor="w",
                command=lambda a=artist: _put(a)
            ).pack(padx=20, pady=3, fill="x")

        # ── Saisie manuelle ───────────────────────────────────────────────────
        ctk.CTkLabel(
            dialog,
            text="Artiste absent ? Entrez l'ID Genius ou l'URL genius.com/artists/… :",
            font=("Arial", 11),
            text_color="gray"
        ).pack(padx=20, pady=(14, 2), anchor="w")

        id_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        id_frame.pack(padx=20, pady=(0, 4), fill="x")

        id_entry = ctk.CTkEntry(id_frame, placeholder_text="Ex : 123456  ou  genius.com/artists/Isha")
        id_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        id_entry.bind("<Return>", lambda e: _confirm_manual_id())

        ctk.CTkButton(id_frame, text="OK", width=50, command=_confirm_manual_id).pack(side="left")

        # ── Annuler ───────────────────────────────────────────────────────────
        ctk.CTkButton(
            dialog,
            text="Annuler",
            fg_color="gray",
            command=lambda: _put(None)
        ).pack(padx=20, pady=(8, 16))

        height = 130 + len(candidates) * 46 + 90
        dialog.geometry(f"460x{height}")

        dialog.protocol("WM_DELETE_WINDOW", lambda: _put(None))
        dialog.wait_window()

    def _resolve_genius_slug(self, slug: str, artist_name: str, result_queue, parent_dialog):
        """Charge genius.com/artists/{slug} via Playwright et extrait l'ID artiste."""
        import threading

        def fetch():
            url = f"https://genius.com/artists/{slug}"
            artist = self._fetch_artist_from_genius_url(url, artist_name)
            if artist and artist.genius_id:
                result_queue.put(artist)
                self.root.after(0, parent_dialog.destroy)
            else:
                self.root.after(0, lambda: messagebox.showwarning(
                    "Introuvable",
                    f"Aucun artiste trouvé sur genius.com/artists/{slug}.\n"
                    "Vérifiez l'orthographe ou entrez l'ID numérique directement."
                ))

        threading.Thread(target=fetch, daemon=True).start()

    def _search_artist(self):
        """Recherche un artiste - VERSION CORRIGÉE POUR CHARGEMENT EXISTANT"""
        artist_name = self.artist_entry.get().strip()
        if not artist_name:
            messagebox.showwarning("Attention", "Veuillez entrer un nom d'artiste")
            return
        
        # Désactiver les boutons pendant la recherche
        self.search_button.configure(state="disabled", text="Recherche...")
        
        def search():
            try:
                logger.info(f"🔍 Recherche de l'artiste: '{artist_name}'")

                # Vérifier d'abord dans la base de données locale
                artist = self.data_manager.get_artist_by_name(artist_name)
                if artist:
                    logger.info(f"✅ Artiste trouvé en base: {artist.name} avec {len(artist.tracks)} morceaux")
                    self.current_artist = artist
                    self.root.after(0, self._update_artist_info)
                    self.root.after(0, self._apply_default_sort)
                    self.root.after(0, lambda: messagebox.showinfo(
                        "Artiste chargé",
                        f"✅ Artiste '{artist.name}' chargé depuis la base de données\n"
                        f"📀 {len(artist.tracks)} morceaux disponibles\n"
                        f"🎤 ID Genius: {artist.genius_id}\n\n"
                        "Vous pouvez maintenant scraper ou enrichir les données."
                    ))
                    return

                # Construire l'URL Genius depuis le nom (sans appel API)
                logger.info("🌐 Artiste non trouvé en base, recherche via URL Genius...")
                slug = self._build_genius_slug(artist_name)
                genius_url = f"https://genius.com/artists/{slug}"
                logger.info(f"🔗 Tentative : {genius_url}")

                genius_artist = self._fetch_artist_from_genius_url(genius_url, artist_name)

                if not (genius_artist and genius_artist.genius_id):
                    # Slug incorrect ou page inexistante → dialog de saisie manuelle
                    logger.info(f"⚠️ Artiste non trouvé sur {genius_url}, affichage du dialog")
                    import queue as _queue
                    result_q = _queue.Queue()
                    self.root.after(0, lambda: self._show_artist_selection_dialog(
                        [], artist_name, result_q
                    ))
                    genius_artist = result_q.get()
                    if genius_artist is None:
                        return  # Annulé par l'utilisateur

                self.data_manager.save_artist(genius_artist)
                self.current_artist = genius_artist
                self.root.after(0, self._update_artist_info)
                self.root.after(0, self._apply_default_sort)
                self.root.after(0, lambda: messagebox.showinfo(
                    "Artiste trouvé",
                    f"✅ Artiste trouvé : '{genius_artist.name}'\n"
                    f"🎤 ID Genius: {genius_artist.genius_id}\n\n"
                    "Cliquez sur 'Récupérer les morceaux' pour commencer."
                ))

            except Exception as e:
                error_msg = str(e) if str(e) else "Erreur inconnue lors de la recherche"
                logger.error(f"❌ Erreur lors de la recherche: {error_msg}")
                import traceback
                logger.debug(f"Traceback complet: {traceback.format_exc()}")
                self.root.after(0, lambda: messagebox.showerror(
                    "Erreur",
                    f"❌ Erreur lors de la recherche:\n{error_msg}\n\n"
                    "Consultez les logs pour plus de détails."
                ))
            finally:
                self.root.after(0, lambda: self.search_button.configure(state="normal", text="Rechercher"))
        
        # Lancer dans un thread
        threading.Thread(target=search, daemon=True).start()

    def _load_existing_artist(self):
        """Charge un artiste existant depuis la base de données - VERSION AVEC GESTION"""
        # Créer une fenêtre de gestion des artistes
        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Gestionnaire d'artistes")
        dialog.geometry("600x650")
        
        # Centrer la fenêtre sur l'écran
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() // 2) - (300)
        y = (dialog.winfo_screenheight() // 2) - (325)
        dialog.geometry(f"600x650+{x}+{y}")
        
        dialog.lift()
        dialog.focus_force()
        dialog.grab_set()
        
        # Titre
        ctk.CTkLabel(dialog, text="Gestionnaire d'artistes", 
                    font=("Arial", 18, "bold")).pack(pady=15)
        
        # Frame pour la liste et les boutons
        main_frame = ctk.CTkFrame(dialog)
        main_frame.pack(fill="both", expand=True, padx=20, pady=10)
        
        # Liste des artistes avec informations
        list_frame = ctk.CTkFrame(main_frame)
        list_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        ctk.CTkLabel(list_frame, text="Artistes en base de données:", 
                    font=("Arial", 14, "bold")).pack(anchor="w", padx=10, pady=(10, 5))
        
        # Récupérer les artistes avec leurs statistiques
        artists_data = self._get_artists_with_stats()
        
        if not artists_data:
            ctk.CTkLabel(list_frame, text="Aucun artiste dans la base de données",
                        text_color="gray").pack(pady=20)
            ctk.CTkButton(dialog, text="Fermer", command=dialog.destroy).pack(pady=10)
            return
        
        # Scrollable frame pour la liste
        scrollable_frame = ctk.CTkScrollableFrame(list_frame, height=300)
        scrollable_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        # Variable pour stocker l'artiste sélectionné
        selected_artist = {"name": None, "widget": None}
        
        # Créer les éléments de la liste
        artist_widgets = []

        # Fonction pour sélectionner un artiste (surligne le BOUTON, pas la frame
        # cachée derrière — sinon la sélection est invisible et les frames non
        # sélectionnées paraissent surlignées à cause de leur couleur par défaut)
        def select_artist(name, button):
            if selected_artist["widget"]:
                selected_artist["widget"].configure(fg_color="transparent")
            selected_artist["name"] = name
            selected_artist["widget"] = button
            button.configure(fg_color=("#3B8ED0", "#1F6AA5"))

        # Fonction pour charger directement avec double-clic
        def load_on_double_click(name):
            self.artist_entry.delete(0, "end")
            self.artist_entry.insert(0, name)
            dialog.destroy()
            self._search_artist()

        for artist_info in artists_data:
            # Frame transparente : toutes les lignes non sélectionnées sont identiques
            artist_frame = ctk.CTkFrame(scrollable_frame, fg_color="transparent")
            artist_frame.pack(fill="x", padx=5, pady=2)

            # Informations de l'artiste
            info_text = f"🎤 {artist_info['name']}\n"
            info_text += f"   📀 {artist_info['tracks_count']} morceaux"
            if artist_info['credits_count'] > 0:
                info_text += f" • 🏷️ {artist_info['credits_count']} crédits"
            if artist_info['last_update']:
                info_text += f"\n   📅 Mis à jour: {artist_info['last_update']}"

            # Bouton cliquable pour sélectionner
            artist_button = ctk.CTkButton(
                artist_frame,
                text=info_text,
                fg_color="transparent",
                text_color=("black", "white"),
                hover_color=("gray80", "gray40"),
                anchor="w",
                height=60
            )
            artist_button.configure(
                command=lambda n=artist_info['name'], b=artist_button: select_artist(n, b)
            )
            artist_button.pack(fill="x", padx=5, pady=2)

            # Double-clic pour charger directement
            artist_button.bind("<Double-Button-1>", lambda e, n=artist_info['name']: load_on_double_click(n))

            artist_widgets.append({
                'name': artist_info['name'],
                'frame': artist_frame,
                'button': artist_button
            })
        
        # Frame pour les boutons d'action
        action_frame = ctk.CTkFrame(main_frame)
        action_frame.pack(fill="x", padx=10, pady=10)
        
        ctk.CTkLabel(action_frame, text="Actions:", 
                    font=("Arial", 12, "bold")).pack(anchor="w", padx=10, pady=(10, 5))
        
        # Boutons d'action
        buttons_frame = ctk.CTkFrame(action_frame)
        buttons_frame.pack(fill="x", padx=10, pady=15)

        def load_selected():
            if not selected_artist["name"]:
                messagebox.showwarning("Attention", "Veuillez sélectionner un artiste")
                return
            
            # Charger l'artiste sélectionné
            self.artist_entry.delete(0, "end")
            self.artist_entry.insert(0, selected_artist["name"])
            dialog.destroy()
            self._search_artist()
        
        def delete_selected():
            if not selected_artist["name"]:
                messagebox.showwarning("Attention", "Veuillez sélectionner un artiste à supprimer")
                return
            
            artist_name = selected_artist["name"]
            
            # Confirmation de suppression
            result = messagebox.askyesno(
                "Confirmation de suppression",
                f"Êtes-vous sûr de vouloir supprimer l'artiste '{artist_name}' ?\n\n"
                "⚠️ Cette action supprimera :\n"
                "• L'artiste\n"
                "• Tous ses morceaux\n"
                "• Tous les crédits associés\n"
                "• Toutes les données de scraping\n\n"
                "Cette action est IRRÉVERSIBLE !",
                icon="warning"
            )
            
            if result:
                try:
                    # Supprimer l'artiste et toutes ses données
                    success = self.data_manager.delete_artist(artist_name)
                    
                    if success:
                        messagebox.showinfo("Succès", f"Artiste '{artist_name}' supprimé avec succès")
                        dialog.destroy()
                        # Rafraîchir la liste en rouvrant le dialog
                        self._load_existing_artist()
                    else:
                        messagebox.showerror("Erreur", "Impossible de supprimer l'artiste")
                        
                except Exception as e:
                    error_msg = str(e)
                    logger.error(f"Erreur lors de la suppression: {error_msg}")
                    messagebox.showerror("Erreur", f"Erreur lors de la suppression:\n{error_msg}")
        
        def refresh_list():
            """Rafraîchit la liste des artistes"""
            dialog.destroy()
            self._load_existing_artist()
        
        def show_artist_details():
            """Affiche les détails de l'artiste sélectionné"""
            if not selected_artist["name"]:
                messagebox.showwarning("Attention", "Veuillez sélectionner un artiste")
                return
            
            # Récupérer les détails complets
            details = self.data_manager.get_artist_details(selected_artist["name"])
            
            # Créer une fenêtre de détails
            details_dialog = ctk.CTkToplevel(dialog)
            details_dialog.title(f"Détails - {selected_artist['name']}")
            details_dialog.geometry("600x500")
            details_dialog.transient(dialog)
            
            text_widget = ctk.CTkTextbox(details_dialog, width=580, height=450)
            text_widget.pack(padx=10, pady=10)
            
            details_text = f"""🎤 ARTISTE: {details['name']}
{'='*50}

📊 STATISTIQUES:
• Morceaux: {details['tracks_count']}
• Crédits: {details['credits_count']}
• Créé le: {details['created_at']}
• Mis à jour: {details['updated_at']}

🎵 MORCEAUX LES PLUS RÉCENTS:
"""
            
            for track in details['recent_tracks'][:10]:  # 10 morceaux les plus récents
                details_text += f"• {track['title']}"
                if track['album']:
                    details_text += f" ({track['album']})"
                if track['release_date']:
                    details_text += f" - {track['release_date'][:4]}"
                details_text += f" - {track['credits_count']} crédits\n"
            
            if details['tracks_count'] > 10:
                details_text += f"... et {details['tracks_count'] - 10} autres morceaux\n"
            
            details_text += f"""
🏷️ CRÉDITS PAR RÔLE:
"""
            for role, count in details['credits_by_role'].items():
                details_text += f"• {role}: {count}\n"
            
            text_widget.insert("0.0", details_text)
            text_widget.configure(state="disabled")
        
        # Rangée de boutons
        ctk.CTkButton(buttons_frame, text="📂 Charger", 
                 command=load_selected, width=120).pack(side="left", padx=8)
    
        ctk.CTkButton(buttons_frame, text="🗑️ Supprimer", 
                 command=delete_selected, width=120,
                 fg_color="red", hover_color="darkred").pack(side="left", padx=8)
    
        ctk.CTkButton(buttons_frame, text="ℹ️ Détails", 
                 command=show_artist_details, width=120).pack(side="left", padx=8)
    
        ctk.CTkButton(buttons_frame, text="🔄 Actualiser", 
                 command=refresh_list, width=120).pack(side="left", padx=8)
        
        # Bouton fermer
        ctk.CTkButton(dialog, text="Fermer", command=dialog.destroy, width=100).pack(pady=10)

    def _get_artists_with_stats(self):
        """Récupère la liste des artistes avec leurs statistiques - VERSION AVEC DEBUG"""
        try:
            logger.info("🔍 Début récupération des artistes avec stats")
            
            with self.data_manager._get_connection() as conn:
                cursor = conn.cursor()
                logger.info("✅ Connexion à la base établie")
                
                cursor.execute("""
                    SELECT 
                        a.name,
                        COUNT(DISTINCT t.id) as tracks_count,
                        COUNT(DISTINCT c.id) as credits_count,
                        MAX(t.updated_at) as last_update,
                        a.created_at
                    FROM artists a
                    LEFT JOIN tracks t ON a.id = t.artist_id
                    LEFT JOIN credits c ON t.id = c.track_id
                    GROUP BY a.id, a.name, a.created_at
                    ORDER BY a.name
                """)
                
                rows = cursor.fetchall()
                logger.info(f"📊 {len(rows)} lignes récupérées de la base")
                
                artists_data = []
                for i, row in enumerate(rows):
                    logger.debug(f"Ligne {i}: {row}")
                    
                    try:
                        artist_info = {
                            'name': row[0],
                            'tracks_count': row[1] or 0,
                            'credits_count': row[2] or 0,
                            'last_update': row[3][:10] if row[3] else None,
                            'created_at': row[4][:10] if row[4] else None
                        }
                        artists_data.append(artist_info)
                        logger.debug(f"✅ Artiste {artist_info['name']} traité")
                        
                    except Exception as row_error:
                        logger.error(f"❌ Erreur sur la ligne {i}: {row_error}")
                        logger.error(f"Contenu de la ligne: {row}")
                
                logger.info(f"✅ {len(artists_data)} artistes traités avec succès")
                return artists_data
                
        except Exception as e:
            logger.error(f"❌ Erreur lors de la récupération des artistes: {e}")
            logger.error(f"Type d'erreur: {type(e)}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return []

    def _close_all_detail_windows(self):
        """Ferme toutes les fenêtres de détail ouvertes (appelé lors du changement d'artiste)"""
        for track_id, (window, _) in list(self.open_detail_windows.items()):
            try:
                window.destroy()
            except Exception:
                pass
        self.open_detail_windows.clear()

    def _apply_default_sort(self):
        """Tri par défaut : date de sortie, plus récent en haut."""
        if not self.current_artist or not self.current_artist.tracks:
            return
        # Astuce : _sort_column inverse l'ordre quand on re-trie la même colonne.
        # En pré-positionnant sort_reverse=False, l'appel produit un tri descendant.
        self.sort_column = "Date sortie"
        self.sort_reverse = False
        self._sort_column("Date sortie")

    def _update_artist_info(self):
        """Met à jour les informations de l'artiste - VERSION AVEC DÉCOMPTES"""
        # Fermer les fenêtres de détail de l'artiste précédent
        self._close_all_detail_windows()
        if self.current_artist:
            self.artist_info_label.configure(text=f"Artiste: {self.current_artist.name}")
            
            if self.current_artist.tracks:
                # Calculs des statistiques
                total_tracks = len(self.current_artist.tracks)

                # Compter les features
                featuring_count = sum(1 for t in self.current_artist.tracks
                                    if hasattr(t, 'is_featuring') and t.is_featuring)
                main_tracks = total_tracks - featuring_count

                # Compter les morceaux désactivés (utilise IDs maintenant)
                disabled_count = len(self.disabled_tracks) if hasattr(self, 'disabled_tracks') else 0

                # Compter les morceaux ACTIFS (non désactivés) pour les stats
                # Utilise _is_track_disabled qui vérifie par ID
                active_tracks = [t for t in self.current_artist.tracks if not self._is_track_disabled(t)]

                # Morceaux avec crédits musicaux (actifs uniquement)
                tracks_with_music_credits = sum(1 for t in active_tracks if len(t.get_music_credits()) > 0)

                # Morceaux avec paroles (actifs uniquement)
                tracks_with_lyrics = sum(1 for t in active_tracks
                                        if hasattr(t, 'lyrics') and t.lyrics and t.lyrics.strip())

                # Morceaux avec données additionnelles = BPM + Key/Mode + Durée (actifs uniquement)
                tracks_with_additional = sum(1 for t in active_tracks
                                            if (hasattr(t, 'bpm') and t.bpm and (isinstance(t.bpm, (int, float)) and t.bpm > 0 or isinstance(t.bpm, str) and t.bpm.isdigit() and int(t.bpm) > 0)) and
                                            ((hasattr(t, 'musical_key') and t.musical_key) or
                                            (hasattr(t, 'key') and t.key and hasattr(t, 'mode') and t.mode is not None)) and
                                            (hasattr(t, 'duration') and t.duration))

                # Morceaux avec certifications (actifs uniquement)
                tracks_with_certifications = sum(1 for t in active_tracks
                                                if hasattr(t, 'certifications') and t.certifications and len(t.certifications) > 0)

                # Albums avec certifications (compter les albums uniques, pas les morceaux)
                albums_with_certifications = len({t.album for t in active_tracks
                                                 if hasattr(t, 'album_certifications') and t.album_certifications and len(t.album_certifications) > 0
                                                 and hasattr(t, 'album') and t.album})

                # Morceaux avec données manquantes (SANS compter les désactivés)
                tracks_with_missing_data = sum(1 for t in active_tracks if self._get_track_status_icon(t) == '⚠️')

                # ✅ LIGNE 1: Statistiques principales
                line1_parts = []

                # Total avec détail features
                if featuring_count > 0:
                    line1_parts.append(f"{total_tracks} Morceaux ({main_tracks} Principaux + {featuring_count} Feat)")
                else:
                    line1_parts.append(f"{total_tracks} Morceaux")

                # Avec crédits musicaux
                line1_parts.append(f"{tracks_with_music_credits} avec Crédits")

                # Avec paroles
                line1_parts.append(f"{tracks_with_lyrics} avec Paroles")

                # Avec données additionnelles
                line1_parts.append(f"{tracks_with_additional} avec Données Add.")

                # Avec certifications (morceaux + albums)
                if albums_with_certifications > 0:
                    line1_parts.append(f"{tracks_with_certifications} avec Certifications (+ {albums_with_certifications} Certifications Album)")
                else:
                    line1_parts.append(f"{tracks_with_certifications} avec Certifications")

                line1 = " - ".join(line1_parts)

                # ✅ LIGNE 2: Données manquantes
                line2 = f"{tracks_with_missing_data} Morceaux avec Données manquantes"

                # ✅ LIGNE 3: Streams et auditeurs mensuels
                try:
                    from src.utils.streams_calculator import (
                        calculate_total_streams, calculate_total_monthly_listeners, format_streams)
                    total_cumul = 0
                    tracks_with_streams = 0
                    for t in active_tracks:
                        sp = getattr(t, 'spotify_streams', None)
                        yt = getattr(t, 'ytm_streams', None)
                        est = calculate_total_streams(sp, yt)
                        if est:
                            total_cumul += est
                            tracks_with_streams += 1
                    sp_ml = getattr(self.current_artist, 'spotify_monthly_listeners', None)
                    yt_ml = getattr(self.current_artist, 'ytm_monthly_listeners', None)
                    total_ml = calculate_total_monthly_listeners(sp_ml, yt_ml)

                    line3_parts = []
                    if total_cumul > 0:
                        line3_parts.append(f"Streams cumulés : {format_streams(total_cumul)} (estimé, {tracks_with_streams} morceaux)")
                    if total_ml:
                        line3_parts.append(f"Auditeurs/mois : {format_streams(total_ml)} (estimé)")
                    line3 = " - ".join(line3_parts) if line3_parts else ""
                except Exception:
                    line3 = ""

                info_text = f"{line1}\n{line2}"
                if line3:
                    info_text += f"\n{line3}"
                self.tracks_info_label.configure(text=info_text)
                
                self._populate_tracks_table()
                
                # Activer les boutons
                self.scrape_button.configure(state="normal")
                self.export_button.configure(state="normal")
                
                if hasattr(self, 'force_update_button'):
                    self.force_update_button.configure(state="normal")
                if hasattr(self, 'enrich_button'):
                    self.enrich_button.configure(state="normal")
                if hasattr(self, 'lyrics_button'):
                    self.lyrics_button.configure(state="normal")
                    
            else:
                self.tracks_info_label.configure(text="Aucun morceau chargé")
                if hasattr(self, 'lyrics_button'):
                    self.lyrics_button.configure(state="disabled")
            
            self.get_tracks_button.configure(state="normal")

    def _update_statistics(self):
        """Met à jour les statistiques affichées"""
        try:
            stats = self.data_manager.get_statistics()
            text = (f"Base de données: {stats['total_artists']} artistes | "
                   f"{stats['total_tracks']} morceaux | "
                   f"{stats['total_credits']} crédits | "
                   f"{stats['recent_errors']} erreurs récentes")
            self.stats_label.configure(text=text)
        except Exception as e:
            logger.error(f"Erreur lors de la mise à jour des stats: {e}")

    def _format_lyrics_for_display(self, lyrics: str) -> str:
        """Formate les paroles pour l'affichage dans l'interface - VERSION CORRIGÉE"""
        if not lyrics:
            return "Aucunes paroles disponibles"

        lines = lyrics.split('\n')
        formatted_lines = []

        for line in lines:
            line = line.strip()
            if not line:
                formatted_lines.append('')
                continue

            # ✅ CORRECTION: TOUTES les sections entre crochets ont le même formatage
            if line.startswith('[') and line.endswith(']'):
                # Extraire le contenu entre crochets
                section_content = line[1:-1]  # Enlever les [ ]

                # Créer la ligne décorée
                decorated_line = f"───────────────────────── [{section_content}] ─────────────────────────"

                formatted_lines.append('')
                formatted_lines.append(decorated_line)
                formatted_lines.append('')

            # Mentions d'artistes ou indentations spéciales
            elif '*' in line:
                formatted_lines.append(f"        {line}")

            # Paroles normales
            else:
                formatted_lines.append(line)

        return '\n'.join(formatted_lines)

    def _rescrape_single_lyrics(self, track: Track, parent_window):
        """Re-scrape les paroles d'un seul morceau"""
        try:
            from src.scrapers.genius_scraper_v2 import GeniusScraper

            scraper = GeniusScraper(headless=True)
            lyrics = scraper.scrape_track_lyrics(track)

            if lyrics:
                track.lyrics = lyrics
                track.has_lyrics = True
                track.lyrics_scraped_at = datetime.now()

                # Sauvegarder en base
                track.artist = self.current_artist
                self.data_manager.save_track(track)

                self.root.after(0, lambda: messagebox.showinfo(
                    "Succès",
                    f"✅ Paroles re-scrapées avec succès pour '{track.title}'"
                ))

                # Fermer et rouvrir la fenêtre de détails pour rafraîchir
                self.root.after(0, lambda: parent_window.destroy())
            else:
                self.root.after(0, lambda: messagebox.showwarning(
                    "Échec",
                    f"❌ Impossible de récupérer les paroles pour '{track.title}'"
                ))

            scraper.close()

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Erreur re-scraping paroles: {error_msg}")
            self.root.after(0, lambda: messagebox.showerror(
                "Erreur",
                f"Erreur lors du re-scraping:\n{error_msg}"
            ))

    def _get_tracks(self):
        """Récupère les morceaux de l'artiste - VERSION AVEC FEATURES"""
        if not self.current_artist:
            return
        
        # Inclure les features
        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Options de récupération")
        dialog.geometry("480x820")

        # Centrer la fenêtre
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() // 2) - (240)
        y = (dialog.winfo_screenheight() // 2) - (410)
        dialog.geometry(f"480x820+{x}+{y}")
        
        dialog.lift()
        dialog.focus_force()
        dialog.grab_set()
        
        # Variables pour les options
        include_features_var = ctk.BooleanVar(value=True)  # Par défaut, inclure les features
        prefill_var = ctk.BooleanVar(value=True)  # Appel API album + Spotify/YouTube (media)
        include_secondary_var = ctk.BooleanVar(value=False)  # Rôles secondaires (Additional Voices…)
        respect_deleted_var = ctk.BooleanVar(value=True)  # Ne pas réajouter les morceaux supprimés
        max_songs_var = ctk.IntVar(value=200)
        
        # Interface
        ctk.CTkLabel(dialog, text="Options de récupération des morceaux", 
                    font=("Arial", 16, "bold")).pack(pady=15)
        
        # Checkbox pour les features
        features_frame = ctk.CTkFrame(dialog)
        features_frame.pack(fill="x", padx=20, pady=15)
        
        ctk.CTkCheckBox(
            features_frame,
            text="Inclure les morceaux où l'artiste est en featuring",
            variable=include_features_var,
            font=("Arial", 12)
        ).pack(anchor="w", padx=15, pady=12)
        
        ctk.CTkLabel(features_frame,
                    text="✓ Recommandé : permet de récupérer plus de morceaux",
                    text_color="gray",
                    font=("Arial", 10)).pack(anchor="w", padx=15, pady=(0, 8))

        # Checkbox pour l'appel API album + media (Spotify/YouTube/relations)
        prefill_frame = ctk.CTkFrame(dialog)
        prefill_frame.pack(fill="x", padx=20, pady=(0, 5))

        ctk.CTkCheckBox(
            prefill_frame,
            text="Récupérer album + Spotify/YouTube (API media)",
            variable=prefill_var,
            font=("Arial", 12)
        ).pack(anchor="w", padx=15, pady=12)

        ctk.CTkLabel(prefill_frame,
                    text="⚡ Appel API détail par morceau primaire (album, Spotify ID, lien YouTube, relations).\n"
                         "Décocher = liste seule (plus rapide, le scrape rattrapera).",
                    text_color="gray",
                    font=("Arial", 10),
                    justify="left").pack(anchor="w", padx=15, pady=(0, 8))

        # Checkbox pour les rôles secondaires (Additional Voices, chœurs…)
        secondary_frame = ctk.CTkFrame(dialog)
        secondary_frame.pack(fill="x", padx=20, pady=(0, 5))

        ctk.CTkCheckBox(
            secondary_frame,
            text="Inclure les rôles secondaires (chœurs, Additional Voices…)",
            variable=include_secondary_var,
            font=("Arial", 12)
        ).pack(anchor="w", padx=15, pady=12)

        ctk.CTkLabel(secondary_frame,
                    text="🎙️ Vérifie chaque candidat au détail (id exact) → garde la vraie contribution\n"
                         "avec son rôle, écarte les homonymes. Quelques appels API en plus.",
                    text_color="gray",
                    font=("Arial", 10),
                    justify="left").pack(anchor="w", padx=15, pady=(0, 8))

        # Checkbox : ne pas réajouter les morceaux supprimés
        deleted_frame = ctk.CTkFrame(dialog)
        deleted_frame.pack(fill="x", padx=20, pady=(0, 5))

        ctk.CTkCheckBox(
            deleted_frame,
            text="Ne pas réajouter les morceaux supprimés",
            variable=respect_deleted_var,
            font=("Arial", 12)
        ).pack(anchor="w", padx=15, pady=12)

        _deleted_count = 0
        try:
            if self.current_artist:
                _deleted_count = len(self.deleted_tracks_manager.load_deleted_ids(self.current_artist.name))
        except Exception:
            _deleted_count = 0
        ctk.CTkLabel(deleted_frame,
                    text=f"🗂️ Respecte l'historique des suppressions ({_deleted_count} morceau(x) mémorisé(s)).\n"
                         "Décocher = autorise leur retour à cet import.",
                    text_color="gray",
                    font=("Arial", 10),
                    justify="left").pack(anchor="w", padx=15, pady=(0, 8))

        # Nombre maximum de morceaux
        max_songs_frame = ctk.CTkFrame(dialog)
        max_songs_frame.pack(fill="x", padx=20, pady=15)
        
        ctk.CTkLabel(max_songs_frame, text="Nombre maximum de morceaux:", 
                    font=("Arial", 12)).pack(anchor="w", padx=15, pady=(12, 5))
        
        max_songs_entry = ctk.CTkEntry(max_songs_frame, width=100, placeholder_text="200")
        max_songs_entry.pack(anchor="w", padx=15, pady=(0, 12))
        max_songs_entry.insert(0, "200")
        
        # Info supplémentaire
        info_frame = ctk.CTkFrame(dialog)
        info_frame.pack(fill="x", padx=20, pady=15)
        
        info_text = """ℹ️ Les morceaux en featuring seront marqués avec 🎤
⚡ L'album et la date seront récupérés automatiquement via l'API
🔍 Le scraping ne sera utilisé que pour les crédits détaillés"""
        
        ctk.CTkLabel(info_frame, text=info_text, 
                    font=("Arial", 9), 
                    text_color="gray",
                    justify="left").pack(anchor="w", padx=15, pady=10)
        
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
            dialog.destroy()
            self._start_track_retrieval(max_songs, include_features,
                                        prefill=prefill, update_only=update_only,
                                        include_secondary=include_secondary,
                                        respect_deleted=respect_deleted)

        def cancel():
            dialog.destroy()

        ctk.CTkButton(button_frame, text="🎵 Récupérer",
                 command=lambda: start_retrieval(update_only=False),
                 width=120, height=35).pack(side="left", padx=6)
        ctk.CTkButton(button_frame, text="🔄 Mettre à jour",
                 command=lambda: start_retrieval(update_only=True),
                 fg_color="#2A8C4A", hover_color="#23733D",
                 width=130, height=35).pack(side="left", padx=6)
        ctk.CTkButton(button_frame, text="❌ Annuler",
                 command=cancel, width=90, height=35).pack(side="right", padx=6)

    def _start_track_retrieval(self, max_songs: int, include_features: bool,
                               prefill: bool = True, update_only: bool = False,
                               include_secondary: bool = False, respect_deleted: bool = True):
        """Lance la récupération des morceaux avec les options choisies.

        prefill: appeler l'API détail (album + Spotify/YouTube media + relations).
        update_only: mode MàJ — n'appelle l'API media/album QUE pour les nouveaux
            titres (les genius_id déjà en base sont exclus du prefill).
        include_secondary: inclure les rôles secondaires (vérif détail + secondary_role).
        respect_deleted: ignorer les morceaux dont le genius_id est dans l'historique
            des suppressions (ne pas les réajouter).
        """
        self.get_tracks_button.configure(state="disabled", text="Récupération...")

        # Message de progression plus informatif
        features_text = "avec features" if include_features else "sans features"
        mode_text = "MàJ" if update_only else "complet"
        self.progress_label.configure(
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

                logger.info(f"Début récupération: max_songs={max_songs}, include_features={include_features}")

                # ✅ NOUVEAU : Charger les tracks existants AVANT la récupération
                existing_tracks = {}
                existing_by_title = {}  # Index par titre (insensible à la casse)
                if self.current_artist.tracks:
                    for track in self.current_artist.tracks:
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

                logger.info(f"📦 {len(self.current_artist.tracks)} morceaux déjà en base avant récupération")

                # Mode MàJ : exclure du prefill (album/media) les genius_id déjà en base
                known_genius_ids = None
                if update_only and self.current_artist.tracks:
                    known_genius_ids = {
                        t.genius_id for t in self.current_artist.tracks if t.genius_id
                    }
                    logger.info(f"🔄 MàJ : {len(known_genius_ids)} titres connus exclus du prefill API")

                # Récupérer les morceaux via l'API avec l'option features
                new_tracks = self.genius_api.get_artist_songs(
                    self.current_artist,
                    max_songs=max_songs,
                    include_features=include_features,
                    prefill=prefill,
                    known_genius_ids=known_genius_ids,
                    include_secondary=include_secondary
                )

                # Historique des suppressions : ne pas réajouter les morceaux supprimés
                if new_tracks:
                    deleted_ids = self.deleted_tracks_manager.load_deleted_ids(self.current_artist.name)
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
                                logger.info(f"🗂️ {skipped} morceau(x) supprimé(s) ignoré(s) (historique)")
                        else:
                            # Réautorisés à cet import → purge de l'historique
                            for t in new_tracks:
                                gid = _gid_int(t)
                                if gid in deleted_ids:
                                    self.deleted_tracks_manager.remove_deleted(self.current_artist.name, gid)

                if new_tracks:
                    # ✅ MERGE : Combiner les nouveaux tracks avec les existants
                    new_count = 0
                    updated_count = 0
                    duplicates_avoided = 0

                    for track in new_tracks:
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
                                    logger.warning(f"⚠️ Doublon de casse détecté pour '{track.title}': {len(candidates)} versions")
                                    duplicates_avoided += 1

                                # Prendre la première (ou la plus complète si on veut optimiser)
                                best_candidate = max(candidates, key=lambda t: (
                                    bool(t.album),
                                    bool(t.bpm),
                                    bool(getattr(t, 'lyrics', None)),
                                    len(t.credits)
                                ))
                                existing_track = best_candidate
                                updated_count += 1
                            else:
                                new_count += 1

                        # Si le morceau existe, fusionner les données
                        if existing_track:
                            # Préserver les données enrichies existantes (BPM, lyrics, crédits, etc.)
                            if not track.bpm and existing_track.bpm:
                                track.bpm = existing_track.bpm
                            if not hasattr(track, 'musical_key') and hasattr(existing_track, 'musical_key'):
                                track.musical_key = existing_track.musical_key
                            if not hasattr(track, 'lyrics') and hasattr(existing_track, 'lyrics'):
                                track.lyrics = existing_track.lyrics
                                track.has_lyrics = existing_track.has_lyrics
                            if not track.credits and existing_track.credits:
                                track.credits = existing_track.credits
                            if not hasattr(track, 'certifications') and hasattr(existing_track, 'certifications'):
                                track.certifications = existing_track.certifications
                            # Préserver l'ID de la base de données
                            track.id = existing_track.id

                    # Sauvegarder dans la base
                    saved_count = 0
                    for track in new_tracks:
                        try:
                            self.data_manager.save_track(track)
                            saved_count += 1
                        except Exception as e:
                            logger.warning(f"Erreur sauvegarde {track.title}: {e}")

                    # ✅ CORRECTION : Recharger TOUS les tracks depuis la base après sauvegarde
                    self.current_artist.tracks = self.data_manager.get_artist_tracks(self.current_artist.id)
                    self.tracks = self.current_artist.tracks

                    logger.info(f"✅ Merge terminé : {new_count} nouveaux, {updated_count} mis à jour, {saved_count} sauvegardés, {duplicates_avoided} doublons évités")

                    # Analyser les résultats
                    featuring_count = sum(1 for t in self.current_artist.tracks if hasattr(t, 'is_featuring') and t.is_featuring)
                    api_albums = sum(1 for t in new_tracks if t.album)
                    api_dates = sum(1 for t in new_tracks if t.release_date)

                    # Message de succès détaillé
                    success_msg = f"✅ {len(new_tracks)} morceaux récupérés pour {self.current_artist.name}"
                    success_msg += f"\n🆕 {new_count} nouveaux morceaux"
                    success_msg += f"\n🔄 {updated_count} morceaux mis à jour"

                    if duplicates_avoided > 0:
                        success_msg += f"\n🚫 {duplicates_avoided} doublons évités"

                    if featuring_count > 0:
                        success_msg += f"\n🎤 {featuring_count} morceaux en featuring (total)"

                    success_msg += f"\n💿 {api_albums} albums récupérés via l'API"
                    success_msg += f"\n📅 {api_dates} dates de sortie récupérées via l'API"
                    success_msg += f"\n💾 {saved_count} morceaux sauvegardés en base"
                    success_msg += f"\n📊 Total en base : {len(self.current_artist.tracks)} morceaux"

                    self.root.after(0, self._update_artist_info)
                    self.root.after(0, lambda: messagebox.showinfo("Succès", success_msg))

                    logger.info(f"Récupération terminée avec succès: {len(new_tracks)} nouveaux, total: {len(self.current_artist.tracks)} morceaux")
                    
                else:
                    self.root.after(0, lambda: messagebox.showwarning(
                        "Attention", 
                        "Aucun morceau trouvé.\n\nVérifiez le nom de l'artiste ou essayez avec les features activées."
                    ))
                    logger.warning("Aucun morceau trouvé")
                    
            except Exception as e:
                error_msg = str(e) if str(e) else "Erreur inconnue lors de la récupération"
                logger.error(f"Erreur lors de la récupération des morceaux: {error_msg}")
                self.root.after(0, lambda: messagebox.showerror(
                    "Erreur", 
                    f"Erreur lors de la récupération:\n{error_msg}"
                ))
            finally:
                self.root.after(0, lambda: self.get_tracks_button.configure(
                    state="normal",
                    text="Discographie"
                ))
                self.root.after(0, lambda: self.progress_label.configure(text=""))
        
        threading.Thread(target=get_tracks, daemon=True).start()

    # ✅ AJOUT DES MÉTHODES MANQUANTES POUR FONCTIONNALITÉS EXISTANTES

    def _get_track_id_from_index(self, index: int) -> Optional[int]:
        """Convertit un index de track en track ID"""
        if not self.current_artist or index < 0 or index >= len(self.current_artist.tracks):
            return None
        track = self.current_artist.tracks[index]
        return track.id

    def _is_track_disabled_by_index(self, index: int) -> bool:
        """Vérifie si un track est désactivé par son index"""
        track_id = self._get_track_id_from_index(index)
        if track_id is None:
            return False
        return track_id in self.disabled_tracks

    def _is_track_disabled(self, track: Track) -> bool:
        """Vérifie si un track est désactivé"""
        if track.id is None:
            return False
        return track.id in self.disabled_tracks

    def _get_track_status_icon(self, track: Track) -> str:
        """Retourne l'icône de statut selon le niveau de complétude des données

        Infos nécessaires pour validation complète:
        - Date de sortie ✓
        - Crédits obtenus ✓
        - Paroles obtenues ✓
        - BPM ✓
        - Key et Mode ✓
        - Durée ✓
        - Certifications ✓ (ou validation si base à jour)

        Note: Album n'est PAS obligatoire (singles, featurings hors projet)

        Retourne:
        - ❌ : Morceau désactivé
        - ⚠️ : Données incomplètes
        - ✅ : Toutes les infos présentes
        """
        try:
            # Si le morceau est désactivé, retourner ❌
            if self._is_track_disabled(track):
                return "❌"

            # Liste des champs requis avec leur validation
            missing = []

            # 1. Date de sortie
            if not hasattr(track, 'release_date') or not track.release_date:
                missing.append("Date")

            # 3. Crédits obtenus
            try:
                music_credits = track.get_music_credits()
                if not music_credits or len(music_credits) == 0:
                    missing.append("Crédits")
            except:
                missing.append("Crédits")

            # 4. Paroles obtenues
            if not hasattr(track, 'lyrics') or not track.lyrics or not track.lyrics.strip():
                missing.append("Paroles")

            # 5. BPM
            if not hasattr(track, 'bpm') or not track.bpm or track.bpm == 0:
                missing.append("BPM")

            # 6. Key et Mode
            has_key = hasattr(track, 'key') and track.key
            has_mode = hasattr(track, 'mode') and track.mode
            has_musical_key = hasattr(track, 'musical_key') and track.musical_key

            if not (has_musical_key or (has_key and has_mode)):
                missing.append("Key/Mode")

            # 7. Durée
            if not hasattr(track, 'duration') or not track.duration:
                missing.append("Durée")

            # 8. Certifications (validé si base à jour même sans certif)
            # On considère que si le champ 'certifications' existe (même vide), c'est que la recherche a été faite
            if not hasattr(track, 'certifications'):
                missing.append("Certifications")

            # Retourner le statut selon les données manquantes
            if len(missing) == 0:
                return "✅"  # Toutes les infos présentes
            else:
                return "⚠️"  # Données incomplètes

        except Exception as e:
            logger.error(f"Erreur dans _get_track_status_icon pour {getattr(track, 'title', 'unknown')}: {e}")
            return "⚠️"  # Erreur = incomplet

    def _show_scraping_menu(self):
        """Affiche le menu de sélection des options de scraping"""
        if not self.current_artist or not self.current_artist.tracks:
            messagebox.showwarning("Attention", "Aucun artiste ou morceaux chargés")
            return

        if not self.selected_tracks:
            messagebox.showwarning("Attention", "Aucun morceau sélectionné")
            return

        # Créer la fenêtre popup
        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Crédits & Paroles")
        dialog.geometry("500x500")

        ctk.CTkLabel(dialog, text="Sélectionnez les données à scraper:",
                    font=("Arial", 14, "bold")).pack(pady=15)

        # Frame principal pour les options
        options_frame = ctk.CTkFrame(dialog)
        options_frame.pack(fill="both", expand=True, padx=20, pady=10)

        # Variables pour les checkboxes
        scrape_genius_var = ctk.BooleanVar(value=True)  # Genius coché par défaut
        scrape_discogs_var = ctk.BooleanVar(value=True)  # Discogs coché par défaut
        force_credits_var = ctk.BooleanVar(value=False)
        # Sources paroles (uniformisé comme les crédits)
        lyrics_ytm_var = ctk.BooleanVar(value=True)     # YouTube Music (+ synchro) par défaut
        lyrics_genius_var = ctk.BooleanVar(value=True)  # Genius (scrape, fallback) par défaut
        force_lyrics_var = ctk.BooleanVar(value=False)

        # Section Crédits
        credits_frame = ctk.CTkFrame(options_frame)
        credits_frame.pack(fill="x", padx=15, pady=10)

        # Titre de section (non cliquable)
        ctk.CTkLabel(
            credits_frame,
            text="🎵 Scraper les crédits musicaux",
            font=("Arial", 13, "bold")
        ).pack(anchor="w", padx=10, pady=5)

        # Checkbox Genius
        genius_checkbox = ctk.CTkCheckBox(
            credits_frame,
            text="   Genius (crédits détaillés)",
            variable=scrape_genius_var,
            font=("Arial", 11)
        )
        genius_checkbox.pack(anchor="w", padx=30, pady=2)

        # Checkbox Discogs
        discogs_checkbox = ctk.CTkCheckBox(
            credits_frame,
            text="   Discogs (crédits complémentaires)",
            variable=scrape_discogs_var,
            font=("Arial", 11)
        )
        discogs_checkbox.pack(anchor="w", padx=30, pady=2)

        # Checkbox Mise à jour forcée
        force_credits_checkbox = ctk.CTkCheckBox(
            credits_frame,
            text="   🔄 Mise à jour forcée (re-scraper les crédits existants)",
            variable=force_credits_var,
            font=("Arial", 11)
        )
        force_credits_checkbox.pack(anchor="w", padx=30, pady=5)

        # Séparateur
        ctk.CTkFrame(options_frame, height=2, fg_color="gray").pack(fill="x", padx=20, pady=10)

        # Section Paroles (uniformisée : titre + sources, comme les crédits)
        lyrics_frame = ctk.CTkFrame(options_frame)
        lyrics_frame.pack(fill="x", padx=15, pady=10)

        ctk.CTkLabel(
            lyrics_frame,
            text="📝 Récupérer les paroles",
            font=("Arial", 13, "bold")
        ).pack(anchor="w", padx=10, pady=5)

        # Source YouTube Music (primaire, + synchro)
        lyrics_ytm_checkbox = ctk.CTkCheckBox(
            lyrics_frame,
            text="   YouTube Music (paroles + synchro)",
            variable=lyrics_ytm_var,
            font=("Arial", 11)
        )
        lyrics_ytm_checkbox.pack(anchor="w", padx=30, pady=2)

        # Source Genius (scrape, fallback)
        lyrics_genius_checkbox = ctk.CTkCheckBox(
            lyrics_frame,
            text="   Genius (scrape, fallback)",
            variable=lyrics_genius_var,
            font=("Arial", 11)
        )
        lyrics_genius_checkbox.pack(anchor="w", padx=30, pady=2)

        force_lyrics_checkbox = ctk.CTkCheckBox(
            lyrics_frame,
            text="   🔄 Mise à jour forcée (re-récupérer les paroles existantes)",
            variable=force_lyrics_var,
            font=("Arial", 11)
        )
        force_lyrics_checkbox.pack(anchor="w", padx=30, pady=5)

        ctk.CTkLabel(
            lyrics_frame,
            text="YTM en priorité (avec timestamps si dispo) ; Genius ne traite que les manquants.",
            font=("Arial", 9),
            text_color="gray"
        ).pack(anchor="w", padx=30, pady=(0, 5))

        # Frame pour les boutons
        button_frame = ctk.CTkFrame(dialog)
        button_frame.pack(fill="x", padx=20, pady=15)

        def start_scraping():
            scrape_genius = scrape_genius_var.get()
            scrape_discogs = scrape_discogs_var.get()
            force_credits = force_credits_var.get()
            lyrics_ytm = lyrics_ytm_var.get()
            lyrics_genius = lyrics_genius_var.get()
            scrape_lyrics = lyrics_ytm or lyrics_genius
            force_lyrics = force_lyrics_var.get()

            # Au moins une source de crédits ou de paroles doit être sélectionnée
            if not scrape_genius and not scrape_discogs and not scrape_lyrics:
                messagebox.showwarning("Attention", "Sélectionnez au moins une option de scraping")
                return

            dialog.destroy()

            # Lancer le scraping avec les options sélectionnées
            self._start_combined_scraping(
                scrape_genius=scrape_genius,
                scrape_discogs=scrape_discogs,
                force_credits=force_credits,
                scrape_lyrics=scrape_lyrics,
                lyrics_ytm=lyrics_ytm,
                lyrics_genius=lyrics_genius,
                force_lyrics=force_lyrics
            )

        ctk.CTkButton(
            button_frame,
            text="🚀 Lancer le scraping",
            command=start_scraping,
            width=200,
            height=40,
            font=("Arial", 13, "bold"),
            fg_color="green",
            hover_color="darkgreen"
        ).pack(side="left", padx=5)

        ctk.CTkButton(
            button_frame,
            text="Annuler",
            command=dialog.destroy,
            width=100,
            height=40
        ).pack(side="left", padx=5)

        # Centrer la fenêtre
        dialog.transient(self.root)
        dialog.grab_set()

    def _start_combined_scraping(self, scrape_genius=False, scrape_discogs=False, force_credits=False,
                                  scrape_lyrics=False, force_lyrics=False,
                                  lyrics_ytm=True, lyrics_genius=True):
        """Lance le scraping combiné des crédits (Genius/Discogs) et/ou paroles avec options de mise à jour forcée"""

        # Filtrer les morceaux sélectionnés ET actifs
        selected_tracks_list = []
        for i in sorted(self.selected_tracks):
            if not self._is_track_disabled_by_index(i):
                selected_tracks_list.append(self.current_artist.tracks[i])

        if not selected_tracks_list:
            messagebox.showwarning("Attention", "Tous les morceaux sélectionnés sont désactivés")
            return

        # Vérifier si déjà en cours
        if self.is_scraping:
            messagebox.showinfo("Scraping en cours", "Un scraping est déjà en cours. Veuillez patienter.")
            return

        # Message de confirmation
        disabled_count = len(self.selected_tracks) - len(selected_tracks_list)
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
        confirm_msg += f"\n⏱️ Temps estimé : ~{len(selected_tracks_list) * time_per_track:.0f}s"

        result = messagebox.askyesno("Crédits & Paroles", confirm_msg)

        if not result:
            return

        # Afficher la barre de progression
        self._show_progress_bar()
        self.is_scraping = True
        self._update_buttons_state()

        self.scrape_button.configure(state="disabled", text="Scraping...")
        self.progress_bar.set(0)

        def update_progress(current, total, track_name, task=""):
            """Callback de progression"""
            progress = current / total
            self.root.after(0, lambda: self.progress_var.set(progress))
            task_str = f" [{task}]" if task else ""
            self.root.after(0, lambda: self.progress_label.configure(
                text=f"{current}/{total}{task_str} - {track_name[:25]}..."
            ))

        def scrape():
            scraper = None
            genius_credits_results = None
            discogs_credits_results = None
            lyrics_results = None

            try:
                logger.info(f"Début du scraping combiné de {len(selected_tracks_list)} morceaux")

                total_tasks = (1 if scrape_genius else 0) + (1 if scrape_discogs else 0) + (1 if scrape_lyrics else 0)
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
                        progress_callback=lambda c, t, n: update_progress(c, t, n, "Genius")
                    )

                # Scraping Discogs crédits
                if scrape_discogs:
                    current_task += 1
                    logger.info(f"[{current_task}/{total_tasks}] Scraping des crédits Discogs...")

                    from src.api.discogs_api import DiscogsClient
                    import os

                    discogs_token = os.getenv('DISCOGS_TOKEN') or os.getenv('DISCOGS_USER_TOKEN')
                    discogs_client = DiscogsClient(user_token=discogs_token)

                    if force_credits:
                        # Effacer les crédits Discogs existants pour forcer le re-scraping
                        for track in selected_tracks_list:
                            # Garder les crédits Genius, supprimer uniquement ceux de Discogs
                            track.credits = [c for c in track.credits if c.source != "discogs"]

                    discogs_success = 0
                    discogs_failed = 0
                    for i, track in enumerate(selected_tracks_list, 1):
                        try:
                            update_progress(i, len(selected_tracks_list), track.title, "Discogs")

                            if discogs_client.enrich_track_data(track, force_update=force_credits):
                                discogs_success += 1
                            else:
                                discogs_failed += 1
                        except Exception as e:
                            logger.error(f"Erreur Discogs pour {track.title}: {e}")
                            discogs_failed += 1

                    discogs_credits_results = {'success': discogs_success, 'failed': discogs_failed}

                # Paroles : TEXTE structuré = Genius (primaire) ; TIMESTAMPS = YTM
                if scrape_lyrics:
                    current_task += 1
                    logger.info(f"[{current_task}/{total_tasks}] Récupération des paroles...")

                    if force_lyrics:
                        for track in selected_tracks_list:
                            track.lyrics = None
                            track.anecdotes = None
                            track.has_lyrics = False
                            track.lyrics_scraped_at = None
                            track.lyrics_source = None
                            track.lyrics_synced = None

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
                                progress_callback=lambda c, t, n: update_progress(c, t, n, "Paroles (Genius)")
                            )
                        for t in selected_tracks_list:
                            if t.has_lyrics and t.lyrics and not getattr(t, 'lyrics_source', None):
                                t.lyrics_source = 'genius'

                    # 2) TIMESTAMPS : YTM, TOUJOURS tenté (même si le texte structuré existe),
                    #    sans écraser le texte Genius. YTM sert aussi de fallback TEXTE.
                    if lyrics_ytm:
                        try:
                            from src.api.ytmusic_api import YTMusicAPI
                            ytm = YTMusicAPI()
                            ytm_synced, ytm_text = 0, 0
                            for i, track in enumerate(selected_tracks_list):
                                if getattr(track, 'lyrics_synced', None):
                                    continue  # synchro déjà présente
                                if getattr(track, 'is_featuring', False) and getattr(track, 'primary_artist_name', None):
                                    a_name = track.primary_artist_name
                                elif track.artist:
                                    a_name = track.artist.name
                                else:
                                    a_name = self.current_artist.name
                                res = ytm.get_lyrics(a_name, track.title)
                                if res:
                                    if res.get('lyrics_synced'):
                                        track.lyrics_synced = res['lyrics_synced']
                                        ytm_synced += 1
                                    # Fallback TEXTE seulement si Genius n'a rien donné
                                    if not (track.has_lyrics and track.lyrics) and res.get('lyrics'):
                                        track.lyrics = res['lyrics']
                                        track.has_lyrics = True
                                        track.lyrics_scraped_at = datetime.now()
                                        track.lyrics_source = res.get('source') or 'YouTube Music'
                                        ytm_text += 1
                                update_progress(i + 1, n_tracks, track.title, "Paroles (timestamps)")
                            logger.info(f"📝 YTM : {ytm_synced} synchro(s), {ytm_text} texte(s) fallback")
                        except Exception as e:
                            logger.warning(f"Passe timestamps YTM échouée: {e}")

                    if lyrics_results is None:
                        n_ok = sum(1 for t in selected_tracks_list if t.has_lyrics and t.lyrics)
                        lyrics_results = {'success': n_ok, 'failed': n_tracks - n_ok,
                                          'errors': [], 'lyrics_scraped': n_ok}

                # Sauvegarder les données mises à jour
                for track in selected_tracks_list:
                    track.artist = self.current_artist
                    self.data_manager.save_track(track)

                # Afficher le résumé
                success_msg = "Scraping terminé !\n\n"

                if genius_credits_results:
                    success_msg += "🎵 Crédits Genius:\n"
                    success_msg += f"  - Réussis: {genius_credits_results['success']}\n"
                    success_msg += f"  - Échoués: {genius_credits_results['failed']}\n"
                    if genius_credits_results.get('errors'):
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
                    if lyrics_results.get('errors'):
                        success_msg += f"  - Erreurs: {len(lyrics_results['errors'])}\n"

                if disabled_count > 0:
                    success_msg += f"\n⚠️ {disabled_count} morceaux désactivés ignorés"

                self.root.after(0, lambda: messagebox.showinfo("Scraping terminé", success_msg))

                # Mettre à jour l'affichage
                self.root.after(0, self._update_artist_info)
                self.root.after(0, self._update_statistics)

                # Rafraîchir la fenêtre de détails si elle est ouverte
                self.root.after(0, self._refresh_detail_window_if_open)

            except Exception as err:
                error_msg = str(err) if str(err) != "None" else "Erreur inconnue lors du scraping"
                logger.error(f"Erreur lors du scraping combiné: {error_msg}", exc_info=True)
                self.root.after(0, lambda: messagebox.showerror(
                    "Erreur",
                    f"Erreur lors du scraping: {error_msg}"
                ))
            finally:
                # S'assurer que le scraper est fermé
                if scraper:
                    try:
                        scraper.close()
                    except:
                        pass

                self.is_scraping = False
                self.root.after(0, lambda: self.scrape_button.configure(
                    state="normal",
                    text="Crédits & Paroles"
                ))
                self.root.after(0, self._hide_progress_bar)
                self.root.after(0, lambda: self.progress_label.configure(text=""))
                self.root.after(0, self._update_buttons_state)

        threading.Thread(target=scrape, daemon=True).start()

    def get_release_year_safely(self, track):
        """Récupère l'année de sortie de manière sécurisée"""
        if not track.release_date:
            return None
        
        # Si c'est déjà un objet datetime
        if hasattr(track.release_date, 'year'):
            return track.release_date.year
        
        # Si c'est une chaîne, essayer de l'analyser
        if isinstance(track.release_date, str):
            try:
                # Essayer différents formats de date
                from datetime import datetime
                
                # Format YYYY-MM-DD
                if len(track.release_date) >= 4:
                    year_str = track.release_date[:4]
                    if year_str.isdigit():
                        return int(year_str)
                
                # Essayer de parser comme datetime
                for fmt in ['%Y-%m-%d', '%Y/%m/%d', '%d/%m/%Y', '%m/%d/%Y', '%Y']:
                    try:
                        date_obj = datetime.strptime(track.release_date, fmt)
                        return date_obj.year
                    except ValueError:
                        continue
                        
            except Exception as e:
                logger.debug(f"Erreur parsing date '{track.release_date}': {e}")
        
        return None
    
    def _on_closing(self):
        """Gère la fermeture de l'application en sauvegardant les morceaux désactivés"""
        try:
            # Sauvegarder les morceaux désactivés avant de fermer
            if self.current_artist and self.disabled_tracks:
                self.disabled_tracks_manager.save_disabled_tracks(
                    self.current_artist.name, 
                    self.disabled_tracks
                )
                logger.info(f"Morceaux désactivés sauvegardés pour {self.current_artist.name}")
        except Exception as e:
            logger.error(f"Erreur lors de la sauvegarde à la fermeture: {e}")
        finally:
            self.root.destroy()

    def _get_enrichment_status(self, track):
        """Retourne le statut d'enrichissement d'un morceau"""
        try:
            # Vérifier la présence des crédits
            try:
                music_credits = track.get_music_credits()
                has_credits = len(music_credits) > 0 if music_credits else False
            except Exception:
                has_credits = False
            
            # Vérifier la présence des paroles
            try:
                has_lyrics = (hasattr(track, 'lyrics') and 
                            track.lyrics is not None and 
                            len(str(track.lyrics).strip()) > 0 and
                            str(track.lyrics).strip() not in ['None', 'NULL'])
            except Exception:
                has_lyrics = False
            
            # Vérifier la présence du BPM
            try:
                if isinstance(track.bpm, (int, float)):
                    has_bpm = track.bpm > 0
                elif isinstance(track.bpm, str):
                    has_bpm = track.bpm.isdigit() and int(track.bpm) > 0
                else:
                    has_bpm = False
            except Exception:
                has_bpm = False
            
            # Compter le nombre de types de données disponibles
            data_count = sum([has_credits, has_lyrics, has_bpm])
            
            if data_count == 0:
                return "❌"  # Aucune donnée
            elif data_count >= 3:
                return "✅"  # Données complètes
            else:
                return "⚠️"  # Données partielles
                
        except Exception as e:
            logger.error(f"Erreur dans _get_enrichment_status pour {getattr(track, 'title', 'unknown')}: {e}")
            return "❓"  # Erreur

    def _format_date(self, release_date):
        """Formate une date pour l'affichage en format français DD/MM/YYYY"""
        if not release_date:
            return "N/A"

        try:
            # Si c'est déjà un objet datetime
            if hasattr(release_date, 'strftime'):
                return release_date.strftime('%d/%m/%Y')

            # Si c'est une chaîne
            if isinstance(release_date, str):
                # Convertir de YYYY-MM-DD vers DD/MM/YYYY
                date_str = str(release_date)[:10]  # Prendre YYYY-MM-DD
                if len(date_str) == 10 and '-' in date_str:
                    try:
                        dt = datetime.strptime(date_str, '%Y-%m-%d')
                        return dt.strftime('%d/%m/%Y')
                    except:
                        pass
                # Si format ISO avec T
                if 'T' in str(release_date):
                    try:
                        dt = datetime.fromisoformat(str(release_date).replace('Z', '+00:00').split('T')[0])
                        return dt.strftime('%d/%m/%Y')
                    except:
                        pass
                return date_str

            return str(release_date)[:10]

        except Exception as e:
            logger.debug(f"Erreur formatage date '{release_date}': {e}")
            return "N/A"

    def _format_datetime(self, date_value):
        """Formate une date avec heure en format français DD/MM/YYYY à HH:MM"""
        if not date_value:
            return "N/A"

        try:
            # Si c'est déjà un objet datetime
            if hasattr(date_value, 'strftime'):
                return date_value.strftime('%d/%m/%Y à %H:%M')

            # Si c'est une chaîne
            if isinstance(date_value, str):
                # Format ISO avec T (ex: 2024-10-05T14:23:45)
                if 'T' in date_value:
                    try:
                        dt = datetime.fromisoformat(date_value.replace('Z', '+00:00'))
                        return dt.strftime('%d/%m/%Y à %H:%M')
                    except:
                        pass

                # Format YYYY-MM-DD HH:MM:SS
                if len(date_value) > 10 and ' ' in date_value:
                    try:
                        dt = datetime.strptime(date_value[:19], '%Y-%m-%d %H:%M:%S')
                        return dt.strftime('%d/%m/%Y à %H:%M')
                    except:
                        pass

                # Format court YYYY-MM-DD (sans heure)
                if len(date_value) == 10:
                    try:
                        dt = datetime.strptime(date_value, '%Y-%m-%d')
                        return dt.strftime('%d/%m/%Y')
                    except:
                        pass

                return date_value

            return str(date_value)

        except Exception as e:
            logger.debug(f"Erreur formatage datetime '{date_value}': {e}")
            return "N/A"

    def _update_buttons_state(self):
        """Met à jour l'état des boutons selon le contexte"""
        
        # Si un scraping est en cours, désactiver certains boutons
        if self.is_scraping:
            self.scrape_button.configure(state="disabled")
            if hasattr(self, 'force_update_button'):
                self.force_update_button.configure(state="disabled")
            if hasattr(self, 'get_tracks_button'):
                self.get_tracks_button.configure(state="disabled")
            if hasattr(self, 'stop_button'):
                self.stop_button.configure(state="normal")
            # On peut laisser export et autres boutons actifs pendant le scraping
            return  # Sortir ici pour ne pas changer les autres états
        
        # Si pas de scraping en cours, appliquer la logique normale
        if hasattr(self, 'stop_button'):
            self.stop_button.configure(state="disabled")
        
        if not self.current_artist:
            # Aucun artiste chargé
            self.get_tracks_button.configure(state="disabled")
            self.scrape_button.configure(state="disabled")
            self.export_button.configure(state="disabled")
            if hasattr(self, 'force_update_button'):
                self.force_update_button.configure(state="disabled")
            if hasattr(self, 'enrich_button'):
                self.enrich_button.configure(state="disabled")
            if hasattr(self, 'lyrics_button'):
                self.lyrics_button.configure(state="disabled")
            if hasattr(self, 'streams_button'):
                self.streams_button.configure(state="disabled")
        elif not self.current_artist.tracks:
            # Artiste chargé mais pas de morceaux
            self.get_tracks_button.configure(state="normal")
            self.scrape_button.configure(state="disabled")
            self.export_button.configure(state="disabled")
            if hasattr(self, 'force_update_button'):
                self.force_update_button.configure(state="disabled")
            if hasattr(self, 'enrich_button'):
                self.enrich_button.configure(state="disabled")
            if hasattr(self, 'lyrics_button'):
                self.lyrics_button.configure(state="disabled")
            if hasattr(self, 'streams_button'):
                self.streams_button.configure(state="disabled")
        else:
            # Artiste avec morceaux
            self.get_tracks_button.configure(state="normal")
            self.scrape_button.configure(state="normal")
            self.export_button.configure(state="normal")
            if hasattr(self, 'force_update_button'):
                self.force_update_button.configure(state="normal")
            if hasattr(self, 'enrich_button'):
                self.enrich_button.configure(state="normal")
            if hasattr(self, 'lyrics_button'):
                self.lyrics_button.configure(state="normal")
            if hasattr(self, 'streams_button'):
                self.streams_button.configure(state="normal")

    def _start_streams_update(self):
        """Ouvre le dialog de récupération des streams Spotify + YouTube Music."""
        if not self.current_artist:
            return

        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Nb Streams")
        dialog.geometry("380x300")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        ctk.CTkLabel(dialog, text="Sources de streams à récupérer :",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(pady=(18, 8))

        spotify_var = ctk.BooleanVar(value=True)
        ytm_var = ctk.BooleanVar(value=True)

        ctk.CTkCheckBox(dialog, text="Spotify (Kworb)", variable=spotify_var).pack(anchor="w", padx=40, pady=4)
        ctk.CTkCheckBox(dialog, text="YouTube Music", variable=ytm_var).pack(anchor="w", padx=40, pady=4)

        # Canal YTM épinglé (résout les homonymes : @handle, lien ou UC...)
        ctk.CTkLabel(dialog, text="Canal YTM (optionnel — @handle, lien ou UC...) :",
                     font=ctk.CTkFont(size=11)).pack(anchor="w", padx=40, pady=(10, 2))
        ytm_channel_entry = ctk.CTkEntry(
            dialog, width=290, placeholder_text="@ISHAOfficiel"
        )
        ytm_channel_entry.pack(padx=40, anchor="w")
        try:
            stored = self.data_manager.get_artist_ytm_channel(self.current_artist.id) \
                if self.current_artist else None
            if stored:
                ytm_channel_entry.insert(0, stored)
        except Exception:
            pass

        def launch():
            fetch_spotify = spotify_var.get()
            fetch_ytm = ytm_var.get()
            ytm_channel_raw = ytm_channel_entry.get().strip()
            dialog.destroy()
            if fetch_spotify or fetch_ytm:
                self._run_streams_update(fetch_spotify, fetch_ytm, ytm_channel_raw)

        ctk.CTkButton(dialog, text="Lancer", command=launch, width=120).pack(pady=18)

    def _run_streams_update(self, fetch_spotify: bool, fetch_ytm: bool,
                            ytm_channel_raw: str = ""):
        """Lance la récupération des streams dans un thread daemon."""
        if hasattr(self, 'streams_button'):
            self.streams_button.configure(state="disabled")

        def run():
            try:
                self.root.after(0, self._show_progress_bar)
                results = {}

                if fetch_spotify:
                    self.root.after(0, lambda: self.progress_label.configure(
                        text="Spotify (Kworb) en cours..."))
                    try:
                        from src.utils.update_kworb import update_kworb_streams
                        results['spotify'] = update_kworb_streams(
                            self.current_artist, self.data_manager)
                    except Exception as e:
                        results['spotify'] = {'error': str(e)}

                if fetch_ytm:
                    self.root.after(0, lambda: self.progress_label.configure(
                        text="YouTube Music en cours..."))
                    try:
                        from src.utils.update_ytmusic import update_ytmusic_streams
                        from src.api.ytmusic_api import YTMusicAPI

                        # Épingler le canal YTM saisi (@handle, lien ou UC...)
                        if ytm_channel_raw:
                            resolved = YTMusicAPI().resolve_channel(ytm_channel_raw)
                            if resolved:
                                self.data_manager.set_artist_ytm_channel(
                                    self.current_artist.id, resolved)
                            else:
                                logger.warning(
                                    f"Canal YTM non résolu: {ytm_channel_raw!r} — "
                                    "recherche automatique utilisée")

                        results['ytm'] = update_ytmusic_streams(
                            self.current_artist, self.data_manager)
                    except Exception as e:
                        results['ytm'] = {'error': str(e)}

                # Construire le message résumé
                lines = ["Récupération terminée !\n"]
                if 'spotify' in results:
                    r = results['spotify']
                    if 'error' in r:
                        lines.append(f"Spotify : ❌ {r['error']}")
                    else:
                        lines.append(
                            f"Spotify : {r.get('matched', 0)} matchés, "
                            f"{r.get('unmatched', 0)} non matchés, "
                            f"{r.get('albums_updated', 0)} albums"
                        )
                if 'ytm' in results:
                    r = results['ytm']
                    if 'error' in r:
                        lines.append(f"YouTube Music : ❌ {r['error']}")
                    else:
                        lines.append(
                            f"YouTube Music : {r.get('matched', 0)} matchés, "
                            f"{r.get('unmatched', 0)} non matchés, "
                            f"{r.get('albums_processed', 0)} albums"
                        )
                summary_msg = "\n".join(lines)

                self.root.after(0, lambda: messagebox.showinfo("Nb Streams", summary_msg))
                self.root.after(0, self._populate_tracks_table)
            except Exception as e:
                err_msg = f"Erreur inattendue : {e}"
                self.root.after(0, lambda: messagebox.showerror(
                    "Erreur Streams", err_msg))
            finally:
                self.root.after(0, self._hide_progress_bar)
                self.root.after(0, self._update_buttons_state)

        import threading
        threading.Thread(target=run, daemon=True).start()

    def _normalize_text(self, text: str) -> str:
        """Normalise le texte pour le tri (sans accents, minuscules)"""
        if not text:
            return ""
        # Supprimer les accents
        text = unicodedata.normalize('NFD', str(text))
        text = ''.join(char for char in text if unicodedata.category(char) != 'Mn')
        # Convertir en minuscules
        return text.lower()

    def _show_progress_bar(self):
        """Affiche la barre de progression"""
        if not self.progress_bar.winfo_ismapped():
            # Réafficher la barre avant le label
            self.progress_bar.pack(side="left", padx=10, before=self.progress_label)
        self.progress_bar.set(0)

    def _hide_progress_bar(self):
        """Cache la barre de progression"""
        if self.progress_bar.winfo_ismapped():
            self.progress_bar.pack_forget()
        self.progress_bar.set(0)
        self.progress_label.configure(text="")

    def _start_enrichment(self):
        """Lance l'enrichissement des données depuis toutes les sources"""
        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Sources d'enrichissement")
        dialog.geometry("450x750")  # Augmenté pour GetSongBPM + Deezer
        dialog.transient(self.root)
        dialog.grab_set()

        ctk.CTkLabel(dialog, text="Sélectionnez les sources à utiliser:",
                    font=("Arial", 14)).pack(pady=10)

        # Variables pour les checkboxes
        sources_vars = {}
        sources_info = {
            'spotify_id': 'Spotify ID Scraper (fallback automatique) 🎯',
            'reccobeats': 'ReccoBeats (BPM/Key/Mode via ISRC) 🎵',
            'getsongbpm': 'GetSongBPM API (2ᵉ vote BPM/Key/Mode) 🎹',
            'songbpm': 'SongBPM Scraper (départage BPM/Key) 🎼',
            'deezer': 'Deezer (ISRC, durée, date de sortie) 🎶',
            'discogs': 'Discogs (crédits supplémentaires, labels) 💿'
        }

        available = self.data_enricher.get_available_sources()

        for source, description in sources_info.items():
            var = ctk.BooleanVar(value=source in available)
            sources_vars[source] = var

            frame = ctk.CTkFrame(dialog)
            frame.pack(fill="x", padx=20, pady=5)

            checkbox = ctk.CTkCheckBox(
                frame,
                text=description,
                variable=var,
                state="normal" if source in available else "disabled"
            )
            checkbox.pack(anchor="w")

            if source not in available:
                ctk.CTkLabel(frame, text="(API non configurée)",
                        text_color="gray").pack(anchor="w", padx=25)

            # Info supplémentaire pour spotify_id
            if source == 'spotify_id':
                info_text = "Fallback : ReccoBeats résout d'abord via l'ISRC (Deezer).\nCe scraper n'est lancé que si aucun ISRC n'est trouvé — laisser coché suffit."
                ctk.CTkLabel(frame, text=info_text,
                        font=("Arial", 9), text_color="gray").pack(anchor="w", padx=25)

            # Info supplémentaire pour GetSongBPM
            if source == 'getsongbpm':
                info_text = "API rapide. Toujours interrogée pour le 2ᵉ vote BPM (recoupe ReccoBeats).\nNécessite clé API (GETSONGBPM_API_KEY)."
                ctk.CTkLabel(frame, text=info_text,
                        font=("Arial", 9), text_color="gray").pack(anchor="w", padx=25)

            # Info supplémentaire pour Deezer
            if source == 'deezer':
                info_text = "Fournit l'ISRC (pivot pour ReccoBeats), la durée et la date.\nVérifie aussi la cohérence des métadonnées."
                ctk.CTkLabel(frame, text=info_text,
                        font=("Arial", 9), text_color="gray").pack(anchor="w", padx=25)

        separator = ctk.CTkFrame(dialog, height=2, fg_color="gray")
        separator.pack(fill="x", padx=20, pady=15)

        force_frame = ctk.CTkFrame(dialog)
        force_frame.pack(fill="x", padx=20, pady=5)

        force_var = ctk.BooleanVar(value=False)
        force_checkbox = ctk.CTkCheckBox(
            force_frame,
            text="🔄 Forcer la mise à jour des données existantes",
            variable=force_var,
            font=("Arial", 12)
        )
        force_checkbox.pack(anchor="w", pady=5)

        info_label = ctk.CTkLabel(
            force_frame,
            text="Cochez pour re-scraper même si BPM/Key/Mode/Duration\nexistent déjà (utile pour corriger des données)",
            font=("Arial", 9),
            text_color="gray"
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
            font=("Arial", 12)
        )
        reset_spotify_checkbox.pack(anchor="w", pady=5)

        reset_info_label = ctk.CTkLabel(
            force_frame,
            text="Efface les Spotify IDs existants pour permettre\nleur re-scraping (utile si IDs incorrects)",
            font=("Arial", 9),
            text_color="gray"
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
            font=("Arial", 12)
        )
        clear_on_failure_checkbox.pack(anchor="w", pady=5)

        clear_info_label = ctk.CTkLabel(
            force_frame,
            text="Efface les BPM/Key/Mode/Duration erronés quand aucune\nsource ne trouve de nouvelles données (recommandé)",
            font=("Arial", 9),
            text_color="gray"
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
            self._run_enrichment(selected_sources, force_update=force_update,
                            reset_spotify_id=reset_spotify_id,
                            clear_on_failure=clear_on_failure)

        ctk.CTkButton(dialog, text="Démarrer", command=start_enrichment).pack(pady=20)

    def _run_enrichment(self, sources: List[str], force_update: bool = False,
                    reset_spotify_id: bool = False, clear_on_failure: bool = True):
        """Exécute l'enrichissement avec les sources sélectionnées"""
        if not self.selected_tracks:
            messagebox.showwarning("Attention", "Aucun morceau sélectionné")
            return

        selected_tracks_list = []
        for i in sorted(self.selected_tracks):
            if not self._is_track_disabled_by_index(i) and i < len(self.current_artist.tracks):
                selected_tracks_list.append(self.current_artist.tracks[i])

        if not selected_tracks_list:
            messagebox.showwarning("Attention", "Tous les morceaux sélectionnés sont désactivés")
            return

        # Reset des Spotify IDs si demandé
        if reset_spotify_id:
            for track in selected_tracks_list:
                if hasattr(track, 'spotify_id') and track.spotify_id:
                    old_id = track.spotify_id
                    track.spotify_id = None
                    logger.info(f"🔄 Spotify ID reset pour '{track.title}' (ancien: {old_id})")

        self.enrich_button.configure(state="disabled", text="Enrichissement...")
        self.progress_bar.set(0)

        def update_progress(current, total, info):
            """Callback de progression"""
            progress = current / total
            self.root.after(0, lambda: self.progress_var.set(progress))
            self.root.after(0, lambda: self.progress_label.configure(text=info))

        def enrich():
            try:
                # Préparer la liste complète des tracks de l'artiste pour validation
                all_artist_tracks = self.current_artist.tracks if self.current_artist else []

                # Compteurs pour le résumé
                cleaned_count = 0
                track_results = []  # Pour stocker les résultats détaillés par track

                # Enrichir chaque track individuellement
                for i, track in enumerate(selected_tracks_list):
                    update_progress(i, len(selected_tracks_list), f"Enrichissement: {track.title}")

                    results = self.data_enricher.enrich_track(
                        track,
                        sources=sources,
                        force_update=force_update,
                        artist_tracks=all_artist_tracks,
                        clear_on_failure=clear_on_failure
                    )

                    # Compter les nettoyages
                    if results.get('cleaned', False):
                        cleaned_count += 1

                    # Stocker les résultats pour ce track
                    track_results.append({
                        'title': track.title,
                        'results': results
                    })

                    # Sauvegarder après chaque enrichissement
                    self.data_manager.save_track(track)

                # Construire le message de fin avec détails par morceau
                disabled_count = len(self.selected_tracks) - len(selected_tracks_list)
                summary = "Enrichissement terminé!\n\n"
                summary += f"Morceaux traités: {len(selected_tracks_list)}\n\n"

                if force_update:
                    summary += "✅ Mode force update activé\n"

                if reset_spotify_id:
                    summary += "🔄 Spotify IDs réinitialisés\n"

                if clear_on_failure and cleaned_count > 0:
                    summary += f"🗑️ {cleaned_count} morceau(x) nettoyé(s) (données erronées effacées)\n"

                # Ajouter la légende
                summary += "\nDÉTAIL PAR MORCEAU:\n"
                summary += "Légende: ✓=succès | ✗=échec/absent | ?=crash/timeout | -=déjà présent\n\n"

                for track_result in track_results:
                    title = track_result['title']
                    results = track_result['results']

                    # Raccourcir le titre s'il est trop long
                    if len(title) > 30:
                        title = title[:27] + "..."

                    # Créer le résumé des sources
                    sources_summary = []

                    # Spotify ID (si demandé) - EN PREMIER
                    if 'spotify_id' in results:
                        if results['spotify_id'] == 'not_needed':
                            sp_status = "-"  # Déjà présent
                        elif results['spotify_id']:
                            sp_status = "✓"  # Trouvé
                        else:
                            sp_status = "✗"  # Échec
                        sources_summary.append(f"SP:{sp_status}")

                    # ReccoBeats (si demandé)
                    if 'reccobeats' in results:
                        if results['reccobeats'] is None:
                            rc_status = "?"
                        elif results['reccobeats']:
                            rc_status = "✓"
                        else:
                            rc_status = "✗"
                        sources_summary.append(f"RC:{rc_status}")

                    # GetSongBPM (si demandé)
                    if 'getsongbpm' in results:
                        if results['getsongbpm'] is None:
                            gs_status = "?"
                        elif results['getsongbpm'] == 'not_needed':
                            gs_status = "-"
                        elif results['getsongbpm']:
                            gs_status = "✓"
                        else:
                            gs_status = "✗"
                        sources_summary.append(f"GS:{gs_status}")

                    # SongBPM (si demandé)
                    if 'songbpm' in results:
                        if results['songbpm'] is None:
                            sb_status = "?"  # Crash/timeout
                        elif results['songbpm'] == 'not_needed':
                            sb_status = "-"  # Déjà présent
                        elif results['songbpm']:
                            sb_status = "✓"  # Succès
                        else:
                            sb_status = "✗"  # Pas de données
                        sources_summary.append(f"SB:{sb_status}")

                    # Deezer (si demandé)
                    if 'deezer' in results:
                        if results['deezer'] is None:
                            dz_status = "?"
                        elif results['deezer']:
                            dz_status = "✓"
                        else:
                            dz_status = "✗"
                        sources_summary.append(f"DZ:{dz_status}")

                    summary += f"• {title}\n  {' | '.join(sources_summary)}\n"

                if disabled_count > 0:
                    summary += f"\n⚠️ {disabled_count} morceaux désactivés ignorés"

                self.root.after(0, lambda: messagebox.showinfo("Enrichissement terminé", summary))
                self.root.after(0, self._update_artist_info)
                self.root.after(0, self._update_statistics)
                self.root.after(0, self._populate_tracks_table)

            except Exception as e:
                error_msg = str(e)
                logger.error(f"Erreur lors de l'enrichissement: {error_msg}")
                self.root.after(0, lambda: messagebox.showerror("Erreur", f"Erreur lors de l'enrichissement: {error_msg}"))
            finally:
                self.root.after(0, lambda: self.enrich_button.configure(
                    state="normal",
                    text="Enrichir données"
                ))
                self.root.after(0, lambda: self.progress_bar.set(0))
                self.root.after(0, lambda: self.progress_label.configure(text=""))

        threading.Thread(target=enrich, daemon=True).start()

    def _show_error(self, title, message):
        """Affiche un message d'erreur"""
        messagebox.showerror(title, message)

    def _refresh_detail_window_if_open(self):
        """Rafraîchit les fenêtres de détails ouvertes après un scraping"""
        if not self.open_detail_windows:
            return

        logger.info(f"🔄 Rafraîchissement de {len(self.open_detail_windows)} fenêtre(s) de détails")

        # Parcourir toutes les fenêtres ouvertes
        for track_id, (window, old_track) in list(self.open_detail_windows.items()):
            try:
                # Recharger le track depuis la base de données
                if self.current_artist:
                    refreshed_track = None
                    for track in self.current_artist.tracks:
                        if track.id == track_id:
                            refreshed_track = self.data_manager.get_track_by_id(track_id)
                            if refreshed_track:
                                # Mettre à jour l'artiste
                                refreshed_track.artist = self.current_artist
                                # Fermer l'ancienne fenêtre et en ouvrir une nouvelle
                                window.destroy()
                                del self.open_detail_windows[track_id]
                                self._show_track_details_for_track(refreshed_track)
                                logger.info(f"✅ Fenêtre rafraîchie pour: {refreshed_track.title}")
                            break
            except Exception as e:
                logger.warning(f"Erreur rafraîchissement fenêtre pour track_id {track_id}: {e}")

    def run(self):
        """Lance l'application"""
        self.root.mainloop()
