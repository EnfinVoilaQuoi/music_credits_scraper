"""Vues des vidéos YouTube + différenciation clip / morceau « classique ».

Chantier « Media », étape « compteurs ». Collecte les videoId de TOUTES les
vidéos connues d'un morceau (table `track_videos`, e20 — le clip ET la version
audio du canal « - Topic », souvent deux vidéos), fait UN batch
`fetch_video_meta_batch` (vues + titre + chaîne, coût quota identique aux
streams), classifie chaque vidéo (`classify_video_kind`) et enregistre le tout.

**Séparé de `update_ytmusic_streams`** : ``ytm_streams`` est la SOMME des vues
de toutes les vidéos d'un morceau (l'écoute, quelle que soit la forme) ; ici on
mesure CHAQUE vidéo et sa nature — c'est la réponse au « bien différencier un
clip d'un morceau classique ».

Deux écritures, et c'est délibéré : `record_track_videos` porte le détail par
vidéo, `update_track_video_views` garde les colonnes `tracks.youtube_video_*`
pour la vidéo PRINCIPALE (celle que la GUI affiche) — les colonnes n'ont pas
été retirées par ce lot. La vidéo principale est celle du lien `youtube_url`
quand il y en a un, sinon la plus vue : sans cette règle, la colonne se mettrait
à décrire une vidéo différente à chaque passage.
"""

from src.models import TrackVideo
from src.utils.logger import get_logger
from src.utils.youtube_utils import classify_video_kind, extract_video_id

logger = get_logger(__name__)


def videos_a_mesurer(track) -> list[str]:
    """videoId connus d'un morceau, la vidéo du lien `youtube_url` en TÊTE.

    Fonction pure. L'ordre porte une décision : la première est celle qui
    alimentera les colonnes `tracks.youtube_video_*`.
    """
    principale = extract_video_id(track.youtube_url)
    ids = [principale] if principale else []
    ids += [v.video_id for v in track.videos if v.video_id and v.video_id != principale]
    return ids


def update_video_views(artist, tracks, data_manager, api=None, track_ids=None) -> dict:
    """Met à jour vues + kind de TOUTES les vidéos YouTube des morceaux.

    ``api`` : YTMusicAPI injecté (StreamsProvider) ; créé en interne si None.
    ``track_ids`` : restreint les ÉCRITURES et le batch YouTube à ces morceaux
    (case « limiter aux morceaux cochés ») ; None = tous.

    Returns:
        Rapport ``{"updated", "videos", "no_video_id", "no_meta", "by_kind"}``
        — ``updated`` compte les MORCEAUX, ``videos`` les vidéos mesurées.
    """
    report = {"updated": 0, "videos": 0, "no_video_id": 0, "no_meta": 0, "by_kind": {}}

    if track_ids is not None:
        tracks = [t for t in tracks if t.id in track_ids]

    pairs = []  # (track, [video_id, ...]) — la principale en tête
    for track in tracks:
        ids = videos_a_mesurer(track)
        if ids:
            pairs.append((track, ids))
        else:
            report["no_video_id"] += 1

    if not pairs:
        logger.info(f"Vues vidéos : aucun lien YouTube exploitable pour {artist.name}")
        return report

    if api is None:
        from src.api.ytmusic_api import YTMusicAPI

        api = YTMusicAPI()
    meta = api.fetch_video_meta_batch([vid for _, ids in pairs for vid in ids])

    for track, ids in pairs:
        mesurees = []
        for vid in ids:
            info = meta.get(vid)
            if not info:
                report["no_meta"] += 1
                continue
            kind = classify_video_kind(info.get("title"), info.get("channel"))
            # Le TITRE est conservé (e21) : c'est lui qui dit ce que la vidéo
            # COUVRE, donc ce qui rend vérifiable une vidéo partagée par
            # plusieurs morceaux — « Donjon & 2h22 » est un clip double.
            mesurees.append(
                TrackVideo(
                    video_id=vid, kind=kind, views=info.get("views"), title=info.get("title")
                )
            )
            report["videos"] += 1
            report["by_kind"][kind] = report["by_kind"].get(kind, 0) + 1

        if not mesurees:
            continue

        data_manager.record_track_videos(track.id, mesurees)
        # Colonnes `tracks.youtube_video_*` : la vidéo PRINCIPALE, c'est-à-dire
        # la première de la liste (cf. `videos_a_mesurer`).
        principale = mesurees[0]
        if data_manager.update_track_video_views(track.id, principale.views, principale.kind):
            # Mutation mémoire (affichage immédiat, pas de reload nécessaire).
            # Les vidéos déjà portées par l'objet sont mises à jour EN PLACE :
            # les remplacer perdrait leur `url` et leur provenance, que cette
            # passe ne connaît pas.
            track.media.youtube_video_kind = principale.kind
            track.media.youtube_video_views = principale.views
            par_id = {v.video_id: v for v in track.videos}
            for mesuree in mesurees:
                existante = par_id.get(mesuree.video_id)
                if existante is None:
                    track.videos.append(mesuree)
                else:
                    existante.kind = mesuree.kind
                    existante.views = mesuree.views
                    existante.title = mesuree.title
            report["updated"] += 1

    logger.info(
        f"Vues vidéos {artist.name} : {report['updated']} morceau(x) mis à jour, "
        f"{report['videos']} vidéo(s) ({report['by_kind']}), "
        f"{report['no_meta']} sans méta, {report['no_video_id']} sans videoId"
    )
    return report
