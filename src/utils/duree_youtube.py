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
#: (`chaines_youtube`, 2026-10-13) : la publication d'une fiche hors
#: plateformes, un témoin au vote ailleurs.
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
    r"int[ée]gral|\(\d ?- ?\d\)|best of|meilleur de|"
    # « [FREE] … type beat » : des instrumentaux à vendre, jamais le morceau.
    r"\[free\]|type ?beat",
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


#: En deçà, une durée n'est pas celle d'un morceau — sauf si le titre en annonce
#: un court (`court_par_nature`). Seuil de l'audit (`duree_aberrante`) et du
#: verdict (`reconcile.resoudre_duree`, extraits vidéo, 2026-10-27).
DUREE_MIN_MORCEAU = 25
_COURT = re.compile(r"\b(?:intro|interlude|skit|outro|prelude|prélude)\b", re.IGNORECASE)


def court_par_nature(titre: str | None) -> bool:
    """PUR. Un titre qui ANNONCE un morceau court (intro, interlude, skit,
    outro) : moins de 25 s n'y est pas aberrant. Mesuré le 2026-10-13 : 20 des
    26 durées sous 25 s de la base (les « Interlude : … » des Cool Sessions de
    MC Solaar, 15-24 s, arrivés avec le lot de streams) ; restent les vrais
    suspects (« ISABELLA » 9 s, « RXN » 7 s, « SICKO MODE (ref. CyHi) » 12 s)."""
    from src.utils.version_descriptors import titre_generique

    t = titre or ""
    principal = re.split(r"[:(\[]", t, maxsplit=1)[0]
    return titre_generique(principal) or bool(_COURT.search(t))


#: Mots de paroles Genius par seconde sous lesquels une vidéo longue dure bien
#: plus que ce que la page transcrit. Mesuré le 2026-10-27 sur les 70
#: freestyles et sessions de plus de 5 min : 0,28 à 3,7 (transcriptions
#: partielles comprises) ; « Kanye West Sway Freestyle » 0,04 (73 mots pour
#: les 34 min de l'entretien entier).
MOTS_PAR_SECONDE_MIN = 0.1


def dementie_par_les_paroles(duree, paroles: str | None) -> bool:
    """PUR. Une durée de plus de 15 min que les paroles Genius DÉMENTENT : la
    vidéo est un entretien, une émission entière, pas la page. Sans paroles,
    rien ne dément."""
    try:
        duree = int(duree)
    except (TypeError, ValueError):
        return False
    mots = len((paroles or "").split())
    return duree > DUREE_MAX_MORCEAU and bool(mots) and mots / duree < MOTS_PAR_SECONDE_MIN


#: Un CLIP a son propre montage (« On contrôle la zone » : clip 4:04, CD 4:48 ;
#: décision utilisateur 2026-10-13 : « la mention Clip officiel doit bloquer
#: la durée »).
CLIP = re.compile(
    r"\bclip\b|official\s+(?:music\s+)?video|vid[ée]o\s+(?:officielle|clip)|music\s+video|"
    r"\bteaser\b|\btrailer\b|making\s+of",
    re.IGNORECASE,
)
#: Une CAPTATION (live, concert) : autre prise que le studio — sauf pour une
#: fiche qui EST une captation (freestyle, session, live), dont elle est la
#: publication.
CAPTATION = re.compile(r"\blive\b|\bconcert\b", re.IGNORECASE)


def est_un_clip(titre_video: str | None, titre_fiche: str | None = None) -> bool:
    """PUR. La vidéo est-elle un clip ou une captation dont la durée n'est pas
    celle de la fiche ?"""
    if not titre_video:
        return False
    if CLIP.search(titre_video):
        return True
    return bool(CAPTATION.search(titre_video)) and not (
        titre_fiche and FICHE_EMISSION.search(titre_fiche)
    )


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
    d'une chaîne `officielle` qui n'est pas un clip est déclarée sous
    `youtube_officiel`, plateformes ou non, AVANT l'audio Topic (dont le
    fichier peut être celui d'une compilation qui n'en garde qu'un bout) ;
    l'arbitrage la range avec les vidéos ordinaires. Une chaîne de
    `leaks` est recevable pour une fiche hors plateformes même quand le titre
    de sa vidéo ne nomme pas la page Genius."""
    from src.utils.chaines_youtube import LEAKS, OFFICIELLE

    nature = nature or (lambda v: None)
    officielles = [
        v["duration"]
        for v in videos
        if v.get("duration")
        and v.get("title")
        and nature(v) == OFFICIELLE
        and not NON_MORCEAU.search(v["title"])
        and not est_un_clip(v["title"], titre_fiche)
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
        if v.get("duration") and v.get("title") and not NON_MORCEAU.search(v["title"])
        # Un clip a son propre montage (décision utilisateur 2026-10-13 : « la
        # mention Clip officiel doit bloquer la durée ») — même hors plateformes.
        and not est_un_clip(v["title"], titre_fiche)
        and (recevable is None or recevable(v) or nature(v) == LEAKS)
        and (
            v["duration"] <= DUREE_MAX_MORCEAU
            or titre_fiche is None
            or FICHE_EMISSION.search(titre_fiche)
        )
    ]
    d = _accord(autres)
    return (d, SOURCE_VIDEO) if d else None
