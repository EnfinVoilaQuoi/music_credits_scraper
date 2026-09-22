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
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import requests

from src.utils import nouveautes_cache
from src.utils.logger import get_logger

logger = get_logger(__name__)

#: Une page suffit : au-delà, ce n'est plus « une nouveauté », c'est une
#: discographie jamais récupérée — et c'est ce que le bandeau doit dire.
PAR_PAGE = 50


@dataclass(frozen=True)
class Nouveaute:
    genius_id: int
    titre: str
    artiste_principal: str = ""
    date: str | None = None


@dataclass(frozen=True)
class Verification:
    """Le verdict, et d'où il sort."""

    nouveautes: tuple[Nouveaute, ...] = ()
    verifie_le: datetime | None = None
    depuis_le_cache: bool = False
    #: Renseigné quand on n'a PAS pu conclure (réseau, artiste sans genius_id).
    motif: str | None = None
    manquants: list = field(default_factory=list, repr=False)

    @property
    def nombre(self) -> int:
        return len(self.nouveautes)

    @property
    def concluante(self) -> bool:
        return self.motif is None


def _depuis_le_cache(entree: dict) -> Verification:
    nouveautes = tuple(
        Nouveaute(
            genius_id=int(n.get("genius_id") or 0),
            titre=n.get("titre") or "",
            artiste_principal=n.get("artiste_principal") or "",
            date=n.get("date"),
        )
        for n in entree.get("nouveautes") or []
    )
    try:
        vu = datetime.fromisoformat(entree["verifie_le"])
    except (KeyError, TypeError, ValueError):
        vu = None
    return Verification(nouveautes=nouveautes, verifie_le=vu, depuis_le_cache=True)


def _ids_connus(dm, artist) -> set[int]:
    """`genius_id` des morceaux déjà en base pour cet artiste.

    Lus sur l'objet quand la discographie est chargée (cas du GUI), en base
    sinon — la vérification tombe souvent AVANT le chargement des morceaux.
    """
    charges = {t.genius_id for t in (getattr(artist, "tracks", None) or []) if t.genius_id}
    if charges:
        return charges
    return {t.genius_id for t in (dm.get_artist_tracks(artist.id) or []) if t.genius_id}


def verifier(runtime, artist, *, force: bool = False, maintenant=None) -> Verification:
    """Les titres Genius absents de la base, au plus une fois par jour.

    `force=True` ignore le cache (bouton « Nouveautés »). Sans `genius_id`
    d'artiste, on ne conclut pas : c'est un motif, pas un zéro.
    """
    maintenant = maintenant or datetime.now()
    if not artist or not getattr(artist, "genius_id", None):
        return Verification(motif="artiste sans identifiant Genius")

    entree = nouveautes_cache.lire(artist.id)
    if not force and nouveautes_cache.est_frais(entree, maintenant):
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

    connus = _ids_connus(runtime.data_manager, artist)
    nouveautes = tuple(
        Nouveaute(
            genius_id=int(c["id"]),
            titre=c.get("title") or "",
            artiste_principal=(c.get("primary_artist") or {}).get("name") or "",
            date=(c.get("release_date_for_display") or c.get("release_date")),
        )
        for c in chansons
        if c.get("id") and int(c["id"]) not in connus
    )
    nouveautes_cache.ecrire(
        artist.id,
        {
            "verifie_le": maintenant.isoformat(timespec="seconds"),
            "lus": len(chansons),
            "nouveautes": [
                {
                    "genius_id": n.genius_id,
                    "titre": n.titre,
                    "artiste_principal": n.artiste_principal,
                    "date": n.date,
                }
                for n in nouveautes
            ],
        },
    )
    if nouveautes:
        logger.info(f"🆕 {len(nouveautes)} titre(s) Genius absent(s) de la base pour {artist.name}")
    return Verification(nouveautes=nouveautes, verifie_le=maintenant)


def resume(verification: Verification, artist_name: str = "") -> str:
    """Texte unique GUI/CLI. Dit quand on n'a PAS pu conclure."""
    if verification.motif:
        return f"Vérification impossible : {verification.motif}"
    if not verification.nouveautes:
        return f"Aucun titre à récupérer pour {artist_name}".strip()
    titres = [f"« {n.titre} »" for n in verification.nouveautes[:5]]
    reste = verification.nombre - len(titres)
    ligne = ", ".join(titres) + (f" … et {reste} autre(s)" if reste > 0 else "")
    return f"{verification.nombre} titre(s) Genius absent(s) de la base : {ligne}"
