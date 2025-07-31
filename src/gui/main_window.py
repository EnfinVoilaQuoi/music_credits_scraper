"""Interface graphique principale de l'application"""
import customtkinter as ctk
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
from src.models import Artist, Track


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
        self.selected_tracks = set()  # Pour stocker les morceaux sélectionnés
        
        self._create_widgets()
        self._update_statistics()
    
    def _create_widgets(self):
        """Crée tous les widgets de l'interface - VERSION AVEC TRI ET DATE"""
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
        
        self.get_tracks_button = ctk.CTkButton(
            control_frame,
            text="Récupérer les morceaux",
            command=self._get_tracks,
            state="disabled",
            width=150
        )
        self.get_tracks_button.pack(side="left", padx=5)
        
        self.scrape_button = ctk.CTkButton(
            control_frame,
            text="Scraper les crédits",
            command=self._start_scraping,
            state="disabled",
            width=150
        )
        self.scrape_button.pack(side="left", padx=5)
        
        self.enrich_button = ctk.CTkButton(
            control_frame,
            text="Enrichir les données",
            command=self._start_enrichment,
            state="disabled",
            width=150
        )
        self.enrich_button.pack(side="left", padx=5)
        
        self.export_button = ctk.CTkButton(
            control_frame,
            text="Exporter JSON",
            command=self._export_data,
            state="disabled",
            width=100
        )
        self.export_button.pack(side="left", padx=5)
        
        # Progress bar
        self.progress_var = ctk.DoubleVar()
        self.progress_bar = ctk.CTkProgressBar(control_frame, variable=self.progress_var, width=200)
        self.progress_bar.pack(side="left", padx=10)
        self.progress_bar.set(0)
        
        self.progress_label = ctk.CTkLabel(control_frame, text="")
        self.progress_label.pack(side="left")
        
        # === Tableau des morceaux avec DATE et TRI ===
        table_frame = ctk.CTkFrame(main_frame)
        table_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Frame pour les boutons de sélection
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
        
        self.selected_count_label = ctk.CTkLabel(selection_frame, text="")
        self.selected_count_label.pack(side="left", padx=20)
        
        # Créer le Treeview dans un conteneur approprié
        tree_container = ctk.CTkFrame(table_frame)
        tree_container.pack(fill="both", expand=True)
        
        tree_scroll_frame = ctk.CTkFrame(tree_container)
        tree_scroll_frame.pack(fill="both", expand=True)
        
        # COLONNES AVEC DATE DE SORTIE ET TRI
        columns = ("Titre", "Artiste principal", "Album", "Date sortie", "Crédits", "BPM", "Statut")
        self.tree = ttk.Treeview(tree_scroll_frame, columns=columns, show="tree headings", height=15)
        
        # Configuration des colonnes avec tri
        self.tree.heading("#0", text="✓")
        self.tree.column("#0", width=50, stretch=False)
        
        # Variable pour suivre l'ordre de tri
        self.sort_reverse = {}
        
        for col in columns:
            self.tree.heading(col, text=col, command=lambda c=col: self._sort_column(c))
            if col == "Titre":
                self.tree.column(col, width=220)  # Titre
            elif col == "Artiste principal":
                self.tree.column(col, width=130)  # Artiste principal
            elif col == "Album":
                self.tree.column(col, width=130)  # Album
            elif col == "Date sortie":
                self.tree.column(col, width=90)   # Date
            elif col == "Crédits":
                self.tree.column(col, width=70)   # Crédits
            elif col == "BPM":
                self.tree.column(col, width=70)   # BPM
            else:  # Statut
                self.tree.column(col, width=70)   # Statut
        
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
        
        # Bindings
        self.tree.bind("<Double-Button-1>", self._show_track_details)
        self.tree.bind("<Button-1>", self._toggle_track_selection)
        
        # === Section statistiques ===
        stats_frame = ctk.CTkFrame(main_frame)
        stats_frame.pack(fill="x", padx=5, pady=5)
        
        self.stats_label = ctk.CTkLabel(stats_frame, text="", font=("Arial", 12))
        self.stats_label.pack()

    def _sort_column(self, col):
        """Trie les morceaux selon la colonne sélectionnée"""
        if not self.current_artist or not self.current_artist.tracks:
            return
        
        # Basculer l'ordre de tri pour cette colonne
        reverse = self.sort_reverse.get(col, False)
        self.sort_reverse[col] = not reverse
        
        # Créer une liste des morceaux avec leur index original
        tracks_with_index = [(i, track) for i, track in enumerate(self.current_artist.tracks)]
        
        # Fonction de tri selon la colonne
        def sort_key(item):
            index, track = item
            
            if col == "Titre":
                return track.get_display_title().lower()
            elif col == "Artiste principal":
                return track.get_display_artist().lower()
            elif col == "Album":
                return (track.album or "zzz").lower()  # Albums vides à la fin
            elif col == "Date sortie":
                # Trier par date (plus récent en premier si reverse=True)
                if track.release_date:
                    return track.release_date
                else:
                    return datetime.min if reverse else datetime.max
            elif col == "Crédits":
                return len(track.get_music_credits())
            elif col == "BPM":
                return track.bpm or 0
            elif col == "Statut":
                # Ordre: ✓ (complet), ⚠ (partiel), ✗ (vide)
                if track.has_complete_credits():
                    return 0
                elif track.get_music_credits():
                    return 1
                else:
                    return 2
            return ""
        
        # Trier
        tracks_with_index.sort(key=sort_key, reverse=reverse)
        
        # Réorganiser la liste des morceaux
        self.current_artist.tracks = [track for index, track in tracks_with_index]
        
        # Recréer l'affichage
        self._populate_tracks_table(self.current_artist.tracks)
        
        # Mettre à jour l'en-tête pour indiquer le tri
        direction = "↓" if reverse else "↑"
        self.tree.heading(col, text=f"{col} {direction}")
        
        # Réinitialiser les autres en-têtes
        for other_col in ["Titre", "Artiste principal", "Album", "Date sortie", "Crédits", "BPM", "Statut"]:
            if other_col != col:
                self.tree.heading(other_col, text=other_col)

    def _populate_tracks_table(self, tracks: List[Track]):
        """Remplit le tableau avec les morceaux - VERSION AVEC DATE ET CORRECTION DU DÉCALAGE"""
        # Effacer le tableau
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        # Réinitialiser les sélections
        self.selected_tracks.clear()
        
        # Ajouter les morceaux
        for i, track in enumerate(tracks):
            # Déterminer l'affichage du titre et de l'artiste
            if hasattr(track, 'is_featuring') and track.is_featuring:
                # Pour les features : afficher "Titre (feat. Artiste recherché)"
                title_display = f"{track.title} (feat. {track.artist.name})"
                # Afficher l'artiste principal
                artist_display = getattr(track, 'primary_artist_name', 'Artiste principal inconnu')
                title_prefix = "🎤 "
            else:
                # Pour les morceaux principaux
                title_display = track.title
                artist_display = track.artist.name if track.artist else ""
                title_prefix = ""
            
            # Formater la date de sortie
            date_display = "-"
            if track.release_date:
                if hasattr(track.release_date, 'strftime'):
                    date_display = track.release_date.strftime('%Y-%m-%d')
                else:
                    date_display = str(track.release_date)[:10]  # Prendre les 10 premiers caractères
            
            # ✅ CORRECTION : Préparer les valeurs dans l'ordre EXACT des colonnes
            # Colonnes: ("Titre", "Artiste principal", "Album", "Date sortie", "Crédits", "BPM", "Statut")
            values = (
                title_prefix + title_display,    # Titre avec préfixe
                artist_display,                  # Artiste principal
                track.album or "-",              # Album
                date_display,                    # Date sortie
                len(track.get_music_credits()),  # ✅ Crédits MUSICAUX seulement
                track.bpm or "-",                # BPM
                "✓" if track.has_complete_credits() else "⚠" if track.get_music_credits() else "✗"  # Statut basé sur crédits musicaux
            )
            
            # Créer l'item dans le treeview
            item = self.tree.insert("", "end", text="☑", values=values, tags=(str(i),))
            
            # Sélectionner par défaut
            self.selected_tracks.add(i)
        
        self._update_selection_count()

    def _update_track_in_table(self, track_name: str):
        """Met à jour une ligne spécifique du tableau - VERSION CORRIGÉE"""
        if not self.current_artist:
            return
            
        for item in self.tree.get_children():
            item_values = self.tree.item(item)["values"]
            if item_values and len(item_values) > 0:
                # Comparer le titre (première colonne, en enlevant le préfixe emoji)
                item_title = item_values[0].replace("🎤 ", "")
                
                # Retrouver le track correspondant
                for i, track in enumerate(self.current_artist.tracks):
                    track_display = track.get_display_title()
                    if hasattr(track, 'is_featuring') and track.is_featuring:
                        track_display = f"{track.title} (feat. {track.artist.name})"
                    
                    if track_display == item_title or track.title in item_title:
                        # Mettre à jour les valeurs - ORDRE CORRECT
                        title_prefix = "🎤 " if (hasattr(track, 'is_featuring') and track.is_featuring) else ""
                        artist_display = track.get_display_artist()
                        
                        # Date de sortie
                        date_display = "-"
                        if track.release_date:
                            if hasattr(track.release_date, 'strftime'):
                                date_display = track.release_date.strftime('%Y-%m-%d')
                            else:
                                date_display = str(track.release_date)[:10]
                        
                        updated_values = (
                            title_prefix + track_display,     # Titre
                            artist_display,                   # Artiste principal  
                            track.album or "-",               # Album
                            date_display,                     # Date sortie
                            len(track.get_music_credits()),   # ✅ Crédits MUSICAUX
                            track.bpm or "-",                 # BPM
                            "✓" if track.has_complete_credits() else "⚠" if track.get_music_credits() else "✗"  # Statut
                        )
                        
                        self.tree.item(item, values=updated_values)
                        break
                break

    def _show_track_details(self, event):
        """Affiche les détails d'un morceau - VERSION AVEC DATE"""
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
            
            # Créer une fenêtre de détails
            details_window = ctk.CTkToplevel(self.root)
            details_window.title(f"Détails - {track.title}")
            details_window.geometry("600x700")
            
            # Informations générales avec DATE
            info_frame = ctk.CTkFrame(details_window)
            info_frame.pack(fill="x", padx=10, pady=10)
            
            ctk.CTkLabel(info_frame, text=f"Titre: {track.title}", font=("Arial", 14, "bold")).pack(anchor="w", padx=5, pady=2)
            
            # Affichage différent selon si c'est un featuring
            if hasattr(track, 'is_featuring') and track.is_featuring:
                ctk.CTkLabel(info_frame, text=f"Artiste principal: {track.get_display_artist()}", font=("Arial", 12)).pack(anchor="w", padx=5, pady=2)
                ctk.CTkLabel(info_frame, text=f"En featuring: {track.artist.name}", font=("Arial", 12)).pack(anchor="w", padx=5, pady=2)
            else:
                ctk.CTkLabel(info_frame, text=f"Artiste: {track.artist.name}", font=("Arial", 12)).pack(anchor="w", padx=5, pady=2)
            
            ctk.CTkLabel(info_frame, text=f"Album: {track.album or 'N/A'}").pack(anchor="w", padx=5, pady=2)
            
            # ✅ AJOUT : Numéro de piste
            if hasattr(track, 'track_number') and track.track_number:
                ctk.CTkLabel(info_frame, text=f"Piste n°: {track.track_number}").pack(anchor="w", padx=5, pady=2)
            
            # ✅ AJOUT : Date de sortie
            if track.release_date:
                if hasattr(track.release_date, 'strftime'):
                    date_str = track.release_date.strftime('%d/%m/%Y')
                else:
                    date_str = str(track.release_date)[:10]
                ctk.CTkLabel(info_frame, text=f"Date de sortie: {date_str}").pack(anchor="w", padx=5, pady=2)
            
            ctk.CTkLabel(info_frame, text=f"BPM: {track.bpm or 'N/A'}").pack(anchor="w", padx=5, pady=2)
            
            # URL Genius cliquable
            if track.genius_url:
                url_frame = ctk.CTkFrame(info_frame)
                url_frame.pack(anchor="w", padx=5, pady=2)
                
                ctk.CTkLabel(url_frame, text="URL Genius: ").pack(side="left")
                
                url_label = ctk.CTkLabel(
                    url_frame, 
                    text=track.genius_url,
                    text_color="blue",
                    cursor="hand2"
                )
                url_label.pack(side="left")
                
                # Rendre le label cliquable
                import webbrowser
                url_label.bind("<Button-1>", lambda e: webbrowser.open(track.genius_url))
            
            # Crédits SÉPARÉS
            credits_frame = ctk.CTkFrame(details_window)
            credits_frame.pack(fill="both", expand=True, padx=10, pady=10)
            
            # ✅ Séparer les crédits musicaux et vidéo
            music_credits = track.get_music_credits()
            video_credits = track.get_video_credits()
            
            ctk.CTkLabel(credits_frame, 
                        text=f"Crédits musicaux ({len(music_credits)}):", 
                        font=("Arial", 12, "bold")).pack(anchor="w", padx=5, pady=5)
            
            # Grouper les crédits MUSICAUX par rôle
            from collections import defaultdict
            music_credits_by_role = defaultdict(list)
            for credit in music_credits:
                music_credits_by_role[credit.role.value].append(credit)
            
            # Créer un textbox scrollable pour les crédits musicaux
            music_textbox = ctk.CTkTextbox(credits_frame, width=550, height=300)
            music_textbox.pack(fill="both", expand=True, padx=5, pady=5)
            
            if music_credits_by_role:
                for role, credits in sorted(music_credits_by_role.items()):
                    music_textbox.insert("end", f"\n{role}:\n", "bold")
                    for credit in credits:
                        detail = f" ({credit.role_detail})" if credit.role_detail else ""
                        music_textbox.insert("end", f"  • {credit.name}{detail}\n")
            else:
                music_textbox.insert("end", "Aucun crédit musical trouvé.\n")
            
            music_textbox.configure(state="disabled")
            
            # ✅ Section crédits vidéo (si présents)
            if video_credits:
                ctk.CTkLabel(credits_frame, 
                            text=f"Crédits vidéo ({len(video_credits)}):", 
                            font=("Arial", 12, "bold")).pack(anchor="w", padx=5, pady=(15, 5))
                
                video_credits_by_role = defaultdict(list)
                for credit in video_credits:
                    video_credits_by_role[credit.role.value].append(credit)
                
                video_textbox = ctk.CTkTextbox(credits_frame, width=550, height=150)
                video_textbox.pack(fill="x", padx=5, pady=5)
                
                for role, credits in sorted(video_credits_by_role.items()):
                    video_textbox.insert("end", f"\n{role}:\n", "bold")
                    for credit in credits:
                        detail = f" ({credit.role_detail})" if credit.role_detail else ""
                        video_textbox.insert("end", f"  • {credit.name}{detail}\n")
                
                video_textbox.configure(state="disabled")
            
            # Erreurs éventuelles
            if track.scraping_errors:
                error_frame = ctk.CTkFrame(details_window)
                error_frame.pack(fill="x", padx=10, pady=10)
                
                ctk.CTkLabel(error_frame, text="Erreurs:", font=("Arial", 12, "bold")).pack(anchor="w", padx=5)
                for error in track.scraping_errors:
                    ctk.CTkLabel(error_frame, text=f"• {error}", text_color="red").pack(anchor="w", padx=15)
    
    def _search_artist(self):
        """Recherche un artiste"""
        artist_name = self.artist_entry.get().strip()
        if not artist_name:
            messagebox.showwarning("Attention", "Veuillez entrer un nom d'artiste")
            return
        
        # Désactiver les boutons pendant la recherche
        self.search_button.configure(state="disabled", text="Recherche...")
        
        def search():
            try:
                # Vérifier d'abord dans la base de données
                artist = self.data_manager.get_artist_by_name(artist_name)
                
                if not artist:
                    # Rechercher sur Genius
                    artist = self.genius_api.search_artist(artist_name)
                    if artist:
                        # Sauvegarder dans la base
                        self.data_manager.save_artist(artist)
                
                if artist:
                    self.current_artist = artist
                    self.root.after(0, self._update_artist_info)
                    self.root.after(0, lambda: messagebox.showinfo("Succès", f"Artiste trouvé: {artist.name}"))
                else:
                    self.root.after(0, lambda: messagebox.showwarning("Non trouvé", f"Aucun artiste trouvé pour '{artist_name}'"))

            except Exception as e:
                error_msg = str(e)
                logger.error(f"Erreur lors de la recherche: {error_msg}")
                self.root.after(0, lambda: messagebox.showerror("Erreur", f"Erreur lors de la recherche: {error_msg}"))
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
                    # ✅ CORRECTION : Capturer la valeur de l'erreur avant la lambda
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
        """Met à jour les informations de l'artiste - VERSION CORRIGÉE"""
        if self.current_artist:
            self.artist_info_label.configure(text=f"Artiste: {self.current_artist.name}")
            
            if self.current_artist.tracks:
                total_credits = sum(len(t.get_music_credits()) for t in self.current_artist.tracks)
                
                # Compter les features et morceaux principaux
                featuring_count = sum(1 for t in self.current_artist.tracks 
                                    if hasattr(t, 'is_featuring') and t.is_featuring)
                main_tracks = len(self.current_artist.tracks) - featuring_count
                
                info_text = f"{len(self.current_artist.tracks)} morceaux au total"
                if featuring_count > 0:
                    info_text += f" ({main_tracks} principaux + {featuring_count} en featuring)"
                info_text += f" - {total_credits} crédits musicaux"
                
                self.tracks_info_label.configure(text=info_text)
                self._populate_tracks_table(self.current_artist.tracks)
            else:
                self.tracks_info_label.configure(text="Aucun morceau chargé")
            
            # Activer les boutons
            self.get_tracks_button.configure(state="normal")
            if self.current_artist.tracks:
                self.scrape_button.configure(state="normal")
                if hasattr(self, 'enrich_button'):
                    self.enrich_button.configure(state="normal")
                self.export_button.configure(state="normal")
    
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
                # ✅ CORRECTION : Capturer la valeur de l'erreur avant la lambda
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
    
    def _start_scraping(self):
        """Lance le scraping des crédits"""
        if not self.current_artist or not self.current_artist.tracks:
            return
        
        if self.is_scraping:
            messagebox.showwarning("Attention", "Un scraping est déjà en cours")
            return
        
        # Vérifier qu'il y a des morceaux sélectionnés
        if not self.selected_tracks:
            messagebox.showwarning("Attention", "Aucun morceau sélectionné")
            return
        
        # Filtrer les morceaux sélectionnés
        selected_tracks_list = [self.current_artist.tracks[i] for i in sorted(self.selected_tracks)]
        
        # Confirmation
        result = messagebox.askyesno(
            "Confirmation",
            f"Voulez-vous scraper les crédits de {len(selected_tracks_list)} morceaux sélectionnés ?\n"
            "Cela peut prendre plusieurs minutes."
        )
        
        if not result:
            return
        
        self.is_scraping = True
        self.scrape_button.configure(state="disabled", text="Scraping en cours...")
        self.progress_bar.set(0)
        
        def update_progress(current, total, track_name):
            """Callback de progression"""
            progress = current / total
            self.root.after(0, lambda: self.progress_var.set(progress))
            self.root.after(0, lambda: self.progress_label.configure(
                text=f"{current}/{total} - {track_name[:30]}..."
            ))
            # Mettre à jour la ligne dans le tableau
            self.root.after(0, lambda: self._update_track_in_table(track_name))
        
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
                self.root.after(0, lambda: messagebox.showinfo(
                    "Scraping terminé",
                    f"Résultats:\n"
                    f"- Réussis: {results['success']}\n"
                    f"- Échoués: {results['failed']}\n"
                    f"- Erreurs: {len(results['errors'])}"
                ))
                
                # Mettre à jour l'affichage
                self.root.after(0, self._update_artist_info)
                self.root.after(0, self._update_statistics)
                
            except Exception as err:
                # ✅ CORRECTION : Capturer la valeur de l'erreur avant la lambda
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
                    text="Scraper les crédits"
                ))
                self.root.after(0, lambda: self.progress_bar.set(0))
                self.root.after(0, lambda: self.progress_label.configure(text=""))
        
        threading.Thread(target=scrape, daemon=True).start()
    
    def _toggle_track_selection(self, event):
        """Bascule la sélection d'un morceau"""
        region = self.tree.identify_region(event.x, event.y)
        if region == "tree":
            item = self.tree.identify_row(event.y)
            if item:
                tags = self.tree.item(item)["tags"]
                if tags:
                    index = int(tags[0])
                    if index in self.selected_tracks:
                        self.selected_tracks.remove(index)
                        self.tree.item(item, text="☐")
                    else:
                        self.selected_tracks.add(index)
                        self.tree.item(item, text="☑")
                    self._update_selection_count()
    
    def _select_all_tracks(self):
        """Sélectionne tous les morceaux"""
        self.selected_tracks.clear()
        for item in self.tree.get_children():
            tags = self.tree.item(item)["tags"]
            if tags:
                index = int(tags[0])
                self.selected_tracks.add(index)
                self.tree.item(item, text="☑")
        self._update_selection_count()
    
    def _deselect_all_tracks(self):
        """Désélectionne tous les morceaux"""
        self.selected_tracks.clear()
        for item in self.tree.get_children():
            self.tree.item(item, text="☐")
        self._update_selection_count()
    
    def _update_selection_count(self):
        """Met à jour le compteur de sélection"""
        total = len(self.current_artist.tracks) if self.current_artist else 0
        selected = len(self.selected_tracks)
        self.selected_count_label.configure(text=f"{selected}/{total} sélectionnés")
    
    def _export_data(self):
        """Exporte les données en JSON"""
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
                self.data_manager.export_to_json(self.current_artist.name, filepath)
                messagebox.showinfo("Succès", f"Données exportées vers:\n{filepath}")
            except Exception as e:
                # ✅ CORRECTION : Capturer la valeur de l'erreur avant la lambda
                error_msg = str(e)
                logger.error(f"Erreur lors de l'export: {error_msg}")
                messagebox.showerror("Erreur", f"Erreur lors de l'export: {error_msg}")
    
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
    
    def run(self):
        """Lance l'application"""
        self.root.mainloop()

    def _start_enrichment(self):
        """Lance l'enrichissement des données depuis toutes les sources"""
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
        """Exécute l'enrichissement avec les sources sélectionnées"""
        # Vérifier qu'il y a des morceaux sélectionnés
        if not self.selected_tracks:
            messagebox.showwarning("Attention", "Aucun morceau sélectionné")
            return
        
        # Filtrer les morceaux sélectionnés
        selected_tracks_list = [self.current_artist.tracks[i] for i in sorted(self.selected_tracks)]
        
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
                summary = "Enrichissement terminé!\n\n"
                summary += f"Morceaux traités: {stats['processed']}/{stats['total']}\n"
                summary += f"Morceaux avec BPM: {stats['tracks_with_bpm']}\n"
                summary += f"Durée: {stats['duration_seconds']:.1f} secondes\n\n"
                
                summary += "Résultats par source:\n"
                for source, results in stats['by_source'].items():
                    if results['success'] + results['failed'] > 0:
                        summary += f"- {source.capitalize()}: {results['success']} réussis, {results['failed']} échoués\n"
                
                self.root.after(0, lambda: messagebox.showinfo("Enrichissement terminé", summary))
                self.root.after(0, self._update_artist_info)
                self.root.after(0, self._update_statistics)
                
            except Exception as e:
                # ✅ CORRECTION : Capturer la valeur de l'erreur avant la lambda
                error_msg = str(e)
                logger.error(f"Erreur lors de l'enrichissement: {error_msg}")
                self.root.after(0, lambda: messagebox.showerror("Erreur", f"Erreur lors de l'enrichissement: {error_msg}"))
            finally:
                self.root.after