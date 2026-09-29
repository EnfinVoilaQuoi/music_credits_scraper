"""Étape « Identité » — adaptateur GUI de `src/services/identite.py`.

Le service est SYNC (il attend lui-même les coroutines de la boucle unique et
le pipeline d'enrichissement) : il tourne sur un fil `run_worker`, jamais dans
la boucle. Ce module ne fait que des widgets : dialogue, progression par
`root.after`, compte rendu. Une identité Deezer AMBIGUË ouvre la fenêtre des
écarts (seule à savoir choisir l'artiste) ; le reste va au panneau « À
trancher », comme depuis le run discographie.
"""

from tkinter import messagebox

import customtkinter as ctk

from src.concurrency.lifecycle import stop_requested
from src.gui.dialogs import report
from src.gui.workers.lifecycle import run_worker
from src.observability import source_usage
from src.observability.registry import Flow
from src.services import identite
from src.services.runtime import Hooks
from src.utils.logger import get_logger

logger = get_logger(__name__)

#: (champ d'`OptionsIdentite`, libellé, aide) — une case par couche.
_COUCHES = (
    (
        "deezer",
        "Deezer : artiste, catalogue, morceaux 🎶",
        "Liens prouvés écrits d'office ; le reste va au panneau « À trancher ».",
    ),
    (
        "musicbrainz",
        "MusicBrainz + Discogs : formations et alias 🪪",
        "Rien n'est confirmé : les liens sont PROPOSÉS, à arbitrer dans « Groupes ».",
    ),
    (
        "par_morceau",
        "IDs des morceaux non reliés (Deezer puis Spotify) 🔎",
        "Deezer d'abord : l'ID Spotify est jugé avec une durée indépendante.",
    ),
    (
        "spotify_artiste",
        "ID Spotify de l'artiste 🟢",
        "Vote sur les morceaux non-feat ; un ID déjà mémorisé n'est pas remplacé.",
    ),
    (
        "kworb",
        "Kworb : catalogue Spotify de l'artiste 🔗",
        "Les ID Spotify que liste sa page Kworb (même gate d'identité) ; avant le scraper.",
    ),
    (
        "apple",
        "Apple Music : ID proposés par Genius 🍎",
        "Vérifiés par iTunes (artiste + titre) ; un ID vérifié apporte sa durée.",
    ),
    (
        "nature_disques",
        "Nature des disques (EP / album / single) 💿",
        "Catalogue Deezer d'abord ; une saisie manuelle n'est jamais écrasée.",
    ),
)


def start_identite(app):
    """Dialogue de l'étape : une case par couche, « Forcer », écarts Deezer."""
    if not app.current_artist:
        return
    dialog = ctk.CTkToplevel(app.root)
    dialog.title("Identité — relier les fiches aux plateformes")
    dialog.geometry("520x610")
    dialog.transient(app.root)
    dialog.grab_set()

    ctk.CTkLabel(dialog, text="Couches à lancer :", font=("Arial", 14)).pack(pady=10)
    cases = {}
    for champ, libelle, aide in _COUCHES:
        cadre = ctk.CTkFrame(dialog)
        cadre.pack(fill="x", padx=20, pady=4)
        cases[champ] = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(cadre, text=libelle, variable=cases[champ]).pack(anchor="w")
        ctk.CTkLabel(cadre, text=aide, font=("Arial", 9), text_color="gray").pack(
            anchor="w", padx=25
        )

    ctk.CTkFrame(dialog, height=2, fg_color="gray").pack(fill="x", padx=20, pady=10)
    force_var = ctk.BooleanVar(value=False)
    ctk.CTkCheckBox(
        dialog, text="🔄 Redemander aussi ce qui est déjà relié ou tranché", variable=force_var
    ).pack(anchor="w", padx=20)
    ctk.CTkLabel(
        dialog,
        text="Redemander ne remplace rien : un identifiant en place le reste.",
        font=("Arial", 9),
        text_color="gray",
    ).pack(anchor="w", padx=45)

    def _ecarts():
        from src.gui.windows.ecarts_deezer import show_ecarts_deezer

        dialog.destroy()
        show_ecarts_deezer(app)

    ctk.CTkButton(
        dialog,
        text="🎧 Écarts Deezer (fenêtre détaillée)",
        command=_ecarts,
        fg_color="gray30",
        hover_color="gray40",
    ).pack(anchor="w", padx=20, pady=(10, 0))

    def _demarrer():
        options = identite.OptionsIdentite(
            **{champ: var.get() for champ, var in cases.items()}, force=force_var.get()
        )
        dialog.destroy()
        run_identite(app, options)

    ctk.CTkButton(dialog, text="Démarrer", command=_demarrer).pack(pady=15)


def run_identite(app, options: identite.OptionsIdentite):
    artist = app.current_artist
    app.identite_button.configure(state="disabled", text="Identité...")

    def progress(courant, total, libelle, tache=""):
        texte = f"{tache} {courant}/{total} - {libelle[:30]}" if total > 1 else f"{tache} {libelle}"
        app.root.after(0, lambda: app.progress_label.configure(text=texte))

    def confirmer_ecarts(bilan_ecarts):
        # Même règle que le run discographie (2026-09-27) : seule une identité
        # AMBIGUË ouvre la fenêtre, les écarts vont au panneau « À trancher ».
        if not bilan_ecarts.ambigu:
            return
        from src.gui.windows.ecarts_deezer import show_ecarts_deezer

        app.root.after(0, lambda b=bilan_ecarts: show_ecarts_deezer(app, b))

    def corps():
        try:
            artist.tracks = app.runtime.data_manager.discographie_reunie(artist)
            bilan = identite.run(
                app.runtime,
                artist,
                options,
                Hooks(
                    progress=progress, should_stop=stop_requested, confirmer_ecarts=confirmer_ecarts
                ),
            )
            texte = identite.resume(bilan, artist)
            app.root.after(0, app._update_artist_info)
            app.root.after(0, lambda: report.show_scrollable_report(app, "Identité", texte))
        except Exception as e:  # dernier ressort : trace complète, erreur affichée
            logger.exception("Étape Identité en échec")
            message = str(e) or type(e).__name__
            app.root.after(0, lambda: messagebox.showerror("Erreur", f"Identité :\n{message}"))
        finally:
            app.root.after(
                0, lambda: app.identite_button.configure(state="normal", text="Identité")
            )
            app.root.after(0, lambda: app.progress_label.configure(text=""))

    def corps_observe():
        with source_usage.run_scope(Flow.IDENTITY, artist_id=artist.id, artist_name=artist.name):
            return corps()

    run_worker(corps_observe, name="identite")
