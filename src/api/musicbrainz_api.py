"""Client MusicBrainz — appartenance à un GROUPE (lot 3).

Doc de référence : `docs/api/musicbrainz-api.md`.

Rôle : **source 1** de « qui compose quelle formation ». Genius ne l'expose pas ;
MusicBrainz la modélise explicitement, avec des dates. Discogs confirme.

Trois décisions, toutes MESURÉES sur l'API réelle le 2026-09-07 — ce module ne
serait pas le même sans ces mesures :

1. **Le User-Agent n'est pas une politesse, c'est le contrat.** Sans UA
   identifiable, MusicBrainz rend `503`. Le même appel avec l'UA ci-dessous rend
   `200`. Une panne d'en-tête se lit donc comme une panne réseau.

2. **Le rapprochement de noms se fait par égalité EXACTE du nom normalisé**, et
   surtout PAS par `names_match_as_words`. Le prédicat par mots entiers est le
   bon outil ailleurs (il sauve « Jul » face à « Jul & SCH »), mais ici son
   relâchement est fatal : sur la requête « Swing », il accepte 17 candidats
   étrangers — *Swing Out Sister*, *Diablo Swing Orchestra*, *Dutch Swing
   College Band*. Un mauvais MBID rend ensuite des relations parfaitement
   formées vers le mauvais artiste, ce qui est la pire forme d'erreur : elle a
   l'air d'une donnée.

3. **Le `score` de MusicBrainz ne départage RIEN.** C'est une pertinence Lucene.
   Sur « Swing », il classe premier un duo de Hong Kong (90) et dixième le
   rappeur belge que nous cherchons (85). Les homonymes exacts sont donc
   départagés par **recouvrement d'albums avec notre base** — le mécanisme déjà
   éprouvé sur les canaux YTMusic (`_pick_best_candidate`), avec la même exigence
   de **pluralité nette** et le même refus de conclure en cas d'égalité.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import requests

from src.observability import source_usage
from src.observability.issues import IssueKind
from src.utils.logger import get_logger
from src.utils.title_matching import normalize_name, normalize_title

logger = get_logger(__name__)

#: Clé de `source_health.SOURCES` sous laquelle cet usage est compté.
_SOURCE = "musicbrainz"

_BASE_URL = "https://musicbrainz.org/ws/2"

#: Format imposé : `Nom/version ( contact )`. Sans lui, 503 (mesuré).
_USER_AGENT = "MusicCreditsScraper/1.0 ( https://github.com/g78rem/music_credits_scraper )"

#: 1 requête/seconde et par IP. Au-delà, MusicBrainz refuse SEC (503) — il ne
#: sert donc à rien d'aller plus vite, on se ferait juste jeter.
_INTERVALLE_MIN_S = 1.1

#: Le 503 n'est PAS que de cadence (mesuré le 2026-09-15) : à 1,1 s d'intervalle,
#: **3 lookups sur 6** rendaient « The MusicBrainz web server is currently busy »
#: avec `Retry-After: 0` — le serveur est SATURÉ et demande de réessayer tout de
#: suite. Sans réessai, un lookup sur deux échouait et `resoudre_artiste` rendait
#: `None` : la GUI annonçait « ambiguïté » là où c'était un aléa de transport.
_TENTATIVES = 4


class MusicBrainzSature(RuntimeError):
    """503 persistant après `_TENTATIVES` essais : la source parle, mais pas à nous.

    Levée (jamais avalée en `[]`/`None`) pour qu'un appelant ne confonde pas
    « pas de réponse » avec « pas de résultat ».
    """


#: UUID du type de relation « member of band ». On s'appuie sur l'identifiant et
#: non sur le libellé `type` : le libellé est du texte d'interface, l'UUID est le
#: contrat.
MEMBRE_DE_GROUPE = "5be4c609-9afa-4ea0-910b-12ffb71e3821"


@dataclass(frozen=True)
class RelationGroupe:
    """Un lien d'appartenance vu par MusicBrainz, du point de vue de l'artiste interrogé."""

    kind: str  # 'member_of' (je suis membre de) | 'has_member' (a pour membre)
    nom: str  # nom de l'artiste à l'autre bout
    mbid: str
    type_cible: str | None = None  # 'Person' / 'Group'
    begin: str | None = None
    end: str | None = None
    ended: bool = False


@dataclass(frozen=True)
class AliasArtiste:
    """Un alias vu par MusicBrainz, avec son TYPE — c'est lui qui décide de l'usage.

    « Artist name » = un vrai nom de scène (Psmaker pour Isha) → proposable.
    « Legal name » (état civil), « Search hint » (faute de frappe courante) et
    les autres → gardés pour information, jamais utilisés en recherche : c'est
    là que naissent les faux positifs (décision utilisateur 2026-09-16).
    """

    nom: str
    type: str | None = None
    locale: str | None = None
    primary: bool = False
    begin: str | None = None
    end: str | None = None

    @property
    def proposable(self) -> bool:
        return self.type == ALIAS_NOM_DE_SCENE


#: Le seul type d'alias MusicBrainz qui vaut proposition.
ALIAS_NOM_DE_SCENE = "Artist name"


@dataclass
class CandidatArtiste:
    """Un artiste MusicBrainz au nom exactement égal au nôtre, et son oracle."""

    mbid: str
    nom: str
    type: str | None = None
    pays: str | None = None
    desambiguation: str | None = None
    albums_communs: int = 0
    relations: list[RelationGroupe] = field(default_factory=list)
    aliases: list[AliasArtiste] = field(default_factory=list)


# ── Logique PURE (testable sans réseau) ──────────────────────────────────────


def candidats_exacts(nom_recherche: str, artistes: list[dict]) -> list[dict]:
    """Candidats dont le nom normalisé est EXACTEMENT le nôtre.

    Fonction pure. Volontairement plus stricte que `names_match_as_words` : cf.
    la décision 2 de l'en-tête de module — mesuré, l'inclusion par mots entiers
    laisse passer 17 artistes étrangers sur la seule requête « Swing ».

    L'égalité porte sur `normalize_name`, donc casse, accents et apostrophes sont
    déjà neutralisés : « Shurik'N » retrouve bien « Shurik’n ».
    """
    cible = normalize_name(nom_recherche)
    if not cible:
        return []
    return [a for a in artistes if a.get("name") and normalize_name(a["name"]) == cible]


def relations_membre(detail: dict) -> list[RelationGroupe]:
    """Relations « member of band » d'une réponse de lookup, sens résolu.

    Fonction pure. **La direction est relative à l'entité interrogée** — c'est le
    piège de cette API, et le confondre inverserait membre et groupe, donc la
    discographie qu'on réunit :

        interroger IAM (Group)        → direction=backward, artist=Akhenaton → has_member
        interroger Akhenaton (Person) → direction=forward,  artist=IAM       → member_of
    """
    liens = []
    for rel in detail.get("relations") or []:
        if rel.get("type-id") != MEMBRE_DE_GROUPE:
            continue
        cible = rel.get("artist") or {}
        if not cible.get("id") or not cible.get("name"):
            continue
        liens.append(
            RelationGroupe(
                kind="member_of" if rel.get("direction") == "forward" else "has_member",
                nom=cible["name"],
                mbid=cible["id"],
                type_cible=cible.get("type"),
                begin=rel.get("begin"),
                end=rel.get("end"),
                ended=bool(rel.get("ended")),
            )
        )
    return liens


def aliases_de(detail: dict) -> list[AliasArtiste]:
    """Alias d'une réponse de lookup (`inc=aliases`), TOUS types, dédoublonnés
    sur le nom normalisé (le premier vu gagne — un « Artist name » et un
    « Search hint » de même graphie ne font qu'une ligne). Fonction pure."""
    vus: set[str] = set()
    out: list[AliasArtiste] = []
    for brut in detail.get("aliases") or []:
        nom = str(brut.get("name") or "").strip()
        cle = normalize_name(nom)
        if not cle or cle in vus:
            continue
        vus.add(cle)
        out.append(
            AliasArtiste(
                nom=nom,
                type=brut.get("type") or None,
                locale=brut.get("locale") or None,
                primary=bool(brut.get("primary")),
                begin=brut.get("begin-date") or brut.get("begin") or None,
                end=brut.get("end-date") or brut.get("end") or None,
            )
        )
    return out


def compter_albums_communs(detail: dict, nos_albums: set[str]) -> int:
    """Nb de release-groups du candidat dont le titre normalisé est dans notre base."""
    if not nos_albums:
        return 0
    titres = {
        normalize_title(rg["title"])
        for rg in (detail.get("release-groups") or [])
        if rg.get("title")
    }
    return len(titres & nos_albums)


def departager(candidats: list[CandidatArtiste]) -> CandidatArtiste | None:
    """Le bon artiste parmi des homonymes EXACTS, ou None si on ne peut pas conclure.

    Fonction pure. Même règle que le départage des canaux YTMusic : il faut un
    recouvrement non nul ET une **pluralité nette** (strictement devant le
    deuxième). Une égalité en tête n'est pas tranchée — l'ordre y serait
    arbitraire, et se tromper d'artiste ici produit des relations crédibles vers
    quelqu'un d'autre.

    Le cas à UN seul candidat est accepté SANS recouvrement : il n'y a personne
    avec qui le confondre. C'est la validation humaine qui reste le dernier mot.
    """
    if not candidats:
        return None
    if len(candidats) == 1:
        return candidats[0]
    classes = sorted(candidats, key=lambda c: -c.albums_communs)
    if classes[0].albums_communs == 0:
        return None
    if classes[0].albums_communs == classes[1].albums_communs:
        return None
    return classes[0]


def _retry_after(valeur: str | None) -> float:
    """Délai avant réessai : `Retry-After` s'il est plus long que la cadence."""
    try:
        demande = float(valeur) if valeur is not None else 0.0
    except ValueError:
        demande = 0.0
    return max(demande, _INTERVALLE_MIN_S)


# ── Client ───────────────────────────────────────────────────────────────────


class MusicBrainzAPI:
    """Lecture seule sur `musicbrainz.org/ws/2` (aucune clé, UA obligatoire)."""

    def __init__(self, timeout: int = 15):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": _USER_AGENT})
        self._dernier_appel = 0.0

    def _attendre_son_tour(self) -> None:
        """Cadence 1 req/s. Aller plus vite ne rapporte rien : on récolterait des 503."""
        reste = _INTERVALLE_MIN_S - (time.monotonic() - self._dernier_appel)
        if reste > 0:
            time.sleep(reste)
        self._dernier_appel = time.monotonic()

    def _get(self, chemin: str, params: dict[str, Any]) -> dict:
        """GET JSON, réessayé sur 503 (serveur saturé — cf. `_TENTATIVES`).

        `Retry-After` est honoré mais jamais en dessous de la cadence : le site
        rend `0`, et repartir aussitôt ne ferait que redemander au même serveur
        saturé. Lève `MusicBrainzSature` quand les essais sont épuisés.
        """
        for essai in range(1, _TENTATIVES + 1):
            self._attendre_son_tour()
            try:
                reponse = self.session.get(
                    f"{_BASE_URL}{chemin}", params={**params, "fmt": "json"}, timeout=self.timeout
                )
            except requests.RequestException as e:
                logger.warning(f"MusicBrainz injoignable ({chemin}): {e}")
                raise
            if reponse.status_code != 503:
                reponse.raise_for_status()
                return reponse.json()
            # Throttling ou saturation, PAS une panne : la source parle et
            # demande de réessayer.
            logger.info(f"MusicBrainz : 503 sur {chemin} (essai {essai}/{_TENTATIVES})")
            if essai < _TENTATIVES:
                time.sleep(_retry_after(reponse.headers.get("Retry-After")))
        raise MusicBrainzSature(
            f"503 persistant sur {chemin} après {_TENTATIVES} essais (serveur saturé)"
        )

    def rechercher_artiste(self, nom: str, limite: int = 25) -> list[dict]:
        """Candidats bruts pour un nom. Le filtrage est l'affaire de l'appelant."""
        with source_usage.observe(_SOURCE, label=f"recherche {nom}") as obs:
            try:
                data = self._get("/artist/", {"query": f'artist:"{nom}"', "limit": limite})
            except MusicBrainzSature as e:
                # La source parle et demande de réessayer : `throttled`, jamais
                # `broken` — et l'exception REMONTE, un `[]` la ferait passer
                # pour « aucun artiste de ce nom ».
                obs.fail(IssueKind.THROTTLED, str(e))
                raise
            artistes = data.get("artists") or []
            if not artistes:
                obs.absent()
            return artistes

    def details_artiste(self, mbid: str, inc: str = "artist-rels") -> dict:
        """Lookup d'un artiste. `inc` cumulable : `artist-rels+release-groups`.

        Lève `MusicBrainzSature` sur 503 persistant (jamais `None` : un lookup
        muet ferait disparaître un homonyme du départage, en silence).
        """
        with source_usage.observe(_SOURCE, label=f"lookup {mbid}") as obs:
            try:
                return self._get(f"/artist/{mbid}", {"inc": inc})
            except MusicBrainzSature as e:
                obs.fail(IssueKind.THROTTLED, str(e))
                raise

    def resoudre_artiste(self, nom: str, nos_albums: set[str]) -> CandidatArtiste | None:
        """Nom → l'artiste MusicBrainz correspondant, relations comprises.

        Enchaîne les trois décisions mesurées : nom EXACT, départage par
        recouvrement d'albums, refus de conclure en cas d'égalité. Rend `None`
        plutôt que de désigner un artiste au hasard — un mauvais MBID donnerait
        des relations parfaitement formées vers quelqu'un d'autre.

        Une requête pour la recherche, puis UNE par homonyme exact — les
        relations ET les albums sont demandés ensemble, donc le gagnant n'a pas
        besoin d'un appel de plus.

        `None` ne signifie QUE « nom inconnu » ou « homonymes indépartageables » :
        une panne de transport lève `MusicBrainzSature`. Avant le 2026-09-15 un
        lookup en 503 était sauté (`continue`), et un artiste unique dont le
        seul lookup échouait ressortait comme « ambigu » — Kid Cudi, un seul
        homonyme exact, l'était une fois sur deux.
        """
        exacts = candidats_exacts(nom, self.rechercher_artiste(nom))
        if not exacts:
            logger.info(f"MusicBrainz : aucun artiste nommé exactement « {nom} »")
            return None

        candidats = []
        for brut in exacts:
            # Les alias voyagent dans la MÊME requête : aucun appel de plus.
            detail = self.details_artiste(brut["id"], inc="artist-rels+release-groups+aliases")
            candidats.append(
                CandidatArtiste(
                    mbid=brut["id"],
                    nom=brut["name"],
                    type=brut.get("type"),
                    pays=brut.get("country"),
                    desambiguation=brut.get("disambiguation"),
                    albums_communs=compter_albums_communs(detail, nos_albums),
                    relations=relations_membre(detail),
                    aliases=aliases_de(detail),
                )
            )

        retenu = departager(candidats)
        if retenu is None:
            logger.warning(
                f"MusicBrainz : {len(candidats)} homonymes exacts pour « {nom} », "
                "aucun ne se détache — rien n'est retenu."
            )
            return None
        logger.info(
            f"MusicBrainz : « {nom} » → {retenu.mbid} "
            f"({retenu.desambiguation or retenu.type or '?'}), "
            f"{retenu.albums_communs} album(s) commun(s), {len(retenu.relations)} relation(s)"
        )
        return retenu

    def close(self) -> None:
        self.session.close()
