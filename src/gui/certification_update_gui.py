"""Interface graphique pour la mise à jour des certifications musicales"""
import customtkinter as ctk
from tkinter import messagebox
import threading
import subprocess
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)


class CertificationUpdateDialog(ctk.CTkToplevel):
    """Fenêtre de gestion des mises à jour de certifications"""
    
    def __init__(self, parent, cert_manager=None):
        super().__init__(parent)
        
        self.cert_manager = cert_manager
        self.title("Mise à jour des certifications")
        self.geometry("600x700")
        
        # Centrer la fenêtre
        self.update_idletasks()
        x = (self.winfo_screenwidth() // 2) - (300)
        y = (self.winfo_screenheight() // 2) - (350)
        self.geometry(f"600x700+{x}+{y}")
        
        self.lift()
        self.focus_force()
        
        self._create_widgets()
        self._update_status()
    
    def _create_widgets(self):
        """Crée l'interface graphique"""
        # Titre principal
        title_label = ctk.CTkLabel(
            self, 
            text="📊 Gestionnaire de Certifications Musicales",
            font=("Arial", 20, "bold")
        )
        title_label.pack(pady=20)
        
        # Frame principal
        main_frame = ctk.CTkFrame(self)
        main_frame.pack(fill="both", expand=True, padx=20, pady=10)
        
        # Section état
        status_frame = ctk.CTkFrame(main_frame)
        status_frame.pack(fill="x", padx=10, pady=10)
        
        ctk.CTkLabel(
            status_frame, 
            text="État des certifications",
            font=("Arial", 16, "bold")
        ).pack(pady=10)
        
        self.status_text = ctk.CTkTextbox(status_frame, height=120)
        self.status_text.pack(fill="x", padx=10, pady=10)
        
        # Section sources
        sources_frame = ctk.CTkFrame(main_frame)
        sources_frame.pack(fill="x", padx=10, pady=10)
        
        ctk.CTkLabel(
            sources_frame,
            text="Sources de certifications",
            font=("Arial", 16, "bold")
        ).pack(pady=10)
        
        # Boutons pour chaque source
        buttons_frame = ctk.CTkFrame(sources_frame)
        buttons_frame.pack(fill="x", padx=10, pady=10)
        
        # SNEP (France)
        snep_frame = ctk.CTkFrame(buttons_frame)
        snep_frame.pack(fill="x", pady=5)
        
        ctk.CTkLabel(snep_frame, text="🇫🇷 SNEP (France)").pack(side="left", padx=10)
        ctk.CTkButton(
            snep_frame,
            text="Mettre à jour",
            command=self._update_snep,
            width=120,
            fg_color="blue"
        ).pack(side="right", padx=10, pady=5)
        
        # BRMA (Belgique)
        brma_frame = ctk.CTkFrame(buttons_frame)
        brma_frame.pack(fill="x", pady=5)
        
        ctk.CTkLabel(brma_frame, text="🇧🇪 BRMA (Belgique)").pack(side="left", padx=10)
        ctk.CTkButton(
            brma_frame,
            text="Mettre à jour",
            command=self._update_brma,
            width=120,
            fg_color="orange"
        ).pack(side="right", padx=10, pady=5)
        
        # RIAA (USA) - Maintenant disponible
        riaa_frame = ctk.CTkFrame(buttons_frame)
        riaa_frame.pack(fill="x", pady=5)
        
        ctk.CTkLabel(riaa_frame, text="🇺🇸 RIAA (USA)").pack(side="left", padx=10)
        ctk.CTkButton(
            riaa_frame,
            text="Mettre à jour",
            command=self._update_riaa,
            width=120,
            fg_color="red"
        ).pack(side="right", padx=10, pady=5)
        
        # Section actions globales
        actions_frame = ctk.CTkFrame(main_frame)
        actions_frame.pack(fill="x", padx=10, pady=10)
        
        ctk.CTkLabel(
            actions_frame,
            text="Actions globales",
            font=("Arial", 16, "bold")
        ).pack(pady=10)
        
        global_buttons_frame = ctk.CTkFrame(actions_frame)
        global_buttons_frame.pack(fill="x", padx=10, pady=10)
        
        # Tout mettre à jour
        ctk.CTkButton(
            global_buttons_frame,
            text="🔄 Tout mettre à jour",
            command=self._update_all,
            fg_color="green",
            hover_color="darkgreen",
            width=150
        ).pack(side="left", padx=5)
        
        # Actualiser l'état
        ctk.CTkButton(
            global_buttons_frame,
            text="🔍 Actualiser l'état",
            command=self._update_status,
            width=150
        ).pack(side="left", padx=5)
        
        # Fermer
        ctk.CTkButton(
            global_buttons_frame,
            text="Fermer",
            command=self.destroy,
            width=100
        ).pack(side="right", padx=5)
        
        # Zone de progression
        self.progress_label = ctk.CTkLabel(main_frame, text="")
        self.progress_label.pack(pady=5)
    
    def _update_status(self):
        """Met à jour l'affichage de l'état"""
        try:
            status_text = "📊 ÉTAT DES CERTIFICATIONS\n"
            status_text += "=" * 40 + "\n\n"
            
            # Vérifier les fichiers de données
            from src.config import DATA_PATH
            data_path = Path(DATA_PATH) / 'certifications'
            
            # SNEP
            snep_path = data_path / 'snep' / 'certif-.csv'
            if snep_path.exists():
                mod_time = datetime.fromtimestamp(snep_path.stat().st_mtime)
                status_text += f"🇫🇷 SNEP: ✅ Dernière MàJ: {mod_time:%d/%m/%Y %H:%M}\n"
            else:
                status_text += f"🇫🇷 SNEP: ❌ Pas de données\n"
            
            # BRMA
            brma_path = data_path / 'brma' / 'brma_certifications.csv'
            if brma_path.exists():
                mod_time = datetime.fromtimestamp(brma_path.stat().st_mtime)
                status_text += f"🇧🇪 BRMA: ✅ Dernière MàJ: {mod_time:%d/%m/%Y %H:%M}\n"
            else:
                status_text += f"🇧🇪 BRMA: ❌ Pas de données\n"
            
            # RIAA
            riaa_path = data_path / 'riaa' / 'riaa.csv'
            if riaa_path.exists():
                mod_time = datetime.fromtimestamp(riaa_path.stat().st_mtime)
                status_text += f"🇺🇸 RIAA: ✅ Dernière MàJ: {mod_time:%d/%m/%Y %H:%M}\n"
            else:
                status_text += f"🇺🇸 RIAA: ❌ Pas de données\n"
            
            # Informations système
            status_text += f"📅 Vérification: {datetime.now():%d/%m/%Y %H:%M:%S}\n"
            status_text += f"💾 Dossier données: {data_path}\n"
            
            self.status_text.delete("0.0", "end")
            self.status_text.insert("0.0", status_text)
            
        except Exception as e:
            logger.error(f"Erreur lors de la mise à jour du statut: {e}")
            self.status_text.delete("0.0", "end")
            self.status_text.insert("0.0", f"❌ Erreur: {e}")
    
    def _update_snep(self):
        """Lance la mise à jour SNEP"""
        self._run_update_script("update_snep.py", "SNEP")
    
    def _update_brma(self):
        """Lance la mise à jour BRMA"""
        self._run_update_script("update_brma.py", "BRMA")
    
    def _update_riaa(self):
        """Lance la mise à jour RIAA"""
        self._run_update_script("update_riaa.py", "RIAA")
    
    def _update_all(self):
        """Lance toutes les mises à jour"""
        def update_all():
            try:
                self._set_progress("Mise à jour de toutes les sources...")
                
                # SNEP
                self._set_progress("Mise à jour SNEP en cours...")
                self._run_script_sync("update_snep.py")
                
                # BRMA  
                self._set_progress("Mise à jour BRMA en cours...")
                self._run_script_sync("update_brma.py")
                
                # RIAA
                self._set_progress("Mise à jour RIAA en cours...")
                self._run_script_sync("update_riaa.py")
                
                self._set_progress("Toutes les mises à jour terminées !")
                self.after(2000, lambda: self._set_progress(""))
                self.after(500, self._update_status)
                
            except Exception as e:
                logger.error(f"Erreur mise à jour globale: {e}")
                self._set_progress(f"❌ Erreur: {e}")
        
        threading.Thread(target=update_all, daemon=True).start()
    
    def _run_update_script(self, script_name: str, source_name: str):
        """Lance un script de mise à jour dans un thread"""
        def run_script():
            try:
                self._set_progress(f"Mise à jour {source_name} en cours...")

                script_path = Path(__file__).parent.parent / 'utils' / script_name
                
                if not script_path.exists():
                    raise FileNotFoundError(f"Script non trouvé: {script_path}")
                
                # Lancer le script avec encodage UTF-8
                result = subprocess.run(
                    [sys.executable, str(script_path)],
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    cwd=Path(__file__).parent.parent.parent
                )
                
                if result.returncode == 0:
                    self._set_progress(f"✅ Mise à jour {source_name} réussie")
                    self.after(500, self._update_status)
                else:
                    error_msg = result.stderr or result.stdout or "Erreur inconnue"
                    self._set_progress(f"❌ Erreur {source_name}: {error_msg[:50]}...")
                    logger.error(f"Erreur script {script_name}: {error_msg}")
                
                # Effacer le message après 3 secondes
                self.after(3000, lambda: self._set_progress(""))
                
            except Exception as e:
                logger.error(f"Erreur lors de l'exécution de {script_name}: {e}")
                self._set_progress(f"❌ Erreur: {e}")
                self.after(3000, lambda: self._set_progress(""))
        
        threading.Thread(target=run_script, daemon=True).start()
    
    def _run_script_sync(self, script_name: str):
        """Lance un script de façon synchrone"""
        script_path = Path(__file__).parent.parent / 'utils' / script_name
        
        if not script_path.exists():
            raise FileNotFoundError(f"Script non trouvé: {script_path}")
        
        result = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            cwd=Path(__file__).parent.parent.parent
        )
        
        if result.returncode != 0:
            error_msg = result.stderr or result.stdout or "Erreur inconnue"
            raise Exception(f"Erreur script {script_name}: {error_msg}")
    
    def _set_progress(self, message: str):
        """Met à jour le message de progression"""
        def update():
            self.progress_label.configure(text=message)
        
        self.after(0, update)