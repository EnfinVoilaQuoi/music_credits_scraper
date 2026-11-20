"""Projets : les ÉDITIONS d'un même disque réunies (e67, plan du 2026-11-20).

Une parution (`releases`) est une ÉDITION ; un PROJET est ce que l'artiste a
sorti comme UN disque, toutes éditions confondues (*M.A.N* + *M.A.N (Bonus)*).
Le niveau projet porte ce qui n'appartient à aucune édition seule (nature,
streams d'album, crédits d'album Genius lus par l'édition) ; plan et mesures :
`therapie_studio/notes/scraper/projets_albums_2026-11-20.md`.

Règles (simulées sur la base entière le 2026-11-20 avant d'être gardées :
4 557 projets, 167 à plusieurs éditions ; échantillons vérifiés à la main) :

1. même code-barres (`cle_upc`) → même projet ;
2. toutes les fiches d'une édition (≥ 2) sont sur l'autre, au même nom de
   disque (`meme_disque`) → même projet ;
2bis. même nom de disque et ≥ 80 % des fiches de la plus petite en commun →
   même projet (décision utilisateur 2026-11-20, « Vas-y » : 30 / 30 justes) ;

jamais :
- avec une édition `single` (un single ne rejoint jamais l'album du même titre,
  décision du 2026-11-12) ;
- un live avec un studio (`releases.mb_live`) ;
- un best-of ou une compilation (titre) ;
- une SUITE qui reprend les précédentes : même nom de disque mais pas la même
  partie (« Chambre 140 (Part. 2) » / « (Part. 3) », « Or Noir » / « Or Noir
  Part 2 ») — un autre projet (décision utilisateur 2026-11-20) ; deux
  éditions aux mêmes fiches (≥ 80 % dans les DEUX sens) ne sont jamais une
  suite (« Bolide » / « Bolide Vol. 2 ») : une suite AJOUTE des morceaux.

Garde « édition cumulée » : un groupe où deux éditions d'au moins 3 fiches ne
partagent RIEN est recollé par une édition qui en cumule deux (« Abysses &
Ivresse des profondeurs », « The Documentary 2 + 2.5 ») : il n'est pas formé,
ses éditions restent chacune leur projet et le groupe est rendu « à trancher ».
Jamais de regroupement au seul titre.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from src.utils.codes_barres import cle_upc
from src.utils.version_descriptors import meme_disque, memes_parties_de_disque

#: Une compilation de l'artiste n'est pas une édition d'un de ses disques
#: (« Immortel (Mini Best Of) » de Sinik, fondu dans *Immortel* à la première
#: simulation). Liste FERMÉE.
_COMPILATION = re.compile(
    r"\b(best of|greatest hits|the hits|compilation|anthologie|anthology)\b", re.IGNORECASE
)

#: Part des fiches de la plus petite édition qu'il faut en commun (règle 2bis).
PART_COMMUNE = 0.8


@dataclass(frozen=True)
class Edition:
    """Une parution `own` confirmée, vue par le regroupement."""

    id: int
    titre: str
    record_type: str | None = None
    upc: str | None = None
    live: bool | None = None
    date: str | None = None
    pistes: frozenset[int] = frozenset()


@dataclass
class Regroupement:
    #: Les projets, chacun la liste triée des ID de ses éditions (singletons compris).
    projets: list[list[int]] = field(default_factory=list)
    #: Groupes NON formés (édition cumulée) : chacun la liste de ses éditions.
    a_trancher: list[list[int]] = field(default_factory=list)
    #: Paires séparées comme suites (même nom, autre partie).
    suites: list[tuple[int, int]] = field(default_factory=list)


def _jamais_ensemble(a: Edition, b: Edition) -> bool:
    if "single" in (a.record_type, b.record_type):
        return True
    if (a.live is True) != (b.live is True) and True in (a.live, b.live):
        return True
    return bool(_COMPILATION.search(a.titre) or _COMPILATION.search(b.titre))


def meme_projet(a: Edition, b: Edition) -> str | None:
    """PUR. La règle qui réunit ces deux éditions, ou None. « suite » quand
    elles sont deux parties d'une suite (jamais réunies)."""
    if a.upc and b.upc and cle_upc(a.upc) == cle_upc(b.upc):
        return "upc"
    if _jamais_ensemble(a, b):
        return None
    commun = len(a.pistes & b.pistes)
    if commun < 2 or not meme_disque(a.titre, b.titre):
        return None
    if commun < PART_COMMUNE * max(len(a.pistes), len(b.pistes)) and not memes_parties_de_disque(
        a.titre, b.titre
    ):
        return "suite"
    petite = min(len(a.pistes), len(b.pistes))
    if commun == petite:
        return "contenue"
    if commun >= PART_COMMUNE * petite:
        return "commune"
    return None


def regrouper(editions: list[Edition]) -> Regroupement:
    """PUR. Les projets d'un artiste à partir de ses éditions `own` confirmées."""
    par_id = {e.id: e for e in editions}
    parent = {e.id: e.id for e in editions}

    def racine(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    # Paires candidates : au moins deux fiches communes, ou le même code-barres.
    par_piste: dict[int, list[int]] = defaultdict(list)
    for e in editions:
        for t in e.pistes:
            par_piste[t].append(e.id)
    communes: dict[tuple[int, int], int] = defaultdict(int)
    for ids in par_piste.values():
        ids = sorted(ids)
        for i, x in enumerate(ids):
            for y in ids[i + 1 :]:
                communes[(x, y)] += 1
    candidates = {p for p, n in communes.items() if n >= 2}
    par_upc: dict[str, list[int]] = defaultdict(list)
    for e in editions:
        if e.upc and (cle := cle_upc(e.upc)):
            par_upc[cle].append(e.id)
    for ids in par_upc.values():
        ids = sorted(ids)
        candidates |= {(x, y) for i, x in enumerate(ids) for y in ids[i + 1 :]}

    out = Regroupement()
    for x, y in sorted(candidates):
        verdict = meme_projet(par_id[x], par_id[y])
        if verdict == "suite":
            out.suites.append((x, y))
        elif verdict:
            parent[racine(x)] = racine(y)

    groupes: dict[int, list[int]] = defaultdict(list)
    for e in editions:
        groupes[racine(e.id)].append(e.id)
    for ids in groupes.values():
        ids.sort()
        gros = [i for i in ids if len(par_id[i].pistes) >= 3]
        cumule = any(
            not (par_id[a].pistes & par_id[b].pistes)
            for k, a in enumerate(gros)
            for b in gros[k + 1 :]
        )
        if cumule:
            out.a_trancher.append(ids)
            out.projets.extend([i] for i in ids)
        else:
            out.projets.append(ids)
    out.projets.sort()
    return out


def edition_de_reference(editions: list[Edition]) -> int:
    """PUR. L'édition qui donne son titre au projet : la plus ancienne qui
    n'est pas live, à date connue d'abord ; à égalité, le plus petit ID."""
    return min(editions, key=lambda e: (e.live is True, not e.date, e.date or "", e.id)).id
