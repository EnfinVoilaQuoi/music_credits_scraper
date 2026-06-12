"""Interface graphique pour la mise à jour des certifications musicales"""
import customtkinter as ctk
import threading
import subprocess
import sys
from pathlib import Path
from datetime import datetime
from tkinter import messagebox
import pandas as pd

from src.utils.logger import get_logger

logger = get_logger(__name__)


class CertificationUpdateDialog(ctk.CTkToplevel):
    """Fenêtre de gestion des mises à jour de certifications"""
    
    def __init__(self, parent, cert_manager=None, default_artist=None):
        super().__init__(parent)

        self.cert_manager = cert_manager
        self.default_artist = default_artist
        self.missing_periods = {}  # Stocke les périodes manquantes par source
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
        
        self.status_text = ctk.CTkTextbox(status_frame, height=200)
        self.status_text.pack(fill="both", expand=True, padx=10, pady=10)
        
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
        ).pack(side="right", padx=(5, 10), pady=5)
        ctk.CTkButton(
            snep_frame,
            text="Vérification",
            command=self._check_snep,
            width=100,
            fg_color="gray40",
            hover_color="gray30"
        ).pack(side="right", padx=5, pady=5)
        
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
        ).pack(side="right", padx=(5, 10), pady=5)
        ctk.CTkButton(
            brma_frame,
            text="Vérification",
            command=self._check_brma,
            width=100,
            fg_color="gray40",
            hover_color="gray30"
        ).pack(side="right", padx=5, pady=5)
        
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
        ).pack(side="right", padx=(5, 10), pady=5)
        ctk.CTkButton(
            riaa_frame,
            text="Vérification",
            command=self._check_riaa,
            width=100,
            fg_color="gray40",
            hover_color="gray30"
        ).pack(side="right", padx=5, pady=5)
        
        # SNEP par artiste : récupère le CSV complet via ?interprete=
        # (seul export SNEP encore complet depuis le changement du site)
        artist_frame = ctk.CTkFrame(buttons_frame)
        artist_frame.pack(fill="x", pady=5)

        ctk.CTkLabel(artist_frame, text="🇫🇷 SNEP par artiste").pack(side="left", padx=10)
        ctk.CTkButton(
            artist_frame,
            text="Récupérer",
            command=self._update_snep_artist,
            width=120,
            fg_color="#1F6AA5"
        ).pack(side="right", padx=(5, 10), pady=5)
        self.artist_entry = ctk.CTkEntry(
            artist_frame, placeholder_text="Nom de l'artiste", width=180
        )
        self.artist_entry.pack(side="right", padx=5, pady=5)
        # Préremplir avec l'artiste courant si fourni
        if getattr(self, "default_artist", None):
            self.artist_entry.insert(0, self.default_artist)

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
                if 'SNEP' in self.missing_periods:
                    gaps = self.missing_periods['SNEP'].get('gaps', [])
                    if gaps:
                        status_text += f"   ⚠️ {len(gaps)} période(s) manquante(s)\n"
            else:
                status_text += f"🇫🇷 SNEP: ❌ Pas de données\n"

            # BRMA
            brma_path = data_path / 'brma' / 'certif_brma.csv'  # Nom correct du fichier BRMA
            if brma_path.exists():
                mod_time = datetime.fromtimestamp(brma_path.stat().st_mtime)
                status_text += f"🇧🇪 BRMA: ✅ Dernière MàJ: {mod_time:%d/%m/%Y %H:%M}\n"
                if 'BRMA' in self.missing_periods:
                    gaps = self.missing_periods['BRMA'].get('gaps', [])
                    if gaps:
                        status_text += f"   ⚠️ {len(gaps)} période(s) manquante(s)\n"
            else:
                status_text += f"🇧🇪 BRMA: ❌ Pas de données\n"

            # RIAA
            riaa_path = data_path / 'riaa' / 'certif_riaa.csv'  # Nom correct du fichier RIAA
            if riaa_path.exists():
                mod_time = datetime.fromtimestamp(riaa_path.stat().st_mtime)
                status_text += f"🇺🇸 RIAA: ✅ Dernière MàJ: {mod_time:%d/%m/%Y %H:%M}\n"
                if 'RIAA' in self.missing_periods:
                    gaps = self.missing_periods['RIAA'].get('gaps', [])
                    if gaps:
                        status_text += f"   ⚠️ {len(gaps)} période(s) manquante(s)\n"
            else:
                status_text += f"🇺🇸 RIAA: ❌ Pas de données\n"

            # Informations système
            status_text += f"\n📅 Vérification: {datetime.now():%d/%m/%Y %H:%M:%S}\n"
            status_text += f"💾 Dossier données: {data_path}\n"

            # Afficher les détails des périodes manquantes
            if self.missing_periods:
                status_text += "\n" + "=" * 40 + "\n"
                status_text += "📋 DÉTAILS DES PÉRIODES MANQUANTES\n"
                status_text += "=" * 40 + "\n"

                for source, data in self.missing_periods.items():
                    gaps = data.get('gaps', [])
                    if gaps:
                        status_text += f"\n🔍 {source}:\n"
                        status_text += f"   Total: {data.get('total', 0)} certifications\n"
                        if data.get('date_range'):
                            status_text += f"   Période: {data['date_range']}\n"
                        status_text += f"   Périodes manquantes ({len(gaps)}):\n"
                        # Afficher max 10 gaps
                        for gap in gaps[:10]:
                            status_text += f"   • {gap}\n"
                        if len(gaps) > 10:
                            status_text += f"   ... et {len(gaps) - 10} autre(s)\n"
            
            self.status_text.delete("0.0", "end")
            self.status_text.insert("0.0", status_text)
            
        except Exception as e:
            logger.error(f"Erreur lors de la mise à jour du statut: {e}")
            self.status_text.delete("0.0", "end")
            self.status_text.insert("0.0", f"❌ Erreur: {e}")
    
    def _update_snep(self):
        """Lance la mise à jour SNEP"""
        self._run_update_script("update_snep.py", "SNEP")

    def _update_snep_artist(self):
        """Récupère le CSV SNEP complet d'un artiste (filtre ?interprete=)"""
        artist = self.artist_entry.get().strip()
        if not artist:
            messagebox.showwarning("Artiste manquant",
                                   "Saisis un nom d'artiste.", parent=self)
            return
        self._run_update_script("update_snep.py", f"SNEP ({artist})",
                                extra_args=["--artist", artist])
    
    def _update_brma(self):
        """Lance la mise à jour BRMA"""
        self._run_update_script("update_brma.py", "BRMA")
    
    def _update_riaa(self):
        """Lance la mise à jour RIAA"""
        self._run_update_script("update_riaa.py", "RIAA")

    def _check_snep(self):
        """Vérifie les périodes manquantes pour SNEP"""
        self._check_missing_periods("SNEP", "snep", "certif-.csv")

    def _check_brma(self):
        """Vérifie les périodes manquantes pour BRMA"""
        self._check_missing_periods("BRMA", "brma", "certif_brma.csv")

    def _check_riaa(self):
        """Vérifie les périodes manquantes pour RIAA"""
        self._check_missing_periods("RIAA", "riaa", "certif_riaa.csv")

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
    
    def _run_update_script(self, script_name: str, source_name: str, extra_args=None):
        """Lance un script de mise à jour dans un thread"""
        def run_script():
            try:
                self._set_progress(f"Mise à jour {source_name} en cours...")

                script_path = Path(__file__).parent.parent / 'utils' / script_name

                if not script_path.exists():
                    raise FileNotFoundError(f"Script non trouvé: {script_path}")

                # Lancer le script avec encodage UTF-8
                result = subprocess.run(
                    [sys.executable, str(script_path)] + list(extra_args or []),
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    cwd=Path(__file__).parent.parent.parent
                )
                
                if result.returncode == 0:
                    self._set_progress(f"✅ Mise à jour {source_name} réussie")
                    self.after(500, self._update_status)
                    # Retour visible : dernières lignes de sortie du script
                    summary = "\n".join(
                        (result.stdout or "").strip().splitlines()[-8:]
                    ) or "Mise à jour terminée."
                    self.after(0, lambda: messagebox.showinfo(
                        f"Mise à jour {source_name}", summary, parent=self))
                else:
                    error_msg = result.stderr or result.stdout or "Erreur inconnue"
                    self._set_progress(f"❌ Erreur {source_name}: {error_msg[:50]}...")
                    logger.error(f"Erreur script {script_name}: {error_msg}")
                    self.after(0, lambda: messagebox.showerror(
                        f"Erreur {source_name}", error_msg[-600:], parent=self))
                
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

    def _check_missing_periods(self, source_name: str, folder: str, filename: str):
        """Vérifie les périodes manquantes dans un CSV de certification"""
        def check_async():
            try:
                from src.config import DATA_PATH
                csv_path = Path(DATA_PATH) / 'certifications' / folder / filename

                if not csv_path.exists():
                    self._set_progress(f"❌ {source_name}: Fichier introuvable")
                    return

                self._set_progress(f"🔍 Analyse de {source_name}...")

                # Analyser le CSV
                missing = self._analyze_csv_gaps(csv_path, source_name)

                # Stocker les résultats
                self.missing_periods[source_name] = missing

                # Mettre à jour l'affichage
                self._update_status()

                if missing['gaps']:
                    gap_count = len(missing['gaps'])
                    self._set_progress(f"⚠️ {source_name}: {gap_count} période(s) manquante(s) détectée(s)")
                else:
                    self._set_progress(f"✅ {source_name}: Aucune période manquante")

            except Exception as e:
                logger.error(f"Erreur vérification {source_name}: {e}")
                self._set_progress(f"❌ Erreur vérification {source_name}: {e}")

        threading.Thread(target=check_async, daemon=True).start()

    def _analyze_csv_gaps(self, csv_path: Path, source: str) -> dict:
        """Analyse un CSV pour détecter les périodes manquantes"""
        try:
            # Charger le CSV avec gestion d'encodage
            try:
                df = pd.read_csv(csv_path, encoding='utf-8', sep=';')
            except:
                df = pd.read_csv(csv_path, encoding='latin1', sep=';')

            if df.empty:
                return {'total': 0, 'gaps': [], 'date_range': None}

            # Identifier la colonne de date selon la source
            date_columns = {
                'SNEP': 'Date de constat',
                'BRMA': 'date',
                'RIAA': 'certification_date'
            }

            date_col = date_columns.get(source)
            if not date_col or date_col not in df.columns:
                # Essayer de trouver une colonne de date
                possible_cols = [col for col in df.columns if 'date' in col.lower()]
                if possible_cols:
                    date_col = possible_cols[0]
                else:
                    return {'total': len(df), 'gaps': ['Colonne de date non trouvée'], 'date_range': None}

            # Convertir les dates
            df[date_col] = pd.to_datetime(df[date_col], errors='coerce', dayfirst=True)
            df = df.dropna(subset=[date_col])

            if df.empty:
                return {'total': 0, 'gaps': ['Aucune date valide'], 'date_range': None}

            # Analyser par année/mois
            df['year_month'] = df[date_col].dt.to_period('M')
            monthly_counts = df.groupby('year_month').size()

            # Détecter les gaps (mois sans certifications)
            if len(monthly_counts) == 0:
                return {'total': len(df), 'gaps': [], 'date_range': None}

            min_period = monthly_counts.index.min()
            max_period = monthly_counts.index.max()

            # Générer tous les mois entre min et max
            all_months = pd.period_range(start=min_period, end=max_period, freq='M')

            # Trouver les mois manquants (avec tolérance pour les mois récents)
            gaps = []
            current_month = pd.Period(datetime.now(), freq='M')

            for month in all_months:
                # Ne pas signaler comme manquant si c'est le mois en cours ou suivant
                if month >= current_month:
                    continue

                if month not in monthly_counts.index:
                    # Mois sans aucune certification
                    gaps.append(f"{month.strftime('%Y-%m')} (0 certifications)")
                elif monthly_counts[month] < 5:  # Seuil minimal de certifications par mois
                    gaps.append(f"{month.strftime('%Y-%m')} ({monthly_counts[month]} certifications - possiblement incomplet)")

            return {
                'total': len(df),
                'gaps': gaps,
                'date_range': f"{min_period.strftime('%Y-%m')} à {max_period.strftime('%Y-%m')}",
                'monthly_avg': monthly_counts.mean()
            }

        except Exception as e:
            logger.error(f"Erreur analyse CSV {source}: {e}")
            return {'total': 0, 'gaps': [f'Erreur: {str(e)}'], 'date_range': None}