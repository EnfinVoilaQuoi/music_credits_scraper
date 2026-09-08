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
    Index,
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


# Appartenance à une formation (e22). Table de LIENS : la discographie d'un
# membre est réunie par UNION d'`artist_id` à la lecture, jamais en dupliquant
# des morceaux (`UNIQUE(title, artist_id)` l'interdit, et cela doublerait
# streams et certifications).
#
# `related_artist_id` est NULLABLE à dessein : le groupe lié n'est pas forcément
# en base, et le lien reste une information — il deviendra une jointure le jour
# où on l'ajoutera. C'est pourquoi la clé d'unicité porte sur le NOM.
#
# `begin_date`/`end_date` en TEXTE : MusicBrainz rend des dates PARTIELLES
# (« 1989-10 », « 2009 ») qu'un type date refuserait ou mutilerait. Et surtout,
# la colonne ne s'appelle PAS `end` — mot réservé SQL, que les nombreux `text()`
# du projet devraient quoter à chaque fois (la colonne `key` a déjà imposé ça).
artist_relations = Table(
    "artist_relations",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("artist_id", Integer, ForeignKey("artists.id"), nullable=False),
    Column("related_artist_id", Integer, ForeignKey("artists.id")),
    Column("related_name", Text, nullable=False),
    Column("kind", Text, nullable=False),  # member_of | has_member | alias
    Column("source", Text),  # musicbrainz | discogs | manual
    # Nature de la formation : un GROUPE apporte tous ses morceaux au membre,
    # un COLLECTIF seulement ceux où le membre est présent (écriture, prod,
    # performance). Nulle pour un alias.
    Column("formation", Text),  # groupe | collectif
    Column("begin_date", Text),
    Column("end_date", Text),
    Column("confirmed_at", TIMESTAMP),
    Column("created_at", TIMESTAMP),
    UniqueConstraint("artist_id", "related_name", "kind"),
    sqlite_autoincrement=True,
)


# Identifiants Spotify d'un morceau (e23) — un morceau en a souvent PLUSIEURS :
# le single et l'album, une réédition, un intl. Le pluriel était déjà modélisé
# côté objet (`Track.spotify_ids`, `add_spotify_id`) et affiché par la GUI
# (sélecteur « Version 1 / Version 2 »), mais AUCUNE colonne ne le portait : la
# liste mourait au `save_track`, et « accepter un ID alternatif » dégénérait en
# « écraser l'ID de l'autre ligne ».
#
# Calque exact de `track_videos` (e20), pour les mêmes raisons : écrivain DÉDIÉ
# et ADDITIF (`record_track_spotify_ids`), retrait EXPLICITE
# (`forget_track_spotify_id`), lecture groupée par artiste. Aucun producteur ne
# connaît la liste complète — Genius en donne un, Kworb un autre, le scraper un
# troisième.
#
# `tracks.spotify_id` RESTE et désigne l'ID PRINCIPAL (celui qu'on ouvre, celui
# qu'on interroge) : même partage des rôles que `tracks.youtube_url` face à
# `track_videos`. `is_primary` en est la matérialisation, maintenue par
# l'écrivain — le verdict reste la colonne, jamais ce drapeau.
#
# ⚠️ Plusieurs IDs = plusieurs ÉDITIONS DU MÊME enregistrement : elles portent
# les MÊMES compteurs. On visite UNE page (l'ID principal) mais on RECONNAÎT
# toutes les IDs, et on ne SOMME JAMAIS deux éditions (JOURNAL 2026-09-05 :
# 99 M au lieu de 50 M sur « Bitume Caviar »).
track_spotify_ids = Table(
    "track_spotify_ids",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("track_id", Integer, ForeignKey("tracks.id"), nullable=False),
    Column("spotify_id", Text, nullable=False),
    Column("source", Text),
    Column("is_primary", _BOOL, server_default=_FALSE),
    Column("seen_at", TIMESTAMP),
    UniqueConstraint("track_id", "spotify_id"),
    sqlite_autoincrement=True,
)


# Index de la clé d'enregistrement (e23). `genius_id` identifie l'ENREGISTREMENT
# là où `tracks.id` identifie la ligne d'UN artiste : c'est par lui que se
# retrouvent les lignes sœurs d'un même morceau (le titre chez son auteur, la
# ligne « feat » chez l'invité). La colonne était nue, ce parcours est désormais
# fait à chaque écriture de donnée d'enregistrement.
Index("ix_tracks_genius_id", tracks.c.genius_id)


# Vidéos YouTube d'un morceau (e20). Un morceau en a souvent DEUX — le clip
# officiel et la version « audio » servie par YouTube Music (chaîne « - Topic »)
# — et les colonnes `tracks.youtube_*` n'en tenaient qu'UNE : la seconde était
# perdue, avec ses vues (mesuré sur « Magot » et « Déluge »).
#
# `video_id` est la clé métier (11 caractères), `url` n'en est que la forme
# affichable : deux URL différentes (youtu.be / watch?v=) désignent la même
# vidéo, l'unicité porte donc sur (track_id, video_id).
#
# `kind` ∈ clip · audio · show · unknown (cf. `youtube_utils.classify_video_kind`).
# `source` ∈ genius_media · search_auto · ytm_album · manual — la provenance du
# LIEN, qui décide de sa priorité (cf. `update_track_youtube_url`).
#
# Les colonnes `tracks.youtube_url` / `youtube_video_*` RESTENT en place : elles
# portent la vidéo « principale » affichée par la GUI. Cette table les complète,
# elle ne les remplace pas encore.
track_videos = Table(
    "track_videos",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("track_id", Integer, ForeignKey("tracks.id"), nullable=False),
    Column("video_id", Text, nullable=False),
    Column("url", Text),
    Column("kind", Text),
    Column("source", Text),
    Column("views", Integer),
    Column("views_updated", TIMESTAMP),
    Column("created_at", TIMESTAMP),
    # Titre de la vidéo (e21), EN FIN de table (ordre d'`add_column` Alembic).
    # C'est lui qui rend VÉRIFIABLE une vidéo partagée par plusieurs morceaux :
    # « B.B. Jacques - Donjon & 2h22 » est un clip double légitime, un titre qui
    # ne nomme qu'un morceau trahit un lien fautif.
    Column("title", Text),
    UniqueConstraint("track_id", "video_id"),
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
