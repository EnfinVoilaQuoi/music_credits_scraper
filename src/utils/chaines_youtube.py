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
  zone », clip 4:04, CD 4:48) donne la durée de référence (`youtube_officiel`,
  en tête de l'arbitrage), et un lien de plateforme nettement plus court est
  refusé comme un extrait (`duree_youtube.lien_coupe`).
- `leaks` — une chaîne qui ne publie que des morceaux leakés. Pour une fiche
  hors plateformes, sa vidéo donne la durée même quand son titre ne nomme pas
  la page Genius (« City In The Sky (Audio) » pour « Sky City », *Yandhi*).

La correspondance se fait par identifiant de chaîne (stable), à défaut par nom.
"""

from __future__ import annotations

from src.utils.title_matching import normalize_name

OFFICIELLE = "officielle"
LEAKS = "leaks"

#: Artiste (nom en base) → [(identifiant de chaîne, nom affiché, nature)].
CHAINES: dict[str, list[tuple[str, str, str]]] = {
    "Booba": [
        # Lunatic et 45 Scientific (le label de l'époque) : même chaîne.
        ("UCH5-Sd-zSHIoX017k7Fa_7g", "Lunatic", OFFICIELLE),
        ("UCNtD7oDVld6YeaYV7tCXo1A", "B20baOfficiel", OFFICIELLE),
    ],
    "Kanye West": [
        ("UCHQly8VDe2KQkmM-BZ7S0qg", "YANDHI", LEAKS),
    ],
}


def nature_de(artiste: str | None, video: dict) -> str | None:
    """PUR. La nature de la chaîne de `video` (`channel_id`, `channel`) pour
    `artiste`, ou None si elle n'est pas déclarée."""
    cid = video.get("channel_id")
    nom = normalize_name(video.get("channel") or "")
    for ident, affiche, nature in CHAINES.get(artiste or "", ()):
        if (cid and cid == ident) or (nom and nom == normalize_name(affiche)):
            return nature
    return None
