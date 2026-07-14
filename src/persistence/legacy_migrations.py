"""Copie GELÉE des migrations de schéma legacy (`PRAGMA user_version`).

Ce module fige la séquence historique qui amène n'importe quelle base
pré-Alembic à `user_version = 46` — état strictement équivalent à la révision
Alembic de base `e1_initial_schema` (schéma complet). Il ne bouge PLUS : toute
évolution de schéma passe désormais par une révision Alembic (phase E3+).

Il a deux usages :
  - avant E3b, `db.py` l'applique au démarrage (chemin legacy, comportement
    constant) ;
  - le bootstrap Alembic (`bootstrap.py`) s'en sert pour rattraper un vieux
    backup restauré (`user_version < 46`, colonnes manquantes) AVANT de le
    stamper à `e1_initial_schema` — sans quoi le stamp mentirait sur le schéma.

Séquence FIGÉE : une entrée par évolution, dans l'ordre, JAMAIS modifiée après
coup (on n'ajoute plus rien ici). Sur une base passée par l'ancien mécanisme
(`user_version` encore 0, colonnes déjà présentes), chaque `ADD COLUMN` est
sauté si sa colonne existe déjà, puis `user_version` est posé. Une fois
`user_version = N`, ces migrations ne sont plus jamais rejouées.
"""

import re

from src.utils.logger import get_logger

logger = get_logger(__name__)


# user_version 46 (dernière entrée ci-dessous) ≡ révision Alembic e1_initial_schema.
LEGACY_HEAD_USER_VERSION = 46

_MIGRATIONS: list[tuple[int, str]] = [
    # artists — auditeurs mensuels + totaux Kworb
    (1, "ALTER TABLE artists ADD COLUMN spotify_monthly_listeners INTEGER"),
    (2, "ALTER TABLE artists ADD COLUMN ytm_monthly_listeners INTEGER"),
    (3, "ALTER TABLE artists ADD COLUMN ytm_channel_id TEXT"),
    (4, "ALTER TABLE artists ADD COLUMN kworb_total_streams INTEGER"),
    (5, "ALTER TABLE artists ADD COLUMN kworb_daily_streams INTEGER"),
    (6, "ALTER TABLE artists ADD COLUMN kworb_lead_streams INTEGER"),
    (7, "ALTER TABLE artists ADD COLUMN kworb_feat_streams INTEGER"),
    (8, "ALTER TABLE artists ADD COLUMN kworb_updated TIMESTAMP"),
    # tracks — colonnes historiques (filet pour bases intermédiaires) puis
    # enrichissements (isrc, vote BPM, key/mode, paroles synchro, streams…)
    (9, "ALTER TABLE tracks ADD COLUMN is_featuring BOOLEAN DEFAULT 0"),
    (10, "ALTER TABLE tracks ADD COLUMN primary_artist_name TEXT"),
    (11, "ALTER TABLE tracks ADD COLUMN featured_artists TEXT"),
    (12, "ALTER TABLE tracks ADD COLUMN lyrics TEXT"),
    (13, "ALTER TABLE tracks ADD COLUMN has_lyrics BOOLEAN DEFAULT 0"),
    (14, "ALTER TABLE tracks ADD COLUMN lyrics_scraped_at TIMESTAMP"),
    (15, "ALTER TABLE tracks ADD COLUMN isrc TEXT"),
    (16, "ALTER TABLE tracks ADD COLUMN bpm_source TEXT"),
    (17, "ALTER TABLE tracks ADD COLUMN bpm_confidence INTEGER"),
    (18, "ALTER TABLE tracks ADD COLUMN key_mode_source TEXT"),
    (19, "ALTER TABLE tracks ADD COLUMN reccobeats_resolution TEXT"),
    (20, "ALTER TABLE tracks ADD COLUMN secondary_role TEXT"),
    (21, "ALTER TABLE tracks ADD COLUMN bpm_alt INTEGER"),
    (22, "ALTER TABLE tracks ADD COLUMN lyrics_source TEXT"),
    (23, "ALTER TABLE tracks ADD COLUMN lyrics_synced TEXT"),
    (24, "ALTER TABLE tracks ADD COLUMN lyrics_synced_source TEXT"),
    (25, "ALTER TABLE tracks ADD COLUMN lyrics_synced_confidence INTEGER"),
    (26, "ALTER TABLE tracks ADD COLUMN relationships TEXT"),
    (27, "ALTER TABLE tracks ADD COLUMN certifications TEXT"),
    (28, "ALTER TABLE tracks ADD COLUMN album_certifications TEXT"),
    (29, "ALTER TABLE tracks ADD COLUMN musical_key TEXT"),
    (30, "ALTER TABLE tracks ADD COLUMN key TEXT"),
    (31, "ALTER TABLE tracks ADD COLUMN mode TEXT"),
    (32, "ALTER TABLE tracks ADD COLUMN time_signature TEXT"),
    (33, "ALTER TABLE tracks ADD COLUMN anecdotes TEXT"),
    (34, "ALTER TABLE tracks ADD COLUMN spotify_page_title TEXT"),
    (35, "ALTER TABLE tracks ADD COLUMN spotify_streams INTEGER"),
    (36, "ALTER TABLE tracks ADD COLUMN spotify_daily_streams INTEGER"),
    (37, "ALTER TABLE tracks ADD COLUMN spotify_streams_updated TIMESTAMP"),
    (38, "ALTER TABLE tracks ADD COLUMN ytm_streams INTEGER"),
    (39, "ALTER TABLE tracks ADD COLUMN ytm_streams_updated TIMESTAMP"),
    (40, "ALTER TABLE tracks ADD COLUMN youtube_url TEXT"),
    (41, "ALTER TABLE tracks ADD COLUMN youtube_url_source TEXT"),
    (42, "ALTER TABLE tracks ADD COLUMN album_override INTEGER"),
    # albums — streams YTM + éditions Spotify agrégées
    (43, "ALTER TABLE albums ADD COLUMN ytm_streams INTEGER"),
    (44, "ALTER TABLE albums ADD COLUMN ytm_streams_updated TIMESTAMP"),
    (45, "ALTER TABLE albums ADD COLUMN spotify_album_ids TEXT"),
    # Backfill : historiquement youtube_url n'était écrit que par Genius (media).
    # Idempotent (clause WHERE) — ne s'exécute qu'une fois grâce au versionnage.
    (
        46,
        "UPDATE tracks SET youtube_url_source = 'genius_media' "
        "WHERE youtube_url IS NOT NULL AND youtube_url != '' "
        "AND (youtube_url_source IS NULL OR youtube_url_source = '')",
    ),
]

_ADD_COLUMN_RE = re.compile(r"ALTER TABLE (\w+) ADD COLUMN (\w+)", re.IGNORECASE)


def _table_columns(cursor, table: str) -> set[str]:
    cursor.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}


def run_migrations(cursor) -> None:
    """Applique les migrations de schéma legacy en attente (voir _MIGRATIONS)."""
    current = cursor.execute("PRAGMA user_version").fetchone()[0]
    latest = _MIGRATIONS[-1][0] if _MIGRATIONS else 0
    if current >= latest:
        return

    # Colonnes présentes par table — consultées uniquement pour le bootstrap
    # depuis user_version = 0 (base déjà migrée par l'ancien mécanisme).
    present: dict[str, set[str]] = {}

    for version, sql in _MIGRATIONS:
        if version <= current:
            continue
        m = _ADD_COLUMN_RE.match(sql)
        if m:
            table, column = m.group(1), m.group(2)
            if table not in present:
                present[table] = _table_columns(cursor, table)
            if column not in present[table]:
                cursor.execute(sql)
                present[table].add(column)
                logger.info(f"✅ Migration {version} : {table}.{column} ajoutée")
        else:
            # Migration de données (backfill), idempotente par sa clause WHERE
            cursor.execute(sql)
            logger.info(f"✅ Migration {version} appliquée (données)")
        cursor.execute(f"PRAGMA user_version = {version}")
