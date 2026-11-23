"""La fenêtre Alias • Formations, sans widget (maquette du 2026-11-21).

Trois cartes : Alias (noms de scène, graphies, indices), Nom légal (état
civil), Groupe • Collectif (formations dont l'artiste est membre, et membres
quand l'artiste est une formation). Convention des décisions, validée le
2026-11-22 et commune à toute l'app : à décider = ✓ et ✖ PLEINS ; décidé = le
seul carré choisi, en contour ; un clic sur lui rétablit.

Les candidats viennent de `utils.formations` (base, puis « Rech. Manuelle »).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from src.utils.formations import Candidat


class Decision(StrEnum):
    A_DECIDER = "a_decider"
    CONFIRME = "confirme"
    REFUSE = "refuse"
    #: « Pour info » (état civil, indice, graphie non proposés) : rien
    #: n'attend, les deux carrés restent offerts en contour.
    INFO = "info"


_PAR_SECTION = {
    "proposed": Decision.A_DECIDER,
    "confirmed": Decision.CONFIRME,
    "refused": Decision.REFUSE,
    "info": Decision.INFO,
}

CARTE_ALIAS = "Alias"
CARTE_NOM_LEGAL = "Nom légal"
CARTE_FORMATIONS = "Groupe • Collectif"

TEXTE_ALIAS = "Autre(s) pseudo(s) sous le(s)quel(s) l'artiste a produit de la musique"
TEXTE_FORMATIONS = (
    "Tout titre d'un groupe associé est considéré comme appartenant à la discographie "
    "principale de l'artiste au même titre que ses morceaux solos ; d'un collectif, "
    "seulement ceux où il est présent."
)


@dataclass(frozen=True)
class LigneFormation:
    candidat: Candidat
    decision: Decision
    #: « groupe » / « collectif » / None — seulement pour une formation.
    nature: str | None
    #: Ce que la ligne dit en plus de son nom (« a pour membre », sources…).
    detail: str = ""

    @property
    def nom(self) -> str:
        return self.candidat.related_name

    @property
    def est_formation(self) -> bool:
        return self.candidat.kind in ("member_of", "has_member")


def section_de(candidat: Candidat) -> str:
    """La section d'un candidat. Un candidat NEUF non proposable (variante de
    graphie, état civil…) va « pour info » — il était rangé avec les proposés et
    enregistré en `proposed` s'il était laissé tel quel (20 variantes en base,
    2026-09-23). Seul juge, partagé avec la fenêtre CustomTkinter."""
    if candidat.status is None:
        return "proposed" if candidat.proposable else "info"
    return candidat.status


def decision_de(candidat: Candidat) -> Decision:
    return _PAR_SECTION[section_de(candidat)]


def _detail(c: Candidat) -> str:
    morceaux = []
    if c.kind == "has_member":
        morceaux.append("a pour membre")
    if c.sources:
        morceaux.append("deux sources" if c.croise else " · ".join(sorted(c.sources)))
    periode = " → ".join(x for x in (c.begin_date, c.end_date) if x)
    if periode:
        morceaux.append(periode)
    return " • ".join(morceaux)


def cartes(candidats: list[Candidat]) -> dict[str, list[LigneFormation]]:
    """Les lignes de chaque carte, à décider d'abord, puis par nom."""
    res: dict[str, list[LigneFormation]] = {
        CARTE_ALIAS: [],
        CARTE_NOM_LEGAL: [],
        CARTE_FORMATIONS: [],
    }
    for c in candidats:
        ligne = LigneFormation(
            c, decision_de(c), c.formation if c.kind != "alias" else None, _detail(c)
        )
        if c.kind == "alias":
            carte = CARTE_NOM_LEGAL if c.nature == "etat_civil" else CARTE_ALIAS
        else:
            carte = CARTE_FORMATIONS
        res[carte].append(ligne)
    ordre = list(Decision)
    for lignes in res.values():
        lignes.sort(key=lambda lg: (ordre.index(lg.decision), lg.nom.casefold()))
    return res


def a_decider(candidats: list[Candidat]) -> int:
    """Le compte de la pastille « ! » de la barre du haut."""
    return sum(1 for c in candidats if decision_de(c) is Decision.A_DECIDER)
