"""« 🔍 Vérifier les identifiants Spotify » — le remplaçant de la case dangereuse.

Elle succède à « 🔄 Réinitialiser les Spotify IDs », qui effaçait **tous** les
identifiants de l'artiste pour les re-scraper : elle emportait les bons avec les
mauvais, et le re-scrape reproposait les fautifs depuis le cache — donc un
scrape complet pour revenir au même état. Le besoin derrière, lui, était réel
(« mes identifiants sont douteux, reprends-les »).

La réponse est celle de `repair_spotify_ids.py` : **n'effacer que ce qu'on peut
MONTRER**. La fenêtre confronte chaque identifiant de l'artiste à l'oracle embed,
affiche ce que Spotify sert vraiment, et n'écrit RIEN tant que rien n'est validé.
Sur A2H, le geste porte sur 65 identifiants au lieu de 330.

**Aucune règle neuve** : le balayage est `spotify_audit.verifier_lignes` (le même
que le CLI), le verdict `identite_concorde` (le même que le garde-fou), le retrait
`rejeter_spotify_id` (les trois gestes, cache compris). Ce fichier ne fait que
câbler un rapport.
"""

from tkinter import messagebox

import customtkinter as ctk

from src.concurrency.lifecycle import start_worker, stop_requested
from src.utils.logger import get_logger
from src.utils.spotify_audit import (
    lignes_a_verifier,
    rejeter_spotify_id,
    track_de_la_ligne,
    verifier_lignes,
)

logger = get_logger(__name__)


class VerificationSpotifyWindow(ctk.CTkToplevel):
    """Rapport de vérification, une case à cocher par écart."""

    def __init__(self, app, artiste: str):
        super().__init__(app)
        self.app = app
        self.artiste = artiste
        self.ecarts: list[dict] = []
        self.cases: list[tuple[dict, ctk.BooleanVar]] = []

        self.title(f"Vérifier les identifiants Spotify — {artiste}")
        self.geometry("900x640")
        self.transient(app)

        self.statut = ctk.CTkLabel(self, text="Lecture des identifiants…", font=("Arial", 13))
        self.statut.pack(pady=(15, 5))

        self.barre = ctk.CTkProgressBar(self, width=500)
        self.barre.set(0)
        self.barre.pack(pady=5)

        self.liste = ctk.CTkScrollableFrame(self, label_text="")
        self.liste.pack(fill="both", expand=True, padx=15, pady=10)

        self.pied = ctk.CTkFrame(self, fg_color="transparent")
        self.pied.pack(fill="x", padx=15, pady=(0, 15))
        self.bouton_retirer = ctk.CTkButton(
            self.pied,
            text="Retirer les identifiants cochés",
            command=self._retirer,
            state="disabled",
            fg_color="#B22222",
            hover_color="#8B0000",
        )
        self.bouton_retirer.pack(side="left")
        ctk.CTkButton(self.pied, text="Fermer", command=self.destroy, width=100).pack(side="right")

        start_worker(self._balayer, name="verification-spotify")

    # ── Le balayage, sur un thread de fond ────────────────────────────────────

    def _balayer(self) -> None:
        """Corps SYNC : l'oracle est du `requests` nu, il ne touche pas la boucle
        asyncio — d'où `start_worker` et non `run_worker` (règle de concurrence
        du projet). L'arrêt est testé ENTRE deux requêtes par `verifier_lignes`.
        """
        try:
            lignes = lignes_a_verifier(self.app.data_manager.engine, self.artiste)
            self.after(0, lambda: self._annoncer(f"🔎 {len(lignes)} identifiant(s) à vérifier"))
            rapport = verifier_lignes(
                lignes,
                progression=lambda faits, total: self.after(
                    0, self._avancement, faits, total, self.artiste
                ),
                interrompu=stop_requested,
            )
        except Exception as e:  # noqa: BLE001 - fond GUI : l'erreur s'affiche
            logger.exception("Vérification des identifiants Spotify interrompue")
            # `e` est délié à la sortie du `except` : le message est copié AVANT
            # que le rappel `after` ne s'exécute, sinon il lèverait un NameError
            # au lieu d'afficher la panne.
            message = str(e)
            self.after(0, lambda: self._annoncer(f"❌ {message}"))
            return
        self.after(0, self._afficher, rapport)

    def _annoncer(self, texte: str) -> None:
        if self.winfo_exists():
            self.statut.configure(text=texte)

    def _avancement(self, faits: int, total: int, artiste: str) -> None:
        if not self.winfo_exists():
            return
        self.statut.configure(text=f"🔎 {artiste} — {faits}/{total} identifiant(s) vérifié(s)")
        self.barre.set(faits / total if total else 1)

    # ── Le rapport ────────────────────────────────────────────────────────────

    def _afficher(self, rapport: dict) -> None:
        if not self.winfo_exists():
            return
        self.barre.set(1)
        self.ecarts = rapport["ecarts"]
        etrangers = sum(1 for e in self.ecarts if e["artiste_etranger"])
        self.statut.configure(
            text=(
                f"{rapport['verifies']} vérifié(s) · {rapport['illisibles']} illisible(s) · "
                f"{len(self.ecarts)} écart(s), dont {etrangers} 🚨 artiste étranger"
            )
        )

        if not self.ecarts:
            ctk.CTkLabel(
                self.liste,
                text="✅ Aucun écart — tous les identifiants désignent le bon morceau.",
                font=("Arial", 13),
            ).pack(anchor="w", pady=10)
            return

        # Un identifiant illisible n'apparaît PAS ici : il n'accuse personne.
        for ecart in self.ecarts:
            self._ligne(ecart)
        self.bouton_retirer.configure(state="normal")
        ctk.CTkLabel(
            self.liste,
            text=(
                "🚨 = aucun artiste attendu chez Spotify : l'identifiant désigne un AUTRE\n"
                "morceau. Les autres écarts demandent un œil — un titre écrit autrement,\n"
                "une version live, un featuring noté d'un seul côté n'est pas une erreur.\n"
                "Seules les lignes COCHÉES seront retirées."
            ),
            font=("Arial", 10),
            text_color="gray",
            justify="left",
        ).pack(anchor="w", pady=(15, 5))

    def _ligne(self, ecart: dict) -> None:
        cadre = ctk.CTkFrame(self.liste)
        cadre.pack(fill="x", pady=4)

        # Pré-cochés : les artistes étrangers seuls, ceux que `repair_spotify_ids`
        # retire sans hésiter. Le reste demande un humain, donc part décoché.
        var = ctk.BooleanVar(value=bool(ecart["artiste_etranger"]))
        self.cases.append((ecart, var))

        marque = "🚨" if ecart["artiste_etranger"] else "  "
        edition = "" if ecart["principal"] else "  (édition)"
        ctk.CTkCheckBox(
            cadre,
            text=f"{marque} #{ecart['track_id']} « {ecart['titre']} »{edition}",
            variable=var,
            font=("Arial", 12, "bold"),
        ).pack(anchor="w", padx=8, pady=(6, 0))

        artistes = ", ".join(ecart["spotify"]["artists"])
        duree = ecart["spotify"]["duration"]
        ctk.CTkLabel(
            cadre,
            text=(
                f"id {ecart['spotify_id']}\n"
                f"Spotify sert : « {ecart['spotify']['name']} » — {artistes}"
                + (f" — {duree} s" if duree else "")
                + f"\n→ {ecart['motif']}"
            ),
            font=("Arial", 10),
            text_color="gray",
            justify="left",
        ).pack(anchor="w", padx=35, pady=(0, 6))

    # ── Le retrait, sur validation ────────────────────────────────────────────

    def _retirer(self) -> None:
        choisis = [(e, v) for e, v in self.cases if v.get()]
        if not choisis:
            messagebox.showinfo("Rien à retirer", "Aucun identifiant n'est coché.")
            return
        if not messagebox.askyesno(
            "Retirer les identifiants",
            f"Retirer {len(choisis)} identifiant(s) ?\n\n"
            "Seront aussi retirées les données qui EN DÉCOULENT : BPM, tonalité et "
            "mode mesurés par ReccoBeats (qui s'interroge PAR l'identifiant), ainsi "
            "que les streams Spotify et Kworb, et l'entrée du cache du scraper.\n\n"
            "Les morceaux repasseront en « jamais cherché ».",
        ):
            return

        par_id = {t.id: t for t in getattr(self.app.current_artist, "tracks", []) or []}
        retires = observations = 0
        for ecart, var in choisis:
            track = par_id.get(ecart["track_id"])
            if track is None:
                # La fiche n'est pas chargée (autre artiste, sélection partielle) :
                # on rejette quand même EN BASE, sur un objet reconstruit.
                track = track_de_la_ligne(
                    {
                        "id": ecart["track_id"],
                        "title": ecart["titre"],
                        "artiste": ecart["artiste"],
                        "duration": None,
                        "is_featuring": False,
                        "primary_artist_name": None,
                    }
                )
                # On ne lui REPOSE pas l'identifiant : `clear_track_spotify_id`
                # nettoie la base sur l'`id` du morceau, et cet objet-ci ne sert
                # qu'à porter cet `id` — il est jeté aussitôt après. Le lui poser
                # ferait de ce fichier un producteur d'identifiants aux yeux du
                # crible AST, pour un objet que personne ne relit.
            rapport = rejeter_spotify_id(self.app.data_manager, track, ecart["spotify_id"])
            retires += 1
            observations += len(rapport["observations_retirees"])
            var.set(False)

        self.bouton_retirer.configure(state="disabled")
        messagebox.showinfo(
            "Identifiants retirés",
            f"{retires} identifiant(s) retiré(s), avec {observations} observation(s) "
            "qui en découlaient.\n\nLe prochain enrichissement leur en cherchera un.",
        )
        self.app._populate_tracks_table()


def show_verification_spotify(app) -> None:
    """Ouvre la fenêtre pour l'artiste courant."""
    artiste = getattr(app.current_artist, "name", None)
    if not artiste:
        messagebox.showwarning("Attention", "Chargez d'abord un artiste")
        return
    VerificationSpotifyWindow(app, artiste)
