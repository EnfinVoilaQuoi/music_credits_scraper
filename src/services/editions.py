"""Éditions de diffusion : une fiche, plusieurs éditions (étape 4, 2026-09-24).

Décision utilisateur : original, radio edit, clean, explicit, album/single
version et remaster sont UN morceau. La ligne de la fiche garde les données de
l'ORIGINAL (durée, paroles) ; chaque édition est détaillée dans
`track_editions`, avec ses identifiants, et ses streams S'ADDITIONNENT au total.

Trois gestes, partagés par l'import Genius, les écarts Deezer, Kworb et le
script de fusion des fiches existantes :
  · `socle_de` — la fiche de l'original d'une édition ;
  · `absorber` — une fiche d'édition existante rejoint l'original ;
  · `rattacher` — une édition vue par une source est notée sur l'original.
"""

from __future__ import annotations

from src.utils.logger import get_logger
from src.utils.title_matching import normalize_title
from src.utils.version_descriptors import parse_variant, titre_sans_edition

logger = get_logger(__name__)

#: Observations qui décrivent l'ÉDITION et non l'enregistrement : la durée d'un
#: radio edit, son ISRC, sa date, le minutage de ses paroles. Gardées dans la
#: ligne d'édition, JAMAIS versées sur l'original (elles y gagneraient
#: l'arbitrage quand l'original n'a pas la même source).
CHAMPS_D_EDITION = ("duration", "isrc", "release_date", "lyrics_synced")


def socle_de(titre: str | None, fiches) -> object | None:
    """PUR. La fiche UNIQUE, parmi `fiches`, dont `titre` est une édition de
    diffusion. None si `titre` n'est pas une édition, ou si l'original est
    absent ou ambigu (homonymes : jamais de choix)."""
    cible = titre_sans_edition(titre)
    if cible is None:
        return None
    cle = normalize_title(cible)
    candidats = [
        t for t in fiches if normalize_title(t.title) == cle and not parse_variant(t.title).edition
    ]
    if len(candidats) > 1:
        # Homonymes séparés depuis e36 : « Runaway » de Kanye et trois covers.
        # L'édition appartient au morceau de l'artiste, pas à la version d'un
        # tiers (rôle secondaire Cover / Remix / Writer…).
        candidats = [t for t in candidats if not t.secondary_role]
    return candidats[0] if len(candidats) == 1 else None


def libelle(titre: str) -> str:
    return parse_variant(titre).edition or titre


def absorber(dm, socle, fiche) -> bool:
    """La fiche d'édition rejoint l'original : ligne d'édition (identifiants,
    durée, page Genius — qui ne sera donc pas recréée), observations propres à
    l'édition retirées, puis fusion (crédits, vidéos, IDs, parutions)."""
    dm.record_track_edition(
        socle.id,
        libelle(fiche.title),
        title=fiche.title,
        source="fusion",
        duration=fiche.duration,
        spotify_id=fiche.spotify_id,
        deezer_id=fiche.deezer_id,
        isrc=fiche.isrc,
        genius_id=fiche.genius_id,
    )
    for champ in CHAMPS_D_EDITION:
        dm.delete_observations(fiche.id, champ)
    return dm.merge_tracks(socle.id, fiche.id)


def rattacher(dm, socle, titre: str, source: str, **identifiants) -> bool:
    """Une source a vu une édition de l'original : notée sur sa fiche."""
    return dm.record_track_edition(
        socle.id, libelle(titre), title=titre, source=source, **identifiants
    )


_NATURES = {"version_of": "version", "remix_of": "remix", "cover_of": "cover"}


def versions_liees(track, fiches) -> list[tuple[object, str]]:
    """PUR. Les autres fiches qui sont des VERSIONS de `track` — (fiche, nature).

    Par la relation (`version_of` / `remix_of` / `cover_of` pointant sur la
    fiche, posée par Genius, Deezer, Kworb ou les corrections à la main), sinon
    par le même morceau souche (« Suzy (Live 2006) » pour « Suzy »). Sert
    l'onglet « Versions » de la fiche."""
    from src.utils.version_descriptors import Kind

    souche = normalize_title(parse_variant(track.title).socle)
    vues, liees = {track.id}, []
    for f in fiches:
        if f.id in vues:
            continue
        rel = next(
            (
                r
                for r in f.relationships or []
                if r.get("type") in _NATURES and r.get("track_id") == track.id
            ),
            None,
        )
        if rel is not None:
            liees.append((f, _NATURES[rel["type"]]))
            vues.add(f.id)
            continue
        v = parse_variant(f.title)
        if v.kind != Kind.NONE and normalize_title(v.socle) == souche:
            nature = "remix" if v.est_remix else "version"
            if f.secondary_role == "Cover":
                nature = "cover"
            liees.append((f, nature))
            vues.add(f.id)
    return liees
