"""Le juge des photos d'artiste gardées comme pochettes de morceaux isolés.

Sans pochette, Genius sert la photo d'un artiste crédité comme
`song_art_image_url` (2 827 morceaux sur 10 730 du brut, 2026-11-03) ;
`media_enricher` la gardait comme pochette des morceaux sans album.
`GeniusAPI._extract_song_art` l'écarte désormais. Deux lecteurs :

- `scripts/retirer_pochettes_photo_artiste.py` (rattrapage) ;
- l'audit des données (`services.audit.pochettes_photo`, pour information).

Le juge : un morceau sans album dont la page Genius (lue dans le brut) sert la
photo d'un artiste crédité, ET dont le fichier est un PNG / WebP (Deezer ne
sert que du JPEG) ou identique à celui d'un AUTRE morceau. Il n'est pas sûr :
la photo d'un artiste est parfois la pochette d'un de ses disques (« Drunk »
de Thundercat, *Artefact* de Swift Guad ; 2026-11-10).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import zlib
from collections import defaultdict
from contextlib import closing
from pathlib import Path


def pages_a_photo(chemin_brut: Path) -> set[int]:
    """Les pages Genius dont la « pochette » est la photo d'un artiste crédité."""
    from src.api.genius_api import GeniusAPI

    pages: set[int] = set()
    vues: set[str] = set()
    if not Path(chemin_brut).exists():
        return pages
    with closing(sqlite3.connect(f"file:{Path(chemin_brut).as_posix()}?mode=ro", uri=True)) as conn:
        lignes = conn.execute(
            "SELECT requete, corps FROM brut WHERE source = 'genius_api' "
            "AND requete LIKE 'https://api.genius.com/songs/%'"
        )
        for requete, corps in lignes:
            page = requete.split("/songs/", 1)[1].split("?", 1)[0]
            if page in vues or not page.isdigit():
                continue
            try:
                song = json.loads(zlib.decompress(corps))["response"]["song"]
            except (zlib.error, ValueError, KeyError, TypeError):
                continue
            vues.add(page)
            art = song.get("song_art_image_url")
            if art and art in GeniusAPI._photos_des_artistes(song):
                pages.add(int(page))
    return pages


def pochettes_a_photo(lignes, dossier: Path | None = None) -> tuple[dict, dict]:
    """`(prouvés, laissés)` : `{fichier: [ligne…]}` des morceaux isolés `lignes`
    (`(artiste, titre, page, …)`, pages déjà jugées par `pages_a_photo`).
    Prouvé : PNG / WebP, ou fichier au contenu partagé par une autre page ;
    laissé : un JPEG unique, qui peut être la pochette Deezer (repli quand
    l'artwork Genius manquait). `dossier` : celui des pochettes, à la place de
    `config.COVER_IMAGES_DIR`."""
    from src.utils.image_downloader import cover_image_path, existing_image

    par_fichier: dict[Path, list] = defaultdict(list)
    for ligne in lignes:
        base = cover_image_path(ligne[0], ligne[1])
        if dossier is not None:
            base = Path(dossier) / base.name
        fichier = existing_image(base)
        if fichier:
            par_fichier[fichier].append(ligne)
    pages_par_contenu: dict[str, set[int]] = defaultdict(set)
    empreintes = {}
    for fichier, du_fichier in par_fichier.items():
        empreintes[fichier] = hashlib.sha256(fichier.read_bytes()).hexdigest()
        pages_par_contenu[empreintes[fichier]] |= {ligne[2] for ligne in du_fichier}
    prouves, laisses = {}, {}
    for fichier in sorted(par_fichier):
        partage = len(pages_par_contenu[empreintes[fichier]]) > 1
        cible = prouves if partage or fichier.suffix.lower() != ".jpg" else laisses
        cible[fichier] = par_fichier[fichier]
    return prouves, laisses
