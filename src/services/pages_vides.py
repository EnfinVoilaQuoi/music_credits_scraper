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
