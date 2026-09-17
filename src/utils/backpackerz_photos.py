"""Magasin des photos The BACKPACKERZ : recherche, téléchargement, CITATION.

Outil AUTONOME (décision utilisateur 2026-09-16) : il ne sert pas encore la
Timeline, il sert d'abord à trouver et garder une photo d'artiste avec sa
citation — pour illustrer un post, ou plus tard un fond de Timeline.

Deux règles :

· **Le fichier est l'état** (comme `image_downloader`) : une photo déjà sur le
  disque n'est pas retéléchargée.
· **La citation survit à tout** : un sidecar `credits.json` PAR DOSSIER
  d'artiste porte, pour chaque photo, le photographe, l'article, sa date et
  l'URL d'origine. Il se FUSIONNE à chaque ajout — jamais réécrit à partir de
  l'entrée courante seule, sans quoi la seconde photo effacerait la citation
  de la première.

Rangement : `data/images/backpackerz/<slug artiste>/<media_id>-<slug titre>.jpg`.
L'identifiant WordPress en tête du nom rend le fichier retrouvable depuis le
sidecar quel que soit le titre (deux photos s'appellent « Isha – Labrador
Bleu » sur le site).

Flux GUI/CLI : les fonctions `rechercher`, `photos_article`, `telecharger`
sont SYNC (elles passent le pont `async_loop.run_sync`) — à appeler depuis un
thread `start_worker`, jamais depuis la boucle.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from src.api.backpackerz_api import Article, BackpackerzApi, Photo, Tag, credit_line
from src.concurrency import async_loop
from src.config import BACKPACKERZ_IMAGES_DIR
from src.observability import source_usage
from src.observability.registry import Flow
from src.utils.image_downloader import download_image, existing_image, slugify_filename
from src.utils.logger import get_logger

logger = get_logger(__name__)

SIDECAR = "credits.json"


@dataclass
class Resultat:
    """Ce que rend une recherche : le tag, les articles, et ce qu'on sait déjà des photos."""

    tag: Tag | None
    articles: list[Article] = field(default_factory=list)
    #: Photos connues par article — au départ la une de chaque article (une
    #: requête pour toutes) ; complétées article par article à la demande.
    photos: dict[int, list[Photo]] = field(default_factory=dict)
    #: Articles dont TOUTES les images ont été demandées (`photos_article`).
    complets: set[int] = field(default_factory=set)
    #: Photos nommées d'après l'artiste mais rattachées à aucun article tagué
    #: (bannière, visuel d'événement) — montrées à part.
    orphelines: list[Photo] = field(default_factory=list)

    @property
    def nb_photos(self) -> int:
        vus = {p.id for lot in self.photos.values() for p in lot}
        return len(vus | {p.id for p in self.orphelines})


# ── Chemins et sidecar (PURS) ────────────────────────────────────────────────


def dossier_artiste(artiste: str) -> Path:
    return BACKPACKERZ_IMAGES_DIR / slugify_filename(artiste, max_len=80)


def chemin_photo(artiste: str, photo: Photo) -> Path:
    """`<dossier>/<id>-<slug titre>.jpg` (extension ajustée au téléchargement)."""
    stem = f"{photo.id}-{slugify_filename(photo.title or 'photo', max_len=80)}"
    return dossier_artiste(artiste) / f"{stem}.jpg"


def photo_locale(artiste: str, photo: Photo) -> Path | None:
    """Le fichier déjà téléchargé pour cette photo, quelle que soit l'extension."""
    return existing_image(chemin_photo(artiste, photo))


def lire_credits(artiste: str) -> dict[str, dict]:
    chemin = dossier_artiste(artiste) / SIDECAR
    if not chemin.exists():
        return {}
    try:
        data = json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"BACKPACKERZ : sidecar illisible ({chemin}) : {exc}")
        return {}
    return data if isinstance(data, dict) else {}


def entree_credit(photo: Photo, article: Article | None, fichier: Path) -> dict:
    """La citation d'UNE photo, telle qu'écrite dans le sidecar."""
    return {
        "file": fichier.name,
        "source_url": photo.source_url,
        "title": photo.title,
        "photographer": photo.photographer,
        "credit_line": credit_line(photo),
        "article_url": article.url if article else None,
        "article_title": article.title if article else None,
        "article_date": article.jour if article else None,
        "photo_date": photo.date[:10],
        "downloaded_at": datetime.now().isoformat(timespec="seconds"),
    }


def enregistrer_credit(artiste: str, photo: Photo, article: Article | None, fichier: Path) -> None:
    """FUSIONNE l'entrée dans le sidecar (les autres photos gardent la leur)."""
    dossier = dossier_artiste(artiste)
    dossier.mkdir(parents=True, exist_ok=True)
    credits = lire_credits(artiste)
    credits[str(photo.id)] = entree_credit(photo, article, fichier)
    chemin = dossier / SIDECAR
    tmp = chemin.with_suffix(".json.part")
    tmp.write_text(json.dumps(credits, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(chemin)


# ── Façade SYNC (GUI, CLI) ───────────────────────────────────────────────────


def _scope(artiste: str, artist_id: int | None):
    return source_usage.run_scope(Flow.MEDIA, artist_id=artist_id, artist_name=artiste)


def rechercher(artiste: str, *, artist_id: int | None = None) -> Resultat:
    """Tag → articles → une de chaque article + photos nommées. Zéro écriture.

    Lève `TagAmbigu` (plusieurs tags exacts) : l'appelant choisit, puis
    rappelle `rechercher_tag`. Aucun article ne charge ici l'intégralité de ses
    images (≈ 1 requête/s : 30 articles = 30 s) — c'est `photos_article`.
    """

    async def _run() -> Resultat:
        api = BackpackerzApi()
        try:
            tag = await api.find_tag(artiste)
            if tag is None:
                return Resultat(tag=None)
            return await _collecter(api, tag, artiste)
        finally:
            await api.aclose()

    with _scope(artiste, artist_id):
        return async_loop.run_sync(_run())


def rechercher_tag(tag: Tag, artiste: str, *, artist_id: int | None = None) -> Resultat:
    """Même chose, à partir d'un tag déjà tranché (cas ambigu)."""

    async def _run() -> Resultat:
        api = BackpackerzApi()
        try:
            return await _collecter(api, tag, artiste)
        finally:
            await api.aclose()

    with _scope(artiste, artist_id):
        return async_loop.run_sync(_run())


async def _collecter(api: BackpackerzApi, tag: Tag, artiste: str) -> Resultat:
    articles = await api.articles(tag.id)
    res = Resultat(tag=tag, articles=articles)
    par_article = {a.id: a for a in articles}

    unes = await api.photos_by_ids([a.featured_media_id for a in articles if a.featured_media_id])
    # La une est rattachée à SON article (l'id demandé), pas au `post` du
    # média : une bannière réutilisée peut porter un `post` étranger ou nul.
    une_vers_article = {a.featured_media_id: a.id for a in articles if a.featured_media_id}
    for p in unes:
        aid = une_vers_article.get(p.id)
        if aid is not None:
            res.photos.setdefault(aid, []).append(p)

    for p in await api.photos_named(artiste):
        if p.article_id in par_article:
            lot = res.photos.setdefault(p.article_id, [])
            if all(q.id != p.id for q in lot):
                lot.append(p)
        elif all(q.id != p.id for q in res.orphelines):
            res.orphelines.append(p)
    return res


def photos_article(
    article_id: int, *, artiste: str = "", artist_id: int | None = None
) -> list[Photo]:
    """TOUTES les images d'un article (une requête)."""

    async def _run() -> list[Photo]:
        api = BackpackerzApi()
        try:
            return await api.photos_of_article(article_id)
        finally:
            await api.aclose()

    with _scope(artiste, artist_id):
        return async_loop.run_sync(_run())


def completer(res: Resultat, article_id: int, photos: list[Photo]) -> None:
    """Range dans `res` les images complètes d'un article (dédup par id, une gardée en tête)."""
    lot = res.photos.setdefault(article_id, [])
    vus = {p.id for p in lot}
    lot.extend(p for p in photos if p.id not in vus)
    res.complets.add(article_id)


def telecharger(artiste: str, photo: Photo, article: Article | None) -> Path | None:
    """Télécharge l'ORIGINAL (`full`) + écrit la citation. Fichier présent ⇒ pas de re-téléchargement.

    Rend `None` si le CDN n'a pas servi l'image (`download_image` ne lève jamais).
    La citation est (ré)écrite dans les deux cas où un fichier existe : un
    sidecar perdu se reconstitue en recliquant.
    """
    deja = photo_locale(artiste, photo)
    if deja is not None:
        enregistrer_credit(artiste, photo, article, deja)
        return deja
    dest = chemin_photo(artiste, photo)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fichier = download_image(photo.source_url, dest, timeout=60)
    if fichier is None:
        return None
    enregistrer_credit(artiste, photo, article, fichier)
    logger.info(f"BACKPACKERZ : {fichier.name} — {credit_line(photo)}")
    return fichier
