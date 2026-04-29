# -*- coding: utf-8 -*-
"""
Тесты для !screenshot handler: auto-start Chrome + screencapture fallback.

Сценарии:
1. CDP доступен → скриншот через CDP
2. CDP недоступен → auto-start Chrome → CDP поднимается → скриншот через CDP
3. CDP недоступен, Chrome не запустился → fallback на screencapture → OK
4. CDP недоступен, Chrome не запустился, screencapture тоже падает → честная ошибка
5. CDP есть, screenshot() возвращает None → fallback на screencapture
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Кладём src/ в путь
sys.path.insert(0, str(Path(__file__).parents[2]))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_message() -> MagicMock:
    msg = MagicMock()
    msg.text = "!screenshot"
    msg.reply = AsyncMock()
    msg.reply_photo = AsyncMock()
    msg.reply_document = AsyncMock()
    return msg


def _make_probe(ok: bool, blocked: bool = False, error: str = "") -> dict:
    return {"ok": ok, "blocked": blocked, "error": error, "tab_count": 0}


def _png_bytes() -> bytes:
    """Минимальный валидный PNG (1x1 px, RGBA)."""
    import base64
    # 1×1 transparent PNG
    b64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhf"
        "DwAChwGA60e6kgAAAABJRU5ErkJggg=="
    )
    return base64.b64decode(b64)


# ---------------------------------------------------------------------------
# Test 1: CDP доступен → скриншот через CDP
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_screenshot_cdp_ok():
    """CDP OK → используем CDP screenshot, screencapture не вызывается."""
    msg = _make_message()
    png = _png_bytes()

    with (
        patch(
            "src.handlers.command_handlers.asyncio",
            wraps=asyncio,
        ),
        patch("src.handlers.command_handlers.os.unlink"),
        patch("builtins.open", MagicMock()),
    ):
        # Импортируем handler после установки патчей
        from src.handlers.command_handlers import handle_screenshot

        mock_bb = MagicMock()
        mock_bb.health_check = AsyncMock(return_value=_make_probe(ok=True))
        mock_bb.screenshot = AsyncMock(return_value=png)

        with (
            patch("src.handlers.command_handlers.asyncio.wait_for", new=AsyncMock(return_value=png)),
            patch(
                "src.integrations.browser_bridge.browser_bridge",
                mock_bb,
            ),
        ):
            # Патчим _bb внутри handle_screenshot через импорт модуля
            import src.integrations.browser_bridge as bb_mod
            orig = bb_mod.browser_bridge
            bb_mod.browser_bridge = mock_bb
            import src.handlers.command_handlers as hm

            # Сохраняем launch_dedicated_chrome чтобы убедиться что не вызывался
            with patch("src.integrations.dedicated_chrome.launch_dedicated_chrome") as mock_launch:
                # Патчим tempfile и os.unlink
                import io
                import tempfile
                tmp_file = MagicMock()
                tmp_file.__enter__ = MagicMock(return_value=tmp_file)
                tmp_file.__exit__ = MagicMock(return_value=False)
                tmp_file.name = "/tmp/test_screen.png"
                tmp_file.write = MagicMock()

                with (
                    patch("tempfile.NamedTemporaryFile", return_value=tmp_file),
                    patch("os.unlink"),
                    patch.object(mock_bb, "health_check", AsyncMock(return_value=_make_probe(ok=True))),
                    patch.object(mock_bb, "screenshot", AsyncMock(return_value=png)),
                    patch("asyncio.wait_for", new=AsyncMock(return_value=png)),
                ):
                    await hm.handle_screenshot(MagicMock(), msg)

            # CDP был доступен — launch не должен был вызываться
            mock_launch.assert_not_called()
            bb_mod.browser_bridge = orig


# ---------------------------------------------------------------------------
# Test 2: CDP недоступен → auto-start → CDP поднимается → CDP screenshot
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_screenshot_cdp_down_autostart_succeeds():
    """
    CDP недоступен → launch_dedicated_chrome() → OK → второй health_check OK
    → скриншот через CDP.
    """
    import src.handlers.command_handlers as hm
    import src.integrations.browser_bridge as bb_mod

    png = _png_bytes()
    msg = _make_message()

    mock_bb = MagicMock()
    probe_down = _make_probe(ok=False, blocked=True, error="CDP not reachable")
    probe_up = _make_probe(ok=True)
    # Первый health_check → down, второй → up (после auto-start)
    mock_bb.health_check = AsyncMock(side_effect=[probe_down, probe_up])
    mock_bb.screenshot = AsyncMock(return_value=png)

    orig = bb_mod.browser_bridge
    bb_mod.browser_bridge = mock_bb

    try:
        import tempfile
        tmp_file = MagicMock()
        tmp_file.__enter__ = MagicMock(return_value=tmp_file)
        tmp_file.__exit__ = MagicMock(return_value=False)
        tmp_file.name = "/tmp/test_screen2.png"
        tmp_file.write = MagicMock()

        with (
            patch(
                "src.handlers.command_handlers.asyncio.to_thread",
                new=AsyncMock(return_value=(True, "launched")),
            ),
            patch("asyncio.wait_for", new=AsyncMock(return_value=png)),
            patch("tempfile.NamedTemporaryFile", return_value=tmp_file),
            patch("os.unlink"),
        ):
            await hm.handle_screenshot(MagicMock(), msg)

        # reply_photo должен был быть вызван с CDP caption
        assert msg.reply_photo.called
        caption = msg.reply_photo.call_args[1].get("caption", "") or msg.reply_photo.call_args[0][1] if msg.reply_photo.call_args[0][1:] else ""
        # Проверяем что не screencapture fallback
        assert "screencapture" not in str(caption)
    finally:
        bb_mod.browser_bridge = orig


# ---------------------------------------------------------------------------
# Test 3: CDP недоступен, Chrome не запускается → screencapture fallback OK
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_screenshot_cdp_down_screencapture_fallback():
    """
    CDP недоступен, auto-start не помогает → screencapture берёт desktop screenshot.
    """
    import src.handlers.command_handlers as hm
    import src.integrations.browser_bridge as bb_mod

    png = _png_bytes()
    msg = _make_message()

    mock_bb = MagicMock()
    mock_bb.health_check = AsyncMock(return_value=_make_probe(ok=False, blocked=True))
    mock_bb.screenshot = AsyncMock(return_value=None)

    orig = bb_mod.browser_bridge
    bb_mod.browser_bridge = mock_bb

    try:
        # screencapture записывает реальные байты во временный файл
        import io
        import tempfile

        # Мокаем create_subprocess_exec → returncode 0
        mock_proc = AsyncMock()
        mock_proc.wait = AsyncMock(return_value=0)

        # Мокаем pathlib.Path для проверки sc_file
        mock_sc_path = MagicMock(spec=Path)
        mock_sc_path.exists.return_value = True
        mock_sc_path.stat.return_value = MagicMock(st_size=len(png))
        mock_sc_path.read_bytes.return_value = png
        mock_sc_path.unlink = MagicMock()

        tmp_file_sc = MagicMock()
        tmp_file_sc.__enter__ = MagicMock(return_value=tmp_file_sc)
        tmp_file_sc.__exit__ = MagicMock(return_value=False)
        tmp_file_sc.name = "/tmp/test_sc.png"

        tmp_file_out = MagicMock()
        tmp_file_out.__enter__ = MagicMock(return_value=tmp_file_out)
        tmp_file_out.__exit__ = MagicMock(return_value=False)
        tmp_file_out.name = "/tmp/test_out.png"
        tmp_file_out.write = MagicMock()

        ntf_calls = [tmp_file_sc, tmp_file_out]

        with (
            patch(
                "src.handlers.command_handlers.asyncio.to_thread",
                new=AsyncMock(return_value=(False, "chrome_binary_not_found")),
            ),
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)),
            patch("asyncio.wait_for", new=AsyncMock(side_effect=lambda coro, timeout: coro if asyncio.iscoroutine(coro) else asyncio.coroutine(lambda: 0)())),
            patch("pathlib.Path", return_value=mock_sc_path),
            patch("tempfile.NamedTemporaryFile", side_effect=ntf_calls),
            patch("os.unlink"),
        ):
            await hm.handle_screenshot(MagicMock(), msg)

        # Должен был вызваться reply_photo (screencapture fallback)
        assert msg.reply_photo.called or msg.reply_document.called
    finally:
        bb_mod.browser_bridge = orig


# ---------------------------------------------------------------------------
# Test 4: CDP down + screencapture тоже падает → честная ошибка
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_screenshot_all_fail_honest_error():
    """Когда и CDP и screencapture недоступны — reply с честным сообщением об ошибке."""
    import src.handlers.command_handlers as hm
    import src.integrations.browser_bridge as bb_mod

    msg = _make_message()

    mock_bb = MagicMock()
    mock_bb.health_check = AsyncMock(return_value=_make_probe(ok=False, blocked=True, error="CDP not reachable"))
    mock_bb.screenshot = AsyncMock(return_value=None)

    orig = bb_mod.browser_bridge
    bb_mod.browser_bridge = mock_bb

    try:
        with (
            patch(
                "src.handlers.command_handlers.asyncio.to_thread",
                new=AsyncMock(return_value=(False, "chrome_binary_not_found")),
            ),
            patch(
                "asyncio.create_subprocess_exec",
                AsyncMock(side_effect=OSError("screencapture not found")),
            ),
        ):
            await hm.handle_screenshot(MagicMock(), msg)

        # Должен быть reply с сообщением об ошибке, не reply_photo
        assert msg.reply.called
        assert not msg.reply_photo.called
        reply_text = msg.reply.call_args[0][0]
        assert "screenshot" in reply_text.lower() or "❌" in reply_text
        assert "start_dedicated_chrome" in reply_text
    finally:
        bb_mod.browser_bridge = orig


# ---------------------------------------------------------------------------
# Test 5: CDP есть, screenshot() → None → screencapture fallback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_screenshot_cdp_returns_none_fallback():
    """
    CDP говорит ok=True, но screenshot() возвращает None (нет вкладок).
    Должен уйти в screencapture fallback.
    """
    import src.handlers.command_handlers as hm
    import src.integrations.browser_bridge as bb_mod

    png = _png_bytes()
    msg = _make_message()

    mock_bb = MagicMock()
    mock_bb.health_check = AsyncMock(return_value=_make_probe(ok=True))
    mock_bb.screenshot = AsyncMock(return_value=None)  # CDP есть, но пустой ответ

    orig = bb_mod.browser_bridge
    bb_mod.browser_bridge = mock_bb

    try:
        mock_proc = AsyncMock()
        mock_proc.wait = AsyncMock(return_value=0)

        mock_sc_path = MagicMock(spec=Path)
        mock_sc_path.exists.return_value = True
        mock_sc_path.stat.return_value = MagicMock(st_size=len(png))
        mock_sc_path.read_bytes.return_value = png
        mock_sc_path.unlink = MagicMock()

        tmp_file_sc = MagicMock()
        tmp_file_sc.__enter__ = MagicMock(return_value=tmp_file_sc)
        tmp_file_sc.__exit__ = MagicMock(return_value=False)
        tmp_file_sc.name = "/tmp/test_sc5.png"

        tmp_file_out = MagicMock()
        tmp_file_out.__enter__ = MagicMock(return_value=tmp_file_out)
        tmp_file_out.__exit__ = MagicMock(return_value=False)
        tmp_file_out.name = "/tmp/test_out5.png"
        tmp_file_out.write = MagicMock()

        ntf_calls = [tmp_file_sc, tmp_file_out]

        with (
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)),
            patch("asyncio.wait_for", new=AsyncMock(return_value=None)),  # screenshot таймаутится → None
            patch("pathlib.Path", return_value=mock_sc_path),
            patch("tempfile.NamedTemporaryFile", side_effect=ntf_calls),
            patch("os.unlink"),
        ):
            await hm.handle_screenshot(MagicMock(), msg)

        # reply_photo или reply_document должен быть вызван
        assert msg.reply_photo.called or msg.reply_document.called
    finally:
        bb_mod.browser_bridge = orig
