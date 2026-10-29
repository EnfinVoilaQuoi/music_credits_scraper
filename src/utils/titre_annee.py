"""Titre + ANNÉE FINALE NUE : « Suzy » / « Suzy 2003 » (règle du 2026-10-29).

Une année finale nue (« Suzy 2003 », « Jesus Walks (2003) », « Muzik - 2003 »)
n'est pas un descripteur de version (`version_descriptors` : taxonomie FERMÉE) :
elle reste dans le socle, et les juges de titre qui lisent `meme_version` ou
retirent les parenthèses (SongBPM, GetSongBPM, score YouTube, LRCLIB /
Musixmatch / YTM, flou Kworb) confondaient « Suzy » et « Suzy 2003 » — vidéo,
mesures et LRC de l'une écrits sur l'autre (Diam's, 2026-10-27 ; rapport
`titre_annee_2026-10-27.md` du studio : 23 paires, 7 / 16 à durées connues un
seul enregistrement, 9 / 16 deux).

L'année seule ne tranche pas (« Planète Mars 2021 » EST le titre ; « Tout ira
bien (2017) » est sans doute « Tout ira bien ») : ce qui départage, c'est la
JUMELLE — une autre fiche du même artiste dont le titre ne diffère que par
l'année finale nue — et une durée INDÉPENDANTE.

Règle (décision utilisateur 2026-10-29) : quand la fiche a une jumelle, un
résultat dont l'année (ou l'absence d'année) est celle de la jumelle et pas la
sienne est REFUSÉ — sauf durée indépendante de la fiche à ± 2 s de celle du
résultat (même enregistrement). Sans durée du résultat, la durée indépendante de
la jumelle en tient lieu : le résultat est alors celui de la jumelle. Sans
jumelle, rien ne change. Les années d'édition (« 2009 Remaster ») et de
descripteur (« Live 2006 », « 2017 Demo ») sont lues par `parse_variant` hors du
socle : elles restent jugées comme avant.

Le témoin (`duree_independante`) : une durée de la fiche venue d'une source qui
n'a pas pu la prendre chez la jumelle — une valeur que la jumelle porte aussi, de
la même source, est écartée ; jamais une durée lue par la source jugée
(`sources_exclues`) ni `legacy` (« on ne retire que ce qu'on peut montrer »).

Ce module ne touche jamais la base : `garde_pour` lit les titres de l'artiste par
`spotify_identity.fournisseur_titres` et les durées par `fournisseur_durees`,
posés par `DataManager`. Sans eux (tests purs, scripts sans base), aucune garde.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache

from src.utils.logger import get_logger
from src.utils.title_matching import normalize_title
from src.utils.version_descriptors import (
    _segments_hors_groupes,
    parse_variant,
    parties_du_morceau,
    titre_de_video_pour,
)

logger = get_logger(__name__)

#: L'année finale nue d'un SOCLE : « Suzy 2003 », « Jesus Walks (2003) »,
#: « Muzik - 2003 », « Tout ira bien [2017] ». Le reste doit porter une lettre :
#: « 2014 » seul est un titre, pas une année.
_ANNEE_FINALE = re.compile(
    r"^(?P<base>.*?[^\W\d_].*?)[\s,\-–—]*[\(\[]?\s*(?P<an>(?:19|20)\d\d)\s*[\)\]]?\s*$"
)

#: Une année seule entre parenthèses dans un titre de VIDÉO : `titre_de_video_pour`
#: la retire (année de mise en ligne) — ici elle doit rester lisible.
_ANNEE_EN_GROUPE = re.compile(r"\s*[\(\[]\s*((?:19|20)\d\d)\s*[\)\]]")

#: Ordre de prise du témoin de durée (les plus sûres d'abord). Hors liste :
#: `legacy` (provenance inconnue).
ORDRE_DES_TEMOINS: tuple[str, ...] = (
    "manual",
    "deezer",
    "apple_music",
    "spotify_web",
    "ytmusic",
    "discogs",
    "youtube_officiel",
    "youtube",
    "songbpm",
    "youtube_video",
    "reccobeats",
)

#: Sources dont la durée vient de ce que chaque juge rapproche : elle suit le
#: résultat jugé, juste ou faux — elle ne peut pas le corroborer.
EXCLUES_SONGBPM = frozenset({"songbpm"})
EXCLUES_YOUTUBE = frozenset({"youtube", "youtube_officiel", "youtube_video", "ytmusic"})
EXCLUES_LRC = frozenset({"ytmusic"})
EXCLUES_KWORB = frozenset({"spotify_web", "reccobeats"})

#: Écart (s) sous lequel deux durées désignent le même enregistrement.
TOLERANCE = 2

#: Fournisseur des durées observées : `[track_id] -> {track_id: {source: s}}`.
#: Posé par `DataManager` ; `None` ⇒ les jumelles n'ont pas de durée connue.
fournisseur_durees = None


@lru_cache(maxsize=1 << 14)
def base_et_annee(titre: str | None) -> tuple[str, int | None]:
    """PUR. (socle normalisé sans l'année finale nue, cette année ou None).
    Le descripteur de version reste hors du socle (`parse_variant`) :
    « Suzy 2003 (Live) » → (« suzy », 2003), « Alien (2020 Demo) » → (« alien »,
    None), « 2014 » → (« 2014 », None)."""
    socle = parse_variant(titre or "").socle or ""
    m = _ANNEE_FINALE.match(socle.strip())
    if m is None:
        return normalize_title(socle), None
    return normalize_title(m["base"]), int(m["an"])


@lru_cache(maxsize=1 << 14)
def _cle_jumelle(titre: str | None) -> tuple:
    """Ce qui doit être ÉGAL entre deux jumelles : le socle sans l'année, le
    descripteur, les parties."""
    v = parse_variant(titre or "")
    return (
        base_et_annee(titre)[0],
        v.kind,
        normalize_title(v.descriptor or ""),
        parties_du_morceau(titre or ""),
    )


def jumelles(fiche_id, titre: str | None, titres: Iterable[tuple]) -> list[tuple]:
    """PUR. Les `(id, titre)` de `titres` (fiches de l'artiste) qui ne diffèrent
    de `titre` que par une année finale nue, hors la fiche elle-même."""
    base, annee = base_et_annee(titre)
    if not base:
        return []
    cle = _cle_jumelle(titre)
    return [
        (tid, t)
        for tid, t in titres
        if tid != fiche_id and t and _cle_jumelle(t) == cle and base_et_annee(t)[1] != annee
    ]


def _duree(valeur) -> int | None:
    """Secondes (« 3:59 », 239, « 239.4 »), ou None."""
    from src.utils.track_mapper import _clean_duration

    return _clean_duration(valeur)


def duree_independante(
    observees: Mapping | None,
    autres: Iterable[Mapping | None] = (),
    sources_exclues: Iterable[str] = (),
) -> int | None:
    """PUR. La durée de la fiche qui ne doit rien à sa jumelle, ou None.

    `observees` : `{source: durée}` de la fiche ; `autres` : ceux de ses
    jumelles. Une source exclue (lue par le juge), `legacy`, ou une valeur que
    l'une des jumelles porte AUSSI de la même source (à la seconde près : copiée
    de l'une à l'autre, ou lue sur la même page) n'est pas un témoin. Deux pages
    distinctes d'un même enregistrement diffèrent souvent d'une seconde (Booba
    « Tout ira bien », Deezer 186 / 187 s) : celles-là restent témoins."""
    exclues = set(sources_exclues)
    autres = [a or {} for a in autres]
    for source in ORDRE_DES_TEMOINS:
        if source in exclues:
            continue
        d = _duree((observees or {}).get(source))
        if d is None:
            continue
        if any(_duree(a.get(source)) == d for a in autres):
            continue
        return d
    return None


@dataclass(frozen=True)
class GardeAnnee:
    """La garde d'UNE fiche qui a au moins une jumelle."""

    titre: str
    #: Socle normalisé sans l'année (`base_et_annee`).
    base: str
    #: L'année finale nue de la fiche, ou None.
    annee: int | None
    #: `(année, titre, durée indépendante ou None)` de chaque jumelle.
    jumelles: tuple[tuple[int | None, str, int | None], ...]
    #: Durée indépendante de la fiche (`duree_independante`), ou None.
    duree: int | None

    def _motif_annee(self, annee: int | None, duree_resultat) -> str | None:
        if annee == self.annee:
            return None
        concernees = [(t, d) for an, t, d in self.jumelles if an == annee]
        if not concernees:
            return None
        if self.duree is not None:
            d = _duree(duree_resultat)
            if d is not None:
                if abs(d - self.duree) <= TOLERANCE:
                    return None
            elif any(dj is not None and abs(dj - self.duree) <= TOLERANCE for _, dj in concernees):
                return None
        return (
            f"année {annee or 'absente'} : celle de la jumelle « {concernees[0][0]} », "
            f"pas de la fiche « {self.titre} »"
        )

    def motif(self, titre_resultat: str | None, duree_resultat=None) -> str | None:
        """PUR. Le motif du refus d'un résultat au titre `titre_resultat` (et à
        la durée `duree_resultat`, si la source la donne), sinon None. Un
        résultat d'un autre socle n'est pas concerné : les juges de titre
        restent maîtres du reste."""
        base, annee = base_et_annee(titre_resultat)
        if base != self.base:
            return None
        return self._motif_annee(annee, duree_resultat)

    def refuse(self, titre_resultat: str | None, duree_resultat=None) -> bool:
        return self.motif(titre_resultat, duree_resultat) is not None

    def motif_video(self, titre_video: str | None, duree_video=None) -> str | None:
        """`motif` pour un titre de VIDÉO (« Kanye West - Muzik (2003) [Official
        Audio] ») : nettoyé comme le juge YouTube (`titre_de_video_pour`), mais
        l'année entre parenthèses gardée, puis jugé au segment qui porte le
        socle de la fiche."""
        brut = _ANNEE_EN_GROUPE.sub(r" \1", titre_video or "")
        for segment in _segments_hors_groupes(titre_de_video_pour(self.titre, brut)):
            base, annee = base_et_annee(segment)
            if base == self.base:
                return self._motif_annee(annee, duree_video)
        return None


def garde(
    fiche_id,
    titre: str | None,
    observees: Mapping | None,
    titres: Iterable[tuple],
    durees_des: Mapping | None = None,
    sources_exclues: Iterable[str] = (),
) -> GardeAnnee | None:
    """PUR. La garde de la fiche `fiche_id` / `titre`, ou None sans jumelle.
    `titres` : `(id, titre)` des fiches de l'artiste ; `durees_des` :
    `{id: {source: durée}}` des jumelles (absent = inconnues)."""
    trouvees = jumelles(fiche_id, titre, titres)
    if not trouvees:
        return None
    durees_des = durees_des or {}
    exclues = frozenset(sources_exclues)
    obs_jumelles = [durees_des.get(tid) or {} for tid, _ in trouvees]
    base, annee = base_et_annee(titre)
    return GardeAnnee(
        titre=titre or "",
        base=base,
        annee=annee,
        jumelles=tuple(
            (
                base_et_annee(t)[1],
                t,
                duree_independante(durees_des.get(tid), [observees], exclues),
            )
            for tid, t in trouvees
        ),
        duree=duree_independante(observees, obs_jumelles, exclues),
    )


def garde_pour(track, sources_exclues: Iterable[str] = ()) -> GardeAnnee | None:
    """La garde d'une fiche en base (titres de l'artiste par
    `spotify_identity.fournisseur_titres`, durées des jumelles par
    `fournisseur_durees`), ou None — sans fournisseur, sans artiste en base,
    sans jumelle. Un fournisseur en panne ne garde rien (et ne bloque rien)."""
    from src.utils import spotify_identity

    artist_id = getattr(track.artist, "id", None)
    if spotify_identity.fournisseur_titres is None or not artist_id or not track.title:
        return None
    try:
        titres = spotify_identity.fournisseur_titres(artist_id)
        trouvees = jumelles(track.id, track.title, titres)
        if not trouvees:
            return None
        durees = (
            fournisseur_durees([tid for tid, _ in trouvees])
            if fournisseur_durees is not None
            else {}
        )
    except Exception:  # noqa: BLE001 — garde-fou : un fournisseur en panne ne refuse rien
        logger.exception("Garde titre + année : fournisseur en échec")
        return None
    return garde(
        track.id, track.title, track.durations_observees, trouvees, durees, sources_exclues
    )


def garde_parmi(track, fiches: Iterable, sources_exclues: Iterable[str] = ()) -> GardeAnnee | None:
    """PUR. `garde` quand l'appelant a déjà les fiches de l'artiste (objets à
    `id`, `title`, `durations_observees`) — le rapprochement Kworb."""
    autres = [t for t in fiches if t is not track and (t.id is None or t.id != track.id)]
    titres = [(i, t.title) for i, t in enumerate(autres)]
    # Les durées ne se lisent que s'il y a une jumelle : la plupart des fiches
    # n'en ont pas.
    trouvees = jumelles(-1, track.title, titres)
    if not trouvees:
        return None
    return garde(
        -1,
        track.title,
        track.durations_observees,
        trouvees,
        {i: autres[i].durations_observees for i, _ in trouvees},
        sources_exclues,
    )
