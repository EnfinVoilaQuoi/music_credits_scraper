"""Ce qui est montré dans la table : catégories, recherche, manque d'un champ.

Valeurs par défaut = la table CustomTkinter actuelle, qui montre TOUT (décision
utilisateur 2026-10-11 : les maquettes ne montraient que la différence visuelle
coché / pas coché).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from src.presentation.format import normalize_text
from src.presentation.table import LigneMorceau
from src.utils.participation import Participation
from src.utils.track_validation import Manque


class ChampPied(StrEnum):
    """Les champs comptés dans le pied, dans l'ordre affiché."""

    DATE = "Date"
    DUREE = "Durée"
    CREDITS = "Crédits"
    PAROLES = "Paroles"
    AUDIO = "BPM/Key/Mode"
    STREAMS = "Streams"


#: Les `Manque` de la validation que chaque champ du pied regroupe — la table
#: n'a qu'une colonne pour les paroles (texte ET timestamps) et une pour l'audio.
MANQUES_DU_CHAMP: dict[ChampPied, frozenset[Manque]] = {
    ChampPied.DATE: frozenset({Manque.DATE}),
    ChampPied.DUREE: frozenset({Manque.DUREE}),
    ChampPied.CREDITS: frozenset({Manque.CREDITS}),
    ChampPied.PAROLES: frozenset({Manque.PAROLES, Manque.TIMESTAMPS}),
    ChampPied.AUDIO: frozenset({Manque.BPM, Manque.KEY_MODE}),
    ChampPied.STREAMS: frozenset({Manque.STREAMS}),
}


def manque_le_champ(ligne: LigneMorceau, champ: ChampPied) -> bool:
    """La ligne compte dans les morceaux à valider ET il lui manque ce champ."""
    if not ligne.constat.compte_a_valider:
        return False
    return bool(MANQUES_DU_CHAMP[champ] & set(ligne.constat.manques))


@dataclass(frozen=True)
class Filtre:
    principal: bool = True
    feat: bool = True
    prod: bool = True
    secondaire: bool = True
    #: Attributs transversaux : décochés, ils retirent TOUTE ligne qui les porte.
    inedit: bool = True
    instrumental: bool = True
    version_alt: bool = True
    desactives: bool = True
    #: Recherche Ctrl+F (titre, album, artiste, crédits).
    texte: str = ""
    #: Clic sur un compteur du pied : seulement les lignes qui manquent ce champ.
    manque: ChampPied | None = None

    def categorie_visible(self, p: Participation) -> bool:
        return {
            Participation.PRINCIPAL: self.principal,
            Participation.FEAT: self.feat,
            Participation.PROD: self.prod,
            Participation.SECONDAIRE: self.secondaire,
        }[p]

    def avec(self, **changements) -> Filtre:
        return replace(self, **changements)

    @property
    def categories_reduites(self) -> bool:
        """Une case du filtre est-elle décochée ? (indicateur du bouton filtre)"""
        return not all(
            (
                self.principal,
                self.feat,
                self.prod,
                self.secondaire,
                self.inedit,
                self.instrumental,
                self.version_alt,
                self.desactives,
            )
        )


def garde(ligne: LigneMorceau, filtre: Filtre) -> bool:
    """La ligne est-elle montrée ?"""
    if not filtre.categorie_visible(ligne.participation):
        return False
    if ligne.inedit and not filtre.inedit:
        return False
    if ligne.instrumental and not filtre.instrumental:
        return False
    if ligne.version_alt and not filtre.version_alt:
        return False
    if ligne.desactive and not filtre.desactives:
        return False
    if filtre.manque is not None and not manque_le_champ(ligne, filtre.manque):
        return False
    texte = normalize_text(filtre.texte.strip())
    return not (texte and not all(mot in ligne.texte_recherche for mot in texte.split()))


def filtrer(lignes, filtre: Filtre) -> list[LigneMorceau]:
    return [ligne for ligne in lignes if garde(ligne, filtre)]
