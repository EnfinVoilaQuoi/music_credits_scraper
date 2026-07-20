"""Interface graphique principale de l'application - VERSION AMÉLIORÉE"""

from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from src.api.genius_api import GeniusAPI
from src.config import THEME, WINDOW_HEIGHT, WINDOW_WIDTH
from src.gui import helpers
from src.gui.certification_update_gui import CertificationUpdateDialog
from src.gui.dialogs import artist_selection, scraping_menu
from src.gui.panels import albums_view, tracks_table
from src.gui.windows import artist_loader
from src.gui.windows.export_studio import show_export_studio
from src.gui.windows.source_health import show_source_health
from src.gui.windows.track_details import TrackDetailsWindow
from src.gui.workers import enrichment, retrieval, streams
from src.gui.workers.lifecycle import start_worker
from src.models import Artist, Track
from src.utils.data_enricher import DataEnricher
from src.utils.data_manager import DataManager
from src.utils.deleted_tracks_manager import DeletedTracksManager
from src.utils.disabled_tracks_manager import DisabledTracksManager
from src.utils.logger import get_logger

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
            headless_reccobeats=True, headless_songbpm=True, headless_spotify_scraper=True
        )
        self.current_artist: Artist | None = None
        self.tracks: list[Track] = []

        # Variables
        self.is_scraping = False
        self.selected_tracks = set()  # Stocker les morceaux sélectionnés
        self.disabled_tracks = set()  # Stocker les morceaux désactivés
        self.sort_column = None
        self.sort_reverse = False
        self.last_selected_index = None  # Sélection multiple
        self.disabled_tracks_manager = DisabledTracksManager()
        # Purge des fichiers de désactivation orphelins (> 30 j sans modif)
        self.disabled_tracks_manager.cleanup_old_files()
        self.deleted_tracks_manager = DeletedTracksManager()
        self.open_detail_windows = {}  # Dict: {track_id: (window, track_object)}
        self.source_health_window = None  # Fenêtre « État des sources » (singleton)
        self.export_studio_window = None  # Fenêtre « Export studio » (singleton)

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

        self.artist_entry = ctk.CTkEntry(
            search_frame, width=300, placeholder_text="Nom de l'artiste"
        )
        self.artist_entry.pack(side="left", padx=5)
        self.artist_entry.bind("<Return>", lambda e: self._search_artist())

        self.search_button = ctk.CTkButton(
            search_frame, text="Rechercher", command=self._search_artist, width=100
        )
        self.search_button.pack(side="left", padx=5)

        self.load_button = ctk.CTkButton(
            search_frame,
            text="Charger existant",
            command=lambda: artist_loader.load_existing_artist(self),
            width=120,
        )
        self.load_button.pack(side="left", padx=5)

        # État des sources (santé des scrapers/APIs) — aligné à droite, toujours accessible
        self.health_button = ctk.CTkButton(
            search_frame,
            text="État sources",
            command=lambda: show_source_health(self),
            width=120,
            fg_color="#00695c",
            hover_color="#004d40",
            text_color="white",
        )
        self.health_button.pack(side="right", padx=5)

        # === Section infos artiste ===
        info_frame = ctk.CTkFrame(main_frame)
        info_frame.pack(fill="x", padx=5, pady=5)

        self.artist_info_label = ctk.CTkLabel(
            info_frame, text="Aucun artiste sélectionné", font=("Arial", 16, "bold")
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
            command=lambda: retrieval.get_tracks(self),
            state="disabled",
            width=150,
        )
        self.get_tracks_button.pack(side="left", padx=5)

        # 2. Crédits & Paroles (menu combiné)
        self.scrape_button = ctk.CTkButton(
            control_frame,
            text="Crédits & Paroles",
            command=lambda: scraping_menu.show_scraping_menu(self),
            state="disabled",
            width=180,
            fg_color="#B8860B",  # Jaune foncé (DarkGoldenrod)
            hover_color="#996515",  # Jaune encore plus foncé au survol
            text_color="white",  # Texte blanc
        )
        self.scrape_button.pack(side="left", padx=5)

        # 5. Données additionnelles
        self.enrich_button = ctk.CTkButton(
            control_frame,
            text="Données Add.",
            command=lambda: enrichment.start_enrichment(self),
            state="disabled",
            width=150,
            fg_color="#B22222",  # Rouge foncé (FireBrick)
            hover_color="#8B0000",  # Rouge très foncé (DarkRed) au survol
            text_color="white",  # Texte blanc
        )
        self.enrich_button.pack(side="left", padx=5)

        # 6. Bouton de mise à jour des certifications
        self.update_certif_button = ctk.CTkButton(
            control_frame,
            text="Certifications",
            command=self._open_certification_update,
            width=150,
            fg_color="darkgreen",
            hover_color="green",
        )
        self.update_certif_button.pack(side="left", padx=5)

        # 7. Nb Streams (Spotify + YouTube Music)
        self.streams_button = ctk.CTkButton(
            control_frame,
            text="Nb Streams",
            command=lambda: streams.start_streams_update(self),
            state="disabled",
            width=130,
            fg_color="#1a237e",
            hover_color="#283593",
            text_color="white",
        )
        self.streams_button.pack(side="left", padx=5)

        # 8. Export studio (aligné à droite) — ouvre la fenêtre des générateurs
        # (Bubble Prod…) ; l'export JSON historique vit dedans.
        self.export_button = ctk.CTkButton(
            control_frame,
            text="Export studio",
            command=lambda: show_export_studio(self),
            state="disabled",
            width=110,
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
            command=lambda: tracks_table.check_selected_tracks(self),
            width=35,
            font=("Arial", 12),
        ).pack(side="left", padx=(5, 2))

        ctk.CTkButton(
            selection_frame,
            text="Tout sélectionner",
            command=lambda: tracks_table.select_all_tracks(self),
            width=120,
        ).pack(side="left", padx=5)

        ctk.CTkButton(
            selection_frame,
            text="Tout désélectionner",
            command=lambda: tracks_table.deselect_all_tracks(self),
            width=120,
        ).pack(side="left", padx=5)

        # ✅ NOUVEAU: Boutons pour les morceaux désactivés
        ctk.CTkButton(
            selection_frame,
            text="Désactiver sélectionnés",
            command=lambda: tracks_table.disable_selected_tracks(self),
            width=140,
            fg_color="gray",
            hover_color="darkgray",
        ).pack(side="left", padx=5)

        ctk.CTkButton(
            selection_frame,
            text="Réactiver tous",
            command=lambda: tracks_table.enable_selected_tracks(self),
            width=120,
            fg_color="gray",
            hover_color="darkgray",
        ).pack(side="left", padx=5)

        self.selected_count_label = ctk.CTkLabel(selection_frame, text="")
        self.selected_count_label.pack(side="left", padx=20)

        # Bascule de vue Morceaux / Albums
        self.view_mode = "tracks"
        self.view_switch = ctk.CTkSegmentedButton(
            selection_frame,
            values=["Morceaux", "Albums"],
            command=lambda v: albums_view.set_view_mode(self, v),
            width=180,
        )
        self.view_switch.set("Morceaux")
        self.view_switch.pack(side="right", padx=10)

        # Créer le Treeview dans un conteneur approprié
        tree_container = ctk.CTkFrame(table_frame)
        tree_container.pack(fill="both", expand=True)

        tree_scroll_frame = ctk.CTkFrame(tree_container)
        tree_scroll_frame.pack(fill="both", expand=True)

        # COLONNES AVEC COLONNE PAROLES ENTRE CRÉDITS ET BPM + DURÉE ENTRE BPM ET CERTIF
        self.TRACK_COLUMNS = (
            "Titre",
            "Artiste principal",
            "Album",
            "Date sortie",
            "Crédits",
            "Paroles",
            "BPM",
            "Durée",
            "Certif.",
            "Streams",
            "Statut",
        )
        self.ALBUM_COLUMNS = (
            "Album",
            "Date sortie",
            "Morceaux",
            "Crédits",
            "Paroles",
            "Durée totale",
            "Streams Spotify",
            "Streams YTM",
        )
        self.tree = ttk.Treeview(
            tree_scroll_frame, columns=self.TRACK_COLUMNS, show="tree headings", height=15
        )

        # Variable pour suivre l'ordre de tri
        self.sort_reverse = {}

        tracks_table.configure_tree_for_tracks(self)

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
        self.tree.bind("<Double-Button-1>", lambda e: tracks_table.show_track_details(self, e))
        self.tree.bind("<Button-1>", lambda e: tracks_table.on_tree_click(self, e))
        # Cocher-glisser : maintenir le clic sur une coche et glisser
        self.tree.bind("<B1-Motion>", lambda e: tracks_table.on_tree_drag(self, e))
        self.tree.bind("<ButtonRelease-1>", lambda e: tracks_table.on_tree_release(self, e))
        self.tree.bind(
            "<Button-3>", lambda e: tracks_table.on_right_click(self, e)
        )  # Clic droit pour menu contextuel

        # === Section statistiques ===
        stats_frame = ctk.CTkFrame(main_frame)
        stats_frame.pack(fill="x", padx=5, pady=5)

        self.stats_label = ctk.CTkLabel(stats_frame, text="", font=("Arial", 12))
        self.stats_label.pack()

    # ──────────────────────────────────────────────────────────────────────
    # Bascule de vue Morceaux / Albums
    # ──────────────────────────────────────────────────────────────────────

    def _populate_tracks_table(self):
        """Rafraîchit la table des morceaux (logique dans panels/tracks_table.py)"""
        tracks_table.populate_tracks_table(self)

    def _reload_tracks_and_refresh(self):
        """Recharge les morceaux depuis la base puis réaffiche le tableau.

        À utiliser après toute écriture DB faite par des objets séparés
        (MàJ streams Kworb/YTM…) : les tracks en mémoire de la GUI ne voient
        pas ces écritures sans reload.
        """
        try:
            if self.current_artist:
                self.current_artist.tracks = self.data_manager.get_artist_tracks(
                    self.current_artist.id
                )
        except Exception as e:
            logger.error(f"Rechargement des morceaux échoué: {e}")
        self._populate_tracks_table()

    def _show_track_details_for_track(self, track: Track):
        """Affiche les détails d'un morceau (fenêtre extraite dans windows/track_details.py)"""
        TrackDetailsWindow(self, track)

    def _open_certification_update(self):
        """Ouvre la fenêtre de mise à jour des certifications"""
        try:
            # Ouvrir la fenêtre (pré-remplie avec l'artiste courant + sa
            # discographie, pour l'audit des certifs orphelines). Depuis E7f-h,
            # les certifs passent par cert_matcher (CSV clean) + apply_certifications :
            # plus de CertificationManager (créait une DB vestigiale + détournait
            # la config logging globale).
            current_name = self.current_artist.name if self.current_artist else None
            artist_tracks = None
            artist_albums = None
            if self.current_artist and self.current_artist.tracks:
                artist_tracks = [t.title for t in self.current_artist.tracks if t.title]
                # Noms d'albums distincts (pour l'audit des certifs d'albums)
                artist_albums = sorted({t.album for t in self.current_artist.tracks if t.album})
            dialog = CertificationUpdateDialog(
                self.root,
                default_artist=current_name,
                artist_tracks=artist_tracks,
                artist_albums=artist_albums,
                app=self,
            )
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
            initialfile=f"{self.current_artist.name.replace(' ', '_').lower()}_credits.json",
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
                        discogs_id=self.current_artist.discogs_id,
                    )

                    # Ajouter seulement les morceaux actifs
                    for track in self.current_artist.tracks:
                        if not self._is_track_disabled(track):
                            temp_artist.tracks.append(track)

                    # Exporter l'artiste filtré
                    self.data_manager.export_to_json(temp_artist.name, filepath)

                    disabled_count = len(self.disabled_tracks)
                    messagebox.showinfo(
                        "Succès",
                        f"Données exportées vers:\n{filepath}\n\n"
                        f"✅ {len(temp_artist.tracks)} morceaux exportés\n"
                        f"⊘ {disabled_count} morceaux désactivés exclus",
                    )
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
                    logger.info(
                        f"✅ Artiste trouvé en base: {artist.name} avec {len(artist.tracks)} morceaux"
                    )
                    self.current_artist = artist
                    self.root.after(0, self._update_artist_info)
                    self.root.after(0, lambda: tracks_table.apply_default_sort(self))
                    self.root.after(
                        0,
                        lambda: messagebox.showinfo(
                            "Artiste chargé",
                            f"✅ Artiste '{artist.name}' chargé depuis la base de données\n"
                            f"📀 {len(artist.tracks)} morceaux disponibles\n"
                            f"🎤 ID Genius: {artist.genius_id}\n\n"
                            "Vous pouvez maintenant scraper ou enrichir les données.",
                        ),
                    )
                    return

                # Construire l'URL Genius depuis le nom (sans appel API)
                logger.info("🌐 Artiste non trouvé en base, recherche via URL Genius...")
                slug = helpers.build_genius_slug(artist_name)
                genius_url = f"https://genius.com/artists/{slug}"
                logger.info(f"🔗 Tentative : {genius_url}")

                genius_artist = artist_selection.fetch_artist_from_genius_url(
                    self, genius_url, artist_name
                )

                if not (genius_artist and genius_artist.genius_id):
                    # Slug incorrect ou page inexistante → dialog de saisie manuelle
                    logger.info(f"⚠️ Artiste non trouvé sur {genius_url}, affichage du dialog")
                    import queue as _queue

                    result_q = _queue.Queue()
                    self.root.after(
                        0,
                        lambda: artist_selection.show_artist_selection_dialog(
                            self, [], artist_name, result_q
                        ),
                    )
                    genius_artist = result_q.get()
                    if genius_artist is None:
                        return  # Annulé par l'utilisateur

                self.data_manager.save_artist(genius_artist)
                self.current_artist = genius_artist
                self.root.after(0, self._update_artist_info)
                self.root.after(0, lambda: tracks_table.apply_default_sort(self))
                self.root.after(
                    0,
                    lambda: messagebox.showinfo(
                        "Artiste trouvé",
                        f"✅ Artiste trouvé : '{genius_artist.name}'\n"
                        f"🎤 ID Genius: {genius_artist.genius_id}\n\n"
                        "Cliquez sur 'Récupérer les morceaux' pour commencer.",
                    ),
                )

            except Exception as e:
                error_msg = str(e) if str(e) else "Erreur inconnue lors de la recherche"
                logger.error(f"❌ Erreur lors de la recherche: {error_msg}")
                import traceback

                logger.debug(f"Traceback complet: {traceback.format_exc()}")
                self.root.after(
                    0,
                    lambda: messagebox.showerror(
                        "Erreur",
                        f"❌ Erreur lors de la recherche:\n{error_msg}\n\n"
                        "Consultez les logs pour plus de détails.",
                    ),
                )
            finally:
                self.root.after(
                    0, lambda: self.search_button.configure(state="normal", text="Rechercher")
                )

        # Lancer dans un thread
        start_worker(search)

    def _close_all_detail_windows(self):
        """Ferme toutes les fenêtres de détail ouvertes (appelé lors du changement d'artiste)"""
        for window, _ in list(self.open_detail_windows.values()):
            try:
                window.destroy()
            except Exception:
                pass
        self.open_detail_windows.clear()

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
                featuring_count = sum(1 for t in self.current_artist.tracks if t.is_featuring)
                main_tracks = total_tracks - featuring_count

                # Compter les morceaux ACTIFS (non désactivés) pour les stats
                # Utilise _is_track_disabled qui vérifie par ID
                active_tracks = [
                    t for t in self.current_artist.tracks if not self._is_track_disabled(t)
                ]

                # Morceaux avec crédits musicaux (actifs uniquement)
                tracks_with_music_credits = sum(
                    1 for t in active_tracks if len(t.get_music_credits()) > 0
                )

                # Morceaux avec paroles (actifs uniquement)
                tracks_with_lyrics = sum(1 for t in active_tracks if t.lyrics and t.lyrics.strip())

                # Morceaux avec données additionnelles = BPM + Key/Mode + Durée (actifs uniquement)
                tracks_with_additional = sum(
                    1
                    for t in active_tracks
                    if (
                        t.bpm
                        and (
                            isinstance(t.bpm, (int, float))
                            and t.bpm > 0
                            or isinstance(t.bpm, str)
                            and t.bpm.isdigit()
                            and int(t.bpm) > 0
                        )
                    )
                    and (
                        t.musical_key
                        # key/mode : attributs dynamiques du mapper → hasattr requis
                        or (
                            hasattr(t, "key")
                            and t.key
                            and hasattr(t, "mode")
                            and t.mode is not None
                        )
                    )
                    and t.duration
                )

                # Morceaux avec certifications (actifs uniquement)
                tracks_with_certifications = sum(
                    1 for t in active_tracks if t.certifications and len(t.certifications) > 0
                )

                # Albums avec certifications (compter les albums uniques, pas les morceaux)
                albums_with_certifications = len(
                    {
                        t.album
                        for t in active_tracks
                        if t.album_certifications and len(t.album_certifications) > 0 and t.album
                    }
                )

                # Morceaux avec données manquantes (SANS compter les désactivés)
                tracks_with_missing_data = sum(
                    1
                    for t in active_tracks
                    if helpers.get_track_status_icon(t, self.disabled_tracks) == "⚠️"
                )

                # ✅ LIGNE 1: Statistiques principales
                line1_parts = []

                # Total avec détail features
                if featuring_count > 0:
                    line1_parts.append(
                        f"{total_tracks} Morceaux ({main_tracks} Principaux + {featuring_count} Feat)"
                    )
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
                    line1_parts.append(
                        f"{tracks_with_certifications} avec Certifications (+ {albums_with_certifications} Certifications Album)"
                    )
                else:
                    line1_parts.append(f"{tracks_with_certifications} avec Certifications")

                line1 = " - ".join(line1_parts)

                # ✅ LIGNE 2: Données manquantes
                line2 = f"{tracks_with_missing_data} Morceaux avec Données manquantes"

                # ✅ LIGNE 3: Streams et auditeurs mensuels
                try:
                    from src.utils.streams_calculator import (
                        calculate_total_monthly_listeners,
                        calculate_total_streams,
                        format_streams,
                    )

                    total_cumul = 0
                    tracks_with_streams = 0
                    for t in active_tracks:
                        est = calculate_total_streams(t.spotify_streams, t.ytm_streams)
                        if est:
                            total_cumul += est
                            tracks_with_streams += 1
                    sp_ml = self.current_artist.spotify_monthly_listeners
                    yt_ml = self.current_artist.ytm_monthly_listeners
                    total_ml = calculate_total_monthly_listeners(sp_ml, yt_ml)

                    line3_parts = []
                    if total_cumul > 0:
                        line3_parts.append(
                            f"Streams cumulés : {format_streams(total_cumul)} (estimé, {tracks_with_streams} morceaux)"
                        )
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

                if hasattr(self, "force_update_button"):
                    self.force_update_button.configure(state="normal")
                if hasattr(self, "enrich_button"):
                    self.enrich_button.configure(state="normal")
                if hasattr(self, "lyrics_button"):
                    self.lyrics_button.configure(state="normal")

            else:
                self.tracks_info_label.configure(text="Aucun morceau chargé")
                if hasattr(self, "lyrics_button"):
                    self.lyrics_button.configure(state="disabled")

            self.get_tracks_button.configure(state="normal")

    def _update_statistics(self):
        """Met à jour les statistiques affichées"""
        try:
            stats = self.data_manager.get_statistics()
            text = (
                f"Base de données: {stats['total_artists']} artistes | "
                f"{stats['total_tracks']} morceaux | "
                f"{stats['total_credits']} crédits | "
                f"{stats['recent_errors']} erreurs récentes"
            )
            self.stats_label.configure(text=text)
        except Exception as e:
            logger.error(f"Erreur lors de la mise à jour des stats: {e}")

    def _get_track_id_from_index(self, index: int) -> int | None:
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

    def _on_closing(self):
        """Gère la fermeture de l'application en sauvegardant les morceaux désactivés"""
        # 1) Arrêt PROPRE des workers AVANT tout le reste : drapeau levé, puis
        # join avec budget de temps — un save_track en cours se termine au lieu
        # d'être tué net (AUDIT §4 « threads démons sans arrêt propre »).
        try:
            from src.gui.workers.lifecycle import shutdown_workers

            try:
                self.progress_label.configure(text="⏳ Finalisation des tâches en cours…")
                self.root.update_idletasks()
            except Exception:
                pass  # feedback best-effort, la fermeture prime
            shutdown_workers()
        except Exception as e:
            logger.error(f"Arrêt des workers à la fermeture: {e}")

        try:
            # Sauvegarder les morceaux désactivés avant de fermer
            if self.current_artist and self.disabled_tracks:
                self.disabled_tracks_manager.save_disabled_tracks(
                    self.current_artist.name, self.disabled_tracks
                )
                logger.info(f"Morceaux désactivés sauvegardés pour {self.current_artist.name}")
        except Exception as e:
            logger.error(f"Erreur lors de la sauvegarde à la fermeture: {e}")

        # Fermer les navigateurs Playwright AVANT de tuer le process : si on laisse
        # faire DataEnricher.__del__ pendant l'arrêt de l'interpréteur, un browser
        # encore vivant émet des événements vers un pipe fermé → EPIPE du driver
        # Node (cosmétique mais bruyant). Best-effort : les drivers créés dans des
        # threads morts sont thread-affines et déjà fermés par les finally des
        # workers ; ici on ferme ceux du main thread, puis l'instance partagée.
        try:
            self.data_enricher.close()
        except Exception as e:
            logger.debug(f"Fermeture data_enricher à l'arrêt: {e}")
        try:
            from src.scrapers.playwright_manager import stop_playwright

            stop_playwright()
        except Exception as e:
            logger.debug(f"stop_playwright à l'arrêt: {e}")

        self.root.destroy()

    def _update_buttons_state(self):
        """Met à jour l'état des boutons selon le contexte"""

        # Si un scraping est en cours, désactiver certains boutons
        if self.is_scraping:
            self.scrape_button.configure(state="disabled")
            if hasattr(self, "force_update_button"):
                self.force_update_button.configure(state="disabled")
            if hasattr(self, "get_tracks_button"):
                self.get_tracks_button.configure(state="disabled")
            if hasattr(self, "stop_button"):
                self.stop_button.configure(state="normal")
            # On peut laisser export et autres boutons actifs pendant le scraping
            return  # Sortir ici pour ne pas changer les autres états

        # Si pas de scraping en cours, appliquer la logique normale
        if hasattr(self, "stop_button"):
            self.stop_button.configure(state="disabled")

        if not self.current_artist:
            # Aucun artiste chargé
            self.get_tracks_button.configure(state="disabled")
            self.scrape_button.configure(state="disabled")
            self.export_button.configure(state="disabled")
            if hasattr(self, "force_update_button"):
                self.force_update_button.configure(state="disabled")
            if hasattr(self, "enrich_button"):
                self.enrich_button.configure(state="disabled")
            if hasattr(self, "lyrics_button"):
                self.lyrics_button.configure(state="disabled")
            if hasattr(self, "streams_button"):
                self.streams_button.configure(state="disabled")
        elif not self.current_artist.tracks:
            # Artiste chargé mais pas de morceaux
            self.get_tracks_button.configure(state="normal")
            self.scrape_button.configure(state="disabled")
            self.export_button.configure(state="disabled")
            if hasattr(self, "force_update_button"):
                self.force_update_button.configure(state="disabled")
            if hasattr(self, "enrich_button"):
                self.enrich_button.configure(state="disabled")
            if hasattr(self, "lyrics_button"):
                self.lyrics_button.configure(state="disabled")
            if hasattr(self, "streams_button"):
                self.streams_button.configure(state="disabled")
        else:
            # Artiste avec morceaux
            self.get_tracks_button.configure(state="normal")
            self.scrape_button.configure(state="normal")
            self.export_button.configure(state="normal")
            if hasattr(self, "force_update_button"):
                self.force_update_button.configure(state="normal")
            if hasattr(self, "enrich_button"):
                self.enrich_button.configure(state="normal")
            if hasattr(self, "lyrics_button"):
                self.lyrics_button.configure(state="normal")
            if hasattr(self, "streams_button"):
                self.streams_button.configure(state="normal")

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

    def _refresh_detail_window_if_open(self):
        """Rafraîchit les fenêtres de détails ouvertes après un scraping"""
        if not self.open_detail_windows:
            return

        logger.info(f"🔄 Rafraîchissement de {len(self.open_detail_windows)} fenêtre(s) de détails")

        # Parcourir toutes les fenêtres ouvertes
        for track_id, (window, _old_track) in list(self.open_detail_windows.items()):
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
