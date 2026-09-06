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
    Column("secondary_role", Text),
    Column("lyrics_source", Text),
    Column("lyrics_synced", Text),
    Column("lyrics_synced_source", Text),
    Column("lyrics_synced_confidence", Integer),
    Column("relationships", Text),
    Column("certifications", Text),
    Column("album_certifications", Text),
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
    # Migration e17 : date de la dernière RÉSOLUTION d'ID Spotify menée à
    # terme, quel qu'en soit le résultat. Distingue « cherché et absent »
    # de « jamais cherché » — un `spotify_id` vide ne disait pas lequel.
    Column("spotify_id_checked_at", TIMESTAMP),
    # Migration e19 : les trois valeurs que le provider Deezer posait sur le
    # Track sans qu'aucune colonne ne les attende. `explicit_lyrics` est
    # NULLABLE à dessein — NULL = jamais mesuré, 0 = Deezer dit que non.
    Column("deezer_id", Integer),
    Column("deezer_url", Text),
    Column("explicit_lyrics", _BOOL),
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
    # Pistes de la galette auxquelles le crédit s'applique (« A1,B3 »), séparées
    # de `role_detail` qui, lui, qualifie le RÔLE (e18). Déclarée APRÈS `source` :
    # `ALTER TABLE ADD COLUMN` ajoute en fin de table, et `test_alembic_baseline`
    # exige que ce fichier rende le MÊME DDL que la suite des migrations.
    Column("tracks", Text),
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
    # Migration e14 : QUI a calculé `spotify_streams`. Kworb ne somme que les
    # morceaux de l'artiste (total INCOMPLET sur un album commun ou de groupe),
    # Spotify somme toutes les pistes du disque. Sans cette colonne, les deux
    # sémantiques se mélangeraient en silence.
    Column("spotify_streams_source", Text),
    # Migration e15 : total de CHAQUE édition ({album_id: streams}). Le total
    # du disque est la somme des enregistrements DISTINCTS — Spotify compte par
    # enregistrement, pas par track_id, donc additionner les éditions
    # compterait deux fois les titres partagés.
    Column("spotify_editions_json", Text),
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


# Usage réel des sources (chantier « état des sources nourri par l'usage »).
# Compteurs AGRÉGÉS, en forme longue : une ligne par nature de verdict, si bien
# qu'ajouter une `IssueKind` ne coûtera aucune migration. `day` est en TEXT et
# non en TIMESTAMP parce que c'est une CLÉ d'agrégation, pas un instant — cela
# l'exempte du piège double-face du type TIMESTAMP (bind typé en écriture,
# reparse en datetime en lecture).
#
# PIÈGE SQLITE : dans un index UNIQUE, deux NULL sont DISTINCTS. La contrainte
# ci-dessous ne se déclenche donc JAMAIS pour les lignes `artist_id IS NULL`
# (usage hors contexte artiste) : l'upsert du repository est explicite et
# null-safe (UPDATE ... WHERE artist_id IS :aid, puis INSERT si rowcount == 0),
# la contrainte n'étant qu'un filet pour les lignes à artiste renseigné.
source_usage_daily = Table(
    "source_usage_daily",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("day", Text, nullable=False),
    Column("source_key", Text, nullable=False),
    Column("artist_id", Integer, ForeignKey("artists.id")),
    Column("flow", Text, nullable=False),
    Column("issue", Text, nullable=False),
    Column("n_calls", Integer),
    Column("n_attempts", Integer),
    Column("expected_blocked", Integer),
    Column("latency_ms_total", Integer),
    Column("last_seen", TIMESTAMP),
    UniqueConstraint("day", "source_key", "artist_id", "flow", "issue"),
    sqlite_autoincrement=True,
)


# Fenêtre glissante des derniers échecs PAR SOURCE (50), pour le diagnostic.
# Ne double pas `scraping_errors`, qui est indexée par morceau : celle-ci l'est
# par source et porte la NATURE de l'échec. Purgée au vidage, jamais à chaque
# insertion → plafond dur d'environ 800 lignes quelle que soit l'ancienneté de
# la base.
source_usage_failures = Table(
    "source_usage_failures",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("source_key", Text, nullable=False),
    Column("issue", Text, nullable=False),
    Column("artist_id", Integer, ForeignKey("artists.id")),
    Column("track_id", Integer, ForeignKey("tracks.id")),
    Column("status_code", Integer),
    Column("message", Text),
    Column("occurred_at", TIMESTAMP),
    sqlite_autoincrement=True,
)
