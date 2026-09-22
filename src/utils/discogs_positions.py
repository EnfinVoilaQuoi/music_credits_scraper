"""À quelles PISTES d'un disque un crédit Discogs s'applique-t-il ?

Discogs place la plupart des crédits au niveau du DISQUE, pas du morceau :
`release.extraartists` liste « Mr Hudson — Vocals — tracks: C2 ». Le champ
`tracks` dit à quelles pistes le crédit se rapporte, et il est **vide quand le
crédit vaut pour tout le disque** (le producteur exécutif, le label, le
graphiste).

Mesuré le 2026-09-22 : ce champ était stocké mais jamais LU, si bien que chaque
crédit de disque était recopié sur chaque morceau — « Mr Hudson, crédité sur la
piste C2 » se retrouvait sur 14 morceaux de *808s & Heartbreak*, et les 16
auteurs de *Man on the Moon II* sur ses 15 titres. **1 460 attributions en trop
sur 2 094 lignes** portant une liste de pistes.

Module PUR : la syntaxe de Discogs est une donnée de la source, elle se teste
sans réseau ni base.
"""

from __future__ import annotations

import re

#: « A1 », « B12 », « 3 », « CD1-4 »… Une position = un préfixe de face ou de
#: disque (facultatif) suivi d'un numéro.
_POSITION = re.compile(r"^(?P<prefixe>[A-Za-z]*(?:\d+-)?)(?P<numero>\d+)$")

#: « B1 to B3 », « 1 to 4 » — Discogs écrit ses plages en toutes lettres.
_PLAGE = re.compile(r"^(?P<debut>\S+)\s+to\s+(?P<fin>\S+)$", re.IGNORECASE)


def _normaliser(position: str) -> str:
    return (position or "").strip().upper()


def _developper_plage(borne_a: str, borne_b: str) -> set[str] | None:
    """« B1 to B3 » → {B1, B2, B3}. None si les bornes ne se comparent pas."""
    a, b = _POSITION.match(borne_a), _POSITION.match(borne_b)
    if not a or not b or a["prefixe"] != b["prefixe"]:
        return None
    debut, fin = int(a["numero"]), int(b["numero"])
    if fin < debut:
        return None
    return {f"{a['prefixe']}{n}" for n in range(debut, fin + 1)}


def positions_citees(tracks: str | None) -> set[str] | None:
    """Les positions nommées par un champ `tracks` Discogs.

    Rend `None` quand le crédit ne cible AUCUNE piste en particulier — champ
    vide (il vaut alors pour tout le disque) ou syntaxe non reconnue. Dans les
    deux cas l'appelant doit rester permissif : un crédit qu'on ne sait pas
    situer se garde, on ne le perd pas sur un doute de syntaxe.
    """
    texte = (tracks or "").strip()
    if not texte:
        return None

    positions: set[str] = set()
    for morceau in re.split(r"[,;/]", texte):
        element = _normaliser(morceau)
        if not element:
            continue
        plage = _PLAGE.match(element)
        if plage:
            developpee = _developper_plage(plage["debut"], plage["fin"])
            if developpee is None:
                return None  # plage illisible : on ne conclut pas
            positions |= developpee
        elif _POSITION.match(element):
            positions.add(element)
        else:
            return None  # position illisible : on ne conclut pas
    return positions or None


def concerne_la_piste(tracks: str | None, position: str | None) -> bool:
    """Ce crédit se rapporte-t-il à CETTE piste du disque ?

    Permissif par construction — il ne répond `False` que lorsque Discogs a
    nommé des pistes ET que celle-ci n'en fait pas partie. Un champ vide, une
    syntaxe inconnue ou une position de piste que l'on ignore laissent le
    crédit en place : la faute mesurée était l'attribution en trop, mais perdre
    un crédit juste parce qu'on n'a pas su lire une position serait pire.
    """
    citees = positions_citees(tracks)
    if citees is None:
        return True
    position = _normaliser(position or "")
    if not position:
        return True
    return position in citees
