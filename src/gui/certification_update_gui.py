"""Interface graphique pour la mise à jour des certifications musicales"""

import subprocess
import sys
from datetime import datetime
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk
import pandas as pd

from src.gui.workers.lifecycle import start_worker
from src.utils.logger import get_logger

logger = get_logger(__name__)


class CertificationUpdateDialog(ctk.CTkToplevel):
    """Fenêtre de gestion des mises à jour de certifications"""

    def __init__(
        self,
        parent,
        default_artist=None,
        artist_tracks=None,
        artist_albums=None,
        app=None,
    ):
        super().__init__(parent)

        # Fenêtre principale : donne accès à current_artist + data_manager pour
        # l'action « Appliquer à l'artiste courant » (E7h). None = action masquée.
        self.app = app
        self.default_artist = default_artist
        self.artist_tracks = artist_tracks or []  # Morceaux pour l'audit
        self.artist_albums = artist_albums or []  # Albums pour l'audit
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
            self, text="📊 Gestionnaire de Certifications Musicales", font=("Arial", 20, "bold")
        )
        title_label.pack(pady=20)

        # Frame principal
        main_frame = ctk.CTkFrame(self)
        main_frame.pack(fill="both", expand=True, padx=20, pady=10)

        # Section état
        status_frame = ctk.CTkFrame(main_frame)
        status_frame.pack(fill="x", padx=10, pady=10)

        ctk.CTkLabel(status_frame, text="État des certifications", font=("Arial", 16, "bold")).pack(
            pady=10
        )

        self.status_text = ctk.CTkTextbox(status_frame, height=200)
        self.status_text.pack(fill="both", expand=True, padx=10, pady=10)

        # Section sources
        sources_frame = ctk.CTkFrame(main_frame)
        sources_frame.pack(fill="x", padx=10, pady=10)

        ctk.CTkLabel(
            sources_frame, text="Sources de certifications", font=("Arial", 16, "bold")
        ).pack(pady=10)

        # Boutons pour chaque source
        buttons_frame = ctk.CTkFrame(sources_frame)
        buttons_frame.pack(fill="x", padx=10, pady=10)

        # SNEP (France)
        snep_frame = ctk.CTkFrame(buttons_frame)
        snep_frame.pack(fill="x", pady=5)

        ctk.CTkLabel(snep_frame, text="🇫🇷 SNEP (France)").pack(side="left", padx=10)
        ctk.CTkButton(
            snep_frame, text="Mettre à jour", command=self._update_snep, width=120, fg_color="blue"
        ).pack(side="right", padx=(5, 10), pady=5)
        ctk.CTkButton(
            snep_frame,
            text="🔎 Valider CSV",
            command=self._check_snep,
            width=110,
            fg_color="gray40",
            hover_color="gray30",
        ).pack(side="right", padx=5, pady=5)
        ctk.CTkButton(
            snep_frame,
            text="🧹 Nettoyer",
            command=self._clean_snep,
            width=100,
            fg_color="gray40",
            hover_color="gray30",
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
            fg_color="orange",
        ).pack(side="right", padx=(5, 10), pady=5)
        ctk.CTkButton(
            brma_frame,
            text="🔎 Valider CSV",
            command=self._check_brma,
            width=110,
            fg_color="gray40",
            hover_color="gray30",
        ).pack(side="right", padx=5, pady=5)
        ctk.CTkButton(
            brma_frame,
            text="🧹 Nettoyer",
            command=self._clean_brma,
            width=100,
            fg_color="gray40",
            hover_color="gray30",
        ).pack(side="right", padx=5, pady=5)

        # RIAA (USA) - Maintenant disponible
        riaa_frame = ctk.CTkFrame(buttons_frame)
        riaa_frame.pack(fill="x", pady=5)

        ctk.CTkLabel(riaa_frame, text="🇺🇸 RIAA (USA)").pack(side="left", padx=10)
        ctk.CTkButton(
            riaa_frame, text="Mettre à jour", command=self._update_riaa, width=120, fg_color="red"
        ).pack(side="right", padx=(5, 10), pady=5)
        ctk.CTkButton(
            riaa_frame,
            text="🔎 Valider CSV",
            command=self._check_riaa,
            width=110,
            fg_color="gray40",
            hover_color="gray30",
        ).pack(side="right", padx=5, pady=5)
        ctk.CTkButton(
            riaa_frame,
            text="🧹 Nettoyer",
            command=self._clean_riaa,
            width=100,
            fg_color="gray40",
            hover_color="gray30",
        ).pack(side="right", padx=5, pady=5)

        # SNEP par artiste : récupère le CSV complet via ?interprete=
        # (seul export SNEP encore complet depuis le changement du site)
        artist_frame = ctk.CTkFrame(buttons_frame)
        artist_frame.pack(fill="x", pady=5)

        ctk.CTkLabel(artist_frame, text="🌍 Certifs par artiste").pack(side="left", padx=10)
        ctk.CTkButton(
            artist_frame,
            text="Récupérer",
            command=self._fetch_artist_all_sources,
            width=110,
            fg_color="#1F6AA5",
        ).pack(side="right", padx=(5, 10), pady=5)
        ctk.CTkButton(
            artist_frame,
            text="🔎 Audit",
            command=self._audit_snep_artist,
            width=90,
            fg_color="gray40",
            hover_color="gray30",
        ).pack(side="right", padx=5, pady=5)
        self.artist_entry = ctk.CTkEntry(
            artist_frame, placeholder_text="Nom de l'artiste", width=170
        )
        self.artist_entry.pack(side="right", padx=5, pady=5)
        # Préremplir avec l'artiste courant si fourni
        if getattr(self, "default_artist", None):
            self.artist_entry.insert(0, self.default_artist)

        # Section actions globales
        actions_frame = ctk.CTkFrame(main_frame)
        actions_frame.pack(fill="x", padx=10, pady=10)

        ctk.CTkLabel(actions_frame, text="Actions globales", font=("Arial", 16, "bold")).pack(
            pady=10
        )

        global_buttons_frame = ctk.CTkFrame(actions_frame)
        global_buttons_frame.pack(fill="x", padx=10, pady=10)

        # Tout mettre à jour
        ctk.CTkButton(
            global_buttons_frame,
            text="🔄 Tout mettre à jour",
            command=self._update_all,
            fg_color="green",
            hover_color="darkgreen",
            width=150,
        ).pack(side="left", padx=5)

        # Appliquer à l'artiste courant (E7h) : rematch des CSV clean + save.
        # Masqué si la fenêtre est ouverte hors contexte artiste.
        if getattr(self, "app", None) is not None:
            ctk.CTkButton(
                global_buttons_frame,
                text="🎯 Appliquer à l'artiste courant",
                command=self._apply_to_current_artist,
                fg_color="#1F6AA5",
                width=200,
            ).pack(side="left", padx=5)

        # Actualiser l'état
        ctk.CTkButton(
            global_buttons_frame,
            text="🔍 Actualiser l'état",
            command=self._update_status,
            width=150,
        ).pack(side="left", padx=5)

        # Fermer
        ctk.CTkButton(global_buttons_frame, text="Fermer", command=self.destroy, width=100).pack(
            side="right", padx=5
        )

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

            data_path = Path(DATA_PATH) / "certifications"

            def _fmt(iso):
                if not iso:
                    return None
                try:
                    return datetime.fromisoformat(iso).strftime("%d/%m/%Y %H:%M")
                except (ValueError, TypeError):
                    return str(iso)[:16]

            # Fraîcheur UNIFORME par source (E7f) : chaque CertificationSource lit
            # son propre metadata.json (MàJ NON-artiste la plus récente, pas le
            # mtime que bumpe une recherche artiste). Plus de code ad-hoc par pays.
            from src.enrichment.cert_source import all_certification_sources

            flags = {"SNEP": "🇫🇷", "BRMA": "🇧🇪", "RIAA": "🇺🇸"}
            for source in all_certification_sources():
                flag = flags.get(source.name, "🏳️")
                fresh = source.freshness()
                if not fresh["available"]:
                    status_text += f"{flag} {source.name}: ❌ Pas de données\n"
                    continue
                if fresh["last_global"]:
                    status_text += (
                        f"{flag} {source.name}: ✅ Dernière MàJ globale: "
                        f"{_fmt(fresh['last_global'])}\n"
                    )
                else:
                    # Aucune MàJ globale tracée : repli sur le mtime, signalé.
                    mod_time = datetime.fromtimestamp(source.clean_path.stat().st_mtime)
                    status_text += (
                        f"{flag} {source.name}: ⚠️ MàJ globale jamais tracée "
                        f"(fichier modifié {mod_time:%d/%m/%Y %H:%M})\n"
                    )
                if fresh["last_artist"]:
                    status_text += f"   ↳ Dernière récup. artiste: {_fmt(fresh['last_artist'])}\n"
                if source.name in self.missing_periods:
                    gaps = self.missing_periods[source.name].get("gaps", [])
                    if gaps:
                        status_text += f"   ⚠️ {len(gaps)} période(s) manquante(s)\n"

            # Informations système
            status_text += f"\n📅 Vérification: {datetime.now():%d/%m/%Y %H:%M:%S}\n"
            status_text += f"💾 Dossier données: {data_path}\n"

            # Afficher les détails des périodes manquantes
            if self.missing_periods:
                status_text += "\n" + "=" * 40 + "\n"
                status_text += "📋 DÉTAILS DES PÉRIODES MANQUANTES\n"
                status_text += "=" * 40 + "\n"

                for source, data in self.missing_periods.items():
                    gaps = data.get("gaps", [])
                    if gaps:
                        status_text += f"\n🔍 {source}:\n"
                        status_text += f"   Total: {data.get('total', 0)} certifications\n"
                        if data.get("date_range"):
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

    def _apply_to_current_artist(self):
        """Rematche les certifs de l'artiste courant depuis les CSV clean puis
        persiste (E7h). Offline (matcher en mémoire) ; reset_cert_matcher pour
        repartir des CSV fraîchement mis à jour dans cette session."""
        app = getattr(self, "app", None)
        artist = getattr(app, "current_artist", None) if app else None
        if not artist or not getattr(artist, "tracks", None):
            messagebox.showinfo("Appliquer", "Aucun artiste courant chargé.", parent=self)
            return

        def run():
            try:
                from src.utils.cert_matcher import get_cert_matcher, reset_cert_matcher
                from src.utils.certification_enricher import apply_certifications

                reset_cert_matcher()  # repartir des CSV clean (MàJ de la session)
                n = apply_certifications(artist, artist.tracks, get_cert_matcher())
                for track in artist.tracks:
                    app.data_manager.save_track(track)
            except Exception as e:
                logger.error(f"Application certifs échouée: {e}")
                # `e` est effacé à la sortie du except → capture par défaut.
                self.after(
                    0,
                    lambda err=e: messagebox.showerror("Appliquer", f"Échec : {err}", parent=self),
                )
                return

            def done():
                messagebox.showinfo(
                    "Appliquer",
                    f"{n} morceau(x) certifié(s) pour {artist.name}.",
                    parent=self,
                )
                if hasattr(app, "_populate_tracks_table"):
                    app._populate_tracks_table()
                self._update_status()

            self.after(0, done)

        start_worker(run)

    def _update_snep(self):
        """Lance la mise à jour SNEP"""
        self._run_update_script("update_snep.py", "SNEP")

    def _update_snep_artist(self):
        """Récupère le CSV SNEP complet d'un artiste (filtre ?interprete=)"""
        artist = self.artist_entry.get().strip()
        if not artist:
            messagebox.showwarning("Artiste manquant", "Saisis un nom d'artiste.", parent=self)
            return
        self._run_update_script(
            "update_snep.py", f"SNEP ({artist})", extra_args=["--artist", artist]
        )

    def _fetch_artist_all_sources(self):
        """Récup UNIFIÉE des certifs d'un artiste : SNEP (?interprete=) + RIAA
        (?ar=, via CDP). BRMA n'a pas de vraie recherche artiste (substring) →
        on s'appuie sur les données BRMA déjà chargées. Rafraîchit le matcher."""
        artist = self.artist_entry.get().strip()
        if not artist:
            messagebox.showwarning("Artiste manquant", "Saisis un nom d'artiste.", parent=self)
            return

        def run():
            import os
            import subprocess

            root = Path(__file__).parent.parent.parent
            py = sys.executable
            outputs = []

            # 1) SNEP par artiste
            self._set_progress(f"🇫🇷 SNEP : {artist}…")
            try:
                r = subprocess.run(
                    [py, str(root / "src" / "utils" / "update_snep.py"), "--artist", artist],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                outputs.append(
                    "SNEP : " + ((r.stdout or "").strip().splitlines()[-1:] or ["ok"])[0]
                )
            except Exception as e:
                logger.error(f"SNEP artiste : {e}")
                outputs.append(f"SNEP : erreur ({e})")

            # 2) RIAA par artiste (route CDP anti-Cloudflare)
            self._set_progress("🇺🇸 RIAA : préparation Chrome…")
            env = None
            try:
                from src.scrapers.cdp_chrome import ensure_cdp_chrome

                cdp = ensure_cdp_chrome()
                if cdp:
                    env = {**os.environ, "GENIUS_CDP_URL": cdp}
            except Exception as e:
                logger.error(f"CDP RIAA : {e}")
            self._set_progress(f"🇺🇸 RIAA : {artist}…")
            try:
                r = subprocess.run(
                    [py, str(root / "src" / "utils" / "update_riaa.py"), "--artist", artist],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                )
                outputs.append(
                    "RIAA : " + ((r.stdout or "").strip().splitlines()[-1:] or ["ok"])[0]
                )
            except Exception as e:
                logger.error(f"RIAA artiste : {e}")
                outputs.append(f"RIAA : erreur ({e})")

            # 3) rafraîchir le matcher (nouvelles certifs sur disque)
            try:
                from src.utils.cert_matcher import reset_cert_matcher

                reset_cert_matcher()
            except Exception:
                pass

            self._set_progress(f"✅ Certifs récupérées pour {artist} (SNEP + RIAA)")
            self.after(
                0,
                lambda: messagebox.showinfo(
                    f"Certifs par artiste — {artist}",
                    "\n".join(outputs) + "\n\nRecharge l'artiste pour voir les certifs raccordées.",
                    parent=self,
                ),
            )
            self.after(500, self._update_status)

        start_worker(run)

    def _update_brma(self):
        """Lance la mise à jour BRMA. Ultratop est derrière un Cloudflare strict :
        on prépare d'abord un Chrome 'debug' (route CDP) puis on passe son URL au
        sous-processus, sinon le scraper boucle sur le challenge."""

        def prepare_and_run():
            try:
                from src.scrapers.cdp_chrome import ensure_cdp_chrome

                self._set_progress("🌐 Préparation de Chrome (Cloudflare ultratop)...")
                cdp_url = ensure_cdp_chrome()
            except Exception as e:
                logger.error(f"Préparation CDP BRMA échouée : {e}")
                cdp_url = None

            env_extra = {"GENIUS_CDP_URL": cdp_url} if cdp_url else None
            if not cdp_url:
                self.after(
                    0,
                    lambda: messagebox.showwarning(
                        "Chrome requis (Cloudflare)",
                        "Impossible de préparer Chrome en mode debug pour contourner le "
                        "Cloudflare d'ultratop.\nVérifie que Google Chrome est installé "
                        "(ou définis la variable CHROME_PATH).\n\nLa mise à jour va tenter "
                        "quand même, mais risque de boucler sur le challenge.",
                        parent=self,
                    ),
                )

            self._run_update_script(
                "update_brma.py",
                "BRMA",
                extra_args=["--mode", "once", "--years-back", "1"],
                env_extra=env_extra,
            )

        start_worker(prepare_and_run)

    def _update_riaa(self):
        """RIAA via patchright. Comme BRMA, on prépare un Chrome debug (route CDP)
        pour contourner Cloudflare, puis MàJ auto (mois manquants) non-interactive."""

        def prepare_and_run():
            try:
                from src.scrapers.cdp_chrome import ensure_cdp_chrome

                self._set_progress("🌐 Préparation de Chrome (RIAA / Cloudflare)...")
                cdp_url = ensure_cdp_chrome()
            except Exception as e:
                logger.error(f"Préparation CDP RIAA échouée : {e}")
                cdp_url = None
            env_extra = {"GENIUS_CDP_URL": cdp_url} if cdp_url else None
            if not cdp_url:
                self.after(
                    0,
                    lambda: messagebox.showwarning(
                        "Chrome requis (Cloudflare)",
                        "Impossible de préparer Chrome debug pour RIAA.\nVérifie que Google "
                        "Chrome est installé (ou CHROME_PATH).\nLa MàJ va tenter quand même.",
                        parent=self,
                    ),
                )
            self._run_update_script(
                "update_riaa.py", "RIAA", extra_args=["--auto"], env_extra=env_extra
            )

        start_worker(prepare_and_run)

    def _check_snep(self):
        """Lance le validateur complet du CSV maître SNEP et affiche le rapport."""

        def run():
            try:
                from src.config import DATA_PATH
                from src.utils.snep_validator import format_report, validate_snep_csv

                csv_path = Path(DATA_PATH) / "certifications" / "snep" / "certif-.csv"

                if not csv_path.exists():
                    self._set_progress("❌ SNEP : fichier introuvable")
                    return

                self._set_progress("🔎 Validation du CSV SNEP...")
                report = validate_snep_csv(csv_path)
                text = format_report(report)

                # Synthèse courte dans le bandeau de progression
                n_gaps = len(report.get("month_gaps", []))
                verdict = "RAS" if report.get("ok") else "anomalies"
                self._set_progress(
                    f"{'✅' if report.get('ok') else '⚠️'} SNEP : {verdict}"
                    f" — {n_gaps} mois sans certif (années actives)"
                )
                # Rapport détaillé dans une fenêtre dédiée
                self.after(0, lambda: self._show_report_window("Validation CSV SNEP", text))
            except Exception as e:
                logger.error(f"Erreur validation SNEP : {e}")
                self._set_progress(f"❌ Erreur validation SNEP : {e}")

        start_worker(run)

    def _audit_snep_artist(self):
        """Audite les certifs SNEP de l'artiste face à sa discographie :
        liste les certifs orphelines (rattachées à aucun morceau)."""
        artist = self.artist_entry.get().strip()
        if not artist:
            messagebox.showwarning("Artiste manquant", "Saisis un nom d'artiste.", parent=self)
            return
        if not self.artist_tracks:
            messagebox.showinfo(
                "Discographie absente",
                "Aucune discographie chargée pour l'audit.\n"
                "Charge d'abord l'artiste dans la fenêtre principale, puis "
                "rouvre les certifications.",
                parent=self,
            )
            return

        def run():
            try:
                from src.utils.cert_matcher import get_cert_matcher

                self._set_progress(f"🔎 Audit des certifs de {artist}...")
                res = get_cert_matcher().audit_artist_certifications(
                    artist, self.artist_tracks, self.artist_albums
                )

                probable = [o for o in res["orphans"] if o["ratio"] >= 0.6]
                absent = [o for o in res["orphans"] if o["ratio"] < 0.6]

                L = []
                L.append("=" * 50)
                L.append(f"🔎 AUDIT CERTIFS — {res['artist']}")
                L.append("=" * 50)
                L.append(f"Certifs SNEP de l'artiste : {res['total']}")
                L.append(f"Rattachées à un morceau   : {res.get('matched_tracks', 0)}")
                L.append(f"Rattachées à un album     : {res.get('matched_albums', 0)}")
                L.append(f"Orphelines                : {len(res['orphans'])}")

                def fmt(o):
                    d = (o["certification_date"] or "")[:10]
                    kind = o.get("kind", "morceau")
                    return (
                        f"  • {o['title']!r} ({o['certification']}, {o['category']}, {d})\n"
                        f"      ≈ {kind} proche : {o['closest']!r}  (sim. {o['ratio']})"
                    )

                if probable:
                    L.append("")
                    L.append(
                        f"── Probablement tronquées/corrompues ({len(probable)}) "
                        f"— récupérables ──"
                    )
                    for o in probable:
                        L.append(fmt(o))
                if absent:
                    L.append("")
                    L.append(
                        f"── Absentes de la discographie ({len(absent)}) "
                        f"— morceau non scrapé ? ──"
                    )
                    for o in absent:
                        L.append(fmt(o))
                if not res["orphans"]:
                    L.append("")
                    L.append("✅ Toutes les certifs de l'artiste sont rattachées à un morceau.")
                L.append("")
                L.append("=" * 50)

                text = "\n".join(L)
                self.after(0, lambda: self._show_report_window(f"Audit certifs — {artist}", text))
                self._set_progress(
                    f"🔎 {artist} : {len(res['orphans'])} certif(s) orpheline(s) "
                    f"sur {res['total']}"
                )
            except Exception as e:
                logger.error(f"Erreur audit {artist} : {e}")
                self._set_progress(f"❌ Erreur audit : {e}")

        start_worker(run)

    def _clean_snep(self):
        """Aperçu (dry-run) du nettoyage du CSV maître SNEP, puis application
        sur confirmation (un backup est créé avant écriture)."""

        def run():
            try:
                from src.config import DATA_PATH
                from src.utils.snep_cleaner import clean_snep_csv, format_report

                csv_path = Path(DATA_PATH) / "certifications" / "snep" / "certif-.csv"
                if not csv_path.exists():
                    self._set_progress("❌ SNEP : fichier introuvable")
                    return

                self._set_progress("🧹 Analyse du nettoyage (aperçu)...")
                dry = clean_snep_csv(csv_path, apply=False)
                n_changes = (
                    dry["levels_recased"]
                    + dry["categories_recased"]
                    + dry["whitespace_fixed"]
                    + dry["duplicates_removed"]
                    + dry["empty_removed"]
                )

                def ask_and_apply():
                    self._show_report_window("Nettoyage CSV SNEP — aperçu", format_report(dry))
                    if n_changes == 0:
                        self._set_progress("✅ SNEP : CSV déjà propre")
                        return
                    msg = (
                        f"{dry['duplicates_removed']} doublon(s), "
                        f"{dry['empty_removed']} ligne(s) vide(s), "
                        f"{dry['levels_recased'] + dry['categories_recased']} casse(s), "
                        f"{dry['whitespace_fixed']} champ(s) espaces/tab.\n\n"
                        f"Un backup horodaté sera créé. Appliquer le nettoyage ?"
                    )
                    if messagebox.askyesno("Appliquer le nettoyage", msg, parent=self):

                        def apply():
                            self._set_progress("🧹 Nettoyage en cours...")
                            res = clean_snep_csv(csv_path, apply=True)
                            self.after(
                                0,
                                lambda: self._show_report_window(
                                    "Nettoyage CSV SNEP — appliqué", format_report(res)
                                ),
                            )
                            self._set_progress(
                                f"✅ SNEP nettoyé : {res['rows_in']}→{res['rows_out']} lignes"
                            )
                            self.after(500, self._update_status)

                        start_worker(apply)

                self.after(0, ask_and_apply)
            except Exception as e:
                logger.error(f"Erreur nettoyage SNEP : {e}")
                self._set_progress(f"❌ Erreur nettoyage SNEP : {e}")

        start_worker(run)

    def _show_report_window(self, title: str, text: str):
        """Affiche un rapport texte dans une fenêtre scrollable + bouton copier."""
        win = ctk.CTkToplevel(self)
        win.title(title)
        win.geometry("640x560")
        # Rester au-dessus de la fenêtre parente (sinon CTkToplevel s'ouvre
        # souvent DERRIÈRE la GUI). transient + topmost temporaire = passage
        # au premier plan fiable, puis on relâche le topmost pour ne pas
        # bloquer les autres fenêtres.
        win.transient(self)
        win.lift()
        win.attributes("-topmost", True)

        def _bring_to_front():
            win.lift()
            win.focus_force()
            win.attributes("-topmost", False)

        # délai court : laisse CTkToplevel finir son init avant le lift/focus
        win.after(200, _bring_to_front)

        ctk.CTkLabel(win, text=title, font=("Arial", 16, "bold")).pack(pady=10)

        box = ctk.CTkTextbox(win, font=("Consolas", 12))
        box.pack(fill="both", expand=True, padx=12, pady=8)
        box.insert("0.0", text)
        box.configure(state="disabled")

        btns = ctk.CTkFrame(win)
        btns.pack(fill="x", padx=12, pady=(0, 10))

        def copy():
            try:
                self.clipboard_clear()
                self.clipboard_append(text)
                self._set_progress("📋 Rapport copié")
            except Exception:
                pass

        ctk.CTkButton(btns, text="📋 Copier", command=copy, width=100).pack(side="left", padx=5)
        ctk.CTkButton(btns, text="Fermer", command=win.destroy, width=100).pack(
            side="right", padx=5
        )

    def _check_brma(self):
        """Valide le CSV BRMA (Ultratop) et affiche le rapport."""

        def run():
            try:
                from src.config import DATA_PATH
                from src.utils.brma_validator import format_report, validate_brma_csv

                csv_path = Path(DATA_PATH) / "certifications" / "brma" / "certif_brma.csv"
                if not csv_path.exists():
                    self._set_progress("❌ BRMA : fichier introuvable")
                    return
                self._set_progress("🔎 Validation du CSV BRMA...")
                report = validate_brma_csv(csv_path)
                text = format_report(report)
                verdict = "RAS" if report.get("ok") else "anomalies"
                self._set_progress(
                    f"{'✅' if report.get('ok') else '⚠️'} BRMA : {verdict} — "
                    f"{len(report.get('month_gaps', []))} mois sans certif (années actives)"
                )
                self.after(0, lambda: self._show_report_window("Validation CSV BRMA", text))
            except Exception as e:
                logger.error(f"Erreur validation BRMA : {e}")
                self._set_progress(f"❌ Erreur validation BRMA : {e}")

        start_worker(run)

    def _check_riaa(self):
        """Valide le CSV RIAA (certif_riaa.csv) et affiche le rapport."""

        def run():
            try:
                from src.config import DATA_PATH
                from src.utils.riaa_validator import format_report, validate_riaa_csv

                csv_path = Path(DATA_PATH) / "certifications" / "riaa" / "certif_riaa.csv"
                if not csv_path.exists():
                    self._set_progress("❌ RIAA : fichier introuvable")
                    return
                self._set_progress("🔎 Validation du CSV RIAA...")
                report = validate_riaa_csv(csv_path)
                text = format_report(report)
                verdict = "RAS" if report.get("ok") else "anomalies"
                self._set_progress(
                    f"{'✅' if report.get('ok') else '⚠️'} RIAA : {verdict} — "
                    f"{len(report.get('month_gaps', []))} mois sans certif (années actives)"
                )
                self.after(0, lambda: self._show_report_window("Validation CSV RIAA", text))
            except Exception as e:
                logger.error(f"Erreur validation RIAA : {e}")
                self._set_progress(f"❌ Erreur validation RIAA : {e}")

        start_worker(run)

    def _clean_riaa(self):
        """Nettoie le CSV RIAA (dédup + vides) après confirmation (backup créé)."""
        if not messagebox.askyesno(
            "Nettoyer RIAA",
            "Dédoublonner certif_riaa.csv (niveau normalisé) et retirer les "
            "lignes sans artiste/titre ? Un backup sera créé.",
            parent=self,
        ):
            return

        def run():
            try:
                self._set_progress("🧹 Nettoyage du CSV RIAA...")
                root = Path(__file__).parent.parent.parent
                result = subprocess.run(
                    [sys.executable, str(root / "src" / "utils" / "update_riaa.py"), "--clean"],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                summary = "\n".join((result.stdout or "").strip().splitlines()[-6:]) or "Terminé."
                try:
                    from src.utils.cert_matcher import reset_cert_matcher

                    reset_cert_matcher()
                except Exception:
                    pass
                self._set_progress("✅ RIAA nettoyé")
                self.after(0, lambda: self._show_report_window("Nettoyage CSV RIAA", summary))
                self.after(500, self._update_status)
            except Exception as e:
                logger.error(f"Erreur nettoyage RIAA : {e}")
                self._set_progress(f"❌ Erreur nettoyage RIAA : {e}")

        start_worker(run)

    def _clean_brma(self):
        """Régénère le clean BRMA depuis le brut (dédup) après confirmation."""
        if not messagebox.askyesno(
            "Nettoyer BRMA",
            "Régénérer certif_brma.csv depuis le brut (dédup + collapse des "
            "niveaux vides) ? Un backup sera créé.",
            parent=self,
        ):
            return

        def run():
            try:
                self._set_progress("🧹 Nettoyage du CSV BRMA...")
                root = Path(__file__).parent.parent.parent
                result = subprocess.run(
                    [sys.executable, str(root / "src" / "utils" / "update_brma.py"), "--dedup"],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                summary = "\n".join((result.stdout or "").strip().splitlines()[-6:]) or "Terminé."
                try:
                    from src.utils.cert_matcher import reset_cert_matcher

                    reset_cert_matcher()
                except Exception:
                    pass
                self._set_progress("✅ BRMA nettoyé")
                self.after(0, lambda: self._show_report_window("Nettoyage CSV BRMA", summary))
                self.after(500, self._update_status)
            except Exception as e:
                logger.error(f"Erreur nettoyage BRMA : {e}")
                self._set_progress(f"❌ Erreur nettoyage BRMA : {e}")

        start_worker(run)

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

        start_worker(update_all)

    def _run_update_script(
        self, script_name: str, source_name: str, extra_args=None, env_extra=None
    ):
        """Lance un script de mise à jour dans un thread.

        `env_extra` : variables d'environnement à injecter dans le sous-processus
        (ex: GENIUS_CDP_URL pour la route CDP de BRMA).
        """

        def run_script():
            try:
                self._set_progress(f"Mise à jour {source_name} en cours...")

                script_path = Path(__file__).parent.parent / "utils" / script_name

                if not script_path.exists():
                    raise FileNotFoundError(f"Script non trouvé: {script_path}")

                run_env = None
                if env_extra:
                    import os

                    run_env = {**os.environ, **{k: v for k, v in env_extra.items() if v}}

                # Lancer le script avec encodage UTF-8
                result = subprocess.run(
                    [sys.executable, str(script_path)] + list(extra_args or []),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    cwd=Path(__file__).parent.parent.parent,
                    env=run_env,
                )

                if result.returncode == 0:
                    self._set_progress(f"✅ Mise à jour {source_name} réussie")
                    self.after(500, self._update_status)
                    # Retour visible : dernières lignes de sortie du script
                    summary = (
                        "\n".join((result.stdout or "").strip().splitlines()[-8:])
                        or "Mise à jour terminée."
                    )
                    self.after(
                        0,
                        lambda: messagebox.showinfo(
                            f"Mise à jour {source_name}", summary, parent=self
                        ),
                    )
                else:
                    error_msg = result.stderr or result.stdout or "Erreur inconnue"
                    self._set_progress(f"❌ Erreur {source_name}: {error_msg[:50]}...")
                    logger.error(f"Erreur script {script_name}: {error_msg}")
                    self.after(
                        0,
                        lambda: messagebox.showerror(
                            f"Erreur {source_name}", error_msg[-600:], parent=self
                        ),
                    )

                # Effacer le message après 3 secondes
                self.after(3000, lambda: self._set_progress(""))

            except Exception as e:
                logger.error(f"Erreur lors de l'exécution de {script_name}: {e}")
                self._set_progress(f"❌ Erreur: {e}")
                self.after(3000, lambda: self._set_progress(""))

        start_worker(run_script)

    def _run_script_sync(self, script_name: str):
        """Lance un script de façon synchrone"""
        script_path = Path(__file__).parent.parent / "utils" / script_name

        if not script_path.exists():
            raise FileNotFoundError(f"Script non trouvé: {script_path}")

        result = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=Path(__file__).parent.parent.parent,
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

                csv_path = Path(DATA_PATH) / "certifications" / folder / filename

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

                if missing["gaps"]:
                    gap_count = len(missing["gaps"])
                    self._set_progress(
                        f"⚠️ {source_name}: {gap_count} période(s) manquante(s) détectée(s)"
                    )
                else:
                    self._set_progress(f"✅ {source_name}: Aucune période manquante")

            except Exception as e:
                logger.error(f"Erreur vérification {source_name}: {e}")
                self._set_progress(f"❌ Erreur vérification {source_name}: {e}")

        start_worker(check_async)

    def _analyze_csv_gaps(self, csv_path: Path, source: str) -> dict:
        """Analyse un CSV pour détecter les périodes manquantes"""
        try:
            # Charger le CSV avec gestion d'encodage
            try:
                df = pd.read_csv(csv_path, encoding="utf-8", sep=";")
            except Exception:
                df = pd.read_csv(csv_path, encoding="latin1", sep=";")

            if df.empty:
                return {"total": 0, "gaps": [], "date_range": None}

            # Identifier la colonne de date selon la source
            date_columns = {"SNEP": "Date de constat", "BRMA": "date", "RIAA": "certification_date"}

            date_col = date_columns.get(source)
            if not date_col or date_col not in df.columns:
                # Essayer de trouver une colonne de date
                possible_cols = [col for col in df.columns if "date" in col.lower()]
                if possible_cols:
                    date_col = possible_cols[0]
                else:
                    return {
                        "total": len(df),
                        "gaps": ["Colonne de date non trouvée"],
                        "date_range": None,
                    }

            # Convertir les dates
            df[date_col] = pd.to_datetime(df[date_col], errors="coerce", dayfirst=True)
            df = df.dropna(subset=[date_col])

            if df.empty:
                return {"total": 0, "gaps": ["Aucune date valide"], "date_range": None}

            # Analyser par année/mois
            df["year_month"] = df[date_col].dt.to_period("M")
            monthly_counts = df.groupby("year_month").size()

            # Détecter les gaps (mois sans certifications)
            if len(monthly_counts) == 0:
                return {"total": len(df), "gaps": [], "date_range": None}

            min_period = monthly_counts.index.min()
            max_period = monthly_counts.index.max()

            # Générer tous les mois entre min et max
            all_months = pd.period_range(start=min_period, end=max_period, freq="M")

            # Trouver les mois manquants (avec tolérance pour les mois récents)
            gaps = []
            current_month = pd.Period(datetime.now(), freq="M")

            for month in all_months:
                # Ne pas signaler comme manquant si c'est le mois en cours ou suivant
                if month >= current_month:
                    continue

                if month not in monthly_counts.index:
                    # Mois sans aucune certification
                    gaps.append(f"{month.strftime('%Y-%m')} (0 certifications)")
                elif monthly_counts[month] < 5:  # Seuil minimal de certifications par mois
                    gaps.append(
                        f"{month.strftime('%Y-%m')} ({monthly_counts[month]} certifications - possiblement incomplet)"
                    )

            return {
                "total": len(df),
                "gaps": gaps,
                "date_range": f"{min_period.strftime('%Y-%m')} à {max_period.strftime('%Y-%m')}",
                "monthly_avg": monthly_counts.mean(),
            }

        except Exception as e:
            logger.error(f"Erreur analyse CSV {source}: {e}")
            return {"total": 0, "gaps": [f"Erreur: {str(e)}"], "date_range": None}
