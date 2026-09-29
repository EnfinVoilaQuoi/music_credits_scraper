"""Identité d'un `apple_music_id` — le contrôle, PUR (lot B6, 2026-09-29).

Genius porte un `apple_music_id` sur ses fiches, et il n'est pas plus sûr que
ses liens Spotify : mesuré sur 40 fiches, « Heartless » de Kid Cudi désignait
la reprise de Kris Allen, « Gold Digger » de Kanye un titre de Beau Monga.
Genius le DÉCLARE (observation `apple_music_id_propose`, champ déclaré jamais
arbitré), l'étape Identité le fait passer ici avec la fiche iTunes, et seul un
identifiant qui concorde rejoint `tracks.apple_music_id`.

Mêmes règles que le gate Deezer (`deezer_identity.hit_concorde`) :
1. **artiste** — un des noms de la fiche (artiste, interprète principal d'un
   feat, invités) par MOTS ENTIERS dans l'artiste iTunes OU dans son titre
   (« Première Catégorie (Featuring Calbo Et Booba) » est crédité à Lino) ;
2. **titre** — obligatoire, `titres_equivalents` après retrait des mentions
   d'invités (iTunes les met dans le titre) ;
3. **durée** — ne sert qu'à CONTREDIRE, quand la fiche en a une.
"""

from __future__ import annotations

import re

from src.utils.title_matching import contains_as_words, names_match_as_words, normalize_name
from src.utils.version_descriptors import titres_equivalents

#: Champ d'observation de l'identifiant PROPOSÉ par Genius (déclaré, jamais arbitré).
CHAMP_APPLE_PROPOSE = "apple_music_id_propose"
#: Source de durée d'un identifiant vérifié.
SOURCE_APPLE = "apple_music"
#: Écart de durée au-delà duquel ce n'est pas le même enregistrement.
TOLERANCE_DUREE = 5

_INVITES_RE = re.compile(r"\s*[\(\[]\s*(?:feat\.?|ft\.?|featuring|with)\b[^\)\]]*[\)\]]", re.I)


def titre_sans_invites(titre: str | None) -> str:
    return _INVITES_RE.sub("", titre or "").strip()


def noms_de_la_fiche(track) -> list[str]:
    noms = [track.artist.name if track.artist else "", track.primary_artist_name or ""]
    noms += [n.strip() for n in re.split(r",|&", track.featured_artists or "")]
    return [n for n in noms if n]


def fiche_concorde(track, fiche: dict | None) -> tuple[bool, str]:
    """La fiche iTunes est-elle ce morceau ? → `(verdict, motif)`."""
    if not fiche:
        return False, "aucune fiche"
    artiste, titre = fiche.get("artistName") or "", fiche.get("trackName") or ""
    if not any(
        names_match_as_words(nom, artiste)
        # Un invité n'est crédité que dans le TITRE : sens unique, le nom dans le titre.
        or contains_as_words(normalize_name(nom), normalize_name(titre))
        for nom in noms_de_la_fiche(track)
    ):
        return False, f"artiste : iTunes crédite « {artiste} »"
    if not titres_equivalents(track.title, titre_sans_invites(titre)):
        return False, f"titre : iTunes sert « {titre} »"
    duree = duree_de(fiche)
    if track.duration and duree and abs(int(track.duration) - duree) > TOLERANCE_DUREE:
        return False, f"durée : {track.duration} s attendus, {duree} s chez iTunes"
    return True, ""


def duree_de(fiche: dict) -> int | None:
    ms = fiche.get("trackTimeMillis")
    return round(ms / 1000) if ms else None
