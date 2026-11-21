"""La portée d'un run lancé depuis l'interface : ce que coche le menu de chaque
bouton de flux, traduit en `cycle.OptionsCycle`.

Maquettes du 2026-11-21 (décisions utilisateur) : chaque bouton de flux a une
flèche qui ouvre un menu à cocher — Titres sélectionnés, Titres désactivés,
Tout, Manquants, Échecs, Forcer —, choix mémorisé ; Run Complet a son propre
menu, centré sur les délais de relecture (Manquants + échecs, Échecs seuls,
Ignorer les délais, Tout relire).

Module PUR : aucun Qt, aucune base. La fenêtre lit les cases, ce module dit ce
qui part — ou pourquoi rien ne part.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from enum import StrEnum

from src.services import cycle

#: Les étapes qui prennent une liste de fiches (`track_ids`). La discographie
#: et les certifications travaillent par ARTISTE : la sélection n'y a pas de sens.
ETAPES_PAR_FICHES = frozenset({"identite", "credits", "enrich", "streams"})
#: Les étapes qui savent FORCER (redemander ce qu'elles ont déjà). Les streams
#: relisent toujours leurs compteurs ; la discographie relit toujours tout.
ETAPES_FORCABLES = frozenset({"identite", "credits", "enrich"})
#: Les étapes qui restreignent aux manquants / échecs (`runtime.restriction`).
ETAPES_RESTREINTES = frozenset({"credits", "enrich", "streams"})

#: Une option que le menu montre mais que les services ne savent pas encore
#: faire : cochée, elle ne doit rien changer en silence — elle est grisée.
OPTIONS_A_VENIR = frozenset({"desactives"})


@dataclass(frozen=True)
class PorteeFlux:
    """Les cases du menu d'un bouton de flux."""

    selection: bool = False
    desactives: bool = False
    tout: bool = False
    manquants: bool = True
    echecs: bool = True
    forcer: bool = False

    def avec(self, **changements) -> PorteeFlux:
        p = replace(self, **changements)
        # « Tout » et le couple manquants / échecs s'excluent : cocher l'un
        # décoche l'autre, et rien de coché revient au défaut (les deux).
        if changements.get("tout"):
            p = replace(p, manquants=False, echecs=False)
        elif changements.get("manquants") or changements.get("echecs"):
            p = replace(p, tout=False)
        if not (p.tout or p.manquants or p.echecs):
            p = replace(p, manquants=True, echecs=True)
        return p

    def en_texte(self) -> dict[str, bool]:
        return {
            "selection": self.selection,
            "desactives": self.desactives,
            "tout": self.tout,
            "manquants": self.manquants,
            "echecs": self.echecs,
            "forcer": self.forcer,
        }

    @classmethod
    def depuis_texte(cls, valeurs: dict) -> PorteeFlux:
        def oui(cle: str, defaut: bool) -> bool:
            v = valeurs.get(cle, defaut)
            return v in (True, "true", "1", 1) if not isinstance(v, bool) else v

        base = cls()
        return cls(
            selection=oui("selection", base.selection),
            desactives=oui("desactives", base.desactives),
            tout=oui("tout", base.tout),
            manquants=oui("manquants", base.manquants),
            echecs=oui("echecs", base.echecs),
            forcer=oui("forcer", base.forcer),
        ).avec()


class ModeComplet(StrEnum):
    """Le menu de Run Complet (validé le 2026-11-22)."""

    MANQUANTS_ECHECS = "manquants_echecs"
    ECHECS = "echecs"
    #: Redemander les « pas sur Spotify / Deezer / Apple » et les absences
    #: récentes : les services n'ont pas encore d'option pour lever les délais.
    IGNORER_DELAIS = "ignorer_delais"
    TOUT = "tout"


LIBELLES_COMPLET = {
    ModeComplet.MANQUANTS_ECHECS: "Manquants + échecs",
    ModeComplet.ECHECS: "Échecs seuls",
    ModeComplet.IGNORER_DELAIS: "Ignorer les délais",
    ModeComplet.TOUT: "Tout relire",
}
MODES_A_VENIR = frozenset({ModeComplet.IGNORER_DELAIS})


class RunImpossible(ValueError):
    """Le run ne part pas ; le message dit pourquoi, à l'utilisateur."""


def options_flux(etape: str, portee: PorteeFlux, coches: Iterable[int]) -> cycle.OptionsCycle:
    """Les options du cycle pour UNE étape lancée par son bouton."""
    if etape not in cycle.ETAPES:
        raise ValueError(f"étape inconnue : {etape!r}")
    ids: frozenset[int] | None = None
    if portee.selection and etape in ETAPES_PAR_FICHES:
        ids = frozenset(i for i in coches if i is not None)
        if not ids:
            raise RunImpossible(
                "Aucun titre sélectionné. Cochez des titres dans la table, "
                "ou décochez « Titres sélectionnés » dans le menu du bouton."
            )
    forcer = portee.forcer and etape in ETAPES_FORCABLES
    options = cycle.OptionsCycle(
        only=(etape,),
        tout=portee.tout,
        # Les deux cochés = le défaut des services (aucun des deux demandé).
        manquants=portee.manquants and not portee.echecs,
        echecs=portee.echecs and not portee.manquants,
    )
    if etape == "identite":
        options = replace(options, identite=replace(options.identite, track_ids=ids, force=forcer))
    elif etape == "credits":
        options = replace(
            options,
            credits=replace(
                options.credits, track_ids=ids, force_credits=forcer, force_paroles=forcer
            ),
        )
    elif etape == "enrich":
        options = replace(
            options, enrich=replace(options.enrich, track_ids=ids, force_update=forcer)
        )
    elif etape == "streams":
        options = replace(options, streams=replace(options.streams, track_ids=ids, fiches=ids))
    return options


def options_complet(mode: ModeComplet) -> cycle.OptionsCycle:
    """Les options du cycle entier selon le menu de Run Complet."""
    if mode in MODES_A_VENIR:
        raise RunImpossible(f"« {LIBELLES_COMPLET[mode]} » n'est pas encore disponible.")
    return cycle.OptionsCycle(
        tout=mode is ModeComplet.TOUT,
        echecs=mode is ModeComplet.ECHECS,
    )


def resume_portee(etape: str, portee: PorteeFlux, n_coches: int) -> str:
    """Une ligne qui dit ce qui va partir (infobulle et confirmation)."""
    if portee.selection and etape in ETAPES_PAR_FICHES:
        quoi = f"{n_coches} titre{'s' if n_coches > 1 else ''} sélectionné{'s' if n_coches > 1 else ''}"
    else:
        quoi = "toute la discographie"
    if etape not in ETAPES_RESTREINTES and etape != "identite":
        critere = ""
    elif portee.tout:
        critere = ", tous les morceaux"
    elif portee.manquants and portee.echecs:
        critere = ", données manquantes et échecs"
    elif portee.manquants:
        critere = ", données manquantes seulement"
    else:
        critere = ", échecs seulement"
    forcer = " — forcé" if portee.forcer and etape in ETAPES_FORCABLES else ""
    return f"{quoi}{critere}{forcer}"
