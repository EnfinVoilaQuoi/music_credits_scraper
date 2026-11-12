"""Une FORMATION chez Genius : son ID, et la part de chacun de nos membres dans
ses morceaux (CORRECTIFS 2026-11-12, « discographies des formations »).

**L'ID se prouve par les morceaux du membre**, jamais par le seul nom : la
recherche Genius par nom prend des homonymes (« Soleil Noir », le rappeur solo
2867423, au lieu du groupe Soleil Noir (FRA) 3044914). La liste
`/artists/{membre}/songs` de Genius donne, pour chaque morceau, son artiste
principal AVEC son ID : un morceau du membre dont l'artiste principal porte le
nom de la formation désigne la formation, par construction (IAM 244 fiches chez
Shurik'n, Lunatic 53, 1995 37). Deux ID au même nom : ambigu, rien n'est posé.

**La nature (groupe / collectif) se lit dans les morceaux de la formation** :
part des pages où la formation est principale qui créditent chacun de nos
membres (règle validée par l'utilisateur le 2026-11-12, `nature_par_les_parts`).
La liste `/artists/{id}/songs` d'un artiste rend tous les morceaux où il a un
crédit (principal, invité, auteur, producteur) : un morceau de la formation y
figure si le membre y est crédité.

Fonctions PURES sur les réponses de Genius, plus deux lectures du brut (en
lecture seule) : rien ici n'écrit ni n'appelle le réseau.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from src.persistence import brut
from src.utils.credit_normalize import display_name
from src.utils.title_matching import normalize_name
from src.utils.version_descriptors import titre_generique

SOURCE = "genius_api"


def prefixe_de_liste(genius_id: int) -> str:
    """Début de la requête gardée des pages `/artists/{id}/songs` (la même que
    `GeniusAPI._get_artist_songs_manual` : `sort=release_date`, 50 par page)."""
    return f"https://api.genius.com/artists/{int(genius_id)}/songs?"


def chansons_du_brut(genius_id: int, chemin=None) -> list[dict]:
    """Les morceaux de la liste `/artists/{id}/songs` gardée dans le brut, la
    lecture la plus récente de chaque page, dédoublonnés par ID de page."""
    vues: dict[int, dict] = {}
    for corps in brut.dernieres_lectures(SOURCE, prefixe_de_liste(genius_id), chemin).values():
        try:
            reponse = json.loads(corps).get("response") or {}
        except (ValueError, AttributeError):
            continue
        for chanson in reponse.get("songs") or []:
            if isinstance(chanson, dict) and chanson.get("id") is not None:
                vues[int(chanson["id"])] = chanson
    return list(vues.values())


def _ids(artistes) -> set[int]:
    out = set()
    for a in artistes or []:
        if isinstance(a, dict) and a.get("id") is not None:
            try:
                out.add(int(a["id"]))
            except (TypeError, ValueError):
                continue
    return out


def _principaux(chanson: dict) -> list[dict]:
    artistes = [chanson.get("primary_artist"), *(chanson.get("primary_artists") or [])]
    return [a for a in artistes if isinstance(a, dict) and a.get("id") is not None]


def ids_principaux(chanson: dict) -> set[int]:
    """ID des artistes principaux d'un morceau de l'API."""
    return _ids(_principaux(chanson))


def cle_de_nom(nom: str | None) -> str:
    """Nom comparable : sans le suffixe régional de Genius (« Soleil Noir
    (FRA) »), normalisé."""
    return normalize_name(display_name(nom or ""))


@dataclass(frozen=True)
class IdTrouve:
    """L'ID Genius d'une formation lu dans les morceaux d'un membre."""

    genius_id: int | None
    #: Morceaux du membre où cet ID est artiste principal (la preuve).
    pages: int = 0
    #: Tous les ID vus au nom de la formation (plusieurs = ambigu).
    candidats: tuple[int, ...] = ()

    @property
    def ambigu(self) -> bool:
        return len(self.candidats) > 1


def id_par_les_morceaux(chansons: Iterable[dict], nom: str) -> IdTrouve:
    """L'ID de l'artiste principal au nom de `nom` (nom comparable égal) dans
    les morceaux d'un membre ; None si aucun ou plusieurs."""
    cle = cle_de_nom(nom)
    if not cle:
        return IdTrouve(None)
    compte: Counter[int] = Counter()
    for chanson in chansons:
        compte.update(
            {int(a["id"]) for a in _principaux(chanson) if cle_de_nom(a.get("name")) == cle}
        )
    candidats = tuple(sorted(compte))
    if len(candidats) != 1:
        return IdTrouve(None, 0, candidats)
    return IdTrouve(candidats[0], compte[candidats[0]], candidats)


def pages_de_la_formation(chansons: Iterable[dict], formation_id: int) -> list[dict]:
    """Les morceaux où la formation est artiste principal, hors titres
    génériques (intro, interlude, skit : le membre y est souvent absent sans
    que cela dise rien de la formation — mesure du WIP ②)."""
    return [
        c
        for c in chansons
        if int(formation_id) in _ids(_principaux(c)) and not titre_generique(c.get("title"))
    ]


def credite(chanson: dict, membre_id: int, pages_du_membre: set[int]) -> bool:
    """Le membre est crédité sur ce morceau : la page figure dans SA liste
    Genius (tout crédit), ou il en est artiste principal ou invité."""
    if int(chanson["id"]) in pages_du_membre:
        return True
    return int(membre_id) in (_ids(_principaux(chanson)) | _ids(chanson.get("featured_artists")))


@dataclass(frozen=True)
class Part:
    """Part d'un de nos membres dans les morceaux de la formation."""

    membre: str
    credites: int
    total: int

    @property
    def taux(self) -> float:
        return self.credites / self.total if self.total else 0.0


def part_du_membre(
    membre: str, pages: list[dict], membre_id: int, pages_du_membre: set[int]
) -> Part:
    return Part(membre, sum(1 for c in pages if credite(c, membre_id, pages_du_membre)), len(pages))


# ── La règle de nature (validée par l'utilisateur le 2026-11-12) ─────────────

#: Tous nos membres au moins à cette part ⇒ groupe. 80 % et non 75 % (décision
#: utilisateur 2026-11-13, après simulation) : à 75 %, 667 (Freeze Corleone 76 %,
#: un meneur dans un collectif) passait en groupe, le sens qui apporte des
#: morceaux en trop ; à 80 %, 1995, Sniper et Team BS vont à l'humain.
SEUIL_GROUPE = 0.80
#: Un de nos membres sous cette part ⇒ collectif.
SEUIL_COLLECTIF = 0.50
#: Morceaux de la formation en dessous desquels la mesure ne tranche pas
#: (T-Kaï Cee : 7 pages, groupe tranché à la main).
CREDITES_MIN = 8

GROUPE, COLLECTIF = "groupe", "collectif"


@dataclass(frozen=True)
class Nature:
    """Verdict de la règle : `nature` None = à l'humain, avec le motif."""

    nature: str | None
    motif: str
    #: Une part nulle : homonyme Genius ou faux lien — signalé, jamais tranché.
    signale: bool = False


def nature_par_les_parts(parts: list[Part], nb_membres: int | None = None) -> Nature:
    """Groupe ou collectif, d'après la part de NOS membres (ceux de la base)
    dans les morceaux Genius de la formation.

    2 membres ⇒ groupe ; sinon tous ≥ 75 % ⇒ groupe, l'un < 50 % ⇒ collectif,
    entre les deux, ou moins de 8 morceaux de la formation ⇒ l'humain ;
    une part nulle ⇒ signalée (homonyme ou faux lien), jamais tranchée.
    `nb_membres` : nombre de membres de la formation, s'il est connu."""
    if not parts:
        return Nature(None, "aucun membre mesuré")
    nuls = [p for p in parts if p.credites == 0]
    if nuls:
        noms = ", ".join(p.membre for p in nuls)
        return Nature(None, f"aucun morceau ne crédite {noms} : homonyme ou faux lien", True)
    detail = ", ".join(f"{p.membre} {p.credites}/{p.total} ({p.taux:.0%})" for p in parts)
    if nb_membres == 2:
        return Nature(GROUPE, f"deux membres ; {detail}")
    if any(p.total < CREDITES_MIN for p in parts):
        return Nature(None, f"moins de {CREDITES_MIN} morceaux de la formation : {detail}")
    if any(p.taux < SEUIL_COLLECTIF for p in parts):
        return Nature(COLLECTIF, f"un membre sous {SEUIL_COLLECTIF:.0%} : {detail}")
    if all(p.taux >= SEUIL_GROUPE for p in parts):
        return Nature(GROUPE, f"tous à {SEUIL_GROUPE:.0%} ou plus : {detail}")
    return Nature(None, f"entre {SEUIL_COLLECTIF:.0%} et {SEUIL_GROUPE:.0%} : {detail}")
