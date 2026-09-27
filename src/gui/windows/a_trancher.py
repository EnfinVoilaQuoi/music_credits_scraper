"""« À trancher » — la porte d'entrée UNIQUE de ce qui attend une décision.

La logique vit dans `src/services/revue.py` (détecteurs PURS, testés) et la
mémoire dans `revue_repository` ; cette fenêtre affiche : un filtre par
détecteur, les cas triés par impact, « Ouvrir » la fiche, et « ✓ Normal » qui
mémorise le verdict — le cas ne revient plus tant que ses preuves ne changent
pas. Les cas marqués normaux restent consultables et rétablissables. Étape 2 :
les ACTIONS de chaque type de cas (`services/revue_actions`), qui appellent les
écrivains existants ; ce qui demande une fenêtre (fusion, groupes, écarts
Deezer) y est renvoyé.
Calculée à l'ouverture sur la discographie chargée — zéro réseau.

Niveau FORMEL (2026-09-27) : les cas dont la preuve suffit (`revue_auto`) sont
corrigés en fin de run ; « ⚡ Corriger les N cas sûrs » fait la même chose à la
demande, et la vue « Corrections auto » liste le journal avec « ↩ Rétablir ».
"""

from tkinter import messagebox

import customtkinter as ctk

from src.services import revue_actions, revue_auto
from src.services.revue import Cas, Revue, analyser, par_detecteur, tous_les_detecteurs

#: Au-delà, la fenêtre deviendrait lente à construire ; le tri par impact met
#: de toute façon en haut ce qui compte.
_MAX_LIGNES = 300
_TOUT = "Tout"
_VUE_CAS, _VUE_NORMAUX, _VUE_JOURNAL = "cas", "normaux", "journal"


def _format_impact(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} M"
    if n >= 1_000:
        return f"{n / 1_000:.0f} k"
    return str(n) if n else "—"


class ATrancherWindow(ctk.CTkToplevel):
    def __init__(self, app, artiste):
        super().__init__(app.root)
        self.app = app
        self.artiste = artiste
        self.tracks = {t.id: t for t in artiste.tracks or []}
        self.defs = {d.code: d for d in tous_les_detecteurs()}
        self.revue: Revue = analyser(app.data_manager, artiste)
        self.journal: list[dict] = app.data_manager.corrections_revue(artiste.id)
        self.vue = _VUE_CAS
        self.choix: dict[str, str | None] = {}
        self.ctx_action = revue_actions.ContexteAction(
            app.data_manager, artiste, self.tracks, renvois=self._renvois()
        )

        self.title(f"À trancher — {artiste.name}")
        self.geometry("1000x700")
        self.transient(app.root)

        entete = ctk.CTkFrame(self, fg_color="transparent")
        entete.pack(fill="x", padx=15, pady=(15, 5))
        self.titre = ctk.CTkLabel(entete, text="", font=("Arial", 14, "bold"))
        self.titre.pack(side="left")
        self.filtre = ctk.CTkOptionMenu(
            entete, values=[_TOUT], command=lambda _v: self._afficher(), width=330
        )
        self.filtre.pack(side="right")

        bandeau = ctk.CTkFrame(self, fg_color="transparent")
        bandeau.pack(fill="x", padx=15, pady=(0, 5))
        self.vues = ctk.CTkSegmentedButton(bandeau, values=[""], command=self._changer_vue)
        self.vues.pack(side="left")
        self.bouton_surs = ctk.CTkButton(
            bandeau,
            text="",
            width=0,
            fg_color="#b26a00",
            hover_color="#8a5200",
            command=self._corriger_surs,
        )
        self.bouton_surs.pack(side="right")

        ctk.CTkLabel(
            self,
            text="« ✓ Normal » : le cas ne revient plus tant que ses preuves ne changent pas. "
            "« Ouvrir » : la fiche du morceau. Les autres boutons corrigent directement.",
            font=("Arial", 11),
            text_color="gray",
        ).pack(anchor="w", padx=15)
        self.compte_rendu = ctk.CTkLabel(self, text="", font=("Arial", 11), anchor="w")
        self.compte_rendu.pack(anchor="w", padx=15)

        self.liste = ctk.CTkScrollableFrame(self)
        self.liste.pack(fill="both", expand=True, padx=15, pady=10)
        ctk.CTkButton(self, text="Fermer", command=self.destroy, width=100).pack(pady=(0, 15))
        self._rafraichir()

    # ── Données affichées ─────────────────────────────────────────────────────

    @property
    def cas(self) -> list[Cas]:
        return self.revue.masques if self.vue == _VUE_NORMAUX else self.revue.actifs

    def _libelles_vues(self) -> dict[str, str]:
        return {
            f"À trancher ({len(self.revue.actifs)})": _VUE_CAS,
            f"Marqués normaux ({len(self.revue.masques)})": _VUE_NORMAUX,
            f"⚡ Corrections auto ({len(self.journal)})": _VUE_JOURNAL,
        }

    def _changer_vue(self, libelle: str) -> None:
        self.vue = self._libelles_vues().get(libelle, _VUE_CAS)
        self._rafraichir()

    def _rafraichir(self) -> None:
        """Recompte, reconstruit le filtre (en gardant le détecteur choisi), réaffiche."""
        libelles = self._libelles_vues()
        self.vues.configure(values=list(libelles))
        self.vues.set(next(k for k, v in libelles.items() if v == self.vue))
        surs = revue_auto.cas_surs(self.revue)
        self.bouton_surs.configure(
            text=f"⚡ Corriger les {len(surs)} cas sûrs",
            state="normal" if surs else "disabled",
        )
        if self.vue == _VUE_JOURNAL:
            self.filtre.configure(values=[_TOUT])
            self.filtre.set(_TOUT)
            self.titre.configure(
                text=f"{len(self.journal)} correction(s) faite(s) sans vous — preuve formelle"
            )
            self._afficher_journal()
            return
        compte = par_detecteur(self.cas)
        ancien = self.choix.get(self.filtre.get())
        self.choix = {f"{_TOUT} ({len(self.cas)})": None}
        for d in tous_les_detecteurs():
            if compte.get(d.code):
                self.choix[f"{d.icone} {d.libelle} ({compte[d.code]})"] = d.code
        self.filtre.configure(values=list(self.choix))
        choisi = next((k for k, v in self.choix.items() if v == ancien and v), None)
        self.filtre.set(choisi or next(iter(self.choix)))
        self.titre.configure(
            text=(
                f"{len(self.revue.masques)} cas marqué(s) normal(aux)"
                if self.vue == _VUE_NORMAUX
                else f"{len(self.revue.actifs)} cas à trancher — triés par impact"
            )
        )
        self._afficher()

    def _afficher_journal(self) -> None:
        for w in self.liste.winfo_children():
            w.destroy()
        if not self.journal:
            ctk.CTkLabel(self.liste, text="Aucune correction automatique").pack(pady=20)
            return
        for c in self.journal[:_MAX_LIGNES]:
            d = self.defs.get(c["detecteur"])
            cadre = ctk.CTkFrame(self.liste)
            cadre.pack(fill="x", pady=2)
            ctk.CTkLabel(cadre, text=d.icone if d else "⚡", width=30).pack(
                side="left", padx=(6, 0)
            )
            texte = ctk.CTkFrame(cadre, fg_color="transparent")
            texte.pack(side="left", fill="x", expand=True, padx=6, pady=3)
            quand = str(c["applied_at"] or "")[:16]
            ctk.CTkLabel(
                texte,
                text=c["compte_rendu"] or c["morceau"],
                font=("Arial", 12, "bold"),
                anchor="w",
            ).pack(fill="x")
            ctk.CTkLabel(
                texte,
                text=f"{quand} — {c['motif']}",
                font=("Arial", 11),
                anchor="w",
                justify="left",
            ).pack(fill="x")
            track = self.tracks.get(c["track_id"])
            ctk.CTkButton(
                cadre,
                text="Ouvrir",
                width=70,
                state="normal" if track else "disabled",
                command=lambda t=track: self.app._show_track_details_for_track(t),
            ).pack(side="left", padx=(6, 3))
            ctk.CTkButton(
                cadre,
                text="↩ Rétablir",
                width=90,
                command=lambda x=c: self._retablir_correction(x),
            ).pack(side="left", padx=(3, 6))

    def _corriger_surs(self) -> None:
        surs = revue_auto.cas_surs(self.revue)
        if not surs or not messagebox.askyesno(
            "À trancher",
            f"Corriger les {len(surs)} cas à preuve formelle ?\n\n"
            "Chaque correction est consignée et reste rétablissable (vue « Corrections auto »).",
            parent=self,
        ):
            return
        bilan = revue_auto.corriger(self.app.data_manager, self.artiste)
        self._recalculer()
        self.compte_rendu.configure(text=bilan.resume() or "Rien à corriger")
        rafraichir = getattr(self.app, "_populate_tracks_table", None)
        if rafraichir:
            rafraichir()

    def _retablir_correction(self, correction: dict) -> None:
        try:
            texte = revue_auto.retablir(self.app.data_manager, self.artiste, correction)
        except (LookupError, ValueError) as e:
            self._echec(str(e))
            return
        self._recalculer()
        self.compte_rendu.configure(text=f"↩ {texte} — le cas est marqué normal")
        rafraichir = getattr(self.app, "_populate_tracks_table", None)
        if rafraichir:
            rafraichir()

    def _recalculer(self) -> None:
        self.revue = analyser(self.app.data_manager, self.artiste)
        self.journal = self.app.data_manager.corrections_revue(self.artiste.id)
        self._rafraichir()

    def _afficher(self) -> None:
        for w in self.liste.winfo_children():
            w.destroy()
        code = self.choix.get(self.filtre.get())
        visibles = [c for c in self.cas if code is None or c.detecteur == code]
        if not visibles:
            ctk.CTkLabel(self.liste, text="✅ Rien à trancher pour ce filtre").pack(pady=20)
            return
        for c in visibles[:_MAX_LIGNES]:
            self._ligne(c)
        if len(visibles) > _MAX_LIGNES:
            ctk.CTkLabel(
                self.liste,
                text=f"… {len(visibles) - _MAX_LIGNES} cas de moindre impact non affichés",
                text_color="gray",
            ).pack(pady=8)

    def _ligne(self, c: Cas) -> None:
        d = self.defs.get(c.detecteur)
        cadre = ctk.CTkFrame(self.liste)
        cadre.pack(fill="x", pady=2)
        ctk.CTkLabel(cadre, text=d.icone if d else "•", width=30).pack(side="left", padx=(6, 0))
        texte = ctk.CTkFrame(cadre, fg_color="transparent")
        texte.pack(side="left", fill="x", expand=True, padx=6, pady=3)
        ctk.CTkLabel(texte, text=c.morceau, font=("Arial", 12, "bold"), anchor="w").pack(fill="x")
        ctk.CTkLabel(texte, text=c.motif, font=("Arial", 11), anchor="w", justify="left").pack(
            fill="x"
        )
        actions = [] if self.vue == _VUE_NORMAUX else revue_actions.actions_pour(c)
        if actions:
            barre = ctk.CTkFrame(texte, fg_color="transparent")
            barre.pack(anchor="w", pady=(2, 0))
            for a in actions:
                ctk.CTkButton(
                    barre,
                    text=a.libelle,
                    height=24,
                    width=0,
                    fg_color="gray30",
                    hover_color="gray40",
                    command=lambda a=a, x=c: self._agir(a, x),
                ).pack(side="left", padx=(0, 4))
        ctk.CTkLabel(cadre, text=_format_impact(c.impact), width=70).pack(side="left")
        track = self.tracks.get(c.track_id)
        ctk.CTkButton(
            cadre,
            text="Ouvrir",
            width=70,
            state="normal" if track else "disabled",
            command=lambda t=track: self.app._show_track_details_for_track(t),
        ).pack(side="left", padx=(6, 3))
        if self.vue == _VUE_NORMAUX:
            ctk.CTkButton(
                cadre, text="↩ Rétablir", width=90, command=lambda x=c: self._retablir(x)
            ).pack(side="left", padx=(3, 6))
        else:
            ctk.CTkButton(
                cadre,
                text="✓ Normal",
                width=90,
                fg_color="#2e7d32",
                hover_color="#1b5e20",
                command=lambda x=c: self._normal(x),
            ).pack(side="left", padx=(3, 6))

    # ── Verdicts ──────────────────────────────────────────────────────────────

    def _normal(self, c: Cas) -> None:
        self.app.data_manager.trancher_cas(
            self.artiste.id, c.detecteur, c.cle, morceau=c.morceau, motif=c.motif
        )
        self.revue.actifs.remove(c)
        self.revue.masques.append(c)
        self._rafraichir()

    def _renvois(self) -> dict:
        """Les fenêtres vers lesquelles une action renvoie (import tardif :
        elles tirent leur propre pile de dépendances)."""

        def fusion(t1, t2):
            from src.gui.dialogs.merge_tracks import fusionner_paire

            fusionner_paire(self.app, t1, t2)

        def groupes():
            from src.gui.windows.formations import show_formations

            show_formations(self.app)

        def ecarts_deezer():
            from src.gui.windows.ecarts_deezer import show_ecarts_deezer

            show_ecarts_deezer(self.app)

        def lire_piste_deezer(tid):
            # Appelé depuis le fil de fond de l'action (réseau) : le pont vers
            # la boucle asyncio de l'application, comme la fenêtre des écarts.
            from src.concurrency import async_loop

            enricher = self.app.runtime.data_enricher
            return async_loop.run_sync(enricher.deezer_client.get_track_async(enricher.http, tid))

        return {
            "fusion": fusion,
            "groupes": groupes,
            "ecarts_deezer": ecarts_deezer,
            "lire_piste_deezer": lire_piste_deezer,
        }

    def _agir(self, action, c: Cas) -> None:
        if action.confirmation and not messagebox.askyesno(
            "À trancher", action.confirmation, parent=self
        ):
            return
        if action.reseau:
            # La page Spotify d'une ligne créée : hors du fil Tk (règle de
            # concurrence du projet — `run_worker` pour ce qui touche la boucle).
            from src.concurrency.lifecycle import run_worker

            self.compte_rendu.configure(text=f"⏳ {action.libelle} — « {c.morceau} »…")

            def _fond():
                try:
                    texte = revue_actions.executer(action, self.ctx_action, c)
                except Exception as e:  # noqa: BLE001 - l'échec s'affiche
                    message = str(e)
                    self.after(0, lambda: self._echec(message))
                    return
                self.after(0, lambda: self._resolu(action, c, texte))

            run_worker(_fond, name="a-trancher-action")
            return
        try:
            compte_rendu = revue_actions.executer(action, self.ctx_action, c)
        except Exception as e:  # noqa: BLE001 - l'échec s'affiche, rien n'est retiré
            self._echec(str(e))
            return
        self._resolu(action, c, compte_rendu)

    def _echec(self, message: str) -> None:
        if self.winfo_exists():
            self.compte_rendu.configure(text="")
            messagebox.showerror("À trancher", f"L'action a échoué : {message}", parent=self)

    def _resolu(self, action, c: Cas, compte_rendu: str) -> None:
        if not action.resout or not self.winfo_exists():
            return  # un renvoi : la décision se prend dans la fenêtre ouverte
        if c in self.revue.actifs:
            self.revue.actifs.remove(c)
        self.compte_rendu.configure(text=f"✅ {compte_rendu}")
        self._rafraichir()
        # Une fiche CRÉÉE (Deezer, remix Kworb) n'existe pas encore dans la
        # liste en mémoire : on recharge ; sinon un simple réaffichage suffit.
        cree = action.code == "deezer_appliquer" or action.code in (
            "kworb_collab",
            "kworb_tiers",
        )
        rafraichir = getattr(
            self.app, "_reload_tracks_and_refresh" if cree else "_populate_tracks_table", None
        )
        if rafraichir:
            rafraichir()

    def _retablir(self, c: Cas) -> None:
        self.app.data_manager.annuler_verdict(self.artiste.id, c.detecteur, c.cle)
        self.revue.masques.remove(c)
        self.revue.actifs.append(c)
        self.revue.actifs.sort(key=lambda x: (-x.impact, x.detecteur, x.morceau))
        self._rafraichir()


def show_a_trancher(app) -> None:
    """Ouvre le panneau pour l'artiste courant (discographie chargée)."""
    artiste = app.current_artist
    if not artiste or not artiste.tracks:
        messagebox.showwarning("Attention", "Chargez d'abord une discographie")
        return
    ATrancherWindow(app, artiste)
