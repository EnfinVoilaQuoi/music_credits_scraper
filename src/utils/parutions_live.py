"""Les parutions LIVE selon MusicBrainz, relues dans le BRUT (e64).

La recherche MusicBrainz par code-barres (`parutions_par_code_barres`) rend le
release-group de chaque parution avec ses types secondaires ; « Live » dit un
album live. Avant e64 la réponse était lue sans garder ce type : ce module le
relit dans les recherches gardées, pour poser `releases.mb_live` sur les
parutions déjà en base (`scripts/marquer_parutions_live.py`).

Le code-barres d'une parution : `releases.upc`, sinon celui de son album
Deezer lu dans le brut (`/album/<id>`), comparés par `cle_upc`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from src.api.musicbrainz_api import est_live
from src.persistence import brut
from src.utils.codes_barres import cle_upc

_ALBUM_DEEZER = re.compile(r"^https://api\.deezer\.com/album/(\d+)$")


def _json(corps: bytes) -> dict:
    try:
        d = json.loads(corps)
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def live_par_code_barres(recherches) -> dict[str, bool]:
    """PUR. `{clé UPC: live}` des recherches MusicBrainz `(requête, corps)` :
    vrai si une parution du code-barres a un release-group « Live », faux si
    MusicBrainz l'a rendu sans ce type."""
    sortie: dict[str, bool] = {}
    for requete, corps in recherches:
        if "barcode" not in requete:
            continue
        for p in _json(corps).get("releases") or []:
            cle = cle_upc(p.get("barcode"))
            if cle:
                sortie[cle] = sortie.get(cle, False) or est_live(p)
    return sortie


def upc_des_albums_deezer(lectures) -> dict[int, str]:
    """PUR. `{album Deezer: clé UPC}` des lectures `/album/<id>` `(requête, corps)`."""
    sortie: dict[int, str] = {}
    for requete, corps in lectures:
        m = _ALBUM_DEEZER.match(requete)
        cle = cle_upc(_json(corps).get("upc")) if m else None
        if cle:
            sortie[int(m.group(1))] = cle
    return sortie


def a_marquer(parutions, live_par_upc: dict, upc_par_album: dict) -> dict[int, bool]:
    """PUR. `{release_id: live}` à poser : les parutions dont le code-barres
    a une réponse MusicBrainz et dont `mb_live` diffère. `parutions` :
    `[{id, upc, deezer_album_ids, mb_live}]`."""
    sortie = {}
    for p in parutions:
        cles = {cle_upc(p.get("upc"))} | {
            upc_par_album.get(a) for a in p.get("deezer_album_ids") or ()
        }
        lus = [live_par_upc[c] for c in cles if c and c in live_par_upc]
        if not lus:
            continue
        live = any(lus)
        if p.get("mb_live") is None or bool(p["mb_live"]) != live:
            sortie[p["id"]] = live
    return sortie


def lire_le_brut(chemin: Path | None = None) -> tuple[dict, dict]:
    """`(live_par_upc, upc_par_album)` depuis le brut (lecture seule)."""
    recherches = ((r, c) for r, c, _p, _d in brut.parcourir("musicbrainz", chemin))
    albums = ((r, c) for r, c, _p, _d in brut.parcourir("deezer", chemin))
    return live_par_code_barres(recherches), upc_des_albums_deezer(albums)
