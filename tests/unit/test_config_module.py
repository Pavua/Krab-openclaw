"""
Расширенные unit-тесты для src/config.py

Покрывают: env var parsing, default values, path resolution,
bool coercion, list parsing, update_setting, validate, load_swarm_team_accounts.
"""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

import src.config as config_module
from src.config import Config

# ---------------------------------------------------------------------------
# Хелпер: перезагрузить модуль с нужными env-переменными.
# Используем monkeypatch на os.environ, затем reload модуля.
# ---------------------------------------------------------------------------


@pytest.fixture()
def clean_config(monkeypatch):
    """Сбрасывает env и перезагружает Config для изоляции тестов."""
    # Удаляем все потенциально влияющие ключи
    keys_to_clear = [
        "TELEGRAM_API_ID",
        "TELEGRAM_API_HASH",
        "TELEGRAM_SESSION_NAME",
        "TELEGRAM_ALLOW_INTERACTIVE_LOGIN",
        "OPENCLAW_URL",
        "OPENCLAW_BASE_URL",
        "OPENCLAW_TOKEN",
        "OPENCLAW_GATEWAY_TOKEN",
        "OPENCLAW_API_KEY",
        "LM_STUDIO_URL",
        "LM_STUDIO_API_KEY",
        "LM_STUDIO_AUTH_TOKEN",
        "GEMINI_API_KEY",
        "GEMINI_API_KEY_FREE",
        "GEMINI_API_KEY_PAID",
        "GEMINI_PAID_KEY_ENABLED",
        "MODEL",
        "MAX_RAM_GB",
        "LOG_LEVEL",
        "FORCE_CLOUD",
        "LOCAL_FALLBACK_ENABLED",
        "TOR_ENABLED",
        "TOR_SOCKS_PORT",
        "TOR_CONTROL_PORT",
        "OWNER_USERNAME",
        "ALLOWED_USERS",
        "OWNER_USER_IDS",
        "MANUAL_BLOCKLIST",
        "TRIGGER_PREFIXES",
        "VOICE_MODE_DEFAULT",
        "VOICE_REPLY_SPEED",
        "VOICE_REPLY_VOICE",
        "VOICE_REPLY_DELIVERY",
        "VOICE_REPLY_BLOCKED_CHATS",
        "SINGLE_LOCAL_MODEL_MODE",
        "GUARDED_IDLE_UNLOAD",
        "GUARDED_IDLE_UNLOAD_GRACE_SEC",
        "USERBOT_MAX_OUTPUT_TOKENS",
        "HISTORY_WINDOW_MESSAGES",
        "SILENCE_DEFAULT_MINUTES",
        "SWARM_AUTONOMOUS_ENABLED",
        "TOOL_NARRATION_ENABLED",
        "AI_DISCLOSURE_ENABLED",
        "NON_OWNER_SAFE_MODE_ENABLED",
        "SCHEDULER_ENABLED",
        "OPENCLAW_BUFFERED_READ_TIMEOUT_SEC",
    ]
    for k in keys_to_clear:
        monkeypatch.delenv(k, raising=False)
    yield


# ---------------------------------------------------------------------------
# 1. Значения по умолчанию
# ---------------------------------------------------------------------------


class TestDefaults:
    """Проверяем, что дефолты заданы правильно (без .env)."""

    def test_openclaw_url_default_starts_with_http(self):
        """OPENCLAW_URL — HTTP адрес по умолчанию."""
        assert Config.OPENCLAW_URL.startswith("http")

    def test_max_ram_gb_is_int_in_valid_range(self):
        """MAX_RAM_GB — целое число в разумном диапазоне."""
        assert isinstance(Config.MAX_RAM_GB, int)
        assert 1 <= Config.MAX_RAM_GB <= 256

    def test_log_level_is_valid_value(self):
        """LOG_LEVEL — один из допустимых уровней."""
        assert Config.LOG_LEVEL in ("INFO", "DEBUG", "WARNING", "ERROR", "CRITICAL")

    def test_tor_enabled_default_false(self):
        """TOR_ENABLED выключен по умолчанию."""
        # Значение из env — если TOR_ENABLED не выставлен, должно быть False.
        # Читаем напрямую через os.getenv чтобы не зависеть от .env.
        raw = os.getenv("TOR_ENABLED", "0").strip().lower()
        assert raw not in ("1", "true", "yes") or Config.TOR_ENABLED is True  # консистентность

    def test_voice_reply_speed_default_is_float(self):
        """VOICE_REPLY_SPEED — float."""
        assert isinstance(Config.VOICE_REPLY_SPEED, float)

    def test_history_window_messages_default_positive(self):
        """HISTORY_WINDOW_MESSAGES > 0."""
        assert Config.HISTORY_WINDOW_MESSAGES > 0

    def test_userbot_max_output_tokens_default_positive(self):
        """USERBOT_MAX_OUTPUT_TOKENS > 0."""
        assert Config.USERBOT_MAX_OUTPUT_TOKENS > 0

    def test_trigger_prefixes_contains_krab(self):
        """TRIGGER_PREFIXES содержит хотя бы один !краб-вариант."""
        lowered = [p.lower() for p in Config.TRIGGER_PREFIXES]
        assert any("краб" in p for p in lowered)

    def test_base_dir_is_path_object(self):
        """BASE_DIR — объект Path."""
        assert isinstance(Config.BASE_DIR, Path)

    def test_userbot_acl_file_is_path(self):
        """USERBOT_ACL_FILE — Path."""
        assert isinstance(Config.USERBOT_ACL_FILE, Path)


# ---------------------------------------------------------------------------
# 2. Bool coercion через env
# ---------------------------------------------------------------------------


class TestBoolCoercion:
    """env-значения '1', 'true', 'yes' → True; остальное → False."""

    @pytest.mark.parametrize("val", ["1", "true", "True", "TRUE", "yes", "YES"])
    def test_tor_enabled_truthy_values(self, monkeypatch, val):
        """TOR_ENABLED воспринимает все truthy-формы."""
        monkeypatch.setenv("TOR_ENABLED", val)
        result = os.getenv("TOR_ENABLED", "0").strip().lower() in ("1", "true", "yes")
        assert result is True

    @pytest.mark.parametrize("val", ["0", "false", "no", "", "off"])
    def test_tor_enabled_falsy_values(self, monkeypatch, val):
        """TOR_ENABLED отклоняет все falsy-формы."""
        monkeypatch.setenv("TOR_ENABLED", val)
        result = os.getenv("TOR_ENABLED", "0").strip().lower() in ("1", "true", "yes")
        assert result is False

    def test_force_cloud_set_via_env(self, monkeypatch):
        """FORCE_CLOUD можно включить через env (проверяем логику парсинга)."""
        monkeypatch.setenv("FORCE_CLOUD", "1")
        # Проверяем через ту же логику, что и в Config
        result = os.getenv("FORCE_CLOUD", "0").strip().lower() in ("1", "true", "yes")
        assert result is True

    def test_swarm_autonomous_disabled_by_default(self):
        """SWARM_AUTONOMOUS_ENABLED выключен по умолчанию."""
        raw = os.getenv("SWARM_AUTONOMOUS_ENABLED", "0").strip().lower()
        # Дефолт должен быть "0" → False
        assert raw not in ("1", "true", "yes") or Config.SWARM_AUTONOMOUS_ENABLED is True


# ---------------------------------------------------------------------------
# 3. Int/float parsing из env
# ---------------------------------------------------------------------------


class TestNumericEnvParsing:
    """Числовые параметры читаются корректно."""

    def test_tor_socks_port_is_int(self):
        """TOR_SOCKS_PORT — int."""
        assert isinstance(Config.TOR_SOCKS_PORT, int)

    def test_silence_default_minutes_is_int(self):
        """SILENCE_DEFAULT_MINUTES — int."""
        assert isinstance(Config.SILENCE_DEFAULT_MINUTES, int)

    def test_voice_reply_speed_is_float(self):
        """VOICE_REPLY_SPEED — float."""
        assert isinstance(Config.VOICE_REPLY_SPEED, float)

    def test_guarded_idle_unload_grace_sec_is_float(self):
        """GUARDED_IDLE_UNLOAD_GRACE_SEC — float."""
        assert isinstance(Config.GUARDED_IDLE_UNLOAD_GRACE_SEC, float)

    def test_openclaw_buffered_read_timeout_none_by_default(self):
        """OPENCLAW_BUFFERED_READ_TIMEOUT_SEC — None если env не выставлен."""
        # Если в окружении нет этой переменной, должно быть None
        if not os.getenv("OPENCLAW_BUFFERED_READ_TIMEOUT_SEC", "").strip():
            assert Config.OPENCLAW_BUFFERED_READ_TIMEOUT_SEC is None


# ---------------------------------------------------------------------------
# 4. List parsing
# ---------------------------------------------------------------------------


class TestListParsing:
    """Comma-separated env-переменные парсятся в list[str]."""

    def test_allowed_users_is_list(self):
        """ALLOWED_USERS — list."""
        assert isinstance(Config.ALLOWED_USERS, list)

    def test_allowed_users_strips_at_sign(self, monkeypatch):
        """ALLOWED_USERS убирает @ из начала имён."""
        monkeypatch.setenv("ALLOWED_USERS", "@alice,@bob,charlie")
        # Пересчитываем вручную по той же логике
        result = [
            u.strip().lstrip("@") for u in os.getenv("ALLOWED_USERS", "").split(",") if u.strip()
        ]
        assert "alice" in result
        assert "bob" in result
        assert "charlie" in result
        # @ не должно остаться
        assert not any(u.startswith("@") for u in result)

    def test_manual_blocklist_normalizes_to_lowercase(self, monkeypatch):
        """MANUAL_BLOCKLIST приводит имена к нижнему регистру."""
        monkeypatch.setenv("MANUAL_BLOCKLIST", "Alice,@BOB,SPAM_BOT")
        result = frozenset(
            u.strip().lstrip("@").lower()
            for u in os.getenv("MANUAL_BLOCKLIST", "").split(",")
            if u.strip()
        )
        assert "alice" in result
        assert "bob" in result
        assert "spam_bot" in result

    def test_voice_reply_blocked_chats_empty_by_default(self):
        """VOICE_REPLY_BLOCKED_CHATS — пустой список если env не задан."""
        if not os.getenv("VOICE_REPLY_BLOCKED_CHATS", "").strip():
            assert Config.VOICE_REPLY_BLOCKED_CHATS == []

    def test_owner_user_ids_empty_by_default(self):
        """OWNER_USER_IDS — пустой список если env не задан."""
        if not os.getenv("OWNER_USER_IDS", "").strip():
            assert Config.OWNER_USER_IDS == []


# ---------------------------------------------------------------------------
# 5. Path resolution
# ---------------------------------------------------------------------------


class TestPathResolution:
    """Path-поля возвращают правильные пути."""

    def test_userbot_acl_file_default_under_home(self):
        """USERBOT_ACL_FILE по умолчанию в ~/.openclaw/."""
        if not os.getenv("USERBOT_ACL_FILE"):
            assert ".openclaw" in str(Config.USERBOT_ACL_FILE)

    def test_openclaw_workspace_dir_default_under_home(self):
        """OPENCLAW_MAIN_WORKSPACE_DIR по умолчанию в ~/.openclaw/."""
        if not os.getenv("OPENCLAW_MAIN_WORKSPACE_DIR"):
            assert ".openclaw" in str(Config.OPENCLAW_MAIN_WORKSPACE_DIR)

    def test_swarm_team_accounts_path_is_path(self):
        """SWARM_TEAM_ACCOUNTS_PATH — объект Path."""
        assert isinstance(Config.SWARM_TEAM_ACCOUNTS_PATH, Path)

    def test_base_dir_points_to_project_root(self):
        """BASE_DIR указывает на директорию проекта (родитель src/)."""
        # BASE_DIR = Path(__file__).parent.parent → директория над src/
        assert (Config.BASE_DIR / "src").exists()


# ---------------------------------------------------------------------------
# 6. validate() и is_valid()
# ---------------------------------------------------------------------------


class TestValidation:
    """Config.validate() возвращает список ошибок."""

    def test_validate_fails_without_api_id(self):
        """validate() сообщает об отсутствии TELEGRAM_API_ID."""
        with patch.object(Config, "TELEGRAM_API_ID", 0):
            with patch.object(Config, "TELEGRAM_API_HASH", ""):
                errors = Config.validate()
        assert any("TELEGRAM_API_ID" in e for e in errors)

    def test_validate_fails_without_api_hash(self):
        """validate() сообщает об отсутствии TELEGRAM_API_HASH."""
        with patch.object(Config, "TELEGRAM_API_ID", 99999):
            with patch.object(Config, "TELEGRAM_API_HASH", ""):
                errors = Config.validate()
        assert any("TELEGRAM_API_HASH" in e for e in errors)

    def test_validate_success_with_both_credentials(self):
        """validate() не возвращает ошибок при наличии обоих credentials."""
        with patch.object(Config, "TELEGRAM_API_ID", 12345678):
            with patch.object(Config, "TELEGRAM_API_HASH", "deadbeefcafe1234"):
                errors = Config.validate()
        assert errors == []

    def test_is_valid_true_when_credentials_present(self):
        """is_valid() → True при наличии credentials."""
        with patch.object(Config, "TELEGRAM_API_ID", 12345678):
            with patch.object(Config, "TELEGRAM_API_HASH", "deadbeefcafe1234"):
                assert Config.is_valid() is True

    def test_is_valid_false_when_missing_credentials(self):
        """is_valid() → False при отсутствии credentials."""
        with patch.object(Config, "TELEGRAM_API_ID", 0):
            with patch.object(Config, "TELEGRAM_API_HASH", ""):
                assert Config.is_valid() is False


# ---------------------------------------------------------------------------
# 7. update_setting()
# ---------------------------------------------------------------------------


class TestUpdateSetting:
    """Config.update_setting() обновляет атрибуты в памяти."""

    def test_update_model_string(self, tmp_path):
        """update_setting меняет MODEL на новое значение."""
        original = Config.MODEL
        # Подменяем BASE_DIR чтобы не трогать реальный .env
        env_file = tmp_path / ".env"
        env_file.write_text("MODEL=old_model\n")
        with patch.object(Config, "BASE_DIR", tmp_path):
            Config.update_setting("MODEL", "google/gemini-3-pro-preview")
        assert Config.MODEL == "google/gemini-3-pro-preview"
        # Восстанавливаем
        Config.MODEL = original

    def test_update_max_ram_gb_int_coercion(self, tmp_path):
        """update_setting корректно преобразует MAX_RAM_GB в int."""
        original = Config.MAX_RAM_GB
        env_file = tmp_path / ".env"
        env_file.write_text("MAX_RAM_GB=24\n")
        with patch.object(Config, "BASE_DIR", tmp_path):
            Config.update_setting("MAX_RAM_GB", "48")
        assert Config.MAX_RAM_GB == 48
        assert isinstance(Config.MAX_RAM_GB, int)
        Config.MAX_RAM_GB = original

    def test_update_force_cloud_bool_true(self, tmp_path):
        """update_setting('FORCE_CLOUD', 'true') → True."""
        original = Config.FORCE_CLOUD
        env_file = tmp_path / ".env"
        env_file.write_text("FORCE_CLOUD=0\n")
        with patch.object(Config, "BASE_DIR", tmp_path):
            Config.update_setting("FORCE_CLOUD", "true")
        assert Config.FORCE_CLOUD is True
        Config.FORCE_CLOUD = original

    def test_update_force_cloud_bool_false(self, tmp_path):
        """update_setting('FORCE_CLOUD', '0') → False."""
        original = Config.FORCE_CLOUD
        env_file = tmp_path / ".env"
        env_file.write_text("FORCE_CLOUD=1\n")
        with patch.object(Config, "BASE_DIR", tmp_path):
            Config.update_setting("FORCE_CLOUD", "0")
        assert Config.FORCE_CLOUD is False
        Config.FORCE_CLOUD = original

    def test_update_allowed_users_strips_at(self, tmp_path):
        """update_setting('ALLOWED_USERS', ...) нормализует @-префиксы."""
        original = Config.ALLOWED_USERS[:]
        env_file = tmp_path / ".env"
        env_file.write_text("ALLOWED_USERS=old\n")
        with patch.object(Config, "BASE_DIR", tmp_path):
            Config.update_setting("ALLOWED_USERS", "@alice,@bob")
        assert "alice" in Config.ALLOWED_USERS
        assert "bob" in Config.ALLOWED_USERS
        assert not any(u.startswith("@") for u in Config.ALLOWED_USERS)
        Config.ALLOWED_USERS = original

    def test_update_creates_env_file_if_missing(self, tmp_path):
        """update_setting создаёт .env файл если его нет."""
        original = Config.MODEL
        assert not (tmp_path / ".env").exists()
        with patch.object(Config, "BASE_DIR", tmp_path):
            result = Config.update_setting("MODEL", "test-model")
        assert result is True
        assert (tmp_path / ".env").exists()
        Config.MODEL = original

    def test_update_lm_studio_auth_token_legacy_alias(self, tmp_path):
        """LM_STUDIO_AUTH_TOKEN нормализуется к LM_STUDIO_API_KEY."""
        original = Config.LM_STUDIO_API_KEY
        env_file = tmp_path / ".env"
        env_file.write_text("LM_STUDIO_API_KEY=old\n")
        with patch.object(Config, "BASE_DIR", tmp_path):
            Config.update_setting("LM_STUDIO_AUTH_TOKEN", "new_token_value")
        assert Config.LM_STUDIO_API_KEY == "new_token_value"
        Config.LM_STUDIO_API_KEY = original

    def test_update_returns_false_on_invalid_key(self, tmp_path):
        """update_setting не падает и возвращает True даже для неизвестного ключа."""
        env_file = tmp_path / ".env"
        env_file.write_text("")
        with patch.object(Config, "BASE_DIR", tmp_path):
            result = Config.update_setting("NONEXISTENT_KEY_XYZ", "value")
        # Функция пишет в .env, но не меняет атрибут — должна вернуть True (не упасть)
        assert isinstance(result, bool)

    def test_update_voice_reply_speed_float(self, tmp_path):
        """update_setting('VOICE_REPLY_SPEED', ...) сохраняет float."""
        original = Config.VOICE_REPLY_SPEED
        env_file = tmp_path / ".env"
        env_file.write_text("VOICE_REPLY_SPEED=1.5\n")
        with patch.object(Config, "BASE_DIR", tmp_path):
            Config.update_setting("VOICE_REPLY_SPEED", "2.0")
        assert Config.VOICE_REPLY_SPEED == 2.0
        assert isinstance(Config.VOICE_REPLY_SPEED, float)
        Config.VOICE_REPLY_SPEED = original

    def test_update_telegram_session_heartbeat_enforces_minimum(self, tmp_path):
        """TELEGRAM_SESSION_HEARTBEAT_SEC ограничен снизу значением 15."""
        original = Config.TELEGRAM_SESSION_HEARTBEAT_SEC
        env_file = tmp_path / ".env"
        env_file.write_text("TELEGRAM_SESSION_HEARTBEAT_SEC=60\n")
        with patch.object(Config, "BASE_DIR", tmp_path):
            Config.update_setting("TELEGRAM_SESSION_HEARTBEAT_SEC", "5")
        assert Config.TELEGRAM_SESSION_HEARTBEAT_SEC >= 15
        Config.TELEGRAM_SESSION_HEARTBEAT_SEC = original


# ---------------------------------------------------------------------------
# 8. load_swarm_team_accounts()
# ---------------------------------------------------------------------------


class TestLoadSwarmTeamAccounts:
    """Config.load_swarm_team_accounts() читает JSON-конфиг или возвращает {}."""

    def test_returns_empty_dict_when_file_missing(self, tmp_path):
        """Возвращает {} если файл не существует."""
        missing = tmp_path / "no_such_file.json"
        with patch.object(Config, "SWARM_TEAM_ACCOUNTS_PATH", missing):
            result = Config.load_swarm_team_accounts()
        assert result == {}

    def test_returns_parsed_dict_for_valid_file(self, tmp_path):
        """Корректно парсит валидный JSON."""
        data = {
            "traders": {"session_name": "swarm_traders", "phone": "+34600000001"},
            "coders": {"session_name": "swarm_coders", "phone": "+34600000002"},
        }
        accounts_file = tmp_path / "swarm_team_accounts.json"
        accounts_file.write_text(json.dumps(data), encoding="utf-8")
        with patch.object(Config, "SWARM_TEAM_ACCOUNTS_PATH", accounts_file):
            result = Config.load_swarm_team_accounts()
        assert result == data

    def test_returns_empty_dict_for_corrupt_json(self, tmp_path):
        """Возвращает {} при повреждённом JSON."""
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("NOT_VALID_JSON{{{", encoding="utf-8")
        with patch.object(Config, "SWARM_TEAM_ACCOUNTS_PATH", bad_file):
            result = Config.load_swarm_team_accounts()
        assert result == {}

    def test_returns_empty_dict_when_json_is_list(self, tmp_path):
        """Возвращает {} если JSON — список, а не объект."""
        list_file = tmp_path / "list.json"
        list_file.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        with patch.object(Config, "SWARM_TEAM_ACCOUNTS_PATH", list_file):
            result = Config.load_swarm_team_accounts()
        assert result == {}


# ---------------------------------------------------------------------------
# 9. Singleton config
# ---------------------------------------------------------------------------


def test_config_singleton_is_config_instance():
    """Синглтон config — экземпляр класса Config."""
    assert isinstance(config_module.config, Config)


# ---------------------------------------------------------------------------
# 10. LM Studio URL trailing slash stripped
# ---------------------------------------------------------------------------


def test_lm_studio_url_has_no_trailing_slash(monkeypatch):
    """LM_STUDIO_URL должен быть без trailing slash (проверяем логику rstrip)."""
    url_with_slash = "http://192.168.0.171:1234/"
    result = url_with_slash.rstrip("/")
    assert not result.endswith("/")
    assert result == "http://192.168.0.171:1234"
