"""Repository des morceaux, albums et crédits.

Toute la persistance liée aux `tracks` (save/get/delete/merge), aux crédits et
aux albums (streams Kworb/YTM). Utilisé comme base de `DataManager`, qui fournit
`self._get_connection` (délégué à `Database`). Les corps sont volontairement
identiques à l'ancien `data_manager.py` (refonte 1.5 à comportement constant).
"""

import json
from datetime import datetime
from typing import Any

from src.models import Credit, Track
from src.utils.logger import get_logger
from src.utils.track_mapper import track_from_row

logger = get_logger(__name__)


class TrackRepository:
    """Persistance des morceaux, crédits et albums. Requiert `self._get_connection`."""

    def save_track(self, track: Track) -> int:
        """Sauvegarde ou met à jour un morceau avec musical_key et time_signature"""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            if not track.artist or not track.artist.id:
                raise ValueError("Le morceau doit avoir un artiste avec un ID")

            cursor.execute(
                """
                SELECT id, is_featuring, primary_artist_name, featured_artists,
                    lyrics, has_lyrics, lyrics_scraped_at FROM tracks
                WHERE title = ? AND artist_id = ?
            """,
                (track.title, track.artist.id),
            )

            existing_track = cursor.fetchone()

            if existing_track:
                track.id = existing_track["id"]

                # Préserver les infos existantes
                if existing_track["is_featuring"] and not hasattr(track, "is_featuring"):
                    track.is_featuring = bool(existing_track["is_featuring"])
                    track.primary_artist_name = existing_track["primary_artist_name"]
                    track.featured_artists = existing_track["featured_artists"]

                if existing_track["lyrics"] and not hasattr(track, "lyrics"):
                    track.lyrics = existing_track["lyrics"]
                    track.has_lyrics = bool(existing_track["has_lyrics"])
                    track.lyrics_scraped_at = existing_track["lyrics_scraped_at"]

            # Sérialiser les champs JSON une seule fois (partagés UPDATE/INSERT)
            certifications_json = (
                json.dumps(getattr(track, "certifications", []))
                if hasattr(track, "certifications")
                else "[]"
            )
            album_certifications_json = (
                json.dumps(getattr(track, "album_certifications", []))
                if hasattr(track, "album_certifications")
                else "[]"
            )
            relationships_json = json.dumps(getattr(track, "relationships", []) or [])

            # Paramètres NOMMÉS : un seul dict {colonne: valeur}, lié par nom
            # (:col). L'ordre des ~44 valeurs ne peut plus se désynchroniser du
            # SQL (cause de bugs positionnels). Le même dict sert à l'UPDATE et
            # à l'INSERT ; sqlite3 ignore les clés non référencées.
            # NB : key/mode/spotify_page_title ne sont pas des champs de la
            # dataclass Track (posés dynamiquement) → getattr conservé ici.
            params = {
                "title": track.title,
                "artist_id": track.artist.id,
                "album": track.album,
                "track_number": getattr(track, "track_number", None),
                "release_date": track.release_date,
                "genius_id": track.genius_id,
                "spotify_id": track.spotify_id,
                "discogs_id": track.discogs_id,
                "isrc": getattr(track, "isrc", None),
                "bpm": track.bpm,
                "bpm_source": getattr(track, "bpm_source", None),
                "bpm_confidence": getattr(track, "bpm_confidence", None),
                "key_mode_source": getattr(track, "key_mode_source", None),
                "reccobeats_resolution": getattr(track, "reccobeats_resolution", None),
                "bpm_alt": getattr(track, "bpm_alt", None),
                "duration": track.duration,
                "genre": track.genre,
                "key": getattr(track, "key", None),
                "mode": getattr(track, "mode", None),
                "musical_key": getattr(track, "musical_key", None),
                "time_signature": getattr(track, "time_signature", None),
                "genius_url": track.genius_url,
                "spotify_url": track.spotify_url,
                "youtube_url": getattr(track, "youtube_url", None),
                "youtube_url_source": getattr(track, "youtube_url_source", None),
                "is_featuring": getattr(track, "is_featuring", False),
                "primary_artist_name": getattr(track, "primary_artist_name", None),
                "featured_artists": getattr(track, "featured_artists", None),
                "secondary_role": getattr(track, "secondary_role", None),
                "lyrics": getattr(track, "lyrics", None),
                "lyrics_scraped_at": getattr(track, "lyrics_scraped_at", None),
                "lyrics_source": getattr(track, "lyrics_source", None),
                "lyrics_synced": getattr(track, "lyrics_synced", None),
                "lyrics_synced_source": getattr(track, "lyrics_synced_source", None),
                "lyrics_synced_confidence": getattr(track, "lyrics_synced_confidence", None),
                "has_lyrics": bool(getattr(track, "lyrics", None)),  # INSERT uniquement
                "anecdotes": getattr(track, "anecdotes", None),
                "certifications_json": certifications_json,
                "album_certifications_json": album_certifications_json,
                "relationships_json": relationships_json,
                "spotify_page_title": getattr(track, "spotify_page_title", None),
                "now": datetime.now(),
                "last_scraped": track.last_scraped,
            }

            if existing_track:
                params["id"] = track.id
                # UPDATE NON-DESTRUCTIF : COALESCE préserve la valeur existante
                # quand le track entrant n'a pas la donnée (None). Évite qu'un
                # re-fetch de discographie (API Genius, champs vides) écrase
                # les données enrichies (lyrics, BPM, key, spotify_id...).
                #
                # DÉCISION is_featuring : seul champ écrasé SANS COALESCE (le
                # track en mémoire fait foi pour le statut featuring au moment du
                # save). Comportement historique conservé. Les appelants qui
                # re-sauvent depuis l'API portent is_featuring sur l'objet ; le
                # rafraîchissement de crédits passe par force_update_track_credits
                # qui relit et re-pose is_featuring AVANT le save.
                cursor.execute(
                    """
                    UPDATE tracks
                    SET album = COALESCE(:album, album),
                        track_number = COALESCE(:track_number, track_number),
                        release_date = COALESCE(:release_date, release_date),
                        genius_id = COALESCE(:genius_id, genius_id),
                        spotify_id = COALESCE(:spotify_id, spotify_id),
                        discogs_id = COALESCE(:discogs_id, discogs_id),
                        isrc = COALESCE(:isrc, isrc),
                        bpm = COALESCE(:bpm, bpm),
                        bpm_source = COALESCE(:bpm_source, bpm_source),
                        bpm_confidence = COALESCE(:bpm_confidence, bpm_confidence),
                        key_mode_source = COALESCE(:key_mode_source, key_mode_source),
                        reccobeats_resolution = COALESCE(:reccobeats_resolution, reccobeats_resolution),
                        bpm_alt = COALESCE(:bpm_alt, bpm_alt),
                        duration = COALESCE(:duration, duration),
                        genre = COALESCE(:genre, genre),
                        key = COALESCE(:key, key),
                        mode = COALESCE(:mode, mode),
                        musical_key = COALESCE(:musical_key, musical_key),
                        time_signature = COALESCE(:time_signature, time_signature),
                        genius_url = COALESCE(:genius_url, genius_url),
                        spotify_url = COALESCE(:spotify_url, spotify_url),
                        youtube_url = COALESCE(:youtube_url, youtube_url),
                        youtube_url_source = COALESCE(:youtube_url_source, youtube_url_source),
                        is_featuring = :is_featuring,
                        primary_artist_name = COALESCE(:primary_artist_name, primary_artist_name),
                        featured_artists = COALESCE(:featured_artists, featured_artists),
                        secondary_role = COALESCE(:secondary_role, secondary_role),
                        lyrics = COALESCE(:lyrics, lyrics),
                        lyrics_scraped_at = COALESCE(:lyrics_scraped_at, lyrics_scraped_at),
                        lyrics_source = COALESCE(:lyrics_source, lyrics_source),
                        lyrics_synced = COALESCE(:lyrics_synced, lyrics_synced),
                        lyrics_synced_source = COALESCE(:lyrics_synced_source, lyrics_synced_source),
                        lyrics_synced_confidence = COALESCE(:lyrics_synced_confidence, lyrics_synced_confidence),
                        has_lyrics = CASE WHEN :lyrics IS NOT NULL THEN 1 ELSE has_lyrics END,
                        anecdotes = COALESCE(:anecdotes, anecdotes),
                        certifications = CASE WHEN :certifications_json = '[]' THEN certifications ELSE :certifications_json END,
                        album_certifications = CASE WHEN :album_certifications_json = '[]' THEN album_certifications ELSE :album_certifications_json END,
                        relationships = CASE WHEN :relationships_json = '[]' THEN relationships ELSE :relationships_json END,
                        updated_at = :now,
                        last_scraped = COALESCE(:last_scraped, last_scraped)
                    WHERE id = :id
                """,
                    params,
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO tracks (
                        title, artist_id, album, track_number, release_date,
                        genius_id, spotify_id, discogs_id, isrc,
                        bpm, bpm_source, bpm_confidence, key_mode_source, reccobeats_resolution, bpm_alt, duration, genre, key, mode, musical_key, time_signature,
                        genius_url, spotify_url, youtube_url, youtube_url_source,
                        is_featuring, primary_artist_name, featured_artists, secondary_role,
                        lyrics, lyrics_scraped_at, lyrics_source, lyrics_synced, lyrics_synced_source, lyrics_synced_confidence, has_lyrics, anecdotes,
                        certifications, album_certifications, relationships, spotify_page_title,
                        created_at, updated_at, last_scraped
                    ) VALUES (
                        :title, :artist_id, :album, :track_number, :release_date,
                        :genius_id, :spotify_id, :discogs_id, :isrc,
                        :bpm, :bpm_source, :bpm_confidence, :key_mode_source, :reccobeats_resolution, :bpm_alt, :duration, :genre, :key, :mode, :musical_key, :time_signature,
                        :genius_url, :spotify_url, :youtube_url, :youtube_url_source,
                        :is_featuring, :primary_artist_name, :featured_artists, :secondary_role,
                        :lyrics, :lyrics_scraped_at, :lyrics_source, :lyrics_synced, :lyrics_synced_source, :lyrics_synced_confidence, :has_lyrics, :anecdotes,
                        :certifications_json, :album_certifications_json, :relationships_json, :spotify_page_title,
                        :now, :now, :last_scraped
                    )
                """,
                    params,
                )
                track.id = cursor.lastrowid

            # Supprimer les anciens crédits avant d'ajouter les nouveaux
            if track.id:
                cursor.execute("DELETE FROM credits WHERE track_id = ?", (track.id,))

                # Sauvegarder les nouveaux crédits
                for credit in track.credits:
                    self._save_credit(cursor, track.id, credit)

            # Sauvegarder les erreurs
            for error in track.scraping_errors:
                cursor.execute(
                    """
                    INSERT INTO scraping_errors (track_id, error_message, error_time)
                    VALUES (?, ?, ?)
                """,
                    (track.id, error, datetime.now()),
                )

            conn.commit()

            lyrics_info = (
                f", Paroles: {bool(getattr(track, 'lyrics', None))}"
                if hasattr(track, "lyrics")
                else ""
            )
            logger.info(
                f"Morceau sauvegardé: {track.title} (ID: {track.id}, Featuring: {getattr(track, 'is_featuring', False)}{lyrics_info})"
            )
            return track.id

    def _save_credit(self, cursor, track_id: int, credit: Credit):
        """Sauvegarde un crédit - VERSION SIMPLIFIÉE SANS VÉRIFICATION UNIQUE"""
        try:
            cursor.execute(
                """
                INSERT INTO credits (track_id, name, role, role_detail, source)
                VALUES (?, ?, ?, ?, ?)
            """,
                (track_id, credit.name, credit.role.value, credit.role_detail, credit.source),
            )
        except Exception as e:
            # Log mais ne pas arrêter le processus pour un crédit
            logger.debug(f"Erreur lors de la sauvegarde du crédit {credit.name}: {e}")

    def get_artist_tracks(self, artist_id: int) -> list[Track]:
        """Récupère tous les morceaux d'un artiste - VERSION SANS YOUTUBE_URL"""
        tracks = []

        try:
            logger.info(f"🔍 Chargement des tracks pour artist_id: {artist_id}")

            with self._get_connection() as conn:
                cursor = conn.cursor()

                # ✅ ÉTAPE 1: Récupérer d'abord les infos de l'artiste
                cursor.execute(
                    "SELECT id, name, genius_id, spotify_id, discogs_id FROM artists WHERE id = ?",
                    (artist_id,),
                )
                artist_row = cursor.fetchone()

                if not artist_row:
                    logger.error(f"❌ Artiste avec ID {artist_id} non trouvé")
                    return tracks

                # ✅ ÉTAPE 2: Créer l'objet Artist
                from src.models import Artist

                artist = Artist(
                    id=artist_row["id"],
                    name=artist_row["name"],
                    genius_id=artist_row["genius_id"],
                    spotify_id=artist_row["spotify_id"],
                    discogs_id=artist_row["discogs_id"],
                )

                # Vérifier le nombre total
                cursor.execute("SELECT COUNT(*) FROM tracks WHERE artist_id = ?", (artist_id,))
                total_count = cursor.fetchone()[0]
                logger.info(f"📊 {total_count} tracks trouvés en base")

                if total_count == 0:
                    return tracks

                # Accès par nom de colonne (sqlite3.Row) : l'ordre des colonnes
                # ne compte plus, le schéma est garanti par Database.init_schema.
                cursor.execute(
                    "SELECT * FROM tracks WHERE artist_id = ? ORDER BY title",
                    (artist_id,),
                )

                rows = cursor.fetchall()
                logger.info(f"📦 {len(rows)} lignes récupérées")

                # Création des objets Track via le mapper (coercitions centralisées)
                for i, row in enumerate(rows):
                    try:
                        track = track_from_row(row, artist)
                        if track is None:
                            continue

                        # Chargement crédits (a besoin du curseur → hors mapper)
                        try:
                            track.credits = self._get_track_credits(cursor, row["id"])
                        except Exception:
                            track.credits = []

                        tracks.append(track)

                        if i < 5:
                            logger.info(f"✅ Track {i+1}: {track.title}")

                    except Exception as track_error:
                        logger.error(f"❌ Erreur track {i}: {track_error}")
                        continue

                # Compter les tracks avec musical_key
                tracks_with_key = sum(
                    1 for t in tracks if hasattr(t, "musical_key") and t.musical_key
                )
                logger.info(
                    f"✅ {len(tracks)} tracks chargés avec succès ({tracks_with_key} avec musical_key)"
                )

        except Exception as e:
            logger.error(f"❌ Erreur dans get_artist_tracks: {e}")

        return tracks

    def _get_track_credits(self, cursor, track_id: int) -> list[Credit]:
        """Récupère les crédits d'un morceau - VERSION ROBUSTE"""
        credits = []

        try:
            cursor.execute("SELECT * FROM credits WHERE track_id = ?", (track_id,))
            credit_rows = cursor.fetchall()

            for row in credit_rows:
                try:
                    name = row["name"]
                    role_str = row["role"]
                    role_detail = row["role_detail"]
                    source = row["source"] or "genius"

                    if name and role_str:
                        from src.models import Credit, CreditRole

                        # Conversion du rôle string vers enum
                        try:
                            role = CreditRole(role_str)
                        except ValueError:
                            role = CreditRole.OTHER

                        credit = Credit(
                            name=str(name),
                            role=role,
                            role_detail=role_detail,
                            source=str(source),
                        )
                        credits.append(credit)

                except Exception as credit_error:
                    logger.debug(f"Erreur crédit: {credit_error}")
                    continue

        except Exception as e:
            logger.debug(f"Erreur _get_track_credits: {e}")

        return credits

    def delete_track(self, track_id: int) -> bool:
        """Supprime définitivement un morceau et ses données associées"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM credits WHERE track_id = ?", (track_id,))
                cursor.execute("DELETE FROM scraping_errors WHERE track_id = ?", (track_id,))
                cursor.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
                deleted = cursor.rowcount
                conn.commit()
                logger.info(f"🗑️ Track {track_id} supprimé ({deleted} ligne(s))")
                return deleted > 0
        except Exception as e:
            logger.error(f"Erreur suppression track {track_id}: {e}")
            return False

    def merge_tracks(self, keep_id: int, delete_id: int) -> bool:
        """Fusionne delete_id dans keep_id : transfère les crédits (en écartant
        ceux déjà présents à l'identique sur le morceau conservé) et les erreurs
        de scraping, puis supprime la ligne en doublon. Même mécanique que
        scripts/merge_duplicates.py + dédup. Le BACKUP est à faire par l'appelant
        AVANT (règle projet : backup avant toute opération destructive)."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                # Crédits : ne transférer que ceux absents du morceau conservé
                cursor.execute(
                    """
                    DELETE FROM credits WHERE track_id = ? AND EXISTS (
                        SELECT 1 FROM credits k WHERE k.track_id = ?
                          AND k.name = credits.name AND k.role = credits.role
                          AND IFNULL(k.role_detail, '') = IFNULL(credits.role_detail, '')
                    )""",
                    (delete_id, keep_id),
                )
                cursor.execute(
                    "UPDATE credits SET track_id = ? WHERE track_id = ?", (keep_id, delete_id)
                )
                transferred = cursor.rowcount
                cursor.execute(
                    "UPDATE scraping_errors SET track_id = ? WHERE track_id = ?",
                    (keep_id, delete_id),
                )
                cursor.execute("DELETE FROM tracks WHERE id = ?", (delete_id,))
                conn.commit()
                logger.info(
                    f"🔀 Track {delete_id} fusionné dans {keep_id} "
                    f"({transferred} crédit(s) transféré(s))"
                )
                return True
        except Exception as e:
            logger.error(f"Erreur fusion track {delete_id} → {keep_id}: {e}")
            return False

    def force_update_track_credits(self, track: Track) -> int:
        """Force la mise à jour complète des crédits d'un morceau - VERSION PRÉSERVANT FEATURES"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # ✅ CORRECTION: Récupérer les infos featuring AVANT suppression
                cursor.execute(
                    """
                    SELECT is_featuring, primary_artist_name, featured_artists
                    FROM tracks WHERE id = ?
                """,
                    (track.id,),
                )

                featuring_info = cursor.fetchone()

                if featuring_info:
                    # Préserver les infos featuring sur l'objet track
                    track.is_featuring = bool(featuring_info["is_featuring"])
                    track.primary_artist_name = featuring_info["primary_artist_name"]
                    track.featured_artists = featuring_info["featured_artists"]
                    logger.info(f"🔒 Infos featuring préservées pour {track.title}")
                else:
                    track.is_featuring = False

                # Supprimer TOUS les anciens crédits
                cursor.execute("DELETE FROM credits WHERE track_id = ?", (track.id,))
                deleted_count = cursor.rowcount
                logger.info(f"🗑️ {deleted_count} anciens crédits supprimés pour '{track.title}'")

                # Supprimer les anciennes erreurs de scraping
                cursor.execute("DELETE FROM scraping_errors WHERE track_id = ?", (track.id,))
                deleted_errors = cursor.rowcount
                if deleted_errors > 0:
                    logger.info(f"🗑️ {deleted_errors} anciennes erreurs supprimées")

                # Remettre à zéro les métadonnées de scraping (MAIS PRÉSERVER FEATURING)
                cursor.execute(
                    """
                    UPDATE tracks
                    SET last_scraped = NULL,
                        genre = CASE
                            WHEN genre IS NOT NULL AND genre != '' THEN genre
                            ELSE NULL
                        END
                    WHERE id = ?
                """,
                    (track.id,),
                )

                # Sauvegarder les nouveaux crédits
                for credit in track.credits:
                    self._save_credit(cursor, track.id, credit)

                # Mettre à jour le track complet (EN PRÉSERVANT LES FEATURES)
                cursor.execute(
                    """
                    UPDATE tracks
                    SET album = ?, track_number = ?, release_date = ?,
                        genius_id = ?, spotify_id = ?, discogs_id = ?,
                        bpm = ?, duration = ?, genre = ?,
                        genius_url = ?, spotify_url = ?,
                        is_featuring = ?, primary_artist_name = ?, featured_artists = ?,
                        updated_at = ?, last_scraped = ?
                    WHERE id = ?
                """,
                    (
                        track.album,
                        getattr(track, "track_number", None),
                        track.release_date,
                        track.genius_id,
                        track.spotify_id,
                        track.discogs_id,
                        track.bpm,
                        track.duration,
                        track.genre,
                        track.genius_url,
                        track.spotify_url,
                        getattr(track, "is_featuring", False),
                        getattr(track, "primary_artist_name", None),
                        getattr(track, "featured_artists", None),
                        datetime.now(),
                        track.last_scraped,
                        track.id,
                    ),
                )

                # Sauvegarder les nouvelles erreurs s'il y en a
                for error in track.scraping_errors:
                    cursor.execute(
                        """
                        INSERT INTO scraping_errors (track_id, error_message, error_time)
                        VALUES (?, ?, ?)
                    """,
                        (track.id, error, datetime.now()),
                    )

                conn.commit()

                new_credits_count = len(track.credits)
                logger.info(
                    f"✅ Mise à jour forcée terminée pour '{track.title}': {new_credits_count} nouveaux crédits (Featuring préservé: {getattr(track, 'is_featuring', False)})"
                )

                return new_credits_count

        except Exception as e:
            logger.error(f"❌ Erreur lors de la mise à jour forcée: {e}")
            return 0

    # ──────────────────────────────────────────────────────────────────────────
    # Kworb — streams Spotify
    # ──────────────────────────────────────────────────────────────────────────

    def update_track_spotify_streams(
        self, track_id: int, streams: int, daily_streams: int, updated_at=None
    ) -> bool:
        """Met à jour les streams Kworb d'un morceau.

        updated_at : date "Last updated" de la page Kworb (fraîcheur réelle),
        sinon now().
        """
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    UPDATE tracks
                    SET spotify_streams = ?, spotify_daily_streams = ?, spotify_streams_updated = ?
                    WHERE id = ?
                """,
                    (streams, daily_streams, updated_at or datetime.now(), track_id),
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Erreur update_track_spotify_streams (track_id={track_id}): {e}")
            return False

    def update_track_spotify_id(self, track_id: int, spotify_id: str) -> bool:
        """Backfill du Spotify Track ID (ex: depuis les liens des pages Kworb).
        Ne remplace jamais un ID existant."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    UPDATE tracks SET spotify_id = ?
                    WHERE id = ? AND (spotify_id IS NULL OR spotify_id = '')
                """,
                    (spotify_id, track_id),
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Erreur update_track_spotify_id (track_id={track_id}): {e}")
            return False

    def clear_track_album(self, track_id: int) -> bool:
        """Détache un morceau de son album (édition MANUELLE : album_override=1
        empêche l'API de re-remplir le champ au prochain prefill)."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    UPDATE tracks SET album = NULL, album_override = 1, updated_at = ?
                    WHERE id = ?
                """,
                    (datetime.now(), track_id),
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Erreur clear_track_album (track_id={track_id}): {e}")
            return False

    def upsert_album(
        self,
        artist_id: int,
        title: str,
        streams: int,
        daily_streams: int,
        spotify_album_ids: str = None,
        updated_at=None,
    ) -> bool:
        """Insère ou met à jour un album avec ses données Kworb.

        spotify_album_ids : IDs Spotify des éditions agrégées, séparés par des
        virgules (un même titre peut couvrir plusieurs éditions — streams sommés
        par l'appelant).
        """
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO albums (title, artist_id, spotify_streams, spotify_daily_streams,
                                        spotify_streams_updated, spotify_album_ids)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(title, artist_id) DO UPDATE SET
                        spotify_streams = excluded.spotify_streams,
                        spotify_daily_streams = excluded.spotify_daily_streams,
                        spotify_streams_updated = excluded.spotify_streams_updated,
                        spotify_album_ids = COALESCE(excluded.spotify_album_ids, spotify_album_ids)
                """,
                    (
                        title,
                        artist_id,
                        streams,
                        daily_streams,
                        updated_at or datetime.now(),
                        spotify_album_ids,
                    ),
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Erreur upsert_album (artist_id={artist_id}, title={title!r}): {e}")
            return False

    def get_albums_for_artist(self, artist_id: int) -> list[dict[str, Any]]:
        """Retourne les albums d'un artiste triés par streams décroissants."""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    """
                    SELECT title, spotify_streams, spotify_daily_streams,
                           spotify_streams_updated, ytm_streams
                    FROM albums WHERE artist_id = ?
                    ORDER BY spotify_streams DESC
                """,
                    (artist_id,),
                )
                rows = cursor.fetchall()
                return [
                    {
                        "title": row["title"],
                        "spotify_streams": row["spotify_streams"],
                        "spotify_daily_streams": row["spotify_daily_streams"],
                        "spotify_streams_updated": row["spotify_streams_updated"],
                        "ytm_streams": row["ytm_streams"],
                    }
                    for row in rows
                ]
        except Exception as e:
            logger.error(f"Erreur get_albums_for_artist (artist_id={artist_id}): {e}")
            return []

    def update_track_ytm_streams(self, track_id: int, streams: int) -> bool:
        """Met à jour les streams YouTube Music d'un morceau."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    UPDATE tracks
                    SET ytm_streams = ?, ytm_streams_updated = ?
                    WHERE id = ?
                """,
                    (streams, datetime.now(), track_id),
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Erreur update_track_ytm_streams (track_id={track_id}): {e}")
            return False

    def update_track_youtube_url(self, track_id: int, url: str, source: str) -> bool:
        """Persiste le lien YouTube d'un morceau + sa provenance.

        Priorité des sources : 'manual' (choix utilisateur) ≥ 'genius_media' >
        'search_auto'. Un lien 'manual' ou 'genius_media' écrase n'importe quoi ;
        un 'search_auto' ne remplace JAMAIS un 'genius_media' ni un 'manual'.
        """
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    UPDATE tracks
                    SET youtube_url = ?, youtube_url_source = ?, updated_at = ?
                    WHERE id = ?
                      AND (? IN ('manual', 'genius_media')
                           OR youtube_url IS NULL
                           OR youtube_url = ''
                           OR COALESCE(youtube_url_source, '') NOT IN ('manual', 'genius_media'))
                """,
                    (url, source, datetime.now(), track_id, source),
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Erreur update_track_youtube_url (track_id={track_id}): {e}")
            return False

    def rename_track(self, track_id: int, new_title: str) -> bool:
        """Renomme un morceau en base (ex. « Matrix (Intro) » → « Matrix » pour
        aligner sur Kworb). Échoue si le titre existe déjà pour l'artiste
        (contrainte UNIQUE(title, artist_id))."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    UPDATE tracks SET title = ?, updated_at = ? WHERE id = ?
                """,
                    (new_title.strip(), datetime.now(), track_id),
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Erreur rename_track (track_id={track_id}): {e}")
            return False

    def clear_track_youtube_link(self, track_id: int) -> bool:
        """Efface le lien YouTube et sa provenance (repasse en recherche live)."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    UPDATE tracks
                    SET youtube_url = NULL, youtube_url_source = NULL, updated_at = ?
                    WHERE id = ?
                """,
                    (datetime.now(), track_id),
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Erreur clear_track_youtube_link (track_id={track_id}): {e}")
            return False

    def update_album_ytm_streams(self, artist_id: int, title: str, streams: int) -> bool:
        """Met à jour les streams YouTube Music d'un album."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    UPDATE albums SET ytm_streams = ?, ytm_streams_updated = ?
                    WHERE title = ? AND artist_id = ?
                """,
                    (streams, datetime.now(), title, artist_id),
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(
                f"Erreur update_album_ytm_streams (artist_id={artist_id}, title={title!r}): {e}"
            )
            return False
