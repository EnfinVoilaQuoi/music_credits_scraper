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
from src.scrapers.genius_scraper import GeniusScraper
from src.utils.data_manager import DataManager
from src.utils.data_enricher import DataEnricher
from src.utils.logger import get_logger
from src.utils.youtube_integration import youtube_integration
from src.models import Artist, Track
from tkinter import ttk as tkinter_ttv
from src.utils.disabled_tracks_manager import DisabledTracksManager
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
            text="Récupérer les Morceaux",
            command=self._get_tracks,
            state="disabled",
            width=150
        )
        self.get_tracks_button.pack(side="left", padx=5)
        
        # 2. Scraper Crédits & Paroles (menu combiné)
        self.scrape_button = ctk.CTkButton(
            control_frame,
            text="Scraper Crédits & Paroles",
            command=self._show_scraping_menu,
            state="disabled",
            width=180
        )
        self.scrape_button.pack(side="left", padx=5)
        
        # 5. Enrichir données
        self.enrich_button = ctk.CTkButton(
            control_frame,
            text="Enrichir Données",
            command=self._start_enrichment,
            state="disabled",
            width=150
        )
        self.enrich_button.pack(side="left", padx=5)

        # 6. Bouton de mise à jour des certifications
        self.update_certif_button = ctk.CTkButton(
            control_frame,
            text="📊 MàJ Certifs",
            command=self._open_certification_update,
            width=150,
            fg_color="darkgreen",
            hover_color="green"
        )
        self.update_certif_button.pack(side="left", padx=5)
        
        # 7. Exporter
        self.export_button = ctk.CTkButton(
            control_frame,
            text="Exporter",
            command=self._export_data,
            state="disabled",
            width=100
        )
        self.export_button.pack(side="left", padx=5)
        
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
        
        ctk.CTkButton(
            selection_frame,
            text="Réactiver sélectionnés",
            command=self._enable_selected_tracks,
            width=140,
            fg_color="green",
            hover_color="darkgreen"
        ).pack(side="left", padx=5)
        
        self.selected_count_label = ctk.CTkLabel(selection_frame, text="")
        self.selected_count_label.pack(side="left", padx=20)
        
        # Créer le Treeview dans un conteneur approprié
        tree_container = ctk.CTkFrame(table_frame)
        tree_container.pack(fill="both", expand=True)
        
        tree_scroll_frame = ctk.CTkFrame(tree_container)
        tree_scroll_frame.pack(fill="both", expand=True)
        
        # COLONNES AVEC COLONNE PAROLES ENTRE CRÉDITS ET BPM + DURÉE ENTRE BPM ET CERTIF
        columns = ("Titre", "Artiste principal", "Album", "Date sortie", "Crédits", "Paroles", "BPM", "Durée", "Certif.", "Statut")
        self.tree = ttk.Treeview(tree_scroll_frame, columns=columns, show="tree headings", height=15)
        
        # Configuration des colonnes avec tri
        self.tree.heading("#0", text="✓")
        self.tree.column("#0", width=50, stretch=False)
        
        # Variable pour suivre l'ordre de tri
        self.sort_reverse = {}
        
        for col in columns:
            self.tree.heading(col, text=col, command=lambda c=col: self._sort_column(c))
            if col == "Titre":
                self.tree.column(col, width=220)
            elif col == "Artiste principal":
                self.tree.column(col, width=130)
            elif col == "Album":
                self.tree.column(col, width=160)  # +30 pixels
            elif col == "Date sortie":
                self.tree.column(col, width=80)  # -10 pixels
            elif col == "Crédits":
                self.tree.column(col, width=70, anchor="center")  # CENTRÉ
            elif col == "Paroles":
                self.tree.column(col, width=60, anchor="center")  # CENTRÉ - -10 pixels
            elif col == "BPM":
                self.tree.column(col, width=90)  # +20 pixels
            elif col == "Durée":
                self.tree.column(col, width=70, anchor="center")  # CENTRÉ
            elif col == "Certif.":
                self.tree.column(col, width=50, anchor="center")  # -10 pixels, CENTRÉ
            else:  # Statut
                self.tree.column(col, width=70)
        
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
        self.tree.bind("<Button-3>", self._on_right_click)  # Clic droit pour menu contextuel
        
        # === Section statistiques ===
        stats_frame = ctk.CTkFrame(main_frame)
        stats_frame.pack(fill="x", padx=5, pady=5)
        
        self.stats_label = ctk.CTkLabel(stats_frame, text="", font=("Arial", 12))
        self.stats_label.pack()

    def _populate_tracks_table(self):
        """Remplit le tableau avec les morceaux - VERSION CORRIGÉE CRÉDITS"""
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
                        credits_display,  # CORRECTION: Affiche le nombre
                        lyrics_display,
                        bpm,
                        duration_display,  # NOUVELLE COLONNE DURÉE
                        certif_display,
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
                        else:
                            self.selected_tracks.add(index)
                            self.tree.item(item, text="☑")
                        self.last_selected_index = index

                    self._update_selection_count()

    def _on_right_click(self, event):
        """Menu contextuel sur clic droit avec actualisation immédiate"""
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
        window_height = "900" if has_lyrics else "700"
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
        
        if is_featuring:
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

                # Debug: Vérifier si spotify_page_title existe
                logger.debug(f"Track {track.title}: hasattr={hasattr(track, 'spotify_page_title')}, value={getattr(track, 'spotify_page_title', 'NOT_SET')}")

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

        youtube_result = youtube_integration.get_youtube_link_for_track(
            artist_name, track.title, track.album, release_year
        )

        # Affichage selon le type de résultat
        if youtube_result['type'] == 'direct':
            # Lien direct trouvé automatiquement
            label_text = f"▶️ Voir (auto • {youtube_result['confidence']:.0%})"
            label_color = "#FF0000"  # Rouge YouTube
            tooltip_text = (f"Lien automatique sélectionné\n"
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
        
        # === ONGLET 3: PAROLES ===
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
        
        # Popularité
        if hasattr(track, 'popularity') and track.popularity:
            tech_textbox.insert("end", f"📈 Popularité: {track.popularity}\n")
        
        # Artwork
        if getattr(track, 'artwork_url', None):
            tech_textbox.insert("end", f"🖼️ Artwork: {track.artwork_url}\n")
        
        # Métadonnées de scraping
        tech_textbox.insert("end", f"\n📅 HISTORIQUE:\n")
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
            from src.api.snep_certifications import get_snep_manager
            snep_manager = get_snep_manager()

            # Récupérer TOUTES les certifications du morceau
            track_certs = snep_manager.get_track_certifications(
                self.current_artist.name,
                track.title
            )

            # Récupérer les certifications de l'album si disponible
            album_certs = []
            if track.album:
                album_certs = snep_manager.get_album_certifications(
                    self.current_artist.name,
                    track.album
                )

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

                cert_text = ""

                # SECTION 1: Certifications du morceau
                if track_certs:
                    cert_text += "🎵 CERTIFICATIONS DU MORCEAU\n"
                    cert_text += "=" * 60 + "\n\n"

                    for i, cert_data in enumerate(track_certs, 1):
                        cert_level = cert_data.get('certification', '')
                        emoji = emoji_map.get(cert_level, '🏆')

                        cert_text += f"{emoji} CERTIFICATION #{i}: {cert_level.upper()}\n"
                        cert_text += "-" * 60 + "\n"
                        cert_text += f"📀 Titre: {cert_data.get('title', '')}\n"
                        cert_text += f"🎤 Artiste: {cert_data.get('artist_name', '')}\n"
                        cert_text += f"📂 Catégorie: {cert_data.get('category', '')}\n"
                        cert_text += f"📅 Date de sortie: {self._format_date(cert_data.get('release_date', 'N/A'))}\n"
                        cert_text += f"✅ Date de constat: {self._format_date(cert_data.get('certification_date', 'N/A'))}\n"
                        cert_text += f"🏢 Éditeur: {cert_data.get('publisher', 'N/A')}\n"

                        # Calculer la durée d'obtention
                        if cert_data.get('release_date') and cert_data.get('certification_date'):
                            try:
                                from datetime import datetime
                                release_str = str(cert_data['release_date'])[:10]
                                certif_str = str(cert_data['certification_date'])[:10]
                                release = datetime.strptime(release_str, '%Y-%m-%d')
                                certif = datetime.strptime(certif_str, '%Y-%m-%d')
                                duration = (certif - release).days
                                cert_text += f"⏱️ Durée d'obtention: {duration} jours ({duration // 365} ans, {(duration % 365) // 30} mois)\n"
                            except Exception as e:
                                logger.debug(f"Erreur calcul durée: {e}")

                        cert_text += "\n"

                # SECTION 2: Certifications de l'album
                if album_certs:
                    cert_text += "\n💿 CERTIFICATIONS DE L'ALBUM\n"
                    cert_text += "=" * 60 + "\n"
                    cert_text += f"📂 Album: {track.album}\n\n"

                    for i, cert_data in enumerate(album_certs, 1):
                        cert_level = cert_data.get('certification', '')
                        emoji = emoji_map.get(cert_level, '🏆')

                        cert_text += f"{emoji} CERTIFICATION #{i}: {cert_level.upper()}\n"
                        cert_text += "-" * 60 + "\n"
                        cert_text += f"💿 Album: {cert_data.get('title', '')}\n"
                        cert_text += f"🎤 Artiste: {cert_data.get('artist_name', '')}\n"
                        cert_text += f"📂 Catégorie: {cert_data.get('category', '')}\n"
                        cert_text += f"📅 Date de sortie: {self._format_date(cert_data.get('release_date', 'N/A'))}\n"
                        cert_text += f"✅ Date de constat: {self._format_date(cert_data.get('certification_date', 'N/A'))}\n"
                        cert_text += f"🏢 Éditeur: {cert_data.get('publisher', 'N/A')}\n"

                        # Calculer la durée d'obtention pour l'album
                        if cert_data.get('release_date') and cert_data.get('certification_date'):
                            try:
                                from datetime import datetime
                                release_str = str(cert_data['release_date'])[:10]
                                certif_str = str(cert_data['certification_date'])[:10]
                                release = datetime.strptime(release_str, '%Y-%m-%d')
                                certif = datetime.strptime(certif_str, '%Y-%m-%d')
                                duration = (certif - release).days
                                cert_text += f"⏱️ Durée d'obtention: {duration} jours ({duration // 365} ans, {(duration % 365) // 30} mois)\n"
                            except Exception as e:
                                logger.debug(f"Erreur calcul durée album: {e}")

                        cert_text += "\n"

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
            
            # Ouvrir la fenêtre
            dialog = CertificationUpdateDialog(self.root, cert_manager)
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
                    
                    # Artiste trouvé en base, l'utiliser directement
                    self.current_artist = artist
                    self.root.after(0, self._update_artist_info)
                    self.root.after(0, lambda: messagebox.showinfo(
                        "Artiste chargé", 
                        f"✅ Artiste '{artist.name}' chargé depuis la base de données\n"
                        f"📀 {len(artist.tracks)} morceaux disponibles\n"
                        f"🎤 ID Genius: {artist.genius_id}\n\n"
                        "Vous pouvez maintenant scraper ou enrichir les données."
                    ))
                    return
                
                # Seulement si pas trouvé en base, chercher sur Genius
                logger.info(f"🌐 Artiste non trouvé en base, recherche sur Genius...")
                
                # Rechercher sur Genius avec gestion d'erreur robuste
                try:
                    genius_artist = self.genius_api.search_artist(artist_name)
                    
                    if genius_artist:
                        # Sauvegarder le nouvel artiste dans la base
                        self.data_manager.save_artist(genius_artist)
                        self.current_artist = genius_artist
                        
                        self.root.after(0, self._update_artist_info)
                        self.root.after(0, lambda: messagebox.showinfo(
                            "Nouvel artiste trouvé", 
                            f"✅ Nouvel artiste trouvé: '{genius_artist.name}'\n"
                            f"🎤 ID Genius: {genius_artist.genius_id}\n\n"
                            "Cliquez sur 'Récupérer les morceaux' pour commencer."
                        ))
                    else:
                        self.root.after(0, lambda: messagebox.showwarning(
                            "Non trouvé", 
                            f"❌ Aucun artiste trouvé pour '{artist_name}'\n\n"
                            "Vérifiez l'orthographe ou essayez un autre nom."
                        ))
                        
                except Exception as api_error:
                    logger.error(f"❌ Erreur API Genius: {api_error}")
                    
                    self.root.after(0, lambda: messagebox.showerror(
                        "Erreur API", 
                        f"❌ Problème avec l'API Genius:\n{str(api_error)}\n\n"
                        "Solutions possibles:\n"
                        "• Vérifiez votre connexion internet\n"
                        "• Vérifiez votre clé API GENIUS_API_KEY\n"
                        "• Réessayez dans quelques minutes"
                    ))

            except Exception as e:
                error_msg = str(e) if str(e) else "Erreur inconnue lors de la recherche"
                logger.error(f"❌ Erreur lors de la recherche: {error_msg}")
                logger.error(f"Type d'erreur: {type(e).__name__}")
                
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
        for artist_info in artists_data:
            # Frame pour chaque artiste
            artist_frame = ctk.CTkFrame(scrollable_frame)
            artist_frame.pack(fill="x", padx=5, pady=5)
            
            # Fonction pour sélectionner un artiste
            def select_artist(name, widget, frame=artist_frame):
                # Désélectionner l'ancien
                if selected_artist["widget"]:
                    selected_artist["widget"].configure(fg_color="transparent")

                # Sélectionner le nouveau
                selected_artist["name"] = name
                selected_artist["widget"] = frame
                frame.configure(fg_color=("gray70", "gray30"))

            # Fonction pour charger directement avec double-clic
            def load_on_double_click(name):
                self.artist_entry.delete(0, "end")
                self.artist_entry.insert(0, name)
                dialog.destroy()
                self._search_artist()

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
                command=lambda n=artist_info['name'], w=artist_frame: select_artist(n, w),
                fg_color="transparent",
                text_color=("black", "white"),
                hover_color=("gray80", "gray40"),
                anchor="w",
                height=60
            )
            artist_button.pack(fill="x", padx=5, pady=5)

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

    def _update_artist_info(self):
        """Met à jour les informations de l'artiste - VERSION AVEC DÉCOMPTES"""
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
                                            if (hasattr(t, 'bpm') and t.bpm and t.bpm > 0) and
                                            ((hasattr(t, 'musical_key') and t.musical_key) or
                                            (hasattr(t, 'key') and t.key and hasattr(t, 'mode') and t.mode)) and
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

                # ✅ LIGNE 2: Données manquantes uniquement (désactivés déjà affiché dans la sélection)
                line2 = f"{tracks_with_missing_data} Morceaux avec Données manquantes"

                # Combiner avec retour à la ligne
                info_text = f"{line1}\n{line2}"
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
            from src.scrapers.genius_scraper import GeniusScraper

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
        dialog.geometry("450x500")
        
        # Centrer la fenêtre
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() // 2) - (225)
        y = (dialog.winfo_screenheight() // 2) - (250)
        dialog.geometry(f"450x500+{x}+{y}")
        
        dialog.lift()
        dialog.focus_force()
        dialog.grab_set()
        
        # Variables pour les options
        include_features_var = ctk.BooleanVar(value=True)  # Par défaut, inclure les features
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
        
        def start_retrieval():
            try:
                max_songs = int(max_songs_entry.get())
                if max_songs <= 0:
                    max_songs = 300
            except ValueError:
                max_songs = 300
            
            include_features = include_features_var.get()
            dialog.destroy()
            self._start_track_retrieval(max_songs, include_features)
        
        def cancel():
            dialog.destroy()
        
        ctk.CTkButton(button_frame, text="🎵 Récupérer", 
                 command=start_retrieval, width=130, height=35).pack(side="left", padx=10)
        ctk.CTkButton(button_frame, text="❌ Annuler", 
                 command=cancel, width=130, height=35).pack(side="right", padx=10)

    def _start_track_retrieval(self, max_songs: int, include_features: bool):
        """Lance la récupération des morceaux avec les options choisies"""
        self.get_tracks_button.configure(state="disabled", text="Récupération...")
        
        # Message de progression plus informatif
        features_text = "avec features" if include_features else "sans features"
        self.progress_label.configure(
            text=f"Récupération de max {max_songs} morceaux ({features_text})..."
        )
        
        def get_tracks():
            try:
                logger.info(f"Début récupération: max_songs={max_songs}, include_features={include_features}")
                
                # Récupérer les morceaux via l'API avec l'option features
                tracks = self.genius_api.get_artist_songs(
                    self.current_artist, 
                    max_songs=max_songs,
                    include_features=include_features
                )
                
                if tracks:
                    # Sauvegarder dans la base
                    saved_count = 0
                    for track in tracks:
                        try:
                            self.data_manager.save_track(track)
                            saved_count += 1
                        except Exception as e:
                            logger.warning(f"Erreur sauvegarde {track.title}: {e}")
                    
                    self.current_artist.tracks = tracks
                    self.tracks = tracks
                    
                    # Analyser les résultats
                    featuring_count = sum(1 for t in tracks if hasattr(t, 'is_featuring') and t.is_featuring)
                    api_albums = sum(1 for t in tracks if t.album)
                    api_dates = sum(1 for t in tracks if t.release_date)
                    
                    # Message de succès détaillé
                    success_msg = f"✅ {len(tracks)} morceaux récupérés pour {self.current_artist.name}"
                    
                    if featuring_count > 0:
                        success_msg += f"\n🎤 {featuring_count} morceaux en featuring"
                    
                    success_msg += f"\n💿 {api_albums} albums récupérés via l'API"
                    success_msg += f"\n📅 {api_dates} dates de sortie récupérées via l'API"
                    success_msg += f"\n💾 {saved_count} morceaux sauvegardés en base"
                    
                    self.root.after(0, self._update_artist_info)
                    self.root.after(0, lambda: messagebox.showinfo("Succès", success_msg))
                    
                    logger.info(f"Récupération terminée avec succès: {len(tracks)} morceaux")
                    
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
                    text="Récupérer les morceaux"
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
        dialog.title("Scraper Crédits & Paroles")
        dialog.geometry("500x400")

        ctk.CTkLabel(dialog, text="Sélectionnez les données à scraper:",
                    font=("Arial", 14, "bold")).pack(pady=15)

        # Frame principal pour les options
        options_frame = ctk.CTkFrame(dialog)
        options_frame.pack(fill="both", expand=True, padx=20, pady=10)

        # Variables pour les checkboxes
        scrape_credits_var = ctk.BooleanVar(value=False)
        force_credits_var = ctk.BooleanVar(value=False)
        scrape_lyrics_var = ctk.BooleanVar(value=False)
        force_lyrics_var = ctk.BooleanVar(value=False)

        # Section Crédits
        credits_frame = ctk.CTkFrame(options_frame)
        credits_frame.pack(fill="x", padx=15, pady=10)

        credits_checkbox = ctk.CTkCheckBox(
            credits_frame,
            text="🎵 Scraper les crédits musicaux",
            variable=scrape_credits_var,
            font=("Arial", 13, "bold")
        )
        credits_checkbox.pack(anchor="w", padx=10, pady=5)

        force_credits_checkbox = ctk.CTkCheckBox(
            credits_frame,
            text="   🔄 Mise à jour forcée (re-scraper les crédits existants)",
            variable=force_credits_var,
            font=("Arial", 11)
        )
        force_credits_checkbox.pack(anchor="w", padx=30, pady=2)

        ctk.CTkLabel(
            credits_frame,
            text="Les crédits incluent : producteurs, compositeurs, etc.",
            font=("Arial", 9),
            text_color="gray"
        ).pack(anchor="w", padx=30, pady=(0, 5))

        # Séparateur
        ctk.CTkFrame(options_frame, height=2, fg_color="gray").pack(fill="x", padx=20, pady=10)

        # Section Paroles
        lyrics_frame = ctk.CTkFrame(options_frame)
        lyrics_frame.pack(fill="x", padx=15, pady=10)

        lyrics_checkbox = ctk.CTkCheckBox(
            lyrics_frame,
            text="📝 Scraper les paroles",
            variable=scrape_lyrics_var,
            font=("Arial", 13, "bold"),
        )
        lyrics_checkbox.pack(anchor="w", padx=10, pady=5)

        force_lyrics_checkbox = ctk.CTkCheckBox(
            lyrics_frame,
            text="   🔄 Mise à jour forcée (re-scraper les paroles existantes)",
            variable=force_lyrics_var,
            font=("Arial", 11)
        )
        force_lyrics_checkbox.pack(anchor="w", padx=30, pady=2)

        ctk.CTkLabel(
            lyrics_frame,
            text="Les paroles complètes + anecdotes depuis Genius",
            font=("Arial", 9),
            text_color="gray"
        ).pack(anchor="w", padx=30, pady=(0, 5))

        # Frame pour les boutons
        button_frame = ctk.CTkFrame(dialog)
        button_frame.pack(fill="x", padx=20, pady=15)

        def start_scraping():
            scrape_credits = scrape_credits_var.get()
            force_credits = force_credits_var.get()
            scrape_lyrics = scrape_lyrics_var.get()
            force_lyrics = force_lyrics_var.get()

            if not scrape_credits and not scrape_lyrics:
                messagebox.showwarning("Attention", "Sélectionnez au moins une option de scraping")
                return

            dialog.destroy()

            # Lancer le scraping avec les options sélectionnées
            self._start_combined_scraping(
                scrape_credits=scrape_credits,
                force_credits=force_credits,
                scrape_lyrics=scrape_lyrics,
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

    def _start_combined_scraping(self, scrape_credits=False, force_credits=False,
                                  scrape_lyrics=False, force_lyrics=False):
        """Lance le scraping combiné des crédits et/ou paroles avec options de mise à jour forcée"""

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
        if scrape_credits:
            tasks.append(f"Crédits{'(forcé)' if force_credits else ''}")
        if scrape_lyrics:
            tasks.append(f"Paroles{'(forcé)' if force_lyrics else ''}")

        confirm_msg = f"Scraping de {', '.join(tasks)}\n\n"
        confirm_msg += f"📊 Morceaux: {len(selected_tracks_list)}\n"
        if disabled_count > 0:
            confirm_msg += f"⚠️ {disabled_count} désactivés ignorés\n"
        confirm_msg += f"\n⏱️ Temps estimé : ~{len(selected_tracks_list) * (3 if scrape_credits else 0 + 2 if scrape_lyrics else 0):.0f}s"

        result = messagebox.askyesno("Scraping Crédits & Paroles", confirm_msg)

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
            credits_results = None
            lyrics_results = None

            try:
                logger.info(f"Début du scraping combiné de {len(selected_tracks_list)} morceaux")
                scraper = GeniusScraper(headless=True)

                total_tasks = (1 if scrape_credits else 0) + (1 if scrape_lyrics else 0)
                current_task = 0

                # Scraping des crédits
                if scrape_credits:
                    current_task += 1
                    logger.info(f"[{current_task}/{total_tasks}] Scraping des crédits...")

                    if force_credits:
                        # Effacer les crédits existants pour forcer le re-scraping
                        for track in selected_tracks_list:
                            track.music_credits = []
                            track.credits_scraped_at = None

                    credits_results = scraper.scrape_multiple_tracks(
                        selected_tracks_list,
                        progress_callback=lambda c, t, n: update_progress(c, t, n, "Crédits")
                    )

                # Scraping des paroles
                if scrape_lyrics:
                    current_task += 1
                    logger.info(f"[{current_task}/{total_tasks}] Scraping des paroles...")

                    if force_lyrics:
                        # Effacer les paroles existantes pour forcer le re-scraping
                        for track in selected_tracks_list:
                            track.lyrics = None
                            track.anecdotes = None
                            track.has_lyrics = False
                            track.lyrics_scraped_at = None

                    lyrics_results = scraper.scrape_lyrics_batch(
                        selected_tracks_list,
                        progress_callback=lambda c, t, n: update_progress(c, t, n, "Paroles")
                    )

                # Sauvegarder les données mises à jour
                for track in selected_tracks_list:
                    track.artist = self.current_artist
                    self.data_manager.save_track(track)

                # Afficher le résumé
                success_msg = "Scraping terminé !\n\n"

                if credits_results:
                    success_msg += "🎵 Crédits:\n"
                    success_msg += f"  - Réussis: {credits_results['success']}\n"
                    success_msg += f"  - Échoués: {credits_results['failed']}\n"
                    if credits_results.get('errors'):
                        success_msg += f"  - Erreurs: {len(credits_results['errors'])}\n"
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
                    text="Scraper Crédits & Paroles"
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
                has_bpm = (track.bpm is not None and track.bpm > 0)
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
        dialog.geometry("450x700")  # Augmenté pour Deezer

        ctk.CTkLabel(dialog, text="Sélectionnez les sources à utiliser:",
                    font=("Arial", 14)).pack(pady=10)

        # Variables pour les checkboxes
        sources_vars = {}
        sources_info = {
            'spotify_id': 'Spotify ID Scraper (Recherche les vrais Track IDs) 🎯',
            'reccobeats': 'ReccoBeats (BPM, features audio complètes) 🎵',
            'songbpm': 'SongBPM (BPM de fallback) 🎼',
            'deezer': 'Deezer (Duration, Release Date - vérification) 🎶',  # NOUVEAU
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
                info_text = "Utilise la recherche web pour trouver les Track IDs corrects.\nRecommandé quand des IDs incorrects ont été attribués."
                ctk.CTkLabel(frame, text=info_text,
                        font=("Arial", 9), text_color="gray").pack(anchor="w", padx=25)

            # NOUVEAU: Info supplémentaire pour Deezer
            if source == 'deezer':
                info_text = "Vérifie la cohérence des durées et dates de sortie.\nEnrichit avec les métadonnées Deezer."
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
            if i not in self.disabled_tracks:
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