"""La PLACE d'un morceau sur un disque, et qui l'a dite (e52, 2026-10-14).

Principe : deux fiches au MÊME emplacement d'un même disque sont un seul
morceau ; à deux emplacements, deux morceaux. Mais un numéro ne vaut que par
sa source, et deux numéros ne se comparent qu'à source ÉGALE :

  · Deezer — la piste porte `track_position` et `disk_number` : fiable ;
  · Apple Music — la fiche iTunes d'un ID vérifié porte `trackNumber` et
    `discNumber` : fiable, sur SA parution iTunes ;
  · Genius — l'ordre de la page ALBUM, FAUX dès qu'un titre du disque n'y a
    pas de page : Genius le saute et le compteur continue (A2H *Art de vivre* :
    « Skit (Qui est trivette) » absent, « Trivette » passe de 13 à 12) ;
  · `legacy` — `tracks.track_number` repris par e31, provenance inconnue.

Mesuré le 2026-10-12 : 6 paires de fiches partageaient un emplacement — 1 vrai
doublon (Swing « RECO30221 » / « REC030221 »), 1 probable (« GO GO GO »), 4
morceaux différents numérotés par deux sources pour deux éditions (A2H
« Trivette » / « Skit (Qui est trivette) », Josman « MOONROCK » / « 3ein »).
Sur les 16 👯 du panneau : AUCUN n'avait de disque numéroté en commun. D'où
l'usage : preuve d'APPOINT d'un 👯, jamais seule.
"""

from __future__ import annotations

#: Ordre de confiance d'une source de position. Une position n'est remplacée
#: que par une source au moins aussi sûre : un numéro Genius n'écrase jamais
#: un numéro Deezer. Source inconnue = `legacy`.
RANG_POSITION: dict[str, int] = {
    "manual": 4,
    "deezer": 3,
    "apple_music": 3,
    "spotify_web": 3,
    "musicbrainz": 3,
    "discogs": 2,
    "genius": 1,
    "legacy": 0,
}

#: Sources dont deux numéros, sur le même disque, se COMPARENT.
SOURCES_COMPARABLES = frozenset({"manual", "deezer", "apple_music", "spotify_web", "musicbrainz"})


def rang(source: str | None) -> int:
    return RANG_POSITION.get(source or "legacy", 0)


def position_retenue(
    connue: tuple[int | None, int | None, str | None] | None,
    observee: tuple[int | None, int | None, str | None],
) -> tuple[int | None, int | None, str | None]:
    """PUR. `(disque, numéro, source)` à garder pour un lien.

    L'observée remplace la connue si elle porte un numéro et que sa source est
    au moins aussi sûre. Le disque SUIT le numéro : jamais le disque d'une
    source et le numéro d'une autre."""
    disque, numero, source = observee
    if numero is None:
        return connue or (None, None, None)
    if connue is None or connue[1] is None or rang(source) >= rang(connue[2]):
        return disque, numero, source
    return connue


def _place(p: dict) -> tuple:
    return (p["release_id"], p.get("disc_number") or 1, p["track_number"], p["position_source"])


def appoint_emplacement(a: list[dict], b: list[dict]) -> str | None:
    """PUR. Ce que les places de deux fiches disent d'elles, ou None.

    `a`, `b` : `[{release_id, title, disc_number, track_number,
    position_source}]`. Seuls deux numéros d'une MÊME source comparable sur
    une MÊME parution parlent ; la première preuve trouvée est rendue —
    « un seul morceau » (même place) l'emporte sur « deux morceaux »."""
    places_b = [_place(p) for p in b if p.get("track_number") is not None]
    deux = None
    for p in a:
        if p.get("track_number") is None or p.get("position_source") not in SOURCES_COMPARABLES:
            continue
        ra, da, na, sa = _place(p)
        for rb, db, nb, sb in places_b:
            if rb != ra or sb != sa:
                continue
            if (db, nb) == (da, na):
                return f"même place sur « {p['title']} » (piste {na}, {sa}) : un seul morceau"
            deux = deux or (
                f"deux places sur « {p['title']} » ({_piste(da, na)} et {_piste(db, nb)}, {sa})"
                " : deux morceaux"
            )
    return deux


def _piste(disque: int, numero: int) -> str:
    return f"{disque}-{numero}" if disque != 1 else str(numero)
