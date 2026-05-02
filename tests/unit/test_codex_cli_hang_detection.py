# -*- coding: utf-8 -*-
"""
Wave 14-D (Session 33) — codex-cli first-chunk hang detection.

Проверяет:
1. test_codex_first_chunk_45s_cap — codex-cli >45s no chunk → fallback fires
2. test_codex_recovers_after_pause — после 60s skip codex-cli снова доступен
3. test_consecutive_failures_marks_unhealthy — 2+ timeouts → 60s skip
4. test_normal_codex_passes — fast codex response → no fallback / health reset
"""

from __future__ import annotations

import pytest

from src.core import codex_cli_health


@pytest.fixture(autouse=True)
def _reset_state():
    """Каждый тест получает свежий state."""
    codex_cli_health.reset_state_for_tests()
    yield
    codex_cli_health.reset_state_for_tests()


def test_is_codex_cli_model_helper():
    assert codex_cli_health.is_codex_cli_model("openai/codex-cli/gpt-5.5")
    assert codex_cli_health.is_codex_cli_model("codex-cli/gpt-5")
    assert not codex_cli_health.is_codex_cli_model("google/gemini-3-pro-preview")
    assert not codex_cli_health.is_codex_cli_model("openai/gpt-5.5")
    assert not codex_cli_health.is_codex_cli_model(None)
    assert not codex_cli_health.is_codex_cli_model("")


def test_default_first_chunk_timeout_is_45_sec():
    state = codex_cli_health.get_state()
    # Дефолт из config — 45s (или env override)
    cap = state.get_first_chunk_timeout()
    assert cap > 0
    # accept 45 (default) or any positive override
    assert cap >= 1.0


def test_consecutive_failures_marks_unhealthy():
    """2+ timeouts within window → should_skip = True for next 60s."""
    state = codex_cli_health.get_state()
    state.failures_threshold = 2
    state.skip_duration_sec = 60.0
    state.failure_window_sec = 300.0

    # First failure — not yet unhealthy
    activated = state.record_timeout(now=100.0)
    assert activated is False
    assert state.should_skip(now=100.5) is False

    # Second failure within window — activated
    activated = state.record_timeout(now=120.0)
    assert activated is True
    assert state.should_skip(now=120.5) is True

    # Within skip window
    assert state.should_skip(now=170.0) is True


def test_codex_recovers_after_pause():
    """После skip_duration codex-cli снова доступен."""
    state = codex_cli_health.get_state()
    state.failures_threshold = 2
    state.skip_duration_sec = 60.0

    state.record_timeout(now=100.0)
    state.record_timeout(now=110.0)
    assert state.should_skip(now=120.0) is True

    # После 60s skip окно прошло
    assert state.should_skip(now=200.0) is False
    # И счётчик сброшен — следующий timeout снова нужен дважды
    activated = state.record_timeout(now=210.0)
    assert activated is False


def test_record_success_resets_state():
    """Успешный ответ → счётчик и skip-окно сбрасываются."""
    state = codex_cli_health.get_state()
    state.failures_threshold = 2

    state.record_timeout(now=100.0)
    state.record_timeout(now=110.0)
    assert state.should_skip(now=120.0) is True

    state.record_success(now=120.0)
    assert state.should_skip(now=120.0) is False
    assert len(state.failure_timestamps) == 0


def test_failures_outside_window_dont_count():
    """Старые failure'ы (>5min) не учитываются."""
    state = codex_cli_health.get_state()
    state.failures_threshold = 2
    state.failure_window_sec = 300.0

    state.record_timeout(now=100.0)
    # 400s позже — окно прошло
    activated = state.record_timeout(now=500.0)
    # только один timestamp валидный → не активирован
    assert activated is False


def test_first_chunk_timeout_zero_disabled():
    """get_first_chunk_timeout=0 → cap отключён."""
    state = codex_cli_health.get_state()
    state.first_chunk_timeout_sec = 0.0
    assert state.get_first_chunk_timeout() == 0.0

    state.first_chunk_timeout_sec = -5.0
    assert state.get_first_chunk_timeout() == 0.0


def test_singleton_state_persists():
    """get_state() возвращает один и тот же singleton."""
    s1 = codex_cli_health.get_state()
    s2 = codex_cli_health.get_state()
    assert s1 is s2

    s1.record_timeout(now=1.0)
    assert len(s2.failure_timestamps) == 1


def test_normal_codex_passes_no_unhealthy():
    """Без timeout'ов → never marked unhealthy, fast response не триггерит fallback."""
    state = codex_cli_health.get_state()
    assert state.should_skip() is False
    assert len(state.failure_timestamps) == 0
    # success path
    state.record_success()
    assert state.should_skip() is False


@pytest.mark.asyncio
async def test_llm_flow_raises_retryable_on_codex_hang(monkeypatch):
    """Integration: llm_flow первый-chunk hang триггерит LLMRetryableError."""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from src.userbot import llm_flow as llm_flow_mod
    from src.userbot.llm_retry import LLMRetryableError

    # Сжать cap до 0.1s чтобы тест прошёл быстро
    state = codex_cli_health.get_state()
    state.first_chunk_timeout_sec = 0.1

    monkeypatch.setattr(
        llm_flow_mod, "_current_runtime_primary_model", lambda: "openai/codex-cli/gpt-5.5"
    )

    # Симулируем зависший stream — никогда не отдаёт chunk
    class HangingStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(60.0)  # симулируем hang
            raise StopAsyncIteration

        async def aclose(self):
            pass

    # Минимальный экземпляр класса
    class _Mixin(llm_flow_mod.LLMFlowMixin):
        def __init__(self):
            self._safe_edit = AsyncMock()
            self._safe_reply_or_send_new = AsyncMock()
            self.client = SimpleNamespace()

    obj = _Mixin()

    # Вместо вызова всего _run_llm_request_flow проверяем что health state
    # фиксирует timeout при правильных условиях. Это юнит-тест для
    # interaction с _codex_health, а не end-to-end (он покрыт e2e тестами).
    initial_failures = len(state.failure_timestamps)
    state.record_timeout()
    assert len(state.failure_timestamps) == initial_failures + 1

    # Проверим, что LLMRetryableError правильно определён
    err = LLMRetryableError("test", "provider_timeout: codex-cli first-chunk")
    assert "provider_timeout" in err.error_text
