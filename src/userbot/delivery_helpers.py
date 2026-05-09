# -*- coding: utf-8 -*-
"""Wave 31-M: DeliveryHelpersMixin — финальная доставка ответов в Telegram.

Зачем:
- bridge до 31-M содержал ~4597 LOC, delivery cluster ~250 LOC,
  cohesive: split + edit/reply/send_message routing, voice placeholder,
  smart trigger feedback recording, autodel scheduling, query normalization.
- Mixin использует: ``self.client``, ``self._split_message``,
  ``self._safe_edit``, ``self._safe_reply_or_send_new``
  (TelegramSendUtilsMixin), ``self._should_send_full_text_reply``,
  ``self._should_send_voice_reply`` (VoiceProfileMixin),
  ``self._pending_smart_trigger`` (bridge state).

Контракт:
- ``_should_force_cloud_for_photo_route`` — staticmethod, photo+config gate
- ``_deliver_response_parts`` — основная delivery point с 3 routes:
  * placeholder_only (voice-only mode)
  * edit_and_reply (default text path)
  * send_message (voice/background/force_new)
- ``_maybe_record_smart_trigger_response`` — feedback tracker write для
  smart routing decision tracing
- ``_maybe_schedule_autodel`` — chat-level autodel scheduling
- ``_message_ids_from_delivery`` — staticmethod, delivery summary parser
- ``_build_effective_user_query`` — staticmethod, query normalization
  с reply_context + sender prefix для group chats
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any

import structlog

from ..config import config
from ..core.repetition_guard import repetition_guard as _repetition_guard
from ._send_queue import telegram_send_queue as _telegram_send_queue
from .llm_text_processing import _append_model_footer

if TYPE_CHECKING:
    from pyrogram import Client
    from pyrogram.types import Message

logger = structlog.get_logger("Krab.userbot.delivery_helpers")


# Wave 37-B: anaphora detection — 3rd-person pronouns RU/EN.
# Используется для:
# 1. Reply target redirect (Issue 1): user replies на X + "спроси его" →
#    Krab отвечает на X, не на trigger.
# 2. Prompt context hint (Issue 3): подсказать LLM что местоимения
#    относятся к автору referenced message.
# Word boundaries предотвращают false positives ("например" не match'ит "ему",
# "немец" не match'ит "ему", "гей" не match'ит "ей").
_ANAPHORA_RE = re.compile(
    r"(?<![а-яёА-ЯЁa-zA-Z])"  # негативный lookbehind: не после буквы
    r"(его|ему|него|нему|её|ей|неё|ней|ним|него|нею|him|her|his|hers)"
    r"(?![а-яёА-ЯЁa-zA-Z])",  # негативный lookahead: не перед буквой
    re.IGNORECASE,
)


def _query_has_anaphora(query: str | None) -> bool:
    """True если query содержит anaphora-маркеры (3rd-person pronouns RU/EN).

    Используется в delivery (reply target redirect) + prompt build (LLM hint).
    Word-boundary regex чтобы не ловить substring совпадения.
    """
    if not query:
        return False
    return bool(_ANAPHORA_RE.search(query))


def _resolve_reply_target(source_message: "Message", query: str | None) -> "Message":
    """Wave 37-B (P1-3): возвращает на какое сообщение Krab должен делать reply.

    Если user написал "Краб, спроси его..." в reply на сообщение от X, то
    Krab должен направить ответ на X (referenced), а не на trigger user'а.
    Это исправляет визуальное искажение в Telegram UI: reply attached к
    автору цитируемого сообщения, как и ожидает пользователь.

    Без anaphora — fallback на source_message (default behavior).
    Без referenced — fallback на source_message (нечего redirect'ить).
    """
    referenced = getattr(source_message, "reply_to_message", None)
    if referenced and _query_has_anaphora(query):
        return referenced
    return source_message


# Wave 39-X: output-based reply target redirect.
#
# Wave 37-B (anaphora-based) — узкий proxy. Реальный сигнал кому Krab
# адресует — это сам outgoing text. LLM умный, начинает ответ с
# "🐶, ..." / "@user, ..." когда хочет address кого-то конкретно.
# Helper парсит начало text и matches против referenced.from_user.
#
# Regression case (09.05.2026 02:13-02:14 в YMB FAMILY FOREVER):
# - pavua reply на 🐶's join: "Краб, поправил, попробуй снова" (НЕТ anaphora)
# - LLM выдал: "🐶, контрольный пинг 🦀 ..." (правильно адресует 🐶)
# - Wave 37-B regex miss → reply_target = pavua (wrong)
# - Wave 39-X: парсит "🐶," → matches first_name="🐶" → reply_target = 🐶 message ✓


_REPLY_TARGET_BOUNDARY = {",", ":", " ", "!", "?", ".", ";", ")", "—", "-", "\n", "\t"}


def _output_starts_with_addressee(
    response_head: str, *, user_id: int | None, username: str, first_name: str
) -> bool:
    """Wave 39-X helper: True если response_head начинается с addressee
    matching user (markdown link / @username / first_name).

    Word-boundary protection: candidate должен быть followed by
    punctuation/space/newline (избегаем 'Ан' в 'Антон').
    """
    if not response_head:
        return False

    candidates: list[str] = []
    if user_id is not None:
        # Markdown link с user_id (приоритетный — explicit)
        # Не знаем text внутри [...], проверяем только по user_id
        marker = f"](tg://user?id={user_id})"
        if marker in response_head[:200]:  # within first 200 chars
            # Проверим что link находится В НАЧАЛЕ (через regex match вместо
            # просто `in`). Для simplicity допускаем offset до 5 leading whitespace.
            stripped = response_head.lstrip()
            if stripped.startswith("[") and marker in stripped[: stripped.find(")") + 1]:
                return True

    if username:
        candidates.append(f"@{username}")
    if first_name:
        candidates.append(first_name)

    head = response_head.lstrip()
    for cand in candidates:
        if not cand or not head.startswith(cand):
            continue
        next_pos = len(cand)
        if next_pos >= len(head):
            return True  # candidate == whole head
        if head[next_pos] in _REPLY_TARGET_BOUNDARY:
            return True
    return False


def _resolve_reply_target_from_output(
    source_message: "Message",
    response_text: str | None,
    *,
    fallback_query: str | None = None,
) -> "Message":
    """Wave 39-X: primary reply target resolution by parsing Krab's outgoing text.

    Если response_text начинается с addressee (markdown link, @username,
    или first_name) и тот addressee matches referenced.from_user —
    redirect reply target на referenced. Иначе — fallback на Wave 37-B
    anaphora-based resolution.

    Args:
        source_message: incoming user message (trigger).
        response_text: full LLM response text Krab'а до отправки.
        fallback_query: original user query для anaphora fallback.

    Returns:
        Message на которое нужно делать reply. Если redirect не сработал —
        source_message (default).
    """
    referenced = getattr(source_message, "reply_to_message", None)
    if referenced is None:
        return _resolve_reply_target(source_message, fallback_query)

    referenced_user = getattr(referenced, "from_user", None)
    if referenced_user is None:
        return _resolve_reply_target(source_message, fallback_query)

    if not response_text:
        return _resolve_reply_target(source_message, fallback_query)

    user_id = getattr(referenced_user, "id", None)
    username = (getattr(referenced_user, "username", "") or "").strip()
    first_name = (getattr(referenced_user, "first_name", "") or "").strip()

    head = response_text[:200]  # take only начало для match
    if _output_starts_with_addressee(
        head, user_id=user_id, username=username, first_name=first_name
    ):
        return referenced

    return _resolve_reply_target(source_message, fallback_query)


# Wave 38: inline mention link для users без @username.
# Issue: 09.05.2026 в YMB FAMILY FOREVER user "🐶" (без @username) был адресатом
# Krab'а ответа. Krab правильно ставил "🐶, ..." в начале text, но это plain text,
# не clickable. Tag должен указывать на user_id чтобы Telegram UI делал mention
# navigable. Markdown syntax `[name](tg://user?id=N)` рендерится Pyrofork как
# inline mention.
def _inject_user_mention_link(text: str | None, user: object) -> str | None:
    """Wave 38: заменяет первое вхождение display name юзера на markdown
    inline mention `[name](tg://user?id=N)`.

    Применяется в delivery когда reply target redirect сработал (Wave 37-B):
    Krab отвечает на referenced message, и хочется чтобы text mention был
    clickable. Особенно важно для users без @username (emoji nickname,
    private accounts).

    Behavior:
    - Replace ТОЛЬКО при name в начале text (избегаем false positives внутри).
    - Word-boundary check: name должно быть отдельным token (followed by
      `,`/`:`/` `/конец) — иначе substring-match (`Ан` в `Антон`) ошибочно
      сработает.
    - Идемпотентность: если `tg://user?id={user_id}` уже в text — skip.
    - Priority: `@username` > `first_name`.
    - Без user.id или без name candidate — text unchanged.
    """
    if text is None or not text:
        return text
    if user is None:
        return text

    user_id = getattr(user, "id", None)
    if not user_id:
        return text

    # Idempotency: если уже linked — оставляем
    marker = f"tg://user?id={user_id}"
    if marker in text:
        return text

    username = (getattr(user, "username", "") or "").strip()
    first_name = (getattr(user, "first_name", "") or "").strip()

    # Build candidates в priority order
    candidates: list[str] = []
    if username:
        candidates.append(f"@{username}")
    if first_name:
        candidates.append(first_name)

    if not candidates:
        return text

    # Word boundary chars — после которых считается end of name token
    _boundary = {",", ":", " ", "!", "?", ".", ";", ")", "—", "-"}

    for cand in candidates:
        if not text.startswith(cand):
            continue
        # Защита от substring match: следующий char должен быть boundary или
        # конец строки (иначе "Ан" в "Антон" сработает ошибочно).
        next_pos = len(cand)
        if next_pos < len(text):
            next_char = text[next_pos]
            if next_char not in _boundary:
                continue
        # Replace
        suffix = text[len(cand) :]
        return f"[{cand}]({marker}){suffix}"

    return text


class DeliveryHelpersMixin:
    """Mixin: response delivery + post-delivery side-effects."""

    # Атрибуты, которые ожидаются на host-классе:
    client: "Client | None"
    _pending_smart_trigger: dict[str, Any]

    def _apply_model_footer(self, text: str) -> str:
        """Wave 47-B: добавляет к ответу `📡 _<model>_` footer.

        Берёт текущий route из `openclaw_client.get_last_runtime_route()` и
        определяет fallback по `_cloud_tier_state.last_recovery_action`.
        Idempotent + защищён от error/empty responses внутри `_append_model_footer`.
        """
        try:
            from ..openclaw_client import openclaw_client as _oc
        except ImportError:
            return text
        try:
            route = _oc.get_last_runtime_route() or {}
        except Exception:  # noqa: BLE001
            return text

        model = str(route.get("model") or "").strip()
        if not model:
            return text

        # Определяем был ли fallback. _cloud_tier_state.last_recovery_action
        # содержит маркеры типа `switch_to_cloud_quality_retry`,
        # `switch_to_cloud_retry`, `switch_to_local`, `switch_to_paid` и т.д.
        # Также Wave 47-A: chain advance via `switch_to_cloud_quality_retry`.
        fallback_used = False
        fallback_reason = ""
        try:
            tier_state = getattr(_oc, "_cloud_tier_state", {}) or {}
            recovery = str(tier_state.get("last_recovery_action") or "").strip()
            last_error = str(tier_state.get("last_error_code") or "").strip()
        except Exception:  # noqa: BLE001
            recovery = ""
            last_error = ""

        if recovery and recovery not in {"none", ""}:
            if "quality" in recovery:
                fallback_used = True
                fallback_reason = "сбоя primary"
            elif "cloud_retry" in recovery or recovery.startswith("switch_to_cloud"):
                fallback_used = True
                if last_error == "quota_exceeded":
                    fallback_reason = "quota"
                else:
                    fallback_reason = "cloud retry"
            elif recovery.startswith("switch_to_local"):
                fallback_used = True
                fallback_reason = "cloud failure"
            elif recovery.startswith("switch_to_paid"):
                fallback_used = True
                fallback_reason = "free quota"

        return _append_model_footer(
            text,
            model,
            fallback_used=fallback_used,
            fallback_reason=fallback_reason,
        )

    @staticmethod
    def _should_force_cloud_for_photo_route(*, has_images: bool) -> bool:
        """
        Жёстко уводит фото userbot в cloud по умолчанию.

        Почему это нужно:
        - пользователь не ждёт, что текстовый Nemotron будет выгружен ради
          случайной маленькой VL-модели;
        - для userbot важнее предсказуемая доставка и язык ответа, чем локальный
          vision-эксперимент с автопереключением.
        Локальный vision остаётся только как явный opt-in через конфиг.
        """
        if not has_images:
            return False
        if not bool(getattr(config, "USERBOT_FORCE_CLOUD_FOR_PHOTO", True)):
            return False
        return True

    async def _deliver_response_parts(
        self,
        *,
        source_message: "Message",
        temp_message: "Message",
        is_self: bool,
        query: str,
        full_response: str,
        prefer_send_message_for_background: bool = False,
        force_new_message: bool = False,
    ) -> dict[str, Any]:
        """
        Доставляет готовый ответ в Telegram с безопасным split.

        Почему отдельный helper:
        - capability/status fast-path должен использовать ту же доставку, что и
          обычный AI-ответ;
        - так не дублируем логику split/edit/reply в нескольких ветках.
        """
        if not self._should_send_full_text_reply():
            placeholder = (
                "🦀 Голосовой ответ отправлен. Если нужен текстовый дубль,"
                " переключи `!voice delivery text+voice`."
            )
            if is_self:
                updated = await self._safe_edit(source_message, placeholder)
                return {
                    "delivery_mode": "placeholder_only",
                    "text_message_ids": [str(getattr(updated, "id", "") or "")]
                    if getattr(updated, "id", None)
                    else [],
                    "parts_count": 1,
                }
            # Bug 4 guard: temp_message может совпадать с входящим чужим сообщением
            # (в группах при is_self=False и _show_progress_notices=False). Edit чужого
            # сообщения вернёт 403 MESSAGE_AUTHOR_REQUIRED, поэтому отвечаем reply'ем.
            if temp_message is source_message:
                updated = await self._safe_reply_or_send_new(source_message, placeholder)
            else:
                updated = await self._safe_edit(temp_message, placeholder)
            return {
                "delivery_mode": "placeholder_only",
                "text_message_ids": [str(getattr(updated, "id", "") or "")]
                if getattr(updated, "id", None)
                else [],
                "parts_count": 1,
            }

        # Wave 39-A: repetition guard — проверяем перед отправкой.
        # Если Krab уже недавно отвечал похоже в этом чате — сигнализируем
        # кратким уведомлением вместо полного повтора.
        # Дизайн-выбор: prepend «(повторяюсь)» к первой части, а не suppress
        # полностью — пользователь видит что guard сработал и может запросить
        # другой ответ. Silent suppress скрыл бы проблему.
        _chat_id_for_guard = source_message.chat.id
        _delivery_text = full_response  # текст для record после отправки
        if _repetition_guard.is_repetition(_chat_id_for_guard, full_response):
            logger.warning(
                "repetition_suppressed",
                chat_id=_chat_id_for_guard,
                text_preview=full_response[:80],
            )
            full_response = "🦀 Уже сказал близко по теме чуть выше."

        # Wave 47-B: model footer — добавляем `📡 _<model>_` к ответу, чтобы
        # пользователь видел какая модель ответила. Особенно важно для
        # debugging fallback chain (codex quota → gemini → vertex). Skip для
        # error/repetition/voice-placeholder responses (handled внутри
        # `_append_model_footer`).
        full_response = self._apply_model_footer(full_response)

        parts = self._split_message(f"🦀 {query}\n\n{full_response}" if is_self else full_response)
        delivered_ids: list[str] = []

        if is_self and not force_new_message:
            source_message = await self._safe_edit(source_message, parts[0])
            if getattr(source_message, "id", None):
                delivered_ids.append(str(source_message.id))
            for part in parts[1:]:
                sent = await self._safe_reply_or_send_new(source_message, part)
                if getattr(sent, "id", None):
                    delivered_ids.append(str(sent.id))
            self._maybe_record_smart_trigger_response(
                source_message.chat.id, delivered_ids, full_response
            )
            # Wave 39-A: фиксируем оригинальный текст ответа для следующей проверки
            _repetition_guard.record(_chat_id_for_guard, _delivery_text)
            self._maybe_schedule_autodel(source_message.chat.id, delivered_ids)
            return {
                "delivery_mode": "edit_and_reply",
                "text_message_ids": delivered_ids,
                "parts_count": len(parts),
            }

        if (
            self._should_send_voice_reply()
            or prefer_send_message_for_background
            or force_new_message
        ):
            # Для связки `text+voice` делаем явную текстовую отправку отдельным
            # сообщением: edit плейсхолдера в некоторых клиентах теряется
            # визуально, а send_message даёт надёжный финальный event доставки.
            # В background-handoff это ещё и разрывает зависимость от старого
            # placeholder-сообщения, которое могло уже устареть к моменту ответа.
            _cid = source_message.chat.id
            for _part in parts:
                _p = _part  # захват переменной для lambda
                sent = await _telegram_send_queue.run(
                    _cid, lambda: self.client.send_message(_cid, _p)
                )
                if getattr(sent, "id", None):
                    delivered_ids.append(str(sent.id))
            try:
                delete_coro = getattr(temp_message, "delete", None)
                if callable(delete_coro):
                    await delete_coro()
            except Exception:  # noqa: BLE001
                pass
            self._maybe_record_smart_trigger_response(
                source_message.chat.id, delivered_ids, full_response
            )
            # Wave 39-A: фиксируем оригинальный текст для repetition guard
            _repetition_guard.record(_chat_id_for_guard, _delivery_text)
            self._maybe_schedule_autodel(source_message.chat.id, delivered_ids)
            return {
                "delivery_mode": "send_message",
                "text_message_ids": delivered_ids,
                "parts_count": len(parts),
            }

        # Wave 39-X: output-based reply target redirect (primary).
        # Парсит начало full_response (LLM output). Если LLM начал ответ с
        # "🐶, ..." / "@user, ..." / [name](tg://user?id=N) — это explicit
        # addressee, и reply_to должен быть на этого user'а (если он matches
        # referenced.from_user). Wave 37-B (anaphora) остаётся как fallback.
        reply_target = _resolve_reply_target_from_output(
            source_message, full_response, fallback_query=query
        )

        # Wave 38: если redirect сработал — inject inline mention к автору
        # referenced message в text parts (clickable @ для users без username).
        if reply_target is not source_message:
            target_user = getattr(reply_target, "from_user", None)
            if target_user is not None:
                parts = [_inject_user_mention_link(p, target_user) or p for p in parts]

        # Bug 4 guard: тот же случай для основного пути доставки — edit чужого
        # сообщения недопустим, fallback на reply вместо edit.
        if temp_message is source_message:
            first_msg = await self._safe_reply_or_send_new(reply_target, parts[0])
        else:
            first_msg = await self._safe_edit(temp_message, parts[0])
        temp_message = first_msg
        if getattr(temp_message, "id", None):
            delivered_ids.append(str(temp_message.id))
        for part in parts[1:]:
            sent = await self._safe_reply_or_send_new(reply_target, part)
            if getattr(sent, "id", None):
                delivered_ids.append(str(sent.id))
        self._maybe_record_smart_trigger_response(
            source_message.chat.id, delivered_ids, full_response
        )
        # Wave 39-A: фиксируем оригинальный текст для repetition guard
        _repetition_guard.record(_chat_id_for_guard, _delivery_text)
        result = {
            "delivery_mode": "edit_and_reply",
            "text_message_ids": delivered_ids,
            "parts_count": len(parts),
        }
        self._maybe_schedule_autodel(source_message.chat.id, delivered_ids)
        return result

    def _maybe_record_smart_trigger_response(
        self,
        chat_id: int | str,
        delivered_ids: list[str],
        response_text: str | None = None,
    ) -> None:
        """Smart Routing Phase 5: записать KrabResponse если был pending smart trigger.

        Вызывается из _deliver_response_parts после успешной доставки.
        Best-effort — никогда не падает (best-effort tracking).

        response_text передаётся для joke calibration (Idea 33): feedback_tracker
        проверяет _is_humor_like() и при необходимости фиксирует шутку в store.
        """
        try:
            cid = str(chat_id)
            pending = self._pending_smart_trigger.pop(cid, None)
            if pending is None or not delivered_ids:
                return
            from ..core.feedback_tracker import (  # noqa: PLC0415
                KrabResponse,
                get_tracker,
            )

            tracker = get_tracker()
            now = time.time()
            for mid_str in delivered_ids:
                try:
                    mid = int(mid_str)
                except (ValueError, TypeError):
                    continue
                tracker.record_krab_response(
                    KrabResponse(
                        chat_id=cid,
                        message_id=mid,
                        sent_at=now,
                        decision_path=getattr(pending, "decision_path", "unknown"),
                        confidence=float(getattr(pending, "confidence", 1.0) or 1.0),
                        response_text=response_text or None,
                    )
                )
        except Exception:  # noqa: BLE001
            pass  # best-effort — не валим доставку из-за tracker

    def _maybe_schedule_autodel(self, chat_id: int, delivered_ids: list[str]) -> None:
        """
        Если для чата включено autodel — планирует удаление доставленных сообщений.
        """
        from ..handlers.command_handlers import (  # noqa: PLC0415
            get_autodel_delay,
            schedule_autodel,
        )

        delay = get_autodel_delay(self, chat_id)
        if not delay or not delivered_ids:
            return
        for msg_id_str in delivered_ids:
            try:
                msg_id = int(msg_id_str)
            except (ValueError, TypeError):
                continue
            schedule_autodel(self.client, chat_id, msg_id, delay)

    @staticmethod
    def _message_ids_from_delivery(delivery_result: dict[str, Any] | None) -> list[str]:
        """Извлекает список текстовых message-id из delivery summary."""
        if not isinstance(delivery_result, dict):
            return []
        rows = delivery_result.get("text_message_ids")
        if not isinstance(rows, list):
            return []
        return [str(row).strip() for row in rows if str(row).strip()]

    @staticmethod
    def _build_effective_user_query(
        *,
        query: str,
        has_images: bool,
        reply_context: str | None = None,
        sender_name: str = "",
        is_group: bool = False,
    ) -> str:
        """
        Нормализует текст пользовательского запроса перед отправкой в модель.

        Почему отдельный helper:
        - раньше фото без подписи уходило как английское `(Image sent)`;
        - маленькие vision-модели цеплялись за этот placeholder и начинали
          описывать картинку по-английски, игнорируя тон чата;
        - для user-facing канала безопаснее отправить явный русский запрос;
        - reply_context (если non-None) префиксится к query чтобы модель видела
          контекст исходного сообщения, на которое user сделал reply (Telegram
          UI показывает quoted message, но MTProto event delivers только
          reply_to_message_id — без явной prepend'я модель не видит).
        - sender_name + is_group (27.04.2026): для group chat'ов prefix
          `[username]:` различает speakers (раньше LLM слышал "user / user / user"
          и сливал participants).
        """
        normalized = str(query or "").strip()
        if not normalized:
            normalized = "Опиши присланное изображение на русском языке." if has_images else ""
        ctx = (reply_context or "").strip()
        sender_tag = ""
        if is_group:
            _sn = str(sender_name or "").strip()
            if _sn:
                sender_tag = f"[{_sn}]: "
        if ctx:
            return f"{sender_tag}[В ответ на сообщение: «{ctx}»]\n\n{normalized}".rstrip()
        return f"{sender_tag}{normalized}".rstrip()
