"""Le titre de la page Spotify d'un ID (`tracks.spotify_page_title`), relu
dans le BRUT : la dernière lecture de la page `/embed/` de l'ID principal.

Le panneau juge « l'ID Spotify d'une autre version » (`revue.id_spotify_autre_version`)
sur ce titre. Mesuré le 2026-10-31 sur une copie : 22 fiches sur 1 638 portaient
un titre qui ne décrit pas la page actuelle de leur ID — lu sous une forme
ancienne (« Know No Better » pour l'ID de « Know No Better - Laibert Remix »,
« MELTDOWN (feat. Drake) » pour « … - Instrumental »), ou hérité d'un ID
précédent (« Moon » : « Man On The Moon • Kid Cudi » pour « Moon • Kanye
West »). Les cas du panneau en venaient (MELTDOWN, Laibert, FRANCHISE).
"""

from __future__ import annotations

from pathlib import Path

from src.persistence import brut
from src.scrapers.spotify_id_scraper_v2 import SpotifyIDScraper


def identifiant_embed(requete: str) -> str:
    """PUR. `…/embed/track/<ID>?…` → `<ID>`."""
    return requete.rsplit("/", 1)[-1].split("?", 1)[0]


def dernieres_lectures(chemin: Path | None = None) -> dict[str, bytes]:
    """`{ID: corps}` de la DERNIÈRE lecture embed de chaque ID dans le brut
    (lecture seule)."""
    dernieres: dict[str, tuple[str, bytes]] = {}
    for requete, corps, _premiere, derniere in brut.parcourir("spotify_embed", chemin):
        sid = identifiant_embed(requete)
        if sid not in dernieres or str(derniere) > str(dernieres[sid][0]):
            dernieres[sid] = (str(derniere), corps)
    return {sid: corps for sid, (_d, corps) in dernieres.items()}


def titre_lu(corps: bytes) -> str | None:
    """PUR. Le titre de page (« Titre • Artiste, Artiste ») d'une lecture embed,
    sous la forme qu'écrit le provider ; None si la page ne dit rien."""
    identite = SpotifyIDScraper._identite_depuis_embed(corps.decode("utf-8", "replace"))
    return SpotifyIDScraper._titre_de_page(identite)


def ecarts(fiches: list[dict], lectures: dict[str, bytes]) -> list[dict]:
    """PUR. Les fiches dont le titre stocké n'est pas celui de la dernière
    lecture de la page de leur ID : `{**fiche, "neuf": titre}`. Sans lecture,
    ou une lecture muette : rien (on ne vide pas). Un titre VIDE reste vide : le
    remplir ferait juger au panneau des fiches qu'il ne jugeait pas (simulé le
    2026-10-31 : 15 cas `id_spotify_version` de plus, des ID acceptés à la main
    surtout) — le rafraîchissement ne corrige que ce qui ment."""
    sortie = []
    for f in fiches:
        if not f["page"]:
            continue
        corps = lectures.get(f["spotify_id"])
        neuf = titre_lu(corps) if corps else None
        if neuf and neuf != f["page"]:
            sortie.append({**f, "neuf": neuf})
    return sortie
