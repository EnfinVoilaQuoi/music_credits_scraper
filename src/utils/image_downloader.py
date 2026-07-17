"""Téléchargement d'images (photos d'artistes, covers, vignettes YouTube).

Brique bas-niveau du chantier « Media » : slugification de noms de fichiers
Windows-safe, download atomique, et conventions de nommage déterministes. Aucune
logique métier ici (quelle image pour quel morceau) — c'est le rôle de
``media_enricher``.

Règle couche ③ (frontière réseau) : ``download_image`` intercepte
``requests.RequestException`` de façon ciblée, log un ``warning`` et renvoie
``None`` — il NE LÈVE JAMAIS (un CDN qui tombe ne doit pas casser un batch).
"""

import os
import re
from pathlib import Path

import requests

from src.config import ARTIST_IMAGES_DIR, COVER_IMAGES_DIR, VIGNETTE_IMAGES_DIR
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Caractères interdits dans un nom de fichier Windows + caractères de contrôle.
_FORBIDDEN_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MULTISPACE = re.compile(r"\s+")

# Content-Type → extension. Défaut ``.jpg`` (cas ultra-majoritaire :
# Deezer/ytimg servent du JPEG ; Genius parfois du PNG).
_CONTENT_TYPE_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}

_DEFAULT_SLUG_LEN = 120


def slugify_filename(name: str, *, max_len: int = _DEFAULT_SLUG_LEN) -> str:
    """Nom de fichier Windows-safe et déterministe à partir d'un nom libre.

    Retire les caractères interdits (``<>:"/\\|?*`` + contrôle), écrase les
    espaces multiples, supprime points/espaces terminaux (interdits sous
    Windows) et tronque à ``max_len`` caractères. Deux entrées identiques
    produisent le MÊME nom (idempotence des chemins).
    """
    if not name:
        return "_"
    cleaned = _FORBIDDEN_CHARS.sub("", str(name))
    cleaned = _MULTISPACE.sub(" ", cleaned).strip()
    cleaned = cleaned.rstrip(". ")  # Windows : pas de point/espace en fin de nom
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip(". ")
    return cleaned or "_"


def _ext_from_content_type(content_type: str | None) -> str:
    """Extension (``.jpg``/``.png``/``.webp``) déduite du Content-Type HTTP."""
    ct = (content_type or "").split(";")[0].strip().lower()
    return _CONTENT_TYPE_EXT.get(ct, ".jpg")


def download_image(url: str | None, dest: Path, *, timeout: int = 15) -> Path | None:
    """Télécharge ``url`` vers ``dest`` (extension corrigée selon le Content-Type).

    Écriture ATOMIQUE : le flux est écrit dans un fichier ``.part`` voisin puis
    ``os.replace`` (atomique, même sous Windows si même dossier) — jamais de
    fichier à moitié écrit visible sous le nom final. L'extension finale suit le
    Content-Type (``dest`` fournit le *stem* ; ``photo.jpg`` peut devenir
    ``photo.png``).

    Renvoie le ``Path`` réellement écrit (truthy → ``if download_image(...)``
    reste valide) ou ``None`` en cas d'échec (URL vide, réponse non-image, erreur
    réseau). Ne lève jamais.
    """
    if not url:
        return None

    dest = Path(dest)
    try:
        with requests.get(url, stream=True, timeout=timeout) as resp:
            resp.raise_for_status()
            content_type = resp.headers.get("Content-Type", "")
            if "image" not in content_type.lower():
                logger.warning(f"Réponse non-image ({content_type!r}) pour {url}")
                return None

            final = dest.with_suffix(_ext_from_content_type(content_type))
            final.parent.mkdir(parents=True, exist_ok=True)
            tmp = final.with_name(final.name + ".part")
            try:
                with open(tmp, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=8192):
                        if chunk:
                            fh.write(chunk)
                os.replace(tmp, final)
            except OSError as exc:
                logger.warning(f"Écriture image échouée ({final}): {exc}")
                tmp.unlink(missing_ok=True)
                return None
            return final
    except requests.RequestException as exc:
        logger.warning(f"Téléchargement image échoué ({url}): {exc}")
        return None


# ── Conventions de nommage (déterministes) ──────────────────────────────────
# Homonymes = même fichier (deux artistes « Kery James » différents se
# partageraient un fichier). Limitation acceptée : le scraper ne dédoublonne pas
# les artistes par autre chose que le nom.


def artist_image_path(name: str) -> Path:
    """`artistes/<slug-nom>.jpg` (extension par défaut, ajustée au download)."""
    return ARTIST_IMAGES_DIR / f"{slugify_filename(name)}.jpg"


def cover_image_path(artist_name: str, album_or_title: str) -> Path:
    """`covers/<slug-artiste> - <slug-album-ou-titre>.jpg`.

    Chaque partie est tronquée plus court (80) pour rester sous la limite de
    longueur de nom de fichier Windows une fois concaténée.
    """
    part_artist = slugify_filename(artist_name, max_len=80)
    part_work = slugify_filename(album_or_title, max_len=80)
    return COVER_IMAGES_DIR / f"{part_artist} - {part_work}.jpg"


def vignette_image_path(video_id: str) -> Path:
    """`vignettes/<video_id>.jpg` (le video id est déjà safe : `[A-Za-z0-9_-]`)."""
    return VIGNETTE_IMAGES_DIR / f"{video_id}.jpg"
