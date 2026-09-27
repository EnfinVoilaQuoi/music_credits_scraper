"""Durée d'un morceau d'après ses vidéos YouTube (2026-09-27). Module PUR.

Deux niveaux, mesurés contre Deezer sur les fiches qui ont les deux :

- `youtube` : la vidéo AUDIO d'une chaîne « - Topic », c'est-à-dire le fichier
  livré par le distributeur — 97 à 99 % à ≤ 2 s sur 540 fiches.
- `youtube_video` : une vidéo ORDINAIRE, et seulement pour une fiche HORS
  plateformes (ni Spotify ni Deezer). Sur un morceau publié aussi en audio, un
  clip se trompe souvent (44-56 % à ≤ 2 s, 10-15 % à plus de 30 s : intro de
  clip, clip couvrant plusieurs titres, reprise) ; hors plateformes, la vidéo
  EST la publication. Avant-dernier rang de l'arbitrage (seul ReccoBeats,
  qui exige un ID Spotify que ces fiches n'ont pas, vient après) : toute
  autre source la remplace.

L'appelant écarte les vidéos rattachées à plusieurs fiches (un clip double
couvre deux morceaux) et ne passe que des vidéos RATTACHÉES à la fiche — liens
Genius, album YTM, lien manuel, ou recherche audio YTM retenue au seuil de
0,90 (mesurée : 97 % à ≤ 2 s).
"""

from __future__ import annotations

import re

SOURCE_AUDIO = "youtube"
SOURCE_VIDEO = "youtube_video"

#: Écart toléré entre deux vidéos d'un même morceau pour les dire d'accord.
TOLERANCE_S = 2

_ISO = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def iso8601_secondes(valeur: str | None) -> int | None:
    """« PT3M13S » → 193 ; None si illisible ou nul (direct, première)."""
    m = _ISO.fullmatch(valeur or "")
    if not m or not any(m.groups()):
        return None
    h, mn, s = (int(x or 0) for x in m.groups())
    return (h * 3600 + mn * 60 + s) or None


def est_topic(chaine: str | None) -> bool:
    return bool(chaine) and chaine.endswith(" - Topic")


def _accord(durees: list[int]) -> int | None:
    if not durees or max(durees) - min(durees) > TOLERANCE_S:
        return None
    return sorted(durees)[len(durees) // 2]


def duree_a_declarer(videos: list[dict], hors_plateformes: bool) -> tuple[int, str] | None:
    """`videos` : `{"duration": s, "channel": nom}` des vidéos PROPRES à la
    fiche. Rend `(secondes, source)` ou None — plusieurs vidéos du même niveau
    en désaccord (> 2 s) ne concluent pas."""
    topic = [v["duration"] for v in videos if v.get("duration") and est_topic(v.get("channel"))]
    if topic:
        d = _accord(topic)
        return (d, SOURCE_AUDIO) if d else None
    if not hors_plateformes:
        return None
    autres = [v["duration"] for v in videos if v.get("duration")]
    d = _accord(autres)
    return (d, SOURCE_VIDEO) if d else None
