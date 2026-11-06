"""Liens YouTube morts : le constat daté, et ce qui se juge dessus.

1 325 fiches portaient un `youtube_url` sans ligne `track_videos` de cette vidéo
(toutes `genius_media`) : le relevé des vues les comptait en `no_meta` puis les
oubliait. Mesuré le 2026-11-06 par `fetch_video_meta_batch` : sur 1 232 vidéos,
1 126 absentes (1 218 fiches), 106 vivantes (100 chez Aketo, dont le run n'avait
pas encore relevé les vues), aucun lot sans réponse.

Seule une RÉPONSE de YouTube prouve une absence (`MetaVideos.absentes`) : le
constat va dans `source_track_absences` (clé `cle_video_morte`), daté, levé dès
que la vidéo est revue, périmé comme les autres (`delai_absence_jours`).

Le retrait, s'il est décidé (`revue.VIDEOS_MORTES_BRANCHEES`), est celui des
autres liens de règle : `clear_track_youtube_link` (vues et nature du lien
comprises), refus `regle:video_morte`, « ↩ Rétablir ». Les durées `youtube*` de
ces fiches RESTENT : une vidéo disparue n'est pas une vidéo fausse, l'observation
garde sa provenance et la colonne son verdict (on ne retire que ce qu'on peut
montrer ; rejet ✖️ d'une vidéo, lui, la dit fausse et emporte ses durées).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import text

from src.config import settings
from src.observability.repository import SourceUsageRepository, cle_video_morte
from src.utils.youtube_utils import extract_video_id


def poser_constats(
    engine,
    mortes: Iterable[tuple[int, str]],
    revues: Iterable[tuple[int, str]] = (),
    quand: datetime | None = None,
) -> None:
    """Pose `mortes` `(track_id, video_id)`, lève `revues`."""
    depot = SourceUsageRepository(engine)
    quand = quand or datetime.now()
    for track_id, video_id in mortes:
        depot.constater_absences(cle_video_morte(video_id), [(track_id, quand)])
    for track_id, video_id in revues:
        depot.constater_absences(cle_video_morte(video_id), [], [track_id])


def constats_frais(engine) -> dict[int, set[str]]:
    """`{track_id: {video_id}}` constatés dans le délai."""
    depuis = datetime.now() - timedelta(days=settings.delai_absence_jours)
    return SourceUsageRepository(engine).videos_mortes(depuis)


def liens_sans_ligne(engine) -> dict[str, list[dict]]:
    """`{video_id: [fiches]}` : les liens sans ligne `track_videos` de leur vidéo."""
    with engine.connect() as conn:
        fiches = (
            conn.execute(
                text(
                    "SELECT t.id, t.title, a.name AS artiste, t.youtube_url, "
                    "t.youtube_url_source FROM tracks t JOIN artists a ON a.id = t.artist_id "
                    "WHERE t.youtube_url IS NOT NULL AND t.youtube_url <> ''"
                )
            )
            .mappings()
            .all()
        )
        lignes = {
            tuple(r) for r in conn.execute(text("SELECT track_id, video_id FROM track_videos"))
        }
    par_video: dict[str, list[dict]] = {}
    for f in fiches:
        vid = extract_video_id(f["youtube_url"])
        if vid and (f["id"], vid) not in lignes:
            par_video.setdefault(vid, []).append(dict(f))
    return par_video


@dataclass
class Mesure:
    """Ce que YouTube répond : chaque vidéo demandée est dans UN de ces ensembles."""

    vivantes: set[str] = field(default_factory=set)
    absentes: set[str] = field(default_factory=set)
    sans_reponse: set[str] = field(default_factory=set)


def mesurer(api, video_ids: Iterable[str]) -> Mesure:
    """Interroge YouTube (quota : 1 unité par lot de 50). Rien n'est écrit."""
    ids = list(dict.fromkeys(video_ids))
    meta = api.fetch_video_meta_batch(ids)
    return Mesure(
        vivantes={v for v in ids if v in meta},
        absentes={v for v in ids if v in meta.absentes},
        sans_reponse={v for v in ids if v not in meta and v not in meta.absentes},
    )
