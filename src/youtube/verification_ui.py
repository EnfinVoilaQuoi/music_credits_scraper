import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk
import requests
from io import BytesIO
import webbrowser

class ManualVerificationInterface:
    """Interface de vérification manuelle avec aperçu"""
    
    def __init__(self, root):
        self.root = root
        self.root.title("Vérification manuelle des liens YouTube")
        self.root.geometry("1200x800")
        
        self.current_candidates = []
        self.selected_choice = None
        
        self._setup_ui()
    
    def _setup_ui(self):
        """Configuration de l'interface utilisateur"""
        
        # Frame principal
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Information du morceau à vérifier
        info_frame = ttk.LabelFrame(main_frame, text="Morceau à vérifier", padding="10")
        info_frame.grid(row=0, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))
        
        self.track_info_label = ttk.Label(info_frame, text="", font=("Arial", 12, "bold"))
        self.track_info_label.grid(row=0, column=0, sticky=tk.W)
        
        # Liste des candidats
        candidates_frame = ttk.LabelFrame(main_frame, text="Candidats trouvés", padding="10")
        candidates_frame.grid(row=1, column=0, columnspan=2, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Treeview pour les résultats
        columns = ("Titre", "Chaîne", "Durée", "Vues", "Score", "Statut")
        self.candidates_tree = ttk.Treeview(candidates_frame, columns=columns, show="headings", height=10)
        
        for col in columns:
            self.candidates_tree.heading(col, text=col)
            self.candidates_tree.column(col, width=150)
        
        # Scrollbar pour la treeview
        scrollbar = ttk.Scrollbar(candidates_frame, orient=tk.VERTICAL, command=self.candidates_tree.yview)
        self.candidates_tree.configure(yscrollcommand=scrollbar.set)
        
        self.candidates_tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))
        
        # Bind selection event
        self.candidates_tree.bind("<<TreeviewSelect>>", self._on_candidate_select)
        
        # Frame d'aperçu
        preview_frame = ttk.LabelFrame(main_frame, text="Aperçu", padding="10")
        preview_frame.grid(row=2, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(10, 0))
        
        # Thumbnail
        self.thumbnail_label = ttk.Label(preview_frame)
        self.thumbnail_label.grid(row=0, column=0, padx=(0, 10))
        
        # Informations détaillées
        details_frame = ttk.Frame(preview_frame)
        details_frame.grid(row=0, column=1, sticky=(tk.W, tk.N))
        
        self.details_text = tk.Text(details_frame, height=8, width=60, wrap=tk.WORD)
        self.details_text.grid(row=0, column=0)
        
        # Boutons d'action
        buttons_frame = ttk.Frame(main_frame)
        buttons_frame.grid(row=3, column=0, columnspan=2, pady=(10, 0))
        
        ttk.Button(buttons_frame, text="Sélectionner", command=self._select_candidate).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(buttons_frame, text="Aperçu YouTube", command=self._open_youtube).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(buttons_frame, text="Ignorer", command=self._skip_track).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(buttons_frame, text="Recherche manuelle", command=self._manual_search).pack(side=tk.LEFT, padx=(0, 5))
        
        # Configuration du redimensionnement
        main_frame.rowconfigure(1, weight=1)
        main_frame.columnconfigure(0, weight=1)
        candidates_frame.rowconfigure(0, weight=1)
        candidates_frame.columnconfigure(0, weight=1)
    
    def show_verification_request(self, artist: str, title: str, candidates: List[Dict]) -> Optional[Dict]:
        """Affiche la demande de vérification et retourne la sélection utilisateur"""
        
        self.track_info_label.config(text=f"{artist} - {title}")
        self.current_candidates = candidates
        self.selected_choice = None
        
        # Vider et remplir la treeview
        for item in self.candidates_tree.get_children():
            self.candidates_tree.delete(item)
        
        for i, candidate in enumerate(candidates):
            self.candidates_tree.insert("", tk.END, values=(
                candidate.get('title', 'N/A')[:50],
                candidate.get('channel_title', 'N/A')[:30],
                candidate.get('duration', 'N/A'),
                self._format_view_count(candidate.get('view_count', 0)),
                f"{candidate.get('relevance_score', 0):.2f}",
                self._get_channel_status(candidate)
            ), tags=(str(i),))
        
        # Attendre la sélection utilisateur
        self.root.wait_variable(self.selected_choice)
        return self.selected_choice
    
    def _on_candidate_select(self, event):
        """Gestion de la sélection d'un candidat"""
        selection = self.candidates_tree.selection()
        if not selection:
            return
        
        item = self.candidates_tree.item(selection[0])
        candidate_index = int(item['tags'][0])
        candidate = self.current_candidates[candidate_index]
        
        # Afficher l'aperçu
        self._show_preview(candidate)
    
    def _show_preview(self, candidate: Dict):
        """Affiche l'aperçu d'un candidat"""
        
        # Charger et afficher la thumbnail
        thumbnail_url = candidate.get('thumbnail_url')
        if thumbnail_url:
            try:
                response = requests.get(thumbnail_url, timeout=5)
                image = Image.open(BytesIO(response.content))
                image = image.resize((120, 90), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(image)
                self.thumbnail_label.config(image=photo)
                self.thumbnail_label.image = photo  # Garder une référence
            except:
                self.thumbnail_label.config(image="", text="Pas d'aperçu")
        
        # Afficher les détails
        details = []
        details.append(f"Titre: {candidate.get('title', 'N/A')}")
        details.append(f"Chaîne: {candidate.get('channel_title', 'N/A')} ({candidate.get('channel_subscriber_count', 'N/A')} abonnés)")
        details.append(f"URL: https://youtube.com/watch?v={candidate.get('video_id', '')}")
        details.append(f"Score de pertinence: {candidate.get('relevance_score', 0):.3f}")
        details.append(f"Publié: {candidate.get('published_at', 'N/A')}")
        details.append("")
        details.append("Description:")
        details.append(candidate.get('description', 'Pas de description')[:300] + "...")
        
        self.details_text.delete(1.0, tk.END)
        self.details_text.insert(1.0, "\n".join(details))
    
    def _select_candidate(self):
        """Sélectionner le candidat actuel"""
        selection = self.candidates_tree.selection()
        if selection:
            item = self.candidates_tree.item(selection[0])
            candidate_index = int(item['tags'][0])
            self.selected_choice = self.current_candidates[candidate_index]
    
    def _open_youtube(self):
        """Ouvrir la vidéo YouTube dans le navigateur"""
        selection = self.candidates_tree.selection()
        if selection:
            item = self.candidates_tree.item(selection[0])
            candidate_index = int(item['tags'][0])
            video_id = self.current_candidates[candidate_index].get('video_id')
            if video_id:
                webbrowser.open(f"https://youtube.com/watch?v={video_id}")
    
    def _format_view_count(self, count: int) -> str:
        """Formate le nombre de vues"""
        if count >= 1_000_000:
            return f"{count/1_000_000:.1f}M"
        elif count >= 1_000:
            return f"{count/1_000:.1f}K"
        else:
            return str(count)
    
    def _get_channel_status(self, candidate: Dict) -> str:
        """Détermine le statut de la chaîne"""
        # Logic pour déterminer le statut basé sur verification, subscribers, etc.
        if candidate.get('is_official_artist'):
            return "Officiel ♪"
        elif candidate.get('is_verified'):
            return "Vérifié ✓"
        elif candidate.get('channel_subscriber_count', 0) > 100000:
            return "Populaire"
        else:
            return "Standard"