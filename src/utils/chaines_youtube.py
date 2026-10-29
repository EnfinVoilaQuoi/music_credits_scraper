"""Chaînes YouTube de confiance, par artiste (décision utilisateur 2026-10-13). PUR.

Beaucoup de morceaux d'avant le streaming ne vivent que sur YouTube : les
mixtapes distribuées en CD gratuits aux samples jamais « clearés » (*Autopsie*),
des albums que les plateformes n'ont pas (*Mauvais œil*). Les plateformes n'en
ont parfois qu'un BOUT — l'extrait de « HLM 3 » repris dans une compilation
(128 s pour 5 min 19), le « On contrôle la zone » coupé d'une mixtape de Mala.
La chaîne de l'artiste ou de son label fait alors foi.

Deux natures :

- `officielle` — la chaîne de l'artiste ou de son label. Une vidéo qui n'est
  pas un clip (le montage d'un clip diffère de l'album : « On contrôle la
  zone », clip 4:04, CD 4:48) est déclarée `youtube_officiel` : la durée d'une
  fiche hors plateformes, un témoin au vote des catalogues ailleurs — c'est le
  vote qui fait ressortir un lien de plateforme isolé (« HLM 3 » : Deezer 128 s
  contre Spotify et Lunatic à 319 s). Mise en tête de l'arbitrage puis
  ramenée (2026-10-14) : ces chaînes publient aussi des extraits « Son
  Officiel » et des vidéos plus longues que le morceau.
- `leaks` — une chaîne qui ne publie que des morceaux leakés. Pour une fiche
  hors plateformes, sa vidéo donne la durée même quand son titre ne nomme pas
  la page Genius (« City In The Sky (Audio) » pour « Sky City », *Yandhi*).

La correspondance se fait par identifiant de chaîne (stable), à défaut par nom.
"""

from __future__ import annotations

from src.utils.title_matching import names_match_as_words, normalize_name

OFFICIELLE = "officielle"
LEAKS = "leaks"

#: Artiste (nom en base) → [(identifiant de chaîne, nom affiché, nature)]. Le
#: canal YTM ÉPINGLÉ (chaîne « artiste officiel ») n'en fait PAS partie d'office
#: : simulé le 2026-10-14, il aurait refusé 39 liens et changé 97 durées, le plus
#: souvent à tort (court-métrage « Neverland » 575 s, captations de DVD).
CHAINES: dict[str, list[tuple[str, str, str]]] = {
    "Booba": [
        # Lunatic et 45 Scientific (le label de l'époque) : même chaîne.
        ("UCH5-Sd-zSHIoX017k7Fa_7g", "Lunatic", OFFICIELLE),
        ("UCNtD7oDVld6YeaYV7tCXo1A", "B20baOfficiel", OFFICIELLE),
    ],
    "Kanye West": [
        ("UCHQly8VDe2KQkmM-BZ7S0qg", "YANDHI", LEAKS),
    ],
    # Deux chaînes « SCH » : celle épinglée par YTM et l'officielle (2026-10-14).
    "SCH": [("UCeBu6AFsaPVElhFzlj5hetg", "SCH", OFFICIELLE)],
    "Keny Arkana": [("UC0fFj_FgksTyPJzGoUVFBqw", "Keny Arkana", OFFICIELLE)],
}

#: Chaînes PARTAGÉES : valables pour tout artiste, quand le titre de la vidéo
#: NOMME l'artiste — elles publient les morceaux de beaucoup d'autres, dont des
#: homonymes (« Intro »).
CATALOGUES: list[tuple[str, str]] = [
    # Catalogues de label : rap français d'avant les plateformes.
    ("UC23Q7PnQFG_dlTkBwiPaS5Q", "FULL CLIP"),  # ~770 titres (Sniper, Kery James, Psy4…)
    # Mala, Booba, L'Skadrille, La Fouine… (~400 titres ; « [FREE] » = type beats).
    ("UCGHR77It-MJi_tPL-mACryQ", "Addictive Music"),
    # Chaîne d'ARTISTE d'un producteur : ses morceaux en featuring avec des
    # rappeurs de la base (Lucio Bukowski, Limsa, « Gunners » de Flynt) — le
    # morceau peut appartenir à l'un ou l'autre (correction utilisateur 2026-10-14).
    ("UCaG37kVuqCDOvJtcio88k9Q", "Mani Deïz"),
]


def _meme_chaine(video: dict, ident: str, affiche: str) -> bool:
    cid = video.get("channel_id")
    nom = normalize_name(video.get("channel") or "")
    return bool((cid and cid == ident) or (nom and nom == normalize_name(affiche)))


#: Chaînes « X - Topic » qui ne nomment AUCUN artiste : YouTube les génère
#: pour une parution sans interprète unique (compilation, distributeur).
TOPIC_SANS_ARTISTE = frozenset({"release", "various artists", "artistes divers"})


def artiste_de_la_chaine(channel: str | None, channel_id: str | None = None) -> str | None:
    """PUR. L'artiste qu'une chaîne NOMME, ou None : la chaîne « X - Topic »
    que YouTube génère pour l'interprète d'une parution (2026-10-29 : « Yo
    Gotti - Topic », « aladin 135 - Topic ») — sauf celles d'aucun artiste
    (« Release - Topic ») —, ou une chaîne officielle déclarée (`CHAINES`).
    Une autre chaîne (un particulier, un label) ne nomme personne."""
    nom = (channel or "").strip()
    if nom.endswith(" - Topic"):
        artiste = nom[: -len(" - Topic")].strip()
        return None if normalize_name(artiste) in TOPIC_SANS_ARTISTE else artiste or None
    video = {"channel": channel, "channel_id": channel_id}
    for artiste, chaines in CHAINES.items():
        for ident, affiche, nature in chaines:
            if nature == OFFICIELLE and _meme_chaine(video, ident, affiche):
                return artiste
    return None


def nature_de(artiste: str | None, video: dict) -> str | None:
    """PUR. La nature de la chaîne de `video` (`channel_id`, `channel`, `title`)
    pour `artiste`, ou None si elle n'est pas déclarée."""
    for ident, affiche, nature in CHAINES.get(artiste or "", ()):
        if _meme_chaine(video, ident, affiche):
            return nature
    for ident, affiche in CATALOGUES:
        if _meme_chaine(video, ident, affiche):
            titre = video.get("title") or ""
            return OFFICIELLE if artiste and names_match_as_words(artiste, titre) else None
    return None
