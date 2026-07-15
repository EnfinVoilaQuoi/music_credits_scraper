"""Tests de la config pydantic-settings.

Vérifie : le fix du P1 « LOG_LEVEL en dur » (désormais piloté par l'env),
la validation typée (crash au démarrage si champ invalide — AUDIT §6), la
priorité env Windows > .env, et la rétro-compat des noms module-niveau
historiques importés par ~54 fichiers.

`_env_file=None` isole du vrai .env du projet pour rendre les tests
déterministes ; l'ambiant est neutralisé via monkeypatch quand nécessaire.
"""

import pytest
from pydantic import ValidationError

import src.config as config
from src.config import Settings


# ── Fix P1 : LOG_LEVEL n'est plus « DEBUG » en dur ─────────────────────────────
def test_log_level_default_is_info(monkeypatch):
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    assert Settings(_env_file=None).log_level == "INFO"


def test_log_level_read_from_env(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "warning")  # normalisé en majuscules
    assert Settings(_env_file=None).log_level == "WARNING"


def test_invalid_log_level_crashes(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "bogus")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


# ── Validation typée : un champ invalide crashe (pas de None silencieux) ───────
def test_invalid_theme_crashes(monkeypatch):
    monkeypatch.setenv("THEME", "purple")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_type_coercion():
    s = Settings(_env_file=None, max_retries="5", delay_between_requests="2.5")
    assert s.max_retries == 5 and isinstance(s.max_retries, int)
    assert s.delay_between_requests == 2.5 and isinstance(s.delay_between_requests, float)


def test_debug_bool_parsing():
    assert Settings(_env_file=None, debug="true").debug is True
    assert Settings(_env_file=None, debug="False").debug is False


# ── Priorité : env Windows (os.environ) > .env ─────────────────────────────────
def test_env_wins_over_dotenv(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("GENIUS_API_KEY=from_dotenv\n", encoding="utf-8")
    monkeypatch.setenv("GENIUS_API_KEY", "from_windows")
    assert Settings(_env_file=str(env_path)).genius_api_key == "from_windows"


def test_dotenv_used_as_fallback(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("GETSONGBPM_API_KEY=only_in_dotenv\n", encoding="utf-8")
    monkeypatch.delenv("GETSONGBPM_API_KEY", raising=False)
    assert Settings(_env_file=str(env_path)).getsongbpm_api_key == "only_in_dotenv"


def test_extra_dotenv_keys_ignored(tmp_path, monkeypatch):
    # .env réel contient des clés hors Settings (MUSIXMATCH_*, GENIUS_CDP_URL…)
    env_path = tmp_path / ".env"
    env_path.write_text("MUSIXMATCH_USER_TOKEN=xyz\nGENIUS_CDP_URL=http://x\n", encoding="utf-8")
    Settings(_env_file=str(env_path))  # ne doit pas lever


# ── Rétro-compat : noms module-niveau historiques toujours exposés ─────────────
def test_backward_compat_names_present():
    expected = [
        "GENIUS_API_KEY",
        "DISCOGS_TOKEN",
        "LAST_FM_API_KEY",
        "SPOTIFY_CLIENT_ID",
        "SPOTIFY_CLIENT_SECRET",
        "GETSONGBPM_API_KEY",
        "YOUTUBE_API_KEY",
        "BPMFINDER_EMAIL",
        "BPMFINDER_PASSWORD",
        "BPMFINDER_SESSION_FILE",
        "DEBUG",
        "LOG_LEVEL",
        "SELENIUM_TIMEOUT",
        "MAX_RETRIES",
        "DELAY_BETWEEN_REQUESTS",
        "GENIUS_TIMEOUT",
        "GENIUS_RETRIES",
        "GENIUS_SLEEP_TIME",
        "WINDOW_WIDTH",
        "WINDOW_HEIGHT",
        "THEME",
        "YOUTUBE_QUOTA_LIMIT",
        "YOUTUBE_CACHE_TTL_HOURS",
        "YOUTUBE_AUTO_SELECT_ALBUM_TRACKS",
        "YOUTUBE_VERIFY_OFFICIAL_CHANNELS",
        "YOUTUBE_CONFIDENCE_THRESHOLD",
        "YOUTUBE_PERSIST_CONFIDENCE",
        "YTM_IDENTITY_MIN_MATCHED",
        "YTM_IDENTITY_MIN_RATIO",
        "BASE_DIR",
        "DATA_DIR",
        "ARTISTS_DIR",
        "LOGS_DIR",
        "DATABASE_URL",
        "DATA_PATH",
    ]
    missing = [name for name in expected if not hasattr(config, name)]
    assert not missing, f"noms de config manquants (rétro-compat cassée) : {missing}"


def test_compat_types_preserved():
    assert isinstance(config.SELENIUM_TIMEOUT, int)
    assert isinstance(config.MAX_RETRIES, int)
    assert isinstance(config.THEME, str) and config.THEME in {"dark", "light"}
    assert config.LOG_LEVEL in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def test_ytm_identity_thresholds_defaults():
    s = Settings(_env_file=None)
    assert s.ytm_identity_min_matched == 2
    assert s.ytm_identity_min_ratio == 0.3


def test_ytm_identity_thresholds_read_from_env(monkeypatch):
    monkeypatch.setenv("YTM_IDENTITY_MIN_MATCHED", "3")
    monkeypatch.setenv("YTM_IDENTITY_MIN_RATIO", "0.5")
    s = Settings(_env_file=None)
    assert s.ytm_identity_min_matched == 3 and isinstance(s.ytm_identity_min_matched, int)
    assert s.ytm_identity_min_ratio == 0.5


def test_derived_paths():
    assert config.DATABASE_URL.endswith("data/music_credits.db")
    assert str(config.DATA_DIR) == config.DATA_PATH
    assert config.BPMFINDER_SESSION_FILE.name == ".bpmfinder_session.json"
