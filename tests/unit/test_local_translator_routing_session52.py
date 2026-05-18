# -*- coding: utf-8 -*-
"""Session 52 P2: local translator via LM Studio Gemma 4 vanilla.

Background:
- Highest-frequency cloud-burner в Krab: `translator_engine.translate_text`
  hardcoded `force_cloud=True, preferred_model="google/gemini-3-flash-preview"`.
- Every auto-translate потоков (incoming foreign messages) + voice transcript
  translation hits Gemini Flash via Gateway — significant cost over time.
- S52 P0 (commit d4ff0e6) уже loaded Gemma 4 26B vanilla в LM Studio для
  vision. **Reuse**: тот же loaded model для translation = **0 RAM cost**.
- Bench S52: Gemma 4 vanilla accurately translates RU↔EN with idiomatic
  word choice ("Быстрая бурая лиса перепрыгивает через ленивую собаку"
  formal preserved).

Coverage:
- _translate_via_lmstudio happy path (200 OK + clean text)
- env defaults (KRAB_LOCAL_VISION_URL reuse + KRAB_LOCAL_TRANSLATOR_MODEL fallback)
- HTTP error → empty string (fail-open)
- reasoning fallback when content empty
- translate_text routing: KRAB_LOCAL_TRANSLATOR_ENABLED=1 → local first,
  empty result falls through to cloud
- cache still works (S95 wave)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.core import translator_engine
from src.core.translator_engine import (
    TranslationResult,
    _translate_via_lmstudio,
    translate_text,
)

# ── _translate_via_lmstudio direct tests ───────────────────────────────────


@pytest.mark.asyncio
async def test_lmstudio_translate_happy_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LM Studio 200 OK → returns (translated, model_id)."""
    monkeypatch.setenv("KRAB_LOCAL_VISION_URL", "http://127.0.0.1:1234")
    monkeypatch.setenv("KRAB_LOCAL_TRANSLATOR_MODEL", "gemma-test")
    monkeypatch.setenv("LM_STUDIO_API_KEY", "sk-test-key")

    captured: dict[str, Any] = {}

    class _FakeResp:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {"choices": [{"message": {"content": "Привет, мир!"}}]}

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return _FakeResp()

    with patch.object(httpx, "AsyncClient", _FakeClient):
        translated, model_id = await _translate_via_lmstudio(
            "Hello, world!", "en", "ru", timeout_sec=20.0
        )

    assert translated == "Привет, мир!"
    assert model_id == "lmstudio/gemma-test"
    assert captured["url"] == "http://127.0.0.1:1234/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer sk-test-key"
    # Request includes both system message + user prompt
    messages = captured["json"]["messages"]
    assert messages[0]["role"] == "system"
    assert "переводчик" in messages[0]["content"].lower()
    assert messages[1]["role"] == "user"
    assert "Hello, world!" in messages[1]["content"]
    assert captured["json"]["max_tokens"] == 512
    assert captured["json"]["temperature"] == 0.0


@pytest.mark.asyncio
async def test_lmstudio_translate_http_error_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP error → log + empty string + lmstudio_error marker."""
    monkeypatch.setenv("LM_STUDIO_API_KEY", "sk-test")

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            raise httpx.HTTPError("connection refused")

    with patch.object(httpx, "AsyncClient", _FakeClient):
        translated, model_id = await _translate_via_lmstudio("test", "en", "ru", timeout_sec=5.0)

    assert translated == ""
    assert model_id == "lmstudio_error"


@pytest.mark.asyncio
async def test_lmstudio_translate_reasoning_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Если content empty, читаем reasoning (thinking-mode models)."""
    monkeypatch.delenv("LM_STUDIO_API_KEY", raising=False)

    class _FakeResp:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "reasoning": "Перевожу: 'привет'.",
                        }
                    }
                ]
            }

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            return _FakeResp()

    with patch.object(httpx, "AsyncClient", _FakeClient):
        translated, _ = await _translate_via_lmstudio("hello", "en", "ru", timeout_sec=5.0)

    assert translated == "Перевожу: 'привет'."


@pytest.mark.asyncio
async def test_lmstudio_translate_falls_back_vision_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KRAB_LOCAL_TRANSLATOR_MODEL unset → fall back на KRAB_LOCAL_VISION_MODEL."""
    monkeypatch.delenv("KRAB_LOCAL_TRANSLATOR_MODEL", raising=False)
    monkeypatch.setenv("KRAB_LOCAL_VISION_MODEL", "shared-gemma-model")
    monkeypatch.delenv("LM_STUDIO_API_KEY", raising=False)

    captured: dict[str, Any] = {}

    class _FakeResp:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {"choices": [{"message": {"content": "ok"}}]}

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None, headers=None):
            captured["json"] = json
            return _FakeResp()

    with patch.object(httpx, "AsyncClient", _FakeClient):
        _, model_id = await _translate_via_lmstudio("test", "en", "ru", timeout_sec=5.0)

    assert captured["json"]["model"] == "shared-gemma-model"
    assert model_id == "lmstudio/shared-gemma-model"


# ── translate_text routing ────────────────────────────────────────────────


def _make_mock_client(chunks: list[str]):
    """Mock OpenClawClient with streaming response."""

    async def _async_iter(items):
        for item in items:
            yield item

    mock = AsyncMock()
    mock.send_message_stream = lambda **kwargs: _async_iter(chunks)
    mock.clear_session = AsyncMock(return_value=None)
    mock._last_runtime_route = {"model": "google/gemini-3-flash-preview"}
    return mock


@pytest.mark.asyncio
async def test_translate_text_local_when_env_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KRAB_LOCAL_TRANSLATOR_ENABLED=1 → local path used, cloud bypassed."""
    monkeypatch.setenv("KRAB_LOCAL_TRANSLATOR_ENABLED", "1")
    # Clear cache to avoid pollution
    from src.core import translation_cache as cache_mod

    cache_mod.translation_cache._entries.clear()  # type: ignore[attr-defined]

    local_mock = AsyncMock(return_value=("Привет!", "lmstudio/gemma-4-26b-a4b-it@4bit"))
    mock_client = _make_mock_client(["should not be called"])

    with patch.object(translator_engine, "_translate_via_lmstudio", local_mock):
        result = await translate_text("Hello!", "en", "ru", openclaw_client=mock_client)

    assert isinstance(result, TranslationResult)
    assert result.translated == "Привет!"
    assert result.model_id == "lmstudio/gemma-4-26b-a4b-it@4bit"
    local_mock.assert_called_once()
    # Cloud send_message_stream NOT called (would error since not async-gen)


@pytest.mark.asyncio
async def test_translate_text_cloud_when_env_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KRAB_LOCAL_TRANSLATOR_ENABLED unset (default) → cloud only."""
    monkeypatch.delenv("KRAB_LOCAL_TRANSLATOR_ENABLED", raising=False)
    from src.core import translation_cache as cache_mod

    cache_mod.translation_cache._entries.clear()  # type: ignore[attr-defined]

    local_mock = AsyncMock(return_value=("UNUSED", "lmstudio/X"))
    mock_client = _make_mock_client(["Привет cloud"])

    with patch.object(translator_engine, "_translate_via_lmstudio", local_mock):
        result = await translate_text(
            "Hello cloud!",
            "en",
            "ru",
            openclaw_client=mock_client,
        )

    assert result.translated == "Привет cloud"
    local_mock.assert_not_called()


@pytest.mark.asyncio
async def test_translate_text_local_empty_falls_through_to_cloud(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local returns "" → cloud path used (resilience)."""
    monkeypatch.setenv("KRAB_LOCAL_TRANSLATOR_ENABLED", "1")
    from src.core import translation_cache as cache_mod

    cache_mod.translation_cache._entries.clear()  # type: ignore[attr-defined]

    # Local fails → empty string
    local_mock = AsyncMock(return_value=("", "lmstudio_error"))
    mock_client = _make_mock_client(["Fallback cloud translation"])

    with patch.object(translator_engine, "_translate_via_lmstudio", local_mock):
        result = await translate_text("Fallback test", "en", "ru", openclaw_client=mock_client)

    assert result.translated == "Fallback cloud translation"
    local_mock.assert_called_once()  # Tried local first


@pytest.mark.asyncio
async def test_translate_text_local_strips_quotes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local model wraps в quotes → strip them like cloud path does."""
    monkeypatch.setenv("KRAB_LOCAL_TRANSLATOR_ENABLED", "1")
    from src.core import translation_cache as cache_mod

    cache_mod.translation_cache._entries.clear()  # type: ignore[attr-defined]

    local_mock = AsyncMock(return_value=('"Привет с кавычками"', "lmstudio/X"))
    mock_client = _make_mock_client(["unused"])

    with patch.object(translator_engine, "_translate_via_lmstudio", local_mock):
        result = await translate_text("Hello quotes", "en", "ru", openclaw_client=mock_client)

    assert result.translated == "Привет с кавычками"


# ── S61 W2: idle observability (mirror S55 D / S56 C) ─────────────────────


@pytest.mark.asyncio
async def test_translate_local_failed_logs_idle_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local returns empty → translate_local_idle_skip log emitted with
    reason=local_failed_fallback (rate-limited via module dict).
    """
    monkeypatch.setenv("KRAB_LOCAL_TRANSLATOR_ENABLED", "1")
    monkeypatch.setenv("KRAB_TRANSLATOR_IDLE_LOG_INTERVAL_SEC", "60")
    from src.core import translation_cache as cache_mod

    cache_mod.translation_cache._entries.clear()  # type: ignore[attr-defined]
    # Reset rate-limit dict to ensure deterministic first-log behavior.
    translator_engine._translator_idle_last_log_ts.clear()

    local_mock = AsyncMock(return_value=("", "lmstudio_error"))
    mock_client = _make_mock_client(["cloud fallback text"])

    logged: list[tuple[str, dict]] = []

    def _fake_info(event: str, **kwargs: Any) -> None:
        logged.append((event, kwargs))

    with (
        patch.object(translator_engine, "_translate_via_lmstudio", local_mock),
        patch.object(translator_engine.logger, "info", side_effect=_fake_info),
    ):
        result = await translate_text("Phase2 fallback", "en", "ru", openclaw_client=mock_client)

    assert result.translated == "cloud fallback text"
    idle_events = [(e, k) for e, k in logged if e == "translate_local_idle_skip"]
    assert len(idle_events) == 1, f"expected 1 idle_skip event, got {idle_events}"
    _, kwargs = idle_events[0]
    assert kwargs["reason"] == "local_failed_fallback"
    assert kwargs["src_lang"] == "en"
    assert kwargs["tgt_lang"] == "ru"
    assert kwargs["interval_sec"] == 60.0


@pytest.mark.asyncio
async def test_translate_local_idle_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two consecutive local failures within interval → only one
    translate_local_idle_skip log (rate-limited).
    """
    monkeypatch.setenv("KRAB_LOCAL_TRANSLATOR_ENABLED", "1")
    # Wide window: ensure 2nd call falls inside rate-limit gate.
    monkeypatch.setenv("KRAB_TRANSLATOR_IDLE_LOG_INTERVAL_SEC", "3600")
    from src.core import translation_cache as cache_mod

    cache_mod.translation_cache._entries.clear()  # type: ignore[attr-defined]
    translator_engine._translator_idle_last_log_ts.clear()

    local_mock = AsyncMock(return_value=("", "lmstudio_error"))

    logged: list[tuple[str, dict]] = []

    def _fake_info(event: str, **kwargs: Any) -> None:
        logged.append((event, kwargs))

    # Two consecutive calls with different texts (cache miss both times).
    with (
        patch.object(translator_engine, "_translate_via_lmstudio", local_mock),
        patch.object(translator_engine.logger, "info", side_effect=_fake_info),
    ):
        mock1 = _make_mock_client(["cloud-1"])
        await translate_text("first text", "en", "ru", openclaw_client=mock1)
        mock2 = _make_mock_client(["cloud-2"])
        await translate_text("second text", "en", "ru", openclaw_client=mock2)

    idle_events = [(e, k) for e, k in logged if e == "translate_local_idle_skip"]
    assert len(idle_events) == 1, (
        f"expected exactly 1 idle_skip (rate-limited), got {len(idle_events)}: {idle_events}"
    )


@pytest.mark.asyncio
async def test_translate_local_success_no_idle_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local path succeeds → NO translate_local_idle_skip log."""
    monkeypatch.setenv("KRAB_LOCAL_TRANSLATOR_ENABLED", "1")
    from src.core import translation_cache as cache_mod

    cache_mod.translation_cache._entries.clear()  # type: ignore[attr-defined]
    translator_engine._translator_idle_last_log_ts.clear()

    local_mock = AsyncMock(return_value=("Локальный успех", "lmstudio/gemma"))
    mock_client = _make_mock_client(["unused"])

    logged: list[tuple[str, dict]] = []

    def _fake_info(event: str, **kwargs: Any) -> None:
        logged.append((event, kwargs))

    with (
        patch.object(translator_engine, "_translate_via_lmstudio", local_mock),
        patch.object(translator_engine.logger, "info", side_effect=_fake_info),
    ):
        result = await translate_text("Success path", "en", "ru", openclaw_client=mock_client)

    assert result.translated == "Локальный успех"
    idle_events = [e for e, _ in logged if e == "translate_local_idle_skip"]
    assert idle_events == [], f"unexpected idle_skip on local success: {idle_events}"


# ── S65 W6: residual coverage gaps (translation cache hit, counter import
# failure, cache.store exceptions, runtime route lookup exception) ───────────


@pytest.mark.asyncio
async def test_translate_text_cache_hit_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """translation_cache.lookup hit → return TranslationResult с model_id=
    'translation_cache' БЕЗ обращения к local/cloud (lines 216-217)."""
    monkeypatch.setenv("KRAB_LOCAL_TRANSLATOR_ENABLED", "1")
    from src.core import translation_cache as cache_mod

    cache_mod.translation_cache._entries.clear()  # type: ignore[attr-defined]
    cache_mod.translation_cache.store("cached source text", "ru", "Кэшированный перевод")

    # Local mock raises if called → asserts cache short-circuits.
    local_mock = AsyncMock(side_effect=AssertionError("local should not be called on cache hit"))
    mock_client = _make_mock_client(["unused cloud"])

    with patch.object(translator_engine, "_translate_via_lmstudio", local_mock):
        result = await translate_text(
            "cached source text", "en", "ru", openclaw_client=mock_client
        )

    assert result.translated == "Кэшированный перевод"
    assert result.model_id == "translation_cache"
    local_mock.assert_not_called()


@pytest.mark.asyncio
async def test_translate_local_counter_import_failure_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`inc_translator_idle_skip` import/call раскидывает Exception → swallow,
    log path продолжается (lines 47-48). Гарантирует resilience metrics shim."""
    monkeypatch.setenv("KRAB_LOCAL_TRANSLATOR_ENABLED", "1")
    monkeypatch.setenv("KRAB_TRANSLATOR_IDLE_LOG_INTERVAL_SEC", "60")
    from src.core import translation_cache as cache_mod

    cache_mod.translation_cache._entries.clear()  # type: ignore[attr-defined]
    translator_engine._translator_idle_last_log_ts.clear()

    # Подменяем модуль метрик так чтобы импорт inc_translator_idle_skip
    # внутри _log_translator_idle_skip рейзил → catch BLE001.
    import sys

    fake_module = type(sys)("src.core.metrics.idle_skip")

    def _boom(_reason: str) -> None:
        raise RuntimeError("metrics backend down")

    fake_module.inc_translator_idle_skip = _boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "src.core.metrics.idle_skip", fake_module)

    local_mock = AsyncMock(return_value=("", "lmstudio_error"))
    mock_client = _make_mock_client(["cloud after counter boom"])

    logged: list[tuple[str, dict]] = []

    def _fake_info(event: str, **kwargs: Any) -> None:
        logged.append((event, kwargs))

    with (
        patch.object(translator_engine, "_translate_via_lmstudio", local_mock),
        patch.object(translator_engine.logger, "info", side_effect=_fake_info),
    ):
        result = await translate_text("counter boom", "en", "ru", openclaw_client=mock_client)

    # Несмотря на counter Exception, idle_skip log всё равно эмитится, и
    # cloud fallback отрабатывает успешно.
    assert result.translated == "cloud after counter boom"
    assert any(e == "translate_local_idle_skip" for e, _ in logged)


@pytest.mark.asyncio
async def test_translate_local_cache_store_exception_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """translation_cache.store raises на local-success path → swallow (246-247),
    результат всё равно возвращается caller'у."""
    monkeypatch.setenv("KRAB_LOCAL_TRANSLATOR_ENABLED", "1")
    from src.core import translation_cache as cache_mod

    cache_mod.translation_cache._entries.clear()  # type: ignore[attr-defined]

    def _boom_store(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("cache backend down")

    local_mock = AsyncMock(return_value=("Локальный успех", "lmstudio/gemma"))
    mock_client = _make_mock_client(["unused"])

    with (
        patch.object(translator_engine, "_translate_via_lmstudio", local_mock),
        patch.object(cache_mod.translation_cache, "store", side_effect=_boom_store),
    ):
        result = await translate_text("store boom local", "en", "ru", openclaw_client=mock_client)

    assert result.translated == "Локальный успех"
    assert result.model_id == "lmstudio/gemma"


@pytest.mark.asyncio
async def test_translate_cloud_route_lookup_exception_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """getattr(openclaw_client, '_last_runtime_route') raises → swallow
    (lines 299-300), model_id остаётся 'unknown'."""
    monkeypatch.delenv("KRAB_LOCAL_TRANSLATOR_ENABLED", raising=False)
    from src.core import translation_cache as cache_mod

    cache_mod.translation_cache._entries.clear()  # type: ignore[attr-defined]

    class _RaisingRouteClient:
        """OpenClawClient stub в котором _last_runtime_route — property raising."""

        @property
        def _last_runtime_route(self) -> dict:  # noqa: D401
            raise RuntimeError("route descriptor exploded")

        def clear_session(self, _chat_id: str) -> None:
            return None

        def send_message_stream(self, **_kwargs: Any):
            async def _gen():
                yield "Перевод OK"

            return _gen()

    client = _RaisingRouteClient()
    result = await translate_text("route boom", "en", "ru", openclaw_client=client)  # type: ignore[arg-type]

    assert result.translated == "Перевод OK"
    # _last_runtime_route raised → fallback model_id="unknown" сохранён.
    assert result.model_id == "unknown"


@pytest.mark.asyncio
async def test_translate_cloud_cache_store_exception_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """translation_cache.store raises на cloud-success path → swallow
    (lines 312-314)."""
    monkeypatch.delenv("KRAB_LOCAL_TRANSLATOR_ENABLED", raising=False)
    from src.core import translation_cache as cache_mod

    cache_mod.translation_cache._entries.clear()  # type: ignore[attr-defined]

    def _boom_store(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("cache backend down")

    mock_client = _make_mock_client(["Облачный перевод"])

    with patch.object(cache_mod.translation_cache, "store", side_effect=_boom_store):
        result = await translate_text("cloud store boom", "en", "ru", openclaw_client=mock_client)

    assert result.translated == "Облачный перевод"
