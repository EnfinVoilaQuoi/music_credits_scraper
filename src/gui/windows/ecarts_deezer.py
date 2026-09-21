"""« 🎧 Écarts Deezer » — ce que Deezer publie et que la base n'a pas (2026-09-21).

Adaptateur GUI de `services/ecarts_deezer` : la détection et la création vivent
dans le service (partagé avec `python -m src.cli deezer`). Ici : la fenêtre,
une case par écart (pré-cochée selon la nature), une case « tout le disque »
par album, la liste des candidats quand l'identité Deezer est ambiguë, et le
worker (`run_worker` : la session async vit sur la boucle unique).

Ouverte à la demande (bouton du dialogue « Discographie ») ou en fin de run
discographie par le hook `confirmer_ecarts`, avec un bilan déjà calculé.
"""

from tkinter import messagebox

import customtkinter as ctk

from src.concurrency import async_loop
from src.concurrency.lifecycle import run_worker, stop_requested
from src.observability import source_usage
from src.observability.registry import Flow
from src.services import deezer_identite, ecarts_deezer
from src.utils.logger import get_logger

logger = get_logger(__name__)


class EcartsDeezerWindow(ctk.CTkToplevel):
    def __init__(self, app, bilan=None):
        super().__init__(app.root)
        self.app = app
        self.artist = app.current_artist
        self.bilan = bilan
        self.cases: list[tuple[object, ctk.BooleanVar]] = []
        self.choix_candidat = ctk.StringVar(value="")

        self.title(f"Écarts Deezer — {self.artist.name}")
        self.geometry("960x680")
        self.transient(app.root)

        self.statut = ctk.CTkLabel(self, text="Lecture du catalogue Deezer…", font=("Arial", 13))
        self.statut.pack(pady=(15, 5))
        self.liste = ctk.CTkScrollableFrame(self, label_text="")
        self.liste.pack(fill="both", expand=True, padx=15, pady=10)

        pied = ctk.CTkFrame(self, fg_color="transparent")
        pied.pack(fill="x", padx=15, pady=(0, 15))
        self.bouton_creer = ctk.CTkButton(
            pied, text="Créer les lignes cochées", command=self._creer, state="disabled"
        )
        self.bouton_creer.pack(side="left")
        self.bouton_choisir = ctk.CTkButton(
            pied, text="Choisir cet artiste Deezer", command=self._choisir, state="disabled"
        )
        self.bouton_choisir.pack(side="left", padx=8)
        ctk.CTkButton(pied, text="Fermer", command=self.destroy, width=100).pack(side="right")

        if bilan is not None:
            self._afficher(bilan)
        else:
            run_worker(self._detecter, name="ecarts-deezer")

    # ── Détection ─────────────────────────────────────────────────────────────

    def _detecter(self, force_id: int | None = None) -> None:
        runtime, artist = self.app.runtime, self.artist
        enricher = runtime.data_enricher
        with source_usage.run_scope(Flow.DISCO, artist_id=artist.id, artist_name=artist.name):
            try:
                deezer_id = async_loop.run_sync(
                    deezer_identite.resoudre_async(
                        enricher.deezer_client,
                        enricher.http,
                        runtime.data_manager,
                        artist,
                        force_id=force_id,
                    )
                )
                bilan = ecarts_deezer.detecter(
                    runtime,
                    artist,
                    deezer_id=deezer_id,
                    should_stop=stop_requested,
                    genius_api=runtime.genius_api,
                )
            except deezer_identite.ArtisteDeezerAmbigu as amb:
                bilan = ecarts_deezer.BilanEcarts(complete=False, motif="artiste Deezer ambigu")
                bilan.ambigu = amb.candidats
            except Exception as e:  # noqa: BLE001 - fond GUI : l'erreur s'affiche
                logger.exception("Écarts Deezer : lecture interrompue")
                message = str(e)
                self.after(0, lambda: self._annoncer(f"❌ {message}"))
                return
        self.after(0, self._afficher, bilan)

    def _annoncer(self, texte: str) -> None:
        if self.winfo_exists():
            self.statut.configure(text=texte)

    # ── Affichage ─────────────────────────────────────────────────────────────

    def _vider(self) -> None:
        for w in self.liste.winfo_children():
            w.destroy()
        self.cases = []

    def _afficher(self, bilan) -> None:
        if not self.winfo_exists():
            return
        self.bilan = bilan
        self._vider()
        if bilan.ambigu:
            self._afficher_candidats(bilan.ambigu)
            return
        c = bilan.compteurs()
        self.statut.configure(
            text=(
                f"{bilan.albums_lus} disques · {bilan.pistes_lues} pistes · "
                f"{len(bilan.ecarts)} écart(s) : "
                + " · ".join(
                    f"{ecarts_deezer.GLYPHES[n]} {c[n]}" for n in ecarts_deezer.NATURES if c.get(n)
                )
                + ("" if bilan.complete else f" · ⚠️ incomplet ({bilan.motif})")
            )
        )
        if not bilan.ecarts:
            ctk.CTkLabel(
                self.liste,
                text="✅ Aucun écart — la base a tout ce que Deezer publie.",
                font=("Arial", 13),
            ).pack(anchor="w", pady=10)
            if bilan.editions_jumelles:
                ctk.CTkLabel(
                    self.liste,
                    text="Éditions jumelles (déjà connues) : " + ", ".join(bilan.editions_jumelles),
                    text_color="gray",
                ).pack(anchor="w", pady=4)
            return
        par_album: dict[int, list] = {}
        for e in bilan.ecarts:
            par_album.setdefault(e.album.id, []).append(e)
        for ecarts in par_album.values():
            self._bloc_album(ecarts)
        ctk.CTkLabel(
            self.liste,
            text=(
                "✚ absent d'un disque connu et 💿 disque absent : cochés d'office. "
                "🎚️ version : cochée si Genius ou Kworb la connaît (BPM/tonalité suivront), "
                "sinon listée. 👥 apparition : disque d'un autre (rôle secondaire), à toi de voir.\n"
                "Une ligne créée reçoit de Deezer date, durée, ISRC, feats, label ; d'une version, "
                "son socle lui transmet paroles et auteurs selon sa famille (héritage)."
            ),
            font=("Arial", 10),
            text_color="gray",
            justify="left",
            wraplength=880,
        ).pack(anchor="w", pady=(15, 5))
        self.bouton_creer.configure(state="normal")

    def _bloc_album(self, ecarts: list) -> None:
        a = ecarts[0].album
        cadre = ctk.CTkFrame(self.liste)
        cadre.pack(fill="x", pady=5)
        entete = ctk.CTkFrame(cadre, fg_color="transparent")
        entete.pack(fill="x", padx=8, pady=(6, 2))
        var_album = ctk.BooleanVar(value=all(e.coche for e in ecarts))
        cases_du_disque: list[ctk.BooleanVar] = []

        def _tout(v=var_album, cases=cases_du_disque):
            for c in cases:
                c.set(v.get())

        ctk.CTkCheckBox(entete, text="", variable=var_album, width=28, command=_tout).pack(
            side="left"
        )
        ctk.CTkLabel(
            entete,
            text=f"{(a.record_type or '?').upper()}  « {a.title} » — {a.artist_name}"
            + (f"  ({a.release_date})" if a.release_date else ""),
            font=("Arial", 12, "bold"),
            anchor="w",
        ).pack(side="left", fill="x", expand=True)
        for e in ecarts:
            var = ctk.BooleanVar(value=e.coche)
            self.cases.append((e, var))
            cases_du_disque.append(var)
            texte = f"{ecarts_deezer.GLYPHES[e.nature]} {e.titre}"
            if e.motifs:
                texte += "   · " + " · ".join(e.motifs)
            ctk.CTkCheckBox(cadre, text=texte, variable=var, font=("Arial", 11)).pack(
                anchor="w", padx=30, pady=1
            )

    def _afficher_candidats(self, candidats: list) -> None:
        self.statut.configure(
            text="❓ Plusieurs artistes Deezer portent ce nom — lequel est le tien ?"
        )
        if not candidats:
            ctk.CTkLabel(
                self.liste,
                text="Aucun homonyme exact sur Deezer. Vérifie l'orthographe de l'artiste.",
            ).pack(anchor="w", pady=10)
            return
        for c in candidats:
            ctk.CTkRadioButton(
                self.liste,
                text=(
                    f"{c.name}  —  {c.nb_album} disque(s), {c.nb_fan} fan(s)"
                    + (f"  ·  {c.detail}" if c.detail else "")
                    + f"  ·  https://www.deezer.com/artist/{c.id}"
                ),
                variable=self.choix_candidat,
                value=str(c.id),
            ).pack(anchor="w", pady=4)
        self.bouton_choisir.configure(state="normal")

    # ── Actions ───────────────────────────────────────────────────────────────

    def _choisir(self) -> None:
        if not self.choix_candidat.get():
            return
        force_id = int(self.choix_candidat.get())
        self.bouton_choisir.configure(state="disabled")
        self._vider()
        self._annoncer("Lecture du catalogue Deezer…")
        run_worker(lambda: self._detecter(force_id=force_id), name="ecarts-deezer")

    def _creer(self) -> None:
        choisis = [e for e, var in self.cases if var.get()]
        if not choisis:
            messagebox.showinfo("Écarts Deezer", "Aucune ligne cochée.")
            return
        if not messagebox.askyesno(
            "Écarts Deezer",
            f"Créer {len(choisis)} ligne(s) de morceau pour {self.artist.name} ?",
            parent=self,
        ):
            return
        self.bouton_creer.configure(state="disabled")
        self._annoncer(f"Création de {len(choisis)} ligne(s)…")
        runtime, artist = self.app.runtime, self.artist

        def _travail():
            enricher = runtime.data_enricher
            client, http = enricher.deezer_client, enricher.http
            with source_usage.run_scope(Flow.DISCO, artist_id=artist.id, artist_name=artist.name):
                comptes = ecarts_deezer.creer_lignes(
                    runtime.data_manager,
                    artist,
                    choisis,
                    lire_piste=lambda tid: async_loop.run_sync(client.get_track_async(http, tid)),
                )
            self.after(0, self._fin_creation, comptes)

        run_worker(_travail, name="ecarts-deezer-creation")

    def _fin_creation(self, comptes: list[str]) -> None:
        self.app._reload_tracks_and_refresh()
        messagebox.showinfo("Écarts Deezer", "\n".join(comptes) or "Rien à créer.", parent=self)
        self.destroy()


def show_ecarts_deezer(app, bilan=None) -> None:
    """Ouvre la fenêtre (singleton par artiste) — avec un bilan déjà calculé
    (fin de run discographie) ou en lançant la détection."""
    if app.current_artist is None:
        messagebox.showwarning("Écarts Deezer", "Charge un artiste d'abord.")
        return
    from src.gui.workers.retrieval import discographie_chargee

    if not discographie_chargee(app):
        messagebox.showwarning(
            "Écarts Deezer",
            "Récupère la discographie d'abord : l'identité Deezer se juge sur les albums.",
        )
        return
    existante = getattr(app, "ecarts_deezer_window", None)
    if existante is not None and existante.winfo_exists():
        existante.destroy()
    app.ecarts_deezer_window = EcartsDeezerWindow(app, bilan)
