"""La durée de l'audio des LRC lus AVANT qu'on la garde, retrouvée dans le brut
(2026-10-27).

`lrc_duree_audio` (`lrc_garde.CHAMP_DUREE_AUDIO`) n'est posée à la lecture que
depuis `686e610` : les LRC d'avant n'en ont pas, et la règle « un LRC ne court
pas au-delà de l'audio de SA source » (`lrc_garde.deborde_son_audio`) ne les
voit pas. Le brut (`persistence.brut`) garde les réponses LRCLIB, qui portent
`duration`.

La réponse qui a servi un LRC se reconnaît à TROIS choses à la fois : le même
texte synchronisé, une requête faite pour CE morceau (`track_name` = le titre de
la fiche, celui que le résolveur envoie) et une lecture du même jour que le LRC.
Le texte seul ne suffit pas : LRCLIB recopie un même LRC sur plusieurs
enregistrements (mesuré sur la base le 2026-10-27 : le LRC de « Whole Lotta
Lovin' », 299 s, est aussi celui du remix LeMarquis, 226 s ; celui de « New
Again », 183 s, celui de l'écoute DONDA, 100 s — les deux catalogues de la fiche
démentaient la durée prise au remix).

Rien n'est deviné (« Absent ≠ échec ») : sans réponse qui remplit les trois
conditions, ou si les entrées qui la remplissent ont des durées différentes (la
recherche rend des doublons : laquelle a été choisie ne se lit plus), aucune
durée. YouTube Music n'est pas gardé au brut : ses LRC restent sans durée.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from urllib.parse import parse_qs, urlsplit

#: Sources dont le brut porte la durée de l'audio d'un LRC.
SOURCES_DU_BRUT = ("lrclib",)

#: Un run qui passe minuit : la lecture du brut et le `seen_at` du LRC peuvent
#: tomber sur deux jours voisins.
_MARGE = timedelta(days=1)


def cle_lrc(lrc: str | None) -> str:
    """PUR. Le texte synchronisé, aux fins de ligne près."""
    return (lrc or "").replace("\r\n", "\n").strip()


@dataclass(frozen=True)
class Entree:
    """Une entrée LRCLIB portant un LRC, dans une lecture gardée."""

    duree: int | None
    #: `track_name` de la requête qui l'a rendue.
    titre_demande: str
    premiere: date | None
    derniere: date | None


def _jour(horodatage) -> date | None:
    try:
        return date.fromisoformat(str(horodatage or "").replace(" ", "T")[:10])
    except ValueError:
        return None


def audios_lrclib(lectures) -> dict[str, list[Entree]]:
    """PUR. `{texte LRC: [Entree]}` depuis les lectures LRCLIB gardées
    (`brut.parcourir("lrclib")` : `(requete, corps, premiere, derniere)`) —
    `/get` rend un objet, `/search` une liste."""
    index: dict[str, list[Entree]] = {}
    for requete, corps, premiere, derniere in lectures:
        try:
            lu = json.loads(corps)
        except (ValueError, TypeError):
            continue
        titre = (parse_qs(urlsplit(requete).query).get("track_name") or [""])[0]
        for obj in lu if isinstance(lu, list) else [lu]:
            if not isinstance(obj, dict) or not obj.get("syncedLyrics"):
                continue
            duree = obj.get("duration")
            duree = round(duree) if isinstance(duree, int | float) and duree > 0 else None
            index.setdefault(cle_lrc(obj["syncedLyrics"]), []).append(
                Entree(duree, titre, _jour(premiere), _jour(derniere))
            )
    return index


def _meme_lecture(entree: Entree, jour: date | None) -> bool:
    if jour is None or entree.premiere is None or entree.derniere is None:
        return False
    return entree.premiere - _MARGE <= jour <= entree.derniere + _MARGE


@dataclass
class Plan:
    #: `(track_id, source, lrc, seen_at, duree)` — l'entrée de
    #: `TrackRepository.poser_durees_audio_lrc`.
    faits: list = field(default_factory=list)
    #: Par issue : posee, absent_du_brut, autre_morceau, autre_lecture,
    #: durees_divergentes, sans_duree, source_sans_brut:<source>.
    issues: Counter = field(default_factory=Counter)
    #: `(track_id, durees)` des LRC dont la réponse porte plusieurs durées.
    divergents: list = field(default_factory=list)


def planifier(observations, index_par_source: dict[str, dict[str, list[Entree]]]) -> Plan:
    """PUR. `observations` : `(track_id, source, lrc, seen_at, titre)` des LRC
    sans durée d'audio. Rend ce qui se pose, et pourquoi le reste ne se pose
    pas (une issue par LRC, la plus proche du but)."""
    plan = Plan()
    for track_id, source, lrc, seen_at, titre in observations:
        index = index_par_source.get(source)
        if index is None:
            plan.issues[f"source_sans_brut:{source}"] += 1
            continue
        entrees = index.get(cle_lrc(lrc))
        if not entrees:
            plan.issues["absent_du_brut"] += 1
            continue
        siennes = [e for e in entrees if e.titre_demande == titre]
        if not siennes:
            plan.issues["autre_morceau"] += 1
            continue
        jour = _jour(seen_at)
        servies = [e for e in siennes if _meme_lecture(e, jour)]
        if not servies:
            plan.issues["autre_lecture"] += 1
            continue
        durees = sorted({e.duree for e in servies if e.duree})
        if not durees:
            plan.issues["sans_duree"] += 1
        elif len(durees) > 1:
            plan.issues["durees_divergentes"] += 1
            plan.divergents.append((track_id, durees))
        else:
            plan.issues["posee"] += 1
            plan.faits.append((track_id, source, lrc, seen_at, durees[0]))
    return plan
