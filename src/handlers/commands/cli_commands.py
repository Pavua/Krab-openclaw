# -*- coding: utf-8 -*-
"""
cli_commands — Phase 2 Wave 12 extraction (Session 27).

CLI-утилиты и Hammerspoon bridge:
  !codex, !gemini (!gemini_cli), !claude (!claude_cli),
  !opencode, !hs (Hammerspoon window control).

Re-exported из command_handlers.py для обратной совместимости (тесты,
external imports `from src.handlers.command_handlers import handle_codex`).
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from pyrogram.types import Message

from ...config import config
from ...core.exceptions import UserInputError
from ...core.logger import get_logger
from ...integrations.hammerspoon_bridge import (
    HammerspoonBridgeError,
    hammerspoon,
)

# Baseline-алиасы для dual-namespace lookup (patch через command_handlers namespace)
_hammerspoon_baseline = hammerspoon
_hs_error_baseline = HammerspoonBridgeError

if TYPE_CHECKING:
    from ...userbot_bridge import KraabUserbot

logger = get_logger(__name__)


def _ch_attr(name: str, default: Any) -> Any:
    """Dual-namespace lookup: command_handlers namespace first (для monkeypatch),
    fallback к local baseline."""
    from .. import command_handlers as _ch  # noqa: PLC0415

    return getattr(_ch, name, default)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


async def _cli_keepalive(
    status_msg: Message,
    tool: str,
    started_at: float,
    *,
    interval: float = 20.0,
    stop_event: asyncio.Event,
) -> None:
    """Фоновая задача: обновляет сообщение с прогрессом пока CLI работает."""
    step = 0
    spinners = ["⏳", "⏳⏳", "⏳⏳⏳"]
    try:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(
                    asyncio.shield(stop_event.wait()),
                    timeout=interval,
                )
                break
            except asyncio.TimeoutError:
                pass
            elapsed = int(time.monotonic() - started_at)
            mins, secs = divmod(elapsed, 60)
            time_str = f"{mins}м {secs}с" if mins else f"{secs}с"
            indicator = spinners[step % len(spinners)]
            try:
                await status_msg.edit(f"{indicator} `{tool}` работает... {time_str}")
            except Exception:
                pass
            step += 1
    except asyncio.CancelledError:
        pass


async def _run_cli_with_progress(
    bot: "KraabUserbot",
    message: Message,
    tool: str,
    prompt: str,
    *,
    timeout: float = 120.0,
    tool_label: str | None = None,
) -> None:
    """Общая реализация для handle_codex/handle_gemini/handle_claude."""
    from ...integrations.cli_runner import run_cli

    if not prompt:
        raise UserInputError(
            user_message=(
                f"🤖 Использование: `!{tool} <запрос>`\n"
                f"Пример: `!{tool} напиши hello world на Python`"
            )
        )

    label = tool_label or tool
    status_msg = await message.reply(f"⏳ Запускаю `{label}`...")
    started_at = time.monotonic()
    stop_event = asyncio.Event()
    keepalive = asyncio.create_task(
        _cli_keepalive(status_msg, label, started_at, stop_event=stop_event)
    )
    try:
        result = await run_cli(tool, prompt, timeout=timeout)
    finally:
        stop_event.set()
        keepalive.cancel()
        try:
            await keepalive
        except asyncio.CancelledError:
            pass

    elapsed = int(time.monotonic() - started_at)
    output = result.output or "(нет вывода)"
    header = f"🤖 **{label}** (`{elapsed}с`)"
    if result.exit_code != 0 and not result.timed_out:
        header += f" — код {result.exit_code}"

    # Разбивка на чанки (lazy lookup через dual-namespace для testability)
    _split = _ch_attr("_split_text_for_telegram", None)
    if _split is None:
        from .. import command_handlers as _ch  # noqa: PLC0415

        _split = _ch._split_text_for_telegram  # type: ignore[attr-defined]

    full_text = f"{header}\n\n{output}"
    chunks = _split(full_text)
    await status_msg.edit(chunks[0])
    for part in chunks[1:]:
        await message.reply(part)


# ---------------------------------------------------------------------------
# !codex
# ---------------------------------------------------------------------------


async def handle_codex(bot: "KraabUserbot", message: Message) -> None:
    """Запустить codex-cli с запросом. Использование: !codex <запрос>"""
    prompt = bot._get_command_args(message)
    timeout = float(getattr(config, "CLI_CODEX_TIMEOUT_SEC", 120.0))
    await _run_cli_with_progress(bot, message, "codex", prompt, timeout=timeout)


# ---------------------------------------------------------------------------
# !gemini (!gemini_cli)
# ---------------------------------------------------------------------------


async def handle_gemini_cli(bot: "KraabUserbot", message: Message) -> None:
    """Запустить gemini-cli с запросом. Использование: !gemini <запрос>"""
    prompt = bot._get_command_args(message)
    timeout = float(getattr(config, "CLI_GEMINI_TIMEOUT_SEC", 120.0))
    await _run_cli_with_progress(
        bot, message, "gemini", prompt, timeout=timeout, tool_label="gemini-cli"
    )


# ---------------------------------------------------------------------------
# !claude (!claude_cli)
# ---------------------------------------------------------------------------


async def handle_claude_cli(bot: "KraabUserbot", message: Message) -> None:
    """Запустить claude code CLI с запросом. Использование: !claude_cli <запрос>"""
    prompt = bot._get_command_args(message)
    timeout = float(getattr(config, "CLI_CLAUDE_TIMEOUT_SEC", 120.0))
    await _run_cli_with_progress(
        bot, message, "claude", prompt, timeout=timeout, tool_label="claude-code"
    )


# ---------------------------------------------------------------------------
# !opencode
# ---------------------------------------------------------------------------


async def handle_opencode(bot: "KraabUserbot", message: Message) -> None:
    """Запустить opencode с запросом. Использование: !opencode <запрос>"""
    prompt = bot._get_command_args(message)
    timeout = float(getattr(config, "CLI_OPENCODE_TIMEOUT_SEC", 180.0))
    await _run_cli_with_progress(
        bot, message, "opencode", prompt, timeout=timeout, tool_label="opencode"
    )


# ---------------------------------------------------------------------------
# !hs — Hammerspoon window control
# ---------------------------------------------------------------------------


async def handle_hs(bot: "KraabUserbot", message: Message) -> None:
    """
    Управление окнами macOS через Hammerspoon.

    Требует:
    - Установленного Hammerspoon (https://www.hammerspoon.org/)
    - ~/.hammerspoon/init.lua из репозитория Краба (hammerspoon/init.lua)
    - Accessibility permission для Hammerspoon в System Settings

    Использование:
      !hs               — эта справка
      !hs status        — версия Hammerspoon, количество экранов
      !hs windows       — список видимых окон
      !hs focus <app>   — сфокусировать окно приложения
      !hs tile <preset> [<app>]  — раскладка: left|right|top|bottom|full
      !hs move <app> <x> <y> <w> <h>  — переместить/изменить размер окна
                        (координаты 0..1 = доля экрана, >2 = пиксели)
    """
    del bot
    raw = str(message.text or "").split(maxsplit=1)
    args_str = raw[1].strip() if len(raw) > 1 else ""

    _HELP = (  # noqa: N806
        "🔨 **Hammerspoon window control**\n\n"
        "`!hs status` — версия и статус HS\n"
        "`!hs windows` — список видимых окон\n"
        "`!hs focus <app>` — сфокусировать окно\n"
        "`!hs tile <preset> [<app>]` — раскладка: `left` `right` `top` `bottom` `full`\n"
        "`!hs move <app> <x> <y> <w> <h>` — переместить окно\n\n"
        "_Установи Hammerspoon и скопируй `hammerspoon/init.lua` в `~/.hammerspoon/init.lua`_"
    )

    if not args_str:
        await message.reply(_HELP)
        return

    # dual-namespace: тесты могут patch'ить hammerspoon через command_handlers
    hs = _ch_attr("hammerspoon", _hammerspoon_baseline)
    hs_error_cls = _ch_attr("HammerspoonBridgeError", _hs_error_baseline)

    if not hs.is_available():
        await message.reply(
            "🔨 Hammerspoon недоступен.\n\n"
            "Убедись, что:\n"
            "1. Hammerspoon установлен и запущен\n"
            "2. `~/.hammerspoon/init.lua` содержит krab-hs server\n"
            "3. Выданы разрешения Accessibility в System Settings → Privacy & Security"
        )
        return

    parts = args_str.split()
    sub = parts[0].lower()

    try:
        if sub == "status":
            data = await hs.status()
            lines = [
                "🔨 **Hammerspoon**",
                f"- Версия: `{data.get('version', '?')}`",
                f"- Build: `{data.get('build', '?')}`",
                f"- Экранов: `{data.get('screens', '?')}`",
            ]
            await message.reply("\n".join(lines))

        elif sub == "windows":
            windows = await hs.list_windows()
            if not windows:
                await message.reply("🔨 Нет видимых окон.")
                return
            lines = ["🔨 **Видимые окна**"]
            for w in windows[:20]:
                lines.append(f"- `{w.get('app', '?')}` — {w.get('title', '')}")
            await message.reply("\n".join(lines))

        elif sub == "focus":
            app = " ".join(parts[1:]) if len(parts) > 1 else ""
            if not app:
                raise UserInputError(user_message="🔨 Формат: `!hs focus <app>`")
            result = await hs.focus(app)
            await message.reply(
                f"🔨 Сфокусировано: `{result.get('app', app)}`"
                + (f" — {result.get('title', '')}" if result.get("title") else "")
            )

        elif sub == "tile":
            preset = parts[1].lower() if len(parts) > 1 else "left"
            app = " ".join(parts[2:]) if len(parts) > 2 else ""
            result = await hs.tile(preset=preset, app=app)
            await message.reply(
                f"🔨 Раскладка `{preset}` применена: `{result.get('app', app or 'активное окно')}`"
            )

        elif sub == "move":
            # !hs move <app> <x> <y> <w> <h>   или   !hs move <x> <y> <w> <h>
            floats: list[float] = []
            app_parts: list[str] = []
            for p in parts[1:]:
                try:
                    floats.append(float(p))
                except ValueError:
                    if not floats:
                        app_parts.append(p)
            if len(floats) < 4:
                raise UserInputError(
                    user_message="🔨 Формат: `!hs move [<app>] <x> <y> <w> <h>`\n"
                    "Пример: `!hs move Cursor 0 0 0.5 1` (левая половина экрана)"
                )
            x, y, w, h = floats[:4]
            app = " ".join(app_parts)
            result = await hs.move(app=app, x=x, y=y, w=w, h=h)
            frame = result.get("frame", {})
            await message.reply(
                f"🔨 Окно перемещено: x={frame.get('x')} y={frame.get('y')} "
                f"w={frame.get('w')} h={frame.get('h')}"
            )

        else:
            await message.reply(_HELP)

    except hs_error_cls as exc:
        await message.reply(f"🔨 Ошибка Hammerspoon: `{exc}`")
