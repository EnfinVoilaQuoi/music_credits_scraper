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
        """Crée tous les widgets de l'interface"""
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
        
        # === Tableau des morceaux ===
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
        
        # Créer le Treeview
        columns = ("Titre", "Album", "Crédits", "BPM", "Statut")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="tree headings", height=15)
        
        # Ajouter une colonne pour les checkboxes
        self.tree.heading("#0", text="✓")
        self.tree.column("#0", width=30, stretch=False)
        
        for col in columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=150)
        
        # Scrollbars
        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        
        # Pack
        tree_container = ctk.CTkFrame(table_frame)
        tree_container.pack(fill="both", expand=True)

        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")
        
        # Double-clic pour voir les détails
        self.tree.bind("<Double-Button-1>", self._show_track_details)
        # Simple clic pour sélectionner/désélectionner
        self.tree.bind("<Button-1>", self._toggle_track_selection)
        
        # === Section statistiques ===
        stats_frame = ctk.CTkFrame(main_frame)
        stats_frame.pack(fill="x", padx=5, pady=5)
        
        self.stats_label = ctk.CTkLabel(stats_frame, text="", font=("Arial", 12))
        self.stats_label.pack()
    
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
                logger.error(f"Erreur lors de la recherche: {e}")
                self.root.after(0, lambda: messagebox.showerror("Erreur", f"Erreur lors de la recherche: {str(e)}"))
            finally:
                self.root.after(0, lambda: self.search_button.configure(state="normal", text="Rechercher"))
        
        # Lancer dans un thread
        threading.Thread(target=search, daemon=True).start()
    
    def _load_existing_artist(self):
        """Charge un artiste existant depuis la base de données"""
        # Créer une fenêtre de sélection
        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Charger un artiste")
        dialog.geometry("400x500")
        
        # Liste des artistes
        listbox_frame = ctk.CTkFrame(dialog)
        listbox_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        # Récupérer les statistiques pour avoir la liste des artistes
        with self.data_manager._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM artists ORDER BY name")
            artists = [row[0] for row in cursor.fetchall()]
        
        if not artists:
            messagebox.showinfo("Information", "Aucun artiste dans la base de données")
            dialog.destroy()
            return
        
        # Listbox
        from tkinter import Listbox, SINGLE
        listbox = Listbox(listbox_frame, selectmode=SINGLE)
        listbox.pack(fill="both", expand=True)
        
        for artist in artists:
            listbox.insert("end", artist)
        
        def load_selected():
            selection = listbox.curselection()
            if selection:
                artist_name = listbox.get(selection[0])
                self.artist_entry.delete(0, "end")
                self.artist_entry.insert(0, artist_name)
                dialog.destroy()
                self._search_artist()
        
        ctk.CTkButton(dialog, text="Charger", command=load_selected).pack(pady=10)
    
    def _update_artist_info(self):
        """Met à jour les informations de l'artiste"""
        if self.current_artist:
            self.artist_info_label.configure(text=f"Artiste: {self.current_artist.name}")
            
            if self.current_artist.tracks:
                total_credits = sum(len(t.credits) for t in self.current_artist.tracks)
                self.tracks_info_label.configure(
                    text=f"{len(self.current_artist.tracks)} morceaux - {total_credits} crédits au total"
                )
                self._populate_tracks_table(self.current_artist.tracks)
            else:
                self.tracks_info_label.configure(text="Aucun morceau chargé")
            
            # Activer les boutons
            self.get_tracks_button.configure(state="normal")
            if self.current_artist.tracks:
                self.scrape_button.configure(state="normal")
                self.enrich_button.configure(state="normal")
                self.export_button.configure(state="normal")
    
    def _get_tracks(self):
        """Récupère les morceaux de l'artiste"""
        if not self.current_artist:
            return
        
        self.get_tracks_button.configure(state="disabled", text="Récupération...")
        self.progress_label.configure(text="Récupération des morceaux...")
        
        def get_tracks():
            try:
                # Récupérer les morceaux via l'API
                tracks = self.genius_api.get_artist_songs(self.current_artist, max_songs=200)
                
                if tracks:
                    # Sauvegarder dans la base
                    for track in tracks:
                        self.data_manager.save_track(track)
                    
                    self.current_artist.tracks = tracks
                    self.tracks = tracks
                    
                    self.root.after(0, self._update_artist_info)
                    self.root.after(0, lambda: messagebox.showinfo(
                        "Succès", 
                        f"{len(tracks)} morceaux récupérés pour {self.current_artist.name}"
                    ))
                else:
                    self.root.after(0, lambda: messagebox.showwarning(
                        "Attention", 
                        "Aucun morceau trouvé"
                    ))
                    
            except Exception as e:
                logger.error(f"Erreur lors de la récupération des morceaux: {e}")
                self.root.after(0, lambda: messagebox.showerror(
                    "Erreur", 
                    f"Erreur lors de la récupération: {str(e)}"
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
            try:
                with GeniusScraper(headless=True) as scraper:
                    results = scraper.scrape_multiple_tracks(
                        selected_tracks_list,
                        progress_callback=update_progress
                    )
                
                # Sauvegarder les données mises à jour
                for track in selected_tracks_list:
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
                
            except Exception as e:
                logger.error(f"Erreur lors du scraping: {e}")
                self.root.after(0, lambda: messagebox.showerror(
                    "Erreur",
                    f"Erreur lors du scraping: {str(e)}"
                ))
            finally:
                self.is_scraping = False
                self.root.after(0, lambda: self.scrape_button.configure(
                    state="normal",
                    text="Scraper les crédits"
                ))
                self.root.after(0, lambda: self.progress_bar.set(0))
                self.root.after(0, lambda: self.progress_label.configure(text=""))
        
        threading.Thread(target=scrape, daemon=True).start()
    
    def _populate_tracks_table(self, tracks: List[Track]):
        """Remplit le tableau avec les morceaux"""
        # Effacer le tableau
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        # Réinitialiser les sélections
        self.selected_tracks.clear()
        
        # Ajouter les morceaux
        for i, track in enumerate(tracks):
            status = "✓" if track.has_complete_credits() else "⚠" if track.credits else "✗"
            values = (
                track.title,
                track.album or "-",
                len(track.credits),
                track.bpm or "-",
                status
            )
            item = self.tree.insert("", "end", text="☐", values=values, tags=(str(i),))
            # Sélectionner par défaut
            self.selected_tracks.add(i)
            self.tree.item(item, text="☑")
        
        self._update_selection_count()
    
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
    
    def _update_track_in_table(self, track_name: str):
        """Met à jour une ligne spécifique du tableau"""
        for item in self.tree.get_children():
            if self.tree.item(item)["values"][0] == track_name:
                # Retrouver le track
                for track in self.current_artist.tracks:
                    if track.title == track_name:
                        status = "✓" if track.has_complete_credits() else "⚠" if track.credits else "✗"
                        values = (
                            track.title,
                            track.album or "-",
                            len(track.credits),
                            track.bpm or "-",
                            status
                        )
                        self.tree.item(item, values=values)
                        break
                break
    
    def _show_track_details(self, event):
        """Affiche les détails d'un morceau"""
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
            
            # Informations générales
            info_frame = ctk.CTkFrame(details_window)
            info_frame.pack(fill="x", padx=10, pady=10)
            
            ctk.CTkLabel(info_frame, text=f"Titre: {track.title}", font=("Arial", 14, "bold")).pack(anchor="w", padx=5, pady=2)
            ctk.CTkLabel(info_frame, text=f"Album: {track.album or 'N/A'}").pack(anchor="w", padx=5, pady=2)
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
            
            # Crédits
            credits_frame = ctk.CTkFrame(details_window)
            credits_frame.pack(fill="both", expand=True, padx=10, pady=10)
            
            ctk.CTkLabel(credits_frame, text="Crédits:", font=("Arial", 12, "bold")).pack(anchor="w", padx=5, pady=5)
            
            # Grouper les crédits par rôle
            from collections import defaultdict
            credits_by_role = defaultdict(list)
            for credit in track.credits:
                credits_by_role[credit.role.value].append(credit)
            
            # Créer un textbox scrollable
            textbox = ctk.CTkTextbox(credits_frame, width=550, height=400)
            textbox.pack(fill="both", expand=True, padx=5, pady=5)
            
            for role, credits in sorted(credits_by_role.items()):
                textbox.insert("end", f"\n{role}:\n", "bold")
                for credit in credits:
                    detail = f" ({credit.role_detail})" if credit.role_detail else ""
                    textbox.insert("end", f"  • {credit.name}{detail}\n")
            
            textbox.configure(state="disabled")
            
            # Erreurs éventuelles
            if track.scraping_errors:
                error_frame = ctk.CTkFrame(details_window)
                error_frame.pack(fill="x", padx=10, pady=10)
                
                ctk.CTkLabel(error_frame, text="Erreurs:", font=("Arial", 12, "bold")).pack(anchor="w", padx=5)
                for error in track.scraping_errors:
                    ctk.CTkLabel(error_frame, text=f"• {error}", text_color="red").pack(anchor="w", padx=15)
    
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
                logger.error(f"Erreur lors de l'export: {e}")
                messagebox.showerror("Erreur", f"Erreur lors de l'export: {str(e)}")
    
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
                logger.error(f"Erreur lors de l'enrichissement: {e}")
                self.root.after(0, lambda: messagebox.showerror("Erreur", f"Erreur lors de l'enrichissement: {str(e)}"))
            finally:
                self.root.after(0, lambda: self.enrich_button.configure(state="normal", text="Enrichir les données"))
                self.root.after(0, lambda: self.progress_bar.set(0))
                self.root.after(0, lambda: self.progress_label.configure(text=""))
        
        threading.Thread(target=enrich, daemon=True).start()