"""Mise à jour des streams Spotify (kworb.net) pour les morceaux et albums d'un artiste.

v2 — refonte après session d'exploration du site (JOURNAL 2026-07-02) :
  · VALIDATION D'IDENTITÉ : le <title> de la page Kworb donne le nom de l'artiste ;
    mismatch → re-vote de l'ID (sans les feats !) → re-scrape → abort si toujours faux.
    (Bug historique : l'ID d'Isha pointait vers Limsa d'Aulnay, élu par un vote
    dominé par les pages de ses feats.)
  · MATCHING PAR SPOTIFY ID d'abord (les lignes Kworb contiennent l'URL du track),
    titre normalisé en fallback ; backfill des spotify_id manquants en base.
  · Fraîcheur = date "Last updated" de la page Kworb (pas now()).
  · Totaux artiste (récap Total/As lead/As feature) stockés sur artists.
  · Albums agrégés par titre (éditions multiples sommées, IDs conservés),
    filtrés aux albums PROPRES (≥2 morceaux de l'artiste en base sur l'album —
    garde les projets communs type Bitume Caviar, écarte les simples apparitions).
"""
import re
import sys
import logging
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

sys.path.append(str(Path(__file__).parent.parent.parent))

from src.scrapers.kworb_scraper import KworbScraper
from src.utils.logger import get_logger

logger = get_logger(__name__)


# Normaliseur PARTAGÉ (même matching que update_ytmusic — cf. title_matching.py)
from src.utils.title_matching import normalize_title as _normalize_title


def _names_match(page_name: Optional[str], artist_name: str) -> bool:
    """Le nom affiché par la page Kworb correspond-il à notre artiste ?"""
    if not page_name:
        return False
    a, b = _normalize_title(page_name), _normalize_title(artist_name)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    import difflib
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.8


def _vote_artist_spotify_id(artist, data_manager, max_pages: int = 5) -> Optional[str]:
    """
    Déduit l'ID Spotify de l'ARTISTE par vote majoritaire sur les crédits de
    plusieurs de ses morceaux (pages embed). Doubles garde-fous anti-Limsa
    (bug historique : projets communs Isha × Limsa) :
      · ne vote que sur les NON-FEATS ;
      · vote NAME-AWARE : seul l'artiste crédité portant le bon nom vote —
        un morceau dont aucun crédité ne matche s'abstient.
    Secours : recherche par nom si pas assez de morceaux propres avec spotify_id.
    """
    from collections import Counter
    try:
        from src.scrapers.spotify_id_scraper_v2 import SpotifyIDScraper
    except Exception as e:
        logger.error(f"SpotifyIDScraper indisponible: {e}")
        return None

    try:
        tracks = data_manager.get_artist_tracks(artist.id)
    except Exception:
        tracks = []
    track_ids = [
        t.spotify_id for t in tracks
        if getattr(t, 'spotify_id', None) and not getattr(t, 'is_featuring', False)
    ]

    votes = Counter()
    try:
        with SpotifyIDScraper(headless=True) as scraper:
            for sid in track_ids[:max_pages]:
                aid = scraper.get_artist_id_from_track(sid, expected_name=artist.name)
                if aid:
                    votes[aid] += 1
            if votes:
                best, count = votes.most_common(1)[0]
                total = sum(votes.values())
                if (count >= 2 and count > total / 2) or total == 1:
                    logger.info(f"🗳️ ID artiste Spotify: {best} ({count}/{total} voix, non-feats)")
                    return best
                logger.warning(f"🗳️ Vote ID artiste non concluant: {dict(votes)}")
            # Secours : recherche par nom (ambiguïté possible)
            return scraper.get_artist_spotify_id(artist.name)
    except Exception as e:
        logger.error(f"Vote ID artiste Spotify échoué: {e}")
        return None


def _scrape_validated(scraper, artist, data_manager, spotify_artist_id):
    """Scrape la page songs et valide l'identité. Re-vote une fois si mismatch.

    Returns:
        (page_songs, spotify_artist_id_corrigé) — page_songs=None si échec/identité fausse.
    """
    page = scraper.scrape_songs(spotify_artist_id)
    page_name = page.get('artist_name') if page else None

    if page and page['entries'] and _names_match(page_name, artist.name):
        return page, spotify_artist_id

    if page and page_name and not _names_match(page_name, artist.name):
        logger.warning(
            f"🚨 Page Kworb de {spotify_artist_id} = '{page_name}' ≠ '{artist.name}' "
            f"— ID artiste erroné (homonyme/collab), re-vote sans feats"
        )
    else:
        logger.warning(f"⚠️ Page Kworb vide/absente pour {spotify_artist_id} — re-vote")

    revoted = _vote_artist_spotify_id(artist, data_manager)
    if not revoted or revoted == spotify_artist_id:
        return None, spotify_artist_id

    page = scraper.scrape_songs(revoted)
    page_name = page.get('artist_name') if page else None
    if page and page['entries'] and _names_match(page_name, artist.name):
        logger.info(f"🔁 ID artiste corrigé: {spotify_artist_id} → {revoted}")
        data_manager.update_artist_spotify_id(artist.id, revoted)
        artist.spotify_id = revoted
        return page, revoted

    if page_name:
        logger.error(f"🚨 Après re-vote, page = '{page_name}' ≠ '{artist.name}' — abandon")
    return None, spotify_artist_id


def update_kworb_streams(artist, data_manager) -> Dict:
    """Scrape kworb.net et met à jour les streams des morceaux et albums de l'artiste.

    Args:
        artist: objet Artist avec au moins `id`, `name`, `spotify_id`
        data_manager: instance de DataManager

    Returns:
        dict résumé {matched, unmatched, albums_updated, unmatched_titles,
                     matched_by_id, matched_by_title, spotify_ids_backfilled,
                     albums_excluded, artist_name, kworb_updated}
    """
    result = {
        "matched": 0,
        "unmatched": 0,
        "albums_updated": 0,
        "unmatched_titles": [],
        "unmatched_details": [],  # [(titre kworb, streams)] triés desc — pour la GUI
        "matched_by_id": 0,
        "matched_by_title": 0,
        "spotify_ids_backfilled": 0,
        "albums_excluded": [],
        "artist_name": None,
        "kworb_updated": None,
    }

    # ── 1. S'assurer que l'ID Spotify artiste est disponible ──────────────────
    spotify_artist_id: Optional[str] = getattr(artist, 'spotify_id', None)

    if not spotify_artist_id:
        logger.info(f"spotify_id manquant pour '{artist.name}' — vote sur les pages tracks")
        spotify_artist_id = _vote_artist_spotify_id(artist, data_manager)
        if spotify_artist_id:
            data_manager.update_artist_spotify_id(artist.id, spotify_artist_id)
            artist.spotify_id = spotify_artist_id
            logger.info(f"✅ spotify_id artiste récupéré et stocké: {spotify_artist_id}")
        else:
            logger.error(f"❌ Impossible de récupérer l'ID Spotify de '{artist.name}'. Abandon.")
            return result

    logger.info(f"🎵 Mise à jour Kworb pour '{artist.name}' (spotify_id={spotify_artist_id})")

    scraper = KworbScraper()

    # ── 2. Page songs + VALIDATION D'IDENTITÉ ─────────────────────────────────
    page_songs, spotify_artist_id = _scrape_validated(
        scraper, artist, data_manager, spotify_artist_id)

    if not page_songs:
        logger.error("❌ Impossible d'obtenir une page Kworb validée. Aucune écriture.")
        return result

    result["artist_name"] = page_songs['artist_name']
    kworb_date = page_songs['last_updated']
    result["kworb_updated"] = kworb_date.strftime('%Y-%m-%d') if kworb_date else None

    # ── 3. Totaux artiste (tableau récap) ─────────────────────────────────────
    summary = page_songs.get('summary') or {}
    streams_sum = summary.get('streams') or {}
    daily_sum = summary.get('daily') or {}
    if streams_sum.get('total'):
        data_manager.update_artist_kworb_totals(
            artist.id,
            total=streams_sum.get('total'),
            daily=daily_sum.get('total'),
            lead=streams_sum.get('as_lead'),
            feat=streams_sum.get('as_feature'),
            kworb_date=kworb_date,
        )
        logger.info(
            f"📊 Totaux Kworb: {streams_sum.get('total'):,} streams "
            f"({daily_sum.get('total') or 0:,}/jour)"
        )

    # ── 4. Streams des morceaux : ID → titre unique → homonymes désambiguïsés ─
    tracks = data_manager.get_artist_tracks(artist.id)
    by_spotify_id = {t.spotify_id: t for t in tracks if getattr(t, 'spotify_id', None)}
    by_title: Dict[str, List] = defaultdict(list)
    for t in tracks:
        by_title[_normalize_title(t.title)].append(t)

    # Désambiguïsation des HOMONYMES (deux morceaux distincts au même titre,
    # ex. "MEILLEUR" Souffrance vs "Meilleur" Goldee Money — cf. JOURNAL) :
    # les artistes crédités du track Kworb (page embed Spotify) sont comparés
    # à l'artiste principal de chaque candidat en base.
    _embed_scraper = None

    def _match_ambiguous(entry, candidates):
        nonlocal _embed_scraper
        if not entry.get('spotify_id'):
            return None
        if _embed_scraper is None:
            try:
                from src.scrapers.spotify_id_scraper_v2 import SpotifyIDScraper
                _embed_scraper = SpotifyIDScraper(headless=True)
            except Exception as e:
                logger.warning(f"Désambiguïsation embed indisponible: {e}")
                return None
        try:
            credited = _embed_scraper.get_track_artists(entry['spotify_id'])
        except Exception:
            credited = []
        credited_norm = {_normalize_title(a['name']) for a in credited if a.get('name')}
        if not credited_norm:
            return None
        matches = []
        for cand in candidates:
            primary = (getattr(cand, 'primary_artist_name', None)
                       if getattr(cand, 'is_featuring', False) else None) or artist.name
            p = _normalize_title(primary)
            if any(p == c or p in c or c in p for c in credited_norm):
                matches.append(cand)
        return matches[0] if len(matches) == 1 else None

    # Accumulation par track : un morceau peut avoir PLUSIEURS lignes Kworb
    # (éditions/single) → SOMME des streams (comme les albums).
    agg: Dict[int, Dict] = {}

    for entry in page_songs['entries']:
        track = None
        via = None
        if entry.get('spotify_id') and entry['spotify_id'] in by_spotify_id:
            track, via = by_spotify_id[entry['spotify_id']], 'id'
        else:
            candidates = by_title.get(_normalize_title(entry['title']), [])
            if len(candidates) == 1:
                track, via = candidates[0], 'title'
            elif len(candidates) > 1:
                track = _match_ambiguous(entry, candidates)
                if track:
                    via = 'title+artistes'
                    logger.info(
                        f"🔎 Homonyme résolu par artistes crédités: '{entry['title']}' "
                        f"→ track #{track.id} ({getattr(track, 'primary_artist_name', None) or artist.name})"
                    )
                else:
                    logger.warning(
                        f"⚠️ Titre ambigu NON résolu ({len(candidates)} morceaux "
                        f"en base), passé: '{entry['title']}'"
                    )
            if track:
                # Backfill du Spotify ID depuis le lien Kworb (jamais d'écrasement)
                if entry.get('spotify_id') and not getattr(track, 'spotify_id', None):
                    if data_manager.update_track_spotify_id(track.id, entry['spotify_id']):
                        track.spotify_id = entry['spotify_id']
                        result["spotify_ids_backfilled"] += 1

        if track:
            a = agg.setdefault(track.id, {"streams": 0, "daily": 0, "n": 0, "title": entry["title"]})
            a["streams"] += entry["streams"]
            a["daily"] += entry["daily_streams"]
            a["n"] += 1
            result["matched"] += 1
            result["matched_by_id" if via == 'id' else "matched_by_title"] += 1
            logger.debug(f"✅ Match ({via}): '{entry['title']}' → {entry['streams']:,} streams")
        else:
            result["unmatched"] += 1
            result["unmatched_titles"].append(entry["title"])
            result["unmatched_details"].append((entry["title"], entry["streams"]))
            logger.debug(f"⚠️ Pas de match en DB: '{entry['title']}'")

    if _embed_scraper is not None:
        try:
            _embed_scraper.close()
        except Exception:
            pass

    for track_id, a in agg.items():
        data_manager.update_track_spotify_streams(
            track_id, a["streams"], a["daily"], updated_at=kworb_date)
        if a["n"] > 1:
            logger.info(f"🎛️ '{a['title']}': {a['n']} lignes Kworb sommées → {a['streams']:,}")

    result["unmatched_details"].sort(key=lambda x: x[1], reverse=True)
    logger.info(
        f"Songs Kworb: {result['matched']} matchés "
        f"({result['matched_by_id']} par ID, {result['matched_by_title']} par titre, "
        f"{result['spotify_ids_backfilled']} ID backfillés), "
        f"{result['unmatched']} non matchés"
    )
    if result["unmatched_titles"]:
        logger.warning(f"Titres non matchés: {result['unmatched_titles']}")

    # ── 5. Albums : agrégation des éditions + filtre albums propres ───────────
    page_albums = scraper.scrape_albums(spotify_artist_id)
    if page_albums and page_albums['entries']:
        # Albums connus en base : titre normalisé → nb de morceaux dessus
        album_track_counts = defaultdict(int)
        for t in tracks:
            if getattr(t, 'album', None):
                album_track_counts[_normalize_title(t.album)] += 1

        # Agréger les éditions par titre normalisé (streams sommés, IDs conservés)
        editions = defaultdict(lambda: {"title": None, "streams": 0, "daily": 0, "ids": []})
        for entry in page_albums['entries']:
            key = _normalize_title(entry['title'])
            agg = editions[key]
            agg["title"] = agg["title"] or entry['title']
            agg["streams"] += entry['streams']
            agg["daily"] += entry['daily_streams']
            if entry.get('spotify_id'):
                agg["ids"].append(entry['spotify_id'])

        for key, agg in editions.items():
            n_tracks = album_track_counts.get(key, 0)
            if n_tracks < 2:
                # Simple apparition (ex. XX5 de Dinos avec 1 feat) — les streams
                # du morceau sont déjà comptés au niveau track. Les projets
                # communs (Bitume Caviar…) ont ≥2 morceaux en base → gardés.
                result["albums_excluded"].append(agg["title"])
                logger.info(
                    f"⏭️ Album écarté (apparition, {n_tracks} morceau en base): "
                    f"'{agg['title']}'"
                )
                continue
            ok = data_manager.upsert_album(
                artist.id, agg["title"], agg["streams"], agg["daily"],
                spotify_album_ids=",".join(agg["ids"]) or None,
                updated_at=page_albums['last_updated'] or kworb_date,
            )
            if ok:
                result["albums_updated"] += 1
                if len(agg["ids"]) > 1:
                    logger.info(
                        f"💿 '{agg['title']}': {len(agg['ids'])} éditions agrégées "
                        f"→ {agg['streams']:,} streams"
                    )

    logger.info(
        f"Albums Kworb: {result['albums_updated']} mis à jour, "
        f"{len(result['albums_excluded'])} écartés (apparitions)"
    )

    return result


# ── CLI standalone ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if sys.platform == 'win32':
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')

    import argparse

    parser = argparse.ArgumentParser(description="Met à jour les streams Kworb pour un artiste")
    parser.add_argument("artist_name", help="Nom exact de l'artiste dans la DB")
    parser.add_argument(
        "--spotify-id",
        help="ID Spotify artiste (optionnel, sinon récupéré automatiquement)",
    )
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

    if args.spotify_id:
        artist.spotify_id = args.spotify_id

    summary = update_kworb_streams(artist, dm)
    print("\n── Résumé ──────────────────────────────────")
    print(f"Artiste (page Kworb) : {summary['artist_name']}  (maj {summary['kworb_updated']})")
    print(f"Morceaux matchés    : {summary['matched']} "
          f"({summary['matched_by_id']} par ID, {summary['matched_by_title']} par titre)")
    print(f"Spotify IDs backfillés : {summary['spotify_ids_backfilled']}")
    print(f"Morceaux non matchés: {summary['unmatched']}")
    print(f"Albums mis à jour   : {summary['albums_updated']} "
          f"(+{len(summary['albums_excluded'])} apparitions écartées)")
    if summary["unmatched_titles"]:
        print(f"Titres non matchés  : {summary['unmatched_titles']}")
