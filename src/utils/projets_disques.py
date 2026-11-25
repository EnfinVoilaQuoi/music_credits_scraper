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
   le même nom s'entend aussi à la GRAPHIE près (`meme_disque_a_la_graphie_pres` :
   « Or Noir Part II » / « Part 2 », « Double F**k » / « Fuck », élisions,
   lettre triplée ; décision utilisateur 2026-11-24) ;

3. la MÊME liste de fiches (≥ 80 % de leur réunion, 4 fiches au moins, 3 au
   même nom) sous deux noms (« VVRDL », « CDC4 », « HHHH² (Au carré) ») → même
   projet, sauf deux VERSIONS du disque (instrumental, remix, live…) ;

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
Sauf si toutes portent le même nom à un marqueur d'édition près : deux relevés
partiels d'un même disque (« Under Pressure »), rien n'y est recollé.

Séparés sans l'humain (`cas_a_trancher`) : un disque et le coffret qui en
réunit au moins deux (« Magnum 567 », « Rimes essentielles ») ; un maxi et le
disque dont il porte un titre (« For The Night »), comme un single.
Jamais de regroupement au seul titre.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from src.utils.codes_barres import cle_upc
from src.utils.title_matching import normalize_title
from src.utils.version_descriptors import (
    meme_disque,
    meme_disque_a_la_graphie_pres,
    meme_disque_edition,
    memes_parties_de_disque,
)

#: Une compilation de l'artiste n'est pas une édition d'un de ses disques
#: (« Immortel (Mini Best Of) » de Sinik, fondu dans *Immortel* à la première
#: simulation). Liste FERMÉE.
_COMPILATION = re.compile(
    r"\b(best of|greatest hits|the hits|compilation|anthologie|anthology)\b", re.IGNORECASE
)

#: Un disque qui se dit une VERSION d'un autre n'a pas la même liste, même aux
#: mêmes fiches (« Artefact (Instrumentale) »). Liste FERMÉE.
_VERSION_DE_DISQUE = re.compile(
    r"\b(instrumentals?|instrumentales?|acoustic|acoustique|remix(es)?|chopped|screwed|live"
    r"|unplugged|mixed by|mixé par|rough|unmastered)\b",
    re.IGNORECASE,
)

#: Une édition augmentée n'est pas un coffret (« … (Deluxe) »). Liste FERMÉE.
_EDITION_AUGMENTEE = re.compile(
    r"\b(deluxe|preluxe|édition|edition|réédition|reissue|collector|anniversary|anniversaire)\b",
    re.IGNORECASE,
)

#: Un maxi : peu de titres, et bien moins que le disque dont il vient.
MAXI_FICHES_MAX = 6

#: Part des fiches de la plus petite édition qu'il faut en commun (règle 2bis).
PART_COMMUNE = 0.8


@dataclass(frozen=True)
class Edition:
    """Une parution confirmée, vue par le regroupement."""

    id: int
    titre: str
    record_type: str | None = None
    upc: str | None = None
    live: bool | None = None
    date: str | None = None
    pistes: frozenset[int] = frozenset()
    #: Titres des fiches de l'édition (pour reconnaître un maxi à son nom).
    titres_des_pistes: frozenset[str] = frozenset()
    #: `own` (disque de l'artiste) ou `appearance` (disque d'un autre où il
    #: figure : ses streams d'album en dépendent aussi). Jamais mêlés.
    scope: str = "own"


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


def _meme_liste(a: Edition, b: Edition) -> bool:
    """Règle 3 : la même liste de fiches, ni l'une ni l'autre une autre version."""
    commun = len(a.pistes & b.pistes)
    if commun < 3 or commun < PART_COMMUNE * len(a.pistes | b.pistes):
        return False
    if bool(_VERSION_DE_DISQUE.search(a.titre)) != bool(_VERSION_DE_DISQUE.search(b.titre)):
        return False
    return commun >= 4 or meme_disque(a.titre, b.titre)


def _contenue(a: Edition, b: Edition) -> bool:
    return len(a.pistes & b.pistes) >= PART_COMMUNE * len(a.pistes)


def _coffret(petite: Edition, grande: Edition, editions: list[Edition]) -> bool:
    """`grande` réunit `petite` ET un autre disque qui n'a rien de commun avec
    elle : un coffret ou une trilogie, son propre projet. Une édition augmentée
    (« Deluxe ») n'en est un que si son nom annonce les deux (« 2 + 2.5 »)."""
    if _EDITION_AUGMENTEE.search(grande.titre) and "+" not in grande.titre:
        return False
    if not _contenue(petite, grande):
        return False
    return any(
        e.scope == grande.scope
        and e.record_type != "single"
        and 3 <= len(e.pistes) < len(grande.pistes)
        and not (e.pistes & petite.pistes)
        and _contenue(e, grande)
        for e in editions
    )


def _maxi(petite: Edition, grande: Edition) -> bool:
    """`petite` est le maxi d'un titre de `grande` : elle en porte le nom."""
    if len(petite.pistes) > MAXI_FICHES_MAX or len(petite.pistes) * 3 > len(grande.pistes):
        return False
    if meme_disque(petite.titre, grande.titre):
        return False
    nu = _titre_nu(petite.titre)
    return bool(nu) and any(_titre_nu(t) == nu for t in petite.titres_des_pistes)


def _titre_nu(titre: str) -> str:
    return normalize_title(re.sub(r"\s*[(\[].*$", "", titre))


def meme_projet(a: Edition, b: Edition) -> str | None:
    """PUR. La règle qui réunit ces deux éditions, ou None. « suite » quand
    elles sont deux parties d'une suite (jamais réunies)."""
    if a.scope != b.scope:
        return None
    if a.upc and b.upc and cle_upc(a.upc) == cle_upc(b.upc):
        return "upc"
    if _jamais_ensemble(a, b):
        return None
    commun = len(a.pistes & b.pistes)
    # Même nom à la graphie près (« Or Noir Part II » / « Part 2 », « Double
    # F**k » / « Fuck ») : un même nom de disque (décision utilisateur 2026-11-24).
    graphie = meme_disque_a_la_graphie_pres(a.titre, b.titre)
    liste = "meme_liste" if _meme_liste(a, b) else None
    if commun < 2 or not (graphie or meme_disque(a.titre, b.titre)):
        return liste
    if commun < PART_COMMUNE * max(len(a.pistes), len(b.pistes)) and not memes_parties_de_disque(
        a.titre, b.titre
    ):
        return "suite"
    # Dans le doute, séparer (décision utilisateur 2026-11-20) : réunir sans
    # l'humain exige le même nom à un marqueur d'édition près ; un nom contenu
    # dans l'autre (tome d'un double album) va au panneau « même projet ? ».
    if not (graphie or meme_disque_edition(a.titre, b.titre)):
        return liste
    petite = min(len(a.pistes), len(b.pistes))
    if commun == petite:
        return "contenue"
    if commun >= PART_COMMUNE * petite:
        return "commune"
    return None


def regrouper(
    editions: list[Edition], reunies: frozenset[frozenset[int]] = frozenset()
) -> Regroupement:
    """PUR. Les projets d'un artiste à partir de ses éditions confirmées.
    `reunies` : les paires d'éditions qu'un HUMAIN a dites du même projet
    (panneau « même projet ? ») — réunies quoi que disent les règles, et un
    groupe qui en contient une n'est jamais défait par la garde « édition
    cumulée »."""
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
    for paire in reunies:
        x, y = sorted(paire)
        if x in parent and y in parent:
            parent[racine(x)] = racine(y)

    groupes: dict[int, list[int]] = defaultdict(list)
    for e in editions:
        groupes[racine(e.id)].append(e.id)
    for ids in groupes.values():
        ids.sort()
        gros = [i for i in ids if len(par_id[i].pistes) >= 3]
        decide = any(frozenset((a, b)) in reunies for k, a in enumerate(ids) for b in ids[k + 1 :])
        meme_nom = all(
            meme_disque_edition(par_id[a].titre, par_id[b].titre)
            for k, a in enumerate(ids)
            for b in ids[k + 1 :]
        )
        cumule = (
            not decide
            and not meme_nom
            and any(
                not (par_id[a].pistes & par_id[b].pistes)
                for k, a in enumerate(gros)
                for b in gros[k + 1 :]
            )
        )
        if cumule:
            out.a_trancher.append(ids)
            out.projets.extend([i] for i in ids)
        else:
            out.projets.append(ids)
    out.projets.sort()
    return out


@dataclass(frozen=True)
class CasDeProjet:
    """Un regroupement que les règles ne tranchent pas : au panneau « même
    projet ? » (décision utilisateur 2026-11-20)."""

    editions: tuple[int, ...]
    motif: str


def cas_a_trancher(editions: list[Edition], regroupement: Regroupement) -> list[CasDeProjet]:
    """PUR. Ce qui reste à l'humain après `regrouper` (mesuré sur la base le
    2026-11-20 : ~13 groupes, ~22 + ~69 paires) :

    - un groupe recollé par une édition CUMULÉE (« Abysses & Ivresse ») ;
    - deux éditions au même nom de disque, ≥ 2 fiches communes, mais moins de
      80 % de la plus petite (« Chambre 140 » hors suites, éditions
      internationales) ;
    - deux éditions aux noms différents qui partagent ≥ 3 fiches et ≥ 80 % de
      la plus petite (doublons d'édition legacy / Deezer, « HHHH » / « HHHH² »).

    Jamais : deux éditions déjà du même projet, un single, un live contre un
    studio, une compilation, une apparition contre un disque de l'artiste, une
    suite, un coffret contre l'un de ses disques, un maxi contre son album."""
    par_id = {e.id: e for e in editions}
    projet_de = {i: k for k, ids in enumerate(regroupement.projets) for i in ids}
    cas = [
        CasDeProjet(tuple(ids), "plusieurs disques recollés par une édition qui les cumule")
        for ids in regroupement.a_trancher
    ]
    suites = {frozenset(p) for p in regroupement.suites}
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
    deja = {i for c in cas for i in c.editions}
    for (x, y), n in sorted(communes.items()):
        a, b = par_id[x], par_id[y]
        if projet_de.get(x) == projet_de.get(y) or frozenset((x, y)) in suites:
            continue
        if x in deja and y in deja or a.scope != b.scope or _jamais_ensemble(a, b):
            continue
        petite, grande = sorted((a, b), key=lambda e: len(e.pistes))
        if _coffret(petite, grande, editions) or _maxi(petite, grande):
            continue
        petite = len(petite.pistes)
        if n >= 2 and meme_disque(a.titre, b.titre) and memes_parties_de_disque(a.titre, b.titre):
            cas.append(
                CasDeProjet(
                    (x, y), f"même disque, {n} fiches communes sur {petite} : même projet ?"
                )
            )
        elif n >= 3 and n >= PART_COMMUNE * petite and not meme_disque(a.titre, b.titre):
            cas.append(
                CasDeProjet(
                    (x, y), f"titres différents, {n} fiches communes sur {petite} : même projet ?"
                )
            )
    return cas


def edition_de_reference(editions: list[Edition]) -> int:
    """PUR. L'édition qui donne son titre au projet : la plus ancienne qui
    n'est pas live, à date connue d'abord ; à égalité, le plus petit ID."""
    return min(editions, key=lambda e: (e.live is True, not e.date, e.date or "", e.id)).id
