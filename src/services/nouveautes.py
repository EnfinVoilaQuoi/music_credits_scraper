"""« Y a-t-il des titres que nous n'avons pas ? » — un appel, une fois par jour.

Ce que compte la vérification : les morceaux que **Genius liste et que la base
ignore**. C'est littéralement « des titres à récupérer », et cela couvre aussi
bien un inédit qu'une vraie sortie — sans avoir à trancher lequel des deux,
décision que l'API ne permet pas de prendre de façon fiable.

UN appel réseau (`artist_songs`, page 1 triée par date de sortie, 50 titres) :
le badge n'a pas à être exhaustif, il a à être JUSTE sur ce qu'il montre. Un
plafond journalier (`nouveautes_cache`) évite qu'ouvrir trois fois un artiste
coûte trois appels.

**Une erreur réseau ne ment pas** : elle rend une liste vide AVEC un motif, et
l'appelant affiche « vérification impossible » plutôt qu'un rassurant « 0 ».
C'est la même règle que `absent` en observabilité — ce qu'on n'a pas pu lire
n'accuse personne, et ne dédouane personne non plus.

**Le badge trie comme l'import** (2026-10-15) : la liste Genius d'un artiste
porte aussi les morceaux d'AUTRES artistes où il n'a qu'un rôle secondaire — les
covers d'A2H par Emma Peters et Dinaa, où il est crédité auteur. L'import par
défaut les ignore ; le badge les comptait comme « nouveaux titres ». Ils sont
désormais à part (`secondaires`) : signalés, jamais comptés sur le bouton.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime

import requests

from src.config import DELAY_BETWEEN_REQUESTS
from src.utils import nouveautes_cache
from src.utils.logger import etape_de_flux, get_logger

logger = get_logger(__name__)

#: Une page suffit : au-delà, ce n'est plus « une nouveauté », c'est une
#: discographie jamais récupérée — et c'est ce que le bandeau doit dire.
PAR_PAGE = 50

#: Appels détail au plus par vérification, pour les pages que la liste ne suffit
#: pas à trancher. Au-delà (discographie jamais récupérée), ces pages restent
#: comptées comme nouveautés : c'est ce que faisait le badge avant le tri.
PLAFOND_DETAIL = 10
PAUSE_DETAIL = DELAY_BETWEEN_REQUESTS


@dataclass(frozen=True)
class Nouveaute:
    genius_id: int
    titre: str
    artiste_principal: str = ""
    date: str | None = None
    #: Rôle secondaire de l'artiste sur la page (« Writer »…), None pour un titre
    #: que l'import par défaut récupère (principal, feat, prod).
    role: str | None = None


@dataclass(frozen=True)
class Verification:
    """Le verdict, et d'où il sort."""

    nouveautes: tuple[Nouveaute, ...] = ()
    verifie_le: datetime | None = None
    depuis_le_cache: bool = False
    #: Renseigné quand on n'a PAS pu conclure (réseau, artiste sans genius_id).
    motif: str | None = None
    manquants: list = field(default_factory=list, repr=False)
    #: Pages où l'artiste n'a qu'un rôle secondaire : l'import ne les prend
    #: qu'avec l'option « rôles secondaires ». Hors du compte du badge.
    secondaires: tuple[Nouveaute, ...] = ()

    @property
    def nombre(self) -> int:
        return len(self.nouveautes)

    @property
    def nombre_secondaires(self) -> int:
        return len(self.secondaires)

    @property
    def concluante(self) -> bool:
        return self.motif is None


def _nouveaute_de(n: dict) -> Nouveaute:
    return Nouveaute(
        genius_id=int(n.get("genius_id") or 0),
        titre=n.get("titre") or "",
        artiste_principal=n.get("artiste_principal") or "",
        date=n.get("date"),
        role=n.get("role"),
    )


def _vers_le_cache(n: Nouveaute) -> dict:
    entree = {
        "genius_id": n.genius_id,
        "titre": n.titre,
        "artiste_principal": n.artiste_principal,
        "date": n.date,
    }
    if n.role:
        entree["role"] = n.role
    return entree


def _depuis_le_cache(entree: dict) -> Verification:
    try:
        vu = datetime.fromisoformat(entree["verifie_le"])
    except (KeyError, TypeError, ValueError):
        vu = None
    return Verification(
        nouveautes=tuple(_nouveaute_de(n) for n in entree.get("nouveautes") or []),
        secondaires=tuple(_nouveaute_de(n) for n in entree.get("secondaires") or []),
        verifie_le=vu,
        depuis_le_cache=True,
    )


def _trier_par_role(runtime, artist, chansons: list[dict]) -> tuple[list, list]:
    """Sépare ce que l'import par défaut récupère (principal, feat, prod) des
    pages où l'artiste n'a qu'un rôle secondaire — la règle de
    `GeniusAPI.get_artist_songs`. La liste tranche le plus souvent ; le reste
    passe par le détail, comme à l'import. Une page où le détail ne crédite pas
    l'artiste (homonyme, tag douteux) disparaît : l'import la jette aussi.
    """
    from src.api.genius_api import GeniusAPI

    titres, secondaires = [], []
    appels = 0
    for c in chansons:
        nouveaute = Nouveaute(
            genius_id=int(c["id"]),
            titre=c.get("title") or "",
            artiste_principal=(c.get("primary_artist") or {}).get("name") or "",
            date=(c.get("release_date_for_display") or c.get("release_date")),
        )
        if GeniusAPI.credite_dans_la_liste(c, artist) or appels >= PLAFOND_DETAIL:
            titres.append(nouveaute)
            continue
        if appels:
            time.sleep(PAUSE_DETAIL)
        appels += 1
        verdict = runtime.genius_api._verify_artist_credit(c["id"], artist.genius_id)
        if verdict is None:
            logger.debug(f"Nouveautés : « {nouveaute.titre} » non crédité au détail, ignoré")
            continue
        nature, role = verdict
        if nature == "secondary" and not GeniusAPI.role_de_prod(role):
            secondaires.append(
                Nouveaute(
                    genius_id=nouveaute.genius_id,
                    titre=nouveaute.titre,
                    artiste_principal=nouveaute.artiste_principal,
                    date=nouveaute.date,
                    role=role,
                )
            )
        else:
            titres.append(nouveaute)
    return titres, secondaires


def _ids_connus(dm, artist) -> set[int]:
    """`genius_id` des morceaux déjà en base pour cet artiste.

    Lus sur l'objet quand la discographie est chargée (cas du GUI), en base
    sinon — la vérification tombe souvent AVANT le chargement des morceaux.
    """
    charges = {t.genius_id for t in (getattr(artist, "tracks", None) or []) if t.genius_id}
    if charges:
        return charges
    return {t.genius_id for t in (dm.get_artist_tracks(artist.id) or []) if t.genius_id}


@etape_de_flux("Nouveautés")
def verifier(runtime, artist, *, force: bool = False, maintenant=None) -> Verification:
    """Les titres Genius absents de la base, au plus une fois par jour.

    `force=True` ignore le cache (bouton « Nouveautés »). Sans `genius_id`
    d'artiste, on ne conclut pas : c'est un motif, pas un zéro.
    """
    maintenant = maintenant or datetime.now()
    if not artist or not getattr(artist, "genius_id", None):
        return Verification(motif="artiste sans identifiant Genius")

    entree = nouveautes_cache.lire(artist.id)
    # Une entrée sans `secondaires` date d'avant le tri par rôle : elle compte
    # les covers comme des nouveautés, on ne la ressert pas.
    if not force and nouveautes_cache.est_frais(entree, maintenant) and "secondaires" in entree:
        return _depuis_le_cache(entree)

    try:
        reponse = runtime.genius_api.genius.artist_songs(
            artist.genius_id, sort="release_date", per_page=PAR_PAGE, page=1
        )
    except (requests.RequestException, ValueError, KeyError, TypeError) as e:
        logger.info(f"Nouveautés : Genius injoignable pour {artist.name} ({e})")
        return Verification(motif=f"Genius injoignable ({e})")

    chansons = (reponse or {}).get("songs") or []
    if not chansons:
        return Verification(motif="aucun morceau rendu par Genius")

    # Ce que l'import écarte volontairement (supprimés, fusionnés, éditions,
    # pages non musicales) n'est pas « à récupérer » : sans ce filtre, le badge
    # le reproposait après chaque run Discographie.
    from src.services.discographie import genius_ids_ecartes
    from src.utils.pages_genius import page_non_morceau

    connus = _ids_connus(runtime.data_manager, artist) | genius_ids_ecartes(runtime, artist)
    inconnues = [
        c
        for c in chansons
        if c.get("id")
        and int(c["id"]) not in connus
        and page_non_morceau(c.get("title") or "", None) != "non-musique"
    ]
    titres, secondaires = _trier_par_role(runtime, artist, inconnues)
    nouveautes_cache.ecrire(
        artist.id,
        {
            "verifie_le": maintenant.isoformat(timespec="seconds"),
            "lus": len(chansons),
            "nouveautes": [_vers_le_cache(n) for n in titres],
            "secondaires": [_vers_le_cache(n) for n in secondaires],
        },
    )
    if titres:
        logger.info(f"🆕 {len(titres)} titre(s) Genius absent(s) de la base pour {artist.name}")
    if secondaires:
        logger.info(
            f"🆕 {len(secondaires)} page(s) en rôle secondaire absente(s) de la base pour "
            f"{artist.name} : {', '.join(f'{n.titre} ({n.role})' for n in secondaires)}"
        )
    return Verification(
        nouveautes=tuple(titres), secondaires=tuple(secondaires), verifie_le=maintenant
    )


def resume(verification: Verification, artist_name: str = "") -> str:
    """Texte unique GUI/CLI. Dit quand on n'a PAS pu conclure."""
    if verification.motif:
        return f"Vérification impossible : {verification.motif}"
    sec = mention_secondaires(verification)
    if not verification.nouveautes:
        return f"Aucun titre à récupérer pour {artist_name}".strip() + sec
    titres = [f"« {n.titre} »" for n in verification.nouveautes[:5]]
    reste = verification.nombre - len(titres)
    ligne = ", ".join(titres) + (f" … et {reste} autre(s)" if reste > 0 else "")
    return f"{verification.nombre} titre(s) Genius absent(s) de la base : {ligne}{sec}"


def mention_secondaires(verification: Verification) -> str:
    """« · 2 en rôle secondaire », ou rien — la suite commune des textes."""
    n = verification.nombre_secondaires
    return f" · {n} en rôle secondaire" if n else ""
