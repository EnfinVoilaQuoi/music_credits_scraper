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
from tkinter import ttk as tkinter_ttv


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
        
        self.force_update_button = ctk.CTkButton(
            control_frame,
            text="🔄 Mise à jour forcée",
            command=self._force_update_selected,
            state="disabled",
            width=150,
            fg_color="orange",
            hover_color="darkorange"
        )
        self.force_update_button.pack(side="left", padx=5)

        self.enrich_button = ctk.CTkButton(
            control_frame,
            text="Enrichir les données",
            command=self._start_enrichment,
            state="disabled",
            width=150
        )
        self.enrich_button.pack(side="left", padx=5)
        
        self.lyrics_button = ctk.CTkButton(
            control_frame,
            text="📝 Scraper paroles",
            command=self._start_lyrics_scraping,
            state="disabled",
            width=150,
            fg_color="purple",
            hover_color="darkmagenta"
        )
        self.lyrics_button.pack(side="left", padx=5)

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
    def _get_track_status_icon(self, track):
        """Retourne l'icône de statut selon le niveau de complétude des données"""
        try:
            # Vérifier la présence des crédits
            has_credits = False
            try:
                music_credits = track.get_music_credits()
                has_credits = len(music_credits) > 0 if music_credits else False
            except:
                has_credits = False
            
            # Vérifier la présence des paroles
            has_lyrics = False
            try:
                has_lyrics = (hasattr(track, 'lyrics') and 
                             track.lyrics is not None and 
                             isinstance(track.lyrics, str) and 
                             len(track.lyrics.strip()) > 0)
            except:
                has_lyrics = False
            
            # Vérifier la présence du BPM
            has_bpm = False
            try:
                has_bpm = (track.bpm is not None and 
                          isinstance(track.bpm, (int, float)) and 
                          track.bpm > 0)
            except:
                has_bpm = False
            
            # Compter le nombre de types de données disponibles
            data_count = 0
            if has_credits:
                data_count += 1
            if has_lyrics:
                data_count += 1
            if has_bpm:
                data_count += 1
            
            if data_count == 0:
                return "❌"  # Aucune donnée
            elif data_count >= 3:
                return "✅"  # Données complètes
            else:
                return "⚠️"  # Données partielles
                
        except Exception as e:
            print(f"Erreur dans _get_track_status_icon: {e}")
            return "❓"

    def _get_track_status_details(self, track):
        """Retourne les détails du statut pour le tooltip/debug"""
        details = []
        
        try:
            # Vérifier les crédits
            try:
                music_credits = track.get_music_credits()
                if music_credits and len(music_credits) > 0:
                    details.append(f"🏷️ {len(music_credits)} crédits")
            except:
                pass
            
            # Vérifier les paroles
            try:
                if (hasattr(track, 'lyrics') and 
                    track.lyrics is not None and 
                    isinstance(track.lyrics, str) and 
                    len(track.lyrics.strip()) > 0):
                    word_count = len(track.lyrics.split())
                    details.append(f"📝 {word_count} mots")
            except:
                pass
            
            # Vérifier le BPM
            try:
                if (track.bpm is not None and 
                    isinstance(track.bpm, (int, float)) and 
                    track.bpm > 0):
                    details.append(f"🎼 {int(track.bpm)} BPM")
            except:
                pass
            
            if not details:
                return "Aucune donnée disponible"
            
            return " • ".join(details)
        except Exception as e:
            return f"Erreur: {str(e)}"

    def _populate_tracks_table(self, tracks):
        """Remplit le tableau avec les morceaux - VERSION CORRIGÉE"""
        # Effacer le tableau
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        # Réinitialiser les sélections
        self.selected_tracks.clear()
        
        # Ajouter les morceaux
        for i, track in enumerate(tracks):
            try:
                # Gestion des features
                is_featuring = getattr(track, 'is_featuring', False)
                primary_artist = getattr(track, 'primary_artist_name', None)
                
                if is_featuring and primary_artist:
                    title_display = track.title
                    artist_display = primary_artist
                    title_prefix = "🎤 "
                elif is_featuring:
                    artist_name = track.artist.name if track.artist else 'Unknown'
                    title_display = f"{track.title} (feat. {artist_name})"
                    artist_display = "Artiste principal inconnu"
                    title_prefix = "🎤 "
                else:
                    title_display = track.title or "Titre inconnu"
                    artist_display = track.artist.name if track.artist else "Artiste inconnu"
                    title_prefix = ""
                
                # Date de sortie
                date_display = "-"
                try:
                    if track.release_date:
                        if hasattr(track.release_date, 'strftime'):
                            date_display = track.release_date.strftime('%Y-%m-%d')
                        else:
                            date_display = str(track.release_date)[:10]
                except:
                    date_display = "-"
                
                # Statut
                try:
                    status_icon = self._get_track_status_icon(track)
                    status_details = self._get_track_status_details(track)
                except Exception as e:
                    status_icon = "❓"
                    status_details = f"Erreur: {str(e)}"
                
                # Crédits
                try:
                    music_credits = track.get_music_credits()
                    credits_count = len(music_credits) if music_credits else 0
                except:
                    credits_count = 0
                
                # BPM
                bmp_display = track.bpm if track.bpm is not None else "-"
                
                # Valeurs pour le tableau
                values = (
                    title_prefix + title_display,    # Titre
                    artist_display,                  # Artiste
                    track.album or "-",              # Album
                    date_display,                    # Date
                    credits_count,                   # Crédits
                    bmp_display,                     # BPM
                    status_icon                      # Statut
                )
                
                # Créer l'item
                item = self.tree.insert("", "end", text="☑", values=values, tags=(str(i), status_details))
                
                # Sélectionner par défaut
                self.selected_tracks.add(i)
                
            except Exception as e:
                print(f"Erreur ajout track {i}: {e}")
                # Ligne d'erreur
                error_values = (
                    f"ERREUR: {getattr(track, 'title', 'Track inconnu')}",
                    "Erreur", "-", "-", "0", "-", "❓"
                )
                self.tree.insert("", "end", text="☐", values=error_values, tags=(str(i), f"Erreur: {str(e)}"))
        
        self._update_selection_count()

    def _sort_column(self, col):
        """Trie les morceaux selon la colonne sélectionnée - VERSION CORRIGÉE"""
        if not self.current_artist or not self.current_artist.tracks:
            return
        
        try:
            # Sauvegarder les sélections
            selected_track_ids = set()
            for track_index in self.selected_tracks:
                if 0 <= track_index < len(self.current_artist.tracks):
                    track = self.current_artist.tracks[track_index]
                    if hasattr(track, 'id') and track.id:
                        selected_track_ids.add(track.id)
            
            # Basculer l'ordre de tri
            reverse = self.sort_reverse.get(col, False)
            self.sort_reverse[col] = not reverse
            
            # Créer liste avec index
            tracks_with_index = [(i, track) for i, track in enumerate(self.current_artist.tracks)]
            
            # Fonction de tri
            def sort_key(item):
                index, track = item
                
                try:
                    if col == "Titre":
                        if hasattr(track, 'get_display_title'):
                            return track.get_display_title().lower()
                        return (track.title or "").lower()
                    elif col == "Artiste principal":
                        if hasattr(track, 'get_display_artist'):
                            return track.get_display_artist().lower()
                        return (track.artist.name if track.artist else "").lower()
                    elif col == "Album":
                        return (track.album or "zzz").lower()
                    elif col == "Date sortie":
                        if track.release_date:
                            return track.release_date
                        from datetime import datetime
                        return datetime.min if reverse else datetime.max
                    elif col == "Crédits":
                        try:
                            music_credits = track.get_music_credits()
                            return len(music_credits) if music_credits else 0
                        except:
                            return 0
                    elif col == "BPM":
                        return track.bpm if track.bpm is not None else 0
                    elif col == "Statut":
                        try:
                            # Logique de tri par statut
                            has_credits = False
                            has_lyrics = False
                            has_bpm = False
                            
                            try:
                                music_credits = track.get_music_credits()
                                has_credits = len(music_credits) > 0 if music_credits else False
                            except:
                                pass
                            
                            try:
                                has_lyrics = (hasattr(track, 'lyrics') and 
                                            track.lyrics is not None and 
                                            len(str(track.lyrics).strip()) > 0)
                            except:
                                pass
                            
                            try:
                                has_bpm = (track.bpm is not None and track.bpm > 0)
                            except:
                                pass
                            
                            data_count = sum([has_credits, has_lyrics, has_bpm])
                            
                            if data_count == 0:
                                return 0  # ❌
                            elif data_count >= 3:
                                return 2  # ✅
                            else:
                                return 1  # ⚠️
                        except:
                            return -1  # Erreur
                except Exception as e:
                    print(f"Erreur tri colonne {col}: {e}")
                    return ""
                
                return ""
            
            # Trier
            tracks_with_index.sort(key=sort_key, reverse=reverse)
            
            # Réorganiser
            self.current_artist.tracks = [track for index, track in tracks_with_index]
            
            # Restaurer sélections
            self.selected_tracks.clear()
            for new_index, track in enumerate(self.current_artist.tracks):
                if hasattr(track, 'id') and track.id and track.id in selected_track_ids:
                    self.selected_tracks.add(new_index)
            
            # Recréer affichage
            self._populate_tracks_table(self.current_artist.tracks)
            
            # Mettre à jour en-tête
            direction = "↓" if reverse else "↑"
            self.tree.heading(col, text=f"{col} {direction}")
            
            # Réinitialiser autres en-têtes
            for other_col in ["Titre", "Artiste principal", "Album", "Date sortie", "Crédits", "BPM", "Statut"]:
                if other_col != col:
                    self.tree.heading(other_col, text=other_col)
                    
        except Exception as e:
            print(f"Erreur générale dans _sort_column: {e}")

    def _update_track_in_table(self, track_name):
        """Met à jour une ligne spécifique du tableau - VERSION CORRIGÉE"""
        if not self.current_artist:
            return
            
        for item in self.tree.get_children():
            try:
                item_values = self.tree.item(item)["values"]
                if item_values and len(item_values) > 0:
                    # Comparer le titre
                    item_title = item_values[0].replace("🎤 ", "")
                    
                    # Retrouver le track
                    for i, track in enumerate(self.current_artist.tracks):
                        try:
                            track_display = track.title
                            if hasattr(track, 'is_featuring') and track.is_featuring:
                                artist_name = track.artist.name if track.artist else 'Unknown'
                                track_display = f"{track.title} (feat. {artist_name})"
                            
                            if track_display == item_title or (track.title and track.title in item_title):
                                # Mettre à jour
                                title_prefix = "🎤 " if (hasattr(track, 'is_featuring') and track.is_featuring) else ""
                                
                                if hasattr(track, 'get_display_artist'):
                                    artist_display = track.get_display_artist()
                                else:
                                    artist_display = track.artist.name if track.artist else "Inconnu"
                                
                                # Date
                                date_display = "-"
                                try:
                                    if track.release_date and hasattr(track.release_date, 'strftime'):
                                        date_display = track.release_date.strftime('%Y-%m-%d')
                                except:
                                    pass
                                
                                # Statut
                                try:
                                    status_icon = self._get_track_status_icon(track)
                                    status_details = self._get_track_status_details(track)
                                except:
                                    status_icon = "❓"
                                    status_details = "Erreur de calcul"
                                
                                # Crédits
                                try:
                                    music_credits = track.get_music_credits()
                                    credits_count = len(music_credits) if music_credits else 0
                                except:
                                    credits_count = 0
                                
                                updated_values = (
                                    title_prefix + track_display,
                                    artist_display,
                                    track.album or "-",
                                    date_display,
                                    credits_count,
                                    track.bpm or "-",
                                    status_icon
                                )
                                
                                self.tree.item(item, values=updated_values, tags=(str(i), status_details))
                                break
                        except Exception as track_error:
                            print(f"Erreur maj track {i}: {track_error}")
                            continue
                    break
            except Exception as item_error:
                print(f"Erreur maj item: {item_error}")
                continue



    def _force_update_selected(self):
        """Force la mise à jour des morceaux sélectionnés (efface les anciens crédits)"""
        if not self.current_artist or not self.current_artist.tracks:
            return
        
        if not self.selected_tracks:
            messagebox.showwarning("Attention", "Aucun morceau sélectionné")
            return
        
        # Filtrer les morceaux sélectionnés
        selected_tracks_list = [self.current_artist.tracks[i] for i in sorted(self.selected_tracks)]
        
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
        
        self.is_scraping = True
        self.force_update_button.configure(state="disabled", text="🔄 Mise à jour...")
        self.scrape_button.configure(state="disabled")
        self.progress_bar.set(0)
        
        def update_progress(current, total, track_name):
            """Callback de progression"""
            progress = current / total
            self.root.after(0, lambda: self.progress_var.set(progress))
            self.root.after(0, lambda: self.progress_label.configure(
                text=f"🔄 {current}/{total} - {track_name[:30]}..."
            ))
            # Mettre à jour la ligne dans le tableau
            self.root.after(0, lambda: self._update_track_in_table(track_name))
        
        def force_update():
            scraper = None
            try:
                logger.info(f"🔄 Début de la mise à jour forcée de {len(selected_tracks_list)} morceaux")
                
                # ✅ ÉTAPE 1: Nettoyer les anciens crédits
                self.root.after(0, lambda: self.progress_label.configure(text="🧹 Nettoyage des anciens crédits..."))
                
                cleanup_results = self.data_manager.force_update_multiple_tracks(
                    selected_tracks_list, 
                    progress_callback=lambda i, t, n: self.root.after(0, lambda: self.progress_label.configure(text=f"🧹 Nettoyage {i}/{t}"))
                )
                
                # ✅ ÉTAPE 2: Re-scraper les morceaux
                self.root.after(0, lambda: self.progress_label.configure(text="🔍 Re-scraping des crédits..."))
                
                scraper = GeniusScraper(headless=True)
                scraping_results = scraper.scrape_multiple_tracks(
                    selected_tracks_list,
                    progress_callback=update_progress
                )
                
                # ✅ ÉTAPE 3: Sauvegarder les nouveaux crédits
                for track in selected_tracks_list:
                    track.artist = self.current_artist
                    self.data_manager.force_update_track_credits(track)
                
                # Préparer le résumé
                total_before = cleanup_results['total_credits_before']
                total_after = cleanup_results['total_credits_after']
                
                # Compter les nouveaux crédits après scraping
                final_credits = sum(len(t.credits) for t in selected_tracks_list)
                music_credits = sum(len(t.get_music_credits()) for t in selected_tracks_list)
                video_credits = sum(len(t.get_video_credits()) for t in selected_tracks_list)
                
                # Afficher le résumé détaillé
                self.root.after(0, lambda: messagebox.showinfo(
                    "🎉 Mise à jour forcée terminée",
                    f"✅ Mise à jour forcée terminée avec succès !\n\n"
                    f"📊 RÉSULTATS:\n"
                    f"• Morceaux traités: {cleanup_results['updated']}/{len(selected_tracks_list)}\n"
                    f"• Scraping réussi: {scraping_results['success']}\n"
                    f"• Scraping échoué: {scraping_results['failed']}\n\n"
                    f"🏷️ CRÉDITS:\n"
                    f"• Avant: {total_before} crédits\n"
                    f"• Après: {final_credits} crédits\n"
                    f"• 🎵 Musicaux: {music_credits}\n"
                    f"• 🎬 Vidéo: {video_credits}\n\n"
                    f"✨ Les crédits sont maintenant séparés correctement !"
                ))
                
                # Mettre à jour l'affichage
                self.root.after(0, self._update_artist_info)
                self.root.after(0, self._update_statistics)
                
            except Exception as err:
                error_msg = str(err) if str(err) != "None" else "Erreur inconnue lors de la mise à jour forcée"
                logger.error(f"❌ Erreur lors de la mise à jour forcée: {error_msg}", exc_info=True)
                self.root.after(0, lambda: messagebox.showerror(
                    "Erreur",
                    f"❌ Erreur lors de la mise à jour forcée:\n{error_msg}"
                ))
            finally:
                # S'assurer que le scraper est fermé
                if scraper:
                    try:
                        scraper.close()
                    except:
                        pass
                
                self.is_scraping = False
                self.root.after(0, lambda: self.force_update_button.configure(
                    state="normal",
                    text="🔄 Mise à jour forcée"
                ))
                self.root.after(0, lambda: self.scrape_button.configure(state="normal"))
                self.root.after(0, lambda: self.progress_bar.set(0))
                self.root.after(0, lambda: self.progress_label.configure(text=""))
        
        threading.Thread(target=force_update, daemon=True).start()
            
        def sort_key(item):
            index, track = item
            
            try:
                if col == "Titre":
                    return track.get_display_title().lower() if hasattr(track, 'get_display_title') else (track.title or "").lower()
                elif col == "Artiste principal":
                    return track.get_display_artist().lower() if hasattr(track, 'get_display_artist') else ""
                elif col == "Album":
                    return (track.album or "zzz").lower()
                elif col == "Date sortie":
                    if track.release_date:
                        return track.release_date
                    else:
                        return datetime.min if reverse else datetime.max
                elif col == "Crédits":
                    try:
                        music_credits = track.get_music_credits()
                        return len(music_credits) if music_credits else 0
                    except Exception:
                        return 0
                elif col == "BPM":
                    return track.bpm if track.bpm is not None else 0
                elif col == "Statut":
                    # ✅ CORRECTION : Tri par niveau de complétude avec gestion d'erreur
                    try:
                        # Vérifications sécurisées
                        try:
                            music_credits = track.get_music_credits()
                            has_credits = len(music_credits) > 0 if music_credits else False
                        except Exception:
                            has_credits = False
                        
                        try:
                            has_lyrics = (hasattr(track, 'lyrics') and 
                                        track.lyrics is not None and 
                                        isinstance(track.lyrics, str) and 
                                        track.lyrics.strip() != "")
                        except Exception:
                            has_lyrics = False
                        
                        try:
                            has_bpm = (track.bpm is not None and 
                                    isinstance(track.bpm, (int, float)) and 
                                    track.bpm > 0)
                        except Exception:
                            has_bpm = False
                        
                        # Conversion sécurisée
                        data_types_count = int(bool(has_credits)) + int(bool(has_lyrics)) + int(bool(has_bpm))
                        
                        if data_types_count == 0:
                            return 0  # ❌ Aucune donnée (priorité haute pour tri croissant)
                        elif data_types_count >= 3:
                            return 2  # ✅ Données complètes
                        else:
                            return 1  # ⚠️ Données partielles
                            
                    except Exception as e:
                        logger.error(f"Erreur lors du tri par statut: {e}")
                        return -1  # Erreur = priorité très haute
                        
            except Exception as e:
                logger.error(f"Erreur lors du tri de la colonne {col}: {e}")
                return ""
            
            return ""
        
        # Trier avec gestion d'erreur
        try:
            tracks_with_index.sort(key=sort_key, reverse=reverse)
        except Exception as e:
            logger.error(f"Erreur lors du tri: {e}")
            return
        
        # Réorganiser la liste des morceaux
        self.current_artist.tracks = [track for index, track in tracks_with_index]
        
        # Restaurer les sélections après le tri
        self.selected_tracks.clear()
        for new_index, track in enumerate(self.current_artist.tracks):
            if hasattr(track, 'id') and track.id and track.id in selected_track_ids:
                self.selected_tracks.add(new_index)
        
        # Recréer l'affichage
        self._populate_tracks_table(self.current_artist.tracks)
        
        # Mettre à jour l'en-tête pour indiquer le tri
        direction = "↓" if reverse else "↑"
        self.tree.heading(col, text=f"{col} {direction}")
        
        # Réinitialiser les autres en-têtes
        for other_col in ["Titre", "Artiste principal", "Album", "Date sortie", "Crédits", "BPM", "Statut"]:
            if other_col != col:
                self.tree.heading(other_col, text=other_col)

        def sort_key(item):
            index, track = item
            
            if col == "Titre":
                return track.get_display_title().lower()
            elif col == "Artiste principal":
                return track.get_display_artist().lower()
            elif col == "Album":
                return (track.album or "zzz").lower()
            elif col == "Date sortie":
                if track.release_date:
                    return track.release_date
                else:
                    return datetime.min if reverse else datetime.max
            elif col == "Crédits":
                return len(track.get_music_credits())
            elif col == "BPM":
                return track.bpm or 0
            elif col == "Statut":
                # ✅ NOUVEAU : Tri par niveau de complétude
                has_credits = len(track.get_music_credits()) > 0
                has_lyrics = hasattr(track, 'lyrics') and track.lyrics and track.lyrics.strip()
                has_bpm = track.bpm is not None and track.bpm > 0
                
                data_types_count = sum([has_credits, has_lyrics, has_bpm])
                
                if data_types_count == 0:
                    return 0  # ❌ Aucune donnée (priorité haute pour tri croissant)
                elif data_types_count >= 3:
                    return 2  # ✅ Données complètes
                else:
                    return 1  # ⚠️ Données partielles
            return ""
        
        # Trier
        tracks_with_index.sort(key=sort_key, reverse=reverse)
        
        # Réorganiser la liste des morceaux
        self.current_artist.tracks = [track for index, track in tracks_with_index]
        
        # Restaurer les sélections après le tri
        self.selected_tracks.clear()
        for new_index, track in enumerate(self.current_artist.tracks):
            if track.id and track.id in selected_track_ids:
                self.selected_tracks.add(new_index)
        
        # Recréer l'affichage
        self._populate_tracks_table(self.current_artist.tracks)
        
        # Mettre à jour l'en-tête pour indiquer le tri
        direction = "↓" if reverse else "↑"
        self.tree.heading(col, text=f"{col} {direction}")
        
        # Réinitialiser les autres en-têtes
        for other_col in ["Titre", "Artiste principal", "Album", "Date sortie", "Crédits", "BPM", "Statut"]:
            if other_col != col:
                self.tree.heading(other_col, text=other_col)


    def _get_track_status_icon(self, track: Track) -> str:
        """Retourne l'icône de statut selon le niveau de complétude des données - VERSION CORRIGÉE"""
        
        # ✅ CORRECTION : Vérifications plus robustes avec gestion des None
        
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
            has_bpm = (track.bpm is not None and 
                      isinstance(track.bpm, (int, float)) and 
                      track.bpm > 0)
        except Exception:
            has_bpm = False
        
        # ✅ CORRECTION : Conversion explicite en bool pour éviter les None
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

    def _get_track_status_details(self, track: Track) -> str:
        """Retourne les détails du statut pour le tooltip/debug - VERSION CORRIGÉE"""
        
        details = []
        
        # Vérifier les crédits
        try:
            music_credits = track.get_music_credits()
            if music_credits and len(music_credits) > 0:
                details.append(f"🏷️ {len(music_credits)} crédits")
        except Exception:
            pass
        
        # Vérifier les paroles
        try:
            if (hasattr(track, 'lyrics') and 
                track.lyrics is not None and 
                isinstance(track.lyrics, str) and 
                track.lyrics.strip()):
                word_count = len(track.lyrics.split())
                details.append(f"📝 {word_count} mots")
        except Exception:
            pass
        
        # Vérifier le BPM
        try:
            if (track.bpm is not None and 
                isinstance(track.bpm, (int, float)) and 
                track.bpm > 0):
                details.append(f"🎼 {int(track.bpm)} BPM")
        except Exception:
            pass
        
        if not details:
            return "Aucune donnée disponible"
        
        return " • ".join(details)


    def _show_track_details(self, event):
        """Affiche les détails d'un morceau - VERSION CORRIGÉE AVEC ONGLETS PROPRES"""
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
                url_frame = ctk.CTkFrame(info_frame, fg_color="transparent")
                url_frame.pack(anchor="w", padx=10, pady=5)
                
                ctk.CTkLabel(url_frame, text="🔗 Genius: ").pack(side="left")
                
                url_label = ctk.CTkLabel(
                    url_frame, 
                    text=track.genius_url,
                    text_color="blue",
                    cursor="hand2"
                )
                url_label.pack(side="left")
                
                import webbrowser
                url_label.bind("<Button-1>", lambda e: webbrowser.open(track.genius_url))
            
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
            
            # === ONGLET 4: INFORMATIONS TECHNIQUES (si nécessaire) ===
            if (track.scraping_errors or 
                hasattr(track, 'popularity') or 
                track.spotify_id or 
                track.discogs_id or
                getattr(track, 'artwork_url', None)):
                
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
                
                # Debug featuring
                if is_featuring:
                    tech_textbox.insert("end", f"\n🎤 DEBUG FEATURING:\n")
                    tech_textbox.insert("end", f"• is_featuring: {is_featuring}\n")
                    tech_textbox.insert("end", f"• primary_artist_name: {primary_artist}\n")
                    tech_textbox.insert("end", f"• featured_artists: {featured_artists}\n")
                
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
                
                # ✅ CORRECTION 1: Vérifier d'abord dans la base de données locale
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
                
                # ✅ CORRECTION 2: Seulement si pas trouvé en base, chercher sur Genius
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
                    
                    # ✅ CORRECTION 3: Message d'erreur plus utile
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
                
                # ✅ CORRECTION 4: Log détaillé pour debug
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
                
                self._populate_tracks_table(self.current_artist.tracks)
                
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

    def _format_lyrics_for_display(self, lyrics: str) -> str:
        """Formate les paroles pour l'affichage dans l'interface"""
        if not lyrics:
            return "Aucunes paroles disponibles"
        
        lines = lyrics.split('\n')
        formatted_lines = []
        
        for line in lines:
            line = line.strip()
            if not line:
                formatted_lines.append('')
                continue
                
            # Sections : mise en forme spéciale
            if line.startswith('[') and line.endswith(']'):
                formatted_lines.append('')
                formatted_lines.append(f"{'─' * 25} {line} {'─' * 25}")
                formatted_lines.append('')
            
            # Mentions d'artistes : indentation
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

    def _start_lyrics_scraping(self):
        """Lance le scraping des paroles pour les morceaux sélectionnés"""
        if not self.current_artist or not self.current_artist.tracks:
            return
        
        if not self.selected_tracks:
            messagebox.showwarning("Attention", "Aucun morceau sélectionné")
            return
        
        # Filtrer les morceaux sélectionnés
        selected_tracks_list = [self.current_artist.tracks[i] for i in sorted(self.selected_tracks)]
        
        # Confirmation
        result = messagebox.askyesno(
            "Scraping des paroles",
            f"Voulez-vous scraper les paroles de {len(selected_tracks_list)} morceaux sélectionnés ?\n\n"
            "📝 Cela récupérera :\n"
            "• Les paroles complètes\n"
            "• Structure nettoyée (sections, artistes)\n"
            "• Suppression automatique des publicités\n\n"
            f"⏱️ Temps estimé : ~{len(selected_tracks_list) * 0.5:.1f} minutes"
        )
        
        if not result:
            return
        
        self.lyrics_button.configure(state="disabled", text="📝 Scraping paroles...")
        self.progress_bar.set(0)
        
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
                self.root.after(0, lambda: messagebox.showinfo(
                    "📝 Paroles récupérées",
                    f"✅ Scraping des paroles terminé !\n\n"
                    f"📊 Résultats :\n"
                    f"• Morceaux traités : {results['success']}\n"
                    f"• Paroles récupérées : {results['lyrics_scraped']}\n"
                    f"• Échecs : {results['failed']}\n\n"
                    f"💡 Les paroles sont maintenant disponibles dans les détails des morceaux"
                ))
                
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

    def _get_track_status_icon(self, track: Track) -> str:
        """Retourne l'icône de statut selon le niveau de complétude des données"""
        
        # Vérifier la présence des différents types de données
        has_credits = len(track.get_music_credits()) > 0
        has_lyrics = hasattr(track, 'lyrics') and track.lyrics and track.lyrics.strip()
        has_bpm = track.bpm is not None and track.bpm > 0
        
        # Compter le nombre de types de données disponibles
        data_types_count = sum([has_credits, has_lyrics, has_bpm])
        
        if data_types_count == 0:
            return "❌"  # Aucune donnée
        elif data_types_count >= 3:
            return "✅"  # Données complètes (crédits + paroles + BPM)
        else:
            return "⚠️"  # Données partielles

    def _get_track_status_details(self, track: Track) -> str:
        """Retourne les détails du statut pour le tooltip/debug"""
        
        has_credits = len(track.get_music_credits()) > 0
        has_lyrics = hasattr(track, 'lyrics') and track.lyrics and track.lyrics.strip()
        has_bpm = track.bpm is not None and track.bpm > 0
        
        details = []
        if has_credits:
            details.append(f"🏷️ {len(track.get_music_credits())} crédits")
        if has_lyrics:
            word_count = len(track.lyrics.split()) if track.lyrics else 0
            details.append(f"📝 {word_count} mots")
        if has_bpm:
            details.append(f"🎼 {track.bpm} BPM")
        
        if not details:
            return "Aucune donnée disponible"
        
        return " • ".join(details)
    
    def _get_simple_status(self, track):
        """Calcule le statut simple d'un track"""
        try:
            has_credits = len(track.get_music_credits() or []) > 0
            has_lyrics = bool(getattr(track, 'lyrics', None) and len(str(track.lyrics).strip()) > 0)
            has_bpm = bool(track.bpm and track.bmp > 0)
            
            data_count = sum([has_credits, has_lyrics, has_bpm])
            
            if data_count == 0:
                return "❌"
            elif data_count >= 3:
                return "✅"
            else:
                return "⚠️"
        except:
            return "❓"