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


#: « 1-15 » : disque 1 piste 15, OU les pistes 1 à 15 — les deux s'écrivent.
_TIRET = re.compile(r"^(?P<debut>\d+)-(?P<fin>\d+)$")


def positions_citees(tracks: str | None, *, tiret_plage: bool = False) -> set[str] | None:
    """Les positions nommées par un champ `tracks` Discogs.

    Rend `None` quand le crédit ne cible AUCUNE piste en particulier — champ
    vide (il vaut alors pour tout le disque) ou syntaxe non reconnue. Dans les
    deux cas l'appelant doit rester permissif : un crédit qu'on ne sait pas
    situer se garde, on ne le perd pas sur un doute de syntaxe.

    `tiret_plage` : le disque numérote ses pistes SANS disque (« 6 »), donc
    « 1-15 » y est une PLAGE et non la piste 15 du disque 1 (2026-09-29 :
    Empty7, compositeur « 1-15 » d'un album de Django, jugé hors de la piste 6).
    """
    texte = (tracks or "").strip()
    if not texte:
        return None

    positions: set[str] = set()
    for morceau in re.split(r"[,;/]", texte):
        element = _normaliser(morceau)
        if not element:
            continue
        plage = _PLAGE.match(element) or (_TIRET.match(element) if tiret_plage else None)
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
    position = _normaliser(position or "")
    if not position:
        return True
    citees = positions_citees(tracks, tiret_plage="-" not in position)
    if citees is None:
        return True
    # Deux numérotations sur un même disque (« Pinocchio Story » piste « 12 »,
    # crédits cités « A2, B1 to B3, D2 » : tracklist de CD, crédits de vinyle) :
    # aucune position citée n'est comparable à la nôtre, on ne conclut pas.
    if not any(_a_des_lettres(c) == _a_des_lettres(position) for c in citees):
        return True
    return position in citees


def _a_des_lettres(position: str) -> bool:
    return any(ch.isalpha() for ch in position)
