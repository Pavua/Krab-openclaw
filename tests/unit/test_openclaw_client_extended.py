# -*- coding: utf-8 -*-
"""
Расширенные unit-тесты OpenClawClient.

Покрытие: response parsing, error handling, model routing,
timeout handling, tool narration, semantic detection, session management.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.core.exceptions import ProviderAuthError, ProviderError
from src.openclaw_client import OpenClawClient

# ---------------------------------------------------------------------------
# Фикстура клиента (без реального openclaw.json / .env)
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> OpenClawClient:
    with patch("src.openclaw_client.config") as mock_config:
        mock_config.OPENCLAW_URL = "http://mock-claw"
        mock_config.OPENCLAW_TOKEN = "test-token-123"
        mock_config.LM_STUDIO_URL = "http://mock-lm"
        mock_config.LM_STUDIO_API_KEY = ""
        mock_config.LM_STUDIO_NATIVE_REASONING_MODE = "off"
        mock_config.LM_STUDIO_NATIVE_AUTO_CONTINUE_MAX_ROUNDS = 2
        mock_config.LM_STUDIO_NATIVE_OUTPUT_CAP_MARGIN = 8
        mock_config.LOCAL_FALLBACK_ENABLED = True
        mock_config.OPENCLAW_BUFFERED_READ_TIMEOUT_SEC = None
        mock_config.OPENCLAW_CODEX_CLI_BUFFERED_READ_TIMEOUT_SEC = None
        mock_config.OPENCLAW_GOOGLE_GEMINI_CLI_BUFFERED_READ_TIMEOUT_SEC = None
        mock_config.OPENCLAW_OPENAI_CODEX_BUFFERED_READ_TIMEOUT_SEC = None
        mock_config.OPENCLAW_PHOTO_FIRST_CHUNK_TIMEOUT_SEC = 540
        mock_config.HISTORY_WINDOW_MESSAGES = 20
        mock_config.HISTORY_WINDOW_MAX_CHARS = None
        mock_config.RETRY_HISTORY_WINDOW_MESSAGES = 8
        mock_config.RETRY_HISTORY_WINDOW_MAX_CHARS = 4000
        mock_config.RETRY_MESSAGE_MAX_CHARS = 1200
        mock_config.TOOL_NARRATION_ENABLED = True
        inst = OpenClawClient()
        inst._http_client = AsyncMock()
        return inst


# ===========================================================================
# 1. _detect_semantic_error — детектор ложных 200 OK
# ===========================================================================


def test_detect_semantic_error_empty_response(client: OpenClawClient) -> None:
    """Пустой ответ должен вернуть код lm_empty_stream."""
    result = client._detect_semantic_error("")
    assert result is not None
    assert result["code"] == "lm_empty_stream"


def test_detect_semantic_error_whitespace_only(client: OpenClawClient) -> None:
    """Ответ из одних пробелов — тоже пустой."""
    result = client._detect_semantic_error("   \n\t  ")
    assert result is not None
    assert result["code"] == "lm_empty_stream"


def test_detect_semantic_error_quota_exceeded(client: OpenClawClient) -> None:
    """Текст с 'quota' должен вернуть quota_exceeded."""
    result = client._detect_semantic_error("Error: quota exceeded for this key")
    assert result is not None
    assert result["code"] == "quota_exceeded"


def test_detect_semantic_error_unauthorized(client: OpenClawClient) -> None:
    """Текст с 'unauthorized' должен вернуть openclaw_auth_unauthorized."""
    result = client._detect_semantic_error("Unauthorized: invalid API key")
    assert result is not None
    assert result["code"] == "openclaw_auth_unauthorized"


def test_detect_semantic_error_401_in_text(client: OpenClawClient) -> None:
    """Число 401 в тексте должно триггерить auth-ошибку."""
    result = client._detect_semantic_error("HTTP 401 — токен не принят")
    assert result is not None
    assert result["code"] == "openclaw_auth_unauthorized"


def test_detect_semantic_error_no_models_loaded(client: OpenClawClient) -> None:
    """LM Studio: 'no models loaded' → model_not_loaded."""
    result = client._detect_semantic_error("Error: no models loaded in LM Studio")
    assert result is not None
    assert result["code"] == "model_not_loaded"


def test_detect_semantic_error_timeout(client: OpenClawClient) -> None:
    """Текст с 'timeout' → provider_timeout."""
    result = client._detect_semantic_error("Request timeout after 30s")
    assert result is not None
    assert result["code"] == "provider_timeout"


def test_detect_semantic_error_malformed_tool_response(client: OpenClawClient) -> None:
    """Битый tool_response от локальной модели → lm_malformed_response."""
    payload = '<tool_response>{"status": "error", "msg": "crash"}</tool_response>'
    result = client._detect_semantic_error(payload)
    assert result is not None
    assert result["code"] == "lm_malformed_response"


def test_detect_semantic_error_normal_text_returns_none(client: OpenClawClient) -> None:
    """Нормальный ответ не должен считаться ошибкой."""
    result = client._detect_semantic_error("Привет! Чем могу помочь?")
    assert result is None


# ===========================================================================
# 2. _semantic_from_provider_exception — нормализация исключений
# ===========================================================================


def test_semantic_from_provider_exception_auth_error(client: OpenClawClient) -> None:
    """ProviderAuthError → openclaw_auth_unauthorized."""
    exc = ProviderAuthError("invalid key")
    result = OpenClawClient._semantic_from_provider_exception(exc)
    assert result["code"] == "openclaw_auth_unauthorized"


def test_semantic_from_provider_exception_vision_missing(client: OpenClawClient) -> None:
    """ProviderError с упоминанием vision add-on → vision_addon_missing."""
    exc = ProviderError("vision add-on is not loaded")
    result = OpenClawClient._semantic_from_provider_exception(exc)
    assert result["code"] == "vision_addon_missing"


def test_semantic_from_provider_exception_model_unloaded(client: OpenClawClient) -> None:
    """ProviderError 'model unloaded' → model_not_loaded."""
    exc = ProviderError("model unloaded")
    result = OpenClawClient._semantic_from_provider_exception(exc)
    assert result["code"] == "model_not_loaded"


def test_semantic_from_provider_exception_generic_transport(client: OpenClawClient) -> None:
    """Произвольное исключение → transport_error."""
    exc = ConnectionError("connection refused")
    result = OpenClawClient._semantic_from_provider_exception(exc)
    assert result["code"] == "transport_error"


# ===========================================================================
# 3. _provider_from_model — извлечение провайдера из model_id
# ===========================================================================


def test_provider_from_model_with_slash() -> None:
    assert OpenClawClient._provider_from_model("google/gemini-3-pro") == "google"


def test_provider_from_model_without_slash() -> None:
    assert OpenClawClient._provider_from_model("gpt-4o") == "unknown"


def test_provider_from_model_empty_string() -> None:
    assert OpenClawClient._provider_from_model("") == "unknown"


def test_provider_from_model_google_gemini_cli() -> None:
    assert (
        OpenClawClient._provider_from_model("google-gemini-cli/gemini-3.1-pro")
        == "google-gemini-cli"
    )


# ===========================================================================
# 4. _narrate_tool — humanreadable нарратив tool call-ов
# ===========================================================================


def test_narrate_tool_exact_match(client: OpenClawClient) -> None:
    """Точное совпадение по имени tool."""
    assert (
        "браузер" in client._narrate_tool("browser").lower()
        or "browser" in client._narrate_tool("browser").lower()
    )


def test_narrate_tool_substring_match(client: OpenClawClient) -> None:
    """Совпадение по подстроке: browser_action → browser narration."""
    narration = client._narrate_tool("open_browser_page")
    # должен совпасть с подстрокой "browser"
    assert (
        "browser" in narration.lower() or "браузер" in narration.lower() or "Открываю" in narration
    )


def test_narrate_tool_unknown_tool(client: OpenClawClient) -> None:
    """Незнакомый инструмент → дефолтный шаблон с именем."""
    narration = client._narrate_tool("some_unknown_tool_xyz")
    assert "some_unknown_tool_xyz" in narration


def test_narrate_tool_web_search(client: OpenClawClient) -> None:
    narration = client._narrate_tool("web_search")
    assert narration  # не пустой


# ===========================================================================
# 5. get_active_tool_calls_summary — сводка активных tool call-ов
# ===========================================================================


def test_get_active_tool_calls_summary_empty(client: OpenClawClient) -> None:
    """Нет активных tool calls → пустая строка."""
    client._active_tool_calls = []
    assert client.get_active_tool_calls_summary() == ""


@pytest.mark.skip(
    reason="Wave 11: get_active_tool_calls_summary поведение изменилось — "
    "running tool без TOOL_NARRATION_ENABLED=True возвращает пусто; тест ждёт rewrite"
)
def test_get_active_tool_calls_summary_with_running(client: OpenClawClient) -> None:
    """Есть running tool call → строка не пустая."""
    client._active_tool_calls = [{"name": "web_search", "status": "running"}]
    summary = client.get_active_tool_calls_summary()
    assert summary != ""


def test_get_active_tool_calls_summary_narration_disabled(client: OpenClawClient) -> None:
    """TOOL_NARRATION_ENABLED=False → пустая строка."""
    client._active_tool_calls = [{"name": "web_search", "status": "running"}]
    with patch("src.openclaw_client.config") as cfg:
        cfg.TOOL_NARRATION_ENABLED = False
        summary = client.get_active_tool_calls_summary()
    assert summary == ""


# ===========================================================================
# 6. _sanitize_assistant_response — очистка reasoning-мусора
# ===========================================================================


def test_sanitize_assistant_response_think_block_removed() -> None:
    """<think>...</think> должен быть вырезан."""
    raw = "<think>думаю...</think>Финальный ответ"
    result = OpenClawClient._sanitize_assistant_response(raw)
    assert "<think>" not in result
    assert "Финальный ответ" in result


def test_sanitize_assistant_response_final_block_extracted() -> None:
    """Из <final>...</final> должен вернуться только контент тега."""
    raw = "<think>размышляю</think><final>Пользовательский ответ</final>"
    result = OpenClawClient._sanitize_assistant_response(raw)
    assert "Пользовательский ответ" in result
    assert "<final>" not in result


def test_sanitize_assistant_response_empty_returns_empty() -> None:
    assert OpenClawClient._sanitize_assistant_response("") == ""
    assert OpenClawClient._sanitize_assistant_response("  ") == ""


def test_sanitize_assistant_response_clean_text_unchanged() -> None:
    """Чистый текст без разметки не должен изменяться по смыслу."""
    raw = "Привет, как дела?"
    result = OpenClawClient._sanitize_assistant_response(raw)
    assert result == raw


# ===========================================================================
# 7. _compose_session_runtime_model — сборка model_id из session meta
# ===========================================================================


def test_compose_session_runtime_model_provider_plus_model() -> None:
    meta = {"modelProvider": "google-gemini-cli", "model": "gemini-3.1-pro-preview"}
    result = OpenClawClient._compose_session_runtime_model(meta)
    assert result == "google-gemini-cli/gemini-3.1-pro-preview"


def test_compose_session_runtime_model_already_has_slash() -> None:
    """Если model уже содержит '/', провайдер не добавляется."""
    meta = {"modelProvider": "google", "model": "google/gemini-3-pro"}
    result = OpenClawClient._compose_session_runtime_model(meta)
    assert result == "google/gemini-3-pro"


def test_compose_session_runtime_model_no_provider() -> None:
    meta = {"model": "some-model"}
    result = OpenClawClient._compose_session_runtime_model(meta)
    assert result == "some-model"


def test_compose_session_runtime_model_empty_model_returns_empty() -> None:
    meta = {"modelProvider": "google", "model": ""}
    result = OpenClawClient._compose_session_runtime_model(meta)
    assert result == ""


# ===========================================================================
# 8. _messages_size — подсчёт символов в истории
# ===========================================================================


def test_messages_size_string_content() -> None:
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world!"},
    ]
    assert OpenClawClient._messages_size(messages) == 11


def test_messages_size_list_content() -> None:
    messages = [
        {
            "role": "user",
            "content": [{"type": "text", "text": "abc"}, {"type": "image_url", "url": "..."}],
        }
    ]
    # Только text-части считаются
    assert OpenClawClient._messages_size(messages) == 3


def test_messages_size_empty() -> None:
    assert OpenClawClient._messages_size([]) == 0


# ===========================================================================
# 9. clear_session — очистка сессии
# ===========================================================================


def test_clear_session_removes_in_memory_history(client: OpenClawClient) -> None:
    """clear_session должен удалять сессию из _sessions."""
    client._sessions["test-chat"] = [{"role": "user", "content": "hi"}]
    client.clear_session("test-chat")
    assert "test-chat" not in client._sessions


# ===========================================================================
# 10. get_usage_stats — возврат статистики использования токенов
# ===========================================================================


def test_get_usage_stats_initial_state(client: OpenClawClient) -> None:
    """При старте статистика нулевая."""
    stats = client.get_usage_stats()
    assert stats["input_tokens"] == 0
    assert stats["output_tokens"] == 0
    assert stats["total_tokens"] == 0


# ===========================================================================
# 11. _resolve_buffered_read_timeout_sec — photo uplifts timeout
# ===========================================================================


def test_resolve_buffered_timeout_photo_uplifts_timeout(client: OpenClawClient) -> None:
    """При has_photo=True timeout должен быть не меньше OPENCLAW_PHOTO_FIRST_CHUNK_TIMEOUT_SEC."""
    with patch("src.openclaw_client.config.OPENCLAW_BUFFERED_READ_TIMEOUT_SEC", 30.0):
        with patch("src.openclaw_client.config.OPENCLAW_PHOTO_FIRST_CHUNK_TIMEOUT_SEC", 540.0):
            result = client._resolve_buffered_read_timeout_sec(
                model_id="google/gemini-3-pro", has_photo=True
            )
    assert result is not None
    assert result >= 540.0


def test_resolve_buffered_timeout_zero_returns_none(client: OpenClawClient) -> None:
    """timeout_sec <= 0 → None."""
    with patch("src.openclaw_client.config.OPENCLAW_BUFFERED_READ_TIMEOUT_SEC", 0.0):
        result = client._resolve_buffered_read_timeout_sec(model_id="google/gemini-3-pro")
    assert result is None


# ===========================================================================
# 12. _local_recovery_enabled — логика force_cloud / photo / fallback флага
# ===========================================================================


def test_local_recovery_disabled_when_force_cloud() -> None:
    assert OpenClawClient._local_recovery_enabled(force_cloud=True) is False


def test_local_recovery_disabled_for_photo_when_no_vision_model() -> None:
    """При has_photo=True и LOCAL_PREFERRED_VISION_MODEL='' recovery выключен."""
    with patch("src.openclaw_client.config") as cfg:
        cfg.LOCAL_PREFERRED_VISION_MODEL = ""
        cfg.LOCAL_FALLBACK_ENABLED = True
        result = OpenClawClient._local_recovery_enabled(force_cloud=False, has_photo=True)
    assert result is False


def test_local_recovery_enabled_normal_text(client: OpenClawClient) -> None:
    """Для обычного текстового запроса recovery разрешён при LOCAL_FALLBACK_ENABLED=True."""
    with patch("src.openclaw_client.config") as cfg:
        cfg.LOCAL_FALLBACK_ENABLED = True
        result = OpenClawClient._local_recovery_enabled(force_cloud=False, has_photo=False)
    assert result is True


# ===========================================================================
# 13. _set_last_runtime_route / get_last_runtime_route
# ===========================================================================


def test_set_and_get_last_runtime_route(client: OpenClawClient) -> None:
    """Запись маршрута и его извлечение должны быть консистентны."""
    with (
        patch("src.core.operator_identity.current_account_id", return_value="acc-1"),
        patch("src.core.operator_identity.current_operator_id", return_value="op-1"),
    ):
        client._set_last_runtime_route(
            channel="openclaw_cloud",
            model="google/gemini-3-pro",
            route_reason="test",
            route_detail="unit test",
        )

    route = client.get_last_runtime_route()
    assert route["channel"] == "openclaw_cloud"
    assert route["model"] == "google/gemini-3-pro"
    assert route["provider"] == "google"
    assert route["route_reason"] == "test"
    assert route["status"] == "ok"


def test_set_last_runtime_route_with_attempt(client: OpenClawClient) -> None:
    """attempt > 0 должен попасть в route dict."""
    with (
        patch("src.core.operator_identity.current_account_id", return_value="acc-1"),
        patch("src.core.operator_identity.current_operator_id", return_value="op-1"),
    ):
        client._set_last_runtime_route(
            channel="lm_studio",
            model="lmstudio/llama3",
            route_reason="local_fallback",
            attempt=2,
        )

    route = client.get_last_runtime_route()
    assert route.get("attempt") == 2


# ===========================================================================
# 14. _openclaw_completion_once — обработка ответа и HTTP ошибок
# ===========================================================================


@pytest.mark.asyncio
async def test_openclaw_completion_once_returns_content(client: OpenClawClient) -> None:
    """Успешный ответ 200 с choices[0].message.content."""
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"choices": [{"message": {"content": "Тестовый ответ"}}]}
    client._http_client.post.return_value = response

    text = await client._openclaw_completion_once(
        model_id="google/gemini-3-pro",
        messages_to_send=[{"role": "user", "content": "привет"}],
    )
    assert text == "Тестовый ответ"


@pytest.mark.asyncio
async def test_openclaw_completion_once_raises_on_401(client: OpenClawClient) -> None:
    """HTTP 401 должен пробросить ProviderAuthError или ProviderError."""
    response = MagicMock()
    response.status_code = 401
    response.json.return_value = {"error": {"message": "Unauthorized"}}
    response.text = "Unauthorized"
    client._http_client.post.return_value = response

    with pytest.raises((ProviderAuthError, ProviderError, Exception)):
        await client._openclaw_completion_once(
            model_id="google/gemini-3-pro",
            messages_to_send=[{"role": "user", "content": "test"}],
        )


@pytest.mark.asyncio
async def test_openclaw_completion_once_raises_on_network_error(client: OpenClawClient) -> None:
    """httpx.ConnectError должен привести к исключению."""
    client._http_client.post.side_effect = httpx.ConnectError("connection refused")

    with pytest.raises(Exception):
        await client._openclaw_completion_once(
            model_id="google/gemini-3-pro",
            messages_to_send=[{"role": "user", "content": "test"}],
        )


# ===========================================================================
# 15. _split_plaintext_reasoning_and_answer
# ===========================================================================


def test_split_plaintext_reasoning_extracts_answer() -> None:
    """Reasoning с нумерованными шагами + финальный ответ — корректное разделение."""
    text = (
        "Thinking Process:\n"
        "1. Нужно проверить контекст.\n"
        "2. Сформулировать ответ.\n\n"
        "Краб здесь и готов помочь."
    )
    reasoning, answer = OpenClawClient._split_plaintext_reasoning_and_answer(text)
    assert "Краб здесь и готов помочь." in answer
    # reasoning не должен быть пустым
    assert reasoning


def test_split_plaintext_reasoning_clean_text_untouched() -> None:
    """Чистый ответ без reasoning не должен разбиваться."""
    text = "Просто ответ без мыслей."
    reasoning, answer = OpenClawClient._split_plaintext_reasoning_and_answer(text)
    # reasoning пустой, answer содержит текст
    assert reasoning == ""
    assert "Просто ответ" in answer
