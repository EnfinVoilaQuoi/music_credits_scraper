"""Pages Genius vides désactivées, et leur RÉACTIVATION (2026-10-29).

Une page qui ne dit rien (🕳️ `page_sans_info`, 🏷️ `page_annotee_sans_trace`)
et n'a aucune trace (ni observation, ni vidéo, ni album, ni paroles) est
désactivée avec le motif `page_vide` : les étapes ne la retraitent plus. Mais
une page vide peut se remplir. L'étape discographie relit encore le détail
Genius de TOUTES les pages (album, Spotify, YouTube, sans filtre des
désactivées) ; à sa fin, une page `page_vide` qui sort des deux détecteurs est
réactivée. Les paroles, elles, ne sont relues que par l'étape crédits, qui
saute les désactivées : passé `EXPIRATION`, la page est réactivée pour que la
lecture suivante dise si elle a changé (un constat qui expire, comme
`runtime.constat_perime`).

Seules les désactivations à motif `page_vide` sont concernées : celles faites
dans l'IHM n'ont pas de motif et restent une décision de l'utilisateur.

Depuis le 2026-10-30 (décision utilisateur), la DÉSACTIVATION est elle aussi
une règle : en fin de cycle, une page vide sans AUCUN signe de vie
(`signes_de_vie`) est désactivée, origine `regle:page_vide` ; une page qu'une
règle avait désactivée et que l'utilisateur réactive dans l'IHM ne l'est plus
(`DisabledTracksManager.load_reactivees_a_la_main`).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from src.utils.logger import get_logger

logger = get_logger(__name__)

MOTIF_PAGE_VIDE = "page_vide"
EXPIRATION = timedelta(days=180)


def toujours_vide(track) -> bool:
    """Le verdict des détecteurs du panneau, seul juge de « vide »."""
    from src.services.revue import page_annotee_sans_trace, page_sans_info

    return bool(page_sans_info(track) or page_annotee_sans_trace(track))


def a_reactiver(
    tracks, motifs: dict[int, dict], maintenant: datetime | None = None
) -> dict[int, str]:
    """`{track_id: raison}` des pages `page_vide` à réactiver : remplie, ou
    désactivée depuis plus d'`EXPIRATION`. Une fiche disparue (fusion,
    suppression) n'est pas réactivée : elle n'est plus dans `tracks`."""
    maintenant = maintenant or datetime.now()
    par_id = {t.id: t for t in tracks}
    raisons = {}
    for tid, motif in motifs.items():
        if motif.get("motif") != MOTIF_PAGE_VIDE or tid not in par_id:
            continue
        if not toujours_vide(par_id[tid]):
            raisons[tid] = "la page n'est plus vide"
            continue
        try:
            le = datetime.fromisoformat(motif.get("le") or "")
        except ValueError:
            le = None
        if le is None or maintenant - le > EXPIRATION:
            raisons[tid] = f"désactivée depuis plus de {EXPIRATION.days} j : paroles à relire"
    return raisons


def reactiver_pages_remplies(disabled, artist, tracks, maintenant: datetime | None = None) -> int:
    """Réactive ce qu'`a_reactiver` désigne, et le journalise. Rend le nombre."""
    raisons = a_reactiver(tracks, disabled.load_motifs(artist.name), maintenant)
    if not raisons:
        return 0
    titres = {t.id: t.title for t in tracks}
    for tid, raison in raisons.items():
        logger.info(f"♻️ Page réactivée « {titres.get(tid)} » (#{tid}) : {raison}")
    disabled.reactiver(artist.name, raisons)
    return len(raisons)


# ── La DÉSACTIVATION par règle (décision utilisateur 2026-10-30) ─────────────

ORIGINE_REGLE = f"regle:{MOTIF_PAGE_VIDE}"

#: Observations qui ne disent rien de la VIE du morceau : la date que la page
#: Genius elle-même donne (et sa reprise `legacy`).
_OBSERVATIONS_SANS_VIE = frozenset({("release_date", "genius"), ("release_date", "legacy")})


def signes_de_vie(track, observations=()) -> list[str]:
    """Ce qui fait qu'une page vide au sens du panneau n'est pas « sans RIEN »
    (CORRECTIFS, validé le 2026-10-30) : une anecdote, un crédit (même de
    l'artiste), une vidéo, un identifiant de catalogue, des streams, un album,
    des paroles ou un LRC, une observation d'une source (une mesure SongBPM,
    l'ID Apple que propose Genius). Une seule suffit : la page reste à
    l'humain."""
    from src.utils.paroles_genius import texte_utile

    signes = []
    if (track.anecdotes or "").strip():
        signes.append("anecdote")
    if track.credits:
        signes.append("crédit")
    if track.videos or track.youtube_url:
        signes.append("vidéo")
    ids = (track.spotify_id, track.deezer_id, track.isrc, track.discogs_id, track.apple_music_id)
    if any(ids):
        signes.append("identifiant de catalogue")
    if track.streams.spotify_streams or track.streams.ytm_streams:
        signes.append("streams")
    if track.album:
        signes.append("album")
    if any(ch.isalnum() for ch in texte_utile(track.lyrics.text)) or track.lyrics.synced:
        signes.append("paroles")
    if any((o.field, o.source) not in _OBSERVATIONS_SANS_VIE for o in observations):
        signes.append("observation")
    return signes


def a_desactiver(tracks, desactives, observations: dict, a_la_main=()) -> list:
    """PUR. Les pages à désactiver : vides pour le panneau (🕳️ / 🏷️) ET sans
    aucun signe de vie, ni déjà désactivées, ni réactivées à la main."""
    return [
        t
        for t in tracks
        if t.id is not None
        and t.id not in desactives
        and t.id not in a_la_main
        and toujours_vide(t)
        and not signes_de_vie(t, observations.get(t.id) or ())
    ]


def desactiver_pages_vides(disabled, data_manager, artist, tracks) -> int:
    """Désactive (réversible, motif `page_vide`, origine `regle:page_vide`)
    les pages qu'`a_desactiver` désigne, et le journalise. Appelée en fin de
    CYCLE, quand identité, crédits et streams ont eu leur chance de donner
    signe de vie ; une page réactivée faute de relecture (180 j) repasse ainsi
    par eux avant d'être jugée. Jamais de suppression."""
    pages = a_desactiver(
        tracks,
        disabled.load_disabled_tracks(artist.name),
        data_manager.get_artist_observations(artist.id) if artist.id else {},
        disabled.load_reactivees_a_la_main(artist.name),
    )
    if not pages:
        return 0
    for t in pages:
        logger.info(f"🕳️ Page désactivée « {t.title} » (#{t.id}) : vide, sans aucune trace")
    disabled.desactiver(artist.name, [t.id for t in pages], MOTIF_PAGE_VIDE, ORIGINE_REGLE)
    return len(pages)
