# -*- coding: utf-8 -*-
"""
Базовые тесты Config class — update_setting, парсинг env, load_swarm_team_accounts.

Охватывает:
- булевы поля (различные truthy/falsy варианты);
- int/float поля с минимумом (TELEGRAM_SESSION_HEARTBEAT_SEC);
- списочные поля (OWNER_USER_IDS, ALLOWED_USERS);
- TOOL_NARRATION_ENABLED через update_setting;
- load_swarm_team_accounts: отсутствующий файл, корректный JSON, битый JSON, не-dict;
- BASE_DIR — Path к корню проекта;
- update_setting возвращает True / пишет .env;
- update_setting для неизвестного ключа (не падает, возвращает True, пишет .env).
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import src.config as config_module

# ---------------------------------------------------------------------------
# Вспомогательная функция — создать минимальный .env в tmp_path
# ---------------------------------------------------------------------------

def _make_env(tmp_path, extra: str = "") -> None:
    (tmp_path / ".env").write_text(extra + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# BASE_DIR
# ---------------------------------------------------------------------------


def test_base_dir_is_path_instance() -> None:
    """BASE_DIR должен быть объектом Path, а не строкой."""

    assert isinstance(config_module.Config.BASE_DIR, Path)


def test_base_dir_contains_src() -> None:
    """BASE_DIR должен указывать на корень проекта, где лежит папка src/."""
    assert (config_module.Config.BASE_DIR / "src").is_dir()


# ---------------------------------------------------------------------------
# Булевы поля — парсинг env при reload
# ---------------------------------------------------------------------------


def test_boolean_tool_narration_enabled_truthy(monkeypatch) -> None:
    """TOOL_NARRATION_ENABLED=1 → True."""
    with monkeypatch.context() as mp:
        mp.setenv("TOOL_NARRATION_ENABLED", "1")
        reloaded = importlib.reload(config_module)
        assert reloaded.config.TOOL_NARRATION_ENABLED is True
    importlib.reload(config_module)


def test_boolean_tool_narration_enabled_falsy(monkeypatch) -> None:
    """TOOL_NARRATION_ENABLED=0 → False."""
    with monkeypatch.context() as mp:
        mp.setenv("TOOL_NARRATION_ENABLED", "0")
        reloaded = importlib.reload(config_module)
        assert reloaded.config.TOOL_NARRATION_ENABLED is False
    importlib.reload(config_module)


def test_boolean_non_owner_safe_mode_yes(monkeypatch) -> None:
    """NON_OWNER_SAFE_MODE_ENABLED=yes → True (альтернативный truthy)."""
    with monkeypatch.context() as mp:
        mp.setenv("NON_OWNER_SAFE_MODE_ENABLED", "yes")
        reloaded = importlib.reload(config_module)
        assert reloaded.config.NON_OWNER_SAFE_MODE_ENABLED is True
    importlib.reload(config_module)


def test_boolean_non_owner_safe_mode_false(monkeypatch) -> None:
    """NON_OWNER_SAFE_MODE_ENABLED=false → False."""
    with monkeypatch.context() as mp:
        mp.setenv("NON_OWNER_SAFE_MODE_ENABLED", "false")
        reloaded = importlib.reload(config_module)
        assert reloaded.config.NON_OWNER_SAFE_MODE_ENABLED is False
    importlib.reload(config_module)


# ---------------------------------------------------------------------------
# OWNER_USER_IDS — список числовых ID
# ---------------------------------------------------------------------------


def test_owner_user_ids_parsing(monkeypatch) -> None:
    """OWNER_USER_IDS должен парситься как stripped list строк."""
    with monkeypatch.context() as mp:
        mp.setenv("OWNER_USER_IDS", "123456789, 987654321 ,")
        reloaded = importlib.reload(config_module)
        assert reloaded.config.OWNER_USER_IDS == ["123456789", "987654321"]
    importlib.reload(config_module)


def test_owner_user_ids_empty(monkeypatch) -> None:
    """Пустой OWNER_USER_IDS → пустой список."""
    with monkeypatch.context() as mp:
        mp.setenv("OWNER_USER_IDS", "")
        reloaded = importlib.reload(config_module)
        assert reloaded.config.OWNER_USER_IDS == []
    importlib.reload(config_module)


# ---------------------------------------------------------------------------
# update_setting — TOOL_NARRATION_ENABLED
# ---------------------------------------------------------------------------


def test_update_setting_tool_narration_off(tmp_path, monkeypatch) -> None:
    """update_setting('TOOL_NARRATION_ENABLED', '0') выключает narration в памяти и .env."""
    _make_env(tmp_path, "TOOL_NARRATION_ENABLED=1")

    with monkeypatch.context() as mp:
        mp.setattr(config_module.Config, "BASE_DIR", tmp_path)
        mp.setattr(config_module.Config, "TOOL_NARRATION_ENABLED", True, raising=False)

        result = config_module.Config.update_setting("TOOL_NARRATION_ENABLED", "0")

        assert result is True
        assert config_module.Config.TOOL_NARRATION_ENABLED is False

    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "TOOL_NARRATION_ENABLED=0" in env_text


def test_update_setting_tool_narration_on_true(tmp_path, monkeypatch) -> None:
    """update_setting('TOOL_NARRATION_ENABLED', 'true') включает через alias 'true'."""
    _make_env(tmp_path, "TOOL_NARRATION_ENABLED=0")

    with monkeypatch.context() as mp:
        mp.setattr(config_module.Config, "BASE_DIR", tmp_path)
        mp.setattr(config_module.Config, "TOOL_NARRATION_ENABLED", False, raising=False)

        config_module.Config.update_setting("TOOL_NARRATION_ENABLED", "true")

        assert config_module.Config.TOOL_NARRATION_ENABLED is True


# ---------------------------------------------------------------------------
# update_setting — TELEGRAM_SESSION_HEARTBEAT_SEC (минимум 15)
# ---------------------------------------------------------------------------


def test_update_setting_heartbeat_normal(tmp_path, monkeypatch) -> None:
    """TELEGRAM_SESSION_HEARTBEAT_SEC=120 должен применяться без изменений."""
    _make_env(tmp_path, "TELEGRAM_SESSION_HEARTBEAT_SEC=60")

    with monkeypatch.context() as mp:
        mp.setattr(config_module.Config, "BASE_DIR", tmp_path)
        mp.setattr(config_module.Config, "TELEGRAM_SESSION_HEARTBEAT_SEC", 60, raising=False)

        config_module.Config.update_setting("TELEGRAM_SESSION_HEARTBEAT_SEC", "120")

        assert config_module.Config.TELEGRAM_SESSION_HEARTBEAT_SEC == 120


def test_update_setting_heartbeat_clamped_to_minimum(tmp_path, monkeypatch) -> None:
    """Слишком маленький heartbeat (< 15) должен быть clamp'нут до 15."""
    _make_env(tmp_path, "TELEGRAM_SESSION_HEARTBEAT_SEC=60")

    with monkeypatch.context() as mp:
        mp.setattr(config_module.Config, "BASE_DIR", tmp_path)
        mp.setattr(config_module.Config, "TELEGRAM_SESSION_HEARTBEAT_SEC", 60, raising=False)

        config_module.Config.update_setting("TELEGRAM_SESSION_HEARTBEAT_SEC", "5")

        assert config_module.Config.TELEGRAM_SESSION_HEARTBEAT_SEC == 15


# ---------------------------------------------------------------------------
# update_setting — неизвестный ключ не должен бросать исключение
# ---------------------------------------------------------------------------


def test_update_setting_unknown_key_returns_true(tmp_path, monkeypatch) -> None:
    """Несуществующий ключ не падает — update_setting возвращает True и пишет .env."""
    _make_env(tmp_path)

    with monkeypatch.context() as mp:
        mp.setattr(config_module.Config, "BASE_DIR", tmp_path)

        result = config_module.Config.update_setting("TOTALLY_UNKNOWN_KRAB_KEY", "somevalue")

    assert result is True
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "TOTALLY_UNKNOWN_KRAB_KEY=somevalue" in env_text


# ---------------------------------------------------------------------------
# load_swarm_team_accounts
# ---------------------------------------------------------------------------


def test_load_swarm_team_accounts_missing_file(tmp_path, monkeypatch) -> None:
    """Отсутствующий файл → пустой dict без исключений."""
    missing = tmp_path / "no_such_file.json"

    with monkeypatch.context() as mp:
        mp.setattr(config_module.Config, "SWARM_TEAM_ACCOUNTS_PATH", missing)

        result = config_module.Config.load_swarm_team_accounts()

    assert result == {}


def test_load_swarm_team_accounts_valid_json(tmp_path, monkeypatch) -> None:
    """Корректный JSON dict → возвращается как есть."""
    accounts = {
        "traders": {"session_name": "swarm_traders", "phone": "+34600000001"},
        "coders": {"session_name": "swarm_coders", "phone": "+34600000002"},
    }
    path = tmp_path / "swarm_team_accounts.json"
    path.write_text(json.dumps(accounts), encoding="utf-8")

    with monkeypatch.context() as mp:
        mp.setattr(config_module.Config, "SWARM_TEAM_ACCOUNTS_PATH", path)

        result = config_module.Config.load_swarm_team_accounts()

    assert result == accounts


def test_load_swarm_team_accounts_malformed_json(tmp_path, monkeypatch) -> None:
    """Битый JSON → пустой dict без краша."""
    path = tmp_path / "swarm_team_accounts.json"
    path.write_text("{not valid json!!!", encoding="utf-8")

    with monkeypatch.context() as mp:
        mp.setattr(config_module.Config, "SWARM_TEAM_ACCOUNTS_PATH", path)

        result = config_module.Config.load_swarm_team_accounts()

    assert result == {}


def test_load_swarm_team_accounts_not_dict(tmp_path, monkeypatch) -> None:
    """JSON валиден, но не dict (например список) → пустой dict."""
    path = tmp_path / "swarm_team_accounts.json"
    path.write_text(json.dumps(["traders", "coders"]), encoding="utf-8")

    with monkeypatch.context() as mp:
        mp.setattr(config_module.Config, "SWARM_TEAM_ACCOUNTS_PATH", path)

        result = config_module.Config.load_swarm_team_accounts()

    assert result == {}
