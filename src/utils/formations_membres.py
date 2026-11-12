"""Nombre de membres d'une FORMATION, lu chez MusicBrainz et Discogs (règle
« 2 membres ⇒ groupe », décision utilisateur 2026-11-13).

Aucune de nos tables ne le porte : il se lit sur la fiche de la FORMATION à
chaque source (MusicBrainz : relations « member of band » d'un lookup ;
Discogs : `members` de la page d'artiste), anciens et actuels compris (les
membres actuels seuls faussent : Lunatic n'en a aucun chez MusicBrainz, WIP ②).
On trouve la formation par la fiche de son MEMBRE, déjà lue par l'étape
identité : MusicBrainz y donne le MBID de chaque formation, Discogs l'ID de
chaque groupe.

MusicBrainz fait foi, Discogs ne peut que le démentir à la hausse : une liste
incomplète d'un côté ne doit jamais faire un duo d'un collectif (One Shot :
Discogs 5, MusicBrainz 7 ; Sniper : Discogs 7, MusicBrainz 4).

Lectures du brut en LECTURE SEULE (`index_du_brut`) ; `lire_les_fiches` est
le seul appel réseau (MusicBrainz et Discogs), sa réponse est gardée dans le
brut par les sessions des deux clients.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import requests
from discogs_client.exceptions import DiscogsAPIError

from src.persistence import brut
from src.utils.formations_genius import cle_de_nom
from src.utils.logger import get_logger

logger = get_logger(__name__)

_MB_LOOKUP = re.compile(r"/ws/2/artist/([0-9a-f-]{36})\?.*inc=[^&]*artist-rels")
_DISCOGS_ARTISTE = re.compile(r"^https://api\.discogs\.com/artists/(\d+)$")
#: Suffixe d'homonyme de Discogs (« Sniper (6) »).
_SUFFIXE_DISCOGS = re.compile(r"\s*\(\d+\)$")


@dataclass
class IndexMembres:
    """Ce que le brut sait des formations : par nom comparable, le MBID et l'ID
    Discogs trouvés chez un membre, et les membres lus sur la fiche même."""

    mbid: dict[str, str] = field(default_factory=dict)
    discogs: dict[str, int] = field(default_factory=dict)
    membres_mb: dict[str, set[str]] = field(default_factory=dict)  # par MBID
    membres_discogs: dict[int, set[str]] = field(default_factory=dict)  # par ID

    def compte(self, nom: str) -> tuple[int | None, int | None]:
        """(membres chez MusicBrainz, chez Discogs), None si non lus."""
        cle = cle_de_nom(nom)
        mb = self.membres_mb.get(self.mbid.get(cle, ""))
        dg = self.membres_discogs.get(self.discogs.get(cle, -1))
        return (len(mb) if mb else None, len(dg) if dg else None)


def _json(corps: bytes) -> dict | None:
    try:
        d = json.loads(corps)
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def _relations_mb(d: dict):
    from src.api.musicbrainz_api import relations_membre

    return relations_membre(d)


def index_du_brut(chemin=None) -> IndexMembres:
    index = IndexMembres()
    mb_lus = brut.dernieres_lectures("musicbrainz", "https://musicbrainz.org/ws/2/artist/", chemin)
    for requete, corps in mb_lus.items():
        m = _MB_LOOKUP.search(requete)
        d = _json(corps) if m else None
        if not d:
            continue
        membres = set()
        for rel in _relations_mb(d):
            if rel.kind == "member_of":
                index.mbid.setdefault(cle_de_nom(rel.nom), rel.mbid)
            else:
                membres.add(cle_de_nom(rel.nom))
        if membres:
            index.membres_mb[m.group(1)] = membres
    discogs_lus = brut.dernieres_lectures("discogs", "https://api.discogs.com/artists/", chemin)
    for requete, corps in discogs_lus.items():
        m = _DISCOGS_ARTISTE.match(requete.split("?")[0])
        d = _json(corps) if m else None
        if not d:
            continue
        for groupe in d.get("groups") or []:
            if isinstance(groupe, dict) and groupe.get("id") and groupe.get("name"):
                nom = _SUFFIXE_DISCOGS.sub("", str(groupe["name"]))
                index.discogs.setdefault(cle_de_nom(nom), int(groupe["id"]))
        membres = {
            cle_de_nom(_SUFFIXE_DISCOGS.sub("", str(x.get("name") or "")))
            for x in d.get("members") or []
            if isinstance(x, dict)
        }
        if membres:
            index.membres_discogs[int(m.group(1))] = membres
    return index


def nombre_de_membres(mb: int | None, discogs: int | None) -> int | None:
    """Le nombre que la règle retient : celui de MusicBrainz, sauf si Discogs en
    donne PLUS (une liste incomplète ne fait pas un duo) ; None sans
    MusicBrainz (Discogs seul ne suffit pas)."""
    if mb is None:
        return None
    return discogs if discogs is not None and discogs > mb else mb


def lire_les_fiches(noms, index: IndexMembres, mb=None, discogs=None) -> list[str]:
    """Lit (réseau) la fiche MusicBrainz et la page Discogs des formations
    `noms` qui ne sont pas encore dans le brut ; rend les noms lus. Une source
    en panne n'empêche pas l'autre ; rien n'est écrit en base."""
    lues = []
    for nom in noms:
        cle = cle_de_nom(nom)
        mbid, gid = index.mbid.get(cle), index.discogs.get(cle)
        if mb is not None and mbid and mbid not in index.membres_mb:
            try:
                mb.details_artiste(mbid)
                lues.append(nom)
            except (RuntimeError, ValueError, requests.RequestException) as e:
                logger.warning(f"Fiche MusicBrainz de « {nom} » illisible : {e}")
        if discogs is not None and gid and gid not in index.membres_discogs:
            try:
                list(discogs.client.artist(gid).members or [])
                lues.append(nom)
            except (DiscogsAPIError, ValueError, requests.RequestException) as e:
                logger.warning(f"Page Discogs de « {nom} » illisible : {e}")
    return lues
