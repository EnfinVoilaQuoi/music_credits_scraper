"""Mise à jour des streams Spotify (kworb.net) pour les morceaux et albums d'un artiste."""
import re
import sys
import io
import unicodedata
import logging
from pathlib import Path
from typing import Dict, List, Optional

sys.path.append(str(Path(__file__).parent.parent.parent))

from src.scrapers.kworb_scraper import KworbScraper
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _normalize_title(s: str) -> str:
    """Normalise un titre pour la comparaison : feat, apostrophes, accents, casse."""
    # Retirer les suffixes featuring : "Titre (feat. X)" → "Titre"
    s = re.sub(r'\s*[\(\[]\s*(?:feat|ft|avec|with)\.?[^\)\]]*[\)\]]', '', s, flags=re.IGNORECASE)
    # Unifier/supprimer les apostrophes (typographiques ou droites)
    s = re.sub(r"['’‘`´]", '', s)
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')
    s = re.sub(r'\s+', ' ', s).strip().lower()
    return s


def update_kworb_streams(artist, data_manager) -> Dict:
    """Scrape kworb.net et met à jour les streams des morceaux et albums de l'artiste.

    Args:
        artist: objet Artist avec au moins `id`, `name`, `spotify_id`
        data_manager: instance de DataManager

    Returns:
        dict résumé {matched, unmatched, albums_updated, unmatched_titles}
    """
    result = {
        "matched": 0,
        "unmatched": 0,
        "albums_updated": 0,
        "unmatched_titles": [],
    }

    # ── 1. S'assurer que l'ID Spotify artiste est disponible ──────────────────
    spotify_artist_id: Optional[str] = getattr(artist, 'spotify_id', None)

    if not spotify_artist_id:
        logger.info(f"spotify_id manquant pour '{artist.name}' — tentative via scraper Spotify")
        try:
            from src.scrapers.spotify_id_scraper_v2 import SpotifyIDScraper
            with SpotifyIDScraper(headless=True) as scraper:
                # 1. Méthode déterministe : déduire l'ID artiste depuis la page
                #    d'un morceau dont on connaît déjà le Spotify ID
                tracks = data_manager.get_artist_tracks(artist.id)
                for t in tracks:
                    track_sid = getattr(t, 'spotify_id', None)
                    if track_sid:
                        spotify_artist_id = scraper.get_artist_id_from_track(track_sid)
                        if spotify_artist_id:
                            break
                # 2. Secours : recherche par nom (ambiguïté possible)
                if not spotify_artist_id:
                    spotify_artist_id = scraper.get_artist_spotify_id(artist.name)
        except Exception as e:
            logger.error(f"Impossible de récupérer l'ID Spotify artiste: {e}")

        if spotify_artist_id:
            data_manager.update_artist_spotify_id(artist.id, spotify_artist_id)
            artist.spotify_id = spotify_artist_id
            logger.info(f"✅ spotify_id artiste récupéré et stocké: {spotify_artist_id}")
        else:
            logger.error(f"❌ Impossible de récupérer l'ID Spotify de '{artist.name}'. Abandon.")
            return result

    logger.info(f"🎵 Mise à jour Kworb pour '{artist.name}' (spotify_id={spotify_artist_id})")

    scraper = KworbScraper()

    # ── 2. Streams des morceaux ───────────────────────────────────────────────
    kworb_songs = scraper.scrape_songs(spotify_artist_id)

    if kworb_songs:
        # Charger les tracks de l'artiste depuis la DB
        tracks = data_manager.get_artist_tracks(artist.id)
        # Construire un index normalisé titre → track
        track_index: Dict[str, object] = {
            _normalize_title(t.title): t for t in tracks
        }

        for entry in kworb_songs:
            norm_kworb = _normalize_title(entry["title"])
            matched_track = track_index.get(norm_kworb)

            if matched_track:
                ok = data_manager.update_track_spotify_streams(
                    matched_track.id, entry["streams"], entry["daily_streams"]
                )
                if ok:
                    result["matched"] += 1
                    logger.debug(f"✅ Match: '{entry['title']}' → {entry['streams']:,} streams")
            else:
                result["unmatched"] += 1
                result["unmatched_titles"].append(entry["title"])
                logger.debug(f"⚠️ Pas de match en DB: '{entry['title']}'")

        logger.info(
            f"Songs Kworb: {result['matched']} matchés, {result['unmatched']} non matchés"
        )
        if result["unmatched_titles"]:
            logger.warning(f"Titres non matchés: {result['unmatched_titles']}")
    else:
        logger.warning("Aucun morceau récupéré depuis Kworb.")

    # ── 3. Streams des albums ─────────────────────────────────────────────────
    kworb_albums = scraper.scrape_albums(spotify_artist_id)

    for album_entry in kworb_albums:
        ok = data_manager.upsert_album(
            artist.id, album_entry["title"], album_entry["streams"], album_entry["daily_streams"]
        )
        if ok:
            result["albums_updated"] += 1

    logger.info(f"Albums Kworb: {result['albums_updated']} mis à jour")

    return result


# ── CLI standalone ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if sys.platform == 'win32':
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

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
    print(f"Morceaux matchés    : {summary['matched']}")
    print(f"Morceaux non matchés: {summary['unmatched']}")
    print(f"Albums mis à jour   : {summary['albums_updated']}")
    if summary["unmatched_titles"]:
        print(f"Titres non matchés  : {summary['unmatched_titles']}")
