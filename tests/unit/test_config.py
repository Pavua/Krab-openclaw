"""
Unit тесты для модуля конфигурации
"""

from unittest.mock import patch


class TestConfig:
    """Тесты для класса Config"""

    def test_config_defaults(self):
        """Тест значений по умолчанию — env-tolerant проверка типов и диапазонов."""
        from src.config import Config

        # OPENCLAW_URL может быть переопределён через .env, проверяем тип и формат
        assert isinstance(Config.OPENCLAW_URL, str)
        assert Config.OPENCLAW_URL.startswith("http")
        # MAX_RAM_GB — допустимый диапазон, .env может переопределить дефолт (24)
        assert isinstance(Config.MAX_RAM_GB, int)
        assert 1 <= Config.MAX_RAM_GB <= 256
        assert Config.LOG_LEVEL in ["INFO", "DEBUG", "WARNING", "ERROR"]

    def test_config_validate_missing_api_id(self):
        """Тест валидации при отсутствии API_ID"""
        from src.config import Config

        with patch.object(Config, "TELEGRAM_API_ID", 0):
            with patch.object(Config, "TELEGRAM_API_HASH", ""):
                errors = Config.validate()

                assert "TELEGRAM_API_ID не установлен" in errors
                assert "TELEGRAM_API_HASH не установлен" in errors

    def test_config_validate_success(self):
        """Тест успешной валидации"""
        from src.config import Config

        with patch.object(Config, "TELEGRAM_API_ID", 12345678):
            with patch.object(Config, "TELEGRAM_API_HASH", "abc123def456"):
                errors = Config.validate()

                assert len(errors) == 0
                assert Config.is_valid() is True

    def test_trigger_prefixes(self):
        """Тест триггерных префиксов"""
        from src.config import Config

        assert "!краб" in Config.TRIGGER_PREFIXES
        assert "@краб" in Config.TRIGGER_PREFIXES


# ---------------------------------------------------------------------------
# Wave 44-F: HISTORY_WINDOW_MAX_CHARS default + env override.
# ---------------------------------------------------------------------------


class TestHistoryWindowMaxCharsCap:
    """Wave 44-F: HISTORY_WINDOW_MAX_CHARS теперь по умолчанию 200KB."""

    def _reload_config(self):
        import importlib

        import src.config as cfg_mod

        return importlib.reload(cfg_mod).Config

    def test_default_is_200kb(self, monkeypatch):
        """По умолчанию (без env) cap = 200000 символов."""
        monkeypatch.delenv("KRAB_HISTORY_WINDOW_MAX_CHARS", raising=False)
        monkeypatch.delenv("HISTORY_WINDOW_MAX_CHARS", raising=False)
        Config = self._reload_config()
        assert Config.HISTORY_WINDOW_MAX_CHARS == 200000

    def test_krab_env_override(self, monkeypatch):
        """KRAB_HISTORY_WINDOW_MAX_CHARS переопределяет дефолт."""
        monkeypatch.setenv("KRAB_HISTORY_WINDOW_MAX_CHARS", "50000")
        monkeypatch.delenv("HISTORY_WINDOW_MAX_CHARS", raising=False)
        Config = self._reload_config()
        assert Config.HISTORY_WINDOW_MAX_CHARS == 50000

    def test_zero_disables_cap(self, monkeypatch):
        """0 → None (без лимита) для совместимости со старым поведением."""
        monkeypatch.setenv("KRAB_HISTORY_WINDOW_MAX_CHARS", "0")
        monkeypatch.delenv("HISTORY_WINDOW_MAX_CHARS", raising=False)
        Config = self._reload_config()
        assert Config.HISTORY_WINDOW_MAX_CHARS is None
