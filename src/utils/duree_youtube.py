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
#: Vidéo (hors clip) d'une chaîne OFFICIELLE de l'artiste ou de son label
#: (`chaines_youtube`, 2026-10-13) : la référence, en tête de l'arbitrage.
SOURCE_OFFICIEL = "youtube_officiel"

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


#: Une vidéo qui n'est pas LE morceau : extrait, émission ou compilation
#: entière (mesuré le 2026-10-09 : « LEGO » 3 181 s = un Planète Rap,
#: « Hella » 15 s = un extrait, « Pas de boulot » 1 610 s = une mixtape).
NON_MORCEAU = re.compile(
    r"snippet|extrait|mixtape|mix-tape|mix[eé] par|plan[eè]te ?rap|full album|"
    r"int[ée]gral|\(\d ?- ?\d\)|best of|meilleur de",
    re.IGNORECASE,
)


#: Au-delà, une vidéo ordinaire n'est pas un morceau — sauf si la FICHE elle-même
#: est une émission (Grünt, Rentre dans le Cercle, freestyle long). Seuil de
#: l'audit (`duree_aberrante`) ; mesuré le 2026-10-10 : Diam's « Morts pour
#: rien » avait reçu les 64 min d'un reportage qui nommait le titre.
DUREE_MAX_MORCEAU = 15 * 60
FICHE_EMISSION = re.compile(
    r"gr[üu]nt|rentre dans le cercle|freestyle|cypher|session|\blive\b|planète rap|"
    r"planete rap|colors|mixtape|medley|[ée]pisode",
    re.IGNORECASE,
)


#: Une vidéo de chaîne officielle qui est un CLIP (ou une captation) : son
#: montage diffère de l'album (« On contrôle la zone » : clip 4:04, CD 4:48).
CLIP = re.compile(
    r"\bclip\b|official\s+(?:music\s+)?video|vid[ée]o\s+(?:officielle|clip)|music\s+video|"
    r"\bteaser\b|\btrailer\b|making\s+of|\blive\b|\bconcert\b",
    re.IGNORECASE,
)

#: Un lien de plateforme plus court que la vidéo officielle d'au moins
#: max(15 s, 10 %) en est un extrait ou une version coupée.
COUPE_MIN_S = 15
COUPE_MIN_PART = 0.10


def lien_coupe(duree_lien: int | None, duree_officielle: int | None) -> bool:
    """PUR. Le lien (Deezer, Spotify) est-il un BOUT du morceau publié par
    la chaîne officielle ? « HLM 3 » : 128 s dans une compilation, 319 s
    chez Lunatic (2026-10-13). Plus LONG, ce n'est pas un extrait : on ne
    conclut pas."""
    if not duree_lien or not duree_officielle:
        return False
    manque = int(duree_officielle) - int(duree_lien)
    return manque > max(COUPE_MIN_S, COUPE_MIN_PART * int(duree_officielle))


def duree_a_declarer(
    videos: list[dict],
    hors_plateformes: bool,
    recevable=None,
    titre_fiche: str | None = None,
    nature=None,
) -> tuple[int, str] | None:
    """`videos` : `{"duration": s, "channel": nom, "title": titre}` des vidéos
    PROPRES à la fiche. Rend `(secondes, source)` ou None — plusieurs vidéos du
    même niveau en désaccord (> 2 s) ne concluent pas.

    Une vidéo ORDINAIRE ne donne sa durée que si son titre est connu, n'est ni
    un extrait ni une émission (`NON_MORCEAU`), et que `recevable(video)` —
    le juge « la vidéo nomme-t-elle le morceau ? » de l'appelant — l'accepte
    (décision utilisateur 2026-10-09, CORRECTIFS 1.10).

    `nature(video)` (`chaines_youtube.nature_de`, 2026-10-13) : une vidéo
    d'une chaîne `officielle` qui n'est pas un clip donne la durée de
    référence, plateformes ou non, AVANT l'audio Topic (dont le fichier peut
    être celui d'une compilation qui n'en garde qu'un bout). Une chaîne de
    `leaks` est recevable pour une fiche hors plateformes même quand le titre
    de sa vidéo ne nomme pas la page Genius."""
    from src.utils.chaines_youtube import LEAKS, OFFICIELLE

    nature = nature or (lambda v: None)
    live_attendu = bool(titre_fiche and re.search(r"\blive\b", titre_fiche, re.IGNORECASE))
    officielles = [
        v["duration"]
        for v in videos
        if v.get("duration")
        and v.get("title")
        and nature(v) == OFFICIELLE
        and not NON_MORCEAU.search(v["title"])
        and not (CLIP.search(v["title"]) and not (live_attendu and "live" in v["title"].lower()))
        and (recevable is None or recevable(v))
        and v["duration"] <= DUREE_MAX_MORCEAU
    ]
    d = _accord(officielles)
    if d:
        return d, SOURCE_OFFICIEL
    topic = [v["duration"] for v in videos if v.get("duration") and est_topic(v.get("channel"))]
    if topic:
        d = _accord(topic)
        return (d, SOURCE_AUDIO) if d else None
    if not hors_plateformes:
        return None
    autres = [
        v["duration"]
        for v in videos
        if v.get("duration")
        and v.get("title")
        and not NON_MORCEAU.search(v["title"])
        and (recevable is None or recevable(v) or nature(v) == LEAKS)
        and (
            v["duration"] <= DUREE_MAX_MORCEAU
            or titre_fiche is None
            or FICHE_EMISSION.search(titre_fiche)
        )
    ]
    d = _accord(autres)
    return (d, SOURCE_VIDEO) if d else None
