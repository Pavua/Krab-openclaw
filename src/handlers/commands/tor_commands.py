# -*- coding: utf-8 -*-
"""
tor_commands — обработчик команды !tor.

Субкоманды (owner-only):
  !tor status   — проверить доступность Tor, показать текущий exit-IP
  !tor ip       — показать текущий exit-IP (краткая форма)
  !tor newid    — запросить новую цепочку Tor (новый identity)
  !tor fetch <url> — анонимный GET через Tor

Требования:
  - Tor daemon запущен локально (brew services start tor)
  - TOR_ENABLED=1 в .env (опционально — команда работает и без него,
    но проверяет доступность SOCKS5 :9050 напрямую)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pyrogram.types import Message

from ...core.access_control import AccessLevel
from ...core.exceptions import UserInputError
from ...core.logger import get_logger

if TYPE_CHECKING:
    from ...userbot_bridge import KraabUserbot

logger = get_logger(__name__)

# Tor control port — стандартный (9051) и пароль (если настроен)
_TOR_CONTROL_PORT = 9051
_TOR_SOCKS_PORT = 9050


async def _newid() -> dict:
    """Отправляет NEWNYM сигнал Tor control port для смены цепочки.

    Returns {"ok": bool, "error": str}
    """
    import asyncio

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", _TOR_CONTROL_PORT),
            timeout=5.0,
        )
        # Аутентификация без пароля (COOKIE / NULL)
        writer.write(b"AUTHENTICATE\r\nSIGNAL NEWNYM\r\nQUIT\r\n")
        await writer.drain()
        response = await asyncio.wait_for(reader.read(256), timeout=5.0)
        writer.close()
        await writer.wait_closed()
        text = response.decode(errors="ignore")
        if "250" in text:
            return {"ok": True, "error": ""}
        return {"ok": False, "error": text.strip()}
    except (OSError, TimeoutError, ConnectionRefusedError) as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": repr(exc)}


async def handle_tor(bot: "KraabUserbot", message: Message) -> None:
    """Управление Tor-подключением.

    !tor status      — статус Tor (SOCKS alive, exit-IP)
    !tor ip          — только текущий exit-IP
    !tor newid       — сменить Tor-цепочку (SIGNAL NEWNYM)
    !tor fetch <url> — анонимный GET через Tor
    """
    # Owner-only
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 `!tor` доступен только владельцу.")

    from ...integrations.tor_bridge import get_tor_ip, is_tor_available, tor_fetch

    raw = bot._get_command_args(message).strip()
    parts = raw.split(None, 1)
    sub = parts[0].lower() if parts else "status"
    arg = parts[1].strip() if len(parts) > 1 else ""

    # ── !tor status ──────────────────────────────────────────────────────────
    if sub in ("status", ""):
        alive = await is_tor_available(_TOR_SOCKS_PORT)
        if not alive:
            await message.reply(
                "❌ **Tor** недоступен\n"
                f"`127.0.0.1:{_TOR_SOCKS_PORT}` SOCKS5 — нет подключения\n\n"
                "Запустить: `brew services start tor`"
            )
            return

        ip = await get_tor_ip(_TOR_SOCKS_PORT)
        ip_str = f"`{ip}`" if ip else "_не удалось получить_"
        await message.reply(
            f"🧅 **Tor** — активен\nSOCKS5: `127.0.0.1:{_TOR_SOCKS_PORT}` ✅\nExit IP: {ip_str}"
        )

    # ── !tor ip ───────────────────────────────────────────────────────────────
    elif sub == "ip":
        alive = await is_tor_available(_TOR_SOCKS_PORT)
        if not alive:
            await message.reply("❌ Tor не запущен.")
            return
        ip = await get_tor_ip(_TOR_SOCKS_PORT)
        if ip:
            await message.reply(f"🌐 Tor exit IP: `{ip}`")
        else:
            await message.reply("⚠️ Tor запущен, но exit IP не удалось получить.")

    # ── !tor newid ────────────────────────────────────────────────────────────
    elif sub == "newid":
        result = await _newid()
        if result["ok"]:
            # Получаем новый IP после смены цепочки
            import asyncio as _aio

            await _aio.sleep(1.0)  # короткая пауза для смены цепочки
            new_ip = await get_tor_ip(_TOR_SOCKS_PORT)
            ip_str = f"`{new_ip}`" if new_ip else "_пока не известен_"
            await message.reply(f"🔄 **Новая Tor-цепочка** получена\nНовый exit IP: {ip_str}")
        else:
            err = result["error"]
            await message.reply(
                f"⚠️ NEWNYM через control port :9051 не удался:\n`{err}`\n\n"
                "Убедитесь что `ControlPort 9051` включён в `/usr/local/etc/tor/torrc`\n"
                "и `CookieAuthentication 1` или `HashedControlPassword` настроен."
            )

    # ── !tor fetch <url> ──────────────────────────────────────────────────────
    elif sub == "fetch":
        if not arg:
            raise UserInputError(user_message="Укажи URL: `!tor fetch https://example.com`")

        url = arg
        # Базовая валидация URL
        if not url.startswith(("http://", "https://", "http://", ".onion")):
            if not url.startswith("http"):
                url = "https://" + url

        await message.reply(f"🔄 Fetching через Tor: `{url}`…")

        result = await tor_fetch(url, timeout=30.0)
        if not result.get("ok"):
            err = result.get("error", "unknown")
            if err == "tor_not_running":
                await message.reply("❌ Tor не запущен. `brew services start tor`")
            else:
                await message.reply(f"❌ Ошибка: `{err}`")
            return

        status = result.get("status", "?")
        final_url = result.get("url", url)
        text = result.get("text", "")
        # Обрезаем до 3000 символов для Telegram
        preview = text[:3000].strip()
        if len(text) > 3000:
            preview += f"\n…[обрезано, всего {len(text)} символов]"

        await message.reply(
            f"✅ **Tor fetch** — HTTP {status}\nURL: `{final_url}`\n───────────\n{preview}"
        )

    # ── неизвестная субкоманда ────────────────────────────────────────────────
    else:
        await message.reply(
            "**!tor** — Tor управление\n\n"
            "`!tor status`       — статус и exit IP\n"
            "`!tor ip`           — только exit IP\n"
            "`!tor newid`        — сменить цепочку\n"
            "`!tor fetch <url>`  — анонимный GET"
        )
