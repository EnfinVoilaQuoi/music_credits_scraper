"""Fenêtre « Charger existant » : liste des artistes en base avec stats, chargement et suppression"""
import customtkinter as ctk
from tkinter import messagebox

from src.utils.logger import get_logger

logger = get_logger(__name__)


def load_existing_artist(app):
    """Charge un artiste existant depuis la base de données - VERSION AVEC GESTION"""
    # Créer une fenêtre de gestion des artistes
    dialog = ctk.CTkToplevel(app.root)
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
    artists_data = get_artists_with_stats(app)
    
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
        app.artist_entry.delete(0, "end")
        app.artist_entry.insert(0, name)
        dialog.destroy()
        app._search_artist()

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
        app.artist_entry.delete(0, "end")
        app.artist_entry.insert(0, selected_artist["name"])
        dialog.destroy()
        app._search_artist()
    
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
                success = app.data_manager.delete_artist(artist_name)
                
                if success:
                    messagebox.showinfo("Succès", f"Artiste '{artist_name}' supprimé avec succès")
                    dialog.destroy()
                    # Rafraîchir la liste en rouvrant le dialog
                    load_existing_artist(app)
                else:
                    messagebox.showerror("Erreur", "Impossible de supprimer l'artiste")
                    
            except Exception as e:
                error_msg = str(e)
                logger.error(f"Erreur lors de la suppression: {error_msg}")
                messagebox.showerror("Erreur", f"Erreur lors de la suppression:\n{error_msg}")
    
    def refresh_list():
        """Rafraîchit la liste des artistes"""
        dialog.destroy()
        load_existing_artist(app)
    
    def show_artist_details():
        """Affiche les détails de l'artiste sélectionné"""
        if not selected_artist["name"]:
            messagebox.showwarning("Attention", "Veuillez sélectionner un artiste")
            return
        
        # Récupérer les détails complets
        details = app.data_manager.get_artist_details(selected_artist["name"])
        
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

def get_artists_with_stats(app):
    """Récupère la liste des artistes avec leurs statistiques - VERSION AVEC DEBUG"""
    try:
        logger.info("🔍 Début récupération des artistes avec stats")
        
        with app.data_manager._get_connection() as conn:
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
