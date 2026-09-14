"""Interface graphique pour la mise à jour des certifications musicales"""

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from tkinter import messagebox
from typing import NamedTuple

import customtkinter as ctk

from src.config import DATA_PATH
from src.gui.workers.lifecycle import start_worker, stop_requested
from src.utils.cert_normalize import drapeau, libelle_tronque
from src.utils.cert_trous import BRUTS_PAR_SOURCE as _BRUTS_PAR_SOURCE
from src.utils.cert_trous import periodes_manquantes
from src.utils.logger import get_logger

logger = get_logger(__name__)


class MiseAJour(NamedTuple):
    """Ce qu'il faut lancer pour mettre à jour une source.

    UNE déclaration, consommée par le bouton individuel ET par « 🔄 Tout mettre
    à jour ». Ce dernier appelait `_run_script_sync(script)` **sans aucun
    argument** : trois sources sur quatre en sortaient cassées — BRMA
    (`--mode manual` par défaut) et RIAA (`manual_update()`) partaient en mode
    INTERACTIF face à un stdin invalide, et BPI affichait son aide en sortant
    en 0, donc en succès. BRMA perdait en prime sa préparation de Chrome,
    décrite dans son propre code comme « la seule route qui passe ».
    """

    script: str
    args: tuple[str, ...] = ()
    #: Chrome de debug préparé EN AMONT (BRMA : le Cloudflare d'ultratop fait
    #: boucler tout navigateur d'automation, le CDP n'y est pas un repli).
    cdp_amont: bool = False
    #: Chrome de debug en REPLI d'un échec (RIAA : le headless passe, et un
    #: repli qui réussit dit que c'était un problème d'accès, pas un parseur).
    repli_cdp: bool = False


MISES_A_JOUR: dict[str, MiseAJour] = {
    "SNEP": MiseAJour("update_snep.py"),
    "BRMA": MiseAJour("update_brma.py", ("--mode", "once", "--years-back", "1"), cdp_amont=True),
    "RIAA": MiseAJour("update_riaa.py", ("--auto",), repli_cdp=True),
    "BPI": MiseAJour("update_bpi.py", ("--auto",)),
}


def _ligne_bilan(source: str, code: int, sortie: str) -> str:
    """Une ligne de bilan par source, qui DIT si le script a échoué.

    La dernière ligne de sortie était reprise telle quelle, code de retour
    ignoré : un script planté produisait « SNEP : <dernière ligne quelconque> »,
    indiscernable d'un succès. Seul RIAA exploitait son code, et seulement pour
    déclencher le repli CDP.
    """
    derniere = (sortie.strip().splitlines()[-1:] or ["ok"])[0]
    return (
        f"{source} : {derniere}" if code == 0 else f"{source} : ❌ ÉCHEC (code {code}) — {derniere}"
    )


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
        #: Travaux en cours, par CLÉ. Aucune ré-entrance n'était gardée et aucun
        #: bouton n'était désactivé : deux clics sur « Mettre à jour » lançaient
        #: deux sous-processus écrivant le MÊME CSV.
        self._en_cours: dict[str, str] = {}
        self._ferme = False
        self.title("Mise à jour des certifications")
        self.geometry("600x700")

        # Centrer la fenêtre
        self.update_idletasks()
        x = (self.winfo_screenwidth() // 2) - (300)
        y = (self.winfo_screenheight() // 2) - (350)
        self.geometry(f"600x700+{x}+{y}")

        self.lift()
        self.focus_force()

        # Fermer pendant un run faisait exploser `after` sur un widget détruit,
        # au milieu de la boucle de relais de `_run_streaming`.
        self.protocol("WM_DELETE_WINDOW", self._on_closing)

        self._create_widgets()
        self._update_status()

    def _on_closing(self):
        """Ferme, en prévenant les fils de fond qu'ils écrivent dans le vide."""
        self._ferme = True
        if self._en_cours:
            logger.info(f"Fenêtre certifs fermée pendant : {', '.join(self._en_cours.values())}")
        self.destroy()

    def _demarrer(self, cle: str, travail, libelle: str = "") -> bool:
        """Lance `travail` dans un fil, en refusant les lancements CONCURRENTS.

        Les clés se PARTAGENT volontairement : tout ce qui écrit un magasin
        porte « ecriture », de sorte qu'une validation (lecture seule) reste
        possible pendant une mise à jour, mais que deux écritures ne se croisent
        jamais sur les mêmes CSV.
        """
        if cle in self._en_cours:
            messagebox.showinfo(
                "Déjà en cours",
                f"« {self._en_cours[cle]} » n'est pas terminé.\n\n"
                "Attends la fin — deux traitements qui écrivent les mêmes "
                "fichiers en même temps se marcheraient dessus.",
                parent=self,
            )
            return False

        self._en_cours[cle] = libelle or cle

        def enveloppe():
            try:
                travail()
            finally:
                self._en_cours.pop(cle, None)

        start_worker(enveloppe)
        return True

    def _rafraichir_apres_ecriture(self):
        """Rafraîchit l'état APRÈS une écriture, en INVALIDANT ce qui a vieilli.

        `missing_periods` n'était jamais vidé : le pavé « périodes manquantes »
        survivait à un rescrape réussi, affiché sous un horodatage
        « Vérification : maintenant » qui le démentait dans la même fenêtre.
        Les trous se recalculent depuis les CSV, qui viennent de changer — les
        garder, c'est afficher la mesure d'avant sous la date d'après.
        """
        self.missing_periods.clear()
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

        # Une LIGNE PAR SOURCE, construites par une boucle. C'étaient quatre
        # blocs isomorphes de vingt lignes, où seuls variaient le libellé et la
        # couleur — la cinquième des six énumérations parallèles des quatre
        # sources qu'il fallait éditer pour en ajouter une. Le libellé et la
        # couleur restent ici : ce sont des choix d'affichage, ils n'ont rien à
        # faire dans un référentiel de données.
        for nom, pays, couleur in (
            ("SNEP", "France", "blue"),
            ("BRMA", "Belgique", "orange"),
            ("RIAA", "USA", "red"),
            # BPI n'a aucune préparation CDP, contrairement à BRMA : son site est
            # rendu côté serveur et un GET nu passe (mesuré). Lui coller la
            # plomberie navigateur « par symétrie » coûterait un Chrome pour rien.
            ("BPI", "UK", "#1f4e8c"),
        ):
            ligne = ctk.CTkFrame(buttons_frame)
            ligne.pack(fill="x", pady=5)

            ctk.CTkLabel(ligne, text=f"{drapeau(nom)} {nom} ({pays})").pack(side="left", padx=10)
            ctk.CTkButton(
                ligne,
                text="Mettre à jour",
                command=lambda n=nom: self._lancer_maj(n),
                width=120,
                fg_color=couleur,
            ).pack(side="right", padx=(5, 10), pady=5)
            ctk.CTkButton(
                ligne,
                text="🔎 Valider / Nettoyer",
                command=getattr(self, f"_check_{nom.lower()}"),
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
        # certifiée. Depuis le lot « groupes », ce champ se remplit tout seul
        # depuis les formations CONFIRMÉES — il reste éditable, parce que la
        # base ne connaît pas toutes les formations du monde.
        self.artist_entry = ctk.CTkEntry(
            artist_frame, placeholder_text="Artiste (; pour un groupe)", width=170
        )
        self.artist_entry.pack(side="right", padx=5, pady=5)
        prerempli = self._noms_a_preremplir()
        if prerempli:
            self.artist_entry.insert(0, " ; ".join(prerempli))

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

            for source in all_certification_sources():
                flag = drapeau(source.name)
                fresh = source.freshness()
                if not fresh["available"]:
                    status_text += f"{flag} {source.name}: ❌ Pas de données\n"
                    continue
                if fresh["last_global"]:
                    # La date reste VRAIE — on a bien vérifié à cette heure-là.
                    # Ce qui manquait est à côté d'elle : sans le motif, un run
                    # partiel affiche la même coche verte qu'un run complet, et
                    # la boîte d'erreur qui le contredisait a disparu depuis
                    # longtemps quand on revient regarder le panneau.
                    marque = "⚠️" if fresh["partial"] else "✅"
                    status_text += (
                        f"{flag} {source.name}: {marque} Dernière MàJ globale: "
                        f"{_fmt(fresh['last_global'])}\n"
                    )
                    if fresh["partial"]:
                        status_text += f"   ⚠️ PARTIELLE — {fresh['partial']}\n"
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
        self._lancer_maj("SNEP")

    def _noms_a_preremplir(self) -> list[str]:
        """L'artiste courant, puis les formations sous lesquelles il est crédité.

        Seuls les liens `member_of` comptent : on cherche les certifications de
        l'artiste sous le nom de SES groupes, pas sous celui de ses membres — un
        groupe n'est pas crédité sous le nom de chacun d'eux. Les `alias`
        comptent aussi : ils désignent la même personne.

        Les liens sont ceux CONFIRMÉS à la main. Une formation devinée par un
        rapprochement de noms n'a rien à faire dans une URL de recherche.
        """
        from src.utils.cert_artist import noms_de_recherche

        principal = getattr(self, "default_artist", None) or ""
        formations: list[str] = []
        artiste = getattr(self.app, "current_artist", None) if self.app else None
        if artiste and artiste.id:
            try:
                formations = [
                    rel.related_name
                    for rel in self.app.data_manager.get_artist_relations(artiste.id)
                    if rel.kind in ("member_of", "alias")
                ]
            except Exception as e:  # noqa: BLE001 — le champ reste saisissable
                logger.warning(f"Formations indisponibles pour le préremplissage : {e}")
        return noms_de_recherche(principal, formations)

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
                code, sortie = self._run_streaming(
                    [py, str(root / "src" / "utils" / "update_snep.py"), *args_noms],
                    f"SNEP {etiquette}",
                )
                outputs.append(_ligne_bilan("SNEP", code, sortie))
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
                outputs.append(_ligne_bilan("RIAA", code, sortie))
            except Exception as e:
                logger.error(f"RIAA artiste : {e}")
                outputs.append(f"RIAA : erreur ({e})")

            # BPI : HTTP nu, aucun navigateur, donc aucun repli à prévoir.
            self._set_progress(f"🇬🇧 BPI : {etiquette}…")
            try:
                code, sortie = self._run_streaming(
                    [py, str(root / "src" / "utils" / "update_bpi.py"), *args_noms],
                    f"BPI {etiquette}",
                )
                outputs.append(_ligne_bilan("BPI", code, sortie))
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
        """MàJ BRMA — Chrome préparé EN AMONT (cf. `MISES_A_JOUR`)."""
        self._lancer_maj("BRMA")

    def _update_bpi(self):
        """MàJ BPI : fenêtre glissante, en HTTP nu.

        Aucun navigateur, donc aucun repli CDP : le site est du htmx rendu côté
        serveur et un client non-navigateur y passe sans défi (mesuré le
        2026-09-07). C'est la source la plus légère des quatre.
        """
        self._lancer_maj("BPI")

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
        """
        self._lancer_maj("RIAA")

    def _check_snep(self):
        """Lance le validateur complet du CSV maître SNEP et affiche le rapport."""

        def run():
            try:
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
            # Les codes de retour étaient JETÉS : douze rattrapages qui échouent
            # tous s'affichaient « ✅ 12 période(s) relancée(s) ». C'est le seul
            # chemin de comblement de trous du logiciel — il ne pouvait pas dire
            # qu'il n'avait rien comblé.
            echecs = []
            for i, cmd in enumerate(a_lancer, 1):
                if stop_requested():
                    break
                self._set_progress(f"🕳️ {source} : période {i}/{len(a_lancer)}…")
                code, _ = self._run_streaming(cmd, f"{source} rattrapage {i}/{len(a_lancer)}")
                if code != 0:
                    echecs.append(i)
            if echecs:
                self._set_progress(
                    f"❌ {source} : {len(echecs)}/{len(a_lancer)} période(s) en ÉCHEC"
                )
                self.after(
                    0,
                    lambda: messagebox.showerror(
                        f"Rescraper {source}",
                        f"{len(echecs)} période(s) sur {len(a_lancer)} ont échoué "
                        f"(n° {', '.join(map(str, echecs[:10]))}).\n\n"
                        "Les trous correspondants n'ont PAS été comblés — "
                        "voir la console pour le détail.",
                        parent=self,
                    ),
                )
            else:
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
                        code, sortie_appliquee = self._run_streaming(
                            [sys.executable, chemin, *args], f"{source} nettoyage"
                        )
                        try:
                            from src.utils.cert_matcher import reset_cert_matcher

                            reset_cert_matcher()
                        except ImportError as e:
                            logger.warning(f"Matcher non rafraîchi : {e}")
                        # « ✅ nettoyé » s'affichait même quand le script sortait
                        # en erreur : le rapport à l'écran décrivait alors une
                        # réécriture qui n'avait pas eu lieu.
                        self._set_progress(
                            f"✅ {source} nettoyé"
                            if code == 0
                            else f"❌ {source} : nettoyage ÉCHOUÉ"
                        )
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
        """Les quatre sources, EN SÉRIE et avec leurs vrais arguments.

        Elles étaient lancées par `_run_script_sync(script)` **sans aucun
        argument** : BRMA et RIAA partaient en mode interactif, BPI affichait son
        aide et sortait en 0 — et la fenêtre annonçait « Toutes les mises à jour
        terminées ! ». En série et non en parallèle : quatre sous-processus
        écrivant leurs CSV en même temps ne se surveillent pas l'un l'autre.
        """

        def update_all():
            bilans: list[str] = []
            for nom in MISES_A_JOUR:
                if stop_requested():
                    bilans.append(f"{nom} : interrompu")
                    break
                try:
                    code, sortie = self._executer_maj(nom)
                    bilans.append(_ligne_bilan(nom, code, sortie))
                except Exception as e:
                    logger.exception(f"Mise à jour globale — {nom}")
                    bilans.append(f"{nom} : ❌ {e}")

            echecs = [b for b in bilans if "❌" in b or "interrompu" in b]
            self._set_progress(
                f"❌ {len(echecs)}/{len(bilans)} source(s) en échec"
                if echecs
                else "Toutes les mises à jour terminées !"
            )
            self.after(3000, lambda: self._set_progress(""))
            self.after(500, self._rafraichir_apres_ecriture)
            rapport = "\n".join(bilans)
            self.after(
                0,
                lambda: (messagebox.showerror if echecs else messagebox.showinfo)(
                    "Mise à jour de toutes les sources", rapport, parent=self
                ),
            )

        self._demarrer("maj-toutes", update_all)

    def _preparer_cdp(self) -> str | None:
        """Lance (ou retrouve) un Chrome de debug et rend son URL CDP."""
        try:
            from src.scrapers.cdp_chrome import ensure_cdp_chrome

            return ensure_cdp_chrome()
        except Exception as e:
            logger.error(f"Préparation CDP échouée : {e}")
            return None

    def _executer_maj(self, nom: str) -> tuple[int, str]:
        """Met à jour UNE source, de façon SYNCHRONE. Rend (code, sortie).

        Le corps est séparé du worker pour que « Tout mettre à jour » puisse
        enchaîner les quatre sources DANS UN SEUL fil : appeler les quatre
        boutons les lancerait en parallèle, donc quatre sous-processus écrivant
        leurs CSV en même temps.
        """
        maj = MISES_A_JOUR[nom]
        script_path = Path(__file__).parent.parent / "utils" / maj.script
        if not script_path.exists():
            raise FileNotFoundError(f"Script non trouvé: {script_path}")

        commande = [sys.executable, str(script_path), *maj.args]
        run_env = None

        if maj.cdp_amont:
            # Le Cloudflare d'ultratop fait boucler tout navigateur lancé par de
            # l'automation, même le vrai Chrome (JOURNAL 2026-06-29) : ici le CDP
            # n'est pas un repli, c'est la seule route qui passe.
            self._set_progress(f"🌐 {nom} : préparation de Chrome (Cloudflare)…")
            cdp_url = self._preparer_cdp()
            if cdp_url:
                run_env = {**os.environ, "GENIUS_CDP_URL": cdp_url}
            else:
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

        self._set_progress(f"Mise à jour {nom} en cours...")
        # Sortie RELAYÉE en direct (console + log du jour), au lieu d'être
        # avalée jusqu'à la fin du processus.
        code, sortie = self._run_streaming(commande, nom, env=run_env)

        if code != 0 and maj.repli_cdp:
            self._set_progress(f"{nom} : échec en headless — seconde tentative via Chrome…")
            logger.warning(
                f"[{nom}] échec en headless, repli sur la route CDP "
                "(si elle réussit, c'était un problème d'accès et non un parseur cassé)"
            )
            cdp_url = self._preparer_cdp()
            if cdp_url:
                code, sortie_cdp = self._run_streaming(
                    commande, f"{nom} (CDP)", env={**os.environ, "GENIUS_CDP_URL": cdp_url}
                )
                sortie = sortie_cdp or sortie
            else:
                logger.error(
                    f"[{nom}] repli CDP impossible : Chrome introuvable "
                    "(installe Google Chrome ou définis CHROME_PATH)"
                )
        return code, sortie

    def _lancer_maj(self, nom: str):
        """Un bouton de mise à jour : le corps ci-dessus, dans un fil, avec dialogue."""

        def run_script():
            try:
                code, sortie = self._executer_maj(nom)
                if code == 0:
                    self._set_progress(f"✅ Mise à jour {nom} réussie")
                    self.after(500, self._rafraichir_apres_ecriture)
                    # Retour visible, DÉBRUITÉ : la console garde le détail.
                    summary = self._resume_humain(sortie) or "Mise à jour terminée."
                    self.after(
                        0,
                        lambda: messagebox.showinfo(f"Mise à jour {nom}", summary, parent=self),
                    )
                else:
                    error_msg = self._resume_humain(sortie, lignes_max=12) or "Erreur inconnue"
                    self._set_progress(f"❌ Erreur {nom}: {error_msg[:50]}...")
                    logger.error(f"Erreur mise à jour {nom}: {error_msg}")
                    self.after(
                        0,
                        lambda: messagebox.showerror(
                            f"Erreur {nom}", error_msg[-600:], parent=self
                        ),
                    )
                self.after(3000, lambda: self._set_progress(""))
            except Exception as e:
                logger.exception(f"Erreur lors de la mise à jour {nom}")
                self._set_progress(f"❌ Erreur: {e}")
                self.after(3000, lambda: self._set_progress(""))

        self._demarrer(f"maj-{nom}", run_script)

    def _set_progress(self, message: str):
        """Met à jour le message de progression (depuis n'importe quel fil)."""
        if self._ferme:
            return

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
        """Cherche les périodes manquantes des quatre sources, EN UN SEUL fil.

        Il en lançait QUATRE d'un coup, qui écrivaient tous dans
        `self.missing_periods` puis réécrivaient la même `CTkTextbox` — depuis
        leur propre fil, qui plus est. Quatre fils Tcl non synchronisés sur le
        même widget.
        """

        def travail():
            for nom, (dossier, brut) in _BRUTS_PAR_SOURCE.items():
                if stop_requested() or self._ferme:
                    return
                self._analyser_trous(nom, dossier, brut)
            # UN seul rendu, sur le fil Tk : `_update_status` touche la textbox.
            self.after(0, self._update_status)

        self._demarrer("trous", travail, "Vérification des périodes manquantes")

    def _analyser_trous(self, source_name: str, folder: str, filename: str) -> None:
        """Analyse le CSV BRUT d'UNE source (accumulation complète, avec sa
        colonne de date native) — distinct de l'audit PAR ARTISTE."""
        try:
            csv_path = Path(DATA_PATH) / "certifications" / folder / filename
            if not csv_path.exists():
                self._set_progress(f"❌ {source_name}: Fichier introuvable")
                return

            self._set_progress(f"🔍 Analyse de {source_name}...")
            self.missing_periods[source_name] = periodes_manquantes(csv_path, source_name)

            gaps = self.missing_periods[source_name]["gaps"]
            self._set_progress(
                f"⚠️ {source_name}: {len(gaps)} période(s) manquante(s) détectée(s)"
                if gaps
                else f"✅ {source_name}: Aucune période manquante"
            )
        except (OSError, ValueError, KeyError) as e:
            logger.exception(f"Vérification {source_name}")
            self._set_progress(f"❌ Erreur vérification {source_name}: {e}")
