"""« À trancher » — ce que les détecteurs trouvent suspect (étape 1, lecture seule).

La logique vit dans `src/services/revue.py` (détecteurs PURS, testés) ; cette
fenêtre ne fait qu'afficher : un filtre par détecteur, les cas triés par
impact, et un bouton qui ouvre la fiche du morceau. Calculée à l'ouverture sur
la discographie déjà chargée — zéro réseau, zéro écriture.
"""

from tkinter import messagebox

import customtkinter as ctk

from src.services.revue import Cas, analyser, par_detecteur, tous_les_detecteurs

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
        self.cas: list[Cas] = analyser(app.data_manager, artiste)
        self.defs = {d.code: d for d in tous_les_detecteurs()}

        self.title(f"À trancher — {artiste.name}")
        self.geometry("980x680")
        self.transient(app.root)

        compte = par_detecteur(self.cas)
        entete = ctk.CTkFrame(self, fg_color="transparent")
        entete.pack(fill="x", padx=15, pady=(15, 5))
        ctk.CTkLabel(
            entete,
            text=f"{len(self.cas)} cas suspect(s) — triés par impact (streams)",
            font=("Arial", 14, "bold"),
        ).pack(side="left")

        self.choix = {f"{_TOUT} ({len(self.cas)})": None}
        for d in tous_les_detecteurs():
            if compte.get(d.code):
                self.choix[f"{d.icone} {d.libelle} ({compte[d.code]})"] = d.code
        self.filtre = ctk.CTkOptionMenu(
            entete, values=list(self.choix), command=lambda _v: self._afficher(), width=330
        )
        self.filtre.pack(side="right")

        ctk.CTkLabel(
            self,
            text="Lecture seule : rien n'est corrigé ici. Ouvrir la fiche pour trancher ; "
            "un cas disparaît de lui-même quand une meilleure donnée arrive.",
            font=("Arial", 11),
            text_color="gray",
        ).pack(anchor="w", padx=15)

        self.liste = ctk.CTkScrollableFrame(self)
        self.liste.pack(fill="both", expand=True, padx=15, pady=10)
        ctk.CTkButton(self, text="Fermer", command=self.destroy, width=100).pack(pady=(0, 15))
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
        d = self.defs[c.detecteur]
        cadre = ctk.CTkFrame(self.liste)
        cadre.pack(fill="x", pady=2)
        ctk.CTkLabel(cadre, text=d.icone, width=30).pack(side="left", padx=(6, 0))
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
        ).pack(side="left", padx=6)


def show_a_trancher(app) -> None:
    """Ouvre le panneau pour l'artiste courant (discographie chargée)."""
    artiste = app.current_artist
    if not artiste or not artiste.tracks:
        messagebox.showwarning("Attention", "Chargez d'abord une discographie")
        return
    ATrancherWindow(app, artiste)
