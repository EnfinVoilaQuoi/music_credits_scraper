"""Mise à jour des streams YouTube Music pour les morceaux et albums d'un artiste.

Architecture quota-optimisée :
  Étape 1  — ytmusicapi collecte tous les videoId (tous albums, zéro quota YT)
  Étape 2  — UN seul passage YouTube Data API v3 avec tous les IDs en batch
  Étape 3  — Matching normalisé titre → DB, vidéos du CANAL par morceau
  Étape 3b — Vidéos HORS canal déjà connues de la base (`track_videos`, e20)
  Étape 4  — Découverte d'une version audio pour les morceaux hors canal
  Étape 5  — UN second batch pour toutes ces vidéos hors canal
  Étape 6  — Un total par morceau : somme DÉDUPLIQUÉE par videoId

Ce que les étapes 3b/6 ont réparé (2026-09-07) : le clip officiel d'un morceau
porte un videoId DIFFÉRENT de sa version audio et ne vit pas sur le canal
« - Topic ». Il n'était additionné que pour les feats repérés par Kworb — les
vues du clip d'un morceau d'album n'étaient donc comptées nulle part.
"""

import logging
import sys

import requests
from sqlalchemy.exc import SQLAlchemyError
from ytmusicapi.exceptions import YTMusicError

from src.api.ytmusic_api import YTMusicAPI
from src.config import YTM_IDENTITY_MIN_MATCHED, YTM_IDENTITY_MIN_RATIO
from src.models import TrackVideo
from src.utils.logger import get_logger

# Extraction du video id : helper partagé (factorisé, cf. youtube_utils). Alias
# privé conservé pour ne pas toucher les appelants internes.
from src.utils.youtube_utils import artiste_de_recherche
from src.utils.youtube_utils import extract_video_id as _extract_video_id

logger = get_logger(__name__)


# Normaliseur PARTAGÉ (même matching que update_kworb — cf. title_matching.py).
# L'ancien normaliseur local ratait "MURDER INC"/"MURDER INC.", "SOAB"/"S.O.A.B",
# "L’Augmentation - Pt. 2"/"L’augmentation, Pt. 2".
from src.utils.title_matching import normalize_title as _normalize_title


def _infer_channel_from_youtube_links(
    api, artist, data_manager, max_votes: int = 8, max_attempts: int = 15
) -> "str | None":
    """
    Déduit le canal YTM de l'artiste par vote majoritaire sur les chaînes
    propriétaires des vidéos. Fiable à ~99% dès 2-3 morceaux connus.

    Source des vidéos : d'abord les `youtube_url` déjà en base (Genius media,
    prioritaire — confiance 1.0), puis, seulement s'il en manque, la recherche
    live (ancien système, fallback pour les rares titres sans lien Genius).
    """
    try:
        tracks = data_manager.get_artist_tracks(artist.id)
    except SQLAlchemyError as e:
        logger.debug(f"Lecture morceaux DB échouée (inférence canal): {e}")
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
        except ImportError:
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
                except (AttributeError, TypeError, KeyError):
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


# ── Gate d'identité : valider le canal AVANT toute écriture ────────────────────
# Contrairement à Kworb (`_scrape_validated`), YTM écrivait streams + auditeurs
# même sur un homonyme ne matchant presque rien de la base. On confronte donc la
# discographie du canal à la base par TITRES (signal primaire, déjà collectés) +
# ALBUMS (repêchage/départage). Un canal MANUEL n'est jamais bloqué (warning) ;
# un canal inféré/recherché suspect → abort sans aucune écriture.
# Seuils configurables via Settings (config.py) : dérivés ici en constantes
# module (l'alias local reste lu par `_identity_suspect` + monkeypatchable).
_IDENTITY_MIN_MATCHED = YTM_IDENTITY_MIN_MATCHED  # plancher absolu de titres communs
_IDENTITY_MIN_RATIO = YTM_IDENTITY_MIN_RATIO  # part des titres YTM retrouvés en base


def _channel_identity_report(
    tracks_by_album: dict, db_norm_titles: set, ytm_album_titles, db_norm_albums: set
) -> dict:
    """Confronte la discographie du canal YTM à la base.

    Ratio calculé CÔTÉ YTM (part des titres YTM uniques retrouvés en base) :
    robuste à une base plus complète que le canal — un « - Topic » de 8 morceaux
    matche ~100 % de SES titres, un mauvais homonyme quasi 0 %.

    Returns:
        {"ytm_titles": n uniques (dédup _normalize_title), "matched": n,
         "ratio": float, "album_overlap": n albums YTM au titre normalisé en base}
    """
    ytm_norm_titles = set()
    for raw_tracks in tracks_by_album.values():
        for entry in raw_tracks:
            title = entry.get("title") if isinstance(entry, dict) else None
            if title:
                ytm_norm_titles.add(_normalize_title(title))
    matched = len(ytm_norm_titles & db_norm_titles)
    ytm_titles = len(ytm_norm_titles)
    ratio = matched / ytm_titles if ytm_titles else 0.0
    album_overlap = sum(1 for a in ytm_album_titles if a and _normalize_title(a) in db_norm_albums)
    return {
        "ytm_titles": ytm_titles,
        "matched": matched,
        "ratio": ratio,
        "album_overlap": album_overlap,
    }


def _identity_suspect(report: dict) -> bool:
    """Canal suspect si trop peu de titres communs, OU ratio faible SANS album commun.

    Un album entier en commun (album_overlap ≥ 1) REPÊCHE un ratio faible : canal
    Topic incomplet / artiste niche dont la base est plus fournie que le canal.
    """
    if report["matched"] < _IDENTITY_MIN_MATCHED:
        return True
    return report["ratio"] < _IDENTITY_MIN_RATIO and report["album_overlap"] == 0


def _score_candidate_albums(ytm_album_titles, db_norm_albums) -> int:
    """Nb d'albums YTM dont le titre normalisé figure en colonne `album` de la base."""
    return sum(1 for a in ytm_album_titles if a and _normalize_title(a) in db_norm_albums)


def _pick_best_candidate(api, candidates, db_norm_albums) -> tuple:
    """Départage les homonymes par RECOUVREMENT d'albums avec la base.

    Charge `get_artist_info` pour chaque candidat (≤5, ytmusicapi, zéro quota YT).
    Retenu si son score (nb d'albums communs) est > 0 ET strictement devant le 2ᵉ
    (même esprit de pluralité nette que le vote sur les vidéos). Sinon fallback
    statu quo : premier candidat avec albums, sinon le tout premier — le gate
    d'identité protège désormais ce fallback. `db_norm_albums` vide → dégénère
    exactement en comportement historique. Renvoie `(None, None)` si aucun candidat.
    """
    infos = [(cid, cname, api.get_artist_info(cid)) for cid, cname in candidates]
    if not infos:
        return (None, None)

    if db_norm_albums:
        scored = sorted(
            (
                (_score_candidate_albums([a["title"] for a in info["albums"]], db_norm_albums), i)
                for i, (_, _, info) in enumerate(infos)
            ),
            key=lambda x: x[0],
            reverse=True,
        )
        best_score, best_i = scored[0]
        runner_up = scored[1][0] if len(scored) > 1 else 0
        if best_score > 0 and best_score > runner_up:
            cid, cname, info = infos[best_i]
            logger.info(
                f"Canal YTMusic départagé par albums: '{cname}' ({cid}) — "
                f"{best_score} album(s) commun(s) avec la base"
            )
            return cid, info

    # Fallback statu quo : premier candidat avec albums, sinon le tout premier.
    for cid, cname, info in infos:
        if info.get("albums"):
            logger.info(f"Canal YTMusic retenu: '{cname}' ({cid}) — {len(info['albums'])} album(s)")
            return cid, info
    cid, _, info = infos[0]
    return cid, info


# ── Vidéos partagées entre plusieurs morceaux ────────────────────────────────
# Mesuré sur la base réelle le 2026-09-07 : 15 vidéos sont rattachées à
# plusieurs morceaux d'un MÊME artiste, 32 morceaux concernés. Deux causes, que
# rien ne distingue automatiquement :
#
#   · un lien Genius fautif — « Innocent » et « Interlude » (Jazzy Bazz)
#     pointent sur la même vidéo, qui n'appartient qu'à l'un des deux ;
#   · un CLIP DOUBLE légitime — `iIHdTMHWAic` s'intitule « B.B. Jacques -
#     Donjon & 2h22 » et couvre réellement les deux morceaux, qui existent par
#     ailleurs séparément en audio sur le canal « - Topic ».
#
# Dans les DEUX cas, attribuer les vues entières à chaque morceau les
# multiplie : l'album « Honeymoon » affichait 3 × 531 930 pour une vidéo vue
# 531 930 fois. Et aucune répartition n'est défendable — la vidéo est un objet,
# les morceaux sont trois.
#
# La règle est donc de NE PAS COMPTER, et de SIGNALER : c'est la doctrine du
# projet quand il n'y a pas d'oracle (cf. les titres tronqués du SNEP). Elle
# n'ampute rien définitivement — dès que le mauvais lien est rejeté (bouton ✖️),
# la vidéo cesse d'être partagée et recompte pour le morceau qui la garde.
def videos_partagees(vid_counts: dict) -> set:
    """videoId rattachés à PLUSIEURS morceaux, donc non attribuables à un seul.

    Fonction pure. `vid_counts` : `{track_id: {video_id: vues}}`.
    """
    vus_par = {}
    for track_id, vids in vid_counts.items():
        for video_id in vids:
            vus_par.setdefault(video_id, set()).add(track_id)
    return {video_id for video_id, tracks in vus_par.items() if len(tracks) > 1}


def _rapport_partagees(partagees: set, vid_counts: dict, tracks_par_id: dict) -> list:
    """De quoi VÉRIFIER chaque vidéo partagée : son titre, son lien, ses morceaux.

    Le titre de la vidéo (`track_videos.title`, e21) est ce qui tranche entre le
    clip double et le lien fautif — quand il est connu, c'est-à-dire après une
    passe « vues des vidéos ». Sinon l'URL suffit à aller voir.
    """
    rapport = []
    for video_id in sorted(partagees):
        morceaux = sorted(
            tracks_par_id[tid].title for tid, vids in vid_counts.items() if video_id in vids
        )
        titre_video = next(
            (
                v.title
                for tid, vids in vid_counts.items()
                if video_id in vids
                for v in tracks_par_id[tid].videos
                if v.video_id == video_id and v.title
            ),
            None,
        )
        vues = next(vids[video_id] for vids in vid_counts.values() if video_id in vids)
        rapport.append(
            {
                "video_id": video_id,
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "titre_video": titre_video,
                "vues": vues,
                "morceaux": morceaux,
            }
        )
    return rapport


def update_ytmusic_streams(artist, data_manager, api=None, track_ids=None) -> dict:
    """Met à jour les streams YouTube Music des morceaux et albums de l'artiste.

    Args:
        artist: objet Artist avec `id` et `name`
        data_manager: instance de DataManager
        api: YTMusicAPI injecté (StreamsProvider) ; créé en interne si None
        track_ids: restreint les ÉCRITURES et le batch YouTube à ces morceaux
            (case « limiter aux morceaux cochés ») ; None = toute la discographie.

    Le PARCOURS du canal reste entier même sous sélection : le gate d'identité
    confronte la discographie COMPLÈTE du canal à la base, et le restreindre lui
    ôterait ce qui lui permet de conclure. Seul le coûteux — les compteurs de
    vues — est restreint.

    Sous sélection, les TOTAUX D'ALBUM ne sont pas écrits : ils s'additionnent
    sur tous les morceaux du disque, dont les compteurs ne sont justement plus
    demandés. Les écrire donnerait un total silencieusement amputé, plus faux
    que pas de total du tout.

    Returns:
        dict résumé {matched, unmatched, albums_processed, yt_api_calls, unmatched_titles}
    """
    result = {
        "matched": 0,
        "unmatched": 0,
        "albums_processed": 0,
        "yt_api_calls": 0,
        "unmatched_titles": [],
        "feats_covered": 0,  # morceaux hors canal résolus via un lien YouTube
        "ambiguous": 0,  # titres homonymes passés à l'étape 4 (pas d'écriture au hasard)
        # Morceaux dont le total additionne PLUSIEURS vidéos (clip + audio, ou
        # plusieurs éditions). C'est la mesure de ce que la table `track_videos`
        # apporte : avant e20, ces morceaux ne comptaient qu'une de leurs vidéos.
        "multi_video": 0,
        # Vidéos rattachées à plusieurs morceaux : écartées de la somme, et
        # décrites pour que l'utilisateur puisse trancher (clip double légitime
        # ou lien fautif). `vues_non_attribuees` chiffre ce que ça laisse de côté.
        "videos_partagees": [],
        "vues_non_attribuees": 0,
    }

    if api is None:
        api = YTMusicAPI()

    # ── Étape 0 : ID artiste YTMusic (gestion des homonymes) ──────────────────
    # Canal épinglé (manuel ou inféré-persisté) + son ORIGINE : 'manual' n'est
    # jamais bloqué/écrasé par le gate ; 'inferred' pourra être dé-figé si le gate
    # (étape 1b) le juge suspect. Le pin inféré/recherché n'est persisté qu'APRÈS
    # validation (plus de set_artist_ytm_channel immédiat après vote).
    pinned, pinned_source = None, None
    try:
        pinned, pinned_source = data_manager.get_artist_ytm_channel_info(artist.id)
    except SQLAlchemyError:
        pass

    if pinned:
        channel_source = pinned_source or "manual"
        logger.info(f"📌 Canal YTM utilisé: {pinned} ({channel_source})")
        candidates = [(pinned, artist.name)]
    else:
        inferred = _infer_channel_from_youtube_links(api, artist, data_manager)
        if inferred:
            channel_source = "inferred"
            candidates = [(inferred, artist.name)]
            logger.info(f"🗳️ Canal YTM déduit (non encore épinglé) : {inferred}")
        else:
            # Dernier recours : recherche par nom (candidats avec albums d'abord).
            channel_source = "search"
            candidates = api.get_artist_channel_candidates(artist.name)
    if not candidates:
        logger.error(f"Artiste '{artist.name}' introuvable sur YouTube Music.")
        return result

    # db_tracks lu UNE fois : sert au départage des homonymes par albums (0b) ET
    # au gate d'identité (1b).
    db_tracks = data_manager.get_artist_tracks(artist.id)
    db_norm_albums = {_normalize_title(t.album) for t in db_tracks if t.album}

    # ── Étape 0b : départage des homonymes par recouvrement d'albums ──────────
    # (plusieurs artistes peuvent porter le même nom, ex: 'Isha')
    channel_id, artist_info = _pick_best_candidate(api, candidates, db_norm_albums)
    if artist_info is None:
        logger.error(f"Artiste '{artist.name}' introuvable sur YouTube Music.")
        return result

    albums = artist_info["albums"]
    ytm_monthly_listeners = artist_info["monthly_listeners"]

    if not albums:
        logger.warning(f"Aucun album YTMusic pour '{artist.name}'")
        return result

    if not db_tracks:
        logger.warning(
            f"Base vide pour '{artist.name}' — rien à valider ni à écrire "
            "(récupère d'abord la discographie)."
        )
        result["identity"] = {
            "status": "aborted",
            "channel_id": channel_id,
            "channel_source": channel_source,
            "matched": 0,
            "ytm_titles": 0,
            "ratio": 0.0,
            "album_overlap": 0,
        }
        return result

    # ── Étape 1 : collecter tous les tracks via ytmusicapi (zéro quota YT) ───
    tracks_by_album: dict[str, list[dict]] = {}
    all_video_ids: list[str] = []

    for album_info in albums:
        raw_tracks = api.get_album_tracks_raw(album_info["browseId"])
        tracks_by_album[album_info["title"]] = raw_tracks
        all_video_ids.extend(t["video_id"] for t in raw_tracks if t.get("video_id"))

    logger.info(
        f"ytmusicapi : {len(albums)} album(s), "
        f"{sum(len(v) for v in tracks_by_album.values())} tracks, "
        f"{len(all_video_ids)} videoId collectés"
    )

    # ── Étape 1b : GATE d'identité — valider le canal AVANT toute écriture ────
    # Placé AVANT fetch_view_counts_batch (économise le quota YT en cas d'abort)
    # ET avant le write des auditeurs mensuels.
    db_norm_titles = {_normalize_title(t.title) for t in db_tracks}
    report = _channel_identity_report(
        tracks_by_album, db_norm_titles, [a["title"] for a in albums], db_norm_albums
    )
    result["identity"] = {"channel_id": channel_id, "channel_source": channel_source, **report}

    if _identity_suspect(report):
        if channel_source == "manual":
            logger.warning(
                f"⚠️ Canal YTM manuel à l'identité divergente "
                f"({report['matched']}/{report['ytm_titles']} titres, "
                f"ratio {report['ratio']:.0%}) — écriture maintenue (saisie manuelle)."
            )
            result["identity"]["status"] = "warning"
        else:
            logger.error(
                f"🚨 Identité du canal YTM suspecte pour '{artist.name}' "
                f"({report['matched']}/{report['ytm_titles']} titres retrouvés, "
                f"ratio {report['ratio']:.0%}, {report['album_overlap']} album(s) commun(s)) "
                "— AUCUNE écriture. Épingle le bon canal (@handle) dans la fenêtre Nb Streams."
            )
            if pinned_source == "inferred":
                data_manager.clear_artist_ytm_channel(artist.id)
                logger.info("Canal inféré erroné dé-épinglé (ré-inférence au prochain run).")
            result["identity"]["status"] = "aborted"
            return result
    else:
        result["identity"]["status"] = "ok"
        # Canal validé : persister le pin inféré/recherché MAINTENANT (jamais
        # par-dessus un manuel, qui est déjà en base avec sa propre source).
        if channel_source != "manual":
            try:
                data_manager.set_artist_ytm_channel(artist.id, channel_id, source="inferred")
            except SQLAlchemyError:
                pass

    # Auditeurs mensuels : écrits APRÈS validation (jamais sur un homonyme).
    if ytm_monthly_listeners:
        data_manager.update_artist_monthly_listeners(artist.id, ytm_listeners=ytm_monthly_listeners)
        logger.info(f"Auditeurs mensuels YTMusic : {ytm_monthly_listeners:,}")

    # Index titre normalisé → morceaux de la base. Construit AVANT le batch : il
    # sert aussi à savoir quelles vidéos du canal appartiennent aux morceaux
    # retenus, donc quelles vues demander.
    track_index: dict[str, list] = {}
    for t in db_tracks:
        track_index.setdefault(_normalize_title(t.title), []).append(t)

    def retenu(track_id) -> bool:
        """Ce morceau fait-il partie de la sélection ? (True si aucune sélection)"""
        return track_ids is None or track_id in track_ids

    if track_ids is not None:
        interessantes = {
            entry["video_id"]
            for raw_tracks in tracks_by_album.values()
            for entry in raw_tracks
            if entry.get("video_id")
            for candidats in [track_index.get(_normalize_title(entry["title"]), [])]
            if len(candidats) == 1 and retenu(candidats[0].id)
        }
        all_video_ids = [v for v in all_video_ids if v in interessantes]
        logger.info(
            f"🎯 Sélection : {len(track_ids)} morceau(x) coché(s), "
            f"{len(all_video_ids)} vidéo(s) du canal à mesurer"
        )

    # ── Étape 2 : UNE seule passe YouTube Data API v3 pour tous les IDs ──────
    view_counts = api.fetch_view_counts_batch(all_video_ids)
    # Estimer le nb de requêtes effectuées
    result["yt_api_calls"] = (len(all_video_ids) + 49) // 50 if all_video_ids else 0

    # ── Étape 3 : matching DB + vidéos du CANAL ───────────────────────────────
    covered_track_ids = set()  # tracks dont les streams ont été résolus (passe albums)
    # track_id → {videoId: count} : un morceau sur PLUSIEURS éditions d'album a
    # des videoIds distincts → SOMME des compteurs, dédupliquée par videoId
    # (la même vidéo listée sur deux éditions n'est comptée qu'une fois).
    # C'est le dictionnaire LUI-MÊME qui déduplique : il n'y a pas de somme sur
    # une liste où une vidéo pourrait figurer deux fois.
    vid_counts: dict[int, dict[str, int]] = {}
    # track_id → {videoId: TrackVideo} à enregistrer (e20). Ce que CETTE passe a
    # vu, pas la liste complète : l'écrivain est additif.
    videos_vues: dict[int, dict[str, TrackVideo]] = {}

    for album_title, raw_tracks in tracks_by_album.items():
        # Total d'album indexé PAR VIDEOID, comme les totaux par morceau : une
        # vidéo qui couvre deux titres du disque (clip double) est listée sur
        # les deux, et l'additionner deux fois gonflerait le total de l'album.
        album_vid_counts: dict[str, int] = {}

        for entry in raw_tracks:
            streams = api.resolve_streams(entry, view_counts)
            norm = _normalize_title(entry["title"])
            candidates = track_index.get(norm, [])

            if len(candidates) > 1:
                # HOMONYMES en base (deux morceaux distincts au même titre, ex.
                # "MEILLEUR" Souffrance vs "Meilleur" Goldee Money) : ne pas
                # écrire au hasard — les étapes 3b/4 (lien YouTube EXACT par
                # morceau) couvriront chacun individuellement.
                result["ambiguous"] += 1
                logger.info(
                    f"⚠️ Titre ambigu ({len(candidates)} morceaux en base), "
                    f"laissé aux liens par morceau : '{entry['title']}'"
                )
                continue

            matched_track = candidates[0] if candidates else None
            if matched_track:
                if streams is not None and retenu(matched_track.id):
                    video_id = entry.get("video_id")
                    vid = video_id or f"_novid_{album_title}_{norm}"
                    vid_counts.setdefault(matched_track.id, {})[vid] = streams
                    album_vid_counts[vid] = streams
                    covered_track_ids.add(matched_track.id)
                    if video_id:
                        videos_vues.setdefault(matched_track.id, {})[video_id] = TrackVideo(
                            video_id=video_id,
                            url=f"https://www.youtube.com/watch?v={video_id}",
                            source="ytm_album",
                            # Vues enregistrées SEULEMENT quand elles viennent du
                            # compteur exact de l'API : `resolve_streams` retombe
                            # sinon sur le « 1,2 M » arrondi d'ytmusicapi, qu'on
                            # ne veut pas figer comme une mesure.
                            views=view_counts.get(video_id),
                        )
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

        # Sous SÉLECTION, pas de total d'album : il s'additionne sur tous les
        # morceaux du disque, dont les compteurs ne sont plus demandés. L'écrire
        # donnerait un total amputé — plus faux que pas de total du tout.
        album_total_streams = sum(album_vid_counts.values())
        if album_total_streams > 0 and track_ids is None:
            data_manager.update_album_ytm_streams(artist.id, album_title, album_total_streams)

        result["albums_processed"] += 1

    # ── Étape 3b : les vidéos HORS canal DÉJÀ connues de la base ──────────────
    # Le clip officiel d'un morceau ne vit presque jamais sur le canal
    # « - Topic » : il vient du catalogue Genius (`youtube_url`) ou d'une
    # validation manuelle, et porte un videoId DIFFÉRENT de la version audio.
    # Jusqu'au 2026-09-07 il n'était additionné qu'à l'étape 4, réservée aux
    # feats repérés par Kworb — un morceau d'album gardait donc les seules vues
    # de son audio, et celles de son clip n'étaient comptées nulle part
    # (mesuré sur « Magot » et « Déluge »). Elles le sont ici, pour TOUS les
    # morceaux, et sans requête supplémentaire : les identifiants sont en base,
    # ils rejoignent simplement le batch de l'étape 5.
    extras: dict[int, dict[str, TrackVideo]] = {}
    for t in db_tracks:
        if not retenu(t.id):
            continue
        connues = {v.video_id: v for v in t.videos if v.video_id}
        clip_vid = _extract_video_id(t.youtube_url)
        if clip_vid and clip_vid not in connues:
            connues[clip_vid] = TrackVideo(
                video_id=clip_vid, url=t.youtube_url, source=t.youtube_url_source
            )
        # Ce que la passe canal vient de chiffrer n'a pas à être redemandé.
        deja_chiffrees = set(vid_counts.get(t.id, {}))
        for video_id, video in connues.items():
            if video_id not in deja_chiffrees:
                extras.setdefault(t.id, {})[video_id] = video

    # ── Étape 4 : DÉCOUVRIR une version audio pour les morceaux hors canal ────
    # Les feats sortis sur les albums d'AUTRES artistes ne passent pas par le
    # canal YTM de l'artiste : leur audio n'est connu de personne. On la cherche
    # (ytmusicapi, filter=songs, avec cache), et seulement pour ceux que Kworb a
    # repérés — une recherche coûte une requête par morceau, et ce gate est ce
    # qui empêche d'en lancer une pour toute la discographie.
    tracks_par_id = {t.id: t for t in db_tracks}
    candidates = [
        t
        for t in db_tracks
        if t.id not in covered_track_ids
        and t.streams.spotify_streams is not None  # repéré sur Kworb
        and retenu(t.id)
    ]
    if candidates:
        try:
            from src.config import YOUTUBE_CONFIDENCE_THRESHOLD
            from src.youtube.youtube_searcher import YouTubeSearcher

            searcher = YouTubeSearcher()
        except (ImportError, YTMusicError, requests.RequestException, OSError) as e:
            logger.warning(f"Recherche audio YTM indisponible ({e}) — clips seulement")
            searcher, YOUTUBE_CONFIDENCE_THRESHOLD = None, 1.1

        for t in candidates:
            if not searcher:
                continue
            try:
                # Pour un feat, chercher sous l'artiste PRINCIPAL (meilleur rappel).
                # Règle PARTAGÉE (`youtube_utils`) : c'est elle qui détermine la
                # clé du cache de recherche, qu'un rejet doit pouvoir purger.
                results = searcher.search_track(
                    artiste_de_recherche(t, artist.name), t.title, max_results=5
                )
                best = results[0] if results else None
                if (
                    best
                    and not best.get("is_search_url")
                    and best.get("relevance_score", 0) >= YOUTUBE_CONFIDENCE_THRESHOLD
                    and best.get("video_id")
                ):
                    video_id = best["video_id"]
                    extras.setdefault(t.id, {}).setdefault(
                        video_id,
                        TrackVideo(
                            video_id=video_id,
                            url=f"https://www.youtube.com/watch?v={video_id}",
                            source="search_auto",
                        ),
                    )
            except (AttributeError, TypeError, KeyError, IndexError) as e:
                logger.debug(f"Recherche audio YTM échouée '{t.title}': {e}")

    # ── Étape 5 : UN batch pour toutes les vidéos hors canal ──────────────────
    if extras:
        all_extra_vids = sorted({v for videos in extras.values() for v in videos})
        logger.info(
            f"🎬 Vidéos hors canal : {len(extras)} morceau(x), "
            f"{len(all_extra_vids)} vidéo(s) (clip Genius, lien manuel, audio trouvée)"
        )
        extra_counts = api.fetch_view_counts_batch(all_extra_vids)
        result["yt_api_calls"] += (len(all_extra_vids) + 49) // 50
        for track_id, videos in extras.items():
            for video_id, video in videos.items():
                vues = extra_counts.get(video_id)
                if vues is None:
                    logger.debug(f"⚠️ Vidéo sans viewCount : {video_id} (track #{track_id})")
                    continue
                vid_counts.setdefault(track_id, {})[video_id] = vues
                video.views = vues
                videos_vues.setdefault(track_id, {})[video_id] = video
                if track_id not in covered_track_ids:
                    covered_track_ids.add(track_id)
                    result["feats_covered"] += 1

    # ── Étape 6 : écriture — un total par morceau, une vidéo comptée une fois ─
    # La somme porte sur un dict indexé par videoId : si le lien Genius EST la
    # vidéo audio du canal, elle n'est comptée qu'une fois.
    #
    # Les vidéos PARTAGÉES entre plusieurs morceaux sont écartées de la somme
    # (cf. `videos_partagees`) et signalées : on refuse de conclure plutôt que
    # de multiplier un même compteur.
    partagees = videos_partagees(vid_counts)
    vues_par_video = {vid: n for vids in vid_counts.values() for vid, n in vids.items()}
    result["videos_partagees"] = _rapport_partagees(partagees, vid_counts, tracks_par_id)
    # Montant que la règle laisse de côté : une perte assumée doit être VISIBLE,
    # sans quoi elle se lit comme une baisse inexpliquée des compteurs.
    result["vues_non_attribuees"] = sum(vues_par_video.get(vid, 0) for vid in partagees)

    for track_id, vids in sorted(vid_counts.items()):
        comptees = {v: n for v, n in vids.items() if v not in partagees}
        total = sum(comptees.values())
        if not comptees:
            # Toutes ses vidéos sont ambiguës : ne rien écrire vaut mieux
            # qu'écrire 0, qui se lirait comme « jamais écouté ».
            logger.info(
                f"⏸️ « {tracks_par_id[track_id].title} » : "
                "aucune vidéo qui lui soit propre — total inchangé"
            )
            continue
        data_manager.update_track_ytm_streams(track_id, total)
        if len(comptees) > 1:
            result["multi_video"] += 1
            titre = tracks_par_id[track_id].title if track_id in tracks_par_id else track_id
            logger.debug(f"🎛️ « {titre} » : {len(comptees)} vidéos sommées → {total:,}")

    for track_id, videos in videos_vues.items():
        data_manager.record_track_videos(track_id, list(videos.values()))

    logger.info(
        f"YTMusic terminé : {result['matched']} matchés, "
        f"{result['unmatched']} non matchés, "
        f"{result['feats_covered']} morceau(x) hors canal via lien YouTube, "
        f"{result['multi_video']} morceau(x) à plusieurs vidéos, "
        f"{result['albums_processed']} albums, "
        f"{result['yt_api_calls']} requête(s) YouTube API"
    )
    if result["unmatched_titles"]:
        logger.warning(f"Titres YTMusic non matchés : {result['unmatched_titles']}")

    return result


# ── CLI standalone ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

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
    print(f"Hors canal (lien YT)  : {summary['feats_covered']}")
    print(f"À plusieurs vidéos    : {summary['multi_video']}")
    if summary["videos_partagees"]:
        print(
            f"Vidéos partagées      : {len(summary['videos_partagees'])} "
            f"({summary['vues_non_attribuees']:,} vues non attribuées)".replace(",", " ")
        )
        for v in summary["videos_partagees"]:
            print(f"   • {v['titre_video'] or v['url']} → {', '.join(v['morceaux'])}")
    print(f"Morceaux non matchés  : {summary['unmatched']}")
    print(f"Albums traités        : {summary['albums_processed']}")
    print(f"Requêtes YouTube API  : {summary['yt_api_calls']}")
    if summary["unmatched_titles"]:
        print(f"Titres non matchés    : {summary['unmatched_titles']}")
