"""Un morceau est-il INÉDIT ? — la règle, à UN seul endroit (e34, 2026-09-23).

Genius référence des morceaux pas encore sortis (leaks, snippets, titres
annoncés) et la communauté les marque d'une **astérisque finale** dans le
titre : « Drugs 2* », « Némésis* », « Turn To Gold* ». 168 en base.

Ce qui a été ESSAYÉ ET ÉCARTÉ, avant d'écrire une ligne : le champ
`lyrics_state` de l'API Genius. Mesuré sur les 168 marqués et 168 témoins
(`scripts/mesurer_lyrics_state.py`), **91 % des inédits sont `complete`** et six
témoins sans astérisque sont `unreleased`, dont « Mouvement » d'A2H, qui a
26 312 streams Spotify et une date de sortie. Ce champ décrit l'état des
PAROLES sur Genius, pas celui du morceau.

Module PUR : ni base, ni réseau, ni `src.models` (canard-typage — `src/utils/
__init__` tire `DataEnricher`).
"""

from __future__ import annotations

import re

#: L'astérisque d'inédit : une étoile FINALE collée à un caractère.
#:
#: Mesuré sur les 171 titres finissant par « * » : 168 marqueurs et **trois
#: contre-exemples réels** que ce motif écarte — « Jeune N**** » de Josman
#: (étoile sur étoile, censure), « * » et « Interlude * » de PLK (étoile
#: DÉTACHÉE, qui est le titre lui-même). C'est ce trio qui interdit de
#: « simplifier » en `\*$`, et le test le gèle nommément.
MARQUEUR = re.compile(r"[^\s*]\*$")


def porte_le_marqueur(titre: str | None) -> bool:
    """Ce titre porte-t-il l'astérisque d'inédit ?"""
    return bool(titre) and bool(MARQUEUR.search(titre.rstrip()))


def titre_sans_marqueur(titre: str | None) -> str:
    """Le titre débarrassé de son astérisque — inchangé s'il n'en porte pas.

    ⚠️ **Ce retrait n'a PAS sa place dans `clean_stored_title`**, qui est le
    point de passage unique des titres vers la base précisément parce qu'il ne
    retire que ce qui n'a AUCUN sens (caractères sans largeur). L'astérisque,
    elle, porte du sens : son retrait est une DÉCISION, prise une fois, là où
    le constat est posé — sans quoi l'information disparaîtrait sans laisser de
    trace.
    """
    if not titre:
        return titre or ""
    depouille = titre.rstrip()
    if not MARQUEUR.search(depouille):
        return titre
    return depouille[:-1].rstrip()


def trace_de_plateforme(track) -> bool:
    """Une plateforme de streaming connaît-elle ce morceau ? (⇒ il est SORTI)

    C'est ce qui LÈVE le constat : sans quoi un inédit le resterait pour
    toujours, Genius retirant l'astérisque de son côté sans que rien ne nous le
    dise — 7 des 168 sont déjà sortis (B.B. Jacques « Amertume* », 5,5 M de
    streams).

    ⚠️ **YouTube n'est pas une trace** : son catalogue est plus large que celui
    des plateformes de streaming (leaks, extraits, lyrics vidéos de projets
    jamais sortis) et 41 des 168 y ont des vues. Même asymétrie que pour les
    streams dans `track_validation.streams_valides` : l'absence de YouTube ne
    prouve rien, sa présence non plus.
    """
    if getattr(track, "spotify_id", None):
        return True
    streams = getattr(track, "streams", None)
    return bool(getattr(streams, "spotify_streams", None))


def constat_a_ecrire(track) -> bool | None:
    """Le constat à poser au moment d'une sauvegarde — tri-état préservé.

    **Ne dit « sorti » que pour LEVER un constat d'inédit.** L'absence de
    marqueur ne prouve rien (Genius ne marque pas tout), et une trace de
    plateforme sur un morceau dont on n'a jamais rien constaté n'apprend rien
    non plus : écrire `0` partout remplacerait « jamais regardé » par une
    affirmation, ce qui est exactement le défaut que le tri-état évite.

    Ce qu'elle fait, en revanche, c'est rendre le constat AUTO-RÉPARATEUR :
    Genius retire l'astérisque de son côté sans nous le dire, mais un
    identifiant Spotify apparaît — 7 des 168 inédits sont déjà sortis
    (B.B. Jacques « Amertume* », 5,5 M de streams).
    """
    connu = getattr(track, "unreleased", None)
    if connu and trace_de_plateforme(track):
        return False
    return connu
