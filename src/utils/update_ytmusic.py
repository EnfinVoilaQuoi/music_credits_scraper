"""Mise à jour des streams YouTube Music pour les morceaux et albums d'un artiste.

Architecture quota-optimisée :
  Étape 1 — ytmusicapi collecte tous les videoId (tous albums, zéro quota YT)
  Étape 2 — UN seul passage YouTube Data API v3 avec tous les IDs en batch
  Étape 3 — Matching normalisé titre → DB + écriture en base
"""

import re
import sys
import io
import logging
from pathlib import Path
from typing import Dict, List, Optional

sys.path.append(str(Path(__file__).parent.parent.parent))

from src.api.ytmusic_api import YTMusicAPI
from src.utils.logger import get_logger

logger = get_logger(__name__)


# Normaliseur PARTAGÉ (même matching que update_kworb — cf. title_matching.py).
# L'ancien normaliseur local ratait "MURDER INC"/"MURDER INC.", "SOAB"/"S.O.A.B",
# "L’Augmentation - Pt. 2"/"L’augmentation, Pt. 2".
from src.utils.title_matching import normalize_title as _normalize_title


def _extract_video_id(url: str) -> Optional[str]:
    m = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", url or "")
    return m.group(1) if m else None


def _infer_channel_from_youtube_links(
    api, artist, data_manager, max_votes: int = 8, max_attempts: int = 15
) -> "Optional[str]":
    """
    Déduit le canal YTM de l'artiste par vote majoritaire sur les chaînes
    propriétaires des vidéos. Fiable à ~99% dès 2-3 morceaux connus.

    Source des vidéos : d'abord les `youtube_url` déjà en base (Genius media,
    prioritaire — confiance 1.0), puis, seulement s'il en manque, la recherche
    live (ancien système, fallback pour les rares titres sans lien Genius).
    """
    try:
        tracks = data_manager.get_artist_tracks(artist.id)
    except Exception:
        return None
    if not tracks:
        return None

    video_ids = []

    # 1) Liens déjà en base (Genius media ou recherche persistée ≥ 0.9)
    tracks_without_link = []
    for t in tracks:
        if len(video_ids) >= max_votes:
            break
        vid = _extract_video_id(getattr(t, "youtube_url", None))
        if vid:
            video_ids.append(vid)
        else:
            tracks_without_link.append(t)

    if video_ids:
        logger.info(
            f"🎫 Inférence canal : {len(video_ids)} lien(s) YouTube depuis la base (Genius)"
        )

    # 2) Complément éventuel : recherche live (fallback, rare) — uniquement si
    #    la base ne fournit pas assez de votes pour être fiable (< 3)
    if len(video_ids) < 3 and tracks_without_link:
        try:
            from src.utils.youtube_integration import youtube_integration
        except Exception:
            youtube_integration = None

        if youtube_integration:
            attempts = 0
            for t in tracks_without_link:
                if len(video_ids) >= max_votes or attempts >= max_attempts:
                    break
                attempts += 1
                try:
                    res = youtube_integration.get_youtube_link_for_track(
                        artist.name, t.title, album=getattr(t, "album", None)
                    )
                except Exception:
                    continue
                if res.get("type") == "direct" and res.get("confidence", 0) >= 0.8:
                    vid = _extract_video_id(res.get("url"))
                    if vid:
                        video_ids.append(vid)

    if len(video_ids) < 2:
        logger.debug(f"Inférence canal YT: seulement {len(video_ids)} vidéo(s) fiable(s)")
        return None

    logger.info(f"🗳️ Inférence du canal via {len(video_ids)} vidéo(s) à confiance ≥ 0.8")
    return api.infer_channel_from_videos(video_ids)


def update_ytmusic_streams(artist, data_manager) -> Dict:
    """Met à jour les streams YouTube Music des morceaux et albums de l'artiste.

    Args:
        artist: objet Artist avec `id` et `name`
        data_manager: instance de DataManager

    Returns:
        dict résumé {matched, unmatched, albums_processed, yt_api_calls, unmatched_titles}
    """
    result = {
        "matched": 0,
        "unmatched": 0,
        "albums_processed": 0,
        "yt_api_calls": 0,
        "unmatched_titles": [],
        "feats_covered": 0,  # feats hors canal résolus via lien YouTube (repérés Kworb)
        "ambiguous": 0,  # titres homonymes passés à l'étape 4 (pas d'écriture au hasard)
    }

    api = YTMusicAPI()

    # ── Étape 0 : ID artiste YTMusic (gestion des homonymes) ──────────────────
    # 1. Canal épinglé manuellement ? (résout définitivement les homonymes)
    pinned = None
    try:
        pinned = data_manager.get_artist_ytm_channel(artist.id)
    except Exception:
        pass

    # 2. Sinon, déduire le canal par vote sur les vidéos YT à haute confiance
    if not pinned:
        inferred = _infer_channel_from_youtube_links(api, artist, data_manager)
        if inferred:
            pinned = inferred
            try:
                data_manager.set_artist_ytm_channel(artist.id, inferred)
            except Exception:
                pass

    if pinned:
        logger.info(f"📌 Canal YTM utilisé: {pinned}")
        candidates = [(pinned, artist.name)]
    else:
        # 3. Dernier recours : recherche par nom (candidats avec albums d'abord)
        candidates = api.get_artist_channel_candidates(artist.name)
    if not candidates:
        logger.error(f"Artiste '{artist.name}' introuvable sur YouTube Music.")
        return result

    # Essayer les candidats jusqu'à en trouver un avec des albums
    # (plusieurs artistes peuvent porter le même nom, ex: 'Isha')
    channel_id, artist_info = None, None
    for cid, cname in candidates:
        info = api.get_artist_info(cid)
        if info.get("albums"):
            channel_id, artist_info = cid, info
            logger.info(
                f"Canal YTMusic retenu: '{cname}' ({cid}) — " f"{len(info['albums'])} album(s)"
            )
            break
        logger.info(f"Canal YTMusic '{cname}' ({cid}) sans albums — candidat suivant")

    if artist_info is None:
        channel_id = candidates[0][0]
        artist_info = api.get_artist_info(channel_id)

    albums = artist_info["albums"]
    ytm_monthly_listeners = artist_info["monthly_listeners"]

    if ytm_monthly_listeners:
        data_manager.update_artist_monthly_listeners(artist.id, ytm_listeners=ytm_monthly_listeners)
        logger.info(f"Auditeurs mensuels YTMusic : {ytm_monthly_listeners:,}")

    if not albums:
        logger.warning(f"Aucun album YTMusic pour '{artist.name}'")
        return result

    # ── Étape 1 : collecter tous les tracks via ytmusicapi (zéro quota YT) ───
    tracks_by_album: Dict[str, List[Dict]] = {}
    all_video_ids: List[str] = []

    for album_info in albums:
        raw_tracks = api.get_album_tracks_raw(album_info["browseId"])
        tracks_by_album[album_info["title"]] = raw_tracks
        all_video_ids.extend(t["video_id"] for t in raw_tracks if t.get("video_id"))

    logger.info(
        f"ytmusicapi : {len(albums)} album(s), "
        f"{sum(len(v) for v in tracks_by_album.values())} tracks, "
        f"{len(all_video_ids)} videoId collectés"
    )

    # ── Étape 2 : UNE seule passe YouTube Data API v3 pour tous les IDs ──────
    view_counts = api.fetch_view_counts_batch(all_video_ids)
    # Estimer le nb de requêtes effectuées
    result["yt_api_calls"] = (len(all_video_ids) + 49) // 50 if all_video_ids else 0

    # ── Étape 3 : matching DB + mise à jour ───────────────────────────────────
    db_tracks = data_manager.get_artist_tracks(artist.id)
    track_index: Dict[str, list] = {}
    for t in db_tracks:
        track_index.setdefault(_normalize_title(t.title), []).append(t)

    covered_track_ids = set()  # tracks dont les streams ont été résolus (passe albums)
    # track_id → {videoId: count} : un morceau sur PLUSIEURS éditions d'album a
    # des videoIds distincts → SOMME des compteurs, dédupliquée par videoId
    # (la même vidéo listée sur deux éditions n'est comptée qu'une fois).
    vid_counts: Dict[int, Dict[str, int]] = {}

    for album_title, raw_tracks in tracks_by_album.items():
        album_total_streams = 0

        for entry in raw_tracks:
            streams = api.resolve_streams(entry, view_counts)
            norm = _normalize_title(entry["title"])
            candidates = track_index.get(norm, [])

            if len(candidates) > 1:
                # HOMONYMES en base (deux morceaux distincts au même titre, ex.
                # "MEILLEUR" Souffrance vs "Meilleur" Goldee Money) : ne pas
                # écrire au hasard — l'étape 4 (lien YouTube exact par morceau)
                # couvrira chacun individuellement.
                result["ambiguous"] += 1
                logger.info(
                    f"⚠️ Titre ambigu ({len(candidates)} morceaux en base), "
                    f"passé à l'étape 4: '{entry['title']}'"
                )
                continue

            matched_track = candidates[0] if candidates else None
            if matched_track:
                if streams is not None:
                    vid = entry.get("video_id") or f"_novid_{album_title}_{norm}"
                    vid_counts.setdefault(matched_track.id, {})[vid] = streams
                    album_total_streams += streams
                    covered_track_ids.add(matched_track.id)
                result["matched"] += 1
                logger.debug(
                    f"✅ Match YTM: '{entry['title']}' → " f"{streams:,}"
                    if streams is not None
                    else f"✅ Match YTM: '{entry['title']}' (streams N/A)"
                )
            else:
                result["unmatched"] += 1
                result["unmatched_titles"].append(entry["title"])
                logger.debug(f"⚠️ Pas de match DB: '{entry['title']}'")

        if album_total_streams > 0:
            data_manager.update_album_ytm_streams(artist.id, album_title, album_total_streams)

        result["albums_processed"] += 1

    for track_id, vids in vid_counts.items():
        total = sum(vids.values())
        data_manager.update_track_ytm_streams(track_id, total)
        if len(vids) > 1:
            logger.debug(f"🎛️ Track #{track_id}: {len(vids)} vidéos sommées → {total:,}")

    # ── Étape 4 : feats hors canal, repérés sur Kworb ─────────────────────────
    # Les feats sortis sur les albums d'AUTRES artistes ne passent pas par le
    # canal YTM de l'artiste. Pour ceux que Kworb a repérés (spotify_streams
    # présent), on additionne :
    #   · le CLIP : lien YouTube en base (Genius media / recherche persistée) ;
    #   · la version AUDIO YTM : recherche ytmusicapi (filter=songs, avec cache),
    #     retenue seulement si confiance ≥ YOUTUBE_CONFIDENCE_THRESHOLD.
    # Somme dédupliquée par videoId (si le lien Genius EST l'audio, compté 1×).
    extras = []  # (track, {videoId, ...})
    candidates = [
        t
        for t in db_tracks
        if t.id not in covered_track_ids
        and getattr(t, "spotify_streams", None) is not None  # repéré sur Kworb
    ]
    if candidates:
        try:
            from src.config import YOUTUBE_CONFIDENCE_THRESHOLD
            from src.youtube.youtube_searcher import YouTubeSearcher

            searcher = YouTubeSearcher()
        except Exception as e:
            logger.warning(f"Recherche audio YTM indisponible ({e}) — clips seulement")
            searcher, YOUTUBE_CONFIDENCE_THRESHOLD = None, 1.1

        for t in candidates:
            vids = set()
            clip_vid = _extract_video_id(getattr(t, "youtube_url", None))
            if clip_vid:
                vids.add(clip_vid)
            if searcher:
                try:
                    # Pour un feat, chercher sous l'artiste PRINCIPAL (meilleur rappel)
                    search_artist = (
                        getattr(t, "primary_artist_name", None)
                        if getattr(t, "is_featuring", False)
                        else None
                    ) or artist.name
                    results = searcher.search_track(search_artist, t.title, max_results=5)
                    best = results[0] if results else None
                    if (
                        best
                        and not best.get("is_search_url")
                        and best.get("relevance_score", 0) >= YOUTUBE_CONFIDENCE_THRESHOLD
                        and best.get("video_id")
                    ):
                        vids.add(best["video_id"])
                except Exception as e:
                    logger.debug(f"Recherche audio YTM échouée '{t.title}': {e}")
            if vids:
                extras.append((t, vids))

    if extras:
        all_extra_vids = sorted({v for _, vids in extras for v in vids})
        logger.info(
            f"🎤 Feats hors canal repérés Kworb : {len(extras)} morceau(x), "
            f"{len(all_extra_vids)} vidéo(s) (clip + audio)"
        )
        extra_counts = api.fetch_view_counts_batch(all_extra_vids)
        result["yt_api_calls"] += (len(all_extra_vids) + 49) // 50
        for t, vids in extras:
            counts = [extra_counts[v] for v in vids if extra_counts.get(v) is not None]
            if counts:
                total = sum(counts)
                data_manager.update_track_ytm_streams(t.id, total)
                result["feats_covered"] += 1
                logger.debug(
                    f"✅ Feat: '{t.title}' → {total:,} "
                    f"({len(counts)} vidéo(s) : clip et/ou audio)"
                )
            else:
                logger.debug(f"⚠️ Feat sans viewCount: '{t.title}' ({sorted(vids)})")

    logger.info(
        f"YTMusic terminé : {result['matched']} matchés, "
        f"{result['unmatched']} non matchés, "
        f"{result['feats_covered']} feat(s) via lien YouTube, "
        f"{result['albums_processed']} albums, "
        f"{result['yt_api_calls']} requête(s) YouTube API"
    )
    if result["unmatched_titles"]:
        logger.warning(f"Titres YTMusic non matchés : {result['unmatched_titles']}")

    return result


# ── CLI standalone ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    import argparse

    parser = argparse.ArgumentParser(
        description="Met à jour les streams YouTube Music pour un artiste"
    )
    parser.add_argument("artist_name", help="Nom exact de l'artiste dans la DB")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    from src.utils.data_manager import DataManager

    dm = DataManager()
    artist = dm.get_artist_by_name(args.artist_name)
    if not artist:
        print(f"❌ Artiste '{args.artist_name}' non trouvé en base de données.")
        sys.exit(1)

    summary = update_ytmusic_streams(artist, dm)
    print("\n── Résumé YTMusic ──────────────────────────────────")
    print(f"Morceaux matchés      : {summary['matched']}")
    print(f"Feats via lien YouTube: {summary['feats_covered']}")
    print(f"Morceaux non matchés  : {summary['unmatched']}")
    print(f"Albums traités        : {summary['albums_processed']}")
    print(f"Requêtes YouTube API  : {summary['yt_api_calls']}")
    if summary["unmatched_titles"]:
        print(f"Titres non matchés    : {summary['unmatched_titles']}")
