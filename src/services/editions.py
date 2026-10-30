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
from src.utils.version_descriptors import (
    edition_de_diffusion,
    est_un_remix,
    porte_une_version,
    socle_normalise,
    titre_sans_edition,
)

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
        t for t in fiches if normalize_title(t.title) == cle and not edition_de_diffusion(t.title)
    ]
    if len(candidats) > 1:
        # Homonymes séparés depuis e36 : « Runaway » de Kanye et trois covers.
        # L'édition appartient au morceau de l'artiste, pas à la version d'un
        # tiers (rôle secondaire Cover / Remix / Writer…).
        candidats = [t for t in candidats if not t.secondary_role]
    return candidats[0] if len(candidats) == 1 else None


def libelle(titre: str) -> str:
    return edition_de_diffusion(titre) or titre


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


def rattacher(dm, socle, titre: str, source: str, label: str | None = None, **identifiants) -> bool:
    """Une source a vu une édition de l'original : notée sur sa fiche. `label`
    distingue une édition au MÊME titre (le disque Deezer d'une autre page du
    même enregistrement), que `libelle(titre)` confondrait avec la fiche."""
    return dm.record_track_edition(
        socle.id, label or libelle(titre), title=titre, source=source, **identifiants
    )


_NATURES = {"version_of": "version", "remix_of": "remix", "cover_of": "cover"}


def _cle_page(url) -> str | None:
    """PUR. Clé de comparaison d'une page Genius : sans espaces, sans barre
    finale, en minuscules (None si vide)."""
    cle = (url or "").strip().rstrip("/").lower()
    return cle or None


def versions_liees(track, fiches) -> list[tuple[object, str]]:
    """PUR. Les autres fiches qui sont des VERSIONS de `track` — (fiche, nature).

    Par la relation (`version_of` / `remix_of` / `cover_of` pointant sur la
    fiche, posée par Genius, Deezer, Kworb ou les corrections à la main), sinon
    par le même morceau souche (« Suzy (Live 2006) » pour « Suzy »). Sert
    l'onglet « Versions » de la fiche.

    Une relation lue chez Genius ne porte pas de `track_id` : elle vise la fiche
    par sa PAGE (`url` = `genius_url`), ce que le socle ne voit pas quand les
    titres diffèrent (« Day N Night » → « Day ‘N’ Nite », 493 liens de plus en
    base, 2026-10-26). AFFICHAGE seulement : chez Genius, `remix_of` couvre
    aussi un freestyle sur la prod d'un autre (« Grünt #33 » → « Petit
    frère ») — jamais une source d'héritage (BPM, durée, tonalité). Genius dit
    aussi l'original « reprise » de son propre instrumental (« Boulbi » →
    « Boulbi (Instrumental) », 21 cas) : ce sens-là ne s'affiche pas, le socle
    lie déjà l'instrumental depuis l'original."""
    version = porte_une_version(track.title)
    souche = socle_normalise(track.title)
    page = _cle_page(track.genius_url)

    def vise(r, f) -> bool:
        if r.get("track_id") is not None and r.get("track_id") == track.id:
            return True
        if page is None or _cle_page(r.get("url")) != page:
            return False
        inverse = version and not porte_une_version(f.title) and socle_normalise(f.title) == souche
        return not inverse

    vues, liees = {track.id}, []
    for f in fiches:
        if f.id in vues:
            continue
        rel = next(
            (r for r in f.relationships or [] if r.get("type") in _NATURES and vise(r, f)),
            None,
        )
        if rel is not None:
            liees.append((f, _NATURES[rel["type"]]))
            vues.add(f.id)
            continue
        if porte_une_version(f.title) and socle_normalise(f.title) == souche:
            nature = "remix" if est_un_remix(f.title) else "version"
            if f.secondary_role == "Cover":
                nature = "cover"
            liees.append((f, nature))
            vues.add(f.id)
    return liees
