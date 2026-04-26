# -*- coding: utf-8 -*-
"""
ai_commands - Phase 2 Wave 7 extraction (Session 27).

AI-команды на базе LLM (OpenClaw + web_search):
  !ask, !search, !agent, !rate, !explain, !fix, !rewrite,
  !summary, !catchup, !report.

Зависят от ``openclaw_client`` (streaming), ``search_brave`` (raw mode),
``cost_analytics`` / ``inbox_service`` / ``weekly_digest`` (для !report).

Re-exported from command_handlers.py для обратной совместимости (тесты,
_AgentRoomRouterAdapter, любые external imports).

См. ``docs/CODE_SPLITS_PLAN.md`` Phase 2 - domain extractions.
"""

from __future__ import annotations

import datetime
import re
from typing import TYPE_CHECKING

import httpx
from pyrogram.types import Message

from ...config import config
from ...core.access_control import AccessLevel
from ...core.cost_analytics import cost_analytics
from ...core.exceptions import UserInputError
from ...core.inbox_service import inbox_service
from ...core.logger import get_logger
from ...core.swarm import AgentRoom
from ...core.weekly_digest import weekly_digest
from ...employee_templates import get_role_prompt, list_roles, save_role
from ...openclaw_client import openclaw_client
from ...search_engine import search_brave

if TYPE_CHECKING:
    from ...userbot_bridge import KraabUserbot

logger = get_logger(__name__)


def _split_text(text: str) -> list[str]:
    """Lazy proxy к ``command_handlers._split_text_for_telegram``.

    Помогает избежать циркулярного импорта на старте: команды AI грузятся
    раньше, чем command_handlers полностью инициализирован.
    """
    from ..command_handlers import _split_text_for_telegram

    return _split_text_for_telegram(text)


# ---------------------------------------------------------------------------
# !summary / !catchup constants & helpers
# ---------------------------------------------------------------------------

_SUMMARY_DEFAULT_N = 50
_SUMMARY_MAX_N = 500
# Максимум символов истории, передаваемых в LLM
_SUMMARY_MAX_HISTORY_CHARS = 24_000
# Порог редактирования streaming-сообщения (каждые N новых символов)
_SUMMARY_EDIT_THRESHOLD = 200


def _format_chat_history_for_llm(messages: list) -> str:
    """
    Форматирует список Pyrogram Message в читаемый текст для LLM.

    Формат строки: [HH:MM] Имя: текст
    Медиа без подписи помечается как [тип медиа].
    История приходит из get_chat_history новые-первые, разворачиваем в хронологию.
    """
    lines: list[str] = []
    for msg in reversed(messages):
        # Определяем имя отправителя
        sender = "Unknown"
        if getattr(msg, "from_user", None):
            u = msg.from_user
            name_parts = [
                p for p in [getattr(u, "first_name", None), getattr(u, "last_name", None)] if p
            ]
            if name_parts:
                sender = " ".join(name_parts)
            elif getattr(u, "username", None):
                sender = f"@{u.username}"
            else:
                sender = str(u.id)
        elif getattr(msg, "sender_chat", None):
            sender = getattr(msg.sender_chat, "title", None) or str(msg.sender_chat.id)

        # Текст сообщения
        text: str = getattr(msg, "text", None) or getattr(msg, "caption", None) or ""
        if not text:
            if getattr(msg, "photo", None):
                text = "[фото]"
            elif getattr(msg, "video", None):
                text = "[видео]"
            elif getattr(msg, "voice", None) or getattr(msg, "audio", None):
                text = "[голосовое/аудио]"
            elif getattr(msg, "document", None):
                text = "[документ]"
            elif getattr(msg, "sticker", None):
                text = "[стикер]"
            else:
                text = "[медиа]"

        # Время
        ts = ""
        date = getattr(msg, "date", None)
        if date:
            ts = date.strftime("%H:%M")

        lines.append(f"[{ts}] {sender}: {text}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# !rewrite режимы
# ---------------------------------------------------------------------------

_REWRITE_MODES: dict[str, tuple[str, str]] = {
    "formal": (
        "formal",
        "Перепиши текст в официальном / формальном стиле. "
        "Сохраняй смысл, избегай сленга и разговорных выражений.",
    ),
    "casual": (
        "casual",
        "Перепиши текст в разговорном / неформальном стиле. "
        "Можно использовать живой язык, сокращения, избегать бюрократических оборотов.",
    ),
    "short": (
        "short",
        "Сократи текст: убери воду, оставь только суть. Итог должен быть заметно короче оригинала.",
    ),
    # режим по умолчанию — ключ пустая строка
    "": (
        "default",
        "Улучши текст: сделай его чётче, читабельнее и грамотнее. "
        "Исправь стиль и формулировки, сохрани смысл и язык оригинала.",
    ),
}


# ---------------------------------------------------------------------------
# !rate constants & helpers
# ---------------------------------------------------------------------------

_RATE_CRYPTO_ALIASES: dict[str, str] = {
    "btc": "Bitcoin (BTC)",
    "eth": "Ethereum (ETH)",
    "sol": "Solana (SOL)",
    "bnb": "BNB (Binance Coin)",
    "xrp": "XRP (Ripple)",
    "ada": "Cardano (ADA)",
    "doge": "Dogecoin (DOGE)",
    "ton": "Toncoin (TON)",
    "usdt": "Tether (USDT)",
    "usdc": "USD Coin (USDC)",
    "avax": "Avalanche (AVAX)",
    "link": "Chainlink (LINK)",
    "dot": "Polkadot (DOT)",
    "ltc": "Litecoin (LTC)",
    "shib": "Shiba Inu (SHIB)",
}

_RATE_MAX_ASSETS = 5


def _rate_asset_label(ticker: str) -> str:
    """Возвращает читаемое название актива по тикеру (крипто) или тикер в верхнем регистре (акции)."""
    return _RATE_CRYPTO_ALIASES.get(ticker.lower(), ticker.upper())


def _build_rate_prompt(assets: list[str]) -> str:
    """Формирует промпт для AI-запроса текущего курса активов."""
    labels = [_rate_asset_label(a) for a in assets]
    if len(labels) == 1:
        asset_str = labels[0]
        return (
            f"Найди текущую цену {asset_str}. "
            "Покажи: цену в USD, изменение за 24ч, капитализацию. "
            "Используй актуальные данные из веб-поиска. "
            "Ответ дай кратко, без лишних вступлений."
        )
    else:
        asset_str = ", ".join(labels)
        return (
            f"Найди текущие цены следующих активов: {asset_str}. "
            "Для каждого актива покажи: цену в USD, изменение за 24ч, капитализацию. "
            "В конце добавь краткое сравнение. "
            "Используй актуальные данные из веб-поиска. "
            "Ответ дай кратко, без лишних вступлений."
        )


# ---------------------------------------------------------------------------
# !explain prompt
# ---------------------------------------------------------------------------

_EXPLAIN_PROMPT = "Объясни этот код простым языком. Что он делает, зачем, как работает."


# ---------------------------------------------------------------------------
# !ask helpers
# ---------------------------------------------------------------------------


def _parse_ask_memory_flags(question: str) -> tuple[str, "bool | None"]:
    """
    Извлекает `--with-memory` / `--no-memory` флаги из вопроса.

    Returns:
        (cleaned_question, force_enable) — force_enable: True / False / None.
    """
    tokens = (question or "").split()
    force_enable: "bool | None" = None
    remaining: list[str] = []
    for tok in tokens:
        low = tok.lower()
        if low in ("--with-memory", "--memory", "--with-mem"):
            force_enable = True
            continue
        if low in ("--no-memory", "--no-mem"):
            force_enable = False
            continue
        remaining.append(tok)
    return " ".join(remaining).strip(), force_enable


# ---------------------------------------------------------------------------
# !report helpers
# ---------------------------------------------------------------------------


def _collect_daily_report_data() -> dict:
    """Собирает данные для дневного отчёта из доступных источников."""
    import time as _time

    from ...core.swarm_artifact_store import swarm_artifact_store

    data: dict = {}

    # --- Расходы за сегодня ---
    try:
        today_start = _time.mktime(datetime.date.today().timetuple())
        today_calls = [r for r in cost_analytics._calls if r.timestamp >= today_start]
        data["cost_today_usd"] = round(sum(r.cost_usd for r in today_calls), 4)
        data["cost_month_usd"] = round(cost_analytics.get_monthly_cost_usd(), 4)
        data["calls_today"] = len(today_calls)
        data["tokens_today"] = sum(r.input_tokens + r.output_tokens for r in today_calls)
    except Exception:  # noqa: BLE001
        data["cost_today_usd"] = 0.0
        data["cost_month_usd"] = 0.0
        data["calls_today"] = 0
        data["tokens_today"] = 0

    # --- Swarm rounds за сегодня ---
    try:
        all_arts = swarm_artifact_store.list_artifacts(limit=500)
        today_str = datetime.date.today().isoformat()
        today_arts = [a for a in all_arts if str(a.get("timestamp_iso", "")).startswith(today_str)]
        data["swarm_rounds_today"] = len(today_arts)
        data["swarm_teams_today"] = sorted({a.get("team", "?") for a in today_arts})
        data["swarm_duration_today"] = sum(a.get("duration_sec", 0) for a in today_arts)
    except Exception:  # noqa: BLE001
        data["swarm_rounds_today"] = 0
        data["swarm_teams_today"] = []
        data["swarm_duration_today"] = 0

    # --- Errors/warnings из inbox ---
    try:
        summary = inbox_service.get_summary()
        data["inbox_open"] = summary.get("open", 0)
        data["inbox_errors"] = summary.get("error", 0)
        data["inbox_warnings"] = summary.get("warning", 0)
    except Exception:  # noqa: BLE001
        data["inbox_open"] = 0
        data["inbox_errors"] = 0
        data["inbox_warnings"] = 0

    return data


def _render_daily_report(data: dict) -> str:
    """Форматирует дневной отчёт в markdown."""
    today = datetime.date.today().isoformat()
    lines = [
        f"📊 **Daily Report — {today}**",
        "",
        "**💰 Расходы**",
        f"  • Сегодня: ${data['cost_today_usd']:.4f} ({data['calls_today']} вызовов, {data['tokens_today']:,} токенов)",
        f"  • Месяц: ${data['cost_month_usd']:.4f}",
        "",
        "**🐝 Swarm**",
        f"  • Раундов сегодня: {data['swarm_rounds_today']}",
    ]
    if data["swarm_teams_today"]:
        lines.append(f"  • Команды: {', '.join(data['swarm_teams_today'])}")
    if data["swarm_duration_today"]:
        lines.append(f"  • Суммарное время: {data['swarm_duration_today']:.0f}с")
    lines += [
        "",
        "**⚠️ Inbox**",
        f"  • Открытых: {data['inbox_open']} (🔴 ошибок: {data['inbox_errors']}, 🟡 warnings: {data['inbox_warnings']})",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def handle_search(bot: "KraabUserbot", message: Message) -> None:
    """
    Веб-поиск с AI-суммаризацией через OpenClaw + web_search tool.

    Форматы:
      !search <запрос>         — AI-режим: краткий ответ + источники (по умолчанию)
      !search --raw <запрос>   — сырые результаты Brave без AI
      !search --brave <запрос> — то же, что --raw

    Длинные ответы автоматически разбиваются на части (пагинация).
    """
    raw_args = bot._get_command_args(message).strip()

    # Проверяем пустой запрос
    if not raw_args or raw_args.lower() in ["search", "!search"]:
        raise UserInputError(
            user_message=(
                "🔍 Что ищем?\n"
                "`!search <запрос>` — поиск с AI-суммаризацией\n"
                "`!search --raw <запрос>` — сырые результаты Brave"
            )
        )

    # Определяем режим: --raw/--brave → без AI
    raw_mode = False
    query = raw_args
    for flag in ("--raw", "--brave"):
        if raw_args.lower().startswith(flag):
            raw_mode = True
            query = raw_args[len(flag) :].strip()
            break

    if not query:
        raise UserInputError(user_message="🔍 Укажи запрос после флага.")

    # Изолированная сессия, чтобы не загрязнять основной контекст чата
    _chat_for_session = getattr(message, "chat", None)
    _chat_id_for_session = getattr(_chat_for_session, "id", 0) if _chat_for_session else 0
    session_id = f"search_{_chat_id_for_session}"

    if raw_mode:
        # --- Режим raw: прямой Brave-поиск без AI ---
        msg = await message.reply(f"🔍 **Ищу (raw):** `{query}`...")
        try:
            results = await search_brave(query)
            if not results:
                await msg.edit("❌ Ничего не найдено.")
                return
            # Пагинация длинных результатов
            header = f"🔍 **Результаты поиска:** `{query}`\n\n"
            parts = _split_text(header + results)
            await msg.edit(parts[0])
            for part in parts[1:]:
                await message.reply(part)
        except (httpx.HTTPError, OSError, ValueError, KeyError) as e:
            logger.error("handle_search_raw_error", query=query, error=str(e))
            await msg.edit(f"❌ Ошибка поиска: {e}")
        return

    # --- Режим AI: OpenClaw + web_search tool ---
    msg = await message.reply(f"🔍 **Краб ищет в сети:** `{query}`...")

    prompt = (
        f"Найди в интернете информацию по запросу: {query}. "
        "Дай краткий структурированный ответ с ключевыми фактами и источниками (URL)."
    )

    try:
        chunks: list[str] = []
        async for chunk in openclaw_client.send_message_stream(
            message=prompt,
            chat_id=session_id,
            disable_tools=False,  # обязательно использует web_search
        ):
            chunks.append(str(chunk))

        result = "".join(chunks).strip()

        if not result:
            await msg.edit("❌ Не удалось получить результаты поиска.")
            return

        # Заголовок + результат
        header = f"🔍 **{query}**\n\n"
        full_text = header + result

        # Пагинация: Telegram ограничивает ~4096 символов
        parts = _split_text(full_text)
        total = len(parts)

        # Редактируем первое сообщение
        first = parts[0]
        if total > 1:
            first += f"\n\n_(часть 1/{total})_"
        await msg.edit(first)

        # Остальные части отправляем как новые сообщения
        for i, part in enumerate(parts[1:], start=2):
            suffix = f"\n\n_(часть {i}/{total})_" if total > 2 else ""
            await message.reply(part + suffix)

    except Exception as exc:  # noqa: BLE001
        logger.error("handle_search_ai_error", query=query, error=str(exc))
        await msg.edit(f"❌ Ошибка поиска: {exc}")


async def handle_agent(bot: "KraabUserbot", message: Message) -> None:
    """Управление агентами: !agent new <name> <prompt>."""
    # Lazy import: _AgentRoomRouterAdapter определён в command_handlers и
    # используется handle_swarm — переносить отдельно нет смысла.
    from ..command_handlers import _AgentRoomRouterAdapter

    text = bot._get_command_args(message)
    if not text:
        raise UserInputError(
            user_message=(
                "🕵️‍♂️ Использование:\n"
                "- `!agent new <имя> <промпт>`\n"
                "- `!agent list`\n"
                "- `!agent swarm <тема>`\n"
                "- `!agent swarm loop [N] <тема>`"
            )
        )
    if text.startswith("list"):
        await message.reply(f"🕵️‍♂️ **Доступные агенты:**\n\n{list_roles()}")
        return
    if text.startswith("swarm"):
        swarm_args = text[5:].strip()
        if not swarm_args:
            raise UserInputError(user_message="🐝 Формат: `!agent swarm <тема>`")

        topic = swarm_args
        is_loop = False
        loop_rounds = 2
        if swarm_args.startswith("loop"):
            is_loop = True
            loop_payload = swarm_args[4:].strip()
            if not loop_payload:
                raise UserInputError(user_message="🐝 Формат: `!agent swarm loop [N] <тема>`")
            first, *rest = loop_payload.split(" ", 1)
            if first.isdigit():
                loop_rounds = int(first)
                topic = rest[0].strip() if rest else ""
            else:
                topic = loop_payload
            if not topic:
                raise UserInputError(user_message="🐝 Формат: `!agent swarm loop [N] <тема>`")

        max_rounds = int(getattr(config, "SWARM_LOOP_MAX_ROUNDS", 3) or 3)
        next_round_clip = int(getattr(config, "SWARM_LOOP_NEXT_ROUND_CLIP", 4000) or 4000)
        safe_rounds = max(1, min(loop_rounds, max_rounds))

        if is_loop:
            status = await message.reply(
                f"🐝 Запускаю роевой loop: {safe_rounds} раунд(а), роли аналитик → критик → интегратор..."
            )
        else:
            status = await message.reply(
                "🐝 Запускаю роевой раунд: аналитик → критик → интегратор..."
            )
        room = AgentRoom()
        role_prompt = get_role_prompt(getattr(bot, "current_role", "default"))
        room_chat_id = f"swarm:{message.chat.id}"
        router = _AgentRoomRouterAdapter(
            chat_id=room_chat_id,
            system_prompt=role_prompt,
        )
        if is_loop:
            result = await room.run_loop(
                topic,
                router,
                rounds=safe_rounds,
                max_rounds=max_rounds,
                next_round_clip=next_round_clip,
            )
        else:
            result = await room.run_round(topic, router)
        chunks = _split_text(result)
        await status.edit(chunks[0])
        for part in chunks[1:]:
            await message.reply(part)
        return
    if text.startswith("new"):
        parts = text[3:].strip().split(" ", 1)
        if len(parts) < 2:
            raise UserInputError(user_message="❌ Ошибка: укажите имя и промпт.")
        name = parts[0].strip()
        prompt = parts[1].strip().strip('"').strip("'")
        if save_role(name, prompt):
            await message.reply(
                f"🕵️‍♂️ **Агент создан:** `{name}`\n\nТеперь можно использовать: `стань {name}`"
            )
        else:
            await message.reply("❌ Ошибка при сохранении агента.")


async def handle_summary(bot: "KraabUserbot", message: Message) -> None:
    """
    Суммаризирует историю чата через LLM.

    Синтаксис:
      !summary [N]               — последние N сообщений текущего чата (default 50)
      !summary <chat_id> [N]     — другой чат (userbot видит всё)
      !catchup                   — алиас для !summary 100

    Примеры:
      !summary
      !summary 100
      !summary -1001234567890 200
    """
    raw_args = bot._get_command_args(message).strip()
    parts = raw_args.split() if raw_args else []

    # Парсим аргументы: опциональный chat_id и N
    target_chat_id: int = message.chat.id
    n: int = _SUMMARY_DEFAULT_N

    if parts:
        first = parts[0]
        # chat_id: начинается с '-100' или длинное число (>6 цифр)
        is_chat_id = first.startswith("-100") or (
            first.lstrip("-").isdigit() and len(first.lstrip("-")) > 6
        )
        if is_chat_id:
            try:
                target_chat_id = int(first)
            except ValueError:
                raise UserInputError(user_message=f"❌ Некорректный chat_id: `{first}`")
            if len(parts) >= 2 and parts[1].isdigit():
                n = max(1, min(int(parts[1]), _SUMMARY_MAX_N))
        elif first.isdigit():
            # Только число — N для текущего чата
            n = max(1, min(int(first), _SUMMARY_MAX_N))
        else:
            raise UserInputError(
                user_message=(
                    "📋 **Суммаризация чата**\n\n"
                    "`!summary [N]` — последние N сообщений текущего чата\n"
                    "`!summary <chat_id> [N]` — другой чат\n"
                    "`!catchup` — алиас для !summary 100"
                )
            )

    # Отправляем плейсхолдер
    status_msg = await message.reply(f"📋 Собираю последние {n} сообщений...")

    # Читаем историю через pyrogram
    try:
        raw_messages = [m async for m in bot.client.get_chat_history(target_chat_id, limit=n)]
    except Exception as exc:
        logger.warning("handle_summary_fetch_failed", chat_id=target_chat_id, error=str(exc))
        await status_msg.edit(f"❌ Не удалось получить историю чата: {exc}")
        return

    if not raw_messages:
        await status_msg.edit("📭 История чата пуста или недоступна.")
        return

    # Форматируем историю
    history_text = _format_chat_history_for_llm(raw_messages)

    # Обрезаем если слишком длинно (эвристика по символам)
    if len(history_text) > _SUMMARY_MAX_HISTORY_CHARS:
        history_text = "[...]\n" + history_text[-_SUMMARY_MAX_HISTORY_CHARS:]

    actual_n = len(raw_messages)
    chat_label = str(target_chat_id) if target_chat_id != message.chat.id else "текущего чата"

    prompt = (
        f"Суммаризируй этот чат (последние {actual_n} сообщений из {chat_label}).\n"
        "Выдели ключевые темы, решения и важные факты. "
        "Будь кратким и структурированным. Отвечай на языке чата.\n\n"
        f"История:\n{history_text}"
    )

    # Обновляем плейсхолдер перед стримингом
    header = f"📋 **Сводка чата** (последние {actual_n} сообщений)\n─────────────\n"
    await status_msg.edit(header + "⏳ Генерирую...")

    chunks: list[str] = []
    last_edit_len = 0

    try:
        async for chunk in openclaw_client.send_message_stream(
            message=prompt,
            # Изолированная сессия — не портит основной контекст чата
            chat_id=f"summary_{message.chat.id}",
            disable_tools=True,
        ):
            chunks.append(str(chunk))
            total = "".join(chunks)
            if len(total) - last_edit_len >= _SUMMARY_EDIT_THRESHOLD:
                last_edit_len = len(total)
                preview = total
                max_preview = 4000 - len(header)
                if len(preview) > max_preview:
                    preview = preview[-max_preview:]
                try:
                    await status_msg.edit(header + preview)
                except Exception:  # noqa: BLE001
                    pass  # промежуточные ошибки редактирования игнорируем

    except Exception as exc:
        logger.warning("handle_summary_llm_failed", error=str(exc))
        await status_msg.edit(f"❌ Ошибка суммаризации: {exc}")
        return

    # Финальное редактирование с полным текстом
    final_text = "".join(chunks).strip()
    result = header + final_text
    if len(result) > 4096:
        result = result[:4090] + "..."
    try:
        await status_msg.edit(result)
    except Exception:  # noqa: BLE001
        # Если редактирование не удалось — пишем новым сообщением
        await message.reply(result)


async def handle_catchup(bot: "KraabUserbot", message: Message) -> None:
    """!catchup — алиас для !summary 100 (быстро догнать пропущенное)."""
    # Подменяем _get_command_args чтобы handle_summary получил "100"
    original_get_args = bot._get_command_args

    def _patched_args(_msg: Message) -> str:  # noqa: ARG001
        return "100"

    bot._get_command_args = _patched_args  # type: ignore[method-assign]
    try:
        await handle_summary(bot, message)
    finally:
        bot._get_command_args = original_get_args  # type: ignore[method-assign]


async def handle_ask(bot: "KraabUserbot", message: Message) -> None:
    """
    !ask [вопрос] — задаёт вопрос AI о конкретном сообщении (reply).

    Использование:
      !ask кратко                     — суммаризировать сообщение
      !ask переведи                   — перевести
      !ask                            — объяснить сообщение (вопрос по умолчанию)
      !ask --with-memory <вопрос>     — augment context из memory recall
      !ask --no-memory <вопрос>       — чистый LLM без recall (override env)
    """
    raw_question = bot._get_command_args(message).strip()
    question, force_memory = _parse_ask_memory_flags(raw_question)

    # Получаем исходное сообщение — только из reply
    replied = message.reply_to_message
    if replied is None:
        raise UserInputError(
            user_message=(
                "💬 Ответь на сообщение командой `!ask [вопрос]`.\n"
                "Пример: `!ask кратко` (в reply на длинный текст)"
            )
        )

    # Извлекаем текст из reply-сообщения
    source_text = (replied.text or replied.caption or "").strip()
    if not source_text:
        raise UserInputError(user_message="❌ Исходное сообщение не содержит текста.")

    # Вопрос по умолчанию если не указан
    if not question:
        question = "Объясни это сообщение"

    # Изолированная сессия, чтобы не загрязнять основной контекст чата
    session_id = f"ask_{message.chat.id}"

    # Системный промпт: роль аналитика без лишнего контекста
    system_prompt = (
        "Ты — Краб, персональный AI-ассистент. "
        "Пользователь прислал фрагмент текста и задал вопрос о нём. "
        "Отвечай кратко и по делу. "
        "Используй язык вопроса (если вопрос на русском — отвечай по-русски)."
    )

    # Формируем промпт: текст + вопрос
    prompt = f'Текст:\n"""\n{source_text}\n"""\n\nВопрос: {question}'

    # Semantic recall auto-context: prepend top-k memory chunks если включено
    from ...core.memory_context_augmenter import augment_query_with_memory

    augmented = await augment_query_with_memory(
        question,
        force_enable=force_memory,
    )
    if augmented.enabled and augmented.chunks_used:
        # Добавляем recall-префикс перед исходным prompt
        prompt = f'{augmented.augmented_prompt}\n\nТекст:\n"""\n{source_text}\n"""'

    # Отправляем статус и запускаем стриминг
    msg = await message.reply("🤔 Думаю...")

    try:
        chunks: list[str] = []
        async for chunk in openclaw_client.send_message_stream(
            message=prompt,
            chat_id=session_id,
            system_prompt=system_prompt,
            disable_tools=True,  # !ask не нужны tool_calls — только ответ
        ):
            chunks.append(str(chunk))

        result = "".join(chunks).strip()
        if not result:
            await msg.edit("❌ AI вернул пустой ответ.")
            return

        # Разбиваем длинный ответ на куски для Telegram
        parts = _split_text(result)
        await msg.edit(parts[0])
        for part in parts[1:]:
            await message.reply(part)

    except Exception as exc:  # noqa: BLE001
        logger.error("handle_ask_error", error=str(exc))
        await msg.edit(f"❌ Ошибка: {exc}")


async def handle_fix(bot: "KraabUserbot", message: Message) -> None:
    """
    !fix [текст] — исправляет грамматику, орфографию и пунктуацию через AI.

    Использование:
      !fix Привет как дела   — исправить текст из аргументов команды
      !fix                   — исправить текст из reply-сообщения
    """
    args_text = bot._get_command_args(message).strip()

    # Если нет аргументов — берём текст из reply
    if not args_text:
        replied = message.reply_to_message
        if replied is None:
            raise UserInputError(
                user_message=(
                    "✏️ Укажи текст после команды или ответь на сообщение:\n"
                    "`!fix Привет как дела` — исправит текст\n"
                    "`!fix` (в reply) — исправит текст ответного сообщения"
                )
            )
        source_text = (replied.text or replied.caption or "").strip()
        if not source_text:
            raise UserInputError(user_message="❌ Исходное сообщение не содержит текста.")
    else:
        source_text = args_text

    # Изолированная сессия — не загрязняем основной контекст чата
    session_id = f"fix_{message.chat.id}"

    # Промпт: только исправленный текст без объяснений
    prompt = (
        "Исправь грамматику, орфографию и пунктуацию. "
        "Верни ТОЛЬКО исправленный текст.\n\n"
        f"{source_text}"
    )

    # Статусное сообщение пока AI обрабатывает
    msg = await message.reply("✏️ Исправляю...")

    try:
        chunks: list[str] = []
        async for chunk in openclaw_client.send_message_stream(
            message=prompt,
            chat_id=session_id,
            disable_tools=True,  # только текстовый ответ, без tool_calls
            max_output_tokens=512,  # короткий вывод — только исправленный текст
        ):
            chunks.append(str(chunk))

        result = "".join(chunks).strip()
        if not result:
            await msg.edit("❌ AI вернул пустой ответ.")
            return

        # Разбиваем длинный ответ на куски для Telegram (редко нужно, но на всякий случай)
        parts = _split_text(result)
        await msg.edit(parts[0])
        for part in parts[1:]:
            await message.reply(part)

    except Exception as exc:  # noqa: BLE001
        logger.error("handle_fix_error", error=str(exc))
        await msg.edit(f"❌ Ошибка: {exc}")


async def handle_rewrite(bot: "KraabUserbot", message: Message) -> None:
    """
    !rewrite [режим] [текст] — переписывает текст через LLM.

    Режимы:
      !rewrite <текст>          — улучшить / переписать
      !rewrite formal <текст>   — формальный стиль
      !rewrite casual <текст>   — разговорный стиль
      !rewrite short <текст>    — сократить

    Также работает в reply — если текст не указан, берётся из ответного сообщения.
    """
    args = bot._get_command_args(message).strip()

    # Определяем режим — первое слово, если оно совпадает с известным
    mode_key = ""
    text_to_rewrite = ""

    if args:
        first_word = args.split()[0].lower()
        if first_word in _REWRITE_MODES:
            mode_key = first_word
            text_to_rewrite = args[len(first_word) :].strip()
        else:
            text_to_rewrite = args

    # Если текст не передан аргументом — пробуем reply
    if not text_to_rewrite:
        replied = message.reply_to_message
        if replied is None:
            raise UserInputError(
                user_message=(
                    "✏️ Использование:\n"
                    "- `!rewrite <текст>` — улучшить текст\n"
                    "- `!rewrite formal <текст>` — формальный стиль\n"
                    "- `!rewrite casual <текст>` — разговорный стиль\n"
                    "- `!rewrite short <текст>` — сократить\n\n"
                    "Или ответь на сообщение командой `!rewrite [режим]`."
                )
            )
        text_to_rewrite = (replied.text or replied.caption or "").strip()
        if not text_to_rewrite:
            raise UserInputError(user_message="❌ Исходное сообщение не содержит текста.")

    _mode_label, mode_instruction = _REWRITE_MODES[mode_key]

    # Системный промпт
    system_prompt = (
        "Ты — Краб, персональный AI-ассистент. "
        "Твоя задача — редактировать тексты по инструкции пользователя. "
        "Возвращай ТОЛЬКО переписанный текст без пояснений, заголовков и лишних слов. "
        "Сохраняй язык оригинала (если текст на русском — отвечай по-русски, "
        "если на английском — по-английски)."
    )

    # Промпт = инструкция + текст
    prompt = f'{mode_instruction}\n\nТекст:\n"""\n{text_to_rewrite}\n"""'

    # Изолированная сессия, чтобы не загрязнять основной контекст чата
    session_id = f"rewrite_{message.chat.id}"

    msg = await message.reply("✏️ Переписываю...")

    try:
        chunks: list[str] = []
        async for chunk in openclaw_client.send_message_stream(
            message=prompt,
            chat_id=session_id,
            system_prompt=system_prompt,
            disable_tools=True,  # только текстовый ответ, tool_calls не нужны
        ):
            chunks.append(str(chunk))

        result = "".join(chunks).strip()
        if not result:
            await msg.edit("❌ AI вернул пустой ответ.")
            return

        # Разбиваем длинный ответ на куски для Telegram
        parts = _split_text(result)
        await msg.edit(parts[0])
        for part in parts[1:]:
            await message.reply(part)

    except Exception as exc:  # noqa: BLE001
        logger.error("handle_rewrite_error", error=str(exc))
        await msg.edit(f"❌ Ошибка: {exc}")


async def handle_report(bot: "KraabUserbot", message: Message) -> None:
    """
    Структурированный отчёт через LLM.

    Синтаксис:
      !report daily   — дневной отчёт (cost, swarm rounds, ошибки)
      !report weekly  — недельный отчёт через WeeklyDigest
      !report <тема>  — кастомный отчёт через LLM по заданной теме

    Owner-only команда.
    """
    # Проверка прав
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 Команда доступна только владельцу.")

    args = bot._get_command_args(message).strip()

    if not args or args.lower() in {"help", "помощь"}:
        raise UserInputError(
            user_message=(
                "📊 **!report — генерация отчётов**\n\n"
                "`!report daily` — дневной отчёт (cost, swarm, ошибки)\n"
                "`!report weekly` — недельный отчёт через WeeklyDigest\n"
                "`!report <тема>` — кастомный отчёт через LLM по любой теме"
            )
        )

    # --- daily ---
    if args.lower() in {"daily", "день", "дневной"}:
        status_msg = await message.reply("⏳ Собираю данные за сегодня...")
        try:
            data = _collect_daily_report_data()
            report_text = _render_daily_report(data)
        except Exception as exc:  # noqa: BLE001
            logger.warning("handle_report_daily_failed", error=str(exc))
            await status_msg.edit(f"❌ Ошибка сбора данных: {exc}")
            return
        await status_msg.edit(report_text)
        return

    # --- weekly ---
    if args.lower() in {"weekly", "неделя", "недельный"}:
        status_msg = await message.reply("⏳ Генерирую недельный отчёт...")
        try:
            result = await weekly_digest.generate_digest()
        except Exception as exc:  # noqa: BLE001
            logger.warning("handle_report_weekly_failed", error=str(exc))
            await status_msg.edit(f"❌ Ошибка генерации недельного отчёта: {exc}")
            return

        if not result.get("ok"):
            err = result.get("error", "неизвестная ошибка")
            await status_msg.edit(f"❌ Недельный отчёт не удался: {err}")
            return

        rounds = result.get("total_rounds", 0)
        cost = result.get("cost_week_usd", 0.0)
        attention = result.get("attention_count", 0)
        calls = result.get("calls_count", 0)
        tokens = result.get("total_tokens", 0)

        lines = [
            "📊 **Weekly Report**",
            "",
            "**🐝 Swarm**",
            f"  • Раундов за неделю: {rounds}",
            "",
            "**💰 Расходы (7 дней)**",
            f"  • Cost: ${cost:.4f}",
            f"  • Вызовов: {calls}",
            f"  • Токенов: {tokens:,}",
            "",
            "**⚠️ Inbox (attention)**",
            f"  • Требуют внимания: {attention}",
        ]
        await status_msg.edit("\n".join(lines))
        return

    # --- кастомный отчёт через LLM ---
    topic = args
    status_msg = await message.reply(f"⏳ Генерирую отчёт по теме: **{topic}**...")

    # Собираем контекст системных данных для LLM
    try:
        daily_data = _collect_daily_report_data()
        context_block = (
            f"Текущие системные данные Краба (на {datetime.date.today().isoformat()}):\n"
            f"- Расходы сегодня: ${daily_data['cost_today_usd']:.4f} ({daily_data['calls_today']} вызовов)\n"
            f"- Расходы за месяц: ${daily_data['cost_month_usd']:.4f}\n"
            f"- Swarm раундов сегодня: {daily_data['swarm_rounds_today']}\n"
            f"- Команды сегодня: {', '.join(daily_data['swarm_teams_today']) or 'нет'}\n"
            f"- Открытых inbox-items: {daily_data['inbox_open']} "
            f"(ошибок: {daily_data['inbox_errors']}, warnings: {daily_data['inbox_warnings']})\n"
        )
    except Exception:  # noqa: BLE001
        context_block = ""

    prompt = (
        f"Ты — аналитик Telegram userbot Краб. Напиши структурированный отчёт по теме: **{topic}**.\n\n"
        f"{context_block}\n"
        "Требования к отчёту:\n"
        "- Оформи в виде markdown с секциями\n"
        "- Выдели ключевые метрики, выводы, рекомендации\n"
        "- Будь конкретным и кратким\n"
        "- Отвечай на русском языке\n"
    )

    header = f"📊 **Отчёт: {topic}**\n─────────────────\n"
    await status_msg.edit(header + "⏳ LLM генерирует...")

    chunks: list[str] = []
    last_edit_len = 0
    edit_threshold = 200

    try:
        async for chunk in openclaw_client.send_message_stream(
            message=prompt,
            chat_id=f"report_{message.chat.id}_{int(datetime.datetime.now().timestamp())}",
            disable_tools=True,
        ):
            chunks.append(str(chunk))
            total = "".join(chunks)
            if len(total) - last_edit_len >= edit_threshold:
                last_edit_len = len(total)
                preview = total
                max_preview = 4000 - len(header)
                if len(preview) > max_preview:
                    preview = preview[-max_preview:]
                try:
                    await status_msg.edit(header + preview)
                except Exception:  # noqa: BLE001
                    pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("handle_report_llm_failed", topic=topic, error=str(exc))
        await status_msg.edit(f"❌ Ошибка генерации отчёта: {exc}")
        return

    # Финальное обновление
    final_text = "".join(chunks)
    max_len = 4000 - len(header)
    if len(final_text) > max_len:
        final_text = final_text[-max_len:]
    try:
        await status_msg.edit(header + final_text)
    except Exception:  # noqa: BLE001
        pass


async def handle_rate(bot: "KraabUserbot", message: Message) -> None:
    """
    Курсы криптовалют и акций через AI + web_search.

    Форматы:
      !rate btc          — текущая цена Bitcoin (цена, 24h%, капитализация)
      !rate eth          — Ethereum
      !rate AAPL         — акция Apple
      !rate btc eth      — сравнение двух активов
      !rate btc eth sol  — сравнение нескольких активов (до 5)
    """
    raw_args = bot._get_command_args(message).strip()

    # Проверяем пустой запрос
    if not raw_args:
        raise UserInputError(
            user_message=(
                "📈 Укажи тикер:\n"
                "`!rate btc` — Bitcoin\n"
                "`!rate eth` — Ethereum\n"
                "`!rate AAPL` — акция Apple\n"
                "`!rate btc eth` — сравнение активов"
            )
        )

    # Парсим список тикеров (разделители: пробел или запятая)
    assets = [a.strip() for a in re.split(r"[\s,]+", raw_args) if a.strip()]

    if not assets:
        raise UserInputError(user_message="📈 Укажи хотя бы один тикер.")

    # Ограничиваем количество активов
    if len(assets) > _RATE_MAX_ASSETS:
        assets = assets[:_RATE_MAX_ASSETS]

    # Изолированная сессия (не загрязняем основной контекст чата)
    session_id = f"rate_{message.chat.id}"

    # Индикатор загрузки
    labels_preview = ", ".join(_rate_asset_label(a) for a in assets)
    msg = await message.reply(f"📈 Смотрю курс: **{labels_preview}**...")

    prompt = _build_rate_prompt(assets)

    try:
        chunks: list[str] = []
        async for chunk in openclaw_client.send_message_stream(
            message=prompt,
            chat_id=session_id,
            disable_tools=False,  # AI использует web_search для актуальных данных
        ):
            chunks.append(str(chunk))

        result = "".join(chunks).strip()
        if not result:
            await msg.edit("❌ Не удалось получить данные о курсе.")
            return

        # Заголовок + ответ AI
        header = f"📈 **{labels_preview}**\n\n"
        full_text = header + result

        # Пагинация для длинных ответов (Telegram лимит ~4096)
        parts = _split_text(full_text)
        total = len(parts)

        first = parts[0]
        if total > 1:
            first += f"\n\n_(часть 1/{total})_"
        await msg.edit(first)

        for i, part in enumerate(parts[1:], start=2):
            suffix = f"\n\n_(часть {i}/{total})_" if total > 2 else ""
            await message.reply(part + suffix)

    except Exception as exc:  # noqa: BLE001
        logger.error("handle_rate_error", assets=assets, error=str(exc))
        await msg.edit(f"❌ Ошибка получения курса: {exc}")


async def handle_explain(bot: "KraabUserbot", message: Message) -> None:
    """
    Объяснение фрагмента кода через AI.

    Форматы:
      !explain <код>   — объясняет переданный код
      !explain         — reply на сообщение с кодом → объясняет код из reply
    """
    raw_args = bot._get_command_args(message).strip()

    # Получаем код: из аргументов или из reply-сообщения
    code = raw_args
    if not code:
        replied = getattr(message, "reply_to_message", None)
        if replied:
            code = (replied.text or replied.caption or "").strip()

    if not code:
        raise UserInputError(
            user_message=(
                "💡 Пришли код для объяснения:\n"
                "`!explain <код>` — вставь код напрямую\n"
                "Или ответь на сообщение с кодом командой `!explain`"
            )
        )

    # Изолированная сессия — не смешиваем с основным диалогом чата
    session_id = f"explain_{message.chat.id}"

    msg = await message.reply("💡 **Анализирую код...**")

    prompt = f"{_EXPLAIN_PROMPT}\n\n```\n{code}\n```"

    try:
        chunks: list[str] = []
        async for chunk in openclaw_client.send_message_stream(
            message=prompt,
            chat_id=session_id,
            disable_tools=True,
            max_output_tokens=1024,
        ):
            chunks.append(str(chunk))

        result = "".join(chunks).strip()

        if not result:
            await msg.edit("❌ AI не смог объяснить этот код.")
            return

        # Пагинация при длинном ответе
        header = "💡 **Объяснение кода**\n\n"
        parts = _split_text(header + result)
        total = len(parts)

        first = parts[0]
        if total > 1:
            first += f"\n\n_(часть 1/{total})_"
        await msg.edit(first)

        for i, part in enumerate(parts[1:], start=2):
            suffix = f"\n\n_(часть {i}/{total})_" if total > 2 else ""
            await message.reply(part + suffix)

    except Exception as exc:  # noqa: BLE001
        logger.error("handle_explain_error", error=str(exc))
        await msg.edit(f"❌ Ошибка объяснения: {exc}")


__all__ = [
    "_EXPLAIN_PROMPT",
    "_REWRITE_MODES",
    "_RATE_CRYPTO_ALIASES",
    "_RATE_MAX_ASSETS",
    "_SUMMARY_DEFAULT_N",
    "_SUMMARY_EDIT_THRESHOLD",
    "_SUMMARY_MAX_HISTORY_CHARS",
    "_SUMMARY_MAX_N",
    "_build_rate_prompt",
    "_collect_daily_report_data",
    "_format_chat_history_for_llm",
    "_parse_ask_memory_flags",
    "_rate_asset_label",
    "_render_daily_report",
    "handle_agent",
    "handle_ask",
    "handle_catchup",
    "handle_explain",
    "handle_fix",
    "handle_rate",
    "handle_report",
    "handle_rewrite",
    "handle_search",
    "handle_summary",
]
