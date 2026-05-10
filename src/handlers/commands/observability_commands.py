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
# Wave 48-B: !routes — детальный routing state
# ---------------------------------------------------------------------------


def _format_recovery_time(seconds: int) -> str:
    """Преобразует секунды в человеко-читаемый ETA (4d 12h / 2h 15m / 5m 30s / 23s).

    Соответствует Wave 48-B спецификации: компактный формат с двумя единицами.
    """
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec}s" if sec else f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h" if hours else f"{days}d"


def _read_codex_quota_state() -> dict[str, Any]:
    """Читает codex_quota_state.json — graceful если файл отсутствует/повреждён."""
    state_path = pathlib.Path.home() / ".openclaw/krab_runtime_state/codex_quota_state.json"
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError, TypeError):
        return {}


def _format_chain_state(
    primary: str,
    fallbacks: list[str],
    quota_state: dict[str, Any],
    now_utc: datetime.datetime | None = None,
) -> list[str]:
    """Собирает emoji-marked lines per-model.

    Логика:
    - Если codex disabled и модель начинается с ``codex-cli/`` → quota exhausted
      с recovery ETA из disabled_at + cooldown (weekly=7d / transient=1h).
    - Иначе ✅ available (минималистично; runtime failure-state probe выходит за
      Wave 48-B scope, оставлено как placeholder ".".
    """
    if now_utc is None:
        now_utc = datetime.datetime.now(datetime.timezone.utc)

    chain: list[str] = []
    seen: set[str] = set()
    # primary первым (если его нет в fallbacks — добавим)
    ordered = [primary] if primary else []
    for fb in fallbacks:
        if fb not in ordered:
            ordered.append(fb)

    codex_disabled = bool(quota_state.get("disabled"))
    disabled_at_raw = quota_state.get("disabled_at") or ""
    kind = (quota_state.get("kind") or "").strip() or "weekly"
    # Cooldown: weekly=7d, transient=1h
    cooldown_sec = 7 * 24 * 3600 if kind == "weekly" else 3600

    recovery_eta_str: str | None = None
    if codex_disabled and disabled_at_raw:
        try:
            disabled_dt = datetime.datetime.fromisoformat(disabled_at_raw)
            elapsed = (now_utc - disabled_dt).total_seconds()
            remaining = max(0, int(cooldown_sec - elapsed))
            recovery_eta_str = _format_recovery_time(remaining)
        except (ValueError, TypeError):
            recovery_eta_str = None

    for model in ordered:
        if not model or model in seen:
            continue
        seen.add(model)
        if codex_disabled and model.startswith("codex-cli/"):
            if recovery_eta_str:
                chain.append(f"⏸ {model} — quota exhausted ({kind}, recovery {recovery_eta_str})")
            else:
                chain.append(f"⏸ {model} — quota exhausted ({kind})")
        else:
            chain.append(f"✅ {model}")
    return chain


def _format_recent_switches(entries: list[dict[str, Any]]) -> list[str]:
    """Преобразует JSONL entries в строки `HH:MM from → to (reason)`."""
    lines: list[str] = []
    for entry in entries:
        ts_raw = str(entry.get("ts") or "")
        try:
            ts_dt = datetime.datetime.fromisoformat(ts_raw)
            ts_str = ts_dt.strftime("%H:%M")
        except (ValueError, TypeError):
            ts_str = "?"
        from_m = str(entry.get("from") or "?")
        to_m = str(entry.get("to") or "?")
        reason = str(entry.get("reason") or "?")
        lines.append(f"{ts_str} {from_m} → {to_m} ({reason})")
    return lines


async def handle_routes(bot: "KraabUserbot", message: Message) -> None:
    """!routes — детальное состояние routing chain (Wave 48-B).

    Показывает:
    - Primary + active модель (last_fallback_model если codex disabled)
    - Полную fallback chain с per-model status (✅ / ⏸ quota exhausted)
    - Recovery ETA для codex моделей (если disabled)
    - Последние 5 переключений из route_switches.jsonl
    """
    del bot

    from pyrogram.enums import ParseMode

    # Читаем runtime truth
    try:
        from ...core.openclaw_runtime_models import (
            get_runtime_fallback_models,
            get_runtime_primary_model,
        )

        primary = get_runtime_primary_model() or "—"
        fallbacks = list(get_runtime_fallback_models() or [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("routes_runtime_read_failed", error=str(exc))
        primary = "—"
        fallbacks = []

    quota_state = _read_codex_quota_state()
    chain_lines = _format_chain_state(primary, fallbacks, quota_state)

    # Активная модель = primary, если codex enabled, иначе last_fallback_model
    if quota_state.get("disabled") and quota_state.get("last_fallback_model"):
        active_model = str(quota_state.get("last_fallback_model"))
        active_note = " (fallback after quota)"
    else:
        active_model = primary
        active_note = ""

    # Recent switches
    try:
        from ...integrations.route_switch_log import read_recent

        switches = read_recent(limit=5)
    except Exception as exc:  # noqa: BLE001
        logger.debug("routes_switches_read_failed", error=str(exc))
        switches = []
    switch_lines = _format_recent_switches(switches)

    parts: list[str] = ["📡 *Routing State*", ""]
    parts.append(f"*Primary*: `{primary}`")
    parts.append(f"*Active*: `{active_model}`{active_note}")
    parts.append("")
    parts.append("*Fallback chain*:")
    if chain_lines:
        parts.extend(chain_lines)
    else:
        parts.append("  (chain пустая)")
    parts.append("")
    parts.append("*Recent switches* (last 5):")
    if switch_lines:
        parts.extend(switch_lines)
    else:
        parts.append("  (no recent switches)")

    await message.reply("\n".join(parts), parse_mode=ParseMode.MARKDOWN)


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


# ---------------------------------------------------------------------------
# Wave 52-E: !mcp — read-only MCP inventory + registered overview (owner-only)
# ---------------------------------------------------------------------------

_MCP_INVENTORY_PATH = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "mcp_inventory.toml"


def _load_mcp_inventory(path: pathlib.Path | None = None) -> dict[str, dict[str, Any]]:
    """Парсит scripts/mcp_inventory.toml. Возвращает {name: spec}."""
    import tomllib  # noqa: PLC0415

    target = path if path is not None else _ch_attr("_MCP_INVENTORY_PATH", _MCP_INVENTORY_PATH)
    try:
        with open(target, "rb") as fh:
            data = tomllib.load(fh) or {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.warning("mcp_inventory_load_failed", error=str(exc))
        return {}
    return {name: spec for name, spec in data.items() if isinstance(spec, dict)}


async def _get_registered_mcps(
    timeout: float = 5.0,
) -> tuple[dict[str, dict[str, Any]], str | None]:
    """Возвращает (registered, error). registered = {name: spec_from_openclaw}."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "openclaw",
            "mcp",
            "list",
            "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0:
            return {}, f"exit_code={proc.returncode}"
        text = stdout.decode(errors="replace").strip()
        if not text.startswith("{"):
            return {}, "non_json_output"
        # openclaw иногда печатает мусор после JSON — берём первый top-level объект.
        depth = 0
        end_idx = -1
        for idx, ch in enumerate(text):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end_idx = idx + 1
                    break
        json_part = text[:end_idx] if end_idx > 0 else text
        data = json.loads(json_part) or {}
        if not isinstance(data, dict):
            return {}, "unexpected_root_type"
        return ({name: spec for name, spec in data.items() if isinstance(spec, dict)}, None)
    except asyncio.TimeoutError:
        return {}, "timeout"
    except FileNotFoundError:
        return {}, "openclaw_cli_not_found"
    except json.JSONDecodeError as exc:
        return {}, f"json_decode_error: {str(exc)[:80]}"
    except Exception as exc:  # noqa: BLE001
        return {}, f"unknown_error: {str(exc)[:80]}"


def _check_env_var_present(var: str, env: dict[str, str] | None = None) -> bool:
    """True, если env-переменная задана и не пустая."""
    import os as _os  # noqa: PLC0415

    src = env if env is not None else _os.environ
    val = src.get(var, "")
    return bool(val and val.strip())


def _classify_mcp_status(name: str, spec: dict[str, Any], registered_names: set[str]) -> str:
    """active / deferred / deprecated / inactive."""
    raw_status = str(spec.get("status") or "").strip().lower()
    if raw_status == "deprecated":
        return "deprecated"
    if name in registered_names and raw_status != "deprecated":
        return "active"
    if raw_status == "deferred":
        return "deferred"
    required_env = spec.get("required_env") or []
    if isinstance(required_env, list) and required_env:
        missing = [v for v in required_env if not _check_env_var_present(str(v))]
        if missing:
            return "deferred"
    return "inactive"


def _format_mcp_summary(
    inventory: dict[str, dict[str, Any]],
    registered: dict[str, dict[str, Any]],
) -> str:
    """Markdown-сводка: Active / Deferred / Deprecated."""
    registered_names = set(registered.keys())
    inventory_names = set(inventory.keys())

    actives: list[tuple[str, dict[str, Any]]] = []
    deferreds: list[tuple[str, dict[str, Any]]] = []
    deprecateds: list[tuple[str, dict[str, Any]]] = []

    for name in sorted(registered_names | inventory_names):
        spec = inventory.get(name) or registered.get(name) or {}
        status = _classify_mcp_status(name, spec, registered_names)
        if status == "active":
            actives.append((name, spec))
        elif status == "deferred":
            deferreds.append((name, spec))
        elif status == "deprecated":
            deprecateds.append((name, spec))

    lines: list[str] = []
    lines.append(
        f"📡 **MCP Inventory** ({len(registered_names)} registered, "
        f"{len(inventory_names)} in registry)"
    )
    lines.append("")

    if actives:
        lines.append("**Active**:")
        for name, spec in actives:
            desc = str(spec.get("description") or "—")
            transport = str(
                spec.get("transport") or registered.get(name, {}).get("transport") or "stdio"
            )
            required_env = spec.get("required_env") or []
            key_marker = ""
            if isinstance(required_env, list) and required_env:
                missing = [v for v in required_env if not _check_env_var_present(str(v))]
                key_marker = " (key: ✓)" if not missing else " (key: ⚠️ missing)"
            lines.append(f"✅ `{name}` — {transport}, {desc}{key_marker}")
        lines.append("")

    if deferreds:
        lines.append("**Deferred** (pending tokens):")
        for name, spec in deferreds:
            required_env = spec.get("required_env") or []
            need = ", ".join(str(v) for v in required_env) if required_env else "—"
            lines.append(f"⏸ `{name}` — needs {need}")
        lines.append("")

    if deprecateds:
        lines.append("**Deprecated**:")
        for name, spec in deprecateds:
            note = str(spec.get("notes") or spec.get("description") or "")
            short = note.split(".")[0][:80] if note else "—"
            lines.append(f"❌ `{name}` — {short}")
        lines.append("")

    lines.append("Use `!mcp info <name>` для детали, `!mcp inventory` — полный список.")
    lines.append("Add: `python scripts/openclaw_mcp_register.py --add <name>` (CLI only).")
    return "\n".join(lines)


def _format_mcp_info(
    name: str,
    inventory: dict[str, dict[str, Any]],
    registered: dict[str, dict[str, Any]],
) -> str | None:
    """Детальная карточка одного MCP. None если нигде не найден."""
    spec = inventory.get(name)
    reg = registered.get(name)
    if spec is None and reg is None:
        return None
    spec = spec or {}
    reg = reg or {}

    transport = str(spec.get("transport") or reg.get("transport") or "stdio")
    desc = str(spec.get("description") or "—")
    notes = str(spec.get("notes") or "").strip()

    is_registered = name in registered
    raw_status = str(spec.get("status") or "").strip().lower()
    if raw_status == "deprecated":
        status_str = "❌ deprecated"
    elif is_registered:
        status_str = "✅ active"
    elif raw_status == "deferred":
        status_str = "⏸ deferred"
    else:
        status_str = "○ inactive"

    out: list[str] = [
        f"📡 **MCP `{name}`**",
        f"Status: {status_str}",
        f"Transport: `{transport}`",
        f"Description: {desc}",
    ]
    url = spec.get("url") or reg.get("url")
    if url:
        out.append(f"URL: `{url}`")
    cmd = spec.get("command") or reg.get("command")
    if cmd:
        out.append(f"Command: `{cmd}`")
    args = spec.get("args") or reg.get("args")
    if args and isinstance(args, list):
        joined = " ".join(str(a) for a in args)
        if len(joined) > 200:
            joined = joined[:200] + "…"
        out.append(f"Args: `{joined}`")

    required_env = spec.get("required_env") or []
    if isinstance(required_env, list) and required_env:
        out.append("Required env:")
        for var in required_env:
            present = _check_env_var_present(str(var))
            marker = "✅" if present else "❌"
            out.append(f"  {marker} `{var}`")

    if notes:
        out.append(f"Notes: {notes[:300]}{'…' if len(notes) > 300 else ''}")
    return "\n".join(out)


def _format_mcp_inventory_full(
    inventory: dict[str, dict[str, Any]],
    registered: dict[str, dict[str, Any]],
) -> str:
    """Полный реестр grouped by status."""
    registered_names = set(registered.keys())
    by_status: dict[str, list[str]] = {
        "active": [],
        "deferred": [],
        "deprecated": [],
        "inactive": [],
    }
    for name in sorted(set(inventory.keys()) | registered_names):
        spec = inventory.get(name) or registered.get(name) or {}
        status = _classify_mcp_status(name, spec, registered_names)
        by_status[status].append(name)

    lines: list[str] = ["📡 **Full MCP Registry**", ""]
    if by_status["active"]:
        lines.append(f"**Active ({len(by_status['active'])})**:")
        lines.extend(f"✅ `{n}`" for n in by_status["active"])
        lines.append("")
    if by_status["deferred"]:
        lines.append(f"**Deferred ({len(by_status['deferred'])})**:")
        lines.extend(f"⏸ `{n}`" for n in by_status["deferred"])
        lines.append("")
    if by_status["deprecated"]:
        lines.append(f"**Deprecated ({len(by_status['deprecated'])})**:")
        lines.extend(f"❌ `{n}`" for n in by_status["deprecated"])
        lines.append("")
    if by_status["inactive"]:
        lines.append(f"**Inactive ({len(by_status['inactive'])})**:")
        lines.extend(f"○ `{n}`" for n in by_status["inactive"])
    return "\n".join(lines).strip()


async def handle_mcp(bot: "KraabUserbot", message: Message) -> None:
    """!mcp — read-only обзор MCP-серверов и реестра (owner-only).

    Подкоманды:
    - `!mcp`               — list active MCPs со статусами;
    - `!mcp info <name>`   — детальная карточка;
    - `!mcp inventory`     — полный реестр (active + deferred + deprecated).

    Управление (add/remove) — только через
    `scripts/openclaw_mcp_register.py` (CLI).
    """
    from ...core.access_control import AccessLevel  # noqa: PLC0415

    access_profile = bot._get_access_profile(getattr(message, "from_user", None))
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 Команда `!mcp` доступна только владельцу.")

    raw = str(message.text or "").strip()
    parts = raw.split(maxsplit=2)
    sub = parts[1].strip().lower() if len(parts) > 1 else ""

    inventory = _load_mcp_inventory()
    registered, reg_error = await _get_registered_mcps()

    if sub == "info":
        if len(parts) < 3 or not parts[2].strip():
            raise UserInputError(user_message="📡 Формат: `!mcp info <name>`")
        target = parts[2].strip().split()[0]
        text = _format_mcp_info(target, inventory, registered)
        if text is None:
            await message.reply(f"📡 MCP `{target}` не найден ни в openclaw, ни в реестре.")
            return
        await message.reply(text)
        return

    if sub == "inventory":
        text = _format_mcp_inventory_full(inventory, registered)
        if reg_error:
            text += f"\n\n⚠️ openclaw cli: {reg_error} (показан только inventory.toml)."
        await message.reply(text)
        return

    text = _format_mcp_summary(inventory, registered)
    if reg_error:
        text += f"\n\n⚠️ openclaw cli: {reg_error} (fallback на inventory.toml)."
    await message.reply(text)


__all__ = [
    "_CHECKPOINTS_DIR",
    "_count_today_calls",
    "_estimate_session_tokens",
    "_format_chain_state",
    "_format_recent_switches",
    "_format_recovery_time",
    "_format_time_ago",
    "_probe_anthropic_vertex",
    "_probe_gemini_cli",
    "_probe_vertex_gemini",
    "_read_codex_quota_state",
    "handle_bookmark",
    "handle_context",
    "handle_inbox",
    "handle_memo",
    "handle_metrics",
    "handle_note",
    "handle_quota",
    "handle_routes",
    "handle_skills",
    "handle_watch",
    "handle_mcp",
    "handle_test",
    "_run_pytest_subset",
    "_resolve_pytest_bin",
    "_E2E_SMOKE_FILE",
    "_FULL_CI_PATHS",
    "_MAX_ARGS_TOKENS",
    "_KRAB_PROJECT_ROOT",
    "_PYTEST_CANDIDATES",
    "_load_mcp_inventory",
    "_get_registered_mcps",
    "_check_env_var_present",
    "_classify_mcp_status",
    "_format_mcp_summary",
    "_format_mcp_info",
    "_format_mcp_inventory_full",
    "_MCP_INVENTORY_PATH",
]


# ---------------------------------------------------------------------------
# !skills — Wave 55-D: SkillCurator pending improvements queue management
# ---------------------------------------------------------------------------

# Module-level imports from skill_curator exposed at module scope so tests
# can patch via `src.handlers.commands.observability_commands.<name>`.
from ...core.skill_curator import (  # noqa: E402
    PENDING_IMPROVEMENTS_PATH,
    _load_pending_queue,
    _save_pending_queue_atomic,
    list_pending_improvements,
)

# Short ID length shown to user (first 6 hex chars of entry_id suffix)
_SKILLS_ID_LEN = 6

# Live skill_curator directory: overlays are applied here via CuratorState
_SKILLS_LIVE_DIR_NAME = "curator"


def _short_id(entry_id: str) -> str:
    """Returns a short display ID from a full entry_id (e.g. 'coders-a1b2c3d4' → 'a1b2c3')."""
    # entry_id format: "<team>-<8-hex-uuid>"
    parts = entry_id.rsplit("-", 1)
    return parts[-1][:_SKILLS_ID_LEN] if len(parts) == 2 else entry_id[:_SKILLS_ID_LEN]


def _find_entry_by_short_id(
    queue: list[dict],
    short_id: str,
) -> dict | None:
    """Finds a queue entry whose entry_id ends with the given short_id (case-insensitive)."""
    low = short_id.lower()
    for entry in queue:
        eid = (entry.get("entry_id") or "").lower()
        if eid.endswith(low) or _short_id(entry.get("entry_id", "")).lower() == low:
            return entry
    return None


def _apply_pending_entry(
    entry: dict,
    *,
    backup_base: pathlib.Path | None = None,
) -> tuple[bool, str]:
    """Writes candidate prompt to live skill_curator overlay and removes entry from queue.

    Safety: creates a before-apply backup of the current live prompt BEFORE writing.

    Returns (ok, message).
    """
    from ...core.skill_curator import CURATOR_BASE_DIR  # noqa: PLC0415
    from ...core.skill_curator_state import CURATOR_STATE_PATH, CuratorState  # noqa: PLC0415

    team = (entry.get("team") or "").lower()
    candidate_prompt = entry.get("candidate_prompt") or ""
    entry_id = entry.get("entry_id", "?")

    if not team:
        return False, "entry missing team field"
    if not candidate_prompt.strip():
        return False, "entry has empty candidate_prompt"

    _backup_base = backup_base or (CURATOR_BASE_DIR / "prompts_archive" / team)
    _backup_base.mkdir(parents=True, exist_ok=True)

    # 1. Backup current live prompt BEFORE overwriting (rollback safety)
    try:
        state = CuratorState.load(CURATOR_STATE_PATH)
        overlay = state.get_overlay(team)
        current_live = (overlay or {}).get("prompt", "")
    except Exception as exc:  # noqa: BLE001
        logger.warning("skills_apply_state_read_failed", team=team, error=str(exc))
        current_live = ""

    import datetime as _dt

    ts_str = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%S")
    backup_path = _backup_base / f"before_apply_{ts_str}.md"
    try:
        backup_path.write_text(current_live or "", encoding="utf-8")
    except OSError as exc:
        return False, f"backup failed: {exc}"

    # 2. Write overlay via CuratorState (same path as skill_curator.apply_with_approval)
    try:
        state = CuratorState.load(CURATOR_STATE_PATH)
        overlay = state.get_overlay(team) or {}
        version = overlay.get("version", 0) + 1
        applied_at = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
        state.apply_overlay(
            team,
            {
                "prompt": candidate_prompt,
                "proposal_id": f"skills-queue-{entry_id}",
                "applied_at": applied_at,
                "version": version,
                "archive_path": str(backup_path),
            },
        )
        state.mark_apply(team)
        state.save_atomic(CURATOR_STATE_PATH)
    except Exception as exc:  # noqa: BLE001
        return False, f"state write failed: {exc}"

    # Invalidate overlay cache
    try:
        from ...core import swarm_team_prompts as _stp  # noqa: PLC0415

        _stp._invalidate_overlay_cache(team)
    except Exception:  # noqa: BLE001
        pass

    # 3. Remove applied entry from the pending queue
    try:
        queue = _load_pending_queue(PENDING_IMPROVEMENTS_PATH)
        queue = [e for e in queue if e.get("entry_id") != entry_id]
        _save_pending_queue_atomic(queue, PENDING_IMPROVEMENTS_PATH)
    except Exception as exc:  # noqa: BLE001
        logger.warning("skills_apply_queue_remove_failed", entry_id=entry_id, error=str(exc))

    logger.info(
        "skills_apply_done",
        team=team,
        entry_id=entry_id,
        version=version,
        backup=str(backup_path),
    )
    return True, f"applied (version={version}, backup={backup_path.name})"


async def handle_skills(bot: "KraabUserbot", message: Message) -> None:
    """!skills — управление очередью pending improvements Wave 53-A.

    Субкоманды:
      !skills                        — список всех pending improvements по командам
      !skills info <id>              — детали записи (промпт, delta_score, метаданные)
      !skills apply <id>             — применить запись к live skill_curator overlay
      !skills reject <id>            — удалить из очереди без применения
      !skills clear --confirm        — очистить всю очередь (требует --confirm)

    Owner-only.
    """
    from ...core.access_control import is_owner_user_id  # noqa: PLC0415

    user_id = getattr(getattr(message, "from_user", None), "id", None)
    if not is_owner_user_id(user_id or 0):
        await message.reply("Команда `!skills` доступна только владельцу.")
        return

    try:
        from ...core.command_registry import bump_command

        bump_command("skills")
    except Exception:  # noqa: BLE001
        pass

    raw = (message.text or "").strip()
    parts = raw.split(maxsplit=2)
    # parts[0] = "!skills", parts[1] = sub, parts[2] = arg
    sub = parts[1].strip().lower() if len(parts) > 1 else ""
    arg = parts[2].strip() if len(parts) > 2 else ""

    # ── list (default) ──────────────────────────────────────────────────────
    if not sub or sub in {"list", "ls"}:
        queue = list_pending_improvements()
        if not queue:
            await message.reply(
                "Skill Curator Queue\n\n_Очередь пуста — нет pending improvements._\n\n"
                "Добавить: запусти `!curator propose <team>` или настрой "
                "`KRAB_SKILL_CURATOR_WEEKLY_AUTO_PROPOSE=1`."
            )
            return

        # Группируем по team
        by_team: dict[str, list[dict]] = {}
        for entry in queue:
            t = entry.get("team") or "unknown"
            by_team.setdefault(t, []).append(entry)

        lines: list[str] = ["Skill Curator Queue\n"]
        for team_name in sorted(by_team.keys()):
            entries = by_team[team_name]
            lines.append(f"**{team_name}** ({len(entries)} pending):")
            for e in entries:
                sid = _short_id(e.get("entry_id", "?"))
                score = e.get("delta_score", 0.0)
                meta = e.get("metadata") or {}
                hint = (
                    str(meta.get("rationale") or meta.get("reason") or "")[:60].strip()
                    or "improvement queued"
                )
                lines.append(f"  `{sid}` delta +{score:.2f} — {hint}")
            lines.append("")

        lines.append(
            "Используй `!skills info <id>` для деталей,\n    `!skills apply <id>` чтобы применить."
        )
        await message.reply("\n".join(lines))
        return

    # ── info <id> ───────────────────────────────────────────────────────────
    if sub == "info":
        if not arg:
            await message.reply("Укажи id: `!skills info <id>`")
            return
        queue = _load_pending_queue(PENDING_IMPROVEMENTS_PATH)
        entry = _find_entry_by_short_id(queue, arg)
        if not entry:
            await message.reply(f"Запись `{arg}` не найдена в очереди.")
            return

        sid = _short_id(entry.get("entry_id", "?"))
        prompt_preview = (entry.get("candidate_prompt") or "")[:400].strip()
        if len(entry.get("candidate_prompt") or "") > 400:
            prompt_preview += "\n... (truncated)"
        meta = entry.get("metadata") or {}
        meta_str = "\n".join(f"  {k}: {v}" for k, v in meta.items()) if meta else "  —"
        lines = [
            f"Skill Curator Entry `{sid}`\n",
            f"- Team: `{entry.get('team', '?')}`",
            f"- delta_score: **{entry.get('delta_score', 0.0):.4f}**",
            f"- threshold: {entry.get('threshold', 0.15)}",
            f"- queued_at: {entry.get('queued_at', '?')[:16]}",
            f"- status: {entry.get('status', 'pending')}",
            f"\n**Metadata:**\n{meta_str}",
            f"\n**Candidate prompt preview:**\n```\n{prompt_preview}\n```",
            f"\nПрименить: `!skills apply {sid}`  |  Отклонить: `!skills reject {sid}`",
        ]
        await message.reply("\n".join(lines))
        return

    # ── apply <id> ──────────────────────────────────────────────────────────
    if sub == "apply":
        if not arg:
            await message.reply("Укажи id: `!skills apply <id>`")
            return
        queue = _load_pending_queue(PENDING_IMPROVEMENTS_PATH)
        entry = _find_entry_by_short_id(queue, arg)
        if not entry:
            await message.reply(f"Запись `{arg}` не найдена в очереди.")
            return

        sid = _short_id(entry.get("entry_id", "?"))
        await message.reply(f"Применяю `{sid}`…")
        try:
            ok, msg = _apply_pending_entry(entry)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "skills_apply_command_failed", entry_id=entry.get("entry_id"), error=str(exc)
            )
            await message.reply(f"Apply error: {exc}")
            return

        icon = "✅" if ok else "❌"
        await message.reply(f"{icon} `{sid}`: {msg}")
        return

    # ── reject <id> ─────────────────────────────────────────────────────────
    if sub == "reject":
        if not arg:
            await message.reply("Укажи id: `!skills reject <id>`")
            return
        queue = _load_pending_queue(PENDING_IMPROVEMENTS_PATH)
        entry = _find_entry_by_short_id(queue, arg)
        if not entry:
            await message.reply(f"Запись `{arg}` не найдена в очереди.")
            return

        sid = _short_id(entry.get("entry_id", "?"))
        eid = entry.get("entry_id")
        new_queue = [e for e in queue if e.get("entry_id") != eid]
        try:
            _save_pending_queue_atomic(new_queue, PENDING_IMPROVEMENTS_PATH)
        except Exception as exc:  # noqa: BLE001
            await message.reply(f"Reject error: {exc}")
            return

        logger.info("skills_reject_done", entry_id=eid, team=entry.get("team"))
        await message.reply(f"Запись `{sid}` удалена из очереди.")
        return

    # ── clear --confirm ──────────────────────────────────────────────────────
    if sub == "clear":
        if "--confirm" not in (arg or ""):
            # Show count without clearing
            queue = _load_pending_queue(PENDING_IMPROVEMENTS_PATH)
            count = len(queue)
            await message.reply(
                f"Очередь содержит **{count}** записей.\n"
                "Добавь `--confirm` для полной очистки:\n"
                "`!skills clear --confirm`"
            )
            return

        queue = _load_pending_queue(PENDING_IMPROVEMENTS_PATH)
        count = len(queue)
        try:
            _save_pending_queue_atomic([], PENDING_IMPROVEMENTS_PATH)
        except Exception as exc:  # noqa: BLE001
            await message.reply(f"Clear error: {exc}")
            return

        logger.info("skills_clear_done", cleared=count)
        await message.reply(f"Очередь очищена: удалено **{count}** записей.")
        return

    # ── unknown subcommand → help ────────────────────────────────────────────
    await message.reply(
        "Skill Curator — управление очередью pending improvements\n\n"
        "`!skills` — список всех pending (по командам)\n"
        "`!skills info <id>` — детали записи\n"
        "`!skills apply <id>` — применить к live overlay\n"
        "`!skills reject <id>` — удалить из очереди\n"
        "`!skills clear --confirm` — очистить очередь\n\n"
        "Записи добавляются автоматически при `auto_apply_if_threshold` (Wave 53-A)."
    )


# ---------------------------------------------------------------------------
# Wave 56-K: !test — on-demand pytest subset runner (owner-only)
# ---------------------------------------------------------------------------

import sys as _sys  # noqa: E402

_KRAB_PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent.parent

# Ordered list of candidate pytest executables (first that exists wins at runtime)
_PYTEST_CANDIDATES = [
    _KRAB_PROJECT_ROOT / "venv" / "bin" / "pytest",
    _KRAB_PROJECT_ROOT / ".venv_krab" / "bin" / "pytest",
]

# Default e2e smoke test path (Wave 53-C)
_E2E_SMOKE_FILE = "tests/integration/test_e2e_smoke_wave53c.py"

# CI subset: lightweight unit tests (fast, no live network)
_FULL_CI_PATHS = ["tests/unit/"]

# Hard-coded safety cap: reject CLI with 30+ arg tokens
_MAX_ARGS_TOKENS = 28


def _resolve_pytest_bin() -> list[str]:
    """Возвращает список аргументов для запуска pytest.

    Предпочитает бинарник из project venv; fallback на sys.executable -m pytest.
    """
    for candidate in _PYTEST_CANDIDATES:
        if candidate.exists():
            return [str(candidate)]
    return [_sys.executable, "-m", "pytest"]


async def _run_pytest_subset(
    pytest_args: list[str],
    *,
    timeout: int = 60,
) -> dict:
    """Запускает pytest в async subprocess с timeout.

    Args:
        pytest_args: аргументы после бинарника (пути, -k filters, etc.)
        timeout: максимальное время в секундах (hard cap 120s)

    Returns:
        dict: ok, duration_sec, passed, failed, errors, summary_text
    """
    from ...core.subprocess_env import clean_subprocess_env  # noqa: PLC0415

    _timeout = min(int(timeout), 120)
    _pytest_bin = _resolve_pytest_bin()

    cmd = _pytest_bin + ["--tb=no", "-q", "--no-header"] + pytest_args

    t0 = asyncio.get_event_loop().time()
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(  # safe: uses execfile-style, not shell
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(_KRAB_PROJECT_ROOT),
            env=clean_subprocess_env(),
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=_timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
            duration = asyncio.get_event_loop().time() - t0
            return {
                "ok": False,
                "duration_sec": round(duration, 2),
                "passed": 0,
                "failed": 0,
                "errors": 0,
                "summary_text": f"TIMEOUT after {_timeout}s — pytest killed",
            }
    except Exception as exc:  # noqa: BLE001
        duration = asyncio.get_event_loop().time() - t0
        return {
            "ok": False,
            "duration_sec": round(duration, 2),
            "passed": 0,
            "failed": 0,
            "errors": 0,
            "summary_text": f"subprocess error: {exc}",
        }

    duration = asyncio.get_event_loop().time() - t0
    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    combined = stdout + ("\n" + stderr if stderr.strip() else "")

    # Parse last summary line: "N passed in Xs" / "N failed, M passed in Xs" / etc.
    passed = failed = errors = 0
    summary_text = combined.strip().splitlines()[-1] if combined.strip() else ""

    import re as _re  # noqa: PLC0415

    _pass_m = _re.search(r"(\d+)\s+passed", summary_text)
    _fail_m = _re.search(r"(\d+)\s+failed", summary_text)
    _err_m = _re.search(r"(\d+)\s+error", summary_text)

    if _pass_m:
        passed = int(_pass_m.group(1))
    if _fail_m:
        failed = int(_fail_m.group(1))
    if _err_m:
        errors = int(_err_m.group(1))

    ok = proc.returncode == 0 and failed == 0 and errors == 0

    return {
        "ok": ok,
        "duration_sec": round(duration, 2),
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "summary_text": summary_text,
        "_stdout_tail": "\n".join(combined.strip().splitlines()[-5:]),
    }


async def handle_test(bot: "KraabUserbot", message: Message) -> None:
    """!test — on-demand pytest subset runner (owner-only, Wave 56-K).

    Использование:
      !test              — e2e smoke (tests/integration/test_e2e_smoke_wave53c.py)
      !test full         — unit subset (tests/unit/)
      !test <pattern>    — pytest -k "<pattern>" по всем тестам
    """
    from ...core.access_control import AccessLevel  # noqa: PLC0415

    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 `!test` доступен только владельцу.")

    raw = (message.text or "").strip()
    parts = raw.split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""

    # Safety: reject excessively long args
    if len(arg.split()) > _MAX_ARGS_TOKENS:
        raise UserInputError(
            user_message=f"❌ Слишком много аргументов (>{_MAX_ARGS_TOKENS}). Упрости запрос."
        )

    # Determine run mode
    if not arg:
        # Default: e2e smoke suite
        pytest_args = [_E2E_SMOKE_FILE]
        label = "e2e_smoke_wave53c"
    elif arg.lower() == "full":
        # CI subset: unit tests only (fast)
        pytest_args = _FULL_CI_PATHS
        label = "unit subset"
    else:
        # Pattern filter: run -k <pattern> across all tests
        pytest_args = ["-k", arg, "tests/"]
        label = f"pattern: {arg[:40]}"

    await message.reply(f"🧪 Запускаем тесты (`{label}`)…\nМакс. ожидание: 120s")

    result = await _run_pytest_subset(pytest_args, timeout=60)

    ok_icon = "✅" if result["ok"] else "❌"
    passed = result["passed"]
    failed = result["failed"]
    errors = result["errors"]
    duration = result["duration_sec"]
    tail = result.get("_stdout_tail", result["summary_text"])

    # Compact status line
    total = passed + failed + errors
    status_line = f"{ok_icon} {passed}/{total} passed | {failed} failed | {errors} errors"

    # Last lines from output (Telegram-safe code block)
    tail_lines = (
        "\n".join(f"  {ln}" for ln in tail.strip().splitlines()[-5:])
        if tail.strip()
        else "  (нет вывода)"
    )

    reply = (
        f"🧪 **Test Run** ({label})\n\n{status_line}\n⏱ {duration}s\n\n```\n{tail_lines}\n```\n\n"
    )

    if not arg:
        reply += "_Full subset: `!test full` | Filter: `!test <pattern>`_"
    elif arg.lower() == "full":
        reply += "_E2E smoke: `!test` | Filter: `!test <pattern>`_"
    else:
        reply += "_E2E smoke: `!test` | Full subset: `!test full`_"

    await message.reply(reply)


# ---------------------------------------------------------------------------
# !routing — управление политикой маршрутизации LM Studio vs Cloud (Wave 60-A)
# ---------------------------------------------------------------------------


async def handle_routing(bot: "KraabUserbot", message: Message) -> None:
    """
    Управление явной политикой маршрутизации между LM Studio (local) и облаком.

    Синтаксис (только для owner):
      !routing               — показать текущую матрицу + временные overrides
      !routing local <task>  — временно направлять <task> в local
      !routing cloud <task>  — временно направлять <task> в cloud
      !routing auto <task>   — сбросить override для <task> (вернуть к матрице)
      !routing log [N]       — последние N решений маршрутизации (default 10)

    Изменения временные: сбрасываются при рестарте Krab.
    Для постоянных изменений — редактировать ROUTING_POLICY в routing_policy.py.
    """
    from ...core.access_control import is_owner_user_id  # noqa: PLC0415
    from ...core.routing_policy import (  # noqa: PLC0415
        ROUTING_POLICY,
        clear_task_override,
        get_overrides,
        read_recent_decisions,
        set_task_override,
    )

    # Owner-only
    if not is_owner_user_id(message.from_user.id if message.from_user else 0):
        await message.reply("❌ Команда доступна только владельцу.")
        return

    raw = (message.text or "").strip()
    parts = raw.split(maxsplit=3)
    # parts[0] = "!routing", parts[1:] = subcommand

    if len(parts) < 2:
        # Показать текущую матрицу + overrides
        overrides = get_overrides()
        lines = ["📡 **Routing Policy (Wave 60-A)**", "─────────────────────────────────", ""]
        lines.append("**Матрица** (`task_type → backend`):")
        for task, backend in sorted(ROUTING_POLICY.items()):
            icon = "🏠" if backend == "local" else ("☁️" if backend == "cloud" else "⚡")
            override_mark = f" ← **override: {overrides[task]}**" if task in overrides else ""
            lines.append(f"  {icon} `{task}` → `{backend}`{override_mark}")

        if overrides:
            lines += ["", "**Временные overrides**:"]
            for task, backend in sorted(overrides.items()):
                lines.append(f"  🔧 `{task}` → `{backend}`")

        lines += [
            "",
            "**Команды:**",
            "`!routing local <task>` — направить в local",
            "`!routing cloud <task>` — направить в cloud",
            "`!routing auto <task>`  — сбросить override",
            "`!routing log [N]`      — последние решения",
        ]
        await message.reply("\n".join(lines))
        return

    sub = parts[1].strip().lower()

    # !routing log [N]
    if sub == "log":
        n = 10
        if len(parts) >= 3:
            try:
                n = int(parts[2])
            except ValueError:
                n = 10
        entries = read_recent_decisions(n)
        if not entries:
            await message.reply("📋 Лог решений пуст.")
            return
        lines = [f"📋 **Последние {len(entries)} решений маршрутизации**", ""]
        for e in entries:
            import datetime as _dt  # noqa: PLC0415

            ts_str = _dt.datetime.fromtimestamp(e.get("ts", 0)).strftime("%H:%M:%S")
            backend = e.get("backend", "?")
            icon = "🏠" if backend == "local" else "☁️"
            lines.append(
                f"{icon} `{ts_str}` **{e.get('task_type', '?')}** → `{backend}`"
                f"\n   _{e.get('reason', '')[:60]}_"
            )
        await message.reply("\n".join(lines))
        return

    # !routing local/cloud/auto <task>
    if sub in ("local", "cloud", "auto") and len(parts) >= 3:
        task_type = parts[2].strip()
        if sub == "auto":
            clear_task_override(task_type)
            await message.reply(
                f"🔄 Override сброшен для `{task_type}`.\n"
                f"Матрица: `{ROUTING_POLICY.get(task_type, 'auto')}`"
            )
        else:
            try:
                set_task_override(task_type, sub)
            except ValueError as exc:
                await message.reply(f"❌ {exc}")
                return
            icon = "🏠" if sub == "local" else "☁️"
            await message.reply(
                f"{icon} Override установлен: `{task_type}` → `{sub}`\n"
                f"Сброс: `!routing auto {task_type}`"
            )
        return

    # Неизвестная подкоманда
    await message.reply(
        "❓ Неизвестная подкоманда. Используйте:\n"
        "`!routing` — показать матрицу\n"
        "`!routing local/cloud/auto <task>` — override\n"
        "`!routing log [N]` — лог решений"
    )
