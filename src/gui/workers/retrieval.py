"""Récupération des morceaux de l'artiste (API Genius) en thread"""

from tkinter import messagebox

import customtkinter as ctk

from src.concurrency.lifecycle import run_worker, stop_requested
from src.gui import nouveautes_gui
from src.gui.dialogs import report
from src.observability import source_usage
from src.observability.registry import Flow
from src.services import discographie
from src.services.runtime import Hooks
from src.utils.logger import get_logger

logger = get_logger(__name__)


def get_tracks(app):
    """Récupère les morceaux de l'artiste - VERSION AVEC FEATURES"""
    if not app.current_artist:
        return

    # Inclure les features
    dialog = ctk.CTkToplevel(app.root)
    dialog.title("Options de récupération")

    # Hauteur adaptée à l'écran : 940 px idéal, plafonné à 85 % de l'écran.
    dialog.update_idletasks()
    screen_h = dialog.winfo_screenheight()
    dialog_w = 480
    dialog_h = min(940, int(screen_h * 0.85))
    x = (dialog.winfo_screenwidth() // 2) - (dialog_w // 2)
    y = (screen_h // 2) - (dialog_h // 2)
    dialog.geometry(f"{dialog_w}x{dialog_h}+{x}+{y}")

    dialog.lift()
    dialog.focus_force()
    dialog.grab_set()

    # Variables pour les options
    include_features_var = ctk.BooleanVar(value=True)  # Par défaut, inclure les features
    prefill_var = ctk.BooleanVar(value=True)  # Appel API album + Spotify/YouTube (media)
    include_secondary_var = ctk.BooleanVar(value=False)  # Rôles secondaires (Additional Voices…)
    respect_deleted_var = ctk.BooleanVar(value=True)  # Ne pas réajouter les morceaux supprimés
    download_images_var = ctk.BooleanVar(value=True)  # Télécharger photos/covers/vignettes (Media)
    deezer_var = ctk.BooleanVar(value=True)  # Compléter par Deezer (écarts listés, jamais créés)

    # Interface — le contenu scrolle, les boutons d'action restent en bas.
    ctk.CTkLabel(
        dialog, text="Options de récupération des morceaux", font=("Arial", 16, "bold")
    ).pack(pady=(15, 5))

    # Bandeau des nouveautés : ce que la vérification quotidienne a trouvé.
    # Il vit ICI et pas dans une fenêtre à part — le geste qui suit est
    # justement celui de ce dialogue (décision utilisateur 2026-09-22).
    texte_bandeau = nouveautes_gui.bandeau(app)
    if texte_bandeau:
        bandeau = ctk.CTkFrame(dialog, fg_color="#1b5e20")
        bandeau.pack(fill="x", padx=15, pady=(0, 5))
        ctk.CTkLabel(
            bandeau,
            text=texte_bandeau,
            font=("Arial", 12),
            text_color="white",
            wraplength=430,
            justify="left",
        ).pack(anchor="w", padx=12, pady=8)

    scroll = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
    scroll.pack(fill="both", expand=True, padx=5, pady=5)

    # Checkbox pour les features
    features_frame = ctk.CTkFrame(scroll)
    features_frame.pack(fill="x", padx=15, pady=(10, 5))

    ctk.CTkCheckBox(
        features_frame,
        text="Inclure les morceaux où l'artiste est en featuring",
        variable=include_features_var,
        font=("Arial", 12),
    ).pack(anchor="w", padx=15, pady=12)

    ctk.CTkLabel(
        features_frame,
        text="✓ Recommandé : permet de récupérer plus de morceaux",
        text_color="gray",
        font=("Arial", 10),
    ).pack(anchor="w", padx=15, pady=(0, 8))

    # Checkbox pour l'appel API album + media (Spotify/YouTube/relations)
    prefill_frame = ctk.CTkFrame(scroll)
    prefill_frame.pack(fill="x", padx=15, pady=(0, 5))

    ctk.CTkCheckBox(
        prefill_frame,
        text="Récupérer album + Spotify/YouTube (API media)",
        variable=prefill_var,
        font=("Arial", 12),
    ).pack(anchor="w", padx=15, pady=12)

    ctk.CTkLabel(
        prefill_frame,
        text="⚡ Appel API détail par morceau primaire (album, Spotify ID, lien YouTube, relations).\n"
        "Décocher = liste seule (plus rapide, le scrape rattrapera).",
        text_color="gray",
        font=("Arial", 10),
        justify="left",
    ).pack(anchor="w", padx=15, pady=(0, 8))

    # Checkbox pour les rôles secondaires (Additional Voices, chœurs…)
    secondary_frame = ctk.CTkFrame(scroll)
    secondary_frame.pack(fill="x", padx=15, pady=(0, 5))

    ctk.CTkCheckBox(
        secondary_frame,
        text="Inclure les rôles secondaires (chœurs, Additional Voices…)",
        variable=include_secondary_var,
        font=("Arial", 12),
    ).pack(anchor="w", padx=15, pady=12)

    ctk.CTkLabel(
        secondary_frame,
        text="🎙️ Vérifie chaque candidat au détail (id exact) → garde la vraie contribution\n"
        "avec son rôle, écarte les homonymes. Quelques appels API en plus.",
        text_color="gray",
        font=("Arial", 10),
        justify="left",
    ).pack(anchor="w", padx=15, pady=(0, 8))

    # Checkbox : ne pas réajouter les morceaux supprimés
    deleted_frame = ctk.CTkFrame(scroll)
    deleted_frame.pack(fill="x", padx=15, pady=(0, 5))

    ctk.CTkCheckBox(
        deleted_frame,
        text="Ne pas réajouter les morceaux supprimés",
        variable=respect_deleted_var,
        font=("Arial", 12),
    ).pack(anchor="w", padx=15, pady=12)

    _deleted_count = 0
    try:
        if app.current_artist:
            _deleted_count = len(
                app.deleted_tracks_manager.load_deleted_ids(app.current_artist.name)
            )
    except Exception:
        _deleted_count = 0
    ctk.CTkLabel(
        deleted_frame,
        text=f"🗂️ Respecte l'historique des suppressions ({_deleted_count} morceau(x) mémorisé(s)).\n"
        "Décocher = autorise leur retour à cet import.",
        text_color="gray",
        font=("Arial", 10),
        justify="left",
    ).pack(anchor="w", padx=15, pady=(0, 8))

    # Checkbox : télécharger les images (chantier « Media »)
    images_frame = ctk.CTkFrame(scroll)
    images_frame.pack(fill="x", padx=15, pady=(0, 5))

    ctk.CTkCheckBox(
        images_frame,
        text="Télécharger les images (photos, covers, vignettes)",
        variable=download_images_var,
        font=("Arial", 12),
    ).pack(anchor="w", padx=15, pady=12)

    ctk.CTkLabel(
        images_frame,
        text="🖼️ Photos d'artistes, pochettes et vignettes YouTube → data/images/.\n"
        "Idempotent (quasi-gratuit au 2ᵉ passage). Décocher = plus rapide.",
        text_color="gray",
        font=("Arial", 10),
        justify="left",
    ).pack(anchor="w", padx=15, pady=(0, 8))

    # Checkbox : compléter par Deezer (2026-09-21) + bouton « Écarts Deezer » seul
    deezer_frame = ctk.CTkFrame(scroll)
    deezer_frame.pack(fill="x", padx=15, pady=(0, 5))
    ctk.CTkCheckBox(
        deezer_frame,
        text="Compléter par Deezer en fin de run",
        variable=deezer_var,
        font=("Arial", 12),
    ).pack(anchor="w", padx=15, pady=(12, 4))
    ctk.CTkLabel(
        deezer_frame,
        text="🎧 Ce que le catalogue des distributeurs publie et que Genius n'a pas (trous,\n"
        "versions, apparitions) : LISTÉ pour validation, jamais créé par le run.",
        text_color="gray",
        font=("Arial", 10),
        justify="left",
    ).pack(anchor="w", padx=15, pady=(0, 4))

    def _ecarts_seuls() -> None:
        from src.gui.windows.ecarts_deezer import show_ecarts_deezer

        dialog.destroy()
        show_ecarts_deezer(app)

    ctk.CTkButton(
        deezer_frame,
        text="🎧 Écarts Deezer seuls (sans refaire Genius)",
        command=_ecarts_seuls,
        fg_color="gray30",
        hover_color="gray20",
        state="normal" if discographie_chargee(app) else "disabled",
    ).pack(anchor="w", padx=15, pady=(0, 12))

    # Nombre maximum de morceaux
    max_songs_frame = ctk.CTkFrame(scroll)
    max_songs_frame.pack(fill="x", padx=15, pady=(10, 5))

    ctk.CTkLabel(
        max_songs_frame, text="Nombre maximum de morceaux (debug):", font=("Arial", 12)
    ).pack(anchor="w", padx=15, pady=(12, 5))
    ctk.CTkLabel(
        max_songs_frame,
        text="✅ Vide = ILLIMITÉ (défaut). Des morceaux manquants = données\n"
        "manquantes (Kanye West déborde 2000). Ne renseigner une valeur\n"
        "que pour DEBUG : c'est un plafond DUR qui coupe le plus VIEUX.",
        text_color="gray",
        font=("Arial", 10),
        justify="left",
    ).pack(anchor="w", padx=15, pady=(0, 4))

    max_songs_entry = ctk.CTkEntry(max_songs_frame, width=100, placeholder_text="illimité")
    max_songs_entry.pack(anchor="w", padx=15, pady=(0, 12))

    # Info supplémentaire
    info_frame = ctk.CTkFrame(scroll)
    info_frame.pack(fill="x", padx=15, pady=(10, 5))

    info_text = """ℹ️ Les morceaux en featuring seront marqués avec 🎤
⚡ L'album et la date seront récupérés automatiquement via l'API
🔍 Le scraping ne sera utilisé que pour les crédits détaillés"""

    ctk.CTkLabel(
        info_frame, text=info_text, font=("Arial", 9), text_color="gray", justify="left"
    ).pack(anchor="w", padx=15, pady=10)

    # Boutons — HORS du scroll, toujours visibles en bas de la fenêtre.
    button_frame = ctk.CTkFrame(dialog)
    button_frame.pack(fill="x", padx=20, pady=(10, 15))

    def start_retrieval(update_only: bool = False):
        # Vide / invalide / ≤ 0 = illimité (None) : le plafond ne sert qu'au debug.
        texte_max = max_songs_entry.get().strip()
        max_songs: int | None
        try:
            max_songs = int(texte_max)
            if max_songs <= 0:
                max_songs = None
        except ValueError:
            max_songs = None

        include_features = include_features_var.get()
        prefill = prefill_var.get()
        include_secondary = include_secondary_var.get()
        respect_deleted = respect_deleted_var.get()
        download_images = download_images_var.get()
        deezer = deezer_var.get()
        dialog.destroy()
        start_track_retrieval(
            app,
            max_songs,
            include_features,
            prefill=prefill,
            update_only=update_only,
            include_secondary=include_secondary,
            respect_deleted=respect_deleted,
            download_images=download_images,
            deezer=deezer,
        )

    def cancel():
        dialog.destroy()

    ctk.CTkButton(
        button_frame,
        text="🎵 Récupérer",
        command=lambda: start_retrieval(update_only=False),
        width=120,
        height=35,
    ).pack(side="left", padx=6)
    ctk.CTkButton(
        button_frame,
        text="🔄 Mettre à jour",
        command=lambda: start_retrieval(update_only=True),
        fg_color="#2A8C4A",
        hover_color="#23733D",
        width=130,
        height=35,
    ).pack(side="left", padx=6)
    ctk.CTkButton(button_frame, text="❌ Annuler", command=cancel, width=90, height=35).pack(
        side="right", padx=6
    )


def discographie_chargee(app) -> list:
    """Les morceaux de l'artiste courant tels que la GUI les tient : sur
    `app.current_artist.tracks` — `app.tracks` n'est rempli que par le worker
    de récupération (bouton « Écarts Deezer » grisé à tort, 2026-09-21)."""
    artist = getattr(app, "current_artist", None)
    return list(getattr(artist, "tracks", None) or []) if artist is not None else []


def start_track_retrieval(
    app,
    max_songs: int | None,
    include_features: bool,
    prefill: bool = True,
    update_only: bool = False,
    include_secondary: bool = False,
    respect_deleted: bool = True,
    download_images: bool = True,
    deezer: bool = True,
):
    """Lance la récupération des morceaux avec les options choisies.

    Adaptateur GUI : la logique (dédup, fusion, certifs, images, sauvegarde)
    vit dans `src/services/discographie.run`, partagée avec la CLI. Ici : le
    worker, la barre de progression, le compte rendu et les messages d'erreur.
    """
    options = discographie.OptionsDisco(
        max_songs=max_songs,
        include_features=include_features,
        prefill=prefill,
        update_only=update_only,
        include_secondary=include_secondary,
        respect_deleted=respect_deleted,
        download_images=download_images,
        deezer=deezer,
    )
    app.get_tracks_button.configure(state="disabled", text="Récupération...")

    features_text = "avec features" if include_features else "sans features"
    mode_text = "MàJ" if update_only else "complet"
    limite_text = "illimité" if max_songs is None else f"max {max_songs}"
    app.progress_label.configure(
        text=f"Récupération {mode_text} de {limite_text} morceaux ({features_text})..."
    )
    artist = app.current_artist

    def progress(courant, total, libelle, tache=""):
        app.root.after(
            0,
            lambda: app.progress_label.configure(
                text=f"{tache} {courant}/{total} - {libelle[:25]}..."
            ),
        )

    def confirmer_ecarts(bilan_ecarts):
        # Écarts Deezer (ou identité ambiguë) : la fenêtre s'ouvre en fin de
        # run, cases pré-cochées — rien n'est créé sans clic.
        from src.gui.windows.ecarts_deezer import show_ecarts_deezer

        app.root.after(0, lambda b=bilan_ecarts: show_ecarts_deezer(app, b))

    def get_tracks():
        try:
            bilan = discographie.run(
                app.runtime,
                artist,
                options,
                Hooks(
                    progress=progress, should_stop=stop_requested, confirmer_ecarts=confirmer_ecarts
                ),
            )
            app.tracks = artist.tracks
            texte = discographie.resume(bilan, artist)
            if bilan.recuperes == 0:
                app.root.after(0, lambda: messagebox.showwarning("Attention", texte))
                return
            app.root.after(0, app._update_artist_info)
            app.root.after(
                0, lambda: report.show_scrollable_report(app, "Récupération terminée", texte)
            )
        except Exception as e:
            error_msg = str(e) if str(e) else "Erreur inconnue lors de la récupération"
            logger.error(f"Erreur lors de la récupération des morceaux: {error_msg}")
            app.root.after(
                0,
                lambda: messagebox.showerror(
                    "Erreur", f"Erreur lors de la récupération:\n{error_msg}"
                ),
            )
        finally:
            # Le badge repart de la BASE, qui vient de changer : re-vérifier
            # (force) plutôt que de le remettre à zéro d'autorité — un titre
            # peut être resté absent, et le dire est plus utile que se taire.
            app.root.after(0, lambda: app.get_tracks_button.configure(state="normal"))
            app.root.after(0, lambda: nouveautes_gui.verifier_en_fond(app, force=True))
            app.root.after(0, lambda: app.progress_label.configure(text=""))

    def get_tracks_observe():
        """Le scope nomme l'artiste et le flux : sans lui, l'usage des sources
        serait compté sans savoir POUR QUI (`artist_id` à NULL)."""
        with source_usage.run_scope(Flow.DISCO, artist_id=artist.id, artist_name=artist.name):
            return get_tracks()

    run_worker(get_tracks_observe, name="retrieval")
