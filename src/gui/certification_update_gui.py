"""Interface graphique pour la mise à jour des certifications musicales"""

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk
import pandas as pd

from src.gui.workers.lifecycle import start_worker, stop_requested
from src.utils.cert_normalize import libelle_tronque
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
            text="🔎 Valider / Nettoyer",
            command=self._check_snep,
            width=110,
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
            text="🔎 Valider / Nettoyer",
            command=self._check_brma,
            width=110,
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
            text="🔎 Valider / Nettoyer",
            command=self._check_riaa,
            width=110,
            fg_color="gray40",
            hover_color="gray30",
        ).pack(side="right", padx=5, pady=5)

        # BPI (UK). Pas de préparation CDP, contrairement à BRMA : le site est
        # rendu côté serveur et un GET nu passe (mesuré). Lui coller la plomberie
        # navigateur « par symétrie » coûterait un Chrome pour rien.
        bpi_frame = ctk.CTkFrame(buttons_frame)
        bpi_frame.pack(fill="x", pady=5)

        ctk.CTkLabel(bpi_frame, text="🇬🇧 BPI (UK)").pack(side="left", padx=10)
        ctk.CTkButton(
            bpi_frame,
            text="Mettre à jour",
            command=self._update_bpi,
            width=120,
            fg_color="#1f4e8c",
        ).pack(side="right", padx=(5, 10), pady=5)
        ctk.CTkButton(
            bpi_frame,
            text="🔎 Valider / Nettoyer",
            command=self._check_bpi,
            width=110,
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
        # Plusieurs noms séparés par « ; » : un membre de groupe est crédité
        # sous son nom ET sous celui de sa formation (Shurik'N chez IAM), et
        # chercher un seul des deux ampute la moitié de sa discographie
        # certifiée. Le lot « groupes » remplira ce champ tout seul depuis les
        # relations d'artistes ; d'ici là il se saisit à la main.
        self.artist_entry = ctk.CTkEntry(
            artist_frame, placeholder_text="Artiste (; pour un groupe)", width=170
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

        # Vérifier les périodes manquantes (gaps par mois) dans les CSV brut
        ctk.CTkButton(
            global_buttons_frame,
            text="🕳️ Vérifier périodes manquantes",
            command=self._check_missing_periods_all,
            width=220,
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

            flags = {"SNEP": "🇫🇷", "BRMA": "🇧🇪", "RIAA": "🇺🇸", "BPI": "🇬🇧"}
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
                # `record_pending` au lieu de `save_track` : seules les deux
                # colonnes de certifs sont concernées. Le save complet réécrivait
                # une quarantaine de colonnes par morceau pour rien — et il ne
                # savait pas RETIRER une certification devenue caduque.
                for track in artist.tracks:
                    app.data_manager.record_pending(track)
                oublies = app.data_manager.certifications_non_enregistrees(artist.tracks)
                if oublies:
                    logger.error(
                        f"Certifs recalculées mais NON enregistrées ({len(oublies)}): "
                        f"{', '.join(oublies[:8])}"
                    )
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

    def _noms_artiste(self) -> list[str]:
        """Les noms saisis, séparés par « ; ». Un seul nom reste un seul nom."""
        from src.utils.cert_artist import noms_de_recherche

        brut = self.artist_entry.get().split(";")
        return noms_de_recherche(brut[0] if brut else "", brut[1:])

    def _fetch_artist_all_sources(self):
        """Récup des certifs d'un artiste sur les QUATRE corps.

        Ils ne s'interrogent pas de la même façon, et ce n'est pas un choix
        d'architecture — c'est ce que chaque site permet :
          · SNEP `?interprete=` rend un CSV portant tous les paliers du titre ;
          · RIAA `?ar=` déplie la timeline (échelle DATÉE) sur ses DEUX onglets,
            classique et latin ;
          · BPI expose un ANNUAIRE d'ids (`/artists?q=`) et un filtre cumulatif :
            c'est la seule des quatre où le rapprochement de noms se fait CHEZ
            ELLE. Corollaire : une entité BPI est la chaîne de crédit facturée,
            donc « Sigala » y rend 18 entités, toutes retenues ;
          · Ultratop n'expose aucune recherche par artiste qui nous soit
            accessible (Cloudflare, seules les pages par année passent) : BRMA
            contribue par LECTURE du corpus, complet et continu depuis 1995.
        On le dit à l'écran plutôt que de laisser croire à une requête.
        """
        from src.utils.cert_artist import (
            bilan_de,
            certifications,
            evolution,
            nouveautes,
            recap,
            repartition,
        )

        noms = self._noms_artiste()
        if not noms:
            messagebox.showwarning("Artiste manquant", "Saisis un nom d'artiste.", parent=self)
            return

        def run():
            root = Path(__file__).parent.parent.parent
            py = sys.executable
            outputs = []
            certs_avant = certifications(noms)
            etiquette = " + ".join(noms)
            # `--artist` est RÉPÉTABLE sur les deux scripts : un seul
            # sous-processus par source, quel que soit le nombre de noms.
            args_noms = [a for nom in noms for a in ("--artist", nom)]

            self._set_progress(f"🇫🇷 SNEP : {etiquette}…")
            try:
                _code, sortie = self._run_streaming(
                    [py, str(root / "src" / "utils" / "update_snep.py"), *args_noms],
                    f"SNEP {etiquette}",
                )
                outputs.append("SNEP : " + (sortie.strip().splitlines()[-1:] or ["ok"])[0])
            except Exception as e:
                logger.error(f"SNEP artiste : {e}")
                outputs.append(f"SNEP : erreur ({e})")

            # RIAA : headless d'abord, CDP en repli (cf. _update_riaa)
            self._set_progress(f"🇺🇸 RIAA : {etiquette}…")
            try:
                commande = [py, str(root / "src" / "utils" / "update_riaa.py"), *args_noms]
                code, sortie = self._run_streaming(commande, f"RIAA {etiquette}")
                if code != 0:
                    self._set_progress("🇺🇸 RIAA : repli via Chrome…")
                    cdp = self._preparer_cdp()
                    if cdp:
                        code, sortie = self._run_streaming(
                            commande,
                            f"RIAA {etiquette} (CDP)",
                            env={**os.environ, "GENIUS_CDP_URL": cdp},
                        )
                outputs.append("RIAA : " + (sortie.strip().splitlines()[-1:] or ["ok"])[0])
            except Exception as e:
                logger.error(f"RIAA artiste : {e}")
                outputs.append(f"RIAA : erreur ({e})")

            # BPI : HTTP nu, aucun navigateur, donc aucun repli à prévoir.
            self._set_progress(f"🇬🇧 BPI : {etiquette}…")
            try:
                _code, sortie = self._run_streaming(
                    [py, str(root / "src" / "utils" / "update_bpi.py"), *args_noms],
                    f"BPI {etiquette}",
                )
                outputs.append("BPI : " + (sortie.strip().splitlines()[-1:] or ["ok"])[0])
            except Exception as e:
                logger.error(f"BPI artiste : {e}")
                outputs.append(f"BPI : erreur ({e})")

            # Le magasin a changé sur disque : le matcher doit être reconstruit
            # AVANT le bilan d'après, sinon il relirait l'état d'avant le run.
            try:
                from src.utils.cert_matcher import reset_cert_matcher

                reset_cert_matcher()
            except Exception:
                logger.exception("Rafraîchissement du matcher")

            certs_apres = certifications(noms)
            outputs.append("BRMA : corpus local (Ultratop n'a pas de recherche par artiste)")

            # Le rapport dit CE QU'ON A, pas seulement combien : la liste des
            # titres avec leur échelle datée. Des compteurs se lisent sans rien
            # apprendre, et c'est justement l'échelle qui était la question — un
            # titre Platine deux ans après sa sortie a-t-il eu son Or avant ?
            neuves = nouveautes(certs_avant, certs_apres)
            parts = [
                "\n".join(outputs),
                # L'écart d'abord : un total ne dit pas si le run a servi,
                # « 12 certifications » se lit pareil qu'on en ait rapporté
                # douze ou zéro.
                f"Apport de ce run : {evolution(bilan_de(certs_avant), bilan_de(certs_apres))}",
            ]
            if ligne := repartition(certs_apres, noms):
                parts.append(ligne)
            parts.append(recap(certs_apres, neuves))
            rapport = "\n\n".join(parts)

            self._set_progress(f"✅ Certifs récupérées pour {etiquette}")
            self.after(
                0,
                lambda: self._show_report_window(f"Certifs par artiste — {etiquette}", rapport),
            )
            self.after(500, self._update_status)

        start_worker(run)

    def _update_brma(self):
        """Lance la mise à jour BRMA.

        Ici le Chrome de debug est préparé EN AMONT, contrairement à RIAA : le
        Cloudflare d'ultratop est strict et fait boucler tout navigateur lancé
        par de l'automation, même le vrai Chrome (piège documenté, JOURNAL
        2026-06-29). Le CDP n'y est pas un repli mais la seule route qui passe.
        """

        def prepare_and_run():
            self._set_progress("🌐 Préparation de Chrome (Cloudflare ultratop)...")
            cdp_url = self._preparer_cdp()
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

    def _update_bpi(self):
        """MàJ BPI : fenêtre glissante, en HTTP nu.

        Aucun navigateur, donc aucun repli CDP : le site est du htmx rendu côté
        serveur et un client non-navigateur y passe sans défi (mesuré le
        2026-09-07). C'est la source la plus légère des quatre.
        """
        self._run_update_script("update_bpi.py", "BPI", extra_args=["--auto"])

    def _update_riaa(self):
        """MàJ RIAA : **headless d'abord, CDP en repli**.

        Le Chrome de debug était préparé à CHAQUE mise à jour, comme pour BRMA.
        C'était injustifié : mesuré le 2026-09-06, patchright headless passe sur
        riaa.com, profil chaud comme froid. Le coût n'était pas seulement un
        lancement de navigateur pour rien — cela faisait du CDP la route
        NORMALE, donc celle qu'on n'a jamais vue échouer, et qu'on avait oublié
        de vérifier après la refonte du scraper.

        Le repli sert aussi de DIAGNOSTIC : s'il réussit là où le headless a
        échoué, c'était bien un problème d'accès et non un parseur cassé.
        (BRMA garde sa préparation en amont : le Cloudflare d'ultratop est
        strict et fait boucler tout navigateur d'automation — cf. CLAUDE.md.)
        """
        self._run_update_script("update_riaa.py", "RIAA", extra_args=["--auto"], repli_cdp=True)

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
                # Le validateur lit le CSV, qui porte encore les libellés
                # fautifs tant que le nettoyage n'a pas tourné : on lui passe
                # les décisions déjà prises pour qu'il n'en redemande pas.
                from src.utils.cert_fixes_io import charger_acceptes, charger_fixes

                report = validate_snep_csv(
                    csv_path, fixes=charger_fixes("snep"), acceptes=charger_acceptes("snep")
                )
                text = format_report(report)

                # Synthèse courte dans le bandeau de progression
                n_gaps = len(report.get("month_gaps", []))
                verdict = "RAS" if report.get("ok") else "anomalies"
                self._set_progress(
                    f"{'✅' if report.get('ok') else '⚠️'} SNEP : {verdict}"
                    f" — {n_gaps} mois sans certif (années actives)"
                )
                actions = [
                    ("🧹 Nettoyer", self._clean_snep),
                    ("✏️ Corriger les libellés", self._editer_titres_corrompus),
                ]
                # Le rattrapage n'apparaît QUE s'il y a des trous : proposer une
                # action sans objet, c'est laisser croire qu'il y a à faire.
                trous = report.get("month_gaps") or []
                if trous:
                    actions.append(
                        (
                            "🕳️ Rescraper les périodes",
                            lambda g=list(trous): self._rescraper_periodes(
                                "SNEP", ["src", "utils", "update_snep.py"], g
                            ),
                        )
                    )
                # Rapport détaillé dans une fenêtre dédiée, avec l'accès à la
                # correction manuelle des libellés que le nettoyeur ne sait pas
                # réparer tout seul.
                self.after(
                    0,
                    lambda: self._show_report_window(
                        "Validation CSV SNEP",
                        text,
                        actions=actions,
                    ),
                )
            except Exception as e:
                logger.error(f"Erreur validation SNEP : {e}")
                self._set_progress(f"❌ Erreur validation SNEP : {e}")

        start_worker(run)

    def _audit_snep_artist(self):
        """Audite les certifs SNEP de l'artiste face à sa discographie :
        liste les certifs orphelines (rattachées à aucun morceau).

        Le champ accepte plusieurs noms, mais l'audit n'en prend qu'UN : il
        confronte des certifs à la discographie CHARGÉE, et celle-ci est celle
        de l'artiste principal. Auditer le nom d'un groupe contre les morceaux
        d'un membre déclarerait orphelin tout ce que le membre n'a pas dans sa
        propre discographie — ce que le lot « groupes » traitera en réunissant
        les discographies, pas en changeant ce que compte l'audit.
        """
        noms = self._noms_artiste()
        artist = noms[0] if noms else ""
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

                def appliquer():
                    def travail():
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

                    start_worker(travail)

                def montrer():
                    # Le verdict vient du RAPPORT et de lui seul. La GUI le
                    # recalculait de son côté, en oubliant les apostrophes
                    # restaurées et les corrections manuelles : elle concluait
                    # « déjà propre » et n'offrait donc jamais d'appliquer, alors
                    # que le rapport annonçait 15 lignes à modifier.
                    actions = None
                    if dry.get("deja_propre"):
                        self._set_progress("✅ SNEP : CSV déjà propre")
                    else:
                        actions = [("✅ Appliquer le nettoyage", appliquer)]
                    self._show_report_window(
                        "Nettoyage CSV SNEP — aperçu", format_report(dry), actions=actions
                    )

                self.after(0, montrer)
            except Exception as e:
                logger.error(f"Erreur nettoyage SNEP : {e}")
                self._set_progress(f"❌ Erreur nettoyage SNEP : {e}")

        start_worker(run)

    def _show_report_window(self, title: str, text: str, actions: list | None = None):
        """Affiche un rapport texte dans une fenêtre scrollable + bouton copier.

        `actions` : liste de couples `(libellé, callback)` ajoutés à côté de
        « Copier ». C'est par là que la fenêtre de VALIDATION donne accès au
        nettoyage et à la correction des libellés : ces deux actions ne se
        décident qu'au vu du rapport, les proposer ailleurs revenait à demander
        de choisir avant d'avoir lu.
        """
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

        def _lancer(callback):
            # Refermer le rapport AVANT : l'action ouvre sa propre fenêtre, et
            # une boîte de dialogue qui s'empile derrière celle-ci passe pour
            # une absence de réponse.
            win.destroy()
            callback()

        for libelle, callback in actions or []:
            ctk.CTkButton(
                btns, text=libelle, command=lambda cb=callback: _lancer(cb), width=190
            ).pack(side="left", padx=5)
        ctk.CTkButton(btns, text="Fermer", command=win.destroy, width=100).pack(
            side="right", padx=5
        )

    def _editer_titres_corrompus(self):
        """Édite à la main les libellés SNEP porteurs d'un « ? ».

        La restauration automatique (`cert_normalize.restore_apostrophes`) ne
        touche QUE des contextes sûrs — élisions, contractions, mots en Œ connus.
        Mesuré sur le CSV réel : des 102 « ? » restants, l'écrasante majorité
        sont de vrais points d'interrogation (« QUI SAIT ? ») ; seule une poignée
        est corrompue. Les élargir par motif ferait plus de dégâts que de bien,
        d'où cette saisie.

        Ce qui est saisi ne va PAS dans le CSV : il part dans
        `manual_fixes.json` et est réappliqué à chaque « 🧹 Nettoyer » — une
        ré-importation SNEP ressert sinon le libellé fautif.
        """
        from src.config import DATA_PATH
        from src.utils.cert_fixes_io import (
            accepter,
            candidats_a_corriger,
            charger_acceptes,
            charger_fixes,
            enregistrer_fix,
        )
        from src.utils.snep_cleaner import _read_rows

        csv_path = Path(DATA_PATH) / "certifications" / "snep" / "certif-.csv"
        if not csv_path.exists():
            messagebox.showinfo("Titres à corriger", "CSV SNEP introuvable.", parent=self)
            return

        try:
            _header, rows = _read_rows(csv_path)
        except (OSError, ValueError) as e:
            messagebox.showerror("Titres à corriger", f"Lecture impossible : {e}", parent=self)
            return

        candidats = candidats_a_corriger(rows, charger_fixes("snep"), charger_acceptes("snep"))
        if not candidats:
            messagebox.showinfo(
                "Titres à corriger",
                "Aucun libellé cassé ou tronqué à arbitrer : ce que la restauration "
                "automatique sait réparer l'est déjà.",
                parent=self,
            )
            return

        self._fenetre_correction(candidats, enregistrer_fix, accepter)

    def _fenetre_correction(self, candidats: list, enregistrer_fix, accepter):
        """Fenêtre de saisie des corrections (une ligne par libellé).

        Deux décisions y sont possibles, et la seconde compte autant que la
        première : CORRIGER un libellé cassé, ou le VALIDER tel quel. Sans cette
        seconde, la liste ne décroît jamais — or la plupart des « ? » sont de
        vrais points d'interrogation (« ET ALORS ? », « YES, AND? ») et
        reviendraient éternellement demander un arbitrage déjà rendu.

        Ceux-là sont d'ailleurs MASQUÉS par défaut : un « ? » en fin de libellé
        ou suivi d'une espace est un point d'interrogation, pas une corruption.
        Seuls les « ? » ENTRE DEUX LETTRES sont montrés d'emblée.
        """
        win = ctk.CTkToplevel(self)
        win.title("Corriger les libellés")
        win.geometry("980x620")
        win.transient(self)
        win.lift()
        win.attributes("-topmost", True)
        win.after(200, lambda: (win.lift(), win.focus_force(), win.attributes("-topmost", False)))

        ctk.CTkLabel(
            win,
            text="Libellés SNEP cassés ou tronqués",
            font=("Arial", 16, "bold"),
        ).pack(pady=(12, 2))
        suspects = [c for c in candidats if c[0]]
        legitimes = [c for c in candidats if not c[0]]

        ctk.CTkLabel(
            win,
            text=(
                "Deux champs : ARTISTE puis TITRE ; le champ encadré est celui à revoir.\n"
                "Orange = « ? » corrompu · Bleu = libellé COUPÉ par la source "
                "(il manque du texte).\n"
                "« ✓ correct » mémorise que le libellé est BON tel quel : il ne "
                "reviendra plus.\n"
                "Les corrections sont réappliquées à chaque « 🧹 Nettoyer »."
            ),
            justify="left",
        ).pack(pady=(0, 8))

        montrer_legitimes = ctk.BooleanVar(value=not suspects)
        zone = ctk.CTkScrollableFrame(win)

        def remplir():
            for enfant in zone.winfo_children():
                enfant.destroy()
            saisies.clear()
            visibles = suspects + (legitimes if montrer_legitimes.get() else [])
            for candidat in visibles:
                _ligne_correction(candidat)

        ctk.CTkCheckBox(
            win,
            text=(
                f"Afficher aussi les {len(legitimes)} libellé(s) dont le « ? » est "
                "probablement un vrai point d'interrogation"
            ),
            variable=montrer_legitimes,
            command=lambda: remplir(),
        ).pack(anchor="w", padx=16, pady=(0, 6))

        zone.pack(fill="both", expand=True, padx=12, pady=6)

        # DEUX champs, artiste et titre : le défaut est tantôt dans l'un, tantôt
        # dans l'autre (« DES?REE — LIFE » : c'est l'ARTISTE qui est corrompu ;
        # « LES T — QU'EST-CE QU'ON S'FAIT CHIER » : c'est lui qui est coupé).
        # N'offrir que le titre demandait de corriger ce qui n'était pas cassé.
        saisies = []

        def _ligne_correction(candidat):
            suspect, artiste, titre, correction = candidat
            ligne = ctk.CTkFrame(zone)
            ligne.pack(fill="x", pady=3)
            ctk.CTkLabel(
                ligne,
                text="⚠️" if suspect else "  ",
                width=24,
                text_color="#FFA500" if suspect else None,
            ).pack(side="left", padx=(6, 0))

            champs = {}
            for cle, valeur in (("artist", artiste), ("title", titre)):
                # Deux défauts, deux couleurs : ils n'appellent pas le même
                # geste. Un « ? » se remplace par le bon caractère ; un libellé
                # coupé demande de retrouver le texte manquant, que la donnée ne
                # porte nulle part — c'est une recherche, pas une frappe.
                corrompu = "?" in valeur
                couleur = (
                    "#FFA500" if corrompu else ("#3B8ED0" if libelle_tronque(valeur) else None)
                )
                champ = ctk.CTkEntry(
                    ligne,
                    width=300,
                    # Le champ à corriger saute aux yeux ; l'autre reste
                    # modifiable, mais n'attire pas l'attention.
                    border_color=couleur,
                    border_width=2 if couleur else 1,
                )
                champ.insert(0, (correction or {}).get(cle) or valeur)
                champ.pack(side="left", padx=6, pady=4)
                champs[cle] = champ

            correct = ctk.BooleanVar(value=False)
            ctk.CTkCheckBox(ligne, text="✓ correct", variable=correct, width=90).pack(
                side="left", padx=(10, 6)
            )
            saisies.append((artiste, titre, champs, correct))

        remplir()

        def enregistrer():
            n_corrections = n_acceptes = 0
            for artiste, titre, champs, correct in saisies:
                if correct.get():
                    accepter("snep", artiste, titre)
                    n_acceptes += 1
                    continue
                artiste_fixe = champs["artist"].get().strip() or artiste
                titre_fixe = champs["title"].get().strip() or titre
                if (artiste_fixe, titre_fixe) != (artiste, titre):
                    enregistrer_fix("snep", artiste, titre, artiste_fixe, titre_fixe)
                    logger.info(
                        f"[SNEP] correction manuelle : {artiste!r} — {titre!r} → "
                        f"{artiste_fixe!r} — {titre_fixe!r}"
                    )
                    n_corrections += 1
            win.destroy()
            messagebox.showinfo(
                "Libellés à corriger",
                f"{n_corrections} correction(s) et {n_acceptes} libellé(s) validé(s) "
                "tels quels.\n\nLance « 🧹 Nettoyer » sur le SNEP pour appliquer "
                "les corrections au CSV.",
                parent=self,
            )
            self._set_progress(f"✏️ {n_corrections} correction(s), {n_acceptes} validation(s)")

        barre = ctk.CTkFrame(win)
        barre.pack(fill="x", padx=12, pady=(0, 12))
        ctk.CTkButton(barre, text="💾 Enregistrer", command=enregistrer, width=140).pack(
            side="left", padx=5
        )
        ctk.CTkButton(barre, text="Fermer", command=win.destroy, width=100).pack(
            side="right", padx=5
        )

    def _valider_source(self, source, dossier, fichier, importer, nettoyeur, script):
        """Valide le CSV d'une source, affiche le rapport et propose les suites.

        Factorisé entre BRMA, RIAA et BPI : les trois boutons faisaient
        exactement la même chose à trois noms près. Une troisième copie aurait
        été la copie de trop — ce projet a déjà payé le prix d'un garde-fou posé
        sur une seule de deux voies jumelles.

        `importer` diffère l'import du validateur (qui charge pandas) jusqu'au
        thread de fond, comme le faisait chaque copie.
        """

        def run():
            try:
                from src.config import DATA_PATH

                valider, formater = importer()
                csv_path = Path(DATA_PATH) / "certifications" / dossier / fichier
                if not csv_path.exists():
                    self._set_progress(f"❌ {source} : fichier introuvable")
                    return
                self._set_progress(f"🔎 Validation du CSV {source}...")
                report = valider(csv_path)
                text = formater(report)
                verdict = "RAS" if report.get("ok") else "anomalies"
                self._set_progress(
                    f"{'✅' if report.get('ok') else '⚠️'} {source} : {verdict} — "
                    f"{len(report.get('month_gaps', []))} mois sans certif (années actives)"
                )
                actions = [("🧹 Nettoyer", nettoyeur)]
                # Le rattrapage n'apparaît QUE s'il y a des trous : proposer une
                # action sans objet, c'est laisser croire qu'il y a à faire.
                trous = report.get("month_gaps") or []
                if trous:
                    actions.append(
                        (
                            "🕳️ Rescraper les périodes",
                            lambda g=list(trous): self._rescraper_periodes(source, script, g),
                        )
                    )
                self.after(
                    0,
                    lambda: self._show_report_window(
                        f"Validation CSV {source}", text, actions=actions
                    ),
                )
            except Exception as e:
                logger.error(f"Erreur validation {source} : {e}")
                self._set_progress(f"❌ Erreur validation {source} : {e}")

        start_worker(run)

    def _check_brma(self):
        """Valide le CSV BRMA (Ultratop) et affiche le rapport."""

        def importer():
            from src.utils.brma_validator import format_report, validate_brma_csv

            return validate_brma_csv, format_report

        self._valider_source(
            "BRMA",
            "brma",
            "certif_brma.csv",
            importer,
            self._clean_brma,
            ["src", "utils", "update_brma.py"],
        )

    def _check_riaa(self):
        """Valide le CSV RIAA (certif_riaa.csv) et affiche le rapport."""

        def importer():
            from src.utils.riaa_validator import format_report, validate_riaa_csv

            return validate_riaa_csv, format_report

        self._valider_source(
            "RIAA",
            "riaa",
            "certif_riaa.csv",
            importer,
            self._clean_riaa,
            ["src", "utils", "update_riaa.py"],
        )

    def _check_bpi(self):
        """Valide le CSV BPI (certif_bpi.csv) et affiche le rapport."""

        def importer():
            from src.utils.bpi_validator import format_report, validate_bpi_csv

            return validate_bpi_csv, format_report

        self._valider_source(
            "BPI",
            "bpi",
            "certif_bpi.csv",
            importer,
            self._clean_bpi,
            ["src", "utils", "update_bpi.py"],
        )

    def _clean_riaa(self):
        """Aperçu (dry-run) du nettoyage RIAA, puis application sur confirmation.

        Même déroulé que SNEP : on ne demande plus de valider à l'aveugle une
        réécriture de plusieurs dizaines de milliers de lignes — on montre
        d'abord ce qui serait retiré.
        """
        self._nettoyer_avec_apercu(
            "RIAA",
            ["src", "utils", "update_riaa.py"],
            ["--clean"],
        )

    def _clean_bpi(self):
        """Aperçu (dry-run) du nettoyage BPI, puis application sur confirmation."""
        self._nettoyer_avec_apercu(
            "BPI",
            ["src", "utils", "update_bpi.py"],
            ["--clean"],
        )

    def _clean_brma(self):
        """Aperçu (dry-run) du nettoyage BRMA, puis application sur confirmation."""
        self._nettoyer_avec_apercu(
            "BRMA",
            ["src", "utils", "update_brma.py"],
            ["--dedup"],
        )

    @staticmethod
    def _resume_humain(sortie: str, lignes_max: int = 8) -> str:
        """Ce qu'un humain veut lire à la fin d'une MàJ, pas le journal brut.

        La sortie relayée porte les préfixes de logging des sous-processus
        (`INFO:module:`, `2026-… - INFO - `) et l'initialisation d'Alembic : de
        quoi noyer les deux lignes qui comptent. On dépréfixe, on écarte le
        bruit d'amorçage, on dédoublonne — le détail complet reste en console et
        dans le fichier de log du jour.
        """
        import re

        bruit = (
            "alembic.",
            "Base de données initialisée",
            "setup plugin",
            "Context impl",
            "non-transactional DDL",
        )
        vues, propres = set(), []
        for ligne in sortie.splitlines():
            texte = re.sub(r"^(?:\w+:[\w.]+:|[\d-]{10} [\d:,]+ - \w+ - )+", "", ligne).strip()
            if not texte or any(motif in texte for motif in bruit) or texte in vues:
                continue
            vues.add(texte)
            propres.append(texte)
        return "\n".join(propres[-lignes_max:])

    @staticmethod
    def _extraire_rapport(sortie: str) -> str:
        """Isole le rapport encadré des lignes de log qui le précèdent.

        Les scripts sont lancés en sous-processus : leur sortie porte aussi
        l'initialisation Alembic et les logs du module. Le rapport commence à sa
        première ligne de séparation.
        """
        lignes = sortie.splitlines()
        for i, ligne in enumerate(lignes):
            if ligne.startswith("===="):
                return "\n".join(lignes[i:])
        return sortie

    def _rescraper_periodes(self, source: str, script: list[str], gaps: list[str]):
        """Relance la collecte sur les mois signalés par la validation.

        Le chaînon qui manquait : les validateurs savaient DIRE quels mois sont
        vides, rien ne savait aller les chercher. Les trois sources ne se visent
        pas à la même maille — le site l'impose — d'où la traduction dans
        `cert_rescrape`, module pur et donc vérifiable sans réseau.
        """
        from src.utils.cert_rescrape import commandes, resume

        chemin = str(Path(__file__).parent.parent.parent.joinpath(*script))
        a_lancer = commandes(source, gaps, chemin)
        if not a_lancer:
            messagebox.showinfo(f"Rescraper {source}", "Aucune période ciblable.", parent=self)
            return

        apercu = (
            resume(source, gaps)
            + "\n\n"
            + "\n".join(
                "$ " + " ".join(Path(c[1]).name if i == 1 else c for i, c in enumerate(cmd))
                for cmd in a_lancer[:8]
            )
        )
        if len(a_lancer) > 8:
            apercu += f"\n… et {len(a_lancer) - 8} autre(s)"
        if not messagebox.askyesno(
            f"Rescraper {source}",
            f"{apercu}\n\nLancer ? (les données sont fusionnées, jamais remplacées)",
            parent=self,
        ):
            return

        def travail():
            for i, cmd in enumerate(a_lancer, 1):
                if stop_requested():
                    break
                self._set_progress(f"🕳️ {source} : période {i}/{len(a_lancer)}…")
                self._run_streaming(cmd, f"{source} rattrapage {i}/{len(a_lancer)}")
            self._set_progress(f"✅ {source} : {len(a_lancer)} période(s) relancée(s)")
            self.after(500, self._update_status)

        start_worker(travail)

    def _nettoyer_avec_apercu(self, source: str, script: list[str], args: list[str]):
        """Dry-run → rapport → confirmation → application (déroulé commun).

        Factorisé entre BRMA et RIAA : ces deux boutons faisaient la même chose
        à la ligne près, et c'est exactement ainsi que leurs comportements
        avaient fini par diverger de celui de SNEP.
        """

        def run():
            try:
                racine = Path(__file__).parent.parent.parent
                chemin = str(racine.joinpath(*script))

                self._set_progress(f"🧹 {source} : analyse (aperçu)…")
                _code, sortie = self._run_streaming(
                    [sys.executable, chemin, *args, "--dry-run"], f"{source} aperçu"
                )
                apercu = self._extraire_rapport(sortie)

                # Verdict du rapport : inutile de proposer une réécriture qui
                # ne changerait rien (même déroulé que le nettoyeur SNEP).
                deja_propre = "DÉJÀ à jour" in apercu

                def appliquer():
                    def travail():
                        self._set_progress(f"🧹 {source} : nettoyage en cours…")
                        _c, sortie_appliquee = self._run_streaming(
                            [sys.executable, chemin, *args], f"{source} nettoyage"
                        )
                        try:
                            from src.utils.cert_matcher import reset_cert_matcher

                            reset_cert_matcher()
                        except ImportError as e:
                            logger.warning(f"Matcher non rafraîchi : {e}")
                        self._set_progress(f"✅ {source} nettoyé")
                        self.after(
                            0,
                            lambda: self._show_report_window(
                                f"Nettoyage CSV {source} — appliqué",
                                self._extraire_rapport(sortie_appliquee),
                            ),
                        )
                        self.after(500, self._update_status)

                    start_worker(travail)

                def montrer():
                    actions = None
                    if deja_propre:
                        self._set_progress(f"✅ {source} : le CSV est déjà propre")
                    else:
                        actions = [("✅ Appliquer le nettoyage", appliquer)]
                    self._show_report_window(
                        f"Nettoyage CSV {source} — aperçu", apercu, actions=actions
                    )

                self.after(0, montrer)
            except OSError as e:
                logger.error(f"Erreur nettoyage {source} : {e}")
                self._set_progress(f"❌ Erreur nettoyage {source} : {e}")

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

                # BPI
                self._set_progress("Mise à jour BPI en cours...")
                self._run_script_sync("update_bpi.py")

                self._set_progress("Toutes les mises à jour terminées !")
                self.after(2000, lambda: self._set_progress(""))
                self.after(500, self._update_status)

            except Exception as e:
                logger.error(f"Erreur mise à jour globale: {e}")
                self._set_progress(f"❌ Erreur: {e}")

        start_worker(update_all)

    def _preparer_cdp(self) -> str | None:
        """Lance (ou retrouve) un Chrome de debug et rend son URL CDP."""
        try:
            from src.scrapers.cdp_chrome import ensure_cdp_chrome

            return ensure_cdp_chrome()
        except Exception as e:
            logger.error(f"Préparation CDP échouée : {e}")
            return None

    def _run_update_script(
        self,
        script_name: str,
        source_name: str,
        extra_args=None,
        env_extra=None,
        repli_cdp: bool = False,
    ):
        """Lance un script de mise à jour dans un thread.

        `env_extra` : variables d'environnement à injecter dans le sous-processus
        (ex: GENIUS_CDP_URL pour la route CDP de BRMA).
        `repli_cdp` : en cas d'échec, retenter UNE fois via un Chrome de debug.
        Réservé aux sources dont la route normale est le headless — c'est-à-dire
        celles où le CDP répond à un problème d'ACCÈS, pas à un besoin permanent.
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

                # Sortie RELAYÉE en direct (console + log du jour), au lieu
                # d'être avalée jusqu'à la fin du processus.
                code, sortie = self._run_streaming(
                    [sys.executable, str(script_path), *(extra_args or [])],
                    source_name,
                    env=run_env,
                )

                if code != 0 and repli_cdp:
                    self._set_progress(
                        f"{source_name} : échec en headless — seconde tentative via Chrome…"
                    )
                    logger.warning(
                        f"[{source_name}] échec en headless, repli sur la route CDP "
                        "(si elle réussit, c'était un problème d'accès et non un parseur cassé)"
                    )
                    cdp_url = self._preparer_cdp()
                    if cdp_url:
                        code, sortie_cdp = self._run_streaming(
                            [sys.executable, str(script_path), *(extra_args or [])],
                            f"{source_name} (CDP)",
                            env={**os.environ, "GENIUS_CDP_URL": cdp_url},
                        )
                        sortie = sortie_cdp or sortie
                    else:
                        logger.error(
                            f"[{source_name}] repli CDP impossible : Chrome introuvable "
                            "(installe Google Chrome ou définis CHROME_PATH)"
                        )

                if code == 0:
                    self._set_progress(f"✅ Mise à jour {source_name} réussie")
                    self.after(500, self._update_status)
                    # Retour visible, DÉBRUITÉ : la console garde le détail.
                    summary = self._resume_humain(sortie) or "Mise à jour terminée."
                    self.after(
                        0,
                        lambda: messagebox.showinfo(
                            f"Mise à jour {source_name}", summary, parent=self
                        ),
                    )
                else:
                    error_msg = self._resume_humain(sortie, lignes_max=12) or "Erreur inconnue"
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

        code, sortie = self._run_streaming(
            [sys.executable, str(script_path)], script_name.replace("update_", "").upper()
        )

        if code != 0:
            raise RuntimeError(f"Erreur script {script_name}: {sortie or 'Erreur inconnue'}")

    def _set_progress(self, message: str):
        """Met à jour le message de progression"""

        def update():
            self.progress_label.configure(text=message)

        self.after(0, update)

    def _run_streaming(self, cmd: list[str], tag: str, env: dict | None = None) -> tuple[int, str]:
        """Lance un script de certifs en RELAYANT sa sortie ligne à ligne.

        `subprocess.run(capture_output=True)` avalait tout jusqu'à la fin du
        processus, puis n'en montrait que les dernières lignes dans une boîte de
        dialogue : pendant une MàJ RIAA de plusieurs minutes, la console de
        l'application ne disait rien — alors que le reste de l'app y trace tout.
        On relaie donc chaque ligne dans le logger, ce qui la fait apparaître en
        console ET dans le fichier de log du jour.

        `-u` est INDISPENSABLE : la sortie d'un Python dont stdout est un tuyau
        est bufferisée par blocs, et « relayer » l'aurait simplement livrée d'un
        coup à la fin — le défaut qu'on corrige, à l'identique.
        """
        proc = subprocess.Popen(
            [cmd[0], "-u", *cmd[1:]],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            cwd=Path(__file__).parent.parent.parent,
            env=env,
        )
        lignes: list[str] = []
        dernier_affichage = 0.0
        with proc.stdout:
            for ligne in proc.stdout:
                ligne = ligne.rstrip()
                if not ligne:
                    continue
                lignes.append(ligne)
                logger.info(f"[{tag}] {ligne}")
                # Le bandeau ne suit pas chaque ligne : un `after(0, …)` par
                # ligne noierait la boucle Tk sur un script bavard.
                maintenant = time.monotonic()
                if maintenant - dernier_affichage > 0.3:
                    dernier_affichage = maintenant
                    self._set_progress(f"{tag} : {ligne[:70]}")
        return proc.wait(), "\n".join(lignes)

    def _check_missing_periods_all(self):
        """Lance la vérification des périodes manquantes pour chaque source.

        Analyse le CSV BRUT de chaque source (accumulation complète, avec sa
        colonne de date native) — distinct de l'audit PAR ARTISTE
        (`_audit_snep_artist`). Chaque appel s'exécute dans son propre worker et
        rafraîchit l'état à la fin.
        """
        for source_name, folder, filename in (
            ("SNEP", "snep", "certif-.csv"),
            ("BRMA", "brma", "brma_raw.csv"),
            ("RIAA", "riaa", "riaa_raw.csv"),
            ("BPI", "bpi", "bpi_raw.csv"),
        ):
            self._check_missing_periods(source_name, folder, filename)

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
            # Charger le CSV : séparateur auto-détecté (SNEP=';', BRMA/RIAA=',')
            # via le moteur python, avec repli d'encodage.
            try:
                df = pd.read_csv(csv_path, encoding="utf-8", sep=None, engine="python")
            except Exception:
                df = pd.read_csv(csv_path, encoding="latin1", sep=None, engine="python")

            if df.empty:
                return {"total": 0, "gaps": [], "date_range": None}

            # Colonne de date par source (nom brut). Match insensible à la casse,
            # puis repli sur toute colonne contenant « date ».
            date_columns = {
                "SNEP": "Date de constat",
                "BRMA": "certification_date",
                "BPI": "certification_date",
                "RIAA": "Certification_Date",
            }

            date_col = date_columns.get(source)
            if not date_col or date_col not in df.columns:
                lowered = {c.lower(): c for c in df.columns}
                if date_col and date_col.lower() in lowered:
                    date_col = lowered[date_col.lower()]
                else:
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
