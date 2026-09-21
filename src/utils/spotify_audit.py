"""Relecture des identifiants Spotify déjà en base, confrontés à l'oracle.

Vit dans `src/` et non dans un script parce que **deux CLI en dépendent** —
`audit_spotify_ids.py` qui SIGNALE et `repair_spotify_ids.py` qui RETIRE — et
qu'un script ne s'importe pas (`scripts/` n'est pas un package). Les deux
doivent balayer exactement les mêmes lignes et juger avec exactement le même
prédicat : si le rapport et la réparation divergeaient, on retirerait des IDs
que le rapport n'a jamais montrés.

Aucune règle ici : le verdict vient d'`identite_concorde`
(`src/utils/spotify_identity.py`), et le balayage n'est qu'une requête.
"""

import time

from sqlalchemy import text

from src.models import Artist, Track
from src.utils.logger import get_logger

logger = get_logger(__name__)


def lignes_a_verifier(engine, artiste: str | None = None, limite: int | None = None) -> list[dict]:
    """Toutes les lignes portant un ID Spotify — colonne ET table (e23).

    Une ligne par (morceau, ID) : deux morceaux peuvent partager un ID — c'est
    précisément ce qu'on cherche — et un morceau peut porter plusieurs éditions.

    Les deux magasins sont interrogés pour la même raison que
    `get_track_ids_by_spotify_id` : la colonne porte l'ID PRINCIPAL (seul écrit
    par `save_track`), la table les éditions alternatives.

    Les RENDITIONS (e28) sont exclues : leur titre Spotify diffère du titre du
    morceau PAR CONSTRUCTION (« DKR - Bonus Track » sur « DKR ») — la règle
    d'asymétrie de version les signalerait toutes, et la réparation les retirerait.
    """
    filtre = "AND a.name = :artiste" if artiste else ""
    params = {"artiste": artiste} if artiste else {}
    requete = f"""
        SELECT t.id, t.title, t.duration, t.is_featuring, t.primary_artist_name,
               a.name AS artiste, t.spotify_id AS sid, 1 AS principal
          FROM tracks t JOIN artists a ON a.id = t.artist_id
         WHERE t.spotify_id IS NOT NULL AND t.spotify_id != '' {filtre}
         UNION
        SELECT t.id, t.title, t.duration, t.is_featuring, t.primary_artist_name,
               a.name, s.spotify_id, 0
          FROM track_spotify_ids s
          JOIN tracks t ON t.id = s.track_id
          JOIN artists a ON a.id = t.artist_id
         WHERE s.kind = 'edition' {filtre}
         ORDER BY 6, 2
    """
    with engine.connect() as conn:
        lignes = [dict(r) for r in conn.execute(text(requete), params).mappings().all()]

    # Dédup : dans le cas courant la colonne ET la table portent le même ID, ce
    # qui donne deux lignes ne différant que par `principal`. On garde la
    # PRINCIPALE — sinon le rapport dirait « édition » d'un ID qu'on ouvre.
    par_cle: dict[tuple, dict] = {}
    for ligne in lignes:
        cle = (ligne["id"], ligne["sid"])
        connue = par_cle.get(cle)
        if connue is None or ligne["principal"] > connue["principal"]:
            par_cle[cle] = ligne
    uniques = list(par_cle.values())
    return uniques[:limite] if limite else uniques


def track_de_la_ligne(ligne: dict) -> Track:
    """Le morceau tel que le prédicat l'attend — un vrai `Track`, pas un dict.

    C'est ce qui permet d'appeler `identite_concorde` sans en recopier la moindre
    règle : l'audit, la réparation et le garde-fou jugent avec le MÊME code.
    `is_featuring` et `primary_artist_name` comptent autant que le titre —
    c'est sur l'artiste PRINCIPAL qu'un featuring se juge.
    """
    track = Track(title=ligne["title"], artist=Artist(name=ligne["artiste"]))
    track.id = ligne["id"]
    track.duration = ligne["duration"]
    track.is_featuring = bool(ligne["is_featuring"])
    track.primary_artist_name = ligne["primary_artist_name"]
    return track


def purger_cache_scraper(spotify_id: str) -> int:
    """Retire un identifiant du cache du scraper, sur disque.

    Import LOCAL : le module de scraping tire Playwright, et ni l'audit ni la
    réparation n'ont besoin d'un navigateur pour purger un fichier JSON. Le
    scraper est instancié SANS driver (il est créé paresseusement) — on ne fait
    que lire et réécrire son cache.
    """
    from src.scrapers.spotify_id_scraper_v2 import SpotifyIDScraper

    try:
        return SpotifyIDScraper(headless=True).oublier_identifiant(spotify_id)
    except OSError as e:
        logger.warning(f"Cache du scraper non purgé pour {spotify_id} : {e}")
        return 0


def rejeter_spotify_id(data_manager, track, spotify_id: str) -> dict:
    """Rejette un ID Spotify depuis l'interface, et remet l'objet d'aplomb.

    Pendant du rejet d'un lien YouTube (`youtube_integration.reject_youtube_link`)
    et, comme lui, une affaire de gestes COORDONNÉS : la base est nettoyée par
    `clear_track_spotify_id` (les trois gestes, dont les observations venues de
    l'ID), mais l'objet en mémoire doit suivre — sans quoi la fiche continuerait
    d'afficher l'ID rejeté jusqu'au prochain rechargement de l'artiste, et un
    `save_track` le RÉÉCRIRAIT depuis l'objet.

    Returns:
        Le rapport de `clear_track_spotify_id` (ce qui a été retiré).
    """
    rapport = data_manager.clear_track_spotify_id(track.id, spotify_id)
    rapport["cache_purge"] = purger_cache_scraper(spotify_id)
    track.spotify_ids = [s for s in track.spotify_ids if s != spotify_id]
    track.spotify_id_entries = [e for e in track.spotify_id_entries if e.spotify_id != spotify_id]
    if track.spotify_id == spotify_id:
        track.spotify_id = None
        track.spotify_id_checked_at = None
        # `spotify_page_title` décrit la page de l'ID qu'on vient de rejeter.
        if hasattr(track, "spotify_page_title"):
            track.spotify_page_title = None
    logger.info(f"🚫 ID Spotify rejeté : « {track.title} » → {spotify_id}")
    return rapport


def verifier_lignes(
    lignes: list[dict],
    lire_identite=None,
    *,
    tolerance: int | None = None,
    pause: float = 0.2,
    progression=None,
    interrompu=None,
) -> dict:
    """Confronte chaque ligne à l'oracle embed et rend le rapport.

    Ce balayage vivait dans `scripts/audit_spotify_ids.py`. Le câbler à la GUI
    (bouton « Vérifier les identifiants Spotify ») l'aurait recopié, et deux
    copies d'un verdict divergent — c'est le défaut du 2026-09-06, où le même
    jugement porté à deux endroits contredisait le nettoyeur. Le CLI et la
    fenêtre balaient donc les mêmes lignes avec le même prédicat.

    Args:
        lire_identite: l'oracle, INJECTÉ — `lire_identite_http` en production,
            un double en test (aucun test ne parle à Spotify).
        progression: rappel `(faits, total)` pour l'affichage ; la GUI en a
            besoin, le CLI imprime tous les 50.
        interrompu: prédicat d'arrêt (`stop_requested`), testé ENTRE deux
            requêtes — jamais au milieu d'une.

    Returns:
        `{"verifies", "illisibles", "ecarts": [...]}`. Une page illisible
        n'accuse personne : elle est comptée à part, comme `absent`.
    """
    from src.utils.spotify_identity import (
        TOLERANCE_DUREE,
        artiste_etranger,
        identite_concorde,
        lire_identite_http,
        variante_etrangere,
    )

    lire_identite = lire_identite or lire_identite_http
    tolerance = TOLERANCE_DUREE if tolerance is None else tolerance

    ecarts: list[dict] = []
    illisibles = 0
    faits = 0
    for ligne in lignes:
        if interrompu is not None and interrompu():
            break
        identite = lire_identite(ligne["sid"])
        faits += 1
        if identite is None:
            illisibles += 1
        else:
            track = track_de_la_ligne(ligne)
            ok, motif = identite_concorde(track, identite, tolerance=tolerance)
            if not ok:
                ecarts.append(
                    {
                        "track_id": ligne["id"],
                        "artiste": ligne["artiste"],
                        "titre": ligne["title"],
                        "spotify_id": ligne["sid"],
                        "principal": bool(ligne["principal"]),
                        "motif": motif,
                        "artiste_etranger": artiste_etranger(track, identite),
                        "variante_etrangere": variante_etrangere(track, identite),
                        "spotify": identite,
                    }
                )
        if progression is not None:
            progression(faits, len(lignes))
        if pause:
            time.sleep(pause)

    return {"verifies": faits - illisibles, "illisibles": illisibles, "ecarts": ecarts}
