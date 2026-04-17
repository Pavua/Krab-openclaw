# -*- coding: utf-8 -*-
"""
Регрессии `!stats ecosystem` subcommand (и её алиасы `eco` / `health`).

Что тестируем:

1. **Dispatch**: `handle_stats(bot, message)` с args == "ecosystem" / "eco" /
   "health" роутит в `_handle_stats_ecosystem`, остальные значения (включая
   пустую строку) идут в стандартную `_render_stats_panel`.
2. **Форматирование**: `_format_ecosystem_report` корректно собирает текст
   из реального шейпа `/api/ecosystem/health` (status/checks/chain/resources/
   budget/recommendations) и опционального блока `session_10`.
3. **HTTP-путь**: `_handle_stats_ecosystem` парсит `{"ok": True, "report": {...}}`
   через мок httpx.AsyncClient и отдает reply одним сообщением.
4. **Graceful failure**: если httpx падает (timeout/network), пользователь
   получает понятное "❌ Ecosystem health недоступен: …" без исключения.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.handlers.command_handlers import (
    _format_ecosystem_report,
    _handle_stats_ecosystem,
    handle_stats,
)

# ---------------------------------------------------------------------------
# Fake pyrogram Message и bot
# ---------------------------------------------------------------------------


class _FakeMessage:
    """Минимальный двойник pyrogram Message с фиксацией текста reply()."""

    def __init__(self, text: str = "!stats") -> None:
        self.text = text
        self.replies: list[str] = []

    async def reply(self, text: str) -> None:
        self.replies.append(text)


def _make_bot_stub(args_value: str = "") -> SimpleNamespace:
    """
    Минимальный stub с `_get_command_args`, не требующий pyrogram client'а.

    Для теста dispatch важно только: _get_command_args → controls routing.
    Для `_handle_stats_ecosystem` никаких bot-полей не нужно (реплаит через message).
    """
    bot = SimpleNamespace()
    bot._get_command_args = lambda _msg: args_value  # type: ignore[attr-defined]
    return bot


# ---------------------------------------------------------------------------
# 1. Форматтер
# ---------------------------------------------------------------------------


def test_format_ecosystem_report_core_sections() -> None:
    """Основные секции `/api/ecosystem/health` → текст содержит ключевые поля."""
    report = {
        "status": "ok",
        "risk_level": "low",
        "degradation": "normal",
        "chain": {
            "active_ai_channel": "cloud",
            "fallback_ready": True,
            "voice_assist_ready": True,
        },
        "checks": {
            "openclaw": {"ok": True, "status": "ok", "latency_ms": 42},
            "local_lm": {"ok": True, "status": "ok", "latency_ms": 10},
        },
        "resources": {
            "cpu_percent": 12.3,
            "ram_percent": 55.0,
            "ram_available_gb": 16.2,
        },
        "budget": {"usage_percent": 42, "runway_days": 21, "is_economy_mode": False},
        "recommendations": ["Экосистема в норме: поддерживай текущий режим мониторинга."],
    }
    text = _format_ecosystem_report(report)

    assert "Ecosystem Health" in text
    # Overall
    assert "ok" in text and "normal" in text
    # Chain
    assert "cloud" in text
    # Checks
    assert "openclaw" in text and "42ms" in text
    assert "local_lm" in text
    # Resources
    assert "CPU=12.3%" in text and "RAM=55.0%" in text and "free=16.2GB" in text
    # Budget
    assert "usage=42%" in text and "runway=21d" in text
    # Recommendations
    assert "норме" in text


def test_format_ecosystem_report_with_session_10_block() -> None:
    """Блок `session_10` (если появится) рендерится с понятными иконками."""
    report = {
        "status": "degraded",
        "risk_level": "medium",
        "degradation": "degraded_to_local_fallback",
        "session_10": {
            "memory_validator": {
                "available": True,
                "safe_total": 100,
                "injection_blocked_total": 2,
                "pending_count": 1,
            },
            "memory_archive": {
                "exists": True,
                "message_count": 42000,
                "size_bytes": 44040192,  # ~42 MB
            },
            "dedicated_chrome": {"enabled": True, "running": True, "port": 9222},
            "auto_restart": {"enabled": False, "total_attempts_last_hour": 0},
        },
    }
    text = _format_ecosystem_report(report)

    assert "Session 10:" in text
    assert "safe=100" in text
    assert "blocked=2" in text
    assert "pending=1" in text
    # "42000" форматируется как "42 000" (пробел разделитель)
    assert "42 000 msgs" in text
    assert "42 MB" in text
    assert "port=9222" in text
    assert "enabled=False" in text


def test_format_ecosystem_report_truncates_long_text() -> None:
    """Длинные отчёты (>3800 символов) обрезаются с пометкой `…(truncated)`."""
    # Генерим много рекомендаций — но ограничение `recs[:5]` не даст им утонуть,
    # поэтому наполним checks множеством фейковых подсистем, чтобы раздуть текст.
    checks = {f"service_{i}": {"ok": True, "status": "ok" * 200} for i in range(30)}
    report = {"status": "ok", "risk_level": "low", "degradation": "normal", "checks": checks}
    text = _format_ecosystem_report(report)

    assert text.endswith("…(truncated)")
    assert len(text) <= 3800 + len("\n…(truncated)")


# ---------------------------------------------------------------------------
# 2. HTTP-путь: _handle_stats_ecosystem
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stats_ecosystem_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: httpx отдаёт `{ok:True, report:{...}}` → Telegram reply собран."""
    payload = {
        "ok": True,
        "report": {
            "status": "ok",
            "risk_level": "low",
            "degradation": "normal",
            "chain": {
                "active_ai_channel": "cloud",
                "fallback_ready": True,
                "voice_assist_ready": True,
            },
            "checks": {"openclaw": {"ok": True, "status": "ok", "latency_ms": 5}},
        },
    }

    mock_response = MagicMock()
    mock_response.json = MagicMock(return_value=payload)

    mock_client_cm = MagicMock()
    mock_client_cm.__aenter__ = AsyncMock(return_value=mock_client_cm)
    mock_client_cm.__aexit__ = AsyncMock(return_value=None)
    mock_client_cm.get = AsyncMock(return_value=mock_response)
    monkeypatch.setattr("src.handlers.command_handlers.httpx.AsyncClient", lambda **kw: mock_client_cm)

    bot = _make_bot_stub()
    msg = _FakeMessage()
    await _handle_stats_ecosystem(bot, msg)  # type: ignore[arg-type]

    assert len(msg.replies) == 1
    text = msg.replies[0]
    assert "Ecosystem Health" in text
    assert "cloud" in text
    assert "openclaw" in text


@pytest.mark.asyncio
async def test_stats_ecosystem_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Если httpx падает — reply с человекочитаемой ошибкой, без исключения."""

    class _BrokenCM:
        async def __aenter__(self):
            raise RuntimeError("boom: connection refused")

        async def __aexit__(self, *a):
            return None

    monkeypatch.setattr(
        "src.handlers.command_handlers.httpx.AsyncClient", lambda **kw: _BrokenCM()
    )

    bot = _make_bot_stub()
    msg = _FakeMessage()
    await _handle_stats_ecosystem(bot, msg)  # type: ignore[arg-type]

    assert len(msg.replies) == 1
    assert "Ecosystem health недоступен" in msg.replies[0]
    assert "boom" in msg.replies[0]


@pytest.mark.asyncio
async def test_stats_ecosystem_empty_report(monkeypatch: pytest.MonkeyPatch) -> None:
    """Если API вернул пустой report → reply с предупреждением."""
    mock_response = MagicMock()
    mock_response.json = MagicMock(return_value={"ok": True, "report": {}})

    mock_client_cm = MagicMock()
    mock_client_cm.__aenter__ = AsyncMock(return_value=mock_client_cm)
    mock_client_cm.__aexit__ = AsyncMock(return_value=None)
    mock_client_cm.get = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(
        "src.handlers.command_handlers.httpx.AsyncClient", lambda **kw: mock_client_cm
    )

    bot = _make_bot_stub()
    msg = _FakeMessage()
    await _handle_stats_ecosystem(bot, msg)  # type: ignore[arg-type]

    assert len(msg.replies) == 1
    assert "пустой ответ" in msg.replies[0]


# ---------------------------------------------------------------------------
# 3. Dispatch: handle_stats → _handle_stats_ecosystem
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stats_dispatch_routes_ecosystem(monkeypatch: pytest.MonkeyPatch) -> None:
    """`!stats ecosystem` должен вызвать `_handle_stats_ecosystem` (не _render_stats_panel)."""
    called: dict[str, bool] = {"eco": False, "panel": False}

    async def _fake_eco(bot, message):  # noqa: ANN001
        called["eco"] = True

    def _fake_panel(bot):  # noqa: ANN001
        called["panel"] = True
        return "panel"

    monkeypatch.setattr(
        "src.handlers.command_handlers._handle_stats_ecosystem", _fake_eco
    )
    monkeypatch.setattr(
        "src.handlers.command_handlers._render_stats_panel", _fake_panel
    )

    bot = _make_bot_stub(args_value="ecosystem")
    msg = _FakeMessage()
    await handle_stats(bot, msg)  # type: ignore[arg-type]
    assert called["eco"] is True
    assert called["panel"] is False


@pytest.mark.asyncio
async def test_stats_dispatch_alias_eco(monkeypatch: pytest.MonkeyPatch) -> None:
    """Алиас `eco` должен тоже роутиться в ecosystem handler."""
    called: dict[str, bool] = {"eco": False}

    async def _fake_eco(bot, message):  # noqa: ANN001
        called["eco"] = True

    monkeypatch.setattr(
        "src.handlers.command_handlers._handle_stats_ecosystem", _fake_eco
    )

    bot = _make_bot_stub(args_value="ECO")  # регистр не важен
    msg = _FakeMessage()
    await handle_stats(bot, msg)  # type: ignore[arg-type]
    assert called["eco"] is True


@pytest.mark.asyncio
async def test_stats_dispatch_default_renders_panel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Без подкоманды (`!stats`) должен вызваться классический `_render_stats_panel`."""
    called: dict[str, bool] = {"eco": False, "panel": False}

    async def _fake_eco(bot, message):  # noqa: ANN001
        called["eco"] = True

    def _fake_panel(bot):  # noqa: ANN001
        called["panel"] = True
        return "Krab Stats\n─────────────\nstub"

    monkeypatch.setattr(
        "src.handlers.command_handlers._handle_stats_ecosystem", _fake_eco
    )
    monkeypatch.setattr(
        "src.handlers.command_handlers._render_stats_panel", _fake_panel
    )

    bot = _make_bot_stub(args_value="")
    msg = _FakeMessage()
    await handle_stats(bot, msg)  # type: ignore[arg-type]
    assert called["eco"] is False
    assert called["panel"] is True
    assert len(msg.replies) == 1
    assert "Krab Stats" in msg.replies[0]
