"""Auto-complétion des artistes EN BASE.

Deux usages, une règle : la barre de recherche de la fenêtre principale (passer
vite d'un artiste à l'autre) et la fenêtre « Ajout d'artiste » (proposer ceux
qui sont déjà en base pendant la frappe, pour ne pas en ajouter un deux fois ;
sans correspondance, la recherche Genius prend le relais).

C'est une recherche d'INTERFACE, pas un rapprochement d'identité : une
sous-chaîne suffit à PROPOSER, l'utilisateur choisit. Les débuts de nom
passent devant, puis les débuts de mot, puis le reste.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.presentation import format as fmt


@dataclass(frozen=True)
class ResumeArtiste:
    nom: str
    titres: int = 0
    #: Date (« AAAA-MM-JJ ») de la dernière écriture sur ses morceaux.
    derniere_maj: str | None = None

    @property
    def detail(self) -> str:
        morceaux = [f"{fmt.nombre(self.titres)} titre{'s' if self.titres > 1 else ''}"]
        if self.derniere_maj:
            morceaux.append(f"dernier run {fmt.format_date(self.derniere_maj, vide='')}")
        return " • ".join(morceaux)

    @classmethod
    def depuis_resume(cls, d: dict) -> ResumeArtiste:
        """Depuis une ligne de `ArtistRepository.resume_des_artistes`."""
        return cls(d["name"], d.get("tracks_count") or 0, d.get("last_update"))


def _rang(saisie: str, nom: str) -> int | None:
    """0 = début du nom, 1 = début d'un mot, 2 = ailleurs, None = absent."""
    cle = fmt.normalize_text(nom)
    if cle.startswith(saisie):
        return 0
    if any(mot.startswith(saisie) for mot in cle.split()):
        return 1
    if saisie in cle:
        return 2
    return None


def suggestions(
    saisie: str, artistes: list[ResumeArtiste], limite: int = 10
) -> list[ResumeArtiste]:
    """Les artistes dont le nom contient la saisie, les meilleurs d'abord.

    Saisie vide ⇒ tous (la liste déroulante d'un champ vide sert à parcourir).
    """
    saisie = fmt.normalize_text(saisie.strip())
    if not saisie:
        return list(artistes)[:limite]
    classes = []
    for artiste in artistes:
        rang = _rang(saisie, artiste.nom)
        if rang is not None:
            classes.append((rang, fmt.normalize_text(artiste.nom), artiste))
    classes.sort(key=lambda x: (x[0], x[1]))
    return [a for _, _, a in classes[:limite]]


def correspondance_exacte(saisie: str, artistes: list[ResumeArtiste]) -> ResumeArtiste | None:
    """L'artiste en base dont le nom EST la saisie (casse et accents ignorés) :
    l'ajouter serait un doublon, la fenêtre d'ajout propose de l'ouvrir."""
    cle = fmt.normalize_text(saisie.strip())
    return next((a for a in artistes if fmt.normalize_text(a.nom) == cle), None)
