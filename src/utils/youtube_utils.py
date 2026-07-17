"""Helpers YouTube partagés : extraction de video id + URLs de vignettes.

Factorise la regex de video id qui vivait en double (``update_ytmusic._extract_video_id``
et ``bpmfinder_scraper.BPMFinderScraper._video_id``). Un seul endroit à corriger
si un nouveau format d'URL apparaît.
"""

import re

# Formats couverts : ``watch?v=<id>``, ``youtu.be/<id>``, ``/embed/<id>``,
# ``/shorts/<id>``. Un video id YouTube fait exactement 11 caractères
# ``[A-Za-z0-9_-]``.
_VIDEO_ID_RE = re.compile(r"(?:v=|youtu\.be/|/embed/|/shorts/)([A-Za-z0-9_-]{11})")


def extract_video_id(url: str | None) -> str | None:
    """Extrait le video id (11 car.) d'une URL YouTube, ou None si absent."""
    match = _VIDEO_ID_RE.search(url or "")
    return match.group(1) if match else None


def thumbnail_urls(video_id: str) -> list[str]:
    """URLs de vignette à essayer dans l'ordre pour un video id.

    ``maxresdefault`` (1280×720) est la meilleure qualité mais renvoie souvent
    404 (toutes les vidéos n'en ont pas) → on retombe sur ``hqdefault``
    (480×360), toujours présent. L'appelant tente la première, puis la suivante.
    """
    return [
        f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
        f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
    ]


# Marqueurs de version « audio » (upload auto YouTube ou audio officiel).
_AUDIO_HINTS = ("(audio)", "audio officiel", "official audio", "[audio]")
# Marqueurs de clip officiel.
_CLIP_HINTS = ("clip officiel", "official video", "official music video", "(clip)")

# Instance TrackClassifier paresseuse (réutilise `is_show_performance`, la même
# liste de shows que la classification de morceaux). Import local anti-cycle.
_SHOW_CLASSIFIER = None


def _show_classifier():
    global _SHOW_CLASSIFIER
    if _SHOW_CLASSIFIER is None:
        from src.youtube.track_classifier import TrackClassifier

        _SHOW_CLASSIFIER = TrackClassifier()
    return _SHOW_CLASSIFIER


def classify_video_kind(video_title: str | None, channel_title: str | None) -> str:
    """Catégorise une vidéo YouTube : ``audio`` / ``show`` / ``clip`` / ``unknown``.

    Chantier « Media » — différencie un clip d'un morceau « classique » à partir
    du titre et de la chaîne (snippet de l'API). Ordre : audio (chaîne
    ``- Topic`` / titre audio) → show/freestyle (Grünt, COLORS, Planète Rap…) →
    clip officiel → sinon ``unknown``.
    """
    title = (video_title or "").lower()
    channel = (channel_title or "").lower()

    # Audio : chaîne auto-générée « … - Topic » ou titre explicitement audio.
    if channel.strip().endswith("- topic") or any(h in title for h in _AUDIO_HINTS):
        return "audio"
    # Show / freestyle / cypher (titre OU chaîne) — avant le clip (une prestation
    # COLORS taguée « official video » reste un show).
    if _show_classifier().is_show_performance(video_title, channel_title):
        return "show"
    if any(h in title for h in _CLIP_HINTS):
        return "clip"
    return "unknown"
