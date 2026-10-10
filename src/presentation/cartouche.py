"""Le cartouche de l'artiste : nom, formations, décompte, audience.

Deux règles de la refonte (décisions utilisateur 2026-10-11) :

  · les auditeurs mensuels Spotify et YouTube Music sont montrés SÉPARÉMENT —
    ce sont des publics qui se recouvrent, leur somme serait un nombre que
    personne ne publie ;
  · les streams MESURÉS passent d'abord ; l'estimation par part de marché n'est
    qu'une infobulle, qui dit son hypothèse.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.presentation import format as fmt
from src.presentation.table import LigneMorceau
from src.utils.formations import nature_alias
from src.utils.participation import Participation
from src.utils.streams_calculator import SPOTIFY_SHARE, YTM_SHARE, total_discographie
from src.utils.title_matching import cle_album
from src.utils.track_validation import Contexte

#: Types de disque qui comptent comme « albums » de l'artiste. Un disque connu
#: mais non typé compte aussi : on ne le retire pas sur un doute.
_TYPES_ALBUM = {"album", "ep", None}


@dataclass(frozen=True)
class Formation:
    nom: str
    #: « alias » · « groupe » · « collectif » · « formation » (nature non tranchée)
    nature: str
    annee: str = ""

    @property
    def texte(self) -> str:
        return f"{self.nom} ({self.annee})" if self.annee else self.nom


@dataclass(frozen=True)
class Cartouche:
    nom: str
    alias: tuple[Formation, ...] = ()
    groupes: tuple[Formation, ...] = ()
    collectifs: tuple[Formation, ...] = ()
    ligne_titres: str = ""
    ligne_audience: str = ""
    infobulle_audience: str = ""
    compteurs: dict[str, int] = field(default_factory=dict)


def formations_confirmees(relations) -> tuple[tuple[Formation, ...], ...]:
    """(alias, groupes, collectifs) — les liens CONFIRMÉS seulement (les seuls
    que les lecteurs voient, e26). Seuls les noms de SCÈNE vont au cartouche :
    état civil et graphies (« Travis $cott ») restent dans la fenêtre Groupes."""
    alias, groupes, collectifs = [], [], []
    for r in relations or ():
        if r.status != "confirmed":
            continue
        annee = (r.begin_date or "")[:4]
        if r.kind == "alias":
            if nature_alias(r.detail, r.source) == "scene":
                alias.append(Formation(r.related_name, "alias", annee))
        elif r.kind == "member_of":
            if r.formation == "collectif":
                collectifs.append(Formation(r.related_name, "collectif", annee))
            else:
                groupes.append(Formation(r.related_name, r.formation or "formation", annee))
    return tuple(alias), tuple(groupes), tuple(collectifs)


def _pluriel(n: int, singulier: str, pluriel: str | None = None) -> str:
    return f"{fmt.nombre(n)} {singulier if n <= 1 else (pluriel or singulier + 's')}"


def nb_albums(lignes: list[LigneMorceau], ctx: Contexte) -> int:
    """Albums et EP PROPRES : disques des morceaux principaux publiés, hors
    singles et compilations (type Deezer, `albums.record_type`)."""
    disques = set()
    for ligne in lignes:
        if ligne.participation is not Participation.PRINCIPAL or ligne.inedit:
            continue
        album = (ligne.valeurs.get("album") or "").strip()
        if not album:
            continue
        cle = cle_album(album)
        if ctx.types_par_album.get(cle) in _TYPES_ALBUM:
            disques.add(cle)
    return len(disques)


def construire(
    artist,
    lignes: list[LigneMorceau],
    ctx: Contexte,
    *,
    relations=(),
    noms=(),
    formations=(),
) -> Cartouche:
    par_categorie = {p: 0 for p in Participation}
    for ligne in lignes:
        par_categorie[ligne.participation] += 1
    desactives = sum(1 for ligne in lignes if ligne.desactive)
    albums = nb_albums(lignes, ctx)

    detail = " · ".join(
        _pluriel(n, singulier, pluriel)
        for n, singulier, pluriel in (
            (par_categorie[Participation.PRINCIPAL], "principal", "principaux"),
            (par_categorie[Participation.FEAT], "feat", "feats"),
            (par_categorie[Participation.PROD], "prod", "prods"),
            (par_categorie[Participation.SECONDAIRE], "rôle secondaire", "rôles secondaires"),
        )
        if n
    )
    morceaux = [_pluriel(len(lignes), "titre") + (f" ({detail})" if detail else "")]
    if albums:
        morceaux.append(_pluriel(albums, "album"))
    if desactives:
        morceaux.append(_pluriel(desactives, "titre désactivé", "titres désactivés"))
    ligne_titres = " • ".join(morceaux)

    actifs = [ligne.track for ligne in lignes if not ligne.desactive]
    mesure = total_discographie(actifs, noms, formations, mesure=True)
    estime = total_discographie(actifs, noms, formations)
    audience = []
    sp_ml = getattr(artist, "spotify_monthly_listeners", None)
    yt_ml = getattr(artist, "ytm_monthly_listeners", None)
    if sp_ml:
        audience.append(f"{fmt.compact(sp_ml)} d'auditeurs mensuels Spotify")
    if yt_ml:
        audience.append(f"{fmt.compact(yt_ml)} sur YouTube Music")
    if mesure:
        audience.append(f"{fmt.compact(mesure)} de streams mesurés")
    infobulle = ""
    if mesure:
        infobulle = (
            f"Mesuré : {fmt.nombre(mesure)} (Spotify + YouTube Music, publiés par les "
            f"plateformes).\nEstimé toutes plateformes : ≈ {fmt.nombre(estime)} — hypothèse : "
            f"Spotify ≈ {round(SPOTIFY_SHARE * 100)} % et YouTube Music ≈ "
            f"{round(YTM_SHARE * 100)} % du marché du streaming en France."
        )

    alias, groupes, collectifs = formations_confirmees(relations)
    return Cartouche(
        nom=artist.name if artist else "",
        alias=alias,
        groupes=groupes,
        collectifs=collectifs,
        ligne_titres=ligne_titres,
        ligne_audience=" • ".join(audience),
        infobulle_audience=infobulle,
        compteurs={
            "titres": len(lignes),
            "albums": albums,
            "desactives": desactives,
            **{str(p): n for p, n in par_categorie.items()},
        },
    )
