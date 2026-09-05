"""Repository des morceaux, albums et crédits.

Toute la persistance liée aux `tracks` (save/get/delete/merge), aux crédits et
aux albums (streams Kworb/YTM). Utilisé comme base de `DataManager`, qui fournit
`self.engine` (moteur SQLAlchemy Core, délégué à `Database`). Comportement
constant vs l'ancien `data_manager.py` (refonte 1.5 puis bascule Core phase E2).
"""

import json
import time
from datetime import datetime
from typing import Any

from sqlalchemy import func, literal, or_, select, text, update
from sqlalchemy.exc import SQLAlchemyError

from src.config import settings
from src.enrichment.observation import Observation
from src.models import Credit, Track
from src.persistence.binding import date_bind
from src.persistence.schema import albums, artists, credits, tracks
from src.utils.logger import get_logger
from src.utils.track_mapper import track_from_row

logger = get_logger(__name__)

# Champs d'observation AUDIO pilotant les colonnes réconciliées (E6). Supprimés
# ensemble quand un morceau est « nettoyé » (E7-D1) : sinon la réconciliation les
# ressusciterait à la lecture. bpm_alt suit bpm (octave dérivée).
_AUDIO_OBS_FIELDS = ("bpm", "bpm_alt", "key", "mode", "time_signature")


class TrackRepository:
    """Persistance des morceaux, crédits et albums. Requiert `self.engine`."""

    def save_track(self, track: Track) -> int:
        """Sauvegarde ou met à jour un morceau.

        E7-D1 : les colonnes AUDIO (bpm, bpm_alt, bpm_source, bpm_confidence, key,
        mode, key_mode_source, musical_key, time_signature, reccobeats_resolution)
        ne sont PLUS écrites — la vérité vit dans `observations` (upsert ci-dessous),
        relue par la réconciliation du mapper (E6). Les colonnes restent (gelées)
        jusqu'au drop E7-D2 et servent de fallback pour un morceau sans observation.
        """
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
            certifications_json = json.dumps(track.certs.entries)
            album_certifications_json = json.dumps(track.certs.album_entries)
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
                # e17 : date de la dernière résolution d'ID Spotify menée à
                # terme. COALESCE à l'UPDATE — un run qui ne cherche pas l'ID
                # ne doit pas effacer le constat d'un run précédent.
                "spotify_id_checked_at": track.spotify_id_checked_at,
                # E7-D1 : les colonnes audio ne sont plus écrites (pilotées par les
                # observations, reconcile au mapper) → clés retirées de params.
                "duration": track.duration,
                "genre": track.genre,
                "genius_url": track.genius_url,
                "spotify_url": track.spotify_url,
                "youtube_url": track.youtube_url,
                "youtube_url_source": track.youtube_url_source,
                "is_featuring": track.is_featuring,
                "primary_artist_name": track.primary_artist_name,
                "featured_artists": track.featured_artists,
                "secondary_role": track.secondary_role,
                "lyrics": track.lyrics.text,
                "lyrics_scraped_at": track.lyrics.scraped_at,
                "lyrics_source": track.lyrics.source,
                "lyrics_synced": track.lyrics.synced,
                "lyrics_synced_source": track.lyrics.synced_source,
                "lyrics_synced_confidence": track.lyrics.synced_confidence,
                "has_lyrics": bool(track.lyrics.text),  # INSERT uniquement
                "anecdotes": track.anecdotes,
                "certifications_json": certifications_json,
                "album_certifications_json": album_certifications_json,
                "relationships_json": relationships_json,
                "spotify_page_title": getattr(track, "spotify_page_title", None),
                # Chantier « Media » : chemins d'images (kind/vues vidéo passent par
                # update_track_video_views, jamais ici).
                "cover_path": track.media.cover_path,
                "yt_thumbnail_path": track.media.yt_thumbnail_path,
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
                # re-sauvent depuis l'API portent is_featuring sur l'objet.
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
                        spotify_id_checked_at = COALESCE(
                            :spotify_id_checked_at, spotify_id_checked_at),
                        -- E7-D1 : colonnes audio (bpm, bpm_alt, bpm_source,
                        -- bpm_confidence, key, mode, key_mode_source, musical_key,
                        -- time_signature, reccobeats_resolution) NON écrites — la
                        -- réconciliation des observations les pilote (mapper E6).
                        -- Gelées jusqu'au drop E7-D2 ; lues en fallback si aucune obs.
                        duration = COALESCE(:duration, duration),
                        genre = COALESCE(:genre, genre),
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
                        cover_path = COALESCE(:cover_path, cover_path),
                        yt_thumbnail_path = COALESCE(:yt_thumbnail_path, yt_thumbnail_path),
                        updated_at = :now,
                        last_scraped = COALESCE(:last_scraped, last_scraped)
                    WHERE id = :id
                """),
                    params,
                )
            else:
                result = conn.execute(
                    text("""
                    -- E7-D1 : colonnes audio (bpm, bpm_alt, bpm_source, bpm_confidence,
                    -- key, mode, key_mode_source, musical_key, time_signature,
                    -- reccobeats_resolution) NON insérées — pilotées par les
                    -- observations (mapper E6). NULL à l'INSERT, gelées jusqu'au drop D2.
                    INSERT INTO tracks (
                        title, artist_id, album, track_number, release_date,
                        genius_id, spotify_id, discogs_id, isrc, spotify_id_checked_at,
                        duration, genre,
                        genius_url, spotify_url, youtube_url, youtube_url_source,
                        is_featuring, primary_artist_name, featured_artists, secondary_role,
                        lyrics, lyrics_scraped_at, lyrics_source, lyrics_synced, lyrics_synced_source, lyrics_synced_confidence, has_lyrics, anecdotes,
                        certifications, album_certifications, relationships, spotify_page_title,
                        cover_path, yt_thumbnail_path,
                        created_at, updated_at, last_scraped
                    ) VALUES (
                        :title, :artist_id, :album, :track_number, :release_date,
                        :genius_id, :spotify_id, :discogs_id, :isrc, :spotify_id_checked_at,
                        :duration, :genre,
                        :genius_url, :spotify_url, :youtube_url, :youtube_url_source,
                        :is_featuring, :primary_artist_name, :featured_artists, :secondary_role,
                        :lyrics, :lyrics_scraped_at, :lyrics_source, :lyrics_synced, :lyrics_synced_source, :lyrics_synced_confidence, :has_lyrics, :anecdotes,
                        :certifications_json, :album_certifications_json, :relationships_json, :spotify_page_title,
                        :cover_path, :yt_thumbnail_path,
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

            # E7-D1 : nettoyage audio demandé → supprimer les observations audio
            # persistées DANS la même transaction. Sans ça, la réconciliation du
            # mapper les ressusciterait à la lecture (l'attribut mis à None ne
            # suffit plus : la vérité vit dans `observations`, pas dans la colonne).
            if track.id and track.clear_audio_observations:
                for obs_field in _AUDIO_OBS_FIELDS:
                    self._delete_observations(conn, track.id, obs_field)
                # E7-D2 : plus de colonnes audio à vider (droppées) — la suppression
                # des observations suffit (le mapper n'a plus de fallback colonne).

            # commit auto à la sortie du bloc `engine.begin()`
            logger.info(
                f"Morceau sauvegardé: {track.title} (ID: {track.id}, "
                f"Featuring: {track.is_featuring}, Paroles: {bool(track.lyrics.text)})"
            )
            return track.id

    def _save_credit(self, conn, track_id: int, credit: Credit):
        """Sauvegarde un crédit (connexion Core fournie par l'appelant)."""
        try:
            conn.execute(
                text(
                    "INSERT INTO credits "
                    "(track_id, name, role, role_detail, tracks, source) "
                    "VALUES (:track_id, :name, :role, :role_detail, :tracks, :source)"
                ),
                {
                    "track_id": track_id,
                    "name": credit.name,
                    "role": credit.role.value,
                    "role_detail": credit.role_detail,
                    "tracks": credit.tracks,
                    "source": credit.source,
                },
            )
        except SQLAlchemyError as e:
            # Un crédit perdu ne doit pas arrêter la sauvegarde du morceau, mais il
            # doit se VOIR : `logger.debug` n'atteint aucun fichier de log (handler
            # à INFO), la perte était donc silencieuse.
            logger.warning(f"Crédit non sauvegardé ({credit.name}): {e}")

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
                # Témoin de perf (E7d) : chrono par sous-phase pour localiser le
                # coût du chargement d'un gros artiste (SELECT tracks / observations
                # / boucle mapper+crédits). La boucle mapper porte à la fois la
                # réconciliation par morceau ET un N+1 sur les crédits.
                _t0 = time.monotonic()
                rows = (
                    conn.execute(
                        text("SELECT * FROM tracks WHERE artist_id = :aid ORDER BY title"),
                        {"aid": artist_id},
                    )
                    .mappings()
                    .all()
                )
                logger.info(f"📦 {len(rows)} lignes récupérées")
                _t_rows = time.monotonic()

                # E6 : observations de TOUT l'artiste en 1 requête (pas par track),
                # groupées par track_id → passées au mapper qui les réconcilie.
                observations_by_track = self._observations_by_artist(conn, artist_id)
                _t_obs = time.monotonic()

                # Volume des LRC bruts chargés (`lyrics_synced`) : champ lourd
                # (~3-10 Ko × sources × morceaux), cible désignée de l'optim E7d.
                _n_obs = sum(len(v) for v in observations_by_track.values())
                _lyrics_bytes = sum(
                    len(o.value or "")
                    for v in observations_by_track.values()
                    for o in v
                    if o.field == "lyrics_synced"
                )

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
                        except SQLAlchemyError:
                            track.credits = []

                        result.append(track)

                        if i < 5:
                            logger.info(f"✅ Track {i+1}: {track.title}")

                    except Exception:
                        # Dernier ressort : un morceau illisible ne doit pas faire
                        # perdre la discographie entière. `logger.exception` pour
                        # garder la trace (et rester exempt de BLE001).
                        logger.exception("❌ Erreur track %d — ignoré, on continue", i)
                        continue

                # Compter les tracks avec musical_key
                tracks_with_key = sum(1 for t in result if t.audio.musical_key)
                logger.info(
                    f"✅ {len(result)} tracks chargés avec succès ({tracks_with_key} avec musical_key)"
                )

                _t_end = time.monotonic()
                logger.info(
                    "⏱ get_artist_tracks: %d tracks, %d obs dont %.0f Ko lyrics_synced "
                    "en %.2fs (rows %.2fs, obs %.2fs, map+crédits %.2fs)",
                    len(result),
                    _n_obs,
                    _lyrics_bytes / 1024,
                    _t_end - _t0,
                    _t_rows - _t0,
                    _t_obs - _t_rows,
                    _t_end - _t_obs,
                )

        except SQLAlchemyError as e:
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
                            tracks=row["tracks"],
                            source=str(source),
                        )
                        result.append(credit)

                except (KeyError, ValueError, TypeError) as credit_error:
                    logger.debug(f"Erreur crédit: {credit_error}")
                    continue

        except (SQLAlchemyError, KeyError, ValueError, TypeError) as e:
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
        except SQLAlchemyError as e:
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
                # Les colonnes ARBITRÉES doivent suivre les observations qu'on
                # vient de déplacer. Sans ça, le morceau conservé porte les
                # observations du doublon et une colonne restée VIDE — constaté
                # le 2026-09-05 sur « My Love (Acoustic) », qui avait ses deux
                # observations (Kworb et Spotify) et aucun stream affiché.
                valeurs = self._arbitrer_streams(conn, keep_id)
                if valeurs:
                    conn.execute(update(tracks).where(tracks.c.id == keep_id).values(**valeurs))
                conn.execute(
                    text("DELETE FROM tracks WHERE id = :delete_id"), {"delete_id": delete_id}
                )
                logger.info(
                    f"🔀 Track {delete_id} fusionné dans {keep_id} "
                    f"({transferred} crédit(s) transféré(s))"
                )
                return True
        except SQLAlchemyError as e:
            logger.error(f"Erreur fusion track {delete_id} → {keep_id}: {e}")
            return False

    # ──────────────────────────────────────────────────────────────────────────
    # Streams Spotify (Kworb · Spotify web)
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _as_int_or_none(value) -> int | None:
        """Entier d'une valeur d'observation, rendue BRUTE (souvent en TEXT)."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def get_track_ids_by_spotify_id(self) -> dict[str, tuple[int, int]]:
        """Carte `spotify_id → (track_id, artist_id)` sur TOUTE la base.

        C'est elle qui rend possible la RÉCOLTE CROISÉE du scrape Spotify : une
        page titre expose les compteurs de quinze autres morceaux, souvent ceux de
        collaborateurs — sur une base de rap français, les featurings sont la
        norme. Reconnaître ces morceaux suppose de s'appuyer sur un ID et jamais
        sur un nom : l'attribution par nom est précisément ce qui a fait écrire le
        catalogue de Limsa d'Aulnay sur Isha (JOURNAL 2026-07-02).

        Le périmètre est volontairement GLOBAL, pas limité à l'artiste du run :
        c'est tout l'intérêt de la récolte.
        """
        try:
            with self.engine.connect() as conn:
                rows = (
                    conn.execute(
                        text(
                            "SELECT id, artist_id, spotify_id FROM tracks "
                            "WHERE spotify_id IS NOT NULL AND spotify_id != ''"
                        )
                    )
                    .mappings()
                    .all()
                )
            return {r["spotify_id"]: (r["id"], r["artist_id"]) for r in rows}
        except SQLAlchemyError as e:
            logger.error(f"Erreur get_track_ids_by_spotify_id: {e}")
            return {}

    def get_stream_observation_dates(self, source: str) -> dict[int, str | None]:
        """`{track_id: seen_at brut}` des observations de streams d'une source.

        Sert la FRAÎCHEUR du crawl : on ne redépense pas une page pour un morceau
        vu récemment, **peu importe quel run l'a vu** — une valeur récoltée
        pendant le run d'un autre artiste compte comme fraîche. D'où, là encore,
        un périmètre global.

        `seen_at` est rendu BRUT (string) comme partout sur ce chemin `text()` :
        le type TIMESTAMP le parserait en datetime et divergerait du legacy
        (piège E2).
        """
        try:
            with self.engine.connect() as conn:
                rows = (
                    conn.execute(
                        text(
                            "SELECT track_id, seen_at FROM observations "
                            "WHERE field = 'spotify_streams' AND source = :src"
                        ),
                        {"src": source},
                    )
                    .mappings()
                    .all()
                )
            return {r["track_id"]: r["seen_at"] for r in rows}
        except SQLAlchemyError as e:
            logger.error(f"Erreur get_stream_observation_dates({source}): {e}")
            return {}

    def record_spotify_streams(
        self,
        track_id: int,
        streams: int,
        source: str,
        updated_at=None,
        *,
        daily_streams: int | None = None,
    ) -> bool:
        """Enregistre ce qu'UNE source a vu, puis arbitre la valeur de la colonne.

        updated_at : fraîcheur RÉELLE de la source — date « Last updated » de la
        page Kworb, instant du scrape pour Spotify web — sinon now().

        `source` nomme qui a lu la valeur. La clé d'upsert d'une observation étant
        `(track_id, field, source)`, deux sources coexistent sans s'écraser : c'est
        ce qui rend la comparaison Kworb / Spotify possible sans rien dupliquer.

        **La colonne n'est pas écrite par l'appelant : elle est ARBITRÉE ici.**
        Chaque source ne déclare que ce qu'elle a vu ; la valeur inscrite est le
        verdict de `reconcile_spotify_streams`, recalculé dans la MÊME transaction
        à partir de toutes les observations du morceau. L'ordre d'exécution des
        sources n'a donc aucun effet : « Kworb puis Spotify » et « Spotify puis
        Kworb » laissent la base dans le même état.

        `daily_streams=None` laisse `spotify_daily_streams` INTACT : Spotify web
        ne publie aucun chiffre quotidien, et y écrire None effacerait la valeur
        de Kworb, seule source à en donner.
        """
        try:
            with self.engine.begin() as conn:
                # seen_at = fraîcheur de la source VERBATIM (chemin text() de
                # `_upsert_observations`) : date « Last updated » pour Kworb,
                # instant du scrape pour Spotify.
                self._upsert_observations(
                    conn,
                    track_id,
                    # Champ canonique : `reconcile.SPOTIFY_STREAMS_FIELD` (importé
                    # localement dans `_arbitrer_streams`, cf. cycle d'imports).
                    [Observation("spotify_streams", streams, source, seen_at=updated_at)],
                )
                values = self._arbitrer_streams(conn, track_id)
                if daily_streams is not None:
                    values["spotify_daily_streams"] = daily_streams
                if values:
                    conn.execute(update(tracks).where(tracks.c.id == track_id).values(**values))
            return True
        except SQLAlchemyError as e:
            logger.error(f"Erreur record_spotify_streams (track_id={track_id}): {e}")
            return False

    def _arbitrer_streams(self, conn, track_id: int) -> dict:
        """Colonnes de streams à écrire, d'après TOUTES les observations du morceau.

        Le `seen_at` retenu est celui de l'observation GAGNANTE, pas l'instant du
        présent appel : sans quoi une écriture Spotify daterait d'aujourd'hui une
        valeur qui vient en réalité de la dernière mise à jour de Kworb.
        """
        # Import LOCAL : `src.enrichment.reconcile` tire `src.utils.*`, dont
        # l'`__init__` importe `DataEnricher` — un import au sommet reboucle sur
        # ce module. C'est l'idiome anti-cycle déjà en place chez les providers.
        from src.enrichment.reconcile import SPOTIFY_STREAMS_FIELD, reconcile_spotify_streams

        observations = [
            o for o in self._get_observations(conn, track_id) if o.field == SPOTIFY_STREAMS_FIELD
        ]
        verdict = reconcile_spotify_streams(observations, settings.streams_master)
        if verdict is None:
            return {}
        value = self._as_int_or_none(verdict.value)
        if value is None:
            logger.warning(
                f"Streams non numériques pour track_id={track_id} "
                f"(source {verdict.source}, valeur {verdict.value!r}) — colonne inchangée"
            )
            return {}
        gagnante = next((o for o in observations if o.source == verdict.source), None)
        vue_le = gagnante.seen_at if gagnante is not None else None
        return {
            "spotify_streams": value,
            "spotify_streams_updated": date_bind(vue_le or datetime.now()),
        }

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
        except SQLAlchemyError as e:
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
        except SQLAlchemyError as e:
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
        source: str = "kworb",
        editions_json: str = None,
    ) -> bool:
        """Insère ou met à jour le total de streams d'un album.

        spotify_album_ids : IDs Spotify des éditions agrégées, séparés par des
        virgules (un même titre peut couvrir plusieurs éditions — streams sommés
        par l'appelant).

        `source` dit QUI a calculé le total, et les deux ne mesurent pas la même
        chose : Kworb ne somme que les morceaux DE L'ARTISTE, Spotify somme
        **toutes** les pistes du disque. Sur un album commun ou de groupe — la
        majorité des cas ici — le total de Kworb est donc incomplet.

        D'où la seule règle d'arbitrage de cette table : **un total Kworb
        n'écrase jamais un total Spotify**. L'inverse est permis (Spotify est
        complet par construction : l'appelant ne l'écrit que s'il a TOUTES les
        pistes). Sans ce garde, un run Kworb postérieur ramènerait silencieusement
        la valeur à la somme partielle.
        """
        try:
            with self.engine.begin() as conn:
                existant = (
                    conn.execute(
                        text(
                            "SELECT spotify_streams, spotify_daily_streams, spotify_album_ids, "
                            "spotify_streams_source, spotify_editions_json, "
                            "spotify_streams_updated FROM albums "
                            "WHERE title = :t AND artist_id = :a"
                        ),
                        {"t": title, "a": artist_id},
                    )
                    .mappings()
                    .first()
                )
                valeurs = self._valeurs_album(
                    existant,
                    streams,
                    daily_streams,
                    spotify_album_ids,
                    updated_at,
                    source,
                    editions_json,
                )
                if existant:
                    conn.execute(
                        update(albums)
                        .where(albums.c.title == title, albums.c.artist_id == artist_id)
                        .values(**valeurs)
                    )
                else:
                    conn.execute(
                        albums.insert().values(title=title, artist_id=artist_id, **valeurs)
                    )
            return True
        except SQLAlchemyError as e:
            logger.error(f"Erreur upsert_album (artist_id={artist_id}, title={title!r}): {e}")
            return False

    @staticmethod
    def _fusion_ids(ancien: str | None, nouveau: str | None) -> str | None:
        """Union des IDs d'édition, ordre d'apparition conservé.

        Une FUSION, pas un remplacement : chaque source ne connaît que les
        éditions qu'elle a vues — la page artiste Spotify n'en liste qu'une là où
        Kworb en a deux. Remplacer perdrait silencieusement l'autre, alors que
        c'est précisément la donnée qu'on veut garder pour plus tard.
        """
        vus: dict[str, None] = {}
        for source_ids in (ancien, nouveau):
            for identifiant in (source_ids or "").split(","):
                if identifiant.strip():
                    vus.setdefault(identifiant.strip(), None)
        return ",".join(vus) or None

    def _valeurs_album(
        self,
        existant,
        streams,
        daily_streams,
        spotify_album_ids,
        updated_at,
        source,
        editions_json,
    ) -> dict:
        """Colonnes à écrire pour un album, provenance arbitrée.

        Le total de Kworb n'écrase JAMAIS celui de Spotify : Kworb ne somme que
        les morceaux de l'artiste, Spotify toutes les pistes du disque. Mais ce
        garde ne porte QUE sur le total — les IDs d'édition, eux, fusionnent
        toujours, quelle que soit la source qui écrit.
        """
        valeurs = {
            "spotify_album_ids": self._fusion_ids(
                existant["spotify_album_ids"] if existant else None, spotify_album_ids
            ),
        }
        garde = (
            existant is not None
            and existant["spotify_streams_source"] == "spotify_web"
            and source != "spotify_web"
        )
        if garde:
            return valeurs

        valeurs["spotify_streams"] = streams
        valeurs["spotify_streams_updated"] = date_bind(updated_at or datetime.now())
        valeurs["spotify_streams_source"] = source
        # Spotify ne publie aucun chiffre quotidien et passe None : sans ce
        # garde, il effacerait celui de Kworb, seule source à en donner.
        if daily_streams is not None:
            valeurs["spotify_daily_streams"] = daily_streams
        if editions_json is not None:
            valeurs["spotify_editions_json"] = editions_json
        return valeurs

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
                            "spotify_streams_updated, spotify_streams_source, spotify_editions_json, "
                            "spotify_album_ids, "
                            "ytm_streams FROM albums "
                            "WHERE artist_id = :aid ORDER BY spotify_streams DESC"
                        ),
                        {"aid": artist_id},
                    )
                    .mappings()
                    .all()
                )
                # `spotify_streams_source` et `spotify_editions_json` étaient
                # SÉLECTIONNÉS mais absents du dict rendu (2026-09-05) : lus en
                # base puis jetés avant d'atteindre la GUI. Or les deux sources
                # ne comptent PAS la même chose — Kworb ne somme que les morceaux
                # de l'artiste, Spotify toutes les pistes du disque — donc un
                # total sans sa provenance n'est pas interprétable.
                return [
                    {
                        "title": row["title"],
                        "spotify_streams": row["spotify_streams"],
                        "spotify_daily_streams": row["spotify_daily_streams"],
                        "spotify_streams_updated": row["spotify_streams_updated"],
                        "spotify_streams_source": row["spotify_streams_source"],
                        "spotify_editions_json": row["spotify_editions_json"],
                        # 3e occurrence du MÊME défaut : une colonne ajoutée au
                        # SELECT mais oubliée du dict est lue en base puis jetée.
                        # Celle-ci porte les IDs d'édition, seul moyen pour le
                        # scrape Spotify d'atteindre une réédition que la page
                        # artiste ne liste pas.
                        "spotify_album_ids": row["spotify_album_ids"],
                        "ytm_streams": row["ytm_streams"],
                    }
                    for row in rows
                ]
        except SQLAlchemyError as e:
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
                # E7e : write-through de la provenance (mono-source, pas de vote).
                self._upsert_observations(
                    conn,
                    track_id,
                    [Observation("ytm_streams", streams, "ytmusic", seen_at=datetime.now())],
                )
            return True
        except SQLAlchemyError as e:
            logger.error(f"Erreur update_track_ytm_streams (track_id={track_id}): {e}")
            return False

    def update_track_video_views(
        self, track_id: int, views: int | None, kind: str | None = None
    ) -> bool:
        """Met à jour les vues de LA vidéo YouTube d'un morceau + sa catégorie.

        SÉPARÉ de `update_track_ytm_streams` (qui somme audio+clip) : c'est la
        réponse au « différencier un clip d'un morceau classique ». Écriture en
        `text()` non typé + `CURRENT_TIMESTAMP` (pas de bind date typé →
        contourne le piège TIMESTAMP double-face). `kind` en COALESCE : un appel
        sans catégorie ne l'efface pas.
        """
        try:
            with self.engine.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE tracks SET youtube_video_views = :views, "
                        "youtube_video_kind = COALESCE(:kind, youtube_video_kind), "
                        "youtube_video_views_updated = CURRENT_TIMESTAMP, "
                        "updated_at = :now WHERE id = :tid"
                    ),
                    {"views": views, "kind": kind, "now": datetime.now(), "tid": track_id},
                )
            return True
        except SQLAlchemyError as e:
            logger.error(f"Erreur update_track_video_views (track_id={track_id}): {e}")
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
        except SQLAlchemyError as e:
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
        except SQLAlchemyError as e:
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
        except SQLAlchemyError as e:
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
        except SQLAlchemyError as e:
            logger.error(
                f"Erreur update_album_ytm_streams (artist_id={artist_id}, title={title!r}): {e}"
            )
            return False
