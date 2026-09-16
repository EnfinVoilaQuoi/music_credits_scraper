"""Nature des disques (`albums.record_type`) : ce que Deezer déclare, une fois par album.

Un album est un « EP » ou un « album » parce que l'artiste (via son
distributeur) l'a décidé — ni le nombre de titres ni la durée ne le déduisent
(mesuré sur Isha, 2026-09-16 : « La Vie Augmente » 1/2/3 = EP à 10 titres et
26 min ; « Faites pas chier » = EP à 8 titres ; « Bitume Caviar » = album à
12 titres et 32 min ; Deezer concorde partout avec l'étiquette de l'artiste).

La donnée n'est que sur la fiche `GET /album/{id}` : le pas de fin de run
regroupe les morceaux par album, retient l'identifiant Deezer d'album le plus
fréquent parmi leurs hits (`Track._deezer_album_id`, posé par le provider), et
interroge UNE fois la fiche. Deux gardes-fous mesurés : le hit d'un morceau
peut être son ÉDITION SINGLE (« Drôle d'oiseau » : une fiche `single` d'1 titre
à côté de l'EP de 9) — un `single` dont la fiche a moins de pistes que nous
n'en avons sur cet album est rejeté ; et une fiche déjà connue avec le même
identifiant n'est pas redemandée.

Module pur (`regrouper`, `choisir_id`, `verdict`, `libelle_record_type`) +
une coroutine d'orchestration sans GUI ; l'écriture est injectée.
"""

import asyncio
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from src.utils.logger import get_logger
from src.utils.title_matching import normalize_title

logger = get_logger(__name__)

RECORD_TYPES: tuple[str, ...] = ("album", "ep", "single", "compile")

#: Libellé d'affichage (Timeline, vue Albums). `None` → « Album » côté visuel
#: (un projet non qualifié reste un album par défaut) ; la vue Albums, elle,
#: laisse vide : c'est une donnée, pas un libellé.
LIBELLES: dict[str, str] = {
    "album": "Album",
    "ep": "EP",
    "single": "Single",
    "compile": "Compilation",
}


def libelle_record_type(record_type: str | None) -> str:
    return LIBELLES.get((record_type or "").lower(), "Album")


@dataclass(frozen=True)
class FicheAlbum:
    """Ce qu'on retient d'une fiche `GET /album/{id}`."""

    id: int
    title: str
    record_type: str | None  # normalisé, parmi RECORD_TYPES, sinon None
    nb_tracks: int


def fiche_album(data: Mapping | None) -> FicheAlbum | None:
    """Fiche Deezer → `FicheAlbum`, ou None si la réponse n'en est pas une."""
    if not data or not isinstance(data, Mapping) or not data.get("id"):
        return None
    brut = str(data.get("record_type") or "").strip().lower()
    try:
        nb = int(data.get("nb_tracks") or 0)
    except (TypeError, ValueError):
        nb = 0
    return FicheAlbum(
        id=int(data["id"]),
        title=str(data.get("title") or ""),
        record_type=brut if brut in RECORD_TYPES else None,
        nb_tracks=nb,
    )


@dataclass
class GroupeAlbum:
    """Un album de l'artiste : ses morceaux (tous, pas la sélection) et les
    identifiants Deezer d'album vus sur leurs hits ce run."""

    title: str
    nb_morceaux: int = 0
    ids: Counter = field(default_factory=Counter)


def regrouper(tracks) -> dict[str, GroupeAlbum]:
    """Par `track.album` brut (le titre Genius, clé de la table `albums`)."""
    groupes: dict[str, GroupeAlbum] = {}
    for t in tracks:
        title = (t.album or "").strip()
        if not title:
            continue
        g = groupes.setdefault(title, GroupeAlbum(title))
        g.nb_morceaux += 1
        if t._deezer_album_id:
            g.ids[int(t._deezer_album_id)] += 1
    return groupes


def choisir_id(ids: Counter) -> int | None:
    """L'identifiant le plus fréquent ; égalité → le plus petit (déterministe)."""
    if not ids:
        return None
    best = max(ids.values())
    return min(i for i, n in ids.items() if n == best)


def _cle_titre(title: str) -> str:
    """Titre normalisé SANS espaces : « La Vie Augmente, Vol. 1 » (Deezer) et
    « La vie augmente Vol.1 » (Genius) doivent se rejoindre — mesuré, l'espace
    après le point survit à `normalize_title` (« vol 1 » ≠ « vol1 »)."""
    return normalize_title(title or "").replace(" ", "")


def choisir_fiche_recherche(hits, title: str, nb_morceaux: int) -> FicheAlbum | None:
    """Parmi des fiches de `search/album`, celle dont le titre normalisé est
    EXACTEMENT le nôtre — jamais la plus proche. À nombre de morceaux ≥ 2, une
    fiche d'une seule piste (le single homonyme) est écartée ; à égalité, la
    plus fournie (l'édition complète). Fonction pure."""
    cible = _cle_titre(title)
    if not cible:
        return None
    candidates = []
    for hit in hits or ():
        fiche = fiche_album(hit)
        if fiche is None or _cle_titre(fiche.title) != cible:
            continue
        if nb_morceaux >= 2 and fiche.nb_tracks < 2:
            continue
        candidates.append(fiche)
    if not candidates:
        return None
    return max(candidates, key=lambda f: (f.nb_tracks, -f.id))


def verdict(fiche: FicheAlbum | None, nb_morceaux: int) -> tuple[str | None, str | None]:
    """(record_type à écrire, motif de rejet)."""
    if fiche is None:
        return None, "fiche absente"
    if fiche.record_type is None:
        return None, "record_type inconnu"
    if fiche.record_type == "single" and nb_morceaux >= 2 and fiche.nb_tracks < nb_morceaux:
        # Le hit majoritaire était l'édition single d'un titre de l'album.
        return None, f"fiche single ({fiche.nb_tracks} piste) pour {nb_morceaux} morceaux"
    return fiche.record_type, None


@dataclass
class BilanTypes:
    renseignes: int = 0
    ignores: int = 0
    motifs: list[str] = field(default_factory=list)


async def types_albums_deezer(
    client,
    http,
    tracks,
    deja: Mapping[str, Mapping],
    ecrire: Callable[[str, str, int], bool],
    *,
    force: bool = False,
    artist_name: str = "",
) -> BilanTypes:
    """Une fiche par album, puis `ecrire(title, record_type, deezer_album_id)`.

    Deux voies : l'identifiant d'album le plus fréquent parmi les hits des
    morceaux (posé par le provider ce run), sinon — avec `artist_name` — la
    recherche d'album par (artiste, titre), titre normalisé EXACT. `deja` = les
    lignes `albums` déjà en base, par titre brut : un album déjà qualifié n'est
    pas redemandé sauf `force`. L'écriture (sync, SQLite) passe par
    `asyncio.to_thread`.
    """
    bilan = BilanTypes()
    for title, groupe in sorted(regrouper(tracks).items()):
        connu = deja.get(title) or {}
        album_id = choisir_id(groupe.ids)
        # Déjà qualifié : on ne redemande que si un NOUVEL identifiant apparaît.
        deja_su = bool(connu.get("record_type")) and (
            album_id is None or connu.get("deezer_album_id") == album_id
        )
        if deja_su and not force:
            continue
        if album_id is not None:
            fiche = fiche_album(await client.get_album_async(http, album_id))
        elif artist_name:
            hits = await client.search_album_async(http, artist_name, title)
            fiche = choisir_fiche_recherche(hits, title, groupe.nb_morceaux)
            if fiche is None:
                bilan.ignores += 1
                bilan.motifs.append(f"{title} : aucune fiche Deezer à ce titre")
                continue
            album_id = fiche.id
        else:
            bilan.ignores += 1
            bilan.motifs.append(f"{title} : aucun hit Deezer")
            continue
        rt, motif = verdict(fiche, groupe.nb_morceaux)
        if rt is None:
            bilan.ignores += 1
            bilan.motifs.append(f"{title} : {motif}")
            logger.info(f"   💿 {title} : nature non retenue ({motif})")
            continue
        if await asyncio.to_thread(ecrire, title, rt, album_id):
            bilan.renseignes += 1
            logger.info(f"   💿 {title} : {LIBELLES[rt]} (Deezer)")
        else:
            bilan.ignores += 1
            bilan.motifs.append(f"{title} : saisie manuelle conservée")
    return bilan
