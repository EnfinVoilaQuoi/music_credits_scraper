"""Le pied de la table : sélection, compteurs `validés / éligibles`, pourcentage.

Deux portées, toujours affichées pour que le pourcentage ne soit jamais
ambigu : GLOBAL (toute la discographie chargée) et FILTRÉ (les seules lignes
visibles). Les verdicts viennent de `track_validation.evaluer` ; un morceau
qui ne « compte pas à valider » (désactivé, inédit, à venir, sans info) n'est
ni validé ni éligible.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from src.presentation.filtres import ChampPied, manque_le_champ
from src.presentation.table import LigneMorceau


class Portee(StrEnum):
    GLOBAL = "GLOBAL"
    FILTRE = "FILTRÉ"

    def basculee(self) -> Portee:
        return Portee.FILTRE if self is Portee.GLOBAL else Portee.GLOBAL


@dataclass(frozen=True)
class Compteur:
    champ: ChampPied
    valides: int
    eligibles: int

    @property
    def manquants(self) -> int:
        return self.eligibles - self.valides

    @property
    def texte(self) -> str:
        return f"({self.valides}/{self.eligibles})"


@dataclass(frozen=True)
class Pied:
    compteurs: tuple[Compteur, ...]
    #: Certifications RÉELLES portées par les lignes de la portée / par toute la
    #: discographie chargée. Ne conditionne pas le pourcentage.
    certifs_portee: int
    certifs_total: int
    complets: int
    eligibles: int

    @property
    def pourcentage(self) -> int | None:
        if not self.eligibles:
            return None
        return round(100 * self.complets / self.eligibles)

    @property
    def texte_pourcentage(self) -> str:
        p = self.pourcentage
        return "—" if p is None else f"{p} %"

    def compteur(self, champ: ChampPied) -> Compteur:
        return next(c for c in self.compteurs if c.champ is champ)


def _nb_certifs(lignes) -> int:
    return sum(len(ligne.track.certs.reelles) for ligne in lignes)


def calculer(toutes: list[LigneMorceau], visibles: list[LigneMorceau], portee: Portee) -> Pied:
    lignes = toutes if portee is Portee.GLOBAL else visibles
    eligibles = [ligne for ligne in lignes if ligne.constat.compte_a_valider]
    compteurs = tuple(
        Compteur(
            champ=champ,
            valides=sum(1 for ligne in eligibles if not manque_le_champ(ligne, champ)),
            eligibles=len(eligibles),
        )
        for champ in ChampPied
    )
    return Pied(
        compteurs=compteurs,
        certifs_portee=_nb_certifs(lignes),
        certifs_total=_nb_certifs(toutes),
        complets=sum(1 for ligne in eligibles if ligne.constat.complet),
        eligibles=len(eligibles),
    )


def texte_selection(selection: set, visibles: list[LigneMorceau]) -> str:
    """« 15 titres sélectionnés (15/384) » — la sélection rapportée aux lignes
    VISIBLES : une coche cachée par un filtre ne compte pas à l'écran."""
    n = sum(1 for ligne in visibles if ligne.id in selection)
    pluriel = "s" if n > 1 else ""
    return f"{n} titre{pluriel} sélectionné{pluriel} ({n}/{len(visibles)})"


def etat_coche_entete(selection: set, visibles: list[LigneMorceau]) -> str:
    """« vide » / « partielle » / « pleine » pour la coche d'en-tête : elle porte
    sur les lignes VISIBLES et cochables (ni désactivées ni sans identifiant)."""
    cochables = [ligne for ligne in visibles if ligne.cochable]
    n = sum(1 for ligne in cochables if ligne.id in selection)
    if not cochables or n == 0:
        return "vide"
    return "pleine" if n == len(cochables) else "partielle"
