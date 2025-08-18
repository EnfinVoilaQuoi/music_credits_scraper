"""Interface graphique principale de l'application - VERSION AMÉLIORÉE"""
import customtkinter as ctk
import tkinter  # ✅ AJOUTÉ: Pour le menu contextuel
from tkinter import ttk, messagebox, filedialog
import threading
from typing import Optional, List
from datetime import datetime

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
        self.data_enricher = DataEnricher()
        self.current_artist: Optional[Artist] = None
        self.tracks: List[Track] = []
        
        # Variables
        self.is_scraping = False
        self.selected_tracks = set()  # Stocker les morceaux sélectionnés
        self.disabled_track = set()  # Stocker les morceaux désactivés
        self.last_selected_index = None  # Sélection multiple
        self.disabled_tracks_manager = DisabledTracksManager()
        
        self._create_widgets()
        self._update_statistics()

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
        
        # === Section contrôles - ORDRE RÉORGANISÉ ===
        control_frame = ctk.CTkFrame(main_frame)
        control_frame.pack(fill="x", padx=5, pady=5)
        
        # ORDRE SOUHAITÉ :
        # 1. Récupérer les morceaux
        self.get_tracks_button = ctk.CTkButton(
            control_frame,
            text="Récupérer les morceaux",
            command=self._get_tracks,
            state="disabled",
            width=150
        )
        self.get_tracks_button.pack(side="left", padx=5)
        
        # 2. Scraper crédits
        self.scrape_button = ctk.CTkButton(
            control_frame,
            text="Scraper crédits",
            command=self._start_scraping,
            state="disabled",
            width=150
        )
        self.scrape_button.pack(side="left", padx=5)
        
        # 3. Scraper paroles
        self.lyrics_button = ctk.CTkButton(
            control_frame,
            text="Scraper paroles",
            command=self._start_lyrics_scraping,
            state="disabled",
            width=150,
            fg_color="purple",
            hover_color="darkmagenta"
        )
        self.lyrics_button.pack(side="left", padx=5)
        
        # 4. Mise à jour forcée
        self.force_update_button = ctk.CTkButton(
            control_frame,
            text="Mise à jour forcée",
            command=self._force_update_selected,
            state="disabled",
            width=150,
            fg_color="orange",
            hover_color="darkorange"
        )
        self.force_update_button.pack(side="left", padx=5)
        
        # 5. Enrichir données
        self.enrich_button = ctk.CTkButton(
            control_frame,
            text="Enrichir données",
            command=self._start_enrichment,
            state="disabled",
            width=150
        )
        self.enrich_button.pack(side="left", padx=5)
        
        # 6. Exporter
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
        
        # COLONNES AVEC COLONNE PAROLES ENTRE CRÉDITS ET BPM
        columns = ("Titre", "Artiste principal", "Album", "Date sortie", "Crédits", "Paroles", "BPM", "Statut")
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
                self.tree.column(col, width=130)
            elif col == "Date sortie":
                self.tree.column(col, width=90)
            elif col == "Crédits":
                self.tree.column(col, width=70)
            elif col == "Paroles":  # NOUVELLE COLONNE
                self.tree.column(col, width=70)
            elif col == "BPM":
                self.tree.column(col, width=70)
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
        """Remplit le tableau avec les morceaux"""
        # Nettoyer le tableau
        for item in self.tree.get_children():
            self.tree.delete(item)

        if not self.current_artist or not getattr(self.current_artist, 'tracks', None):
            return

        # Charger les morceaux désactivés depuis la mémoire
        try:
            self.disabled_tracks = self.disabled_tracks_manager.load_disabled_tracks(self.current_artist.name)
        except Exception:
            self.disabled_tracks = set()

        logger.info(f"Morceaux désactivés chargés: {len(self.disabled_tracks)} pour {self.current_artist.name}")

        # Réinitialiser la sélection
        self.selected_tracks.clear()

        for i, track in enumerate(self.current_artist.tracks):
            try:
                # Déterminer si désactivé PAR INDICE
                is_disabled = i in self.disabled_tracks
                
                # Utiliser les propriétés du modèle Track (avec fallback sécurisé)
                values = (
                    track.title or "N/A",
                    track.album or "",
                    track.release_date or "",
                    ", ".join(track.featured_artists_list) if hasattr(track, 'featured_artists_list') else "",
                    ", ".join(track.producers) if hasattr(track, 'producers') else "",
                    ", ".join(track.writers) if hasattr(track, 'writers') else "",
                    "✓" if (hasattr(track, 'credits_scraped') and track.credits_scraped) else "",
                    track.genius_url or ""
                )
                
                # Tags
                tags = (str(i),)
                if is_disabled:
                    tags = (str(i), "disabled")
                
                # Insérer la ligne
                self.tree.insert(
                    "", "end",
                    text="⊘" if is_disabled else "☐",
                    values=values,
                    tags=tags
                )
                
                # Si actif, ajouter à la sélection par défaut
                if not is_disabled:
                    self.selected_tracks.add(i)
                    
            except Exception as e:
                logger.error(f"Erreur ajout track idx={i}: {e}")
                # En cas d'erreur, ajouter une ligne minimale
                try:
                    self.tree.insert(
                        "", "end",
                        text="☐",
                        values=(
                            getattr(track, 'title', f"Track {i}"),
                            "", "", "", "", "", "", ""
                        ),
                        tags=(str(i),)
                    )
                except:
                    pass
        
        # Style pour morceaux désactivés
        self.tree.tag_configure("disabled", foreground="gray", background="#2a2a2a")
        
        self._update_selection_count()
        self._update_buttons_state()

    def _on_tree_click(self, event):
        """Gère les clics sur le tableau avec sélection multiple"""
        region = self.tree.identify_region(event.x, event.y)
        if region == "tree":
            item = self.tree.identify_row(event.y)
            if item:
                tags = self.tree.item(item)["tags"]
                if tags:
                    index = int(tags[0])
                    
                    # Vérifier si désactivé PAR INDICE
                    if index in self.disabled_tracks:
                        return  # Ignorer le clic si désactivé
                    
                    # Gestion normale de la sélection
                    if event.state & 0x1:  # Shift key
                        self._handle_shift_selection(index)
                    else:
                        self._toggle_single_selection(index, item)
                    
                    self.last_selected_index = index
                    self._update_selection_count()

    def _toggle_track_disabled(self, index: int):
        """Active/désactive un morceau en utilisant son ID"""
        if 0 <= index < len(self.current_artist.tracks):
            track = self.current_artist.tracks[index]
            # Créer un ID unique pour le track
            track_id = track.id if hasattr(track, 'id') and track.id else f"{track.title}_{track.artist.name}"
            
            if track_id in self.disabled_track_ids:
                self.disabled_track_ids.remove(track_id)
                logger.debug(f"Track réactivé: {track.title} (ID: {track_id})")
            else:
                self.disabled_track_ids.add(track_id)
                # Retirer de la sélection si désactivé
                if index in self.selected_tracks:
                    self.selected_tracks.remove(index)
                logger.debug(f"Track désactivé: {track.title} (ID: {track_id})")

    def _handle_shift_selection(self, current_index: int):
        """Gère la sélection multiple avec Shift - ✅ NOUVEAU"""
        if self.last_selected_index is not None:
            # Sélectionner tous les éléments entre last_selected et current
            start = min(self.last_selected_index, current_index)
            end = max(self.last_selected_index, current_index)
            
            for i in range(start, end + 1):
                # Vérifier que l'index n'est pas désactivé
                if i not in self.disabled_tracks:
                    self.selected_tracks.add(i)
            
            # Mettre à jour l'affichage
            self._refresh_selection_display()
        else:
            # Premier clic avec Shift, traiter comme clic normal
            self.selected_tracks.add(current_index)
            self._refresh_selection_display()

    def _refresh_selection_display(self):
        """Met à jour l'affichage des sélections dans le tableau - ✅ NOUVEAU"""
        for item in self.tree.get_children():
            tags = self.tree.item(item)["tags"]
            if tags and len(tags) > 0:
                index = int(tags[0])
                is_disabled = "disabled" in tags
                
                if is_disabled:
                    self.tree.item(item, text="⊘")
                elif index in self.selected_tracks:
                    self.tree.item(item, text="☑")
                else:
                    self.tree.item(item, text="☐")

    def _on_right_click(self, event):
        """Menu contextuel sur clic droit - ✅ NOUVEAU"""
        item = self.tree.identify_row(event.y)
        if item:
            tags = self.tree.item(item)["tags"]
            if tags:
                index = int(tags[0])
                
                # Créer menu contextuel
                context_menu = tkinter.Menu(self.root, tearoff=0)
                
                is_disabled = "disabled" in tags
                
                if is_disabled:
                    context_menu.add_command(
                        label="Réactiver ce morceau",
                        command=lambda: self._enable_track(index)
                    )
                else:
                    context_menu.add_command(
                        label="Désactiver ce morceau",
                        command=lambda: self._disable_track(index)
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

    def _disable_selected_tracks(self):
        """Désactive les morceaux sélectionnés"""
        if not self.selected_tracks:
            messagebox.showwarning("Aucune sélection", "Veuillez sélectionner des morceaux à désactiver")
            return
        
        try:
            # Ajouter aux morceaux désactivés (utiliser indices)
            self.disabled_tracks.update(self.selected_tracks)
            
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
        """Réactive les morceaux sélectionnés"""
        if not self.disabled_tracks:
            messagebox.showinfo("Info", "Aucun morceau désactivé")
            return
        
        try:
            # Créer une copie pour éviter les modifications pendant l'itération
            tracks_to_enable = set()
            
            # Parcourir tous les morceaux désactivés
            for idx in self.disabled_tracks:
                tracks_to_enable.add(idx)
            
            # Retirer des morceaux désactivés
            self.disabled_tracks.clear()
            
            # Sauvegarder
            if self.current_artist:
                self.disabled_tracks_manager.save_disabled_tracks(
                    self.current_artist.name, 
                    self.disabled_tracks
                )
            
            # Rafraîchir l'affichage
            self._populate_tracks_table()
            
            logger.info(f"Tous les morceaux ont été réactivés")
            
        except Exception as e:
            logger.error(f"Erreur lors de la réactivation: {e}")
            self._show_error("Erreur", f"Impossible de réactiver les morceaux: {e}")

    def _disable_track(self, index: int):
        """Désactive un morceau spécifique - ✅ NOUVEAU"""
        self.disabled_tracks.add(index)
        if index in self.selected_tracks:
            self.selected_tracks.remove(index)
        self._refresh_selection_display()
        self._update_selection_count()

    def _enable_track(self, index: int):
        """Réactive un morceau spécifique - ✅ NOUVEAU"""
        if index in self.disabled_tracks:
            self.disabled_tracks.remove(index)
        self._refresh_selection_display()
        self._update_selection_count()

    def _sort_column(self, col):
        """Trie les morceaux selon la colonne"""
        if not self.current_artist or not self.current_artist.tracks:
            return
        
        try:
            # Sauvegarder les sélections ET désactivations PAR TITRE
            # (car les indices vont changer après le tri)
            selected_track_titles = set()
            for idx in self.selected_tracks:
                if 0 <= idx < len(self.current_artist.tracks):
                    selected_track_titles.add(self.current_artist.tracks[idx].title)
            
            disabled_track_titles = set()
            for idx in self.disabled_tracks:
                if 0 <= idx < len(self.current_artist.tracks):
                    disabled_track_titles.add(self.current_artist.tracks[idx].title)
            
            # Déterminer l'ordre de tri
            reverse = False
            if col == self.sort_column:
                reverse = not self.sort_reverse
            
            # Fonction de tri selon la colonne
            sort_key = None
            if col == "Titre":
                sort_key = lambda t: (t.title or "").lower()
            elif col == "Album":
                sort_key = lambda t: (t.album or "").lower()
            elif col == "Date":
                sort_key = lambda t: t.release_date or ""
            elif col == "Feat.":
                sort_key = lambda t: ", ".join(t.featured_artists) if t.featured_artists else ""
            elif col == "Producteurs":
                sort_key = lambda t: ", ".join(t.producers) if t.producers else ""
            elif col == "Writers":
                sort_key = lambda t: ", ".join(t.writers) if t.writers else ""
            elif col == "Crédits":
                sort_key = lambda t: t.credits_scraped
            
            if sort_key:
                # Trier les morceaux
                self.current_artist.tracks.sort(key=sort_key, reverse=reverse)
            
            # Après le tri, restaurer par titre
            self.selected_tracks.clear()
            self.disabled_tracks.clear()
            
            for new_idx, track in enumerate(self.current_artist.tracks):
                if track.title in selected_track_titles:
                    self.selected_tracks.add(new_idx)
                if track.title in disabled_track_titles:
                    self.disabled_tracks.add(new_idx)
            
            # Sauvegarder les nouveaux indices des morceaux désactivés
            if self.current_artist:
                self.disabled_tracks_manager.save_disabled_tracks(
                    self.current_artist.name, 
                    self.disabled_tracks
                )
            
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
        
        # Créer une fenêtre de détails
        details_window = ctk.CTkToplevel(self.root)
        details_window.title(f"Détails - {track.title}")
        
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
            date_str = track.release_date.strftime('%d/%m/%Y') if hasattr(track.release_date, 'strftime') else str(track.release_date)[:10]
            ctk.CTkLabel(right_column, text=f"📅 Date: {date_str}").pack(anchor="w", pady=1)
        
        if track.bpm:
            ctk.CTkLabel(right_column, text=f"🎼 BPM: {track.bpm}").pack(anchor="w", pady=1)
        
        if track.duration:
            minutes = track.duration // 60
            seconds = track.duration % 60
            ctk.CTkLabel(right_column, text=f"⏱️ Durée: {minutes}:{seconds:02d}").pack(anchor="w", pady=1)
        
        if track.genre:
            ctk.CTkLabel(right_column, text=f"🎭 Genre: {track.genre}").pack(anchor="w", pady=1)
        
        # URL Genius (cliquable)
        if track.genius_url:
            urls_frame = ctk.CTkFrame(info_frame, fg_color="transparent")
            urls_frame.pack(anchor="w", padx=10, pady=5)
            
            # URL Genius
            genius_frame = ctk.CTkFrame(urls_frame, fg_color="transparent")
            genius_frame.pack(side="left", padx=(0, 20))
            
            ctk.CTkLabel(genius_frame, text="🎤 Genius: ").pack(side="left")
            
            genius_label = ctk.CTkLabel(
                genius_frame, 
                text=track.genius_url,
                text_color="blue",
                cursor="hand2"
            )
            genius_label.pack(side="left")
            
            import webbrowser
            genius_label.bind("<Button-1>", lambda e: webbrowser.open(track.genius_url))
            
        # YouTube intelligent
        youtube_frame = ctk.CTkFrame(urls_frame, fg_color="transparent")
        youtube_frame.pack(side="left")
        
        ctk.CTkLabel(youtube_frame, text="🎵 YouTube: ").pack(side="left")
        
        # Obtenir le lien YouTube intelligent
        artist_name = track.artist.name if track.artist else self.current_artist.name
        release_year = self.get_release_year_safely(track)
        
        youtube_result = youtube_integration.get_youtube_link_for_track(
            artist_name, track.title, track.album, release_year
        )

        # Affichage selon le type de résultat
        if youtube_result['type'] == 'direct':
            # Lien direct trouvé automatiquement
            label_text = f"▶️ Écouter (auto • {youtube_result['confidence']:.0%})"
            label_color = "green"
            tooltip_text = (f"Lien automatique sélectionné\n"
                            f"Titre: {youtube_result.get('title', 'N/A')}\n"
                            f"Chaîne: {youtube_result.get('channel', 'N/A')}\n"
                            f"Confiance: {youtube_result['confidence']:.1%}")
        else:
            # URL de recherche optimisée
            label_text = "🔍 Rechercher"
            label_color = "orange" 
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
            
            # Header avec statistiques des paroles
            lyrics_header = ctk.CTkFrame(lyrics_frame)
            lyrics_header.pack(fill="x", padx=10, pady=10)
            
            words_count = len(track.lyrics.split()) if track.lyrics else 0
            chars_count = len(track.lyrics) if track.lyrics else 0
            
            ctk.CTkLabel(lyrics_header, 
                        text=f"📝 Paroles complètes", 
                        font=("Arial", 14, "bold")).pack(anchor="w", padx=5, pady=5)
            
            info_text = f"📊 {words_count} mots • {chars_count} caractères"
            if hasattr(track, 'lyrics_scraped_at') and track.lyrics_scraped_at:
                date_str = track.lyrics_scraped_at.strftime('%d/%m/%Y à %H:%M') if hasattr(track.lyrics_scraped_at, 'strftime') else str(track.lyrics_scraped_at)[:16]
                info_text += f" • Récupérées le {date_str}"
            
            ctk.CTkLabel(lyrics_header, text=info_text, text_color="gray").pack(anchor="w", padx=5)
            
            # Zone de texte pour les paroles
            lyrics_textbox = ctk.CTkTextbox(
                lyrics_frame, 
                width=850, 
                height=450,
                font=("Consolas", 11)
            )
            lyrics_textbox.pack(fill="both", expand=True, padx=10, pady=10)
            
            formatted_lyrics = self._format_lyrics_for_display(track.lyrics)
            lyrics_textbox.insert("0.0", formatted_lyrics)
            lyrics_textbox.configure(state="disabled")
            
            # Boutons d'action pour les paroles
            lyrics_actions = ctk.CTkFrame(lyrics_frame)
            lyrics_actions.pack(fill="x", padx=10, pady=10)
            
            def copy_lyrics():
                """Copie les paroles dans le presse-papier"""
                details_window.clipboard_clear()
                details_window.clipboard_append(track.lyrics)
                messagebox.showinfo("Copié", "Paroles copiées dans le presse-papier")
            
            def save_lyrics():
                """Sauvegarde les paroles dans un fichier"""
                filename = f"{track.artist.name} - {track.title}.txt".replace('/', '_').replace('\\', '_')
                filepath = filedialog.asksaveasfilename(
                    defaultextension=".txt",
                    filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
                    initialfile=filename
                )
                if filepath:
                    try:
                        with open(filepath, 'w', encoding='utf-8') as f:
                            f.write(f"{track.artist.name} - {track.title}\n")
                            f.write("=" * 50 + "\n\n")
                            f.write(track.lyrics)
                        messagebox.showinfo("Sauvegardé", f"Paroles sauvegardées dans:\n{filepath}")
                    except Exception as e:
                        messagebox.showerror("Erreur", f"Erreur lors de la sauvegarde:\n{str(e)}")
            
            def research_lyrics():
                """Re-scraper les paroles de ce morceau"""
                result = messagebox.askyesno(
                    "Re-scraper les paroles",
                    f"Voulez-vous re-scraper les paroles de '{track.title}' ?\n\n"
                    "Cela remplacera les paroles actuelles."
                )
                if result:
                    threading.Thread(target=lambda: self._rescrape_single_lyrics(track, details_window), daemon=True).start()
            
            ctk.CTkButton(lyrics_actions, text="📋 Copier", command=copy_lyrics, width=100).pack(side="left", padx=5)
            ctk.CTkButton(lyrics_actions, text="💾 Sauvegarder", command=save_lyrics, width=100).pack(side="left", padx=5)
            ctk.CTkButton(lyrics_actions, text="🔄 Re-scraper", command=research_lyrics, width=100).pack(side="left", padx=5)
            
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
            tech_textbox.insert("end", f"• Dernier scraping: {track.last_scraped}\n")
        if track.created_at:
            tech_textbox.insert("end", f"• Créé le: {track.created_at}\n")
        if track.updated_at:
            tech_textbox.insert("end", f"• Mis à jour le: {track.updated_at}\n")
        
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
        tech_textbox.insert("end", f"• _release_date_from_api: {getattr(track, '_release_date_from_api', 'Non défini')}\n")
        
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

    def _select_all_tracks(self):
        """Sélectionne tous les morceaux actifs - ✅ MODIFIÉ"""
        self.selected_tracks.clear()
        for item in self.tree.get_children():
            tags = self.tree.item(item)["tags"]
            if tags and "disabled" not in tags:  # Seulement les morceaux actifs
                index = int(tags[0])
                self.selected_tracks.add(index)
                self.tree.item(item, text="☑")
        self._update_selection_count()
    
    def _deselect_all_tracks(self):
        """Désélectionne tous les morceaux - ✅ INCHANGÉ"""
        self.selected_tracks.clear()
        for item in self.tree.get_children():
            tags = self.tree.item(item)["tags"]
            if tags and "disabled" not in tags:
                self.tree.item(item, text="☐")
        self._update_selection_count()
    
    def _update_selection_count(self):
        """Met à jour le compteur de sélection - ✅ AMÉLIORÉ"""
        total = len(self.current_artist.tracks) if self.current_artist else 0
        active_tracks = total - len(self.disabled_tracks)
        selected = len(self.selected_tracks)
        disabled = len(self.disabled_tracks)
        
        count_text = f"{selected}/{active_tracks} sélectionnés"
        if disabled > 0:
            count_text += f" ({disabled} désactivés)"
        
        self.selected_count_label.configure(text=count_text)

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
                    for i, track in enumerate(self.current_artist.tracks):
                        if i not in self.disabled_tracks:
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

        def _load_artist_data(artist_name: str):
            """Charge les données d'un artiste depuis les fichiers (fonction interne)"""
            try:
                self.current_artist = self.data_manager.load_artist_data(artist_name)

                if self.current_artist and self.current_artist.tracks:
                    # Charger les morceaux désactivés et nettoyer les indices invalides
                    self.disabled_tracks = self.disabled_tracks_manager.load_disabled_tracks(self.current_artist.name)
                    max_index = len(self.current_artist.tracks) - 1
                    self.disabled_tracks = {i for i in self.disabled_tracks if 0 <= i <= max_index}

                    # Si on a nettoyé, resave
                    try:
                        orig = self.disabled_tracks_manager.load_disabled_tracks(self.current_artist.name)
                        if len(self.disabled_tracks) != len(orig):
                            self.disabled_tracks_manager.save_disabled_tracks(self.current_artist.name, self.disabled_tracks)
                            logger.info("Indices des morceaux désactivés nettoyés et sauvegardés")
                    except Exception:
                        pass

                    self._populate_tracks_table()
                    self._update_buttons_state()
                    total_tracks = len(self.current_artist.tracks)
                    disabled_count = len(self.disabled_tracks)
                    active_count = total_tracks - disabled_count

                    msg = f"Artiste '{artist_name}' chargé avec succès!\n\n"
                    msg += f"📀 {total_tracks} morceaux au total\n"
                    msg += f"✅ {active_count} morceaux actifs\n"
                    if disabled_count > 0:
                        msg += f"⊘ {disabled_count} morceaux désactivés (restaurés depuis la mémoire)"

                    messagebox.showinfo("Succès", msg)
                    return True
                else:
                    messagebox.showwarning("Attention", f"Aucune donnée trouvée pour '{artist_name}'")
                    return False

            except Exception as e:
                error_msg = f"Erreur lors du chargement: {str(e)}"
                logger.error(error_msg, exc_info=True)
                messagebox.showerror("Erreur", error_msg)
                return False
        
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
                total_music_credits = sum(len(t.get_music_credits()) for t in self.current_artist.tracks)
                total_video_credits = sum(len(t.get_video_credits()) for t in self.current_artist.tracks)
                
                # Compter les morceaux par statut
                complete_tracks = sum(1 for t in self.current_artist.tracks if t.has_complete_credits())
                partial_tracks = sum(1 for t in self.current_artist.tracks 
                                if not t.has_complete_credits() and t.get_music_credits())
                empty_tracks = total_tracks - complete_tracks - partial_tracks
                
                # Compter les features
                featuring_count = sum(1 for t in self.current_artist.tracks 
                                    if hasattr(t, 'is_featuring') and t.is_featuring)
                main_tracks = total_tracks - featuring_count
                
                # Compter les morceaux exclus (si implémenté)
                excluded_count = sum(1 for t in self.current_artist.tracks 
                                if hasattr(t, 'excluded') and t.excluded)
                
                # ✅ NOUVEAU: Texte informatif détaillé
                info_parts = []
                
                # Nombre total avec détail features
                if featuring_count > 0:
                    info_parts.append(f"{total_tracks} morceaux ({main_tracks} principaux + {featuring_count} feat.)")
                else:
                    info_parts.append(f"{total_tracks} morceaux")
                
                # Crédits avec séparation
                credits_text = f"{total_music_credits} crédits musicaux"
                if total_video_credits > 0:
                    credits_text += f" + {total_video_credits} vidéo"
                info_parts.append(credits_text)
                
                # Statut des morceaux
                status_parts = []
                if complete_tracks > 0:
                    status_parts.append(f"{complete_tracks} complets")
                if partial_tracks > 0:
                    status_parts.append(f"{partial_tracks} partiels")
                if empty_tracks > 0:
                    status_parts.append(f"{empty_tracks} sans crédits")
                
                if status_parts:
                    info_parts.append(f"({', '.join(status_parts)})")
                
                # Exclusions
                if excluded_count > 0:
                    info_parts.append(f"({excluded_count} exclus)")
                
                info_text = " - ".join(info_parts)
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

    def _get_track_status_icon(self, track: Track) -> str:
        """Retourne l'icône de statut selon le niveau de complétude des données"""
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
                             isinstance(track.lyrics, str) and 
                             track.lyrics.strip() != "")
            except Exception:
                has_lyrics = False
            
            # Vérifier la présence du BPM
            try:
                has_bpm = (track.bmp is not None and 
                          isinstance(track.bmp, (int, float)) and 
                          track.bmp > 0)
            except Exception:
                has_bpm = False
            
            # Conversion explicite en bool pour éviter les None
            has_credits = bool(has_credits)
            has_lyrics = bool(has_lyrics)
            has_bpm = bool(has_bpm)
            
            # Compter le nombre de types de données disponibles
            data_types_count = int(has_credits) + int(has_lyrics) + int(has_bpm)
            
            if data_types_count == 0:
                return "❌"  # Aucune donnée
            elif data_types_count >= 3:
                return "✅"  # Données complètes (crédits + paroles + BPM)
            else:
                return "⚠️"  # Données partielles
                
        except Exception as e:
            logger.error(f"Erreur générale dans _get_track_status_icon pour {getattr(track, 'title', 'unknown')}: {e}")
            return "❓"  # Erreur

    def _get_track_status_details(self, track):
        """Retourne les détails du statut pour le tooltip/debug"""
        details = []
        
        try:
            # Crédits musicaux
            try:
                if hasattr(track, 'get_music_credits') and callable(track.get_music_credits):
                    music_credits = track.get_music_credits()
                    if music_credits and len(music_credits) > 0:
                        details.append(f"🏷️ {len(music_credits)} crédits")
                else:
                    if track.credits and len(track.credits) > 0:
                        details.append(f"🏷️ {len(track.credits)} crédits")
            except Exception:
                pass
            
            # Paroles
            try:
                if hasattr(track, 'lyrics') and track.lyrics:
                    lyrics_value = str(track.lyrics).strip()
                    if lyrics_value and lyrics_value != "None":
                        word_count = len(lyrics_value.split())
                        details.append(f"📝 {word_count} mots")
            except Exception:
                pass
            
            # BPM
            try:
                if hasattr(track, 'bpm') and track.bpm:
                    bpm_value = track.bpm
                    if isinstance(bpm_value, (int, float)) and bpm_value > 0:
                        details.append(f"🎼 {int(bpm_value)} BPM")
            except Exception:
                pass
            
            return " • ".join(details) if details else "Aucune donnée disponible"
            
        except Exception as e:
            return f"Erreur: {str(e)}"

    def _start_scraping(self):
        """Lance le scraping pour l'artiste sélectionné"""
        if not self.artist_var.get():
            messagebox.showwarning("Aucun artiste", "Veuillez sélectionner un artiste")
            return
        
        artist_name = self.artist_var.get()
        
        # Vérifier si déjà en cours
        if self.is_scraping:
            messagebox.showinfo("Scraping en cours", "Un scraping est déjà en cours. Veuillez patienter.")
            return
        
        # Afficher la barre de progression
        self._show_progress_bar()
        self.is_scraping = True
        self._update_buttons_state()
        
        # Lancer le scraping dans un thread séparé
        def scraping_thread():
            try:
                logger.info(f"Début du scraping pour {artist_name}")
                
                # Créer ou récupérer l'artiste
                artist = self.data_manager.get_or_create_artist(artist_name)
                
                # Initialiser le scraper
                if not self.scraper:
                    self.scraper = GeniusScraper(headless=self.headless_var.get())
                
                # Callback pour la progression
                def progress_callback(current, total, message=""):
                    self.root.after(0, self._update_progress, current, total, message)
                
                # Scraper la liste des morceaux si nécessaire
                if not artist.tracks:
                    self.root.after(0, lambda: self.progress_label.configure(text="Récupération de la liste des morceaux..."))
                    tracks = self.scraper.scrape_artist_tracks(artist_name)
                    
                    if tracks:
                        # Sauvegarder les morceaux
                        for track_data in tracks:
                            track = self.data_manager.add_track(
                                artist_id=artist.id,
                                title=track_data.get('title'),
                                genius_url=track_data.get('url'),
                                is_cover=track_data.get('is_cover', False)
                            )
                        
                        # Recharger l'artiste avec les morceaux
                        artist = self.data_manager.get_artist(artist.id)
                        
                        logger.info(f"{len(tracks)} morceaux trouvés pour {artist_name}")
                    else:
                        logger.warning(f"Aucun morceau trouvé pour {artist_name}")
                        self.root.after(0, lambda: messagebox.showwarning("Aucun morceau", 
                            f"Aucun morceau trouvé pour {artist_name} sur Genius"))
                        return
                
                # Charger les morceaux désactivés
                disabled_track_indices = self.disabled_tracks_manager.load_disabled_tracks(artist_name)
                
                # Filtrer les morceaux actifs (non désactivés)
                active_tracks = []
                for i, track in enumerate(artist.tracks):
                    if i not in disabled_track_indices:
                        active_tracks.append(track)
                
                if not active_tracks:
                    self.root.after(0, lambda: messagebox.showinfo("Info", 
                        "Tous les morceaux sont désactivés. Activez des morceaux pour les scraper."))
                    return
                
                # Scraper les crédits des morceaux actifs
                tracks_to_scrape = [t for t in active_tracks if not t.credits_scraped]
                
                if tracks_to_scrape:
                    logger.info(f"Scraping des crédits pour {len(tracks_to_scrape)} morceaux")
                    
                    for i, track in enumerate(tracks_to_scrape):
                        if not self.is_scraping:  # Vérifier l'annulation
                            break
                        
                        # Mettre à jour la progression
                        progress_callback(i + 1, len(tracks_to_scrape), f"Scraping: {track.title[:50]}...")
                        
                        # Scraper les crédits
                        credits = self.scraper.scrape_track_credits(track)
                        
                        # Sauvegarder les crédits
                        if credits:
                            self.data_manager.save_credits(track.id, credits)
                        
                        # Mettre à jour le morceau
                        self.data_manager.update_track(track)
                        
                        # Pause entre les requêtes
                        if i < len(tracks_to_scrape) - 1:
                            time.sleep(2)
                else:
                    logger.info(f"Tous les morceaux actifs ont déjà leurs crédits")
                
                # Recharger l'artiste final
                self.current_artist = self.data_manager.get_artist(artist.id)
                
                # Rafraîchir l'affichage
                self.root.after(0, self._populate_tracks_table)
                
                # Message de succès
                self.root.after(0, lambda: self._show_success("Scraping terminé", 
                    f"Scraping terminé pour {artist_name}"))
                
            except Exception as e:
                logger.error(f"Erreur lors du scraping: {e}", exc_info=True)
                self.root.after(0, lambda: self._show_error("Erreur de scraping", str(e)))
            
            finally:
                self.is_scraping = False
                # Masquer la barre de progression dans le thread principal
                self.root.after(0, self._hide_progress_bar)
                self.root.after(0, self._update_buttons_state)
        
        # Démarrer le thread
        thread = threading.Thread(target=scraping_thread, daemon=True)
        thread.start()
        
        def update_progress(current, total, track_name):
            """Callback de progression"""
            progress = current / total
            self.root.after(0, lambda: self.progress_var.set(progress))
            self.root.after(0, lambda: self.progress_label.configure(
                text=f"{current}/{total} - {track_name[:30]}..."
            ))
        
        def scrape():
            scraper = None
            try:
                logger.info(f"Début du scraping de {len(selected_tracks_list)} morceaux")
                scraper = GeniusScraper(headless=True)
                results = scraper.scrape_multiple_tracks(
                    selected_tracks_list,
                    progress_callback=update_progress
                )
                
                # Sauvegarder les données mises à jour
                for track in selected_tracks_list:
                    track.artist = self.current_artist
                    self.data_manager.save_track(track)
                
                # Afficher le résumé
                success_msg = f"Résultats:\n"
                success_msg += f"- Réussis: {results['success']}\n"
                success_msg += f"- Échoués: {results['failed']}\n"
                success_msg += f"- Erreurs: {len(results['errors'])}"
                
                if disabled_count > 0:
                    success_msg += f"\n\n⚠️ {disabled_count} morceaux désactivés ignorés"
                
                self.root.after(0, lambda: messagebox.showinfo("Scraping terminé", success_msg))
                
                # Mettre à jour l'affichage
                self.root.after(0, self._update_artist_info)
                self.root.after(0, self._update_statistics)
                
            except Exception as err:
                error_msg = str(err) if str(err) != "None" else "Erreur inconnue lors du scraping"
                logger.error(f"Erreur lors du scraping: {error_msg}", exc_info=True)
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
                    text="Scraper crédits"
                ))
                self.root.after(0, lambda: self.progress_bar.set(0))
                self.root.after(0, lambda: self.progress_label.configure(text=""))
        
        threading.Thread(target=scrape, daemon=True).start()

    def _start_lyrics_scraping(self):
        """Lance le scraping des paroles pour les morceaux sélectionnés - ✅ MODIFIÉ"""
        if not self.current_artist or not self.current_artist.tracks:
            return
        
        if not self.selected_tracks:
            messagebox.showwarning("Attention", "Aucun morceau sélectionné")
            return
        
        # ✅ MODIFIÉ: Filtrer les morceaux sélectionnés ET actifs
        selected_tracks_list = []
        for i in sorted(self.selected_tracks):
            if i not in self.disabled_tracks:
                selected_tracks_list.append(self.current_artist.tracks[i])
        
        if not selected_tracks_list:
            messagebox.showwarning("Attention", "Tous les morceaux sélectionnés sont désactivés")
            return
        
        # Confirmation
        disabled_count = len(self.selected_tracks) - len(selected_tracks_list)
        confirm_msg = f"Voulez-vous scraper les paroles de {len(selected_tracks_list)} morceaux sélectionnés ?\n\n"
        confirm_msg += "📝 Cela récupérera :\n"
        confirm_msg += "• Les paroles complètes\n"
        confirm_msg += "• Structure nettoyée (sections, artistes)\n"
        confirm_msg += "• Suppression automatique des publicités\n\n"
        if disabled_count > 0:
            confirm_msg += f"⚠️ {disabled_count} morceaux désactivés seront ignorés.\n\n"
        confirm_msg += f"⏱️ Temps estimé : ~{len(selected_tracks_list) * 0.5:.1f} minutes"
        
        result = messagebox.askyesno("Scraping des paroles", confirm_msg)
        
        if not result:
            return
        
        self.lyrics_button.configure(state="disabled", text="📝 Scraping paroles...")
        self.progress_bar.set(0)
        self._show_progress_bar()
        self.root.after(0, lambda: self.progress_bar.set(0))

        
        def update_progress(current, total, track_name):
            progress = current / total
            self.root.after(0, lambda: self.progress_var.set(progress))
            self.root.after(0, lambda: self.progress_label.configure(
                text=f"📝 {current}/{total} - {track_name[:25]}..."
            ))
        
        def scrape_lyrics():
            scraper = None
            try:
                scraper = GeniusScraper(headless=True)
                results = scraper.scrape_multiple_tracks_with_lyrics(
                    selected_tracks_list,
                    progress_callback=update_progress,
                    include_lyrics=True
                )
                
                # Sauvegarder les données avec paroles
                for track in selected_tracks_list:
                    track.artist = self.current_artist
                    self.data_manager.save_track(track)
                
                # Afficher le résumé
                success_msg = f"✅ Scraping des paroles terminé !\n\n"
                success_msg += f"📊 Résultats :\n"
                success_msg += f"• Morceaux traités : {results['success']}\n"
                success_msg += f"• Paroles récupérées : {results['lyrics_scraped']}\n"
                success_msg += f"• Échecs : {results['failed']}\n\n"
                
                if disabled_count > 0:
                    success_msg += f"⚠️ {disabled_count} morceaux désactivés ignorés\n\n"
                
                success_msg += f"💡 Les paroles sont maintenant disponibles dans les détails des morceaux"
                
                self.root.after(0, lambda: messagebox.showinfo("📝 Paroles récupérées", success_msg))
                
                self.root.after(0, self._update_artist_info)
                
            except Exception as err:
                error_msg = str(err) if str(err) != "None" else "Erreur inconnue"
                logger.error(f"Erreur scraping paroles: {error_msg}", exc_info=True)
                self.root.after(0, lambda: messagebox.showerror(
                    "Erreur",
                    f"Erreur lors du scraping des paroles :\n{error_msg}"
                ))
            finally:
                if scraper:
                    try:
                        scraper.close()
                    except:
                        pass
                
                self.root.after(0, lambda: self.lyrics_button.configure(
                    state="normal",
                    text="📝 Scraper paroles"
                ))
                self.root.after(0, lambda: self.progress_bar.set(0))
                self.root.after(0, lambda: self.progress_label.configure(text=""))
        
        threading.Thread(target=scrape_lyrics, daemon=True).start()

    def _force_update_selected(self):
        """Force la mise à jour des morceaux sélectionnés"""
        if not self.current_artist or not self.selected_tracks:
            messagebox.showwarning("Aucune sélection", "Veuillez sélectionner des morceaux à mettre à jour")
            return
        
        # Préparer la liste des morceaux à mettre à jour (filtrer les désactivés)
        selected_tracks_list = []
        for i in sorted(self.selected_tracks):
            if i not in self.disabled_tracks:  # CORRECTION: Utiliser disabled_tracks (indices) pas disabled_track_ids
                selected_tracks_list.append(self.current_artist.tracks[i])
        
        if not selected_tracks_list:
            messagebox.showwarning("Aucun morceau actif", "Tous les morceaux sélectionnés sont désactivés")
            return
        
        # Confirmation avec avertissement
        result = messagebox.askyesno(
            "⚠️ Confirmation de mise à jour forcée",
            f"🔄 MISE À JOUR FORCÉE de {len(selected_tracks_list)} morceaux\n\n"
            "⚠️ ATTENTION: Cette opération va :\n"
            "• Supprimer TOUS les anciens crédits\n"
            "• Supprimer les anciennes erreurs\n"
            "• Re-scraper complètement les morceaux\n\n"
            "✨ Bénéfices :\n"
            "• Sépare les crédits vidéo des crédits musicaux\n"
            "• Applique les dernières améliorations du scraper\n"
            "• Nettoie les doublons\n\n"
            "Continuer ?",
            icon="warning"
        )
        
        if not result:
            return
        
        # Confirmer encore une fois
        final_confirm = messagebox.askyesno(
            "Dernière confirmation",
            f"Êtes-vous VRAIMENT sûr ?\n\n"
            f"Cette action va effacer les crédits existants de {len(selected_tracks_list)} morceaux.\n"
            "Cette action est IRRÉVERSIBLE.",
            icon="warning"
        )
        
        if not final_confirm:
            return
        
        # Vérifier si déjà en cours
        if self.is_scraping:
            messagebox.showinfo("Scraping en cours", "Un scraping est déjà en cours. Veuillez patienter.")
            return
        
        # Afficher la barre de progression CORRECTEMENT
        self._show_progress_bar()
        self.is_scraping = True
        self._update_buttons_state()
        
        # Fonction de callback pour la progression (locale à cette méthode)
        def update_progress(current, total, track_name):
            """Callback de progression"""
            progress = current / total if total > 0 else 0
            self.root.after(0, lambda: self.progress_var.set(progress))
            self.root.after(0, lambda: self.progress_label.configure(
                text=f"🔄 {current}/{total} - {track_name[:30]}..."
            ))
            # Mettre à jour la ligne dans le tableau si la méthode existe
            if hasattr(self, '_update_track_in_table'):
                self.root.after(0, lambda: self._update_track_in_table(track_name))
        
        # Thread de mise à jour
        def force_update():
            scraper = None
            try:
                logger.info(f"🔄 Début de la mise à jour forcée de {len(selected_tracks_list)} morceaux")
                
                # ✅ ÉTAPE 1: Nettoyer les anciens crédits
                self.root.after(0, lambda: self.progress_label.configure(text="🧹 Nettoyage des anciens crédits..."))
                
                # Utiliser la méthode du data_manager si elle existe
                if hasattr(self.data_manager, 'force_update_multiple_tracks'):
                    cleanup_results = self.data_manager.force_update_multiple_tracks(
                        selected_tracks_list, 
                        progress_callback=lambda i, t, n: self.root.after(0, lambda: self.progress_label.configure(text=f"🧹 Nettoyage {i}/{t}"))
                    )
                    total_before = cleanup_results.get('total_credits_before', 0)
                    total_after = cleanup_results.get('total_credits_after', 0)
                else:
                    # Alternative : supprimer manuellement les crédits
                    total_before = sum(len(getattr(t, 'credits', [])) for t in selected_tracks_list)
                    for track in selected_tracks_list:
                        if hasattr(self.data_manager, 'delete_credits'):
                            self.data_manager.delete_credits(track.id)
                        track.credits_scraped = False
                    total_after = 0
                
                # ✅ ÉTAPE 2: Re-scraper les morceaux
                self.root.after(0, lambda: self.progress_label.configure(text="🔍 Re-scraping des crédits..."))
                
                # Initialiser le scraper
                if not self.scraper:
                    self.scraper = GeniusScraper(headless=self.headless_var.get())
                
                # Scraper chaque morceau
                success_count = 0
                failed_count = 0
                
                for i, track in enumerate(selected_tracks_list):
                    if not self.is_scraping:  # Vérifier l'annulation
                        break
                    
                    try:
                        # Mettre à jour la progression
                        update_progress(i + 1, len(selected_tracks_list), track.title)
                        
                        # Scraper les crédits
                        credits = self.scraper.scrape_track_credits(track)
                        
                        # Sauvegarder les crédits
                        if credits:
                            self.data_manager.save_credits(track.id, credits)
                            success_count += 1
                        
                        # Mettre à jour le morceau
                        self.data_manager.update_track(track)
                        
                        # Pause entre les requêtes
                        if i < len(selected_tracks_list) - 1:
                            time.sleep(2)
                            
                    except Exception as e:
                        logger.error(f"Erreur scraping {track.title}: {e}")
                        failed_count += 1
                
                # ✅ ÉTAPE 3: Compter les résultats finaux
                final_credits = sum(len(getattr(t, 'credits', [])) for t in selected_tracks_list)
                
                # Calculer les crédits musicaux et vidéo si les méthodes existent
                music_credits = 0
                video_credits = 0
                for track in selected_tracks_list:
                    if hasattr(track, 'get_music_credits'):
                        music_credits += len(track.get_music_credits())
                    if hasattr(track, 'get_video_credits'):
                        video_credits += len(track.get_video_credits())
                
                # Recharger l'artiste
                self.current_artist = self.data_manager.get_artist(self.current_artist.id)
                
                # Rafraîchir l'affichage
                self.root.after(0, self._populate_tracks_table)
                
                # Afficher le résumé
                summary_message = f"✅ Mise à jour forcée terminée !\n\n"
                summary_message += f"📊 RÉSULTATS:\n"
                summary_message += f"• Morceaux traités: {len(selected_tracks_list)}\n"
                summary_message += f"• Scraping réussi: {success_count}\n"
                summary_message += f"• Scraping échoué: {failed_count}\n\n"
                summary_message += f"🏷️ CRÉDITS:\n"
                summary_message += f"• Avant: {total_before} crédits\n"
                summary_message += f"• Après: {final_credits} crédits\n"
                
                if music_credits > 0 or video_credits > 0:
                    summary_message += f"• 🎵 Musicaux: {music_credits}\n"
                    summary_message += f"• 🎬 Vidéo: {video_credits}\n"
                
                self.root.after(0, lambda: messagebox.showinfo(
                    "🎉 Mise à jour forcée terminée",
                    summary_message
                ))
                
                # Mettre à jour les statistiques si les méthodes existent
                if hasattr(self, '_update_artist_info'):
                    self.root.after(0, self._update_artist_info)
                if hasattr(self, '_update_statistics'):
                    self.root.after(0, self._update_statistics)
                
                logger.info(f"Mise à jour forcée terminée: {success_count} réussis, {failed_count} échoués")
                
            except Exception as err:
                error_msg = str(err) if str(err) != "None" else "Erreur inconnue lors de la mise à jour forcée"
                logger.error(f"❌ Erreur lors de la mise à jour forcée: {error_msg}", exc_info=True)
                self.root.after(0, lambda: self._show_error(
                    "Erreur",
                    f"❌ Erreur lors de la mise à jour forcée:\n{error_msg}"
                ))
                
            finally:
                # S'assurer que le scraper est fermé si on l'a créé localement
                if scraper and scraper != self.scraper:
                    try:
                        scraper.close()
                    except:
                        pass
                
                self.is_scraping = False
                # Masquer la barre de progression
                self.root.after(0, self._hide_progress_bar)
                self.root.after(0, self._update_buttons_state)
        
        # Démarrer le thread
        thread = threading.Thread(target=force_update, daemon=True)
        thread.start()

    def _start_enrichment(self):
        """Lance l'enrichissement des données depuis toutes les sources - ✅ MODIFIÉ"""
        if not self.current_artist or not self.current_artist.tracks:
            return
        
        # Dialogue pour choisir les sources
        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Sources d'enrichissement")
        dialog.geometry("400x300")
        
        ctk.CTkLabel(dialog, text="Sélectionnez les sources à utiliser:", 
                    font=("Arial", 14)).pack(pady=10)
        
        # Variables pour les checkboxes
        sources_vars = {}
        sources_info = {
            'rapedia': 'Rapedia.fr (BPM prioritaire pour le rap FR)',
            'spotify': 'Spotify (BPM, durée, popularité)',
            'discogs': 'Discogs (crédits supplémentaires, labels)',
            'lastfm': 'Last.fm (genres, tags)'
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
        
        def start_enrichment():
            selected_sources = [s for s, var in sources_vars.items() if var.get()]
            if not selected_sources:
                messagebox.showwarning("Attention", "Sélectionnez au moins une source")
                return
            
            dialog.destroy()
            self._run_enrichment(selected_sources)
        
        ctk.CTkButton(dialog, text="Démarrer", command=start_enrichment).pack(pady=20)
    
    def _run_enrichment(self, sources: List[str]):
        """Exécute l'enrichissement avec les sources sélectionnées - ✅ MODIFIÉ"""
        # ✅ MODIFIÉ: Filtrer les morceaux sélectionnés ET actifs
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
        
        self.enrich_button.configure(state="disabled", text="Enrichissement...")
        self.progress_bar.set(0)
        
        def update_progress(current, total, info):
            """Callback de progression"""
            progress = current / total
            self.root.after(0, lambda: self.progress_var.set(progress))
            self.root.after(0, lambda: self.progress_label.configure(text=info))
        
        def enrich():
            try:
                stats = self.data_enricher.enrich_tracks(
                    selected_tracks_list,
                    sources=sources,
                    progress_callback=update_progress,
                    use_threading=False  # Pour éviter les problèmes de rate limit
                )
                
                # Sauvegarder les données enrichies
                for track in selected_tracks_list:
                    self.data_manager.save_track(track)
                
                # Préparer le message de résumé
                disabled_count = len(self.selected_tracks) - len(selected_tracks_list)
                summary = "Enrichissement terminé!\n\n"
                summary += f"Morceaux traités: {stats['processed']}/{stats['total']}\n"
                summary += f"Morceaux avec BPM: {stats['tracks_with_bpm']}\n"
                summary += f"Durée: {stats['duration_seconds']:.1f} secondes\n\n"
                
                summary += "Résultats par source:\n"
                for source, results in stats['by_source'].items():
                    if results['success'] + results['failed'] > 0:
                        summary += f"- {source.capitalize()}: {results['success']} réussis, {results['failed']} échoués\n"
                
                if disabled_count > 0:
                    summary += f"\n⚠️ {disabled_count} morceaux désactivés ignorés"
                
                self.root.after(0, lambda: messagebox.showinfo("Enrichissement terminé", summary))
                self.root.after(0, self._update_artist_info)
                self.root.after(0, self._update_statistics)
                
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
    
    def _show_youtube_manual_verification(self, track: Track, candidates: List[dict]):
        """Affiche l'interface de vérification manuelle YouTube - VERSION SIMPLIFIÉE"""
        
        # Créer une fenêtre de vérification simple
        verify_window = ctk.CTkToplevel(self.root)
        verify_window.title(f"Vérification YouTube - {track.title}")
        verify_window.geometry("800x600")
        
        # Centrer la fenêtre
        verify_window.update_idletasks()
        x = (verify_window.winfo_screenwidth() // 2) - 400
        y = (verify_window.winfo_screenheight() // 2) - 300
        verify_window.geometry(f"800x600+{x}+{y}")
        
        verify_window.lift()
        verify_window.focus_force()
        verify_window.grab_set()
        
        # En-tête
        header_frame = ctk.CTkFrame(verify_window)
        header_frame.pack(fill="x", padx=10, pady=10)
        
        ctk.CTkLabel(
            header_frame,
            text=f"🎵 Sélection YouTube pour: {track.title}",
            font=("Arial", 16, "bold")
        ).pack(pady=10)
        
        artist_name = track.artist.name if track.artist else self.current_artist.name
        ctk.CTkLabel(
            header_frame,
            text=f"Artiste: {artist_name} | Album: {track.album or 'N/A'}",
            font=("Arial", 12)
        ).pack()
        
        # Zone de candidats
        candidates_frame = ctk.CTkFrame(verify_window)
        candidates_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        ctk.CTkLabel(
            candidates_frame,
            text="Candidats trouvés (double-clic pour sélectionner):",
            font=("Arial", 12, "bold")
        ).pack(anchor="w", padx=10, pady=(10, 5))
        
        # Scrollable frame pour les candidats
        scroll_frame = ctk.CTkScrollableFrame(candidates_frame, height=350)
        scroll_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        selected_url = {"value": None}  # Pour stocker la sélection
        
        # Afficher chaque candidat
        for i, candidate in enumerate(candidates[:10]):  # Limiter à 10
            
            candidate_frame = ctk.CTkFrame(scroll_frame)
            candidate_frame.pack(fill="x", padx=5, pady=5)
            
            # Informations du candidat
            title_text = candidate.get('title', 'Titre inconnu')[:60]
            channel_text = candidate.get('channel_title', 'Chaîne inconnue')[:30]
            score_text = f"{candidate.get('relevance_score', 0):.2f}"
            
            info_text = f"🎵 {title_text}\n📺 {channel_text} | 📊 Score: {score_text}"
            
            candidate_button = ctk.CTkButton(
                candidate_frame,
                text=info_text,
                anchor="w",
                height=60,
                command=lambda url=candidate.get('url'): self._select_youtube_url(url, selected_url, verify_window)
            )
            candidate_button.pack(fill="x", padx=10, pady=5)
        
        # Boutons d'action
        action_frame = ctk.CTkFrame(verify_window)
        action_frame.pack(fill="x", padx=10, pady=10)
        
        def manual_search():
            # Ouvrir recherche YouTube manuelle
            import webbrowser
            from urllib.parse import quote
            search_term = f"{artist_name} {track.title}"
            search_url = f"https://www.youtube.com/results?search_query={quote(search_term)}"
            webbrowser.open(search_url)
            verify_window.destroy()
        
        def skip_track():
            verify_window.destroy()
        
        ctk.CTkButton(
            action_frame,
            text="🔍 Recherche manuelle",
            command=manual_search,
            width=150
        ).pack(side="left", padx=5)
        
        ctk.CTkButton(
            action_frame,
            text="❌ Ignorer",
            command=skip_track,
            width=100
        ).pack(side="right", padx=5)
        
        # Attendre la sélection
        verify_window.wait_window()
        return selected_url["value"]
    
    def _select_youtube_url(self, url: str, selected_container: dict, window):
        """Sélectionne une URL YouTube et ferme la fenêtre"""
        selected_container["value"] = url
        if url:
            import webbrowser
            webbrowser.open(url)
        window.destroy()

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
        """Formate une date pour l'affichage"""
        if not release_date:
            return "N/A"
        
        try:
            # Si c'est déjà un objet datetime
            if hasattr(release_date, 'strftime'):
                return release_date.strftime('%Y-%m-%d')
            
            # Si c'est une chaîne
            if isinstance(release_date, str):
                # Prendre les 10 premiers caractères pour YYYY-MM-DD
                date_part = str(release_date)[:10]
                if len(date_part) >= 4:
                    return date_part
            
            return str(release_date)[:10]
            
        except Exception as e:
            logger.debug(f"Erreur formatage date '{release_date}': {e}")
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

    def _get_credits_count(self, track):
        """Retourne le nombre de crédits d'un morceau de manière sécurisée"""
        try:
            if hasattr(track, 'get_music_credits'):
                music_credits = track.get_music_credits()
                return len(music_credits) if music_credits else 0
            elif hasattr(track, 'credits'):
                return len(track.credits) if track.credits else 0
            else:
                return 0
        except Exception as e:
            logger.debug(f"Erreur comptage crédits pour {getattr(track, 'title', 'unknown')}: {e}")
            return 0

    def _update_progress(self, current, total, message=""):
        """Met à jour la barre de progression"""
        if total > 0:
            progress = current / total
            self.progress_bar.set(progress)
            
            # Texte de progression
            if message:
                text = f"{message} ({current}/{total})"
            else:
                text = f"Progression: {current}/{total}"
            
            self.progress_label.configure(text=text)

    def _stop_scraping(self):
        """Arrête le scraping en cours"""
        if self.is_scraping:
            response = messagebox.askyesno(
                "Arrêter le scraping",
                "Voulez-vous vraiment arrêter le scraping en cours ?"
            )
            
            if response:
                self.is_scraping = False
                self.progress_label.configure(text="Arrêt en cours...")
                logger.info("Arrêt du scraping demandé par l'utilisateur")
                
                # La barre sera cachée par le finally du thread

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

    def _show_error(self, title, message):
        """Affiche un message d'erreur"""
        messagebox.showerror(title, message)

    def _show_success(self, title, message):
        """Affiche un message de succès"""
        messagebox.showinfo(title, message)

    def run(self):
        """Lance l'application"""
        self.root.mainloop()