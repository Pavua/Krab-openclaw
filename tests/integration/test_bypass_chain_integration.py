# -*- coding: utf-8 -*-
"""Wave 32-A: integration tests для полного bypass chain.

Проверяет весь маршрут в send_message_stream:
  CLI bypass → Vertex bypass → Anthropic-Vertex bypass → Gemma bypass
  → Google-direct bypass → OpenClaw transport (fallback)

Каждый тест мокирует entry-point конкретного bypass и проверяет,
что именно он был вызван (или не вызван) при данном prefix модели.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.openclaw_client import OpenClawClient

# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

MESSAGES = [{"role": "user", "content": "ping"}]
BYPASS_TEXT = "bypass_answer"


def _make_oc_mock(fallback_text: str = "openclaw_fallback") -> AsyncMock:
    """Мок _openclaw_completion_once на случай fallback в OpenClaw транспорт."""
    m = AsyncMock(return_value=fallback_text)
    return m


async def _collect(gen: AsyncGenerator[str, None]) -> list[str]:
    """Собирает чанки из async generator."""
    result = []
    async for chunk in gen:
        result.append(chunk)
    return result


# ---------------------------------------------------------------------------
# Фикстура: отключаем все bypass по умолчанию, включаем точечно в каждом тесте
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _disable_all_bypass(monkeypatch: pytest.MonkeyPatch) -> None:
    """Отключаем все bypass env-флаги — каждый тест включает нужный точечно."""
    monkeypatch.setenv("KRAB_CLI_SUBPROCESS_BYPASS_ENABLED", "0")
    monkeypatch.setenv("KRAB_VERTEX_BYPASS_ENABLED", "0")
    monkeypatch.setenv("KRAB_ANTHROPIC_VERTEX_BYPASS_ENABLED", "0")
    monkeypatch.setenv("KRAB_GOOGLE_DIRECT_BYPASS_ENABLED", "0")


# ---------------------------------------------------------------------------
# Test 1: codex-cli/* → CLI bypass успешен, другие пути НЕ трогаются
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chain_codex_cli_first_attempt_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """codex-cli/gpt-5.5 → cli_subprocess_bypass engage → ответ без fallback."""
    monkeypatch.setenv("KRAB_CLI_SUBPROCESS_BYPASS_ENABLED", "1")

    # CLI bypass возвращает ответ
    cli_mock = AsyncMock(return_value=BYPASS_TEXT)

    with (
        patch(
            "src.integrations.cli_subprocess_bypass.complete_via_cli",
            cli_mock,
        ),
        patch.object(
            OpenClawClient,
            "_openclaw_completion_once",
            new_callable=AsyncMock,
        ) as oc_mock,
        # Vertex/AV/Gemma/Google-direct должны быть отключены (env уже 0)
    ):
        oc_mock.return_value = "openclaw_should_not_be_called"

        client = OpenClawClient()
        chunks = await _collect(
            client.send_message_stream(
                message="ping",
                chat_id="test-chain-cli-1",
                preferred_model="codex-cli/gpt-5.5",
            )
        )

    # CLI bypass вызван
    assert cli_mock.called, "complete_via_cli должен быть вызван для codex-cli/* модели"
    # В ответе — текст из bypass
    full = "".join(chunks)
    assert BYPASS_TEXT in full, f"Ожидали ответ bypass, получили: {full!r}"
    # OpenClaw транспорт НЕ должен быть вызван
    assert not oc_mock.called, "OpenClaw НЕ должен вызываться когда CLI bypass succeed"


# ---------------------------------------------------------------------------
# Test 2: codex-cli/* + CLI raise → НЕ fallback к vertex (не его префикс)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chain_codex_fails_no_vertex_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """codex-cli/* с упавшим CLI → fallback идёт в OpenClaw, не в vertex.

    Vertex bypass не должен engage — prefixes несовместимы.
    """
    monkeypatch.setenv("KRAB_CLI_SUBPROCESS_BYPASS_ENABLED", "1")
    monkeypatch.setenv("KRAB_VERTEX_BYPASS_ENABLED", "1")

    # CLI падает
    cli_mock = AsyncMock(side_effect=RuntimeError("CLI timeout"))
    # Vertex не должен вызываться (модель codex-cli/*, не google-vertex/*)
    vertex_mock = AsyncMock(return_value="vertex_should_not_be_called")

    with (
        patch(
            "src.integrations.cli_subprocess_bypass.complete_via_cli",
            cli_mock,
        ),
        patch(
            "src.integrations.google_vertex_direct.complete_via_vertex",
            vertex_mock,
        ),
        patch.object(
            OpenClawClient,
            "_openclaw_completion_once",
            new_callable=AsyncMock,
        ) as oc_mock,
    ):
        oc_mock.return_value = "openclaw_ok"

        client = OpenClawClient()
        chunks = await _collect(
            client.send_message_stream(
                message="ping",
                chat_id="test-chain-cli-fail-2",
                preferred_model="codex-cli/gpt-5.5",
            )
        )

    # CLI пробовался
    assert cli_mock.called, "CLI bypass должен пробоваться"
    # Vertex НЕ должен вызываться
    assert not vertex_mock.called, (
        "Vertex НЕ должен вызываться при codex-cli/* модели (prefix-mismatch)"
    )
    # Упали в OpenClaw (fallback)
    full = "".join(chunks)
    assert full, "Должен быть какой-то ответ (от OpenClaw)"


# ---------------------------------------------------------------------------
# Test 3: google-vertex/* → Vertex bypass engage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chain_google_vertex_engages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """google-vertex/gemini-2.5-pro → vertex bypass engage, ответ возвращён."""
    monkeypatch.setenv("KRAB_VERTEX_BYPASS_ENABLED", "1")

    vertex_mock = AsyncMock(return_value=BYPASS_TEXT)

    with (
        patch(
            "src.integrations.google_vertex_direct.complete_via_vertex",
            vertex_mock,
        ),
        patch.object(
            OpenClawClient,
            "_openclaw_completion_once",
            new_callable=AsyncMock,
        ) as oc_mock,
    ):
        oc_mock.return_value = "openclaw_fallback"

        client = OpenClawClient()
        chunks = await _collect(
            client.send_message_stream(
                message="ping",
                chat_id="test-chain-vertex-3",
                preferred_model="google-vertex/gemini-2.5-pro",
            )
        )

    assert vertex_mock.called, "complete_via_vertex должен быть вызван для google-vertex/* модели"
    full = "".join(chunks)
    assert BYPASS_TEXT in full, f"Ожидали bypass ответ, получили: {full!r}"
    assert not oc_mock.called, "OpenClaw НЕ должен вызываться когда Vertex bypass succeed"


# ---------------------------------------------------------------------------
# Test 4: anthropic-vertex/* → anthropic_vertex bypass engage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chain_anthropic_vertex_engages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """anthropic-vertex/claude-opus-4-7 → anthropic_vertex bypass engage."""
    monkeypatch.setenv("KRAB_ANTHROPIC_VERTEX_BYPASS_ENABLED", "1")

    av_mock = AsyncMock(return_value=BYPASS_TEXT)

    with (
        patch(
            "src.integrations.anthropic_vertex_direct.complete_via_anthropic_vertex",
            av_mock,
        ),
        patch.object(
            OpenClawClient,
            "_openclaw_completion_once",
            new_callable=AsyncMock,
        ) as oc_mock,
    ):
        oc_mock.return_value = "openclaw_fallback"

        client = OpenClawClient()
        chunks = await _collect(
            client.send_message_stream(
                message="ping",
                chat_id="test-chain-av-4",
                preferred_model="anthropic-vertex/claude-opus-4-7",
            )
        )

    assert av_mock.called, "complete_via_anthropic_vertex должен быть вызван"
    full = "".join(chunks)
    assert BYPASS_TEXT in full, f"Ожидали bypass ответ, получили: {full!r}"
    assert not oc_mock.called, "OpenClaw НЕ должен вызываться когда AV bypass succeed"


# ---------------------------------------------------------------------------
# Test 5: gemma-3-27b-it → gemma path (google_genai_direct.complete_direct)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chain_gemma_engages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """gemma-3-27b-it (без провайдер-префикса) → gemma path в google_genai_direct."""
    monkeypatch.setenv("KRAB_GOOGLE_DIRECT_BYPASS_ENABLED", "1")
    monkeypatch.setenv("GEMINI_API_KEY_FREE", "stub-free-key")

    gemma_mock = AsyncMock(return_value=BYPASS_TEXT)

    with (
        patch(
            "src.integrations.google_genai_direct.complete_direct",
            gemma_mock,
        ),
        patch.object(
            OpenClawClient,
            "_openclaw_completion_once",
            new_callable=AsyncMock,
        ) as oc_mock,
    ):
        oc_mock.return_value = "openclaw_fallback"

        client = OpenClawClient()
        chunks = await _collect(
            client.send_message_stream(
                message="ping",
                chat_id="test-chain-gemma-5",
                preferred_model="gemma-3-27b-it",
            )
        )

    # gemma модель идёт через google_genai_direct.complete_direct
    assert gemma_mock.called, (
        "complete_direct должен вызываться для gemma-* модели через KRAB_GOOGLE_DIRECT_BYPASS_ENABLED"
    )
    full = "".join(chunks)
    assert BYPASS_TEXT in full, f"Ожидали bypass ответ, получили: {full!r}"
    assert not oc_mock.called, "OpenClaw НЕ должен вызываться когда gemma bypass succeed"


# ---------------------------------------------------------------------------
# Test 6: google/gemini-3.1-pro-preview → google_direct bypass (paid AI Studio)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chain_paid_google_engages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """google/gemini-3.1-pro-preview → google_direct bypass engage."""
    monkeypatch.setenv("KRAB_GOOGLE_DIRECT_BYPASS_ENABLED", "1")
    monkeypatch.setenv("GEMINI_API_KEY_FREE", "stub-free-key")

    google_mock = AsyncMock(return_value=BYPASS_TEXT)

    with (
        patch(
            "src.integrations.google_genai_direct.complete_direct",
            google_mock,
        ),
        patch.object(
            OpenClawClient,
            "_openclaw_completion_once",
            new_callable=AsyncMock,
        ) as oc_mock,
    ):
        oc_mock.return_value = "openclaw_fallback"

        client = OpenClawClient()
        chunks = await _collect(
            client.send_message_stream(
                message="ping",
                chat_id="test-chain-google-6",
                preferred_model="google/gemini-3.1-pro-preview",
            )
        )

    assert google_mock.called, "complete_direct должен быть вызван для google/* модели"
    full = "".join(chunks)
    assert BYPASS_TEXT in full, f"Ожидали bypass ответ, получили: {full!r}"
    assert not oc_mock.called, "OpenClaw НЕ должен вызываться когда google direct bypass succeed"


# ---------------------------------------------------------------------------
# Test 7: openai/gpt-4o → ни один bypass не совпадает → OpenClaw transport
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chain_unknown_model_falls_to_openclaw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """openai/gpt-4o → не подходит ни один bypass → OpenClaw broken transport.

    Все bypass отключены через env-fixture. OpenClaw возвращает "TOOL_ERROR"
    (имитируем broken transport), проверяем что именно он был вызван.
    """
    # Все bypass выключены (autouse fixture), проверяем что OpenClaw вызывается
    with patch.object(
        OpenClawClient,
        "_openclaw_completion_once",
        new_callable=AsyncMock,
    ) as oc_mock:
        oc_mock.return_value = "openclaw_transport_ok"

        client = OpenClawClient()
        chunks = await _collect(
            client.send_message_stream(
                message="ping",
                chat_id="test-chain-unknown-7",
                preferred_model="openai/gpt-4o",
            )
        )

    # OpenClaw должен быть вызван (единственный оставшийся путь)
    assert oc_mock.called, "OpenClaw должен вызываться когда bypass не подходит"
    full = "".join(chunks)
    assert full, "Должен быть ответ от OpenClaw"


# ---------------------------------------------------------------------------
# Test 8: все bypass отключены через env → OpenClaw вызывается
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chain_all_bypass_disabled_falls_to_openclaw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Все KRAB_*_BYPASS_ENABLED=0 → только OpenClaw transport.

    Использует google/* модель (которая иначе шла бы через google_direct bypass),
    но все bypass env-флаги явно выключены — должен использоваться OpenClaw.
    """
    # Все bypass уже отключены через autouse _disable_all_bypass фикстуру
    google_mock = AsyncMock(return_value="should_not_be_called")

    with (
        patch(
            "src.integrations.google_genai_direct.complete_direct",
            google_mock,
        ),
        patch.object(
            OpenClawClient,
            "_openclaw_completion_once",
            new_callable=AsyncMock,
        ) as oc_mock,
    ):
        oc_mock.return_value = "openclaw_ok_direct"

        client = OpenClawClient()
        chunks = await _collect(
            client.send_message_stream(
                message="ping",
                chat_id="test-chain-disabled-8",
                preferred_model="google/gemini-3-pro-preview",
            )
        )

    # google_direct bypass НЕ должен был вызваться (env=0)
    assert not google_mock.called, (
        "google_direct bypass НЕ должен вызываться при KRAB_GOOGLE_DIRECT_BYPASS_ENABLED=0"
    )
    # OpenClaw должен вызваться
    assert oc_mock.called, "OpenClaw должен вызываться когда все bypass выключены"
    full = "".join(chunks)
    assert "openclaw_ok_direct" in full, f"Ожидали ответ OpenClaw, получили: {full!r}"
