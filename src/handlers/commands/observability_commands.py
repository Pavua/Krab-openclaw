# -*- coding: utf-8 -*-
"""
observability_commands — Phase 2 Wave 17 extraction (Session 28).

Observability/runtime + persistence handlers, ранее жившие в
``src/handlers/command_handlers.py``:

  !watch     — proactive watch контур (status / now)
  !inbox     — owner inbox + escalation workflow
  !context   — управление сессионным контекстом OpenClaw
  !memo      — быстрые заметки в Obsidian
  !bookmark  — закладки Telegram-сообщений
  !note      — голосовая заметка (транскрибация → Obsidian)

Re-exported из command_handlers.py для обратной совместимости (тесты,
external imports `from src.handlers.command_handlers import handle_X`).

Использует dual-namespace lookup pattern (см. Session 27 fbf3262 + Wave 16):
тесты могут патчить `command_handlers.<symbol>` и оно подхватится через
`_ch_attr()`.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import pathlib
import time
from typing import TYPE_CHECKING, Any

from pyrogram.types import Message

from ...config import config as _config_baseline
from ...core.exceptions import UserInputError
from ...core.inbox_service import inbox_service as _inbox_service_baseline
from ...core.logger import get_logger
from ...core.openclaw_runtime_models import (
    get_runtime_primary_model as _get_runtime_primary_model_baseline,
)
from ...core.proactive_watch import proactive_watch as _proactive_watch_baseline
from ...openclaw_client import openclaw_client as _openclaw_client_baseline

if TYPE_CHECKING:
    from ...userbot_bridge import KraabUserbot

_logger_baseline = get_logger(__name__)


class _LoggerProxy:
    """Прокси к command_handlers.logger (тесты патчат его), fallback к локальному."""

    def __getattr__(self, item: str) -> Any:
        from .. import command_handlers as _ch  # noqa: PLC0415

        target = getattr(_ch, "logger", _logger_baseline)
        return getattr(target, item)


logger = _LoggerProxy()


# ---------------------------------------------------------------------------
# Dual-namespace lookup (patch через command_handlers namespace)
# ---------------------------------------------------------------------------


def _ch_attr(name: str, default: Any) -> Any:
    """Dual-namespace lookup: command_handlers namespace first (для monkeypatch),
    fallback к local baseline."""
    from .. import command_handlers as _ch  # noqa: PLC0415

    return getattr(_ch, name, default)


# ---------------------------------------------------------------------------
# Module-level state / helpers
# ---------------------------------------------------------------------------


def _estimate_session_tokens(messages: list[dict]) -> int:
    """Грубая оценка токенов в истории чата (символы / 4)."""
    total_chars = 0
    for msg in messages:
        content = msg.get("content") or ""
        if isinstance(content, list):
            # multipart: собираем текстовые части
            for part in content:
                if isinstance(part, dict):
                    total_chars += len(str(part.get("text") or ""))
                else:
                    total_chars += len(str(part))
        else:
            total_chars += len(str(content))
    return max(0, (total_chars + 3) // 4)


def _format_time_ago(seconds: float) -> str:
    """Человекочитаемое 'X мин/сек/ч назад'."""
    if seconds < 60:
        return f"{int(seconds)} сек назад"
    if seconds < 3600:
        return f"{int(seconds // 60)} мин назад"
    return f"{int(seconds // 3600)} ч назад"


_CHECKPOINTS_DIR = pathlib.Path.home() / ".openclaw" / "krab_runtime_state" / "context_checkpoints"


# ---------------------------------------------------------------------------
# !watch — proactive watch
# ---------------------------------------------------------------------------


async def handle_watch(bot: "KraabUserbot", message: Message) -> None:
    """
    Управление proactive watch контуром.

    Команды:
    - `!watch status` — persisted состояние фонового watch;
    - `!watch now` — принудительно снять digest и записать его в общую память.
    """
    del bot
    proactive_watch = _ch_attr("proactive_watch", _proactive_watch_baseline)
    raw_args = str(message.text or "").split(maxsplit=2)
    action = raw_args[1].strip().lower() if len(raw_args) > 1 else "status"

    if action == "status":
        status = proactive_watch.get_status()
        snapshot = status.get("last_snapshot") or {}
        route_model = str(snapshot.get("route_model") or snapshot.get("primary_model") or "n/a")
        await message.reply(
            "🛰️ **Proactive Watch**\n"
            f"- enabled: `{status.get('enabled')}`\n"
            f"- interval_sec: `{status.get('interval_sec')}`\n"
            f"- alert_cooldown_sec: `{status.get('alert_cooldown_sec')}`\n"
            f"- last_reason: `{status.get('last_reason') or '-'}`\n"
            f"- last_digest_ts: `{status.get('last_digest_ts') or '-'}`\n"
            f"- last_alert_ts: `{status.get('last_alert_ts') or '-'}`\n"
            f"- last_model: `{route_model}`"
        )
        return

    if action == "now":
        result = await proactive_watch.capture(manual=True, persist_memory=True, notify=False)
        suffix = (
            "\n- Память: записано в workspace memory"
            if result.get("wrote_memory")
            else "\n- Память: запись пропущена"
        )
        await message.reply(str(result.get("digest") or "watch digest unavailable") + suffix)
        return

    raise UserInputError(user_message="🛰️ Формат: `!watch status` или `!watch now`")


# ---------------------------------------------------------------------------
# !inbox — owner inbox + escalation
# ---------------------------------------------------------------------------


async def handle_inbox(bot: "KraabUserbot", message: Message) -> None:
    """
    Owner-visible inbox и escalation foundation.

    Поддерживаем owner workflow-подмножество:
    - `!inbox` / `!inbox list` — открыть текущие open items;
    - `!inbox status` — краткий summary;
    - `!inbox ack <id>` — отметить как просмотренное;
    - `!inbox done <id>` — закрыть item;
    - `!inbox cancel <id>` — отменить item вручную.
    - `!inbox approve <id>` / `!inbox reject <id>` — принять решение по approval item;
    - `!inbox task <title> | <body>` — создать owner-task;
    - `!inbox taskfrom <source_id> | <title> | <body>` — эскалировать item в owner-task;
    - `!inbox approval <scope> | <title> | <body>` — создать approval-request.
    - `!inbox approvalfrom <source_id> | <scope> | <title> | <body>` — эскалировать item в approval.
    """
    del bot
    inbox_service = _ch_attr("inbox_service", _inbox_service_baseline)
    raw_args = str(message.text or "").split(maxsplit=2)
    action = raw_args[1].strip().lower() if len(raw_args) > 1 else "list"

    if action == "status":
        summary = inbox_service.get_summary()
        await message.reply(
            "📥 **Inbox / Escalation**\n"
            f"- operator: `{summary.get('operator_id')}`\n"
            f"- account_id: `{summary.get('account_id')}`\n"
            f"- open_items: `{summary.get('open_items')}`\n"
            f"- attention_items: `{summary.get('attention_items')}`\n"
            f"- pending_reminders: `{summary.get('pending_reminders')}`\n"
            f"- open_escalations: `{summary.get('open_escalations')}`\n"
            f"- pending_owner_tasks: `{summary.get('pending_owner_tasks')}`\n"
            f"- pending_approvals: `{summary.get('pending_approvals')}`\n"
            f"- pending_owner_requests: `{summary.get('pending_owner_requests')}`\n"
            f"- pending_owner_mentions: `{summary.get('pending_owner_mentions')}`\n"
            f"- state: `{summary.get('state_path')}`"
        )
        return

    if action in {"list", "open"}:
        rows = inbox_service.list_items(status="open", limit=8)
        if not rows:
            await message.reply("📥 Inbox сейчас пуст: открытых items нет.")
            return
        lines = ["📥 **Открытые inbox items**"]
        for item in rows:
            meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            due = str(meta.get("due_at_iso") or "").strip()
            due_suffix = f" · due `{due}`" if due else ""
            approval_scope = str((item.get("identity") or {}).get("approval_scope") or "").strip()
            approval_suffix = (
                f" · scope `{approval_scope}`"
                if approval_scope and item["kind"] == "approval_request"
                else ""
            )
            lines.append(
                f"- `{item['item_id']}` · `{item['kind']}` · `{item['severity']}`{due_suffix}{approval_suffix}\n"
                f"  {item['title']}"
            )
        await message.reply("\n".join(lines))
        return

    if action == "task":
        if len(raw_args) < 3 or "|" not in raw_args[2]:
            raise UserInputError(user_message="📥 Формат: `!inbox task <title> | <body>`")
        title, body = [part.strip() for part in raw_args[2].split("|", maxsplit=1)]
        if not title or not body:
            raise UserInputError(user_message="📥 Для task нужны и заголовок, и описание.")
        created = inbox_service.upsert_owner_task(title=title, body=body, source="telegram-owner")
        await message.reply(
            "📝 Owner-task создан.\n"
            f"- ID: `{created['item']['item_id']}`\n"
            f"- Title: {created['item']['title']}"
        )
        return

    if action == "taskfrom":
        if len(raw_args) < 3 or raw_args[2].count("|") < 2:
            raise UserInputError(
                user_message="📥 Формат: `!inbox taskfrom <source_id> | <title> | <body>`"
            )
        source_item_id, title, body = [part.strip() for part in raw_args[2].split("|", maxsplit=2)]
        if not source_item_id or not title or not body:
            raise UserInputError(
                user_message="📥 Для taskfrom нужны source_id, заголовок и описание."
            )
        created = inbox_service.escalate_item_to_owner_task(
            source_item_id=source_item_id,
            title=title,
            body=body,
            source="telegram-owner",
            metadata={"requested_via": "telegram"},
        )
        if not created.get("ok"):
            raise UserInputError(user_message=f"📥 Source item `{source_item_id}` не найден.")
        await message.reply(
            "📝 Owner-task создан из inbox item.\n"
            f"- Source: `{source_item_id}`\n"
            f"- ID: `{created['item']['item_id']}`\n"
            f"- Trace: `{created['item']['identity']['trace_id']}`"
        )
        return

    if action == "approval":
        if len(raw_args) < 3 or raw_args[2].count("|") < 2:
            raise UserInputError(
                user_message="📥 Формат: `!inbox approval <scope> | <title> | <body>`"
            )
        scope, title, body = [part.strip() for part in raw_args[2].split("|", maxsplit=2)]
        if not scope or not title or not body:
            raise UserInputError(user_message="📥 Для approval нужны scope, заголовок и описание.")
        created = inbox_service.upsert_approval_request(
            title=title,
            body=body,
            source="telegram-owner",
            approval_scope=scope,
            requested_action=title,
            metadata={"requested_via": "telegram"},
        )
        await message.reply(
            "🛂 Approval-request создан.\n"
            f"- ID: `{created['item']['item_id']}`\n"
            f"- Scope: `{scope}`\n"
            f"- Title: {created['item']['title']}"
        )
        return

    if action == "approvalfrom":
        if len(raw_args) < 3 or raw_args[2].count("|") < 3:
            raise UserInputError(
                user_message="📥 Формат: `!inbox approvalfrom <source_id> | <scope> | <title> | <body>`"
            )
        source_item_id, scope, title, body = [
            part.strip() for part in raw_args[2].split("|", maxsplit=3)
        ]
        if not source_item_id or not scope or not title or not body:
            raise UserInputError(
                user_message="📥 Для approvalfrom нужны source_id, scope, заголовок и описание."
            )
        created = inbox_service.escalate_item_to_approval_request(
            source_item_id=source_item_id,
            title=title,
            body=body,
            source="telegram-owner",
            approval_scope=scope,
            requested_action=title,
            metadata={"requested_via": "telegram"},
        )
        if not created.get("ok"):
            raise UserInputError(user_message=f"📥 Source item `{source_item_id}` не найден.")
        await message.reply(
            "🛂 Approval-request создан из inbox item.\n"
            f"- Source: `{source_item_id}`\n"
            f"- ID: `{created['item']['item_id']}`\n"
            f"- Scope: `{scope}`\n"
            f"- Trace: `{created['item']['identity']['trace_id']}`"
        )
        return

    if action not in {"ack", "done", "cancel", "approve", "reject"}:
        raise UserInputError(
            user_message=(
                "📥 Формат: "
                "`!inbox [list|status|ack <id>|done <id>|cancel <id>|approve <id>|reject <id>|task <title> | <body>|taskfrom <source_id> | <title> | <body>|approval <scope> | <title> | <body>|approvalfrom <source_id> | <scope> | <title> | <body>]`"
            )
        )

    if len(raw_args) < 3 or not raw_args[2].strip():
        raise UserInputError(
            user_message="📥 Укажи item id: `!inbox ack|done|cancel|approve|reject <id> [| note]`"
        )
    target_payload = raw_args[2].strip()
    target_id, note = (
        [part.strip() for part in target_payload.split("|", maxsplit=1)]
        if "|" in target_payload
        else (target_payload, "")
    )
    if not target_id:
        raise UserInputError(
            user_message="📥 Укажи корректный item id: `!inbox ack|done|cancel|approve|reject <id> [| note]`"
        )
    if action in {"approve", "reject"}:
        result = inbox_service.resolve_approval(
            target_id,
            approved=(action == "approve"),
            actor="telegram-owner",
            note=note,
        )
        target_status = "approved" if action == "approve" else "rejected"
    else:
        target_status = {"ack": "acked", "done": "done", "cancel": "cancelled"}[action]
        result = inbox_service.set_item_status(
            target_id,
            status=target_status,
            actor="telegram-owner",
            note=note,
        )
    if not result.get("ok"):
        if result.get("error") == "inbox_item_not_approval":
            raise UserInputError(
                user_message=f"📥 Item `{target_id}` не является approval-request."
            )
        raise UserInputError(user_message=f"📥 Item `{target_id}` не найден.")
    await message.reply(
        "✅ Inbox item обновлён.\n"
        f"- ID: `{target_id}`\n"
        f"- Новый статус: `{target_status}`" + (f"\n- Note: {note}" if note else "")
    )


# ---------------------------------------------------------------------------
# !context — управление сессионным контекстом OpenClaw
# ---------------------------------------------------------------------------


async def handle_context(bot: "KraabUserbot", message: Message) -> None:
    """!context — управление контекстом чата OpenClaw.

    Синтаксис:
      !context              — показать текущий контекст чата
      !context clear        — очистить историю (сброс)
      !context save         — сохранить checkpoint контекста
    """
    del bot
    openclaw_client = _ch_attr("openclaw_client", _openclaw_client_baseline)
    config = _ch_attr("config", _config_baseline)
    get_runtime_primary_model = _ch_attr(
        "get_runtime_primary_model", _get_runtime_primary_model_baseline
    )
    chat_id = str(message.chat.id)
    raw = (message.text or "").strip()
    parts = raw.split(maxsplit=1)
    sub = parts[1].strip().lower() if len(parts) > 1 else ""

    if sub in ("clear", "очисти", "сброс"):
        # Сброс контекста чата
        openclaw_client.clear_session(chat_id)
        await message.reply(
            "🗑️ **Контекст очищен**\nИстория чата сброшена. Следующее сообщение начнёт новую сессию."
        )
        return

    checkpoints_dir = _ch_attr("_CHECKPOINTS_DIR", _CHECKPOINTS_DIR)
    if sub in ("save", "сохрани", "checkpoint"):
        # Сохранение checkpoint контекста в JSON
        messages = list(openclaw_client._sessions.get(chat_id) or [])
        if not messages:
            await message.reply("⚠️ Контекст пуст — нечего сохранять.")
            return
        try:
            checkpoints_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
            filename = checkpoints_dir / f"{chat_id}_{ts}.json"
            payload = {
                "chat_id": chat_id,
                "saved_at": ts,
                "message_count": len(messages),
                "estimated_tokens": _estimate_session_tokens(messages),
                "messages": messages,
            }
            filename.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
            await message.reply(
                f"💾 **Checkpoint сохранён**\nФайл: `{filename.name}`\nСообщений: {len(messages)}"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("context_checkpoint_save_failed", chat_id=chat_id, error=str(exc))
            await message.reply(f"❌ Не удалось сохранить checkpoint: {exc}")
        return

    # Показ текущего контекста (по умолчанию)
    messages = list(openclaw_client._sessions.get(chat_id) or [])
    # Считаем только не-системные сообщения как «диалоговые»
    dialog_msgs = [m for m in messages if m.get("role") != "system"]
    msg_count = len(dialog_msgs)
    token_est = _estimate_session_tokens(messages)

    # Определяем текущую модель через runtime route или config
    model = ""
    if hasattr(openclaw_client, "get_last_runtime_route"):
        route_meta = openclaw_client.get_last_runtime_route() or {}
        model = str(route_meta.get("model") or "").strip()
    if not model:
        model = str(get_runtime_primary_model() or getattr(config, "MODEL", "") or "unknown")

    # Время последнего обновления из атрибута _session_last_updated (если есть)
    last_update_str = "—"
    try:
        if hasattr(openclaw_client, "_session_last_updated"):
            ts_map: dict = openclaw_client._session_last_updated  # type: ignore[attr-defined]
            last_ts = ts_map.get(chat_id)
            if last_ts is not None:
                elapsed = time.time() - last_ts
                last_update_str = _format_time_ago(elapsed)
    except Exception:  # noqa: BLE001
        pass

    # Считаем сохранённые checkpoint'ы для этого чата
    checkpoint_count = 0
    try:
        if checkpoints_dir.exists():
            checkpoint_count = sum(1 for _ in checkpoints_dir.glob(f"{chat_id}_*.json"))
    except Exception:  # noqa: BLE001
        pass

    lines = [
        "📎 **Контекст чата**",
        "─────────────────────",
        f"Сообщений: `{msg_count}`",
        f"Токенов (оценка): `~{token_est:,}`".replace(",", "_"),
        f"Модель: `{model}`",
        f"Session ID: `telegram_{chat_id}`",
        f"Последнее обновление: {last_update_str}",
    ]
    if checkpoint_count:
        lines.append(f"Checkpoints: `{checkpoint_count}`")
    lines += [
        "",
        "Команды:",
        "`!context clear` — сбросить контекст",
        "`!context save` — сохранить checkpoint",
    ]

    await message.reply("\n".join(lines))


# ---------------------------------------------------------------------------
# !memo — быстрые заметки в Obsidian
# ---------------------------------------------------------------------------


async def handle_memo(bot: "KraabUserbot", message: Message) -> None:
    """
    Быстрые заметки из Telegram в Obsidian vault (00_Inbox).

    Синтаксис:
      !memo <текст>           — сохранить заметку
      !memo list [N]          — последние N заметок (по умолчанию 5)
      !memo search <запрос>   — поиск по заметкам
    """
    from ...core.memo_service import memo_service

    args = bot._get_command_args(message).strip()

    # Определяем название чата
    chat = message.chat
    chat_title: str = (
        getattr(chat, "title", None) or getattr(chat, "first_name", None) or str(chat.id)
    )

    if not args or args.lower() in ("memo", "!memo"):
        raise UserInputError(
            user_message=(
                "📝 **Memo — быстрые заметки в Obsidian**\n\n"
                "`!memo <текст>` — сохранить заметку\n"
                "`!memo list [N]` — последние N заметок\n"
                "`!memo search <запрос>` — поиск по заметкам"
            )
        )

    # --- list ---
    if args.lower().startswith("list"):
        parts = args.split(maxsplit=1)
        n = 5
        if len(parts) == 2 and parts[1].isdigit():
            n = max(1, min(int(parts[1]), 50))
        items = memo_service.list_recent(n)
        if not items:
            await message.reply("📭 Заметок в 00_Inbox пока нет.")
            return
        lines = [f"📝 **Последние заметки ({len(items)}):**\n"]
        for i, item in enumerate(items, 1):
            lines.append(
                f"{i}. `{item['filename']}`\n   🕐 {item['created']}\n   {item['preview']}"
            )
        await message.reply("\n".join(lines))
        return

    # --- search ---
    if args.lower().startswith("search "):
        query = args[7:].strip()
        if not query:
            raise UserInputError(user_message="🔍 Укажи запрос: `!memo search <текст>`")
        results = memo_service.search(query)
        if not results:
            await message.reply(f"🔍 Ничего не найдено по запросу: `{query}`")
            return
        lines = [f"🔍 **Найдено ({len(results)}):**\n"]
        for item in results:
            lines.append(f"📄 `{item['filename']}`\n   {item['match']}")
        await message.reply("\n".join(lines))
        return

    # --- save ---
    result = await memo_service.save_async(args, chat_title=chat_title)
    if result.success:
        await message.reply(f"✅ {result.message}")
    else:
        await message.reply(f"❌ {result.message}")


# ---------------------------------------------------------------------------
# !bookmark — закладки Telegram-сообщений
# ---------------------------------------------------------------------------


async def handle_bookmark(bot: "KraabUserbot", message: Message) -> None:
    """
    Закладки на важные Telegram-сообщения.

    Синтаксис:
      !bookmark (или !bm) — в ответ на сообщение, сохраняет закладку
      !bookmark list       — список всех закладок с превью
      !bookmark search <запрос> — поиск по закладкам
      !bookmark del <id>   — удалить закладку по ID
    """
    import datetime as _dt

    from ...core.bookmark_service import bookmark_service

    args = bot._get_command_args(message).strip()

    # ─── help ───────────────────────────────────────────────────────────────
    if args.lower() in ("help", "помощь", "?"):
        raise UserInputError(
            user_message=(
                "🔖 **Bookmark — закладки на сообщения**\n\n"
                "Ответь на сообщение и введи:\n"
                "`!bookmark` или `!bm` — сохранить закладку\n"
                "`!bookmark list` — все закладки\n"
                "`!bookmark search <запрос>` — поиск\n"
                "`!bookmark del <id>` — удалить"
            )
        )

    # ─── list ───────────────────────────────────────────────────────────────
    if args.lower() in ("list", "список", "ls"):
        items = bookmark_service.list_all()
        if not items:
            await message.reply("📭 Закладок пока нет.")
            return
        lines = [f"🔖 **Закладки ({len(items)}):**\n"]
        for b in items:
            ts = _dt.datetime.fromtimestamp(b["timestamp"]).strftime("%d.%m %H:%M")
            preview = b["text_preview"] or "—"
            from_user = b.get("from_user") or "?"
            lines.append(f"**#{b['id']}** · {b['chat_title']} · {from_user} · {ts}\n   {preview}")
        await message.reply("\n".join(lines))
        return

    # ─── search ─────────────────────────────────────────────────────────────
    if args.lower().startswith("search ") or args.lower().startswith("поиск "):
        query = args.split(maxsplit=1)[1].strip() if " " in args else ""
        if not query:
            raise UserInputError(user_message="🔍 Укажи запрос: `!bookmark search <текст>`")
        results = bookmark_service.search(query)
        if not results:
            await message.reply(f"🔍 Ничего не найдено по запросу: `{query}`")
            return
        lines = [f"🔍 **Найдено ({len(results)}):**\n"]
        for b in results:
            ts = _dt.datetime.fromtimestamp(b["timestamp"]).strftime("%d.%m %H:%M")
            lines.append(
                f"**#{b['id']}** · {b['chat_title']} · {ts}\n   {b['text_preview'] or '—'}"
            )
        await message.reply("\n".join(lines))
        return

    # ─── del ────────────────────────────────────────────────────────────────
    if (
        args.lower().startswith("del ")
        or args.lower().startswith("delete ")
        or args.lower().startswith("rm ")
    ):
        id_str = args.split(maxsplit=1)[1].strip() if " " in args else ""
        if not id_str.isdigit():
            raise UserInputError(user_message="❌ Укажи числовой ID: `!bookmark del <id>`")
        bm_id = int(id_str)
        ok = await bookmark_service.delete_async(bm_id)
        if ok:
            await message.reply(f"🗑 Закладка **#{bm_id}** удалена.")
        else:
            await message.reply(f"❌ Закладка #{bm_id} не найдена.")
        return

    # ─── save (ответ на сообщение) ──────────────────────────────────────────
    reply = getattr(message, "reply_to_message", None)
    if reply is None:
        raise UserInputError(
            user_message=(
                "🔖 Ответь на сообщение, которое хочешь сохранить как закладку:\n"
                "`!bookmark` или `!bm`\n\n"
                "Другие команды:\n"
                "`!bookmark list` · `!bookmark search <текст>` · `!bookmark del <id>`"
            )
        )

    # Собираем данные о сообщении
    chat = message.chat
    chat_title: str = (
        getattr(chat, "title", None) or getattr(chat, "first_name", None) or str(chat.id)
    )
    reply_from = getattr(reply, "from_user", None)
    if reply_from:
        from_name = (
            getattr(reply_from, "username", None)
            and f"@{reply_from.username}"
            or getattr(reply_from, "first_name", None)
            or str(getattr(reply_from, "id", "?"))
        )
    else:
        from_name = "?"

    text = str(getattr(reply, "text", None) or getattr(reply, "caption", None) or "").strip()
    if not text:
        text = "[медиа-сообщение без текста]"

    bm = await bookmark_service.add_async(
        chat_id=chat.id,
        chat_title=chat_title,
        message_id=reply.id,
        text=text,
        from_user=from_name,
    )
    await message.reply(
        f"🔖 Закладка **#{bm['id']}** сохранена!\n"
        f"Чат: {chat_title} · От: {from_name}\n"
        f"_{bm['text_preview'][:100]}{'…' if len(bm['text_preview']) > 100 else ''}_"
    )


# ---------------------------------------------------------------------------
# !note — голосовая заметка (транскрибация → Obsidian)
# ---------------------------------------------------------------------------


async def handle_note(bot: "KraabUserbot", message: Message) -> None:
    """
    Голосовая заметка в Obsidian через транскрибацию.

    Использование:
      !note              — в ответ на голосовое сообщение → транскрибирует и сохраняет
      !note <тег>        — добавляет тег к заметке (например: !note идея)

    Записывает с пометкой [voice] и source: krab-voice.
    """
    from ...core.memo_service import memo_service

    # Проверяем: !note должна быть reply на голосовое сообщение
    reply = message.reply_to_message
    if reply is None:
        raise UserInputError(
            user_message=(
                "🎤 **Note — голосовая заметка в Obsidian**\n\n"
                "Ответь командой `!note` на голосовое сообщение.\n"
                "Добавь тег: `!note идея`"
            )
        )

    # Проверяем что reply содержит аудио
    has_voice = bool(getattr(reply, "voice", None))
    has_audio = bool(getattr(reply, "audio", None))
    has_video_note = bool(getattr(reply, "video_note", None))

    if not (has_voice or has_audio or has_video_note):
        raise UserInputError(
            user_message="❌ Команда `!note` работает только в ответ на голосовое сообщение."
        )

    # Извлекаем опциональный тег из аргументов команды
    raw_args = bot._get_command_args(message).strip()
    # Убираем само слово "note" если пользователь написал "!note note"
    if raw_args.lower() == "note":
        raw_args = ""
    user_tag = raw_args if raw_args else None

    # Определяем название чата
    chat = message.chat
    chat_title: str = (
        getattr(chat, "title", None) or getattr(chat, "first_name", None) or str(chat.id)
    )

    # Транскрибируем через существующий _transcribe_audio_message
    status_msg = await message.reply("⏳ Транскрибирую голосовое сообщение…")

    transcript, voice_error = await bot._transcribe_audio_message(reply)

    if not transcript:
        err = voice_error or "❌ Не удалось распознать голосовое сообщение."
        try:
            await status_msg.edit(err)
        except Exception:  # noqa: BLE001
            await message.reply(err)
        return

    # Формируем тело заметки с пометкой [voice]
    note_body = f"[voice] {transcript}"

    # Теги: всегда "voice", плюс пользовательский тег если задан
    tags: list[str] = ["voice"]
    if user_tag:
        tags.append(user_tag)

    result = await memo_service.save_async(
        text=note_body,
        chat_title=chat_title,
        tags=tags,
        source_type="krab-voice",
    )

    if result.success:
        tag_display = f" #{user_tag}" if user_tag else ""
        reply_text = (
            f"✅ Голосовая заметка сохранена{tag_display}\n"
            f"📄 `{result.file_path.name if result.file_path else '?'}`\n"
            f"\n_{transcript[:300]}{'…' if len(transcript) > 300 else ''}_"
        )
    else:
        reply_text = f"❌ {result.message}"

    try:
        await status_msg.edit(reply_text)
    except Exception:  # noqa: BLE001
        await message.reply(reply_text)


# ---------------------------------------------------------------------------
# !quota — состояние квот по всем провайдерам
# ---------------------------------------------------------------------------

_LOG_FILE = pathlib.Path.home() / ".openclaw/krab_runtime_state/krab_main.log"


async def _probe_gemini_cli(timeout: float = 15.0) -> str:
    """Быстрый probe gemini-cli (free OAuth)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "gemini",
            "--model",
            "gemini-2.5-flash",
            "-p",
            "ok",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode == 0 and stdout.strip():
            return "✅ ok"
        return "❌ failed"
    except asyncio.TimeoutError:
        return "⏱ timeout"
    except FileNotFoundError:
        return "⚠️ gemini not found"
    except Exception as exc:  # noqa: BLE001
        return f"⚠️ {str(exc)[:30]}"


async def _probe_anthropic_vertex() -> str:
    """Probe anthropic-vertex (claude-haiku-4-5, cheapest)."""
    try:
        import anthropic  # noqa: PLC0415

        client = anthropic.AnthropicVertex(region="us-east5", project_id="caramel-anvil-492816-t5")
        client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=5,
            messages=[{"role": "user", "content": "hi"}],
        )
        return "✅ ok"
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)[:80]
        if "429" in msg:
            return "⏳ 0 quota (waiting Google approval)"
        if "404" in msg:
            return "❌ not_found"
        return f"⚠️ {msg[:50]}"


async def _probe_vertex_gemini() -> str:
    """Probe google-vertex (gemini-2.5-flash через google-genai)."""
    try:
        from google import genai  # noqa: PLC0415

        gc = genai.Client(
            vertexai=True,
            project="caramel-anvil-492816-t5",
            location="global",
        )
        gc.models.generate_content(model="gemini-2.5-flash", contents="ok")
        return "✅ ok"
    except Exception as exc:  # noqa: BLE001
        return f"⚠️ {str(exc)[:50]}"


def _count_today_calls(log_file: pathlib.Path, today_str: str) -> dict[str, int]:
    """Считает bypass-вызовы за сегодня по лог-файлу."""
    counts: dict[str, int] = {
        "gemini": 0,
        "codex": 0,
        "vertex": 0,
        "anthropic": 0,
    }
    if not log_file.exists():
        return counts
    try:
        with log_file.open(errors="replace") as fh:
            for line in fh:
                if today_str not in line:
                    continue
                if "cli_subprocess_bypass_engaged" in line:
                    if "binary=gemini" in line:
                        counts["gemini"] += 1
                    elif "binary=codex" in line:
                        counts["codex"] += 1
                elif "google_vertex_bypass_engaged" in line:
                    counts["vertex"] += 1
                elif "anthropic_vertex_bypass_engaged" in line:
                    counts["anthropic"] += 1
    except OSError:
        pass
    return counts


async def handle_quota(bot: "KraabUserbot", message: Message) -> None:
    """
    !quota — показывает состояние квот по всем провайдерам.

    Опции:
    - ``!quota``            — полный отчёт (с probe)
    - ``!quota --no-probe`` — только счётчики из лога (быстро, без сетевых вызовов)
    """
    del bot

    raw = str(message.text or "")
    no_probe = "--no-probe" in raw

    today_str = datetime.datetime.now().strftime("%Y-%m-%d")

    # Собираем счётчики из лога
    counts = _count_today_calls(_LOG_FILE, today_str)

    # Параллельные probe (если не --no-probe)
    if no_probe:
        gemini_status = "❓ пропущен (--no-probe)"
        anthropic_status = "❓ пропущен (--no-probe)"
        vertex_status = "❓ пропущен (--no-probe)"
    else:
        gemini_status, anthropic_status, vertex_status = await asyncio.gather(
            _probe_gemini_cli(),
            _probe_anthropic_vertex(),
            _probe_vertex_gemini(),
        )

    text = (
        f"📊 *Quota Status* ({today_str})\n"
        "\n"
        "🟢 *gemini-cli* (free OAuth, ~1000/day shared)\n"
        f"   Probe: {gemini_status}\n"
        f"   Today: {counts['gemini']} calls (bypass)\n"
        "\n"
        "🟢 *codex-cli* (ChatGPT Plus)\n"
        f"   Today: {counts['codex']} calls\n"
        "\n"
        "🟡 *google-vertex* (€848 credits)\n"
        f"   gemini-2.5-flash probe: {vertex_status}\n"
        f"   Today: {counts['vertex']} calls\n"
        "\n"
        "🟣 *anthropic-vertex* (Claude)\n"
        f"   claude-haiku-4-5 probe: {anthropic_status}\n"
        f"   Today: {counts['anthropic']} calls"
    )

    # Wave 25-D-fix: Pyrogram требует enum или "Markdown"/"HTML" с capital
    from pyrogram.enums import ParseMode

    await message.reply(text, parse_mode=ParseMode.MARKDOWN)


# ---------------------------------------------------------------------------
# !metrics — Wave 39-A: единый системный дашборд (one-card overview)
# ---------------------------------------------------------------------------


async def handle_metrics(bot: "KraabUserbot", message: Message) -> None:
    """!metrics — единый отчёт по всем системам Krab (one-card overview).

    Агрегирует:
    - Uptime Krab-процесса
    - Bypass perf (последний час): top-3 кайндов + p95 + кол-во фейлов
    - Quota: счётчики вызовов за сегодня по 4 провайдерам
    - Memory: krab_rss + ear_rss + swap из coexistence_monitor.log
    - Daemons: число активных LaunchAgents ai.krab.*
    - Zombie escalations: суммарный счётчик из лога (если > 0)
    """
    import json
    import subprocess
    import time
    import urllib.request
    from pathlib import Path

    import psutil
    from pyrogram.enums import ParseMode

    del bot  # не используется напрямую

    parts: list[str] = ["📊 *Krab Metrics* — единый отчёт"]

    # 1. Uptime Krab-процесса
    try:
        krab_pids = [
            p.pid
            for p in psutil.process_iter(["cmdline"])
            if p.info.get("cmdline")
            and any("userbot_bridge" in c or "src/main.py" in c for c in p.info["cmdline"])
        ]
        if krab_pids:
            create_time = psutil.Process(krab_pids[0]).create_time()
            uptime_min = (time.time() - create_time) / 60
            uptime_str = f"{uptime_min / 60:.1f}h" if uptime_min > 60 else f"{uptime_min:.0f} min"
            parts.append(f"\n⏱ *Uptime*: {uptime_str}")
    except Exception:
        pass

    # 2. Bypass perf (Wave 31-A endpoint)
    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:8080/api/bypass/perf?window=1h", timeout=3
        ) as resp:
            perf: dict = json.loads(resp.read())
        total = perf.get("total_calls", 0)
        fails = perf.get("total_failures", 0)
        parts.append(f"\n🔌 *Bypass* (1h): {total} calls, {fails} fails")
        # top-3 кайнда по убыванию count
        by_kind: dict = perf.get("by_kind", {})
        sorted_kinds = sorted(by_kind.items(), key=lambda kv: kv[1].get("count", 0), reverse=True)
        for kind, stats in sorted_kinds[:3]:
            p95 = stats.get("p95", 0.0)
            cnt = stats.get("count", 0)
            parts.append(f"  {kind}: {cnt} ({p95:.1f}s p95)")
    except Exception as exc:
        parts.append(f"\n🔌 *Bypass*: ⚠️ {str(exc)[:40]}")

    # 3. Quota — счётчики вызовов за сегодня (без probe для скорости)
    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:8080/api/quota?probe=false", timeout=3
        ) as resp:
            quota: dict = json.loads(resp.read())
        providers = quota.get("providers", {})
        if providers:
            parts.append("\n📈 *Today's calls*:")
            for prov, info in providers.items():
                calls = info.get("today_calls", 0)
                parts.append(f"  {prov}: {calls}")
    except Exception:
        # Fallback: считаем из лога напрямую через _count_today_calls
        try:
            today_str = datetime.datetime.now().strftime("%Y-%m-%d")
            counts = _count_today_calls(_LOG_FILE, today_str)
            parts.append("\n📈 *Today's calls* (local):")
            for prov, cnt in counts.items():
                parts.append(f"  {prov}: {cnt}")
        except Exception:
            pass

    # 4. Memory — последняя запись coexistence_monitor.log (Wave 25-B)
    try:
        log_path = Path.home() / ".openclaw/krab_runtime_state/coexistence_monitor.log"
        if log_path.exists():
            with log_path.open("rb") as fh:
                fh.seek(0, 2)
                sz = fh.tell()
                fh.seek(max(0, sz - 4096))
                tail_raw = fh.read().decode(errors="ignore").strip().splitlines()
            if tail_raw:
                last_entry: dict = json.loads(tail_raw[-1])
                swap = last_entry.get("swap_used_gb", 0.0)
                krab = last_entry.get("krab_rss_gb", 0.0)
                ear = last_entry.get("ear_rss_gb", 0.0)
                ram_avail = last_entry.get("system_ram_available_gb", 0.0)
                parts.append(
                    f"\n🖥 *Memory*: krab={krab:.1f}GB ear={ear:.1f}GB"
                    f" swap={swap:.1f}GB free={ram_avail:.1f}GB"
                )
    except Exception:
        pass

    # 5. Daemons — Wave 40-A-fix-3 semantic: exit_code <= 1 = healthy, >= 2 = broken
    # PID '-' нормально для cron-style, EXIT — реальный health signal
    try:
        result = subprocess.run(
            ["launchctl", "list"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        krab_lines = [line for line in result.stdout.splitlines() if "ai.krab." in line]
        healthy = 0
        broken = 0
        for line in krab_lines:
            parts2 = line.split()
            if len(parts2) < 3:
                continue
            try:
                exit_code = int(parts2[1])
            except ValueError:
                continue
            if exit_code <= 1:
                healthy += 1
            else:
                broken += 1
        broken_str = f", {broken} broken" if broken else ""
        parts.append(f"\n⚙️ *Daemons*: {healthy}/{len(krab_lines)} healthy{broken_str}")
    except Exception:
        pass

    # 6. Zombie escalations (Wave 36-A/B) — только если счётчик > 0
    try:
        krab_log = Path.home() / ".openclaw/krab_runtime_state/krab_main.log"
        if krab_log.exists():
            grep = subprocess.run(
                ["grep", "-c", "telegram_session_zombie_escalation", str(krab_log)],
                capture_output=True,
                text=True,
                timeout=3,
            )
            zombie_count = int(grep.stdout.strip() or "0")
            if zombie_count > 0:
                parts.append(f"\n🧟 *Zombie escalations*: {zombie_count} (life-time)")
    except Exception:
        pass

    await message.reply("\n".join(parts), parse_mode=ParseMode.MARKDOWN)


__all__ = [
    "_CHECKPOINTS_DIR",
    "_count_today_calls",
    "_estimate_session_tokens",
    "_format_time_ago",
    "_probe_anthropic_vertex",
    "_probe_gemini_cli",
    "_probe_vertex_gemini",
    "handle_bookmark",
    "handle_context",
    "handle_inbox",
    "handle_memo",
    "handle_metrics",
    "handle_note",
    "handle_quota",
    "handle_watch",
]
