"""Des trous signalés par la validation aux commandes qui les comblent.

Les validateurs savent DIRE quels mois manquent ; rien ne savait aller les
chercher. Le chaînon manquant tient en une traduction : une liste de mois
« AAAA-MM » vers les invocations que chaque source comprend.

Les quatre sources ne se visent pas à la même MAILLE, et c'est le site qui
l'impose, pas nous :
  · SNEP  — par ANNÉE (`--year`, répétable) : l'export est annuel.
  · RIAA  — par PÉRIODE (`--from`/`--to`) : la recherche prend deux dates, on
    peut donc viser le mois exact.
  · BRMA  — par NOMBRE D'ANNÉES EN ARRIÈRE (`--years-back`) : Ultratop n'expose
    pas d'année isolée, on remonte donc jusqu'à la plus ancienne demandée, ce
    qui rescrape aussi les années intermédiaires.
  · BPI   — par PÉRIODE (`--from`/`--to`), comme la RIAA : le site prend deux
    dates, on vise donc le mois exact.

⚠️ **Le cas BPI mérite un avertissement**, parce que sa maille est trompeuse :
sa fenêtre de dates ne filtre que la DERNIÈRE certification d'un titre. Un mois
ancien peut donc rester « troué » après rescrape, non par échec mais parce que
les titres certifiés ce mois-là ont été réhaussés depuis et ont migré vers leur
date récente. Pour reconstituer l'historique, c'est `--full` qu'il faut, pas un
rescrape mensuel.

Module PUR : il ne lance rien, il construit des commandes. C'est ce qui le rend
testable sans réseau — et vérifiable de l'œil avant de lancer quoi que ce soit.
"""

from __future__ import annotations

import sys
from datetime import date


def _mois_suivant(annee: int, mois: int) -> str:
    """Premier jour du mois suivant, borne HAUTE exclusive de la période."""
    return f"{annee + 1}-01-01" if mois == 12 else f"{annee}-{mois + 1:02d}-01"


def annees(gaps: list[str]) -> list[int]:
    """Années distinctes concernées par une liste de « AAAA-MM », triées."""
    trouvees = set()
    for gap in gaps:
        tete = str(gap).strip()[:4]
        if tete.isdigit():
            trouvees.add(int(tete))
    return sorted(trouvees)


def _par_mois(python: str, script: str, gaps: list[str]) -> list[list[str]]:
    """Une invocation `--from`/`--to` par mois, borne haute EXCLUSIVE."""
    sorties = []
    for gap in sorted(set(gaps)):
        morceaux = str(gap).strip().split("-")
        if len(morceaux) < 2 or not (morceaux[0].isdigit() and morceaux[1][:2].isdigit()):
            continue
        an, mo = int(morceaux[0]), int(morceaux[1][:2])
        if not 1 <= mo <= 12:
            continue
        sorties.append(
            [python, script, "--from", f"{an}-{mo:02d}-01", "--to", _mois_suivant(an, mo)]
        )
    return sorties


def commandes(source: str, gaps: list[str], script: str, python: str = "") -> list[list[str]]:
    """Commandes à lancer pour combler `gaps` sur cette source.

    Une liste vide signifie « rien à faire » ou « source non ciblable ».
    """
    python = python or sys.executable
    concernees = annees(gaps)
    if not concernees:
        return []

    source = source.upper()
    if source == "SNEP":
        # Une seule invocation : `--year` est répétable.
        args = [a for annee in concernees for a in ("--year", str(annee))]
        return [[python, script, *args]]

    if source in ("RIAA", "BPI"):
        # Le mois exact, borne haute exclusive. Les deux sources prennent la
        # même paire de dates : une seule construction, pas deux jumelles.
        return _par_mois(python, script, gaps)

    if source == "BRMA":
        # Ultratop ne sait pas viser une année : on remonte jusqu'à la plus
        # ancienne demandée, en rescrapant tout ce qui est entre les deux.
        recul = date.today().year - min(concernees) + 1
        return [[python, script, "--mode", "once", "--years-back", str(recul)]]

    return []


def resume(source: str, gaps: list[str]) -> str:
    """Phrase décrivant ce qui va être relancé, à montrer AVANT de lancer."""
    concernees = annees(gaps)
    if not concernees:
        return "Aucune période à rescraper."
    if source.upper() == "RIAA":
        return f"{len(set(gaps))} mois à rescraper, un par un ({concernees[0]}–{concernees[-1]})."
    if source.upper() == "BPI":
        return (
            f"{len(set(gaps))} mois à rescraper, un par un ({concernees[0]}–{concernees[-1]}). "
            "⚠️ La fenêtre BPI ne rend que la DERNIÈRE certification d'un titre : "
            "un mois ancien peut rester troué parce que ses titres ont été "
            "réhaussés depuis. Pour l'historique, lancer un balayage complet."
        )
    if source.upper() == "SNEP":
        return f"{len(concernees)} année(s) à recharger entièrement : {', '.join(map(str, concernees))}."
    if source.upper() == "BRMA":
        recul = date.today().year - min(concernees) + 1
        return (
            f"Ultratop ne cible pas une année : remontée sur {recul} an(s), "
            f"soit depuis {min(concernees)}."
        )
    return "Source non ciblable."


__all__ = ["annees", "commandes", "resume"]
