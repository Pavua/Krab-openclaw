"""Wave 47-B: тесты для per-response model footer.

Производственный сценарий: после fallback от codex (quota) к gemini Krab
не сообщал какая модель отвечала. Footer теперь явно показывает:
  📡 _gemini-3-pro-preview (fallback после quota)_
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.userbot.llm_text_processing import (
    _append_model_footer,
    _shorten_model_id,
)

# ---------------------------------------------------------------------------
# _shorten_model_id
# ---------------------------------------------------------------------------


def test_shorten_strips_google_prefix() -> None:
    assert _shorten_model_id("google/gemini-3-pro-preview") == "gemini-3-pro-preview"


def test_shorten_strips_openai_prefix() -> None:
    assert _shorten_model_id("openai/gpt-4o") == "gpt-4o"


def test_shorten_keeps_codex_cli_prefix() -> None:
    """codex-cli/* остаётся целиком — пользователю важно видеть что был CLI bypass."""
    assert _shorten_model_id("codex-cli/gpt-5.5") == "codex-cli/gpt-5.5"


def test_shorten_keeps_vertex_prefix() -> None:
    assert (
        _shorten_model_id("google-vertex/gemini-3-pro-preview")
        == "google-vertex/gemini-3-pro-preview"
    )


def test_shorten_handles_empty() -> None:
    assert _shorten_model_id("") == ""
    assert _shorten_model_id(None) == ""  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _append_model_footer: positive path
# ---------------------------------------------------------------------------


def test_footer_appended_normal_response() -> None:
    out = _append_model_footer("Привет!", "google/gemini-3-pro-preview", enabled=True)
    assert out.startswith("Привет!")
    assert out.endswith("📡 _gemini-3-pro-preview_")
    assert "\n\n📡 _" in out


def test_footer_indicates_fallback_when_quota_engaged() -> None:
    out = _append_model_footer(
        "Ответ от gemini.",
        "google/gemini-3-pro-preview",
        fallback_used=True,
        fallback_reason="quota",
        enabled=True,
    )
    assert "fallback после quota" in out
    assert out.endswith("_gemini-3-pro-preview (fallback после quota)_")


def test_footer_fallback_without_reason() -> None:
    out = _append_model_footer(
        "Ответ.",
        "google/gemini-3-pro-preview",
        fallback_used=True,
        fallback_reason="",
        enabled=True,
    )
    assert "(fallback)" in out


# ---------------------------------------------------------------------------
# Negative paths
# ---------------------------------------------------------------------------


def test_footer_skipped_when_disabled() -> None:
    """env=0 → footer не добавляется."""
    text = "Ответ модели."
    out = _append_model_footer(text, "google/gemini-3-pro-preview", enabled=False)
    assert out == text


def test_footer_idempotent_on_reapply() -> None:
    """Повторный вызов на уже-helmeted тексте не дублирует footer."""
    once = _append_model_footer("Ответ.", "google/gemini-3-pro-preview", enabled=True)
    twice = _append_model_footer(once, "google/gemini-3-pro-preview", enabled=True)
    # Один и тот же footer, не два
    assert twice == once
    assert twice.count("📡 _") == 1


def test_footer_skipped_for_error_messages() -> None:
    """❌ / ⚠️ ответы skip footer — они и так информативны и короткие."""
    err1 = "❌ Облачный сервис недоступен."
    err2 = "⚠️ OpenClaw вернул неизвестную ошибку."
    assert _append_model_footer(err1, "google/gemini-3-pro-preview", enabled=True) == err1
    assert _append_model_footer(err2, "google/gemini-3-pro-preview", enabled=True) == err2


def test_footer_skipped_for_empty_text() -> None:
    assert _append_model_footer("", "google/gemini-3-pro-preview", enabled=True) == ""
    assert (
        _append_model_footer("   \n  ", "google/gemini-3-pro-preview", enabled=True)
        == "   \n  "
    )


def test_footer_skipped_for_empty_model() -> None:
    text = "Ответ"
    assert _append_model_footer(text, "", enabled=True) == text
    assert _append_model_footer(text, None, enabled=True) == text  # type: ignore[arg-type]


def test_footer_strips_trailing_whitespace_before_appending() -> None:
    """Если у text есть trailing whitespace — нормализуется перед footer."""
    out = _append_model_footer("Ответ.\n\n\n", "google/gemini-3-pro-preview", enabled=True)
    assert out == "Ответ.\n\n📡 _gemini-3-pro-preview_"


# ---------------------------------------------------------------------------
# Env gate via config (default=True)
# ---------------------------------------------------------------------------


def test_footer_default_enabled_via_config() -> None:
    """Без явного enabled — берётся config.KRAB_MODEL_FOOTER_ENABLED (default True)."""
    with patch("src.userbot.llm_text_processing.config") as mock_cfg:
        mock_cfg.KRAB_MODEL_FOOTER_ENABLED = True
        out = _append_model_footer("Привет", "google/gemini-3-pro-preview")
        assert "📡" in out


def test_footer_disabled_via_config() -> None:
    with patch("src.userbot.llm_text_processing.config") as mock_cfg:
        mock_cfg.KRAB_MODEL_FOOTER_ENABLED = False
        out = _append_model_footer("Привет", "google/gemini-3-pro-preview")
        assert "📡" not in out
        assert out == "Привет"


# ---------------------------------------------------------------------------
# Codex CLI / Vertex models
# ---------------------------------------------------------------------------


def test_footer_codex_cli_full_label() -> None:
    out = _append_model_footer("Ответ", "codex-cli/gpt-5.5", enabled=True)
    assert out.endswith("_codex-cli/gpt-5.5_")


def test_footer_vertex_full_label() -> None:
    out = _append_model_footer(
        "Ответ", "google-vertex/gemini-3-pro-preview", enabled=True
    )
    assert out.endswith("_google-vertex/gemini-3-pro-preview_")
