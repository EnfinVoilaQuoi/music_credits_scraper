"""Repository des morceaux, albums et crédits.

Toute la persistance liée aux `tracks` (save/get/delete/merge), aux crédits et
aux albums (streams Kworb/YTM). Utilisé comme base de `DataManager`, qui fournit
`self.engine` (moteur SQLAlchemy Core, délégué à `Database`). Comportement
constant vs l'ancien `data_manager.py` (refonte 1.5 puis bascule Core phase E2).
"""

import json
from datetime import datetime
from typing import Any

from sqlalchemy import func, literal, or_, select, text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.enrichment.observation import Observation
from src.models import Credit, Track
from src.persistence.binding import date_bind
from src.persistence.schema import albums, artists, credits, tracks
from src.utils.logger import get_logger
from src.utils.track_mapper import track_from_row

logger = get_logger(__name__)


class TrackRepository:
    """Persistance des morceaux, crédits et albums. Requiert `self.engine`."""

    def save_track(self, track: Track) -> int:
        """Sauvegarde ou met à jour un morceau avec musical_key et time_signature"""
        # Écriture en `text()` via `engine.begin()` (transaction Core, commit/
        # rollback auto). Le gros UPDATE COALESCE/CASE et l'INSERT gardent leur SQL
        # verbatim (déjà en paramètres nommés depuis A3) : les binds `text()` ne
        # sont PAS typés → les strings de date (release_date, last_scraped…)
        # passent verbatim au driver, ce qui CONTOURNE le piège d'écriture
        # TIMESTAMP (qui refuse les strings sur un bind typé, cf. date_bind).
        with self.engine.begin() as conn:
            if not track.artist or not track.artist.id:
                raise ValueError("Le morceau doit avoir un artiste avec un ID")

            existing_track = (
                conn.execute(
                    text(
                        "SELECT id, is_featuring, primary_artist_name, featured_artists, "
                        "lyrics, has_lyrics, lyrics_scraped_at FROM tracks "
                        "WHERE title = :title AND artist_id = :artist_id"
                    ),
                    {"title": track.title, "artist_id": track.artist.id},
                )
                .mappings()
                .first()
            )

            if existing_track:
                track.id = existing_track["id"]
                # NB : plus de « préservation » ici. Les anciens blocs gardés par
                # `not hasattr(track, "is_featuring"/"lyrics")` étaient morts (champs
                # de la dataclass → hasattr toujours vrai) et, de toute façon,
                # redondants : les paroles sont préservées par le COALESCE de
                # l'UPDATE ci-dessous, et is_featuring suit la décision documentée
                # « le track en mémoire fait foi » (écrasé SANS COALESCE). La fusion
                # en mémoire des données enrichies se fait en amont côté worker
                # (gui/workers/retrieval.py).

            # Sérialiser les champs JSON une seule fois (partagés UPDATE/INSERT)
            certifications_json = json.dumps(track.certifications)
            album_certifications_json = json.dumps(track.album_certifications)
            relationships_json = json.dumps(track.relationships or [])

            # Paramètres NOMMÉS : un seul dict {colonne: valeur}, lié par nom
            # (:col). L'ordre des ~44 valeurs ne peut plus se désynchroniser du
            # SQL (cause de bugs positionnels). Le même dict sert à l'UPDATE et
            # à l'INSERT ; sqlite3 ignore les clés non référencées.
            # NB : SEULS key/mode/spotify_page_title ne sont pas des champs de la
            # dataclass Track (posés dynamiquement par le mapper) → getattr requis.
            # Les autres colonnes sont des champs garantis → accès direct.
            params = {
                "title": track.title,
                "artist_id": track.artist.id,
                "album": track.album,
                "track_number": track.track_number,
                "release_date": track.release_date,
                "genius_id": track.genius_id,
                "spotify_id": track.spotify_id,
                "discogs_id": track.discogs_id,
                "isrc": track.isrc,
                "bpm": track.bpm,
                "bpm_source": track.bpm_source,
                "bpm_confidence": track.bpm_confidence,
                "key_mode_source": track.key_mode_source,
                "reccobeats_resolution": track.reccobeats_resolution,
                "bpm_alt": track.bpm_alt,
                "duration": track.duration,
                "genre": track.genre,
                "key": getattr(track, "key", None),
                "mode": getattr(track, "mode", None),
                "musical_key": track.musical_key,
                "time_signature": track.time_signature,
                "genius_url": track.genius_url,
                "spotify_url": track.spotify_url,
                "youtube_url": track.youtube_url,
                "youtube_url_source": track.youtube_url_source,
                "is_featuring": track.is_featuring,
                "primary_artist_name": track.primary_artist_name,
                "featured_artists": track.featured_artists,
                "secondary_role": track.secondary_role,
                "lyrics": track.lyrics,
                "lyrics_scraped_at": track.lyrics_scraped_at,
                "lyrics_source": track.lyrics_source,
                "lyrics_synced": track.lyrics_synced,
                "lyrics_synced_source": track.lyrics_synced_source,
                "lyrics_synced_confidence": track.lyrics_synced_confidence,
                "has_lyrics": bool(track.lyrics),  # INSERT uniquement
                "anecdotes": track.anecdotes,
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
                conn.execute(
                    text("""
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
                """),
                    params,
                )
            else:
                result = conn.execute(
                    text("""
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
                """),
                    params,
                )
                track.id = result.lastrowid

            # Supprimer les anciens crédits avant d'ajouter les nouveaux
            if track.id:
                conn.execute(
                    text("DELETE FROM credits WHERE track_id = :track_id"),
                    {"track_id": track.id},
                )

                # Sauvegarder les nouveaux crédits
                for credit in track.credits:
                    self._save_credit(conn, track.id, credit)

            # Sauvegarder les erreurs
            for error in track.scraping_errors:
                conn.execute(
                    text(
                        "INSERT INTO scraping_errors (track_id, error_message, error_time) "
                        "VALUES (:track_id, :error_message, :error_time)"
                    ),
                    {"track_id": track.id, "error_message": error, "error_time": datetime.now()},
                )

            # Observations fraîches du run (phase E5) : upsert DANS la même
            # transaction que les colonnes legacy — la moitié « persistance » de
            # la triple écriture (E5c-1). Write-through pur : ne pilote PAS encore
            # les colonnes legacy (bascule reconcile → E5c-2, vote audio). No-op
            # tant qu'aucun provider `fetch()` ne peuple `track.observations`.
            if track.id and track.observations:
                self._upsert_observations(conn, track.id, track.observations)

            # commit auto à la sortie du bloc `engine.begin()`
            logger.info(
                f"Morceau sauvegardé: {track.title} (ID: {track.id}, "
                f"Featuring: {track.is_featuring}, Paroles: {bool(track.lyrics)})"
            )
            return track.id

    def _save_credit(self, conn, track_id: int, credit: Credit):
        """Sauvegarde un crédit (connexion Core fournie par l'appelant)."""
        try:
            conn.execute(
                text(
                    "INSERT INTO credits (track_id, name, role, role_detail, source) "
                    "VALUES (:track_id, :name, :role, :role_detail, :source)"
                ),
                {
                    "track_id": track_id,
                    "name": credit.name,
                    "role": credit.role.value,
                    "role_detail": credit.role_detail,
                    "source": credit.source,
                },
            )
        except Exception as e:
            # Log mais ne pas arrêter le processus pour un crédit
            logger.debug(f"Erreur lors de la sauvegarde du crédit {credit.name}: {e}")

    def get_artist_tracks(self, artist_id: int) -> list[Track]:
        """Récupère tous les morceaux d'un artiste (via le moteur Core)."""
        result: list[Track] = []

        try:
            logger.info(f"🔍 Chargement des tracks pour artist_id: {artist_id}")

            with self.engine.connect() as conn:
                # ✅ ÉTAPE 1: Récupérer d'abord les infos de l'artiste
                artist_row = (
                    conn.execute(
                        select(
                            artists.c.id,
                            artists.c.name,
                            artists.c.genius_id,
                            artists.c.spotify_id,
                            artists.c.discogs_id,
                        ).where(artists.c.id == artist_id)
                    )
                    .mappings()
                    .first()
                )

                if not artist_row:
                    logger.error(f"❌ Artiste avec ID {artist_id} non trouvé")
                    return result

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
                total_count = conn.execute(
                    select(func.count()).select_from(tracks).where(tracks.c.artist_id == artist_id)
                ).scalar()
                logger.info(f"📊 {total_count} tracks trouvés en base")

                if total_count == 0:
                    return result

                # Lecture en `text()` brut (et NON `select(tracks)`) : le type
                # TIMESTAMP de schema.py applique un result-processor qui PARSE
                # les colonnes date (release_date, *_updated, created_at…) en
                # datetime, alors que le legacy sqlite3 — et le mapper, qui fait
                # ses propres coercitions depuis des strings — les veulent
                # VERBATIM. `text()` ne type pas ses colonnes → valeurs brutes,
                # comportement identique. Symétrique du piège d'écriture
                # `date_bind` (cf. REFONTE.md, piège TIMESTAMP E2). `.mappings()`
                # donne un accès par nom, indexable comme sqlite3.Row.
                rows = (
                    conn.execute(
                        text("SELECT * FROM tracks WHERE artist_id = :aid ORDER BY title"),
                        {"aid": artist_id},
                    )
                    .mappings()
                    .all()
                )
                logger.info(f"📦 {len(rows)} lignes récupérées")

                # E6 : observations de TOUT l'artiste en 1 requête (pas par track),
                # groupées par track_id → passées au mapper qui les réconcilie.
                observations_by_track = self._observations_by_artist(conn, artist_id)

                # Création des objets Track via le mapper (coercitions centralisées ;
                # `row` est une RowMapping, indexable par nom comme sqlite3.Row).
                for i, row in enumerate(rows):
                    try:
                        track = track_from_row(
                            row, artist, observations_by_track.get(row["id"], [])
                        )
                        if track is None:
                            continue

                        # Chargement crédits (a besoin de la connexion → hors mapper)
                        try:
                            track.credits = self._get_track_credits(conn, row["id"])
                        except Exception:
                            track.credits = []

                        result.append(track)

                        if i < 5:
                            logger.info(f"✅ Track {i+1}: {track.title}")

                    except Exception as track_error:
                        logger.error(f"❌ Erreur track {i}: {track_error}")
                        continue

                # Compter les tracks avec musical_key
                tracks_with_key = sum(1 for t in result if t.musical_key)
                logger.info(
                    f"✅ {len(result)} tracks chargés avec succès ({tracks_with_key} avec musical_key)"
                )

        except Exception as e:
            logger.error(f"❌ Erreur dans get_artist_tracks: {e}")

        return result

    def _get_track_credits(self, conn, track_id: int) -> list[Credit]:
        """Récupère les crédits d'un morceau (connexion Core fournie par l'appelant)."""
        # Pas d'annotation sur `result` : `Credit` est ré-importé localement dans
        # la boucle (avec `CreditRole`), donc traité comme variable locale — une
        # annotation `list[Credit]` ici déclencherait F823 (réf. avant assignation).
        result = []

        try:
            credit_rows = (
                conn.execute(select(credits).where(credits.c.track_id == track_id)).mappings().all()
            )

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
                        result.append(credit)

                except Exception as credit_error:
                    logger.debug(f"Erreur crédit: {credit_error}")
                    continue

        except Exception as e:
            logger.debug(f"Erreur _get_track_credits: {e}")

        return result

    # ──────────────────────────────────────────────────────────────────────
    # Observations (phase E5) — provenance scalaire par (track, field, source).
    # Modèle UPSERT (clé unique (track_id, field, source)) : dernière valeur par
    # source, `seen_at` = dernière vue. Écriture/lecture en `text()` brut : la
    # colonne `seen_at` (TIMESTAMP) est stockée/relue VERBATIM comme partout dans
    # le legacy (piège TIMESTAMP double-face, cf. E2) ; `value` stocké TEXT
    # (coercition au retour = mapper, E6). Les deux méthodes acceptent une
    # connexion existante (`conn=`) pour composer la triple écriture E5c dans UNE
    # seule transaction `engine.begin()` (l'invariant du contrôle E4 doit
    # survivre à un crash : observations + colonnes legacy tombent ensemble).
    # ──────────────────────────────────────────────────────────────────────

    def _observations_by_artist(self, conn, artist_id: int) -> dict[int, list[Observation]]:
        """Observations de tous les morceaux d'un artiste, groupées par track_id
        (1 requête, pour la bascule lecture E6). `value`/`seen_at` en brut."""
        rows = (
            conn.execute(
                text(
                    "SELECT o.track_id, o.field, o.value, o.source, o.confidence, o.seen_at "
                    "FROM observations o JOIN tracks t ON t.id = o.track_id "
                    "WHERE t.artist_id = :aid"
                ),
                {"aid": artist_id},
            )
            .mappings()
            .all()
        )
        by_track: dict[int, list[Observation]] = {}
        for r in rows:
            by_track.setdefault(r["track_id"], []).append(
                Observation(
                    field=r["field"],
                    value=r["value"],
                    source=r["source"],
                    confidence=r["confidence"],
                    seen_at=r["seen_at"],
                )
            )
        return by_track

    def get_observations(self, track_id: int, *, conn=None) -> list[Observation]:
        """Observations persistées d'un morceau (`value`/`seen_at` en brut, non coercés)."""
        if conn is not None:
            return self._get_observations(conn, track_id)
        with self.engine.connect() as conn:
            return self._get_observations(conn, track_id)

    def _get_observations(self, conn, track_id: int) -> list[Observation]:
        rows = (
            conn.execute(
                text(
                    "SELECT field, value, source, confidence, seen_at "
                    "FROM observations WHERE track_id = :tid"
                ),
                {"tid": track_id},
            )
            .mappings()
            .all()
        )
        return [
            Observation(
                field=r["field"],
                value=r["value"],
                source=r["source"],
                confidence=r["confidence"],
                seen_at=r["seen_at"],
            )
            for r in rows
        ]

    def upsert_observations(self, track_id: int, observations, *, conn=None) -> None:
        """Upsert les observations d'un morceau (clé (field, source)). No-op si vide."""
        if not observations:
            return
        if conn is not None:
            self._upsert_observations(conn, track_id, observations)
        else:
            with self.engine.begin() as conn:
                self._upsert_observations(conn, track_id, observations)

    def delete_observations(self, track_id: int, field: str, *, conn=None) -> None:
        """Supprime toutes les observations d'un champ pour un morceau (composable).

        Sert au « force » d'un scrape (E7d, `force_sync`) : repartir de zéro sur
        `lyrics_synced` — sinon une source disparue laisserait une observation
        stale qui ressusciterait le verdict à la lecture (le mapper réconcilie
        l'union persistée). Symétrique de `upsert_observations`."""
        if conn is not None:
            self._delete_observations(conn, track_id, field)
        else:
            with self.engine.begin() as conn:
                self._delete_observations(conn, track_id, field)

    def _delete_observations(self, conn, track_id: int, field: str) -> None:
        conn.execute(
            text("DELETE FROM observations WHERE track_id = :tid AND field = :field"),
            {"tid": track_id, "field": field},
        )

    def _upsert_observations(self, conn, track_id: int, observations) -> None:
        for obs in observations:
            # `seen_at` verbatim (string) comme le backfill E4 et le stockage
            # legacy ; datetime → format legacy, absent → maintenant.
            seen_at = obs.seen_at or datetime.now()
            if isinstance(seen_at, datetime):
                seen_at = seen_at.strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                text(
                    "INSERT INTO observations "
                    "(track_id, field, value, source, confidence, seen_at) "
                    "VALUES (:tid, :field, :value, :source, :confidence, :seen_at) "
                    "ON CONFLICT(track_id, field, source) DO UPDATE SET "
                    "value = excluded.value, confidence = excluded.confidence, "
                    "seen_at = excluded.seen_at"
                ),
                {
                    "tid": track_id,
                    "field": obs.field,
                    "value": None if obs.value is None else str(obs.value),
                    "source": obs.source,
                    "confidence": None if obs.confidence is None else float(obs.confidence),
                    "seen_at": seen_at,
                },
            )

    def delete_track(self, track_id: int) -> bool:
        """Supprime définitivement un morceau et ses données associées"""
        try:
            with self.engine.begin() as conn:
                conn.execute(text("DELETE FROM credits WHERE track_id = :tid"), {"tid": track_id})
                conn.execute(
                    text("DELETE FROM scraping_errors WHERE track_id = :tid"), {"tid": track_id}
                )
                # Observations : pas de cascade FK (PRAGMA foreign_keys jamais
                # activé) → suppression explicite (E4).
                conn.execute(
                    text("DELETE FROM observations WHERE track_id = :tid"), {"tid": track_id}
                )
                deleted = conn.execute(
                    text("DELETE FROM tracks WHERE id = :tid"), {"tid": track_id}
                ).rowcount
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
            with self.engine.begin() as conn:
                # Crédits : ne transférer que ceux absents du morceau conservé
                conn.execute(
                    text("""
                    DELETE FROM credits WHERE track_id = :delete_id AND EXISTS (
                        SELECT 1 FROM credits k WHERE k.track_id = :keep_id
                          AND k.name = credits.name AND k.role = credits.role
                          AND IFNULL(k.role_detail, '') = IFNULL(credits.role_detail, '')
                    )"""),
                    {"delete_id": delete_id, "keep_id": keep_id},
                )
                transferred = conn.execute(
                    text("UPDATE credits SET track_id = :keep_id WHERE track_id = :delete_id"),
                    {"keep_id": keep_id, "delete_id": delete_id},
                ).rowcount
                conn.execute(
                    text(
                        "UPDATE scraping_errors SET track_id = :keep_id WHERE track_id = :delete_id"
                    ),
                    {"keep_id": keep_id, "delete_id": delete_id},
                )
                # Observations : dédup par la clé unique (field, source) — le keep
                # gagne (on écarte celles du doublon déjà couvertes) — puis
                # réaffectation du reste. Pas de cascade FK (E4).
                conn.execute(
                    text("""
                    DELETE FROM observations WHERE track_id = :delete_id AND EXISTS (
                        SELECT 1 FROM observations k WHERE k.track_id = :keep_id
                          AND k.field = observations.field AND k.source = observations.source
                    )"""),
                    {"delete_id": delete_id, "keep_id": keep_id},
                )
                conn.execute(
                    text("UPDATE observations SET track_id = :keep_id WHERE track_id = :delete_id"),
                    {"keep_id": keep_id, "delete_id": delete_id},
                )
                conn.execute(
                    text("DELETE FROM tracks WHERE id = :delete_id"), {"delete_id": delete_id}
                )
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
            with self.engine.begin() as conn:
                # ✅ CORRECTION: Récupérer les infos featuring AVANT suppression
                featuring_info = (
                    conn.execute(
                        text(
                            "SELECT is_featuring, primary_artist_name, featured_artists "
                            "FROM tracks WHERE id = :tid"
                        ),
                        {"tid": track.id},
                    )
                    .mappings()
                    .first()
                )

                if featuring_info:
                    # Préserver les infos featuring sur l'objet track
                    track.is_featuring = bool(featuring_info["is_featuring"])
                    track.primary_artist_name = featuring_info["primary_artist_name"]
                    track.featured_artists = featuring_info["featured_artists"]
                    logger.info(f"🔒 Infos featuring préservées pour {track.title}")
                else:
                    track.is_featuring = False

                # Supprimer TOUS les anciens crédits
                deleted_count = conn.execute(
                    text("DELETE FROM credits WHERE track_id = :tid"), {"tid": track.id}
                ).rowcount
                logger.info(f"🗑️ {deleted_count} anciens crédits supprimés pour '{track.title}'")

                # Supprimer les anciennes erreurs de scraping
                deleted_errors = conn.execute(
                    text("DELETE FROM scraping_errors WHERE track_id = :tid"), {"tid": track.id}
                ).rowcount
                if deleted_errors > 0:
                    logger.info(f"🗑️ {deleted_errors} anciennes erreurs supprimées")

                # Remettre à zéro les métadonnées de scraping (MAIS PRÉSERVER FEATURING)
                conn.execute(
                    text("""
                    UPDATE tracks
                    SET last_scraped = NULL,
                        genre = CASE
                            WHEN genre IS NOT NULL AND genre != '' THEN genre
                            ELSE NULL
                        END
                    WHERE id = :tid
                """),
                    {"tid": track.id},
                )

                # Sauvegarder les nouveaux crédits
                for credit in track.credits:
                    self._save_credit(conn, track.id, credit)

                # Mettre à jour le track complet (EN PRÉSERVANT LES FEATURES)
                conn.execute(
                    text("""
                    UPDATE tracks
                    SET album = :album, track_number = :track_number, release_date = :release_date,
                        genius_id = :genius_id, spotify_id = :spotify_id, discogs_id = :discogs_id,
                        bpm = :bpm, duration = :duration, genre = :genre,
                        genius_url = :genius_url, spotify_url = :spotify_url,
                        is_featuring = :is_featuring, primary_artist_name = :primary_artist_name,
                        featured_artists = :featured_artists,
                        updated_at = :updated_at, last_scraped = :last_scraped
                    WHERE id = :id
                """),
                    {
                        "album": track.album,
                        "track_number": track.track_number,
                        "release_date": track.release_date,
                        "genius_id": track.genius_id,
                        "spotify_id": track.spotify_id,
                        "discogs_id": track.discogs_id,
                        "bpm": track.bpm,
                        "duration": track.duration,
                        "genre": track.genre,
                        "genius_url": track.genius_url,
                        "spotify_url": track.spotify_url,
                        "is_featuring": track.is_featuring,
                        "primary_artist_name": track.primary_artist_name,
                        "featured_artists": track.featured_artists,
                        "updated_at": datetime.now(),
                        "last_scraped": track.last_scraped,
                        "id": track.id,
                    },
                )

                # Sauvegarder les nouvelles erreurs s'il y en a
                for error in track.scraping_errors:
                    conn.execute(
                        text(
                            "INSERT INTO scraping_errors (track_id, error_message, error_time) "
                            "VALUES (:track_id, :error_message, :error_time)"
                        ),
                        {
                            "track_id": track.id,
                            "error_message": error,
                            "error_time": datetime.now(),
                        },
                    )

                # commit auto à la sortie du bloc `engine.begin()`
                new_credits_count = len(track.credits)
                logger.info(
                    f"✅ Mise à jour forcée terminée pour '{track.title}': {new_credits_count} nouveaux crédits (Featuring préservé: {track.is_featuring})"
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
            stmt = (
                update(tracks)
                .where(tracks.c.id == track_id)
                .values(
                    spotify_streams=streams,
                    spotify_daily_streams=daily_streams,
                    spotify_streams_updated=date_bind(updated_at or datetime.now()),
                )
            )
            with self.engine.begin() as conn:
                conn.execute(stmt)
            return True
        except Exception as e:
            logger.error(f"Erreur update_track_spotify_streams (track_id={track_id}): {e}")
            return False

    def update_track_spotify_id(self, track_id: int, spotify_id: str) -> bool:
        """Backfill du Spotify Track ID (ex: depuis les liens des pages Kworb).
        Ne remplace jamais un ID existant."""
        try:
            stmt = (
                update(tracks)
                .where(
                    tracks.c.id == track_id,
                    (tracks.c.spotify_id.is_(None)) | (tracks.c.spotify_id == ""),
                )
                .values(spotify_id=spotify_id)
            )
            with self.engine.begin() as conn:
                conn.execute(stmt)
            return True
        except Exception as e:
            logger.error(f"Erreur update_track_spotify_id (track_id={track_id}): {e}")
            return False

    def clear_track_album(self, track_id: int) -> bool:
        """Détache un morceau de son album (édition MANUELLE : album_override=1
        empêche l'API de re-remplir le champ au prochain prefill)."""
        try:
            stmt = (
                update(tracks)
                .where(tracks.c.id == track_id)
                .values(album=None, album_override=1, updated_at=datetime.now())
            )
            with self.engine.begin() as conn:
                conn.execute(stmt)
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
            ins = sqlite_insert(albums).values(
                title=title,
                artist_id=artist_id,
                spotify_streams=streams,
                spotify_daily_streams=daily_streams,
                spotify_streams_updated=date_bind(updated_at or datetime.now()),
                spotify_album_ids=spotify_album_ids,
            )
            stmt = ins.on_conflict_do_update(
                index_elements=[albums.c.title, albums.c.artist_id],
                set_={
                    "spotify_streams": ins.excluded.spotify_streams,
                    "spotify_daily_streams": ins.excluded.spotify_daily_streams,
                    "spotify_streams_updated": ins.excluded.spotify_streams_updated,
                    "spotify_album_ids": func.coalesce(
                        ins.excluded.spotify_album_ids, albums.c.spotify_album_ids
                    ),
                },
            )
            with self.engine.begin() as conn:
                conn.execute(stmt)
            return True
        except Exception as e:
            logger.error(f"Erreur upsert_album (artist_id={artist_id}, title={title!r}): {e}")
            return False

    def get_albums_for_artist(self, artist_id: int) -> list[dict[str, Any]]:
        """Retourne les albums d'un artiste triés par streams décroissants."""
        try:
            # `text()` brut : `spotify_streams_updated` (TIMESTAMP) doit revenir
            # en STRING verbatim comme au temps du legacy sqlite3 — un `select()`
            # typé la parserait en datetime (cf. get_artist_tracks / piège E2).
            with self.engine.connect() as conn:
                rows = (
                    conn.execute(
                        text(
                            "SELECT title, spotify_streams, spotify_daily_streams, "
                            "spotify_streams_updated, ytm_streams FROM albums "
                            "WHERE artist_id = :aid ORDER BY spotify_streams DESC"
                        ),
                        {"aid": artist_id},
                    )
                    .mappings()
                    .all()
                )
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
            stmt = (
                update(tracks)
                .where(tracks.c.id == track_id)
                .values(ytm_streams=streams, ytm_streams_updated=datetime.now())
            )
            with self.engine.begin() as conn:
                conn.execute(stmt)
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
        protected = ("manual", "genius_media")
        try:
            stmt = (
                update(tracks)
                .where(
                    tracks.c.id == track_id,
                    or_(
                        literal(source).in_(protected),
                        tracks.c.youtube_url.is_(None),
                        tracks.c.youtube_url == "",
                        func.coalesce(tracks.c.youtube_url_source, "").notin_(protected),
                    ),
                )
                .values(youtube_url=url, youtube_url_source=source, updated_at=datetime.now())
            )
            with self.engine.begin() as conn:
                conn.execute(stmt)
            return True
        except Exception as e:
            logger.error(f"Erreur update_track_youtube_url (track_id={track_id}): {e}")
            return False

    def rename_track(self, track_id: int, new_title: str) -> bool:
        """Renomme un morceau en base (ex. « Matrix (Intro) » → « Matrix » pour
        aligner sur Kworb). Échoue si le titre existe déjà pour l'artiste
        (contrainte UNIQUE(title, artist_id))."""
        try:
            stmt = (
                update(tracks)
                .where(tracks.c.id == track_id)
                .values(title=new_title.strip(), updated_at=datetime.now())
            )
            with self.engine.begin() as conn:
                conn.execute(stmt)
            return True
        except Exception as e:
            logger.error(f"Erreur rename_track (track_id={track_id}): {e}")
            return False

    def clear_track_youtube_link(self, track_id: int) -> bool:
        """Efface le lien YouTube et sa provenance (repasse en recherche live)."""
        try:
            stmt = (
                update(tracks)
                .where(tracks.c.id == track_id)
                .values(youtube_url=None, youtube_url_source=None, updated_at=datetime.now())
            )
            with self.engine.begin() as conn:
                conn.execute(stmt)
            return True
        except Exception as e:
            logger.error(f"Erreur clear_track_youtube_link (track_id={track_id}): {e}")
            return False

    def update_album_ytm_streams(self, artist_id: int, title: str, streams: int) -> bool:
        """Met à jour les streams YouTube Music d'un album."""
        try:
            stmt = (
                update(albums)
                .where(albums.c.title == title, albums.c.artist_id == artist_id)
                .values(ytm_streams=streams, ytm_streams_updated=datetime.now())
            )
            with self.engine.begin() as conn:
                conn.execute(stmt)
            return True
        except Exception as e:
            logger.error(
                f"Erreur update_album_ytm_streams (artist_id={artist_id}, title={title!r}): {e}"
            )
            return False
