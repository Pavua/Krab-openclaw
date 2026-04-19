# -*- coding: utf-8 -*-
"""
Тесты для src/main.py и src/bootstrap/ (validate_config, retry-логика, signal handling).
Мокаем asyncio / pyrogram / openclaw — тестируем только pure-логику.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# validate_config / Config.validate / Config.is_valid
# ---------------------------------------------------------------------------


class TestValidateConfig:
    """Проверяем логику валидации конфига через Config и bootstrap.validate_config."""

    def test_valid_when_both_keys_set(self, monkeypatch):
        """Оба обязательных ключа заданы — конфиг валиден."""
        from src.config import Config

        monkeypatch.setattr(Config, "TELEGRAM_API_ID", 12345)
        monkeypatch.setattr(Config, "TELEGRAM_API_HASH", "abc123")
        assert Config.is_valid() is True

    def test_invalid_when_api_id_missing(self, monkeypatch):
        """Нет TELEGRAM_API_ID — validate() содержит соответствующую ошибку."""
        from src.config import Config

        monkeypatch.setattr(Config, "TELEGRAM_API_ID", None)
        monkeypatch.setattr(Config, "TELEGRAM_API_HASH", "abc123")
        errors = Config.validate()
        assert any("TELEGRAM_API_ID" in e for e in errors)

    def test_invalid_when_api_hash_missing(self, monkeypatch):
        """Нет TELEGRAM_API_HASH — validate() содержит соответствующую ошибку."""
        from src.config import Config

        monkeypatch.setattr(Config, "TELEGRAM_API_ID", 12345)
        monkeypatch.setattr(Config, "TELEGRAM_API_HASH", None)
        errors = Config.validate()
        assert any("TELEGRAM_API_HASH" in e for e in errors)

    def test_invalid_when_both_missing(self, monkeypatch):
        """Оба ключа отсутствуют — два сообщения об ошибке."""
        from src.config import Config

        monkeypatch.setattr(Config, "TELEGRAM_API_ID", None)
        monkeypatch.setattr(Config, "TELEGRAM_API_HASH", None)
        errors = Config.validate()
        assert len(errors) == 2

    def test_is_valid_false_on_empty_id(self, monkeypatch):
        """is_valid() == False, если API_ID пустой."""
        from src.config import Config

        monkeypatch.setattr(Config, "TELEGRAM_API_ID", 0)
        monkeypatch.setattr(Config, "TELEGRAM_API_HASH", "x")
        # 0 falsy → ошибка
        assert Config.is_valid() is False

    def test_validate_config_bootstrap_returns_false(self, monkeypatch):
        """bootstrap.validate_config() проксирует Config.is_valid() → False."""
        from src.config import Config

        monkeypatch.setattr(Config, "TELEGRAM_API_ID", None)
        monkeypatch.setattr(Config, "TELEGRAM_API_HASH", None)

        from src.bootstrap.env_and_lock import validate_config

        assert validate_config() is False

    def test_validate_config_bootstrap_returns_true(self, monkeypatch):
        """bootstrap.validate_config() проксирует Config.is_valid() → True."""
        from src.config import Config

        monkeypatch.setattr(Config, "TELEGRAM_API_ID", 42)
        monkeypatch.setattr(Config, "TELEGRAM_API_HASH", "goodhash")

        from src.bootstrap.env_and_lock import validate_config

        assert validate_config() is True


# ---------------------------------------------------------------------------
# _run_with_retry — backoff и retry-логика
# Патчим run_app и asyncio.sleep непосредственно в модуле src.main,
# чтобы не затрагивать реальный runtime.
# ---------------------------------------------------------------------------


class TestRunWithRetry:
    """Тестируем экспоненциальный backoff и clean-exit пути в main._run_with_retry."""

    @pytest.mark.asyncio
    async def test_clean_exit_no_retry(self):
        """Если run_app завершается без исключения, retry-loop выходит сразу."""
        call_count = 0

        async def mock_run_app():
            nonlocal call_count
            call_count += 1

        with patch("src.main.run_app", side_effect=mock_run_app):
            from src.main import _run_with_retry

            await _run_with_retry()

        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retry_on_connection_error(self):
        """ConnectionError → повтор, на третьем вызове run_app завершается чисто."""
        call_count = 0

        async def mock_run_app():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("network drop")

        async def mock_sleep(_: float):
            pass  # пропускаем реальное ожидание

        with (
            patch("src.main.run_app", side_effect=mock_run_app),
            patch("src.main.asyncio.sleep", side_effect=mock_sleep),
        ):
            from src.main import _run_with_retry

            await _run_with_retry()

        assert call_count == 3

    @pytest.mark.asyncio
    async def test_backoff_cancelled_during_sleep_exits(self):
        """CancelledError во время backoff-sleep → выход без дальнейших retry."""
        call_count = 0

        async def mock_run_app():
            nonlocal call_count
            call_count += 1
            raise OSError("socket closed")

        async def mock_sleep(_: float):
            raise asyncio.CancelledError()

        with (
            patch("src.main.run_app", side_effect=mock_run_app),
            patch("src.main.asyncio.sleep", side_effect=mock_sleep),
        ):
            from src.main import _run_with_retry

            await _run_with_retry()

        # Только один вызов — sleep сразу отменяет
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_backoff_doubles_each_retry(self):
        """Backoff удваивается: 5 → 10 → 20."""
        sleep_durations: list[float] = []
        call_count = 0

        async def mock_run_app():
            nonlocal call_count
            call_count += 1
            if call_count < 4:
                raise TimeoutError("timeout")

        async def mock_sleep(sec: float):
            sleep_durations.append(sec)

        with (
            patch("src.main.run_app", side_effect=mock_run_app),
            patch("src.main.asyncio.sleep", side_effect=mock_sleep),
        ):
            from src.main import _run_with_retry

            await _run_with_retry()

        assert sleep_durations == [5.0, 10.0, 20.0]

    @pytest.mark.asyncio
    async def test_backoff_capped_at_300(self):
        """Backoff не превышает 300 секунд (cap)."""
        sleep_durations: list[float] = []
        call_count = 0

        async def mock_run_app():
            nonlocal call_count
            call_count += 1
            if call_count < 10:
                raise ConnectionError("drop")

        async def mock_sleep(sec: float):
            sleep_durations.append(sec)

        with (
            patch("src.main.run_app", side_effect=mock_run_app),
            patch("src.main.asyncio.sleep", side_effect=mock_sleep),
        ):
            from src.main import _run_with_retry

            await _run_with_retry()

        assert max(sleep_durations) <= 300.0


# ---------------------------------------------------------------------------
# main() — точка входа
# ---------------------------------------------------------------------------


class TestMainEntrypoint:
    """Тестируем main() — sys.exit при невалидном конфиге, вызов retry при валидном."""

    @pytest.mark.asyncio
    async def test_main_exits_if_config_invalid(self):
        """main() вызывает sys.exit(1) при невалидном конфиге."""
        with (
            patch("src.main.validate_config", return_value=False),
            pytest.raises(SystemExit) as exc_info,
        ):
            from src.main import main

            await main()

        assert exc_info.value.code == 1

    @pytest.mark.asyncio
    async def test_main_calls_retry_if_config_valid(self):
        """main() вызывает _run_with_retry если конфиг валиден."""
        retry_called = False

        async def mock_retry():
            nonlocal retry_called
            retry_called = True

        with (
            patch("src.main.validate_config", return_value=True),
            patch("src.main._run_with_retry", side_effect=mock_retry),
        ):
            from src.main import main

            await main()

        assert retry_called is True
