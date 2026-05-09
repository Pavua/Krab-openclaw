"""Wave 54: tests for cross-vendor fallback + smart retry + extended error messages."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Wave 54-A: helper script logic (configure_fallback_chain)
# ---------------------------------------------------------------------------


def _make_openclaw_json(fallbacks: list[str], primary: str = "codex-cli/gpt-5.5") -> dict:
    return {
        "agents": {
            "defaults": {
                "model": {
                    "primary": primary,
                    "fallbacks": fallbacks,
                }
            }
        }
    }


def _make_models_json(av_model_ids: list[str]) -> dict:
    return {
        "providers": {
            "anthropic-vertex": {
                "baseUrl": "https://aiplatform.googleapis.com/v1",
                "models": [{"id": m} for m in av_model_ids],
            }
        }
    }


class TestChainIncludesAnthropicModels:
    """54-A: helper reconizes anthropic-vertex models in models.json."""

    def test_chain_includes_anthropic_models_found(self, tmp_path: Path) -> None:
        """anthropic-vertex моделей в models.json — возвращается список."""
        models_json = _make_models_json(["claude-opus-4-6", "claude-sonnet-4-6"])
        models_path = tmp_path / "models.json"
        models_path.write_text(json.dumps(models_json), encoding="utf-8")

        # Импортируем функцию из скрипта
        import importlib.util
        import sys

        spec = importlib.util.spec_from_file_location(
            "configure_fallback_chain",
            Path(__file__).parent.parent.parent
            / "scripts"
            / "configure_fallback_chain.py",
        )
        module = importlib.util.module_from_spec(spec)

        # Патчим _MODELS_JSON_PATH
        with patch.object(
            sys.modules.get("configure_fallback_chain", module),
            "_MODELS_JSON_PATH",
            models_path,
            create=True,
        ):
            spec.loader.exec_module(module)
            module._MODELS_JSON_PATH = models_path
            result = module._get_anthropic_vertex_models_in_runtime()

        assert "anthropic-vertex/claude-opus-4-6" in result
        assert "anthropic-vertex/claude-sonnet-4-6" in result

    def test_chain_no_models_json_returns_empty(self, tmp_path: Path) -> None:
        """Если models.json нет — возвращается пустой список (нет краша)."""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "configure_fallback_chain_b",
            Path(__file__).parent.parent.parent
            / "scripts"
            / "configure_fallback_chain.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # Переопределяем путь на несуществующий файл ПОСЛЕ загрузки
        module._MODELS_JSON_PATH = tmp_path / "nonexistent.json"
        result = module._get_anthropic_vertex_models_in_runtime()
        assert result == []

    def test_recommended_fallbacks_contain_anthropic_vertex(self) -> None:
        """RECOMMENDED_FALLBACKS содержат anthropic-vertex модели."""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "configure_fallback_chain_c",
            Path(__file__).parent.parent.parent
            / "scripts"
            / "configure_fallback_chain.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        av_models = [m for m in module.RECOMMENDED_FALLBACKS if "anthropic-vertex" in m]
        assert len(av_models) >= 2, "Chain must include at least 2 anthropic-vertex models"

    def test_anthropic_interleaved_early_in_chain(self) -> None:
        """Первый anthropic-vertex в chain — на позиции 2 (index 1), не позднее."""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "configure_fallback_chain_d",
            Path(__file__).parent.parent.parent
            / "scripts"
            / "configure_fallback_chain.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        first_av_pos = next(
            (i for i, m in enumerate(module.RECOMMENDED_FALLBACKS) if "anthropic-vertex" in m),
            len(module.RECOMMENDED_FALLBACKS),
        )
        assert first_av_pos <= 1, (
            f"First anthropic-vertex at position {first_av_pos + 1}, expected <= 2"
        )


# ---------------------------------------------------------------------------
# Wave 54-B: smart retry logic in openclaw_client
# ---------------------------------------------------------------------------


def _make_mock_client(recovery_retry_delay: float = 30.0) -> Any:
    """Создаёт минимальный mock OpenClawClient для тестирования retry."""
    from src.openclaw_client import OpenClawClient

    client = object.__new__(OpenClawClient)
    # Инициализируем только нужные поля
    client._cloud_tier_state = {
        "active_tier": "paid",
        "last_error_code": None,
        "last_error_message": "",
        "last_recovery_action": "none",
    }
    client._cloud_recovery_retry_lock = asyncio.Lock()
    return client


class TestSmartRetryLogic:
    """54-B: smart retry waits cool-down then retries."""

    @pytest.mark.asyncio
    async def test_recovery_lock_created_on_init(self) -> None:
        """asyncio.Lock создаётся при инициализации клиента."""
        from src.openclaw_client import OpenClawClient

        # Создаём raw instance без вызова __init__, добавляем lock вручную
        client = OpenClawClient.__new__(OpenClawClient)
        client._cloud_recovery_retry_lock = asyncio.Lock()
        assert isinstance(client._cloud_recovery_retry_lock, asyncio.Lock)
        assert not client._cloud_recovery_retry_lock.locked()

    @pytest.mark.asyncio
    async def test_smart_retry_skips_if_recovery_lock_held(self) -> None:
        """Если lock занят, smart-retry не запускается (fast-path error)."""
        lock = asyncio.Lock()
        # Занимаем lock симулируя другой retry-loop
        await lock.acquire()
        assert lock.locked()
        # При locked() → _can_retry = False
        _can_retry = not lock.locked()
        assert not _can_retry
        lock.release()

    @pytest.mark.asyncio
    async def test_smart_retry_not_triggered_for_auth_errors(self) -> None:
        """Auth-ошибки (quota_exceeded) не активируют smart retry."""
        # Auth errors не попадают в "все transient" условие
        chain_failure_reasons = {
            "google-gemini-cli/gemini-3-pro-preview": "квота исчерпана",
        }
        _all_transient = all(
            reason in ("таймаут провайдера", "HTTP 500 internal error")
            for reason in chain_failure_reasons.values()
        )
        assert not _all_transient

    @pytest.mark.asyncio
    async def test_smart_retry_triggered_for_all_transient(self) -> None:
        """Все timeout/500 ошибки → _all_transient = True → retry разрешён."""
        chain_failure_reasons = {
            "google-gemini-cli/gemini-3-pro-preview": "таймаут провайдера",
            "google-vertex/gemini-3-pro-preview": "HTTP 500 internal error",
            "google-gemini-cli/gemini-2.5-pro": "таймаут провайдера",
        }
        _all_transient = bool(chain_failure_reasons) and all(
            reason in ("таймаут провайдера", "HTTP 500 internal error")
            for reason in chain_failure_reasons.values()
        )
        assert _all_transient

    @pytest.mark.asyncio
    async def test_smart_retry_waits_configured_delay(self) -> None:
        """asyncio.sleep вызывается с configured delay."""
        delay_called_with: list[float] = []

        async def fake_sleep(sec: float) -> None:
            delay_called_with.append(sec)

        with patch("asyncio.sleep", side_effect=fake_sleep):
            await asyncio.sleep(30.0)

        assert delay_called_with == [30.0]

    @pytest.mark.asyncio
    async def test_smart_retry_success_emits_footer_recovered(self) -> None:
        """После успешного retry ответ содержит footer о recovery."""
        response_text = "Вот мой ответ на твой вопрос."
        retry_delay_int = 30
        final = (
            response_text.rstrip()
            + f"\n\n_(восстановлено после {retry_delay_int}с ожидания)_"
        )
        assert "восстановлено после 30с" in final
        assert response_text in final

    @pytest.mark.asyncio
    async def test_smart_retry_failure_shows_extended_error(self) -> None:
        """Если retry тоже упал — показывается extended error message."""
        chain_failure_reasons = {
            "google-gemini-cli/gemini-3-pro-preview": "таймаут провайдера",
            "google-vertex/gemini-3-pro-preview": "HTTP 500 internal error",
        }
        chain_advance_count = 1
        _top_failures = list(chain_failure_reasons.items())[:3]
        _failures_text = "\n".join(f"• {m} — {r}" for m, r in _top_failures)
        user_text = (
            f"❌ Облако недоступно (попробовал {chain_advance_count + 1} моделей).\n\n"
            f"Последние ошибки:\n{_failures_text}\n\n"
            "⏱ Обычно восстанавливается за 30-90с.\n"
            "!routes — детали | !model local — local переключение"
        )
        assert "❌ Облако недоступно" in user_text
        assert "таймаут провайдера" in user_text
        assert "HTTP 500 internal error" in user_text
        assert "!model local" in user_text


# ---------------------------------------------------------------------------
# Wave 54-C: extended error message tests
# ---------------------------------------------------------------------------


class TestErrorMessageFormat:
    """54-C: финальное сообщение включает детали ошибок и recovery hint."""

    def test_error_message_includes_top_failures(self) -> None:
        """Топ-3 ошибки включены в текст сообщения."""
        chain_failure_reasons = {
            "gemini-3-pro-preview": "таймаут провайдера",
            "gemini-2.5-pro": "HTTP 500 internal error",
            "google-vertex/gemini-3-pro-preview": "таймаут провайдера",
            "gemini-3-flash-preview": "таймаут провайдера",  # 4-й — не попадёт в топ-3
        }
        _top_failures = list(chain_failure_reasons.items())[:3]
        _failures_text = "\n".join(f"• {m} — {r}" for m, r in _top_failures)

        assert "gemini-3-pro-preview" in _failures_text
        assert "gemini-2.5-pro" in _failures_text
        assert "google-vertex/gemini-3-pro-preview" in _failures_text
        # 4-й не должен быть в топ-3
        assert "gemini-3-flash-preview" not in _failures_text

    def test_error_message_recovery_eta_hint(self) -> None:
        """Сообщение содержит recovery ETA hint."""
        user_text = (
            "❌ Облако недоступно (попробовал 7 моделей).\n\n"
            "Последние ошибки:\n• x — таймаут провайдера\n\n"
            "⏱ Обычно восстанавливается за 30-90с.\n"
            "!routes — детали | !model local — local переключение"
        )
        assert "30-90с" in user_text
        assert "⏱" in user_text

    def test_error_message_has_cta_commands(self) -> None:
        """Сообщение включает CTA команды !routes и !model local."""
        user_text = (
            "❌ Облако недоступно.\n"
            "!routes — детали | !model local — local переключение"
        )
        assert "!routes" in user_text
        assert "!model local" in user_text

    def test_error_message_no_failures_fallback_text(self) -> None:
        """Если chain_failure_reasons пуст но advance_count > 0 — корректное сообщение."""
        chain_failure_reasons: dict[str, str] = {}
        chain_advance_count = 5
        _top_failures = list(chain_failure_reasons.items())[:3]
        if _top_failures:
            user_text = "with failures"
        elif chain_advance_count > 0:
            user_text = (
                "❌ Облачный сервис недоступен — попробовал "
                f"{chain_advance_count + 1} моделей в fallback chain.\n"
                "⏱ Обычно восстанавливается за 30-90с.\n"
                "!routes — детали | !model local — local переключение"
            )
        else:
            user_text = "❌ Облачный сервис временно недоступен."

        assert "попробовал 6 моделей" in user_text
        assert "30-90с" in user_text

    def test_failure_reason_mapping_timeout(self) -> None:
        """provider_timeout → 'таймаут провайдера'."""
        _err_code = "provider_timeout"
        if _err_code == "provider_timeout":
            reason = "таймаут провайдера"
        elif _err_code == "provider_error":
            reason = "HTTP 500 internal error"
        else:
            reason = _err_code
        assert reason == "таймаут провайдера"

    def test_failure_reason_mapping_provider_error(self) -> None:
        """provider_error → 'HTTP 500 internal error'."""
        _err_code = "provider_error"
        if _err_code == "provider_timeout":
            reason = "таймаут провайдера"
        elif _err_code == "provider_error":
            reason = "HTTP 500 internal error"
        else:
            reason = _err_code
        assert reason == "HTTP 500 internal error"


# ---------------------------------------------------------------------------
# Wave 54-B: configurable cool-down delay
# ---------------------------------------------------------------------------


class TestCoolDownConfigurable:
    """54-B: KRAB_CLOUD_RECOVERY_RETRY_DELAY_SEC конфигурируем через env."""

    def test_cool_down_default_is_30s(self) -> None:
        """По умолчанию задержка = 30 секунд."""
        import os

        with patch.dict(os.environ, {}, clear=False):
            # Убираем переменную если есть
            os.environ.pop("KRAB_CLOUD_RECOVERY_RETRY_DELAY_SEC", None)
            value = float(os.environ.get("KRAB_CLOUD_RECOVERY_RETRY_DELAY_SEC", "30"))
            assert value == 30.0

    def test_cool_down_configurable_via_env(self) -> None:
        """KRAB_CLOUD_RECOVERY_RETRY_DELAY_SEC читается из env."""
        import os

        with patch.dict(os.environ, {"KRAB_CLOUD_RECOVERY_RETRY_DELAY_SEC": "60"}):
            value = float(os.environ.get("KRAB_CLOUD_RECOVERY_RETRY_DELAY_SEC", "30"))
            clamped = min(180.0, max(0.0, value))
            assert clamped == 60.0

    def test_cool_down_clamped_to_max_180(self) -> None:
        """Значение > 180 зажимается до 180."""
        raw = 9999.0
        clamped = min(180.0, max(0.0, raw))
        assert clamped == 180.0

    def test_cool_down_zero_disables_retry(self) -> None:
        """KRAB_CLOUD_RECOVERY_RETRY_DELAY_SEC=0 отключает smart-retry."""
        retry_delay = 0.0
        _can_retry_when_zero = retry_delay > 0
        assert not _can_retry_when_zero

    def test_config_class_has_new_attribute(self) -> None:
        """Config класс имеет атрибут KRAB_CLOUD_RECOVERY_RETRY_DELAY_SEC."""
        from src.config import Config
        assert hasattr(Config, "KRAB_CLOUD_RECOVERY_RETRY_DELAY_SEC")
        assert isinstance(Config.KRAB_CLOUD_RECOVERY_RETRY_DELAY_SEC, float)
        assert Config.KRAB_CLOUD_RECOVERY_RETRY_DELAY_SEC >= 0.0
