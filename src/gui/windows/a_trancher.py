"""« À trancher » — la porte d'entrée UNIQUE de ce qui attend une décision.

La logique vit dans `src/services/revue.py` (détecteurs PURS, testés) et la
mémoire dans `revue_repository` ; cette fenêtre affiche : un filtre par
détecteur, les cas triés par impact, « Ouvrir » la fiche, et « ✓ Normal » qui
mémorise le verdict — le cas ne revient plus tant que ses preuves ne changent
pas. Les cas marqués normaux restent consultables et rétablissables.
Calculée à l'ouverture sur la discographie chargée — zéro réseau.
"""

from tkinter import messagebox

import customtkinter as ctk

from src.services.revue import Cas, Revue, analyser, par_detecteur, tous_les_detecteurs

#: Au-delà, la fenêtre deviendrait lente à construire ; le tri par impact met
#: de toute façon en haut ce qui compte.
_MAX_LIGNES = 300
_TOUT = "Tout"


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
        self.choix: dict[str, str | None] = {}

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
        self.voir_masques = ctk.BooleanVar(value=False)
        self.case_masques = ctk.CTkCheckBox(
            entete, text="", variable=self.voir_masques, command=self._rafraichir
        )
        self.case_masques.pack(side="right", padx=10)

        ctk.CTkLabel(
            self,
            text="« ✓ Normal » : le cas ne revient plus tant que ses preuves ne changent pas. "
            "« Ouvrir » : la fiche du morceau.",
            font=("Arial", 11),
            text_color="gray",
        ).pack(anchor="w", padx=15)

        self.liste = ctk.CTkScrollableFrame(self)
        self.liste.pack(fill="both", expand=True, padx=15, pady=10)
        ctk.CTkButton(self, text="Fermer", command=self.destroy, width=100).pack(pady=(0, 15))
        self._rafraichir()

    # ── Données affichées ─────────────────────────────────────────────────────

    @property
    def cas(self) -> list[Cas]:
        return self.revue.masques if self.voir_masques.get() else self.revue.actifs

    def _rafraichir(self) -> None:
        """Recompte, reconstruit le filtre (en gardant le détecteur choisi), réaffiche."""
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
                if self.voir_masques.get()
                else f"{len(self.revue.actifs)} cas à trancher — triés par impact"
            )
        )
        self.case_masques.configure(
            text=f"Voir les cas marqués normaux ({len(self.revue.masques)})"
        )
        self._afficher()

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
        ctk.CTkLabel(cadre, text=_format_impact(c.impact), width=70).pack(side="left")
        track = self.tracks.get(c.track_id)
        ctk.CTkButton(
            cadre,
            text="Ouvrir",
            width=70,
            state="normal" if track else "disabled",
            command=lambda t=track: self.app._show_track_details_for_track(t),
        ).pack(side="left", padx=(6, 3))
        if self.voir_masques.get():
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
