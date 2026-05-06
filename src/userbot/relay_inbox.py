# -*- coding: utf-8 -*-
"""Wave 31-F: RelayInboxMixin — выделяет inbox/relay-логику из userbot_bridge.

Зачем:
- bridge до Wave 31-F содержал ~6046 LOC (после 31-E), inbox/relay блок —
  cohesive 7 методов, отвечающих за: capture incoming owner traffic,
  acknowledge open relay items, escalate relay to owner, forward guest
  incoming traffic.
- Mixin использует только: ``self.client``, ``self._owner_notify_target``,
  ``self._message_ids_from_delivery`` — все остаются на bridge как
  cross-cutting transport-helpers.

Контракт:
- ``_record_incoming_reply_to_inbox`` — фиксирует outcome ранее захваченного
  request'а (transport-time, не задним числом).
- ``_should_capture_incoming_owner_item`` — staticmethod, decide-only logic.
- ``_acknowledge_open_relay_requests_for_chat`` — закрывает open relay items
  при возвращении владельца в чат.
- ``_sync_incoming_message_to_inbox`` — публикует directed owner messages в
  inbox (deferred decision через ``_should_capture_incoming_owner_item``).
- ``_detect_relay_intent`` — staticmethod, regex-free keyword match.
- ``_escalate_relay_to_owner`` — inbox + Saved Messages notification.
- ``_forward_guest_incoming_to_owner`` — full notification для незнакомых
  контактов (sender + chat + Krab response excerpt).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from ..core.inbox_service import inbox_service
from ..core.operator_identity import build_trace_id

if TYPE_CHECKING:
    from pyrogram import Client
    from pyrogram.types import Message

logger = structlog.get_logger("Krab.userbot.relay_inbox")


# ─── Module-level константы ──────────────────────────────────────────────────
# Зачем здесь, а не на mixin'е: используется и в bridge (обратная совместимость
# через re-export), и в llm_flow (комментарий-ссылка), и в тестах
# `tests/unit/test_userbot_relay_intent.py`. Frozen — для thread-safety и hashability.
_RELAY_INTENT_KEYWORDS: frozenset[str] = frozenset(
    {
        "передай",
        "передайте",
        "передать",
        "перешли",
        "переслать",
        "скажи",
        "скажите",
        "сообщи",
        "сообщите",
        "расскажи",
        "расскажите",
        "передайте ему",
        "передай ему",
        "напомни",
        "напомните",
        "напоминание",
        "запомни",
        "запомните",
        "запомнить",
        "хозяину",
        "хозяин",
        "хозяином",
        "владельцу",
        "владелец",
        "let know",
        "tell him",
        "tell her",
        "notify",
        "pass along",
        "pass it on",
        "tell pablo",
        "tell the owner",
    }
)


class RelayInboxMixin:
    """Mixin: inbox capture/sync + relay intent detection/escalation."""

    # Атрибуты, которые ожидаются на host-классе (KraabUserbot):
    client: "Client | None"

    # Объявления (host-bridge определяет реальные методы):
    _owner_notify_target: int | str
    _message_ids_from_delivery: Any  # callable

    # ─── Inbox sync ──────────────────────────────────────────────────────────

    def _record_incoming_reply_to_inbox(
        self,
        *,
        incoming_item_result: dict[str, Any] | None,
        response_text: str,
        delivery_result: dict[str, Any] | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """
        Фиксирует outcome для ранее захваченного owner request.

        Важно не гадать по Telegram-логам задним числом: если ответ уже доставлен,
        transport-слой обязан сразу отметить это в persisted inbox.
        """
        if not isinstance(incoming_item_result, dict) or not incoming_item_result.get("ok"):
            return {"ok": False, "skipped": True, "reason": "incoming_item_missing"}
        item = incoming_item_result.get("item")
        metadata = item.get("metadata") if isinstance(item, dict) else {}
        if not isinstance(metadata, dict):
            return {"ok": False, "skipped": True, "reason": "incoming_item_metadata_missing"}
        chat_id = str(metadata.get("chat_id") or "").strip()
        message_id = str(metadata.get("message_id") or "").strip()
        if not chat_id or not message_id:
            return {"ok": False, "skipped": True, "reason": "incoming_item_identity_incomplete"}
        return inbox_service.record_incoming_owner_reply(
            chat_id=chat_id,
            message_id=message_id,
            response_text=response_text,
            delivery_mode=str((delivery_result or {}).get("delivery_mode") or "text")
            .strip()
            .lower()
            or "text",
            reply_message_ids=self._message_ids_from_delivery(delivery_result),
            actor="kraab",
            note=note,
        )

    @staticmethod
    def _should_capture_incoming_owner_item(
        *,
        is_self: bool,
        is_allowed_sender: bool,
        chat_type: object,
        is_reply_to_me: bool,
        has_trigger: bool,
        has_photo: bool,
        has_audio: bool,
        query: str,
    ) -> bool:
        """
        Решает, надо ли складывать входящее сообщение в owner inbox.

        Нам важно не превратить inbox в лог вообще всех сообщений, поэтому
        берём только directed owner traffic:
        - доверенный private chat;
        - trusted group mention/reply;
        - сообщения с вложением, явно адресованные userbot-контуру.
        """
        if is_self or not is_allowed_sender:
            return False
        normalized_chat_type = str(getattr(chat_type, "value", chat_type) or "").strip().lower()
        if normalized_chat_type == "private":
            return bool(str(query or "").strip() or has_photo or has_audio)
        if not (is_reply_to_me or has_trigger):
            return False
        return bool(str(query or "").strip() or has_photo or has_audio or is_reply_to_me)

    def _acknowledge_open_relay_requests_for_chat(
        self,
        *,
        chat_id: str,
        actor: str = "kraab",
        note: str = "owner_followed_up_after_relay",
    ) -> dict[str, Any]:
        """
        Закрывает открытые relay_request для чата, если владелец уже вернулся в диалог.

        Почему это нужно:
        - relay item создаётся как owner-visible напоминание о том, что в чате был
          запрос "передай/сообщи";
        - если затем owner уже пишет в этот же чат, старый relay долг больше не
          отражает реальное состояние и начинает захламлять inbox summary;
        - закрываем только open/acked relay_request с совпадающим `chat_id`.
        """
        normalized_chat_id = str(chat_id or "").strip()
        if not normalized_chat_id:
            return {"ok": False, "skipped": True, "reason": "chat_id_missing"}

        matched_item_ids: list[str] = []
        for item in inbox_service.list_items(status="open", kind="relay_request", limit=100):
            metadata = item.get("metadata") or {}
            if str(metadata.get("chat_id") or "").strip() != normalized_chat_id:
                continue
            item_id = str(item.get("item_id") or "").strip()
            if item_id:
                matched_item_ids.append(item_id)

        if not matched_item_ids:
            return {"ok": True, "updated_count": 0, "item_ids": []}

        result = inbox_service.bulk_update_status(
            item_ids=matched_item_ids,
            status="done",
            actor=actor,
            note=note,
        )
        result["updated_count"] = int(result.get("success_count") or 0)
        result["item_ids"] = matched_item_ids
        return result

    def _sync_incoming_message_to_inbox(
        self,
        *,
        message: "Message",
        user: Any,
        query: str,
        is_self: bool,
        is_allowed_sender: bool,
        has_trigger: bool,
        is_reply_to_me: bool,
        has_audio_message: bool,
    ) -> dict[str, Any]:
        """
        Публикует directed owner messages в persisted inbox.

        Почему это живёт в userbot_bridge:
        - именно здесь у нас есть truthful signal о том, что сообщение реально
          адресовано userbot-контуру, а не просто проходит мимо в группе;
        - storage и summary остаются в inbox_service, bridge только решает
          capture/no-capture на transport-слое.
        """
        if not self._should_capture_incoming_owner_item(
            is_self=is_self,
            is_allowed_sender=is_allowed_sender,
            chat_type=getattr(getattr(message, "chat", None), "type", ""),
            is_reply_to_me=is_reply_to_me,
            has_trigger=has_trigger,
            has_photo=bool(getattr(message, "photo", None)),
            has_audio=bool(has_audio_message),
            query=query,
        ):
            return {"ok": False, "skipped": True, "reason": "not_directed_owner_traffic"}
        chat_obj = getattr(message, "chat", None)
        chat_type = getattr(chat_obj, "type", "")
        normalized_chat_type = str(getattr(chat_type, "value", chat_type) or "").strip().lower()
        result = inbox_service.upsert_incoming_owner_request(
            chat_id=str(getattr(chat_obj, "id", "") or ""),
            message_id=str(getattr(message, "id", "") or ""),
            text=str(query or "").strip(),
            sender_id=str(getattr(user, "id", "") or ""),
            sender_username=str(getattr(user, "username", "") or ""),
            chat_type=normalized_chat_type,
            is_reply_to_me=bool(is_reply_to_me),
            has_trigger=bool(has_trigger),
            has_photo=bool(getattr(message, "photo", None)),
            has_audio=bool(has_audio_message),
        )
        try:
            self._acknowledge_open_relay_requests_for_chat(
                chat_id=str(getattr(chat_obj, "id", "") or ""),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "relay_request_auto_ack_failed",
                chat_id=str(getattr(chat_obj, "id", "") or ""),
                error=str(exc),
            )
        return result

    # ─── Relay detection / escalation ────────────────────────────────────────

    @staticmethod
    def _detect_relay_intent(query: str) -> bool:
        """
        Детектирует намерение передать сообщение владельцу.

        Зачем детерминированный keyword-match, а не LLM:
        - LLM уже обещает передать, но без side-effect;
        - нужна надёжная точка срабатывания независимо от формулировки ответа модели;
        - false-positives лучше чем missed relay — inbox потом закроет владелец.
        """
        normalized = str(query or "").lower()
        return any(kw in normalized for kw in _RELAY_INTENT_KEYWORDS)

    async def _escalate_relay_to_owner(
        self,
        *,
        message: "Message",
        user: Any,
        query: str,
        chat_type: str,
    ) -> None:
        """
        Фиксирует relay-запрос в inbox и уведомляет владельца в Saved Messages.

        Почему Saved Messages (send to self):
        - userbot является аккаунтом владельца, поэтому отправка себе = уведомление;
        - это надёжнее любого бота/вебхука и работает без дополнительных токенов;
        - владелец увидит уведомление через обычный Telegram.
        """
        sender_display = (
            f"@{user.username}"
            if getattr(user, "username", None)
            else f"id:{getattr(user, 'id', '?')}"
        )
        chat_id_str = str(getattr(getattr(message, "chat", None), "id", "") or "")
        message_id_str = str(getattr(message, "id", "") or "")
        excerpt = str(query or "")[:1500]

        try:
            inbox_service.upsert_item(
                dedupe_key=f"relay:{chat_id_str}:{message_id_str}",
                kind="relay_request",
                source="telegram-userbot",
                title=f"📨 Relay от {sender_display}",
                body=(
                    f"Чат: `{chat_id_str}`\nОт: `{sender_display}`\nТип: `{chat_type}`\n\n"
                    f"Сообщение:\n{excerpt}"
                ),
                severity="warning",
                status="open",
                identity=inbox_service.build_identity(
                    channel_id=chat_id_str,
                    team_id="owner",
                    trace_id=build_trace_id("relay", chat_id_str, message_id_str),
                    approval_scope="owner",
                ),
                metadata={
                    "chat_id": chat_id_str,
                    "message_id": message_id_str,
                    "sender_id": str(getattr(user, "id", "") or ""),
                    "sender_username": str(getattr(user, "username", "") or ""),
                    "chat_type": chat_type,
                    "relay_text": excerpt,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("relay_inbox_escalation_failed", error=str(exc))

        try:
            me = await self.client.get_me()
            notification = (
                f"📨 **Relay-запрос**\n\n"
                f"От: `{sender_display}`\n"
                f"Чат: `{chat_id_str}` ({chat_type})\n\n"
                f"**Сообщение:**\n{excerpt[:800]}"
            )
            sent_message = await self.client.send_message(self._owner_notify_target, notification)
            try:
                inbox_service.record_relay_delivery(
                    chat_id=chat_id_str,
                    message_id=message_id_str,
                    notification_text=notification,
                    delivery_mode="saved_messages",
                    delivered_to_chat_id=str(getattr(me, "id", "") or ""),
                    relay_message_ids=[str(getattr(sent_message, "id", "") or "")],
                    actor="kraab",
                    note="relay_owner_notified",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("relay_inbox_resolution_failed", error=str(exc))
            logger.info(
                "relay_owner_notified",
                sender=sender_display,
                chat_id=chat_id_str,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("relay_owner_notification_failed", error=str(exc))

    async def _forward_guest_incoming_to_owner(
        self,
        *,
        message: "Message",
        query: str,
        krab_response: str,
    ) -> None:
        """
        Форвардит входящее сообщение от незнакомого контакта (GUEST) owner-у.

        Почему нужно: аптека пишет 'препараты приехали' → Краб отвечает от лица
        пользователя, но owner не знает об этом. Это решает проблему пропущенных
        входящих от незнакомых контактов.
        """
        try:
            user = getattr(message, "from_user", None) or getattr(message, "sender_chat", None)
            fname = str(getattr(user, "first_name", "") or "").strip()
            lname = str(getattr(user, "last_name", "") or "").strip()
            username = str(getattr(user, "username", "") or "").strip()
            sender_name = f"{fname} {lname}".strip() or ""
            if username:
                sender_name = (
                    f"{sender_name} (@{username})".strip() if sender_name else f"@{username}"
                )
            if not sender_name:
                sender_name = f"id:{getattr(user, 'id', '?')}"

            chat_id_str = str(getattr(getattr(message, "chat", None), "id", "") or "")
            excerpt = str(query or "")[:1500]
            response_excerpt = str(krab_response or "")[:400]

            notification = (
                f"📩 **Незнакомый контакт написал**\n\n"
                f"От: `{sender_name}`\n"
                f"Чат: `{chat_id_str}`\n\n"
                f"**Сообщение:**\n{excerpt}\n\n"
                f"↩️ **Краб ответил:**\n{response_excerpt}"
            )
            await self.client.send_message(self._owner_notify_target, notification)
            logger.info(
                "guest_incoming_forwarded_to_owner",
                sender=sender_name,
                chat_id=chat_id_str,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("guest_incoming_forward_failed", error=str(exc))
