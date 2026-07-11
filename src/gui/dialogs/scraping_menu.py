"""Menu combiné Crédits & Paroles (options de scraping Genius/Discogs)"""

from tkinter import messagebox

import customtkinter as ctk

from src.gui.workers import scraping


def show_scraping_menu(app):
    """Affiche le menu de sélection des options de scraping"""
    if not app.current_artist or not app.current_artist.tracks:
        messagebox.showwarning("Attention", "Aucun artiste ou morceaux chargés")
        return

    if not app.selected_tracks:
        messagebox.showwarning("Attention", "Aucun morceau sélectionné")
        return

    # Créer la fenêtre popup
    dialog = ctk.CTkToplevel(app.root)
    dialog.title("Crédits & Paroles")
    dialog.geometry("520x760")

    ctk.CTkLabel(
        dialog, text="Sélectionnez les données à scraper:", font=("Arial", 14, "bold")
    ).pack(pady=15)

    # Frame principal pour les options
    options_frame = ctk.CTkFrame(dialog)
    options_frame.pack(fill="both", expand=True, padx=20, pady=10)

    # Variables pour les checkboxes
    scrape_genius_var = ctk.BooleanVar(value=True)  # Genius coché par défaut
    scrape_discogs_var = ctk.BooleanVar(value=True)  # Discogs coché par défaut
    force_credits_var = ctk.BooleanVar(value=False)
    # Sources paroles (TEXTE) — uniformisé comme les crédits
    lyrics_ytm_var = ctk.BooleanVar(value=True)  # YouTube Music (texte fallback) par défaut
    lyrics_genius_var = ctk.BooleanVar(value=True)  # Genius (scrape, fallback) par défaut
    force_lyrics_var = ctk.BooleanVar(value=False)
    # Sources TIMESTAMPS (paroles synchronisées) — section dédiée
    sync_lrclib_var = ctk.BooleanVar(value=True)  # SOURCE 1 : LRCLIB
    sync_ytm_var = ctk.BooleanVar(value=True)  # SOURCE 2 : YouTube Music
    sync_musixmatch_var = ctk.BooleanVar(
        value=False
    )  # SOURCE 3 : Musixmatch (dernier recours, opt-in)
    force_sync_var = ctk.BooleanVar(value=False)

    # Section Crédits
    credits_frame = ctk.CTkFrame(options_frame)
    credits_frame.pack(fill="x", padx=15, pady=10)

    # Titre de section (non cliquable)
    ctk.CTkLabel(
        credits_frame, text="🎵 Scraper les crédits musicaux", font=("Arial", 13, "bold")
    ).pack(anchor="w", padx=10, pady=5)

    # Checkbox Genius
    genius_checkbox = ctk.CTkCheckBox(
        credits_frame,
        text="   Genius (crédits détaillés)",
        variable=scrape_genius_var,
        font=("Arial", 11),
    )
    genius_checkbox.pack(anchor="w", padx=30, pady=2)

    # Checkbox Discogs
    discogs_checkbox = ctk.CTkCheckBox(
        credits_frame,
        text="   Discogs (crédits complémentaires)",
        variable=scrape_discogs_var,
        font=("Arial", 11),
    )
    discogs_checkbox.pack(anchor="w", padx=30, pady=2)

    # Checkbox Mise à jour forcée
    force_credits_checkbox = ctk.CTkCheckBox(
        credits_frame,
        text="   🔄 Mise à jour forcée (re-scraper les crédits existants)",
        variable=force_credits_var,
        font=("Arial", 11),
    )
    force_credits_checkbox.pack(anchor="w", padx=30, pady=5)

    # Séparateur
    ctk.CTkFrame(options_frame, height=2, fg_color="gray").pack(fill="x", padx=20, pady=10)

    # Section Paroles (uniformisée : titre + sources, comme les crédits)
    lyrics_frame = ctk.CTkFrame(options_frame)
    lyrics_frame.pack(fill="x", padx=15, pady=10)

    ctk.CTkLabel(lyrics_frame, text="📝 Récupérer les paroles", font=("Arial", 13, "bold")).pack(
        anchor="w", padx=10, pady=5
    )

    # Source YouTube Music (texte fallback)
    lyrics_ytm_checkbox = ctk.CTkCheckBox(
        lyrics_frame,
        text="   YouTube Music (texte, fallback)",
        variable=lyrics_ytm_var,
        font=("Arial", 11),
    )
    lyrics_ytm_checkbox.pack(anchor="w", padx=30, pady=2)

    # Source Genius (scrape, fallback)
    lyrics_genius_checkbox = ctk.CTkCheckBox(
        lyrics_frame,
        text="   Genius (scrape, fallback)",
        variable=lyrics_genius_var,
        font=("Arial", 11),
    )
    lyrics_genius_checkbox.pack(anchor="w", padx=30, pady=2)

    force_lyrics_checkbox = ctk.CTkCheckBox(
        lyrics_frame,
        text="   🔄 Mise à jour forcée (re-récupérer les paroles existantes)",
        variable=force_lyrics_var,
        font=("Arial", 11),
    )
    force_lyrics_checkbox.pack(anchor="w", padx=30, pady=5)

    ctk.CTkLabel(
        lyrics_frame,
        text="Texte : Genius (structuré) en priorité ; YTM ne remplit que les manquants.",
        font=("Arial", 9),
        text_color="gray",
    ).pack(anchor="w", padx=30, pady=(0, 5))

    # Séparateur
    ctk.CTkFrame(options_frame, height=2, fg_color="gray").pack(fill="x", padx=20, pady=10)

    # Section Timestamps (paroles synchronisées) — sources dédiées
    sync_frame = ctk.CTkFrame(options_frame)
    sync_frame.pack(fill="x", padx=15, pady=10)

    ctk.CTkLabel(
        sync_frame, text="⏱ Récupérer les timestamps (synchro)", font=("Arial", 13, "bold")
    ).pack(anchor="w", padx=10, pady=5)

    # SOURCE 1 : LRCLIB
    sync_lrclib_checkbox = ctk.CTkCheckBox(
        sync_frame, text="   LRCLIB (source 1)", variable=sync_lrclib_var, font=("Arial", 11)
    )
    sync_lrclib_checkbox.pack(anchor="w", padx=30, pady=2)

    # SOURCE 2 : YouTube Music
    sync_ytm_checkbox = ctk.CTkCheckBox(
        sync_frame, text="   YouTube Music (source 2)", variable=sync_ytm_var, font=("Arial", 11)
    )
    sync_ytm_checkbox.pack(anchor="w", padx=30, pady=2)

    # SOURCE 3 : Musixmatch (dernier recours)
    sync_musixmatch_checkbox = ctk.CTkCheckBox(
        sync_frame,
        text="   Musixmatch (source 3, dernier recours)",
        variable=sync_musixmatch_var,
        font=("Arial", 11),
    )
    sync_musixmatch_checkbox.pack(anchor="w", padx=30, pady=2)

    force_sync_checkbox = ctk.CTkCheckBox(
        sync_frame,
        text="   🔄 Mise à jour forcée (re-récupérer les timestamps existants)",
        variable=force_sync_var,
        font=("Arial", 11),
    )
    force_sync_checkbox.pack(anchor="w", padx=30, pady=5)

    ctk.CTkLabel(
        sync_frame,
        text="Cross-check + départage par la durée. Musixmatch n'est appelé que si LRCLIB\net YTM échouent (API privée, fragile — cf. MUSIXMATCH_ENABLED).",
        font=("Arial", 9),
        text_color="gray",
        justify="left",
    ).pack(anchor="w", padx=30, pady=(0, 5))

    # Frame pour les boutons
    button_frame = ctk.CTkFrame(dialog)
    button_frame.pack(fill="x", padx=20, pady=15)

    def start_scraping():
        scrape_genius = scrape_genius_var.get()
        scrape_discogs = scrape_discogs_var.get()
        force_credits = force_credits_var.get()
        lyrics_ytm = lyrics_ytm_var.get()
        lyrics_genius = lyrics_genius_var.get()
        scrape_lyrics = lyrics_ytm or lyrics_genius
        force_lyrics = force_lyrics_var.get()
        # Sources timestamps (synchro)
        sync_lrclib = sync_lrclib_var.get()
        sync_ytm = sync_ytm_var.get()
        sync_musixmatch = sync_musixmatch_var.get()
        scrape_sync = sync_lrclib or sync_ytm or sync_musixmatch
        force_sync = force_sync_var.get()

        # Au moins une source (crédits, paroles ou timestamps) doit être sélectionnée
        if not scrape_genius and not scrape_discogs and not scrape_lyrics and not scrape_sync:
            messagebox.showwarning("Attention", "Sélectionnez au moins une option de scraping")
            return

        dialog.destroy()

        # Lancer le scraping avec les options sélectionnées
        scraping.start_combined_scraping(
            app,
            scrape_genius=scrape_genius,
            scrape_discogs=scrape_discogs,
            force_credits=force_credits,
            scrape_lyrics=scrape_lyrics,
            lyrics_ytm=lyrics_ytm,
            lyrics_genius=lyrics_genius,
            force_lyrics=force_lyrics,
            scrape_sync=scrape_sync,
            sync_lrclib=sync_lrclib,
            sync_ytm=sync_ytm,
            sync_musixmatch=sync_musixmatch,
            force_sync=force_sync,
        )

    ctk.CTkButton(
        button_frame,
        text="🚀 Lancer le scraping",
        command=start_scraping,
        width=200,
        height=40,
        font=("Arial", 13, "bold"),
        fg_color="green",
        hover_color="darkgreen",
    ).pack(side="left", padx=5)

    ctk.CTkButton(button_frame, text="Annuler", command=dialog.destroy, width=100, height=40).pack(
        side="left", padx=5
    )

    # Centrer la fenêtre
    dialog.transient(app.root)
    dialog.grab_set()
