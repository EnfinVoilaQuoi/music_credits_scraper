"""Spotify par les requêtes internes du lecteur web (« pathfinder »), SANS navigateur.

La page `open.spotify.com` que lit `spotify_web` (Playwright) affiche ce que ces
requêtes GraphQL rendent : compteur et durée d'une piste, pistes d'un album avec
leur compteur, auditeurs mensuels d'un artiste. Mesuré le 2026-11-20 sur 60 fiches
(copie de la base) : compteurs égaux à notre dernière lecture `spotify_web` (à la
croissance près), durées à moins de 1 s, auditeurs à ± 1 %, ~4 s par requête au
lieu de ~25 min de pages par artiste.

PARTAGE DES RÔLES (décision 2026-11-20) :
- `spotapi` (version épinglée) ne sert qu'au TRANSPORT et à la DÉCOUVERTE des
  requêtes : jeton anonyme, empreintes des requêtes persistées, que Spotify change à
  chaque version du lecteur — la partie fragile, que la bibliothèque maintient ;
- le JSON brut se lit ICI (`lire_*`, fonctions pures) : les modèles typés de
  `spotapi` sont trop stricts (`get_artist` échoue sur une biographie au texte
  `None` : Lunatic, Karlit & Kabok).

Rien ne juge ici : l'oracle `/embed/` et le gate `spotify_identity` restent les
juges de l'identité ; ce module ne fait que LIRE ce que Spotify sert pour un ID.
Le scraper `spotify_web` reste le repli.

Contrat de chaque lecture : rend la donnée ; `None` = Spotify a RÉPONDU que l'objet
n'existe pas (`NotFound`, absence prouvée) ; sinon lève `SansReponse` (bridage,
panne, délai, structure inattendue), jamais une absence (règle « absent ≠ échec »).

Brut (chantier E) : chaque réponse est gardée (`brut`, source `spotify_pathfinder`) ;
en rejeu hors ligne, elle est servie depuis le brut et aucun appel ne part.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from src.config import SPOTIFY_PATHFINDER_DELAI_S
from src.observability import source_usage
from src.observability.issues import IssueKind, SansReponse, classify
from src.persistence import brut
from src.utils.logger import get_logger

logger = get_logger(__name__)

#: Clé de source (`source_health.SOURCES`, `brut.SOURCES_ACTIVES`).
SOURCE = "spotify_pathfinder"
#: Adresse des requêtes internes ; sert aussi de clé au brut (avec l'opération et
#: ses variables en paramètres).
URL_REQUETES = "https://api-partner.spotify.com/pathfinder/v2/query"

# Noms des requêtes persistées du lecteur web (ceux de `spotapi.Operations`).
OP_PISTE = "getTrack"
OP_PISTES_ALBUM = "queryAlbumTracks"
OP_ARTISTE = "queryArtistOverview"

#: Pistes demandées par page d'album (le lecteur web en demande 300).
PISTES_PAR_PAGE = 300
#: Garde-fou de pagination : aucun album n'a 30 pages de 300 pistes.
PAGES_MAX = 30


# ── Données lues ──────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class PisteSpotify:
    """Ce que Spotify sert pour UN ID de piste."""

    id: str
    titre: str
    #: Compteur d'écoutes ; None si Spotify n'en affiche pas (certaines pages titre
    #: n'en ont aucun : *VULTURES*, 2026-10-26) — jamais 0 par défaut.
    compteur: int | None
    duree_s: float | None
    album_id: str | None
    place: int | None
    disque: int | None
    #: (nom, ID Spotify) des artistes crédités, dans l'ordre servi.
    artistes: tuple[tuple[str, str | None], ...]


@dataclass(frozen=True)
class ArtisteSpotify:
    id: str
    nom: str
    auditeurs_mensuels: int | None
    abonnes: int | None


class StructureInattendue(ValueError):
    """La réponse n'a pas la forme connue : Spotify a changé la requête."""


# ── Lecture du JSON (pur) ─────────────────────────────────────────────────────
def _id_de_l_uri(uri: object) -> str | None:
    if not isinstance(uri, str) or not uri.startswith("spotify:"):
        return None
    return uri.rsplit(":", 1)[-1] or None


def _entier(valeur: object) -> int | None:
    """Un compteur servi en chaîne (« 1204777030 ») ou en nombre ; rien d'autre."""
    if isinstance(valeur, bool):
        return None
    if isinstance(valeur, int):
        return valeur
    if isinstance(valeur, str) and valeur.strip().isdigit():
        return int(valeur.strip())
    return None


def _duree_s(duree: object) -> float | None:
    if not isinstance(duree, Mapping):
        return None
    ms = _entier(duree.get("totalMilliseconds"))
    return ms / 1000 if ms else None


def _union(data: object, cle: str) -> Mapping[str, Any] | None:
    """L'objet `<cle>` d'une réponse ; None si Spotify dit `NotFound`."""
    if not isinstance(data, Mapping):
        raise StructureInattendue(f"réponse sans objet (« {cle} » attendu)")
    objet = data.get(cle)
    if not isinstance(objet, Mapping):
        raise StructureInattendue(f"« {cle} » absent de la réponse")
    if objet.get("__typename") == "NotFound":
        return None
    return objet


def _artistes(groupe: object) -> list[tuple[str, str | None]]:
    if not isinstance(groupe, Mapping):
        return []
    rendus = []
    for item in groupe.get("items") or ():
        if not isinstance(item, Mapping):
            continue
        profil = item.get("profile")
        nom = profil.get("name") if isinstance(profil, Mapping) else None
        if isinstance(nom, str) and nom:
            rendus.append((nom, _id_de_l_uri(item.get("uri"))))
    return rendus


def lire_piste(data: object) -> PisteSpotify | None:
    """Réponse de `getTrack` → la piste ; None si Spotify répond `NotFound`."""
    piste = _union(data, "trackUnion")
    if piste is None:
        return None
    id_ = piste.get("id") or _id_de_l_uri(piste.get("uri"))
    titre = piste.get("name")
    if not isinstance(id_, str) or not isinstance(titre, str):
        raise StructureInattendue("piste sans id ni titre")
    album = piste.get("albumOfTrack")
    artistes = _artistes(piste.get("firstArtist")) + _artistes(piste.get("otherArtists"))
    return PisteSpotify(
        id=id_,
        titre=titre,
        compteur=_entier(piste.get("playcount")),
        duree_s=_duree_s(piste.get("duration")),
        album_id=_id_de_l_uri(album.get("uri")) if isinstance(album, Mapping) else None,
        place=_entier(piste.get("trackNumber")),
        disque=_entier(piste.get("discNumber")),
        artistes=tuple(artistes),
    )


def lire_page_album(data: object, album_id: str) -> tuple[list[PisteSpotify], int | None] | None:
    """Une page de `queryAlbumTracks` → (pistes, total annoncé) ; None si `NotFound`."""
    album = _union(data, "albumUnion")
    if album is None:
        return None
    pistes_v2 = album.get("tracksV2")
    if not isinstance(pistes_v2, Mapping):
        raise StructureInattendue("album sans « tracksV2 »")
    pistes = []
    for item in pistes_v2.get("items") or ():
        piste = item.get("track") if isinstance(item, Mapping) else None
        if not isinstance(piste, Mapping):
            continue
        id_ = _id_de_l_uri(piste.get("uri"))
        titre = piste.get("name")
        if id_ is None or not isinstance(titre, str):
            raise StructureInattendue("piste d'album sans uri ni titre")
        pistes.append(
            PisteSpotify(
                id=id_,
                titre=titre,
                compteur=_entier(piste.get("playcount")),
                duree_s=_duree_s(piste.get("duration")),
                album_id=album_id,
                place=_entier(piste.get("trackNumber")),
                disque=_entier(piste.get("discNumber")),
                artistes=tuple(_artistes(piste.get("artists"))),
            )
        )
    return pistes, _entier(pistes_v2.get("totalCount"))


def lire_artiste(data: object) -> ArtisteSpotify | None:
    """Réponse de `queryArtistOverview` → l'artiste ; None si `NotFound`."""
    artiste = _union(data, "artistUnion")
    if artiste is None:
        return None
    id_ = artiste.get("id") or _id_de_l_uri(artiste.get("uri"))
    profil = artiste.get("profile")
    nom = profil.get("name") if isinstance(profil, Mapping) else None
    if not isinstance(id_, str) or not isinstance(nom, str):
        raise StructureInattendue("artiste sans id ni nom")
    stats = artiste.get("stats")
    stats = stats if isinstance(stats, Mapping) else {}
    return ArtisteSpotify(
        id=id_,
        nom=nom,
        auditeurs_mensuels=_entier(stats.get("monthlyListeners")),
        abonnes=_entier(stats.get("followers")),
    )


# ── Transport (spotapi) ───────────────────────────────────────────────────────
def _erreurs_wreq() -> tuple[type[Exception], ...]:
    """Les exceptions de `wreq.exceptions` (extension Rust : leur `__module__` est
    « exceptions », pas « wreq » — d'où une reconnaissance par IDENTITÉ). Elles
    n'ont pas de base commune hormis `Exception` ; d'ordinaire `spotapi` les
    enveloppe dans son `HTTPError`."""
    import builtins

    import wreq.exceptions

    natives = {id(objet) for objet in vars(builtins).values()}
    return tuple(
        classe
        for classe in vars(wreq.exceptions).values()
        if isinstance(classe, type) and issubclass(classe, Exception) and id(classe) not in natives
    )


def _erreurs_du_transport() -> tuple[type[BaseException], ...]:
    """Ce que `spotapi` et `wreq` lèvent : `SpotAPIError` (dont `HTTPError`) et
    les exceptions de `wreq`."""
    from spotapi.types.exceptions import SpotAPIError

    return (SpotAPIError, *_erreurs_wreq())


def _nature_de_l_echec(exc: BaseException) -> IssueKind:
    """Une erreur `wreq` nue : délai ou injoignable ; le reste par `classify`
    (`issues._from_spotapi` pour `spotapi`)."""
    if isinstance(exc, _erreurs_wreq()):
        return IssueKind.TIMEOUT if type(exc).__name__ == "TimeoutError" else IssueKind.UNREACHABLE
    return classify(exc=exc)


def _parametres(operation: str, variables: Mapping[str, Any]) -> dict[str, str]:
    """Clé du brut : l'opération et ses variables, dans un ordre stable."""
    return {
        "operationName": operation,
        "variables": json.dumps(dict(variables), sort_keys=True, ensure_ascii=False),
    }


class SpotifyPathfinder:
    """Client asynchrone, UN par run (la découverte des requêtes, ~20 s à froid,
    ne se fait qu'au premier appel) :

        async with SpotifyPathfinder() as sp:
            piste = await sp.piste("4uLU6hMCjMI75M1A2tKUQC")

    S'emploie sur la boucle unique du projet (`src/concurrency`), jamais depuis un
    fil à part. En rejeu hors ligne (`brut.rejeu`), la session `spotapi` ne s'ouvre
    jamais.
    """

    def __init__(self, *, delai_s: float | None = None) -> None:
        self._delai_s = SPOTIFY_PATHFINDER_DELAI_S if delai_s is None else delai_s
        self._catalogue: Any = None

    async def __aenter__(self) -> SpotifyPathfinder:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.fermer()

    async def fermer(self) -> None:
        catalogue, self._catalogue = self._catalogue, None
        if catalogue is not None:
            await catalogue.__aexit__(None, None, None)

    async def _ouvrir(self) -> Any:
        if self._catalogue is None:
            # Import paresseux : le reste du projet (et la suite de tests) n'a pas
            # besoin de spotapi pour importer ce module.
            from spotapi.catalogue import Catalogue

            catalogue = Catalogue()
            await catalogue.__aenter__()
            self._catalogue = catalogue
        return self._catalogue

    async def _requete(self, operation: str, variables: Mapping[str, Any]) -> object:
        """UNE requête : le `data` de la réponse. Lève `SansReponse` sur tout échec
        (le type d'échec dans `kind`), jamais une exception de `spotapi`."""
        params = _parametres(operation, variables)
        if brut.rejeu_actif():
            servi = brut.servir(SOURCE, URL_REQUETES, params)
            if servi is None:
                raise SansReponse(SOURCE, IssueKind.INDETERMINATE, "absent du brut")
            statut, corps = servi
            if brut.bridee(statut, corps):
                raise SansReponse(SOURCE, IssueKind.THROTTLED, f"lecture bridée ({statut})")
            try:
                return json.loads(corps)
            except json.JSONDecodeError as e:
                raise SansReponse(SOURCE, IssueKind.PARSE, f"brut illisible : {e}") from e
        try:
            catalogue = await self._ouvrir()
            async with asyncio.timeout(self._delai_s):
                data = await catalogue.queries.query(operation, dict(variables))
        except TimeoutError as e:
            raise SansReponse(SOURCE, IssueKind.TIMEOUT, f"{operation} : délai de garde") from e
        except _erreurs_du_transport() as e:
            kind = _nature_de_l_echec(e)
            raise SansReponse(SOURCE, kind, f"{operation} : {type(e).__name__}: {e}") from e
        brut.consigner(
            SOURCE,
            URL_REQUETES,
            200,
            json.dumps(data, ensure_ascii=False).encode("utf-8"),
            params,
        )
        return data

    async def _lire(self, operation, variables, lecteur, *, hors_morceau: bool, label: str):
        with source_usage.observe(SOURCE, hors_morceau=hors_morceau, label=label) as obs:
            data = await self._requete(operation, variables)
            try:
                lu = lecteur(data)
            except StructureInattendue as e:
                raise SansReponse(SOURCE, IssueKind.PARSE, f"{operation} : {e}") from e
            if lu is None:
                obs.absent(f"{operation} : NotFound", repondu=True)
            else:
                obs.ok()
            return lu

    # ── Lectures publiques ────────────────────────────────────────────────────
    async def piste(self, spotify_id: str) -> PisteSpotify | None:
        """Compteur, durée, album, place et artistes d'UN ID de piste."""
        return await self._lire(
            OP_PISTE,
            {"uri": f"spotify:track:{spotify_id}"},
            lire_piste,
            hors_morceau=False,
            label=f"piste {spotify_id}",
        )

    async def artiste(self, spotify_id: str) -> ArtisteSpotify | None:
        """Auditeurs mensuels et abonnés d'un profil d'artiste."""
        return await self._lire(
            OP_ARTISTE,
            {
                "uri": f"spotify:artist:{spotify_id}",
                "locale": "",
                "preReleaseV2": False,
                "includeMusicVideoPreviews": False,
            },
            lire_artiste,
            hors_morceau=True,
            label=f"artiste {spotify_id}",
        )

    async def pistes_album(self, album_id: str) -> list[PisteSpotify] | None:
        """TOUTES les pistes d'un album, dans l'ordre disque / place, avec leur
        compteur (une requête par page de 300). None si l'album n'existe pas."""
        pistes: list[PisteSpotify] = []
        for page in range(PAGES_MAX):
            lu = await self._lire(
                OP_PISTES_ALBUM,
                {
                    "uri": f"spotify:album:{album_id}",
                    "offset": page * PISTES_PAR_PAGE,
                    "limit": PISTES_PAR_PAGE,
                },
                lambda data: lire_page_album(data, album_id),
                hors_morceau=True,
                label=f"album {album_id}",
            )
            if lu is None:
                return None if page == 0 else pistes
            lot, total = lu
            pistes.extend(lot)
            if (
                not lot
                or len(lot) < PISTES_PAR_PAGE
                or (total is not None and len(pistes) >= total)
            ):
                return pistes
        logger.warning(f"Album Spotify {album_id} : plus de {PAGES_MAX} pages, lecture tronquée")
        return pistes
