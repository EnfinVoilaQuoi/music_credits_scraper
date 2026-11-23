"""Les lignes de la vue Album : une ligne par PROJET, ses éditions dépliables.

Maquettes du 2026-11-21, colonnes validées le 2026-11-22. Un projet
(`get_projets_for_artist`, e67-e69) réunit les éditions d'un même disque et
porte la nature et les streams d'album. Les morceaux chargés qui ne sont sur
aucun projet vont dans deux lignes de regroupement : Featurings et Singles.

Rien n'est jugé ici : chaque cellule agrège les `LigneMorceau` déjà calculées
(participation, certif, streams) — jamais la base.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

from src.models.track import CreditRole
from src.presentation import format as fmt
from src.presentation.table import Alignement, Colonne, LigneMorceau
from src.utils.participation import Participation

COLONNES_ALBUM: tuple[Colonne, ...] = (
    Colonne("titre", "Album", obligatoire=True),
    Colonne("date", "Date de sortie", Alignement.CENTRE, infobulle="La plus ancienne du projet"),
    Colonne("type", "Type", Alignement.CENTRE, infobulle="Nature du disque (Deezer ou saisie)"),
    Colonne("morceaux", "Morceaux", Alignement.CENTRE, infobulle="Titres (dont ❌ désactivés)"),
    Colonne("credits", "Crédits", Alignement.CENTRE),
    Colonne("paroles", "Paroles", Alignement.CENTRE, infobulle="Titres avec paroles / total"),
    Colonne("duree", "Durée totale", Alignement.CENTRE),
    Colonne("certif", "Certif", Alignement.CENTRE, infobulle="Plus haute certification"),
    Colonne("editions", "Éditions", Alignement.CENTRE, infobulle="Éditions du projet"),
    Colonne("label", "Label", visible=False, infobulle="Label le plus crédité"),
    Colonne(
        "streams",
        "Streams",
        Alignement.DROITE,
        infobulle="Spotify + YouTube ; l'icône dit la seule source quand l'autre manque",
    ),
)

FEATURINGS = "Featurings"
SINGLES = "Singles et hors album"

#: Rang d'une ligne : les projets d'abord, puis les regroupements.
RANG_PROJET, RANG_FEATURINGS, RANG_SINGLES = 0, 1, 2


@dataclass
class LigneAlbum:
    cle: str
    titre: str
    #: Icône de `data/icons` : disque, micro (featurings), liste (singles).
    icone: str
    rang: int
    lignes: list[LigneMorceau]
    valeurs: dict[str, str] = field(default_factory=dict)
    tri: dict[str, object] = field(default_factory=dict)
    infobulles: dict[str, str] = field(default_factory=dict)
    #: « spotify » / « youtube » quand une seule des deux sources compte.
    streams_seule: str | None = None
    editions: list[LigneAlbum] = field(default_factory=list)


def _date(lignes: list[LigneMorceau]) -> tuple[str, str]:
    """(cellule, clé de tri « AAAA-MM-JJ ») de la date la plus ANCIENNE : un
    bonus ajouté plus tard ne fait pas remonter le projet."""
    dates = [(ligne.tri.get("date") or "", ligne.valeurs.get("date") or "") for ligne in lignes]
    dates = [d for d in dates if d[0]]
    if not dates:
        return "", ""
    tri, cellule = min(dates)
    return cellule, tri


def _rang_certif(ligne: LigneMorceau) -> int:
    return -int(ligne.tri.get("certif") or -99)


def _label(lignes: list[LigneMorceau]) -> str:
    compte = Counter(
        c.name
        for ligne in lignes
        for c in ligne.track.credits
        if c.role is CreditRole.LABEL and c.name
    )
    return compte.most_common(1)[0][0] if compte else ""


def _streams(
    lignes: list[LigneMorceau], spotify: int | None, ytm: int | None
) -> tuple[str, int, str | None, str]:
    """Le total d'album s'il existe, sinon la somme des morceaux, par source."""
    sp_somme = not spotify
    yt_somme = not ytm
    sp = spotify or sum(ligne.track.streams.spotify_streams or 0 for ligne in lignes)
    yt = ytm or sum(ligne.track.streams.ytm_streams or 0 for ligne in lignes)
    total = sp + yt
    if not total:
        return "", -1, None, ""
    seule = None if (sp and yt) else ("spotify" if sp else "youtube")
    infobulle = (
        f"Spotify : {fmt.nombre(sp) or '—'}{' (somme des morceaux)' if sp and sp_somme else ''}"
        f" · YouTube : {fmt.nombre(yt) or '—'}{' (somme des morceaux)' if yt and yt_somme else ''}"
    )
    return fmt.nombre(total), total, seule, infobulle


def _remplir(
    a: LigneAlbum,
    *,
    nature: str = "",
    n_editions: int = 0,
    spotify: int | None = None,
    ytm: int | None = None,
) -> LigneAlbum:
    lignes = a.lignes
    n = len(lignes)
    desactives = sum(1 for ligne in lignes if ligne.desactive)
    credits = sum(len(ligne.track.credits) for ligne in lignes)
    paroles = sum(
        1 for ligne in lignes if ligne.track.lyrics.text and str(ligne.track.lyrics.text).strip()
    )
    duree = sum(
        ligne.track.duration or 0 for ligne in lignes if isinstance(ligne.track.duration, int)
    )
    date_txt, date_tri = _date(lignes)
    # `LigneMorceau.tri["certif"]` est le rang NÉGATIF (tri décroissant) ;
    # 98 = écho d'une version, 99 = aucune : ni l'un ni l'autre ne comptent.
    certifiees = [ligne for ligne in lignes if _rang_certif(ligne) < 98]
    meilleure = min(certifiees, key=_rang_certif, default=None)
    streams_txt, streams_total, seule, streams_info = _streams(lignes, spotify, ytm)
    heures, reste = divmod(duree, 3600)
    duree_txt = (f"{heures}h{reste // 60:02d}" if heures else fmt.duree(duree)) if duree else ""
    a.valeurs = {
        "titre": a.titre,
        "date": date_txt,
        "type": nature or "",
        "morceaux": f"{n} ({desactives}❌)" if desactives else str(n),
        "credits": str(credits),
        "paroles": f"{paroles}/{n}",
        "duree": duree_txt,
        "certif": meilleure.valeurs.get("certif", "") if meilleure else "",
        "editions": str(n_editions) if n_editions else "",
        "label": _label(lignes),
        "streams": streams_txt,
    }
    a.tri = {
        "titre": a.titre.casefold(),
        "date": date_tri,
        "type": nature or "",
        "morceaux": n,
        "credits": credits,
        "paroles": paroles / n if n else 0,
        "duree": duree,
        "certif": -(_rang_certif(meilleure) if meilleure else 99),
        "editions": n_editions,
        "label": a.valeurs["label"].casefold(),
        "streams": streams_total,
    }
    a.infobulles = {
        "streams": streams_info,
        "certif": (
            f"{meilleure.infobulles.get('certif', '')} — « {meilleure.track.title} »"
            if meilleure
            else ""
        ),
    }
    a.streams_seule = seule
    return a


def construire(projets: Iterable[dict], lignes: Iterable[LigneMorceau]) -> list[LigneAlbum]:
    """Les lignes de la vue Album, projets triés du plus récent au plus ancien,
    puis Featurings et Singles."""
    lignes = list(lignes)
    par_id = {ligne.id: ligne for ligne in lignes if ligne.id is not None}
    places: set[int] = set()
    resultat: list[LigneAlbum] = []
    for p in projets:
        siennes = [par_id[i] for i in p.get("track_ids", ()) if i in par_id]
        if not siennes:
            continue
        places.update(ligne.id for ligne in siennes)
        editions = p.get("editions") or []
        a = LigneAlbum(
            cle=f"p{p['projet_id']}",
            titre=p.get("titre") or "?",
            icone="disc",
            rang=RANG_PROJET,
            lignes=siennes,
        )
        _remplir(
            a,
            nature=p.get("nature") or "",
            n_editions=len(editions),
            spotify=p.get("spotify_streams"),
            ytm=p.get("ytm_streams"),
        )
        if len(editions) > 1:
            for e in editions:
                de_l_edition = [par_id[i] for i in e.get("track_ids", ()) if i in par_id]
                if de_l_edition:
                    a.editions.append(
                        _remplir(
                            LigneAlbum(
                                cle=f"r{e['release_id']}",
                                titre=e.get("titre") or "?",
                                icone="disc",
                                rang=RANG_PROJET,
                                lignes=de_l_edition,
                            )
                        )
                    )
        resultat.append(a)
    resultat.sort(key=lambda a: a.tri["date"] or "", reverse=True)
    reste = [ligne for ligne in lignes if ligne.id not in places]
    feats = [ligne for ligne in reste if ligne.participation is Participation.FEAT]
    autres = [ligne for ligne in reste if ligne.participation is not Participation.FEAT]
    for titre, icone, rang, groupe in (
        (FEATURINGS, "microphone-2", RANG_FEATURINGS, feats),
        (SINGLES, "playlist", RANG_SINGLES, autres),
    ):
        if groupe:
            resultat.append(
                _remplir(LigneAlbum(cle=titre, titre=titre, icone=icone, rang=rang, lignes=groupe))
            )
    return resultat


def resume(albums: list[LigneAlbum]) -> str:
    """Le texte de gauche du pied en vue Album."""
    projets = [a for a in albums if a.rang == RANG_PROJET]
    editions = sum(max(1, len(a.editions)) for a in projets)
    s = "s" if len(projets) > 1 else ""
    return f"{len(projets)} projet{s} • {editions} édition{'s' if editions > 1 else ''}"
