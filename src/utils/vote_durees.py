"""Vote des catalogues sur une durée (2026-10-13). Module PUR.

Deux durées en désaccord ne disent pas laquelle est fausse. Les sources, si :
on compte les CATALOGUES indépendants qui donnent chaque valeur. Mesuré sur
les 238 litiges de durée du panneau « À trancher » : dans 179 cas, la majorité
désigne la valeur fausse — souvent pas celle que le détecteur accusait (Diam's
« Vice Versa » : Spotify, Deezer et Apple à 240 s, seul l'audio YouTube à
277 s ; c'est la vidéo, pas l'ID Spotify, qui est d'une autre version).

- Un catalogue ne compte qu'UNE fois : ReccoBeats rend la durée Spotify,
  YTM celle de la vidéo YouTube.
- Les sources FRAGILES ne prouvent rien : `legacy` (sans provenance),
  `songbpm` (page trouvée par une recherche de titre), `discogs` (saisie de
  contributeurs : « 100-8 Zoo » 402 s pour 274 s partout ailleurs). Elles
  peuvent contredire, jamais soutenir.
- Une saisie MANUELLE tranche seule.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Sources qui lisent le même catalogue : une seule voix.
FAMILLE_DE_DUREE = {
    "spotify_web": "spotify",
    "reccobeats": "spotify",
    "youtube_video": "youtube",
    "ytmusic": "youtube",
}

#: Sources qui ne prouvent rien (elles peuvent contredire, jamais soutenir).
FRAGILES = frozenset({"legacy", "songbpm", "discogs"})

#: Hors de tout vote : l'héritage recopie une autre fiche.
HORS_VOTE = frozenset({"heritage"})

#: Écart toléré entre deux catalogues (éditions, encodages).
TOLERANCE_S = 5

SOUTENUE = "soutenue"  # ≥ 2 catalogues pour, aucun catalogue solide contre
MAJORITAIRE = "majoritaire"  # ≥ 2 pour, UN seul catalogue solide contre
ISOLEE = "isolee"  # ≥ 2 catalogues solides contre, au plus un pour (elle-même)
INDECISE = "indecise"


def famille(source: str) -> str:
    return FAMILLE_DE_DUREE.get(source, source)


@dataclass
class Vote:
    nature: str
    pour: set = field(default_factory=set)
    contre: dict = field(default_factory=dict)  # catalogue solide → durée
    fragiles_contre: dict = field(default_factory=dict)  # source fragile → durée

    def dementie_par(self) -> str:
        """« youtube 277 s, apple_music 238 s » — pour un motif."""
        return ", ".join(f"{f} {d} s" for f, d in sorted(self.contre.items()))


def voter(durees: dict, valeur: int | None, tolerance: int = TOLERANCE_S) -> Vote | None:
    """PUR. Le vote des catalogues de `durees` ({source: secondes}) sur `valeur`.
    None si `valeur` est absente."""
    if not valeur:
        return None
    valeur = int(valeur)
    pour, contre, fragiles = set(), {}, {}
    manuelle = durees.get("manual")
    for source, d in durees.items():
        if not d or source in HORS_VOTE or source == "manual":
            continue
        accord = abs(int(d) - valeur) <= tolerance
        if source in FRAGILES:
            if not accord:
                fragiles[source] = int(d)
        elif accord:
            pour.add(famille(source))
        else:
            contre.setdefault(famille(source), int(d))
    # Un catalogue en désaccord avec LUI-MÊME (ReccoBeats 181 s, page Spotify
    # 210 s pour « Garcimore ») ne soutient rien : il reste du côté du doute.
    pour -= set(contre)
    if manuelle:
        nature = SOUTENUE if abs(int(manuelle) - valeur) <= tolerance else ISOLEE
    elif len(pour) >= 2 and not contre:
        nature = SOUTENUE
    elif len(pour) >= 2 and len(contre) == 1:
        nature = MAJORITAIRE
    elif len(contre) >= 2 and len(pour) <= 1:
        nature = ISOLEE
    else:
        nature = INDECISE
    return Vote(nature, pour, contre, fragiles)
