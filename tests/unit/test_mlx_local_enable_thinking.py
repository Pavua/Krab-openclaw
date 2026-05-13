"""Wave 221: тесты для MLX local thinking-mode fix.

Часть A: при отправке запроса на mlx_lm.server (:8088 или префикс
`mlx-local-kv4/`) в payload добавляется `chat_template_args.enable_thinking
= false`, иначе Gemma пишет ответ в `message.reasoning` вместо `content`.

Часть B: при парсинге ответа, если `content` пуст, читаем `reasoning`
как defensive fallback и логируем warning.
"""

from __future__ import annotations

from typing import Any

from src.openclaw_client import (
    _apply_mlx_disable_thinking,
    _is_mlx_local_target,
    extract_message_text,
)

# ── Part A: targeting MLX local ───────────────────────────────────────────


def test_is_mlx_local_target_by_url_8088() -> None:
    """URL содержит `:8088` → distinct MLX local."""
    assert _is_mlx_local_target(base_url="http://127.0.0.1:8088", model_id="gemma-4-26b")


def test_is_mlx_local_target_by_model_prefix() -> None:
    """Model id начинается с `mlx-local-kv4/` → MLX local."""
    assert _is_mlx_local_target(
        base_url="http://gateway.example.com",
        model_id="mlx-local-kv4/qwen3-4b-kv4",
    )


def test_is_mlx_local_target_negative_other_url() -> None:
    """Обычный gateway URL без :8088 и без MLX-префикса → не MLX."""
    assert not _is_mlx_local_target(
        base_url="http://127.0.0.1:18789", model_id="google/gemini-3-pro-preview"
    )


def test_is_mlx_local_target_negative_lm_studio_1234() -> None:
    """LM Studio :1234 ≠ MLX local :8088."""
    assert not _is_mlx_local_target(
        base_url="http://127.0.0.1:1234", model_id="lmstudio/gemma-4-e4b"
    )


def test_is_mlx_local_target_handles_none() -> None:
    """None-аргументы не падают и возвращают False."""
    assert not _is_mlx_local_target(base_url=None, model_id=None)


def test_apply_mlx_disable_thinking_adds_flag_to_empty_payload() -> None:
    """В пустой payload добавляется chat_template_args.enable_thinking=False."""
    payload: dict[str, Any] = {"model": "openclaw", "messages": []}
    _apply_mlx_disable_thinking(payload)
    assert payload["chat_template_args"] == {"enable_thinking": False}


def test_apply_mlx_disable_thinking_preserves_existing_args() -> None:
    """Существующие ключи в chat_template_args сохраняются."""
    payload: dict[str, Any] = {"chat_template_args": {"other_flag": True}}
    _apply_mlx_disable_thinking(payload)
    assert payload["chat_template_args"]["other_flag"] is True
    assert payload["chat_template_args"]["enable_thinking"] is False


# ── Part B: defensive reasoning fallback ─────────────────────────────────


def test_extract_message_text_prefers_content() -> None:
    """Если есть и content и reasoning — выбираем content (нормальный путь)."""
    msg = {"content": "Hello!", "reasoning": "Thinking..."}
    assert extract_message_text(msg) == "Hello!"


def test_extract_message_text_falls_back_to_reasoning() -> None:
    """Пустой content + непустой reasoning → возвращаем reasoning."""
    msg = {"content": "", "reasoning": "Реальный ответ внутри thinking"}
    assert extract_message_text(msg) == "Реальный ответ внутри thinking"


def test_extract_message_text_whitespace_content_falls_back() -> None:
    """Whitespace-only content тоже считается пустым."""
    msg = {"content": "   \n  ", "reasoning": "fallback"}
    assert extract_message_text(msg) == "fallback"


def test_extract_message_text_both_empty_returns_empty_string() -> None:
    """И content, и reasoning пусты → empty string (не None)."""
    msg = {"content": "", "reasoning": ""}
    assert extract_message_text(msg) == ""


def test_extract_message_text_missing_keys_returns_empty() -> None:
    """Отсутствующие ключи трактуются как пустые."""
    assert extract_message_text({}) == ""


def test_extract_message_text_logs_warning_on_fallback() -> None:
    """При fallback к reasoning логируется warning `mlx_local_thinking_fallback`.

    Используем Mock вместо caplog: structlog kwargs не отображаются в plain
    logging output, поэтому проверяем сам вызов warning(...).
    """
    from unittest.mock import MagicMock

    mock_logger = MagicMock()
    msg = {"content": "", "reasoning": "ответ из reasoning"}
    out = extract_message_text(msg, model_id="mlx-local-kv4/gemma-4-26b", logger_=mock_logger)
    assert out == "ответ из reasoning"
    mock_logger.warning.assert_called_once()
    args, kwargs = mock_logger.warning.call_args
    assert args[0] == "mlx_local_thinking_fallback"
    assert kwargs.get("model") == "mlx-local-kv4/gemma-4-26b"


def test_extract_message_text_no_warning_when_content_present() -> None:
    """Если content не пуст — warning не пишется."""
    from unittest.mock import MagicMock

    mock_logger = MagicMock()
    msg = {"content": "ok", "reasoning": "(should be ignored)"}
    out = extract_message_text(msg, model_id="anything", logger_=mock_logger)
    assert out == "ok"
    mock_logger.warning.assert_not_called()
