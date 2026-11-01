"""L'adresse d'une piste chez SA source — un seul endroit (2026-11-01).

`release_tracks.external_track_id` n'a de sens qu'avec sa `source` : pour
`genius`, c'est l'ID de la PAGE Genius, pas un ID Deezer. Lu comme un ID
Deezer, celui de « Ultralight Beam » (Stan Walker) menait à « Bustin Loose »
de Greg Booker, et l'adresse a été montrée à l'utilisateur. Toute adresse
construite depuis un lien de parution passe par ici → garde :
`tests/test_liens_sources.py` (crible : pas d'adresse de plateforme formée
avec un `external_track_id` ailleurs).
"""

from __future__ import annotations

#: Sources qui portent un identifiant de PISTE, et l'adresse publique qu'il
#: désigne chez elles. `legacy` et `genius_scrape` n'en portent pas.
_ADRESSES = {
    "genius": "https://genius.com/songs/{}",
    "deezer": "https://www.deezer.com/track/{}",
    "apple_music": "https://music.apple.com/fr/song/{}",
    "spotify_web": "https://open.spotify.com/track/{}",
}


def adresse_de_piste(source: str | None, identifiant) -> str | None:
    """L'adresse de la piste `identifiant` chez `source` ; None pour une source
    sans identifiant de piste ou un identifiant vide."""
    gabarit = _ADRESSES.get(source or "")
    if gabarit is None or identifiant in (None, ""):
        return None
    return gabarit.format(identifiant)
