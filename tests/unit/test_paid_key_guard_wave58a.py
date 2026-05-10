"""Wave 58-A: тесты guard-а платного Gemini ключа.

Проверяем: при GEMINI_PAID_KEY_ENABLED=0 paid key никогда не используется
ни через config.GEMINI_API_KEY, ни через google_genai_direct._resolve_api_key().
"""

from __future__ import annotations

import importlib
import sys
import types
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reload_config(monkeypatch, env: dict[str, str]):
    """Перезагружает src.config с заданным окружением.

    Использует clear=False чтобы не затирать системные env vars (TELEGRAM_API_ID и т.д.)
    которые нужны для корректной инициализации Config class-body.
    Тестируемые переменные (GEMINI_*) переопределяются через env dict.
    """
    # Чистим из кеша чтобы class-body пересчитался
    for mod in list(sys.modules.keys()):
        if mod == "src.config" or mod.startswith("src.config."):
            monkeypatch.delitem(sys.modules, mod, raising=False)

    # clear=False: не убираем системные vars, только добавляем/перекрываем тестовые
    with patch.dict("os.environ", env, clear=False):
        cfg_mod = importlib.import_module("src.config")
        return cfg_mod.config


# ---------------------------------------------------------------------------
# config.GEMINI_API_KEY guard тесты
# ---------------------------------------------------------------------------


class TestConfigPaidKeyGuard:
    """Тесты класса Config: выбор GEMINI_API_KEY в зависимости от флага."""

    def test_paid_key_disabled_uses_free(self, monkeypatch):
        """flag=0 → GEMINI_API_KEY = FREE key."""
        env = {
            "GEMINI_PAID_KEY_ENABLED": "0",
            "GEMINI_API_KEY_FREE": "free-abc123",
            "GEMINI_API_KEY_PAID": "paid-secret999",
        }
        cfg = _reload_config(monkeypatch, env)
        assert cfg.GEMINI_API_KEY == "free-abc123"
        assert "secret999" not in str(cfg.GEMINI_API_KEY)

    def test_paid_key_enabled_uses_paid(self, monkeypatch):
        """flag=1 → GEMINI_API_KEY = PAID key."""
        env = {
            "GEMINI_PAID_KEY_ENABLED": "1",
            "GEMINI_API_KEY_FREE": "free-abc123",
            "GEMINI_API_KEY_PAID": "paid-secret999",
        }
        cfg = _reload_config(monkeypatch, env)
        assert cfg.GEMINI_API_KEY == "paid-secret999"

    def test_paid_key_disabled_no_free_falls_to_default(self, monkeypatch):
        """flag=0, нет FREE → GEMINI_API_KEY = GEMINI_API_KEY env (legacy).

        Используем monkeypatch.setenv чтобы перекрыть значение из .env:
        load_dotenv уже прочитал .env до запуска теста, поэтому нам важно лишь
        убедиться что paid key НЕ вернётся когда флаг выключен.
        """
        env = {
            "GEMINI_PAID_KEY_ENABLED": "0",
            "GEMINI_API_KEY_FREE": "",  # нет free key
            "GEMINI_API_KEY_PAID": "paid-should-be-blocked",
        }
        cfg = _reload_config(monkeypatch, env)
        # Главный инвариант: paid key никогда не возвращается при flag=0
        assert cfg.GEMINI_API_KEY != "paid-should-be-blocked"

    def test_paid_key_value_not_returned_when_disabled(self, monkeypatch):
        """Даже если PAID env установлен, при flag=0 он НЕ попадает в GEMINI_API_KEY."""
        env = {
            "GEMINI_PAID_KEY_ENABLED": "0",
            "GEMINI_API_KEY_FREE": "free-key",
            "GEMINI_API_KEY_PAID": "super-secret-paid",
        }
        cfg = _reload_config(monkeypatch, env)
        assert cfg.GEMINI_API_KEY != "super-secret-paid"
        assert cfg.GEMINI_API_KEY == "free-key"

    def test_update_setting_recomputes_api_key_when_flag_changes(self, monkeypatch):
        """update_setting при смене GEMINI_PAID_KEY_ENABLED пересчитывает GEMINI_API_KEY.

        Wave 58-A: тест покрывает runtime hot-reload через update_setting().
        """
        env = {
            "GEMINI_PAID_KEY_ENABLED": "0",
            "GEMINI_API_KEY_FREE": "free-key",
            "GEMINI_API_KEY_PAID": "paid-key",
        }
        cfg = _reload_config(monkeypatch, env)
        assert cfg.GEMINI_API_KEY == "free-key"
        assert cfg.GEMINI_PAID_KEY_ENABLED is False

        # Симулируем update_setting с включённым флагом
        with patch.dict("os.environ", {**env, "GEMINI_PAID_KEY_ENABLED": "1"}, clear=True):
            cfg.update_setting("GEMINI_PAID_KEY_ENABLED", "1")
        # После включения флага paid key должен быть доступен
        assert cfg.GEMINI_PAID_KEY_ENABLED is True
        assert cfg.GEMINI_API_KEY == "paid-key"

    def test_paid_disabled_paid_never_leaks_into_api_key(self, monkeypatch):
        """flag=0 + PAID установлен → paid value никогда не попадает в GEMINI_API_KEY.

        Даже если .env содержит другой GEMINI_API_KEY — paid key всё равно не вернётся.
        """
        env = {
            "GEMINI_PAID_KEY_ENABLED": "0",
            "GEMINI_API_KEY_FREE": "",
            "GEMINI_API_KEY": "",
            "GEMINI_API_KEY_PAID": "paid-should-never-leak",
        }
        cfg = _reload_config(monkeypatch, env)
        # Ключевой инвариант: значение paid-should-never-leak не попало в GEMINI_API_KEY
        assert cfg.GEMINI_API_KEY != "paid-should-never-leak"


# ---------------------------------------------------------------------------
# google_genai_direct._resolve_api_key guard тесты
# ---------------------------------------------------------------------------


class TestGoogleGenaiDirectGuard:
    """Тесты _resolve_api_key в google_genai_direct."""

    def _get_resolver(self):
        """Импортируем _resolve_api_key, очищая кеш модуля."""
        for mod in list(sys.modules.keys()):
            if "google_genai_direct" in mod:
                del sys.modules[mod]
        # Минимальные stub'ы для импорта без реального google-genai SDK
        from src.integrations import google_genai_direct as m

        return m._resolve_api_key, m

    def test_resolve_returns_free_when_paid_disabled(self, monkeypatch):
        """_resolve_api_key → free key когда GEMINI_PAID_KEY_ENABLED=0."""
        mock_config = MagicMock()
        mock_config.GEMINI_API_KEY_FREE = "free-xyz"
        mock_config.GEMINI_API_KEY_PAID = "paid-secret"
        mock_config.GEMINI_API_KEY = "free-xyz"

        monkeypatch.setenv("GEMINI_PAID_KEY_ENABLED", "0")
        monkeypatch.setenv("GEMINI_API_KEY_FREE", "free-xyz")

        resolve_fn, module = self._get_resolver()
        with patch.object(module, "_resolve_api_key", wraps=resolve_fn):
            with patch("src.config.config", mock_config):
                with patch.dict("os.environ", {"GEMINI_PAID_KEY_ENABLED": "0"}, clear=False):
                    result = resolve_fn()
        # Paid key не должен быть возвращён
        assert result != "paid-secret"

    def test_resolve_logs_warning_when_paid_blocked(self, monkeypatch, caplog):
        """_resolve_api_key логирует paid_key_attempt_blocked когда флаг выключен."""
        import logging

        mock_config = MagicMock()
        mock_config.GEMINI_API_KEY_FREE = "free-xyz"
        mock_config.GEMINI_API_KEY_PAID = "paid-secret"
        mock_config.GEMINI_API_KEY = "free-xyz"

        resolve_fn, module = self._get_resolver()

        logged_events = []
        original_warning = module.logger.warning

        def capture_warning(event, **kw):
            logged_events.append(event)
            original_warning(event, **kw)

        with patch.object(module.logger, "warning", side_effect=capture_warning):
            with patch("src.config.config", mock_config):
                with patch.dict("os.environ", {"GEMINI_PAID_KEY_ENABLED": "0"}, clear=False):
                    resolve_fn()

        assert "paid_key_attempt_blocked" in logged_events
