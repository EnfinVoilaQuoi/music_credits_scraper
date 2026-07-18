"""Fenêtre « Export studio » — générateurs de visuels par produit final.

Trois onglets : **Analyse de Projet** (7 générateurs prévus, « Bubble Prod » et
« Bubble Feat » sont câblés), **Timeline** et **Stats en Vrac** (à venir).
L'export JSON historique (bouton « Exporter » d'origine) vit désormais ici, en
bas de fenêtre, et délègue à `app._export_data()` (inchangé).

Couche mince : tout le moteur est dans `src.dataviz` (pilotable aussi via
`scripts/bubble_prod.py`). Threads via `start_worker` (contrat lifecycle.py) ;
les générations sont des unités courtes (pas de boucle `stop_requested`
nécessaire), le retour GUI passe par `_safe_after`.
"""

import os
from tkinter import messagebox

import customtkinter as ctk

from src.dataviz.bubble_feat import generate_bubble_feat, generate_feat_preview_grid
from src.dataviz.bubble_prod import (
    PREVIEW_SEEDS,
    generate_bubble_prod,
    generate_preview_grid,
    list_albums,
)
from src.gui.dialogs import report
from src.gui.workers.lifecycle import start_worker, stop_requested
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Générateurs de l'onglet « Analyse de Projet » : (libellé, actif).
_PROJECT_GENERATORS = [
    ("Bubble Prod", True),
    ("Bubble Feat", True),
    ("Répartition BPM", False),
    ("Clés & modes", False),
    ("Durées", False),
    ("Certifications", False),
    ("Streams", False),
]

# Configuration par générateur de bulles : (fonction SVG, fonction aperçus,
# libellé du compteur dans le statut). Même moteur, autre filtre de rôles.
_BUBBLE_KINDS = {
    "prod": (generate_bubble_prod, generate_preview_grid, "producteur(s)", "Bubble Prod"),
    "feat": (generate_bubble_feat, generate_feat_preview_grid, "featuring(s)", "Bubble Feat"),
}


class ExportStudioWindow:
    """Fenêtre CTkToplevel « Export studio » (une seule instance à la fois)."""

    def __init__(self, app):
        self.app = app
        self._stop = False  # drapeau d'arrêt LOCAL (fermeture de cette fenêtre)

        self.window = ctk.CTkToplevel(app.root)
        self.window.title("Export studio")
        self.window.geometry("640x560")
        self.window.transient(app.root)
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_tabs()
        self._build_footer()
        self.refresh_albums()

    # ── Construction ───────────────────────────────────────────────────────────
    def _build_tabs(self):
        self.tabview = ctk.CTkTabview(self.window)
        self.tabview.pack(fill="both", expand=True, padx=10, pady=(10, 5))

        tab_project = self.tabview.add("Analyse de Projet")
        tab_timeline = self.tabview.add("Timeline")
        tab_stats = self.tabview.add("Stats en Vrac")

        for tab in (tab_timeline, tab_stats):
            ctk.CTkLabel(tab, text="À venir…", text_color="gray", font=("Arial", 14)).pack(
                expand=True, pady=40
            )

        # ── Onglet Analyse de Projet ──
        album_row = ctk.CTkFrame(tab_project, fg_color="transparent")
        album_row.pack(fill="x", padx=10, pady=(10, 4))
        ctk.CTkLabel(album_row, text="Album :", font=("Arial", 13, "bold")).pack(
            side="left", padx=(0, 8)
        )
        self.album_var = ctk.StringVar(value="")
        self.album_menu = ctk.CTkOptionMenu(
            album_row, variable=self.album_var, values=[""], width=380
        )
        self.album_menu.pack(side="left", fill="x", expand=True)

        # Variante (seed) + aperçus : le rendu change avec le seed, la grille
        # 2×2 permet de choisir celle qui remplit le mieux pour CET album.
        seed_row = ctk.CTkFrame(tab_project, fg_color="transparent")
        seed_row.pack(fill="x", padx=10, pady=4)
        ctk.CTkLabel(seed_row, text="Variante :", font=("Arial", 13, "bold")).pack(
            side="left", padx=(0, 8)
        )
        self.seed_var = ctk.StringVar(value=str(PREVIEW_SEEDS[0]))
        self.seed_menu = ctk.CTkOptionMenu(
            seed_row, variable=self.seed_var, values=[str(s) for s in PREVIEW_SEEDS], width=90
        )
        self.seed_menu.pack(side="left")
        # Packés côté droit : le premier posé est le plus à droite → ordre
        # visuel [Prod][Feat], aligné sur la grille des générateurs.
        self.preview_feat_button = ctk.CTkButton(
            seed_row,
            text="Aperçus Feat (4)…",
            width=140,
            command=lambda: self._start_preview_grid("feat"),
        )
        self.preview_feat_button.pack(side="right", padx=(6, 0))
        self.preview_prod_button = ctk.CTkButton(
            seed_row,
            text="Aperçus Prod (4)…",
            width=140,
            command=lambda: self._start_preview_grid("prod"),
        )
        self.preview_prod_button.pack(side="right")

        grid = ctk.CTkFrame(tab_project)
        grid.pack(fill="both", expand=True, padx=10, pady=6)
        grid.grid_columnconfigure(0, weight=1)
        grid.grid_columnconfigure(1, weight=1)

        commands = {
            "Bubble Prod": lambda: self._start_bubble("prod"),
            "Bubble Feat": lambda: self._start_bubble("feat"),
        }
        self.generator_buttons: dict[str, ctk.CTkButton] = {}
        for i, (label, enabled) in enumerate(_PROJECT_GENERATORS):
            button = ctk.CTkButton(
                grid,
                text=label,
                state="normal" if enabled else "disabled",
                command=commands.get(label),
            )
            button.grid(row=i // 2, column=i % 2, sticky="ew", padx=8, pady=6)
            self.generator_buttons[label] = button

        self.open_after_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            tab_project, text="Ouvrir le SVG après génération", variable=self.open_after_var
        ).pack(anchor="w", padx=12, pady=(2, 4))

        self.status_label = ctk.CTkLabel(tab_project, text="", text_color="gray", anchor="w")
        self.status_label.pack(fill="x", padx=12, pady=(0, 8))

    def _build_footer(self):
        footer = ctk.CTkFrame(self.window, fg_color="transparent")
        footer.pack(fill="x", padx=10, pady=(0, 10))
        # Export JSON packé en premier → le plus à droite ; « Télécharger les
        # images… » packé ensuite → à sa gauche.
        ctk.CTkButton(
            footer,
            text="Export JSON (données)…",
            width=200,
            command=self.app._export_data,
        ).pack(side="right")
        self.media_button = ctk.CTkButton(
            footer,
            text="Télécharger les images…",
            width=200,
            command=self._start_media,
        )
        self.media_button.pack(side="right", padx=(0, 8))

    # ── Données ────────────────────────────────────────────────────────────────
    def refresh_albums(self):
        """(Re)peuple la liste d'albums depuis l'artiste courant (appelé au refocus)."""
        artist = getattr(self.app, "current_artist", None)
        if artist is None or not artist.tracks:
            self.album_menu.configure(values=[""])
            self.album_var.set("")
            self.status_label.configure(text="Aucun artiste chargé")
            return
        albums = list_albums(artist.tracks)
        if not albums:
            self.album_menu.configure(values=[""])
            self.album_var.set("")
            self.status_label.configure(text="Aucun album détecté pour cet artiste")
            return
        self.album_menu.configure(values=albums)
        if self.album_var.get() not in albums:
            self.album_var.set(albums[0])
        self.status_label.configure(text=f"{len(albums)} album(s) — {artist.name}")

    def _snapshot_inputs(self):
        """Snapshot des entrées SUR LE THREAD TK (jamais depuis le worker).

        Reprend le filtre de `_export_data` : les morceaux désactivés sont exclus.
        Renvoie `(artist_name, album, tracks, seed)` ou `None` si rien à faire.
        """
        artist = getattr(self.app, "current_artist", None)
        album = self.album_var.get().strip()
        if artist is None or not album:
            messagebox.showinfo("Export studio", "Chargez un artiste et choisissez un album.")
            return None
        tracks = [t for t in artist.tracks if not self.app._is_track_disabled(t)]
        try:
            seed = int(self.seed_var.get())
        except ValueError:
            seed = PREVIEW_SEEDS[0]
        return artist.name, album, tracks, seed

    # ── Génération (threads) ───────────────────────────────────────────────────
    def _set_busy(self, busy: bool, message: str = ""):
        state = "disabled" if busy else "normal"
        for label, enabled in _PROJECT_GENERATORS:
            if enabled:
                self.generator_buttons[label].configure(state=state)
        self.preview_prod_button.configure(state=state)
        self.preview_feat_button.configure(state=state)
        self.status_label.configure(text=message)

    def _start_bubble(self, kind: str):
        """Génère le SVG du générateur `kind` (« prod » ou « feat »)."""
        snapshot = self._snapshot_inputs()
        if snapshot is None:
            return
        artist_name, album, tracks, seed = snapshot
        open_after = self.open_after_var.get()
        generate, _, noun, title = _BUBBLE_KINDS[kind]
        self._set_busy(True, f"Génération {title} ({album})…")

        def worker():
            # NB : `exc` est effacé à la sortie du bloc except → le message est
            # FIGÉ en argument par défaut de la lambda (jamais capturé tel quel).
            try:
                result = generate(tracks, album, artist_name=artist_name, seed=seed)
            except ValueError as exc:  # album sans crédit correspondant, etc.
                self._safe_after(lambda msg=str(exc): self._on_error(msg))
            except Exception as exc:  # frontière thread→GUI : tout remonte en dialog
                logger.error(f"{title} : erreur inattendue : {exc}")
                self._safe_after(lambda msg=str(exc): self._on_error(msg))
            else:
                self._safe_after(lambda: self._on_bubble_done(result, open_after, noun))

        start_worker(worker, name=f"export_studio:bubble_{kind}")

    def _on_bubble_done(self, result, open_after: bool, noun: str):
        self._set_busy(
            False,
            f"✅ {result.path.name} — {result.node_count} {noun}, "
            f"{result.track_count} morceau(x)",
        )
        if open_after:
            try:
                os.startfile(result.path)  # noqa: S606 — ouverture du SVG généré
            except OSError as exc:
                logger.warning(f"Ouverture du SVG impossible : {exc}")

    def _start_preview_grid(self, kind: str):
        snapshot = self._snapshot_inputs()
        if snapshot is None:
            return
        artist_name, album, tracks, _ = snapshot
        _, generate_grid, _, title = _BUBBLE_KINDS[kind]
        self._set_busy(True, f"Génération des {len(PREVIEW_SEEDS)} aperçus {title} ({album})…")

        def worker():
            try:
                html_path = generate_grid(tracks, album, artist_name=artist_name)
            except ValueError as exc:
                self._safe_after(lambda msg=str(exc): self._on_error(msg))
            except Exception as exc:  # frontière thread→GUI : tout remonte en dialog
                logger.error(f"Aperçus {title} : erreur inattendue : {exc}")
                self._safe_after(lambda msg=str(exc): self._on_error(msg))
            else:
                self._safe_after(lambda: self._on_preview_done(html_path))

        start_worker(worker, name=f"export_studio:preview_grid_{kind}")

    def _on_preview_done(self, html_path):
        self._set_busy(False, f"✅ Aperçus : {html_path}")
        try:
            os.startfile(html_path)  # noqa: S606 — grille HTML locale générée
        except OSError as exc:
            logger.warning(f"Ouverture des aperçus impossible : {exc}")

    # ── Téléchargement des images (chantier « Media ») ───────────────────────────
    def _start_media(self):
        """Télécharge photos/covers/vignettes de l'artiste courant, puis sauve."""
        artist = getattr(self.app, "current_artist", None)
        if artist is None or not artist.tracks:
            messagebox.showinfo("Export studio", "Chargez un artiste d'abord.")
            return
        self._stop = False
        self.media_button.configure(state="disabled")
        self.status_label.configure(text="Téléchargement des images…")
        tracks = list(artist.tracks)

        def worker():
            try:
                from src.api.deezer_api import DeezerAPI
                from src.utils.media_enricher import apply_images

                def progress(msg):
                    self._safe_after(lambda m=msg: self.status_label.configure(text=m))

                media_report = apply_images(
                    artist,
                    tracks,
                    deezer=DeezerAPI(),
                    genius=getattr(self.app, "genius_api", None),
                    force=False,
                    should_stop=lambda: self._stop or stop_requested(),
                    progress=progress,
                )
                # Persistance : apply_images MUTE mais ne sauve pas (pattern certifs).
                for track in tracks:
                    if self._stop or stop_requested():
                        break
                    try:
                        self.app.data_manager.save_track(track)
                    except Exception as exc:
                        logger.warning(f"Save image track '{track.title}': {exc}")
                if artist.image_path:
                    self.app.data_manager.set_artist_image_path(artist.id, artist.image_path)
            except Exception as exc:  # frontière thread→GUI : tout remonte en dialog
                logger.error(f"Téléchargement des images : erreur inattendue : {exc}")
                self._safe_after(lambda m=str(exc): self._on_media_error(m))
            else:
                self._safe_after(lambda: self._on_media_done(media_report))

        start_worker(worker, name="export_studio:media")

    def _on_media_done(self, media_report):
        try:
            self.media_button.configure(state="normal")
        except Exception:
            pass
        if self._stop:
            self.status_label.configure(text="Téléchargement interrompu")
        else:
            self.status_label.configure(
                text=f"✅ Images : {media_report.total_downloaded()} téléchargée(s)"
            )
        report.show_scrollable_report(self.app, "Téléchargement des images", media_report.summary())
        # Les tracks mutés + sauvés : recharger la vue pour voir les chemins.
        try:
            self.app._reload_tracks_and_refresh()
        except Exception:
            pass

    def _on_media_error(self, message: str):
        try:
            self.media_button.configure(state="normal")
        except Exception:
            pass
        self.status_label.configure(text="❌ Échec du téléchargement")
        messagebox.showerror("Export studio", message)

    def _on_error(self, message: str):
        self._set_busy(False, "❌ Échec de la génération")
        messagebox.showerror("Export studio", message)

    # ── Divers ─────────────────────────────────────────────────────────────────
    def _safe_after(self, fn):
        """Planifie `fn` sur le thread Tk, en ignorant une fenêtre déjà détruite."""
        try:
            self.window.after(0, fn)
        except Exception:
            pass

    def _on_close(self):
        # Demande l'arrêt coopératif du worker média éventuel (testé entre unités).
        self._stop = True
        self.window.destroy()
        if getattr(self.app, "export_studio_window", None) is self:
            self.app.export_studio_window = None


def show_export_studio(app):
    """Ouvre (ou refocus) la fenêtre Export studio."""
    existing = getattr(app, "export_studio_window", None)
    if existing is not None:
        try:
            existing.window.deiconify()
            existing.window.lift()
            existing.window.focus_force()
            existing.refresh_albums()
            return existing
        except Exception:
            pass  # fenêtre détruite entre-temps → on en recrée une
    app.export_studio_window = ExportStudioWindow(app)
    return app.export_studio_window
