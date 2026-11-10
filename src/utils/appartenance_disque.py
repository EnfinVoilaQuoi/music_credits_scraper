"""Appartenance d'une fiche à un disque — le juge de la famille d'audit
`hors_du_disque` (vague 3, 2026-11-10), lu aussi par
`scripts/simuler_regles_audit.py --regle e`.

Cas d'origine (JOURNAL 2026-11-03) : « Tulum, México » (#250), rangé par sa
page Genius dans M.A.N, qu'aucune édition Deezer ne contient. Le juge lit le
CATALOGUE dans le brut : la liste des pistes Deezer de la parution
(`album/N/tracks`, sinon `album/N`) et l'album de la piste Deezer de la fiche,
lue par son ID (`track/N`). Simulé le 2026-11-09 sur toute la base : 29 « hors
du disque », dont 19 / 19 confirmés par un témoin indépendant de Deezer (la
liste iTunes de la parution par son code-barres, `temoin_apple`).

Une fiche absente de la liste NUMÉRIQUE n'est pas forcément hors du disque :
une édition physique peut la contenir (c'est ce que dit souvent la page
Genius). D'où une DÉCISION, jamais une erreur ; une fiche sans piste Deezer
lue, ou une liste Deezer trop courte, est indécidable et ne fait pas de cas.
"""

import collections
import json
import re
import sqlite3
import zlib
from contextlib import closing
from pathlib import Path

from src.utils.codes_barres import cle_upc
from src.utils.version_descriptors import meme_disque, meme_titre_de_base, titres_equivalents

#: Sources de place d'un catalogue (`release_tracks.position_source`) : la
#: piste a été vue dans la liste d'un catalogue, la fiche est sur le disque.
PLACES_DE_CATALOGUE = frozenset({"deezer", "apple_music"})

HORS_DU_DISQUE = "hors_du_disque"
AUTRE_EDITION = "information (autre édition du disque)"
LISTE_COURTE = "indécidable (liste Deezer plus courte que la parution)"
PISTE_INCONNUE = "indécidable (piste Deezer de la fiche inconnue)"


def _json_du_brut(corps: bytes) -> dict | None:
    try:
        d = json.loads(zlib.decompress(corps))
    except (zlib.error, ValueError):
        return None
    return d if isinstance(d, dict) and "error" not in d else None


class CatalogueDuBrut:
    """Listes de pistes Deezer et iTunes lues UNE fois dans le brut (le brut
    n'a pas d'index sur `requete` : un LIKE par parution prendrait des heures).
    `brut` : une connexion, ou le chemin du brut (ouvert en lecture seule)."""

    def __init__(self, brut: sqlite3.Connection | Path | str | None):
        #: album Deezer → [{id, title, isrc}]
        self.albums: dict[str, list[dict]] = {}
        #: piste Deezer → {album_id, album_title}
        self.pistes: dict[str, dict] = {}
        #: clé UPC → (IDs de pistes Apple, titres des pistes, noms de l'album)
        self.itunes: dict[str, tuple[set[str], list[str], set[str]]] = {}
        if isinstance(brut, sqlite3.Connection):
            self._charger(brut)
        elif brut is not None and Path(brut).exists():
            uri = f"file:{Path(brut).as_posix()}?mode=ro"
            with closing(sqlite3.connect(uri, uri=True)) as conn:
                self._charger(conn)

    def _charger(self, brut: sqlite3.Connection) -> None:
        listes, fiches_album = {}, {}
        for requete, corps in brut.execute(
            "SELECT requete, corps FROM brut WHERE source = 'deezer' AND ("
            "requete LIKE 'https://api.deezer.com/album/%' OR "
            "requete LIKE 'https://api.deezer.com/track/%') ORDER BY derniere_lecture"
        ):
            m = re.match(r"https://api\.deezer\.com/(album|track)/(\d+)(/tracks)?", requete)
            if not m or (d := _json_du_brut(corps)) is None:
                continue
            if m[1] == "track":
                alb = d.get("album") or {}
                if alb.get("id"):
                    self.pistes[m[2]] = {
                        "album_id": str(alb["id"]),
                        "album_title": alb.get("title"),
                    }
            elif m[3]:
                listes[m[2]] = d.get("data")
            else:
                fiches_album[m[2]] = (d.get("tracks") or {}).get("data")
        for album, data in {**fiches_album, **listes}.items():
            if isinstance(data, list):
                self.albums[album] = [
                    {"id": str(p.get("id")), "title": p.get("title"), "isrc": p.get("isrc")}
                    for p in data
                ]
        for requete, corps in brut.execute(
            "SELECT requete, corps FROM brut WHERE source = 'itunes' AND requete LIKE '%upc=%' "
            "ORDER BY derniere_lecture"
        ):
            m = re.search(r"upc=(\d+)", requete)
            if not m or (d := _json_du_brut(corps)) is None or not (cle := cle_upc(m[1])):
                continue
            res = d.get("results") or []
            self.itunes[cle] = (
                {str(x["trackId"]) for x in res if x.get("trackId")},
                [x["trackName"] for x in res if x.get("trackName")],
                {x["collectionName"] for x in res if x.get("collectionName")},
            )


def verdict(
    dans_la_liste: bool | None,
    place_par_un_catalogue: bool,
    album_de_la_piste: str | None,
    album_de_la_piste_est_la_parution: bool,
    titre_de_la_parution: str | None,
    liste_complete: bool = True,
    dans_une_autre_edition: bool = False,
) -> str | None:
    """PUR. None = pas de cas.

    `dans_la_liste` : la fiche est dans la liste Deezer de la parution (ID,
    édition, ISRC ou titre équivalent ; None = liste inconnue). L'album de la
    piste Deezer de la FICHE (lue par son ID) départage : le même disque sous
    une autre édition est une information, un autre disque un cas.
    `liste_complete` : faux quand l'album Deezer a moins de pistes que la moitié
    des fiches de la parution (pré-sortie d'une piste, autre édition) ;
    `dans_une_autre_edition` : une autre parution de l'artiste au même nom de
    disque (`meme_disque`) la contient dans sa liste Deezer."""
    if dans_la_liste is None or dans_la_liste or place_par_un_catalogue:
        return None
    if album_de_la_piste_est_la_parution:
        return None  # la liste du brut est incomplète : rien à montrer
    if dans_une_autre_edition:
        return AUTRE_EDITION
    if not liste_complete:
        return LISTE_COURTE
    if not album_de_la_piste:
        return PISTE_INCONNUE
    if meme_disque(album_de_la_piste, titre_de_la_parution):
        return AUTRE_EDITION
    return HORS_DU_DISQUE


def dans_la_liste(fiche, ids_deezer: set[str], isrcs: set[str], liste: list[dict]) -> bool:
    """La fiche est-elle dans cette liste de pistes : par ID Deezer, ISRC, ou
    titre (`titres_equivalents`, `meme_titre_de_base`). Les identifiants
    d'abord : le juge des titres coûte cent fois plus."""
    if any(p["id"] in ids_deezer or (p["isrc"] and p["isrc"] in isrcs) for p in liste):
        return True
    return any(
        titres_equivalents(fiche["title"], p["title"])
        or meme_titre_de_base(fiche["title"], p["title"])
        for p in liste
    )


def temoin_apple(cat: CatalogueDuBrut, upc: str | None, fiche) -> str:
    """Témoin INDÉPENDANT de Deezer : la liste iTunes de la parution (code-barres),
    par l'ID Apple de la fiche puis par le TITRE (l'ID de la fiche peut être
    celui du single : il ne dit pas que l'album n'a pas le morceau)."""
    cle = cle_upc(upc) if upc else None
    if not cle or cle not in cat.itunes:
        return "apple : parution inconnue"
    ids, titres, _noms = cat.itunes[cle]
    if fiche["apple_music_id"] and str(fiche["apple_music_id"]) in ids:
        return "apple : dans le disque (ID)"
    if dans_la_liste(fiche, set(), set(), [{"id": "", "isrc": None, "title": t} for t in titres]):
        return "apple : dans le disque (titre)"
    return "apple : hors du disque"


def liens_hors_liste(c: sqlite3.Connection, cat: CatalogueDuBrut, pistes) -> list[dict]:
    """Les liens fiche → parution que la liste Deezer de la parution ne
    contient pas, avec leur `verdict` (jamais None). `pistes` : les fiches
    jugées (lignes de `tracks` + `artiste`, pages désactivées exclues) — lues
    par l'appelant : relire `tracks` coûte ~9 s (colonnes rangées après les
    paroles). Une fiche dont l'album a été choisi à la main (`album_override`)
    n'est pas jugée : « Tulum, México », sorti de M.A.N le 2026-11-03."""
    ids_parution = collections.defaultdict(set)
    for r in c.execute("SELECT id, deezer_album_id FROM releases WHERE deezer_album_id"):
        ids_parution[r[0]].add(str(r[1]))
    for r in c.execute(
        "SELECT release_id, external_id FROM release_identifiers WHERE source = 'deezer'"
    ):
        ids_parution[r[0]].add(str(r[1]))
    editions = collections.defaultdict(lambda: (set(), set()))
    for r in c.execute("SELECT track_id, deezer_id, isrc FROM track_editions"):
        if r[1]:
            editions[r[0]][0].add(str(r[1]))
        if r[2]:
            editions[r[0]][1].add(r[2])
    sources_rt = collections.defaultdict(set)
    for r in c.execute(
        "SELECT release_track_id, external_track_id FROM release_track_sources "
        "WHERE source = 'deezer'"
    ):
        if r[1]:
            sources_rt[r[0]].add(str(r[1]))
    liees = collections.Counter(r[0] for r in c.execute("SELECT release_id FROM release_tracks"))
    parutions_de = collections.defaultdict(list)  # artiste → [(id, titre)]
    for r in c.execute("SELECT id, artist_id, title FROM releases"):
        if any(a in cat.albums for a in ids_parution.get(r[0], ())):
            parutions_de[r[1]].append((r[0], r[2]))  # celles qui ont une liste

    def liste_de(release_id: int) -> list[dict]:
        return [
            p for a in ids_parution.get(release_id, ()) if a in cat.albums for p in cat.albums[a]
        ]

    fiches = {t["id"]: t for t in pistes}
    out = []
    for rt in c.execute(
        "SELECT rt.id AS rt_id, rt.track_id, rt.source, rt.external_track_id, rt.position_source, "
        "r.id AS release_id, r.title AS parution, r.scope, r.upc, r.credited_artist_name, "
        "r.artist_id AS artiste_parution "
        "FROM release_tracks rt JOIN releases r ON r.id = rt.release_id"
    ).fetchall():
        f = fiches.get(rt["track_id"])
        if f is None or f["album_override"] or rt["position_source"] in PLACES_DE_CATALOGUE:
            continue  # une place de catalogue : la fiche est sur le disque
        albums = [a for a in ids_parution.get(rt["release_id"], ()) if a in cat.albums]
        if not albums:
            continue
        eds_ids, eds_isrc = editions[f["id"]]
        ids = (
            eds_ids | sources_rt[rt["rt_id"]] | ({str(f["deezer_id"])} if f["deezer_id"] else set())
        )
        if rt["source"] == "deezer" and rt["external_track_id"]:
            ids.add(str(rt["external_track_id"]))
        isrcs = eds_isrc | ({f["isrc"]} if f["isrc"] else set())
        liste = [p for a in albums for p in cat.albums[a]]
        piste = cat.pistes.get(str(f["deezer_id"])) if f["deezer_id"] else None
        if bool(piste) and piste["album_id"] in albums:
            continue  # la liste du brut est incomplète : rien à montrer
        if dans_la_liste(f, ids, isrcs, liste):
            continue
        # Une autre édition du disque (cher : `meme_disque` sur les parutions
        # de l'artiste), cherchée seulement pour un lien hors de la liste.
        autre_edition = any(
            rid != rt["release_id"]
            and meme_disque(titre, rt["parution"])
            and dans_la_liste(f, ids, isrcs, liste_de(rid))
            for rid, titre in parutions_de[rt["artiste_parution"]]
        )
        v = verdict(
            False,
            False,
            (piste or {}).get("album_title"),
            False,
            rt["parution"],
            liste_complete=len(liste) * 2 >= liees[rt["release_id"]],
            dans_une_autre_edition=autre_edition,
        )
        out.append(
            {
                "fiche": f,
                "release_id": rt["release_id"],
                "parution": rt["parution"],
                "scope": rt["scope"],
                "credite": rt["credited_artist_name"],
                "lien": f"{rt['source']} / place {rt['position_source']}",
                "album_deezer": (piste or {}).get("album_title"),
                "verdict": v,
                "upc": rt["upc"],
                "pistes_deezer": len(liste),
            }
        )
    return out
