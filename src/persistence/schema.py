"""Schéma SQLAlchemy Core — reflet EXACT du schéma SQLite actuel (phase E1).

Ce module NE PILOTE PAS encore le runtime : le schéma reste créé par
`src/utils/db.py` (CREATE TABLE + migrations `user_version`, gel du schéma
jusqu'à E3). Il sert de source unique de vérité pour :
  - la révision initiale Alembic (E1c : `create_all` sur base vide ≡ `upgrade head`),
  - la bascule Core des repositories (E2 : `select(tracks).where(...)`),
  - le mapper ORM↔domaine.

**Contrainte** : `metadata.create_all(engine)` sur une base vide doit produire
EXACTEMENT le schéma que `db.py` produit aujourd'hui — mêmes colonnes, mêmes
types SQLite déclarés. Vérifié par `tests/test_schema_reflects_db.py`
(comparaison MetaData ↔ `PRAGMA table_info` sur base réelle).

Choix de types pour un rendu SQLite IDENTIQUE au legacy :
  - ``Integer`` → ``INTEGER``  ・ ``Text`` → ``TEXT``  ・ ``TIMESTAMP`` → ``TIMESTAMP``
  - ``Boolean(create_constraint=False)`` → ``BOOLEAN`` (le legacy n'a PAS de CHECK).
    (``DateTime`` rendrait ``DATETIME``, ``String`` rendrait ``VARCHAR`` : à proscrire.)

Toute évolution de colonne passera désormais par une révision Alembic (à partir
de E3), plus jamais par ``_MIGRATIONS`` de ``db.py``.
"""

from sqlalchemy import (
    REAL,
    TIMESTAMP,
    Boolean,
    Column,
    ForeignKey,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    text,
)

metadata = MetaData()

# Booléen legacy : "BOOLEAN DEFAULT 0" SANS contrainte CHECK.
_BOOL = Boolean(create_constraint=False)
_FALSE = text("0")


artists = Table(
    "artists",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", Text, nullable=False, unique=True),
    Column("genius_id", Integer),
    Column("spotify_id", Text),
    Column("discogs_id", Integer),
    Column("spotify_monthly_listeners", Integer),
    Column("ytm_monthly_listeners", Integer),
    Column("created_at", TIMESTAMP),
    Column("updated_at", TIMESTAMP),
    # Migrations user_version 3-8
    Column("ytm_channel_id", Text),
    Column("kworb_total_streams", Integer),
    Column("kworb_daily_streams", Integer),
    Column("kworb_lead_streams", Integer),
    Column("kworb_feat_streams", Integer),
    Column("kworb_updated", TIMESTAMP),
    # Origine du canal épinglé : 'manual' (saisie GUI) / 'inferred' (vote) — E8.
    # En FIN de table : `add_column` Alembic (e8) l'appose en dernière position,
    # `create_all` doit produire le MÊME ordre (garde-fou test_alembic_baseline).
    Column("ytm_channel_source", Text),
    # Chantier « Media » (e9) : chemin relatif (à IMAGES_DIR) de la photo de profil.
    Column("image_path", Text),
    sqlite_autoincrement=True,
)


monthly_listeners_history = Table(
    "monthly_listeners_history",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("artist_id", Integer, ForeignKey("artists.id"), nullable=False),
    Column("spotify_listeners", Integer),
    Column("ytm_listeners", Integer),
    Column("total_estimated", Integer),
    Column("recorded_at", TIMESTAMP),
    sqlite_autoincrement=True,
)


tracks = Table(
    "tracks",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("title", Text, nullable=False),
    Column("artist_id", Integer, ForeignKey("artists.id"), nullable=False),
    Column("album", Text),
    Column("track_number", Integer),
    Column("release_date", TIMESTAMP),
    Column("genius_id", Integer),
    Column("spotify_id", Text),
    Column("discogs_id", Integer),
    Column("bpm", Integer),
    Column("duration", Integer),
    Column("genre", Text),
    Column("genius_url", Text),
    Column("spotify_url", Text),
    Column("youtube_url", Text),
    Column("is_featuring", _BOOL, server_default=_FALSE),
    Column("primary_artist_name", Text),
    Column("featured_artists", Text),
    Column("lyrics", Text),
    Column("has_lyrics", _BOOL, server_default=_FALSE),
    Column("lyrics_scraped_at", TIMESTAMP),
    Column("created_at", TIMESTAMP),
    Column("updated_at", TIMESTAMP),
    Column("last_scraped", TIMESTAMP),
    # Migrations user_version 15-42 (colonnes ajoutées après le schéma de départ)
    Column("isrc", Text),
    Column("bpm_source", Text),
    Column("bpm_confidence", Integer),
    Column("key_mode_source", Text),
    Column("reccobeats_resolution", Text),
    Column("secondary_role", Text),
    Column("bpm_alt", Integer),
    Column("lyrics_source", Text),
    Column("lyrics_synced", Text),
    Column("lyrics_synced_source", Text),
    Column("lyrics_synced_confidence", Integer),
    Column("relationships", Text),
    Column("certifications", Text),
    Column("album_certifications", Text),
    Column("musical_key", Text),
    Column("key", Text),
    Column("mode", Text),
    Column("time_signature", Text),
    Column("anecdotes", Text),
    Column("spotify_page_title", Text),
    Column("spotify_streams", Integer),
    Column("spotify_daily_streams", Integer),
    Column("spotify_streams_updated", TIMESTAMP),
    Column("ytm_streams", Integer),
    Column("ytm_streams_updated", TIMESTAMP),
    Column("youtube_url_source", Text),
    Column("album_override", Integer),
    # Chantier « Media » (e9), EN FIN de table (ordre = add_column Alembic) :
    # chemins d'images (relatifs à IMAGES_DIR) + métadonnées de la vidéo YouTube.
    Column("cover_path", Text),
    Column("yt_thumbnail_path", Text),
    Column("youtube_video_kind", Text),  # 'clip'/'show'/'audio'/'unknown'
    Column("youtube_video_views", Integer),  # vues de LA vidéo (≠ ytm_streams)
    Column("youtube_video_views_updated", TIMESTAMP),
    UniqueConstraint("title", "artist_id"),
    sqlite_autoincrement=True,
)


credits = Table(
    "credits",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("track_id", Integer, ForeignKey("tracks.id"), nullable=False),
    Column("name", Text, nullable=False),
    Column("role", Text, nullable=False),
    Column("role_detail", Text),
    Column("source", Text),
    UniqueConstraint("track_id", "name", "role", "role_detail"),
    sqlite_autoincrement=True,
)


scraping_errors = Table(
    "scraping_errors",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("track_id", Integer, ForeignKey("tracks.id"), nullable=False),
    Column("error_message", Text),
    Column("error_time", TIMESTAMP),
    sqlite_autoincrement=True,
)


albums = Table(
    "albums",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("title", Text, nullable=False),
    Column("artist_id", Integer, ForeignKey("artists.id"), nullable=False),
    Column("spotify_streams", Integer),
    Column("spotify_daily_streams", Integer),
    Column("spotify_streams_updated", TIMESTAMP),
    # Migrations user_version 43-45
    Column("ytm_streams", Integer),
    Column("ytm_streams_updated", TIMESTAMP),
    Column("spotify_album_ids", Text),
    UniqueConstraint("title", "artist_id"),
    sqlite_autoincrement=True,
)


# Observations (phase E4) : provenance scalaire par (morceau, champ, source).
# Modèle UPSERT — au plus une valeur par (track_id, field, source), la dernière
# vue (`seen_at`). Alimentée par backfill E4 (bpm/key/mode depuis les colonnes
# `*_source`) puis, en E5, par les providers (triple écriture). `value` en TEXT
# (coercition au retour par le mapper, E6). `confidence` REAL (sémantique BPM).
# FK déclarative sans cascade (PRAGMA foreign_keys jamais activé) → delete/merge
# gèrent les observations explicitement (track_repository).
# key/mode = DEUX observations distinctes (même source `key_mode_source`), la
# paire est l'unité fiable côté moteur (E5).
observations = Table(
    "observations",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("track_id", Integer, ForeignKey("tracks.id"), nullable=False),
    Column("field", Text, nullable=False),
    Column("value", Text),
    Column("source", Text, nullable=False),
    Column("confidence", REAL),
    Column("seen_at", TIMESTAMP),
    UniqueConstraint("track_id", "field", "source"),
    sqlite_autoincrement=True,
)
