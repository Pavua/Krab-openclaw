"""Тесты GeminiRerankProvider (Chado §6 P1 wiring)."""

from __future__ import annotations

import asyncio
import json  # noqa: F401
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.gemini_rerank_provider import (
    GeminiRerankProvider,
    _build_score_batch_prompt,
    _parse_score_response,
    default_provider,
)

# ---------------------------------------------------------------------------
# _parse_score_response — unit.
# ---------------------------------------------------------------------------


def test_parse_normal_scores():
    """Корректный ответ: [8, 5, 2] → нормализованные [0.8, 0.5, 0.2]."""
    result = _parse_score_response("[8, 5, 2]", expected_count=3)
    assert result == pytest.approx([0.8, 0.5, 0.2])


def test_parse_scores_embedded_in_text():
    """LLM может добавить текст вокруг — ищем первый JSON-массив."""
    raw = "Here are the scores: [7, 3, 9] — done."
    result = _parse_score_response(raw, expected_count=3)
    assert result == pytest.approx([0.7, 0.3, 0.9])


def test_parse_malformed_json_returns_empty():
    """Сломанный JSON → пустой список."""
    result = _parse_score_response("[8, 5, BROKEN]", expected_count=3)
    assert result == []


def test_parse_no_array_returns_empty():
    """Нет массива в ответе → пустой список."""
    result = _parse_score_response("I cannot provide scores.", expected_count=2)
    assert result == []


def test_parse_empty_string_returns_empty():
    """Пустой ответ → пустой список."""
    result = _parse_score_response("", expected_count=2)
    assert result == []


def test_parse_too_few_elements_returns_empty():
    """Меньше элементов чем ожидалось → пустой список (смещённые скоры опасны)."""
    result = _parse_score_response("[8, 5]", expected_count=3)
    assert result == []


def test_parse_clamps_out_of_range():
    """Значения вне [0,10] зажимаются."""
    result = _parse_score_response("[15, -3, 5]", expected_count=3)
    assert result == pytest.approx([1.0, 0.0, 0.5])


# ---------------------------------------------------------------------------
# _build_score_batch_prompt — unit.
# ---------------------------------------------------------------------------


def test_build_prompt_contains_query():
    prompt = _build_score_batch_prompt("test query", ["chunk one", "chunk two"])
    assert "test query" in prompt


def test_build_prompt_numbers_chunks():
    prompt = _build_score_batch_prompt("q", ["a", "b", "c"])
    assert "[0]" in prompt
    assert "[1]" in prompt
    assert "[2]" in prompt


def test_build_prompt_truncates_long_chunks():
    long_chunk = "x" * 500
    prompt = _build_score_batch_prompt("q", [long_chunk])
    # Чанк обрезается до 300 символов — итоговый промпт намного короче 500.
    lines = [l for l in prompt.split("\n") if "[0]" in l]
    assert len(lines) == 1
    assert len(lines[0]) < 320  # 300 + немного служебного текста


# ---------------------------------------------------------------------------
# GeminiRerankProvider.generate() — mock httpx.
# ---------------------------------------------------------------------------


def _mock_gemini_response(text: str) -> MagicMock:
    """Возвращает мок httpx.Response с текстом от Gemini."""
    resp_json = {"candidates": [{"content": {"parts": [{"text": text}]}}]}
    mock_resp = MagicMock()
    mock_resp.json.return_value = resp_json
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


@pytest.mark.asyncio
async def test_generate_returns_text(monkeypatch):
    """generate() возвращает текст из Gemini API через AI Studio fallback path.

    Wave 66 (2026-05-12): generate() теперь сначала пробует Vertex (bonus credits),
    fallback на AI Studio paid API. Этот test проверяет AI Studio path (legacy),
    поэтому disable Vertex через env.
    """
    monkeypatch.setenv("KRAB_GEMINI_RERANK_VERTEX_ENABLED", "0")
    provider = GeminiRerankProvider(api_key="AIzaFAKE")

    mock_resp = _mock_gemini_response("[8, 5, 2]")
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch("src.core.gemini_rerank_provider.httpx.AsyncClient", return_value=mock_client):
        result = await provider.generate("some prompt")

    assert result == "[8, 5, 2]"


@pytest.mark.asyncio
async def test_generate_via_vertex_preferred_when_enabled(monkeypatch):
    """Wave 66: при KRAB_GEMINI_RERANK_VERTEX_ENABLED=1 (default) сначала
    пробуется Vertex. AI Studio httpx path не используется → не уходит paid API key.
    """
    monkeypatch.setenv("KRAB_GEMINI_RERANK_VERTEX_ENABLED", "1")
    provider = GeminiRerankProvider(api_key="AIzaFAKE")

    # Mock Vertex genai SDK call — НЕ паtchим httpx (он не должен быть вызван)
    mock_httpx_client = AsyncMock()

    with patch.object(
        provider,
        "_generate_via_vertex",
        AsyncMock(return_value="[7, 4, 1]"),
    ) as mock_vertex:
        result = await provider.generate("test prompt")

    assert result == "[7, 4, 1]"
    mock_vertex.assert_awaited_once()
    # httpx не должен быть вызван — Vertex отработал first
    mock_httpx_client.post.assert_not_called()


@pytest.mark.asyncio
async def test_generate_vertex_failure_falls_back_to_ai_studio(monkeypatch):
    """Wave 66: если Vertex упал (SDK missing / ADC fail) — graceful fallback
    на AI Studio paid path. Это safety net чтобы system не падал полностью.
    """
    monkeypatch.setenv("KRAB_GEMINI_RERANK_VERTEX_ENABLED", "1")
    provider = GeminiRerankProvider(api_key="AIzaFAKE")

    mock_resp = _mock_gemini_response("[3, 2, 1]")
    mock_httpx_client = AsyncMock()
    mock_httpx_client.__aenter__ = AsyncMock(return_value=mock_httpx_client)
    mock_httpx_client.__aexit__ = AsyncMock(return_value=False)
    mock_httpx_client.post = AsyncMock(return_value=mock_resp)

    with patch.object(
        provider,
        "_generate_via_vertex",
        AsyncMock(side_effect=RuntimeError("Vertex ADC missing")),
    ):
        with patch(
            "src.core.gemini_rerank_provider.httpx.AsyncClient",
            return_value=mock_httpx_client,
        ):
            result = await provider.generate("test prompt")

    assert result == "[3, 2, 1]"


# ---------------------------------------------------------------------------
# GeminiRerankProvider.score_batch() — mock generate().
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_score_batch_normalizes_scores():
    """Мок generate возвращает [8, 5, 2] → нормализованные [0.8, 0.5, 0.2]."""
    provider = GeminiRerankProvider(api_key="AIzaFAKE")

    with patch.object(provider, "generate", AsyncMock(return_value="[8, 5, 2]")):
        scores = await provider.score_batch("query", ["a", "b", "c"])

    assert scores == pytest.approx([0.8, 0.5, 0.2])


@pytest.mark.asyncio
async def test_score_batch_malformed_json_returns_empty(caplog):
    """Если Gemini вернул не-JSON → score_batch возвращает [] и логирует warn."""
    import logging

    provider = GeminiRerankProvider(api_key="AIzaFAKE")

    with patch.object(provider, "generate", AsyncMock(return_value="not valid json at all")):
        with caplog.at_level(logging.WARNING, logger="src.core.gemini_rerank_provider"):
            scores = await provider.score_batch("query", ["a", "b"])

    assert scores == []
    assert any("no_json_array" in r.message or "no_json" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_score_batch_timeout_returns_empty():
    """Timeout → score_batch возвращает []."""
    provider = GeminiRerankProvider(api_key="AIzaFAKE", timeout=0.001)

    async def slow_generate(prompt: str) -> str:
        await asyncio.sleep(10)
        return "[8]"

    with patch.object(provider, "generate", slow_generate):
        scores = await provider.score_batch("query", ["chunk"])

    assert scores == []


@pytest.mark.asyncio
async def test_score_batch_empty_chunks():
    """Пустой список чанков → сразу []."""
    provider = GeminiRerankProvider(api_key="AIzaFAKE")
    scores = await provider.score_batch("query", [])
    assert scores == []


@pytest.mark.asyncio
async def test_score_batch_network_error_returns_empty():
    """Сетевая ошибка → score_batch возвращает []."""
    provider = GeminiRerankProvider(api_key="AIzaFAKE")

    with patch.object(provider, "generate", AsyncMock(side_effect=OSError("connection refused"))):
        scores = await provider.score_batch("query", ["a", "b"])

    assert scores == []


# ---------------------------------------------------------------------------
# default_provider() — env isolation.
# ---------------------------------------------------------------------------


def test_default_provider_no_key_returns_none(monkeypatch):
    """Нет API-ключей → default_provider() вернёт None."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY_FREE", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY_PAID", raising=False)
    monkeypatch.delenv("GEMINI_PAID_KEY_ENABLED", raising=False)
    assert default_provider() is None


def test_default_provider_with_free_key(monkeypatch):
    """Есть GEMINI_API_KEY_FREE → возвращает провайдер."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY_PAID", raising=False)
    monkeypatch.delenv("GEMINI_PAID_KEY_ENABLED", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY_FREE", "AIzaFREEKEY")

    provider = default_provider()
    assert provider is not None
    assert isinstance(provider, GeminiRerankProvider)
    assert provider._api_key == "AIzaFREEKEY"


def test_default_provider_paid_key_disabled(monkeypatch):
    """GEMINI_PAID_KEY_ENABLED=0 → paid key игнорируется, берётся free."""
    monkeypatch.setenv("GEMINI_PAID_KEY_ENABLED", "0")
    monkeypatch.setenv("GEMINI_API_KEY_PAID", "AIzaPAIDKEY")
    monkeypatch.setenv("GEMINI_API_KEY_FREE", "AIzaFREEKEY")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    provider = default_provider()
    assert provider is not None
    assert provider._api_key == "AIzaFREEKEY"


def test_default_provider_paid_key_enabled(monkeypatch):
    """GEMINI_PAID_KEY_ENABLED=1 → берётся paid key."""
    monkeypatch.setenv("GEMINI_PAID_KEY_ENABLED", "1")
    monkeypatch.setenv("GEMINI_API_KEY_PAID", "AIzaPAIDKEY")
    monkeypatch.setenv("GEMINI_API_KEY_FREE", "AIzaFREEKEY")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    provider = default_provider()
    assert provider is not None
    assert provider._api_key == "AIzaPAIDKEY"


def test_default_provider_fallback_to_generic_key(monkeypatch):
    """Только GEMINI_API_KEY → используется как fallback."""
    monkeypatch.delenv("GEMINI_API_KEY_FREE", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY_PAID", raising=False)
    monkeypatch.delenv("GEMINI_PAID_KEY_ENABLED", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaGENERICKEY")

    provider = default_provider()
    assert provider is not None
    assert provider._api_key == "AIzaGENERICKEY"


# ---------------------------------------------------------------------------
# Integration smoke: default_provider() → score_batch().
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_integration_score_batch_via_default_provider(monkeypatch):
    """Сквозной тест: default_provider() + mocked generate → правильные скоры."""
    monkeypatch.setenv("GEMINI_API_KEY_FREE", "AIzaFAKE")
    monkeypatch.delenv("GEMINI_API_KEY_PAID", raising=False)
    monkeypatch.delenv("GEMINI_PAID_KEY_ENABLED", raising=False)

    provider = default_provider()
    assert provider is not None

    with patch.object(provider, "generate", AsyncMock(return_value="[10, 0, 5]")):
        scores = await provider.score_batch("test", ["chunk a", "chunk b", "chunk c"])

    assert scores == pytest.approx([1.0, 0.0, 0.5])
