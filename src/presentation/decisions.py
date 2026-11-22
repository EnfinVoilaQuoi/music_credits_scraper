"""Le panneau Décisions • Erreurs (icône ⚠ de la barre du haut), sans widget.

Pendant Qt de « À trancher » (CustomTkinter) : la logique vit dans
`services.revue` (détecteurs), `services.revue_actions` (gestes) et
`services.revue_auto` (corrections à preuve formelle) ; ici, les vues, les
libellés et les comptes. Rappel (décision utilisateur 2026-10-26) : le code
tranche, l'humain ne voit que l'indécidable — la vue par défaut ne montre que
les DÉCISIONS.
"""

from __future__ import annotations

from enum import StrEnum

from src.services.revue import DECISION, INFORMATION, RUN, Cas, Revue


class Vue(StrEnum):
    DECISIONS = "decisions"
    RUN = "run"
    INFO = "info"
    NORMAUX = "normaux"
    JOURNAL = "journal"


NATURE_DE_LA_VUE = {Vue.DECISIONS: DECISION, Vue.RUN: RUN, Vue.INFO: INFORMATION}

#: Au-delà, l'affichage deviendrait lent ; le tri par impact met en haut ce qui compte.
MAX_LIGNES = 300


def format_impact(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} M"
    if n >= 1_000:
        return f"{n / 1_000:.0f} k"
    return str(n) if n else "—"


def cas_de_la_vue(revue: Revue, vue: Vue) -> list[Cas]:
    if vue is Vue.NORMAUX:
        return list(revue.masques)
    nature = NATURE_DE_LA_VUE.get(vue, DECISION)
    return [c for c in revue.actifs if c.nature == nature]


def libelles_vues(revue: Revue, journal: list) -> dict[Vue, str]:
    def n(nature: str) -> int:
        return sum(1 for c in revue.actifs if c.nature == nature)

    return {
        Vue.DECISIONS: f"À trancher ({n(DECISION)})",
        Vue.RUN: f"Un run tranchera ({n(RUN)})",
        Vue.INFO: f"Pour information ({n(INFORMATION)})",
        Vue.NORMAUX: f"Marqués normaux ({len(revue.masques)})",
        Vue.JOURNAL: f"Corrections auto ({len(journal)})",
    }


def titre_de_la_vue(vue: Vue, n: int) -> str:
    return {
        Vue.DECISIONS: f"{n} cas à trancher — triés par impact",
        Vue.RUN: f"{n} cas qu'un run tranchera — rien à décider",
        Vue.INFO: f"{n} cas déjà neutralisés — pour information",
        Vue.NORMAUX: f"{n} cas marqué(s) normal(aux)",
        Vue.JOURNAL: f"{n} correction(s) faite(s) sans vous — preuve formelle",
    }[vue]


def a_trancher(revue: Revue) -> int:
    """Le compte de la pastille « ! » : les seules DÉCISIONS."""
    return sum(1 for c in revue.actifs if c.nature == DECISION)
