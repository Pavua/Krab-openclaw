"""
Per-chat Message Batcher — буферизует incoming msgs пока предыдущий LLM-call ещё идёт.

Когда LLM busy для chat_id → new msgs queued. При release → batched в один prompt.

Forward Batch Mode (сессия 14):
  Если N пересланных сообщений приходят в окно FORWARD_BATCH_WINDOW_SEC
  и все имеют forward_from* признак — они дожидаются конца пачки и
  обрабатываются одним LLM-запросом с per-sender attribution.

Bulk Forward Mode (Wave 33-C):
  Если в течение BULK_DETECTION_WINDOW_SEC прилетает BULK_DETECTION_THRESHOLD+
  пересланных сообщений — активируется bulk mode: MAX увеличивается до 200,
  WINDOW до 60s. Это позволяет консолидировать 310 сообщений (3 пачки по 100)
  в ОДИН LLM-запрос вместо 9+ отдельных.

Переменные окружения:
  KRAB_FORWARD_BATCH_WINDOW_SEC              — окно ожидания конца пачки (default 5)
  KRAB_FORWARD_BATCH_MAX                     — максимум сообщений в пачке (default 20)
  KRAB_BULK_FORWARD_MAX_BATCH                — bulk mode: максимум сообщений (default 200)
  KRAB_BULK_FORWARD_WINDOW_SEC               — bulk mode: окно ожидания (default 60)
  KRAB_BULK_FORWARD_DETECTION_THRESHOLD      — кол-во forwards для входа в bulk mode (default 5)
  KRAB_BULK_FORWARD_DETECTION_WINDOW_SEC     — окно детекции burst (default 5)
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from structlog import get_logger

logger = get_logger(__name__)

MAX_BATCH_SIZE = int(os.environ.get("BATCH_MAX_SIZE", "5"))
MAX_BATCH_AGE_SEC = float(os.environ.get("BATCH_MAX_AGE_SEC", "30"))
BATCH_FORMAT = os.environ.get(
    "BATCH_FORMAT",
    "Пользователь прислал {count} сообщений подряд:\n\n{messages}\n\nОтветь coherently объединяя контекст.",
)

# Forward batch settings (обычный режим)
FORWARD_BATCH_WINDOW_SEC = float(os.environ.get("KRAB_FORWARD_BATCH_WINDOW_SEC", "5"))
FORWARD_BATCH_MAX = int(os.environ.get("KRAB_FORWARD_BATCH_MAX", "20"))

# Wave 33-C: bulk forward mode — активируется при массовой пересылке (100+ сообщений)
# Wave 33-C-fix: window до 180s — selecting 100 messages tap-by-tap занимает 1-2 мин
BULK_MAX_BATCH_SIZE = int(os.environ.get("KRAB_BULK_FORWARD_MAX_BATCH", "300"))
BULK_WINDOW_SEC = float(os.environ.get("KRAB_BULK_FORWARD_WINDOW_SEC", "180"))
BULK_DETECTION_THRESHOLD = int(os.environ.get("KRAB_BULK_FORWARD_DETECTION_THRESHOLD", "5"))
BULK_DETECTION_WINDOW_SEC = float(os.environ.get("KRAB_BULK_FORWARD_DETECTION_WINDOW_SEC", "5"))

FORWARD_BATCH_PROMPT_HEADER = "[Пачка пересланных сообщений{senders_info}]:\n{lines}"


@dataclass
class PendingMessage:
    text: str
    sender_id: str
    ts: float = field(default_factory=time.time)
    message_id: Optional[int] = None
    # Forward batch fields
    is_forwarded: bool = False
    forward_sender_name: str = ""  # отображаемое имя отправителя оригинала
    forward_sender_username: str = ""  # @username если известен
    forward_date: Optional[int] = None  # unix timestamp оригинального сообщения
    # Wave 14-A coalescing: photo / video — буферизуем как часть пачки,
    # чтобы не плодить independent AI-калы по каждому медиа-сообщению.
    is_photo: bool = False
    photo_caption: str = ""


@dataclass
class ChatBatch:
    """Buffer pending messages для одного чата пока LLM busy."""

    chat_id: str
    pending: list[PendingMessage] = field(default_factory=list)
    busy: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def age_sec(self) -> float:
        if not self.pending:
            return 0.0
        return time.time() - self.pending[0].ts

    def size(self) -> int:
        return len(self.pending)

    def should_flush(self) -> bool:
        """Есть ли причина flush прямо сейчас?"""
        if not self.pending:
            return False
        if self.size() >= MAX_BATCH_SIZE:
            return True
        if self.age_sec() >= MAX_BATCH_AGE_SEC:
            return True
        return False

    def drain(self) -> list[PendingMessage]:
        """Take all pending, reset buffer."""
        msgs = self.pending
        self.pending = []
        return msgs

    def format_batched_prompt(self) -> str:
        """Объединяет pending messages в single prompt."""
        if not self.pending:
            return ""
        lines = []
        for i, m in enumerate(self.pending, 1):
            lines.append(f"[{i}] {m.text}")
        return BATCH_FORMAT.format(count=len(self.pending), messages="\n".join(lines))


@dataclass
class ForwardBatchBuffer:
    """
    Временный буфер пересланных сообщений для одного чата.
    Накапливает forwards в окне FORWARD_BATCH_WINDOW_SEC, затем сбрасывает.

    Wave 33-C: поддерживает bulk mode — при burst 5+ forwards за 5s
    переключается на лимиты 200 сообщений / 60s окно.
    """

    chat_id: str
    messages: list[PendingMessage] = field(default_factory=list)
    _timer_handle: Optional[asyncio.TimerHandle] = field(default=None, repr=False, compare=False)
    _flush_callback: Optional[Callable] = field(default=None, repr=False, compare=False)
    # Wave 33-C: bulk forward detection state
    _bulk_mode: bool = field(default=False, repr=False, compare=False)
    _first_msg_ts: float = field(default=0.0, repr=False, compare=False)

    def reset(self) -> None:
        self.messages = []
        self._bulk_mode = False
        self._first_msg_ts = 0.0
        self._cancel_timer()

    def is_bulk_mode(self) -> bool:
        """Активен ли bulk forward mode для этого буфера."""
        return self._bulk_mode

    def _check_and_activate_bulk_mode(self) -> None:
        """
        Проверяет условия для активации bulk mode.
        Если BULK_DETECTION_THRESHOLD+ forwards пришли за BULK_DETECTION_WINDOW_SEC —
        активируем расширенные лимиты (200 msg / 60s).
        """
        if self._bulk_mode:
            return  # уже активен
        now = time.time()
        if self._first_msg_ts == 0.0:
            return  # нет данных
        elapsed = now - self._first_msg_ts
        count = len(self.messages)
        if count >= BULK_DETECTION_THRESHOLD and elapsed <= BULK_DETECTION_WINDOW_SEC:
            self._bulk_mode = True
            logger.info(
                "bulk_forward_mode_activated",
                chat_id=self.chat_id,
                buffer_size=count,
                elapsed_sec=round(elapsed, 2),
            )

    def effective_max(self) -> int:
        """Максимальный размер батча с учётом текущего режима."""
        return BULK_MAX_BATCH_SIZE if self._bulk_mode else FORWARD_BATCH_MAX

    def effective_window(self) -> float:
        """Окно ожидания с учётом текущего режима."""
        return BULK_WINDOW_SEC if self._bulk_mode else FORWARD_BATCH_WINDOW_SEC

    def _cancel_timer(self) -> None:
        if self._timer_handle is not None:
            self._timer_handle.cancel()
            self._timer_handle = None

    def schedule_flush(self, delay_sec: float, callback: Callable) -> None:
        """Перепланировать flush-таймер (сдвигается при каждом новом сообщении)."""
        self._cancel_timer()
        self._flush_callback = callback
        try:
            loop = asyncio.get_running_loop()
            self._timer_handle = loop.call_later(delay_sec, self._fire_flush)
        except RuntimeError:
            pass  # нет running loop — flush не планируем (только тесты)

    def _fire_flush(self) -> None:
        self._timer_handle = None
        if self._flush_callback is not None:
            asyncio.ensure_future(self._flush_callback())

    def add(self, msg: PendingMessage) -> None:
        # Фиксируем timestamp первого сообщения в буфере
        if not self.messages:
            self._first_msg_ts = time.time()
        self.messages.append(msg)

    def size(self) -> int:
        return len(self.messages)

    def drain(self) -> list[PendingMessage]:
        msgs = list(self.messages)
        self.messages = []
        # Сбрасываем bulk state после flush — следующая пачка начнётся заново
        self._bulk_mode = False
        self._first_msg_ts = 0.0
        self._cancel_timer()
        return msgs

    def format_prompt(self, owner_query: str = "") -> str:
        """
        Собирает prompt из пачки пересланных сообщений.

        Формат:
            [Пачка пересланных сообщений от @alice, @bob]:
            1. [alice, HH:MM]: текст
            2. [bob, HH:MM]: текст
            ...

            [Запрос]: owner_query (если есть)
        """
        if not self.messages:
            return ""

        # Собираем уникальных отправителей для заголовка
        seen: list[str] = []
        for m in self.messages:
            label = m.forward_sender_username or m.forward_sender_name or m.sender_id
            if label not in seen:
                seen.append(label)

        if seen:
            senders_info = " от " + ", ".join(f"@{s}" if not s.startswith("@") else s for s in seen)
        else:
            senders_info = ""

        lines_parts: list[str] = []
        for i, m in enumerate(self.messages, 1):
            sender_label = m.forward_sender_username or m.forward_sender_name or m.sender_id
            if m.forward_date:
                import datetime

                ts = datetime.datetime.fromtimestamp(m.forward_date, tz=datetime.timezone.utc)
                time_str = ts.strftime("%H:%M")
                prefix = f"[{sender_label}, {time_str}]"
            else:
                prefix = f"[{sender_label}]"
            if m.is_photo:
                # Wave 14-A: photo coalesced в пачку. Подаём как text-маркер,
                # чтобы LLM упомянул факт прикреплённого медиа без отдельного
                # multi-modal call. Caption сохраняем — он часто несёт содержание.
                cap = (m.photo_caption or m.text or "").strip()
                if cap:
                    lines_parts.append(f"{i}. {prefix}: [фото] {cap}")
                else:
                    lines_parts.append(f"{i}. {prefix}: [фото без подписи]")
            else:
                lines_parts.append(f"{i}. {prefix}: {m.text}")

        body = FORWARD_BATCH_PROMPT_HEADER.format(
            senders_info=senders_info,
            lines="\n".join(lines_parts),
        )
        if owner_query:
            body += f"\n\n[Запрос]: {owner_query}"
        return body


class MessageBatcher:
    """Singleton per-chat batcher."""

    def __init__(self) -> None:
        self._batches: dict[str, ChatBatch] = {}
        # Forward batch buffers: chat_id → ForwardBatchBuffer
        self._fwd_buffers: dict[str, ForwardBatchBuffer] = {}

    def _get_fwd_buffer(self, chat_id: str) -> ForwardBatchBuffer:
        chat_id = str(chat_id)
        if chat_id not in self._fwd_buffers:
            self._fwd_buffers[chat_id] = ForwardBatchBuffer(chat_id=chat_id)
        return self._fwd_buffers[chat_id]

    def add_forward(
        self,
        chat_id: str,
        msg: PendingMessage,
        on_flush: Callable,  # async callable(chat_id, messages: list[PendingMessage]) -> None
    ) -> bool:
        """
        Добавить пересланное сообщение в forward-буфер.

        Возвращает True если сообщение добавлено в буфер (будет обработано позже).
        Возвращает False если сообщение не является forwarded (нужно обработать сразу).

        on_flush вызывается (async) когда окно истекает или достигается лимит.

        Wave 33-C: при обнаружении bulk burst (5+ forwards за 5s) автоматически
        переключается в bulk mode: лимит 200 msg, окно 60s. Это консолидирует
        массовую пересылку (100-310 сообщений) в ОДИН LLM-запрос.
        """
        if not msg.is_forwarded:
            return False

        chat_id = str(chat_id)
        buf = self._get_fwd_buffer(chat_id)
        buf.add(msg)

        # Wave 33-C: проверяем bulk mode после добавления сообщения
        buf._check_and_activate_bulk_mode()

        effective_max = buf.effective_max()
        effective_window = buf.effective_window()

        logger.info(
            "forward_msg_buffered",
            chat_id=chat_id,
            buffer_size=buf.size(),
            sender=msg.forward_sender_name or msg.forward_sender_username,
            bulk_mode=buf.is_bulk_mode(),
            effective_max=effective_max,
        )

        # Немедленный flush при достижении максимума (с учётом текущего режима)
        if buf.size() >= effective_max:
            msgs = buf.drain()
            logger.info(
                "forward_batch_max_reached",
                chat_id=chat_id,
                count=len(msgs),
                was_bulk_mode=True,
            )
            asyncio.ensure_future(on_flush(chat_id, msgs))
            return True

        # Перепланируем таймер окна (с учётом текущего режима)
        async def _deferred_flush() -> None:
            drained = buf.drain()
            if drained:
                logger.info(
                    "forward_batch_window_expired",
                    chat_id=chat_id,
                    count=len(drained),
                )
                await on_flush(chat_id, drained)

        buf.schedule_flush(effective_window, _deferred_flush)
        return True

    def _get_batch(self, chat_id: str) -> ChatBatch:
        chat_id = str(chat_id)
        if chat_id not in self._batches:
            self._batches[chat_id] = ChatBatch(chat_id=chat_id)
        return self._batches[chat_id]

    async def try_add_or_flush(
        self,
        chat_id: str,
        msg: PendingMessage,
        processor: Callable,  # async processor(chat_id, combined_prompt) -> response
    ) -> tuple[str, Optional[str]]:
        """
        Add message:
        - If LLM busy for chat → buffer, return ("buffered", None)
        - If not busy → mark busy, process immediately, return ("immediate", response)
        """
        batch = self._get_batch(chat_id)
        async with batch.lock:
            if batch.busy:
                batch.pending.append(msg)
                logger.info(
                    "message_buffered",
                    chat_id=chat_id,
                    buffer_size=batch.size(),
                )
                return ("buffered", None)

            batch.busy = True
            batch.pending.append(msg)
            # Drain под локом: всё, что было в pending — уйдёт в текущий combined.
            # Новые messages, прилетевшие во время processor await, попадут в
            # уже пустой batch.pending и НЕ будут затёрты (старый баг: line 297
            # делал batch.pending=[] безусловно, теряя buffered messages).
            current_msgs = batch.drain()

        try:
            # Обрабатываем первое сообщение (или накопленный batch если уже были)
            snapshot = ChatBatch(chat_id=chat_id)
            snapshot.pending = current_msgs
            combined = snapshot.format_batched_prompt()
            response = await processor(chat_id, combined)
            return ("immediate", response)
        finally:
            # После обработки проверяем накопленные в очереди → запускаем flush
            async with batch.lock:
                batch.busy = False
                has_pending = batch.size() > 0

            if has_pending:
                # Не блокируем текущую task — flush идёт фоново
                asyncio.create_task(self._flush_batch(chat_id, processor))

    async def _flush_batch(self, chat_id: str, processor: Callable) -> None:
        """Process accumulated batch."""
        batch = self._get_batch(chat_id)
        async with batch.lock:
            if batch.busy or not batch.pending:
                return
            batch.busy = True
            # Атомарно забираем snapshot pending под локом — новые messages,
            # пришедшие во время processor await, попадут в чистый batch.pending
            # и будут подобраны рекурсивным flush ниже.
            current_msgs = batch.drain()

        try:
            snapshot = ChatBatch(chat_id=chat_id)
            snapshot.pending = current_msgs
            combined = snapshot.format_batched_prompt()
            logger.info("batch_flushing", chat_id=chat_id, combined_length=len(combined))
            await processor(chat_id, combined)
        except Exception as e:
            logger.error("batch_flush_failed", chat_id=chat_id, error=str(e))
        finally:
            async with batch.lock:
                batch.busy = False
                # Рекурсивный flush если ещё накопилось во время обработки
                has_more = batch.size() > 0
            if has_more:
                asyncio.create_task(self._flush_batch(chat_id, processor))

    def stats(self) -> dict:
        return {
            "total_batches": len(self._batches),
            "active_batches": sum(1 for b in self._batches.values() if b.busy),
            "total_pending": sum(b.size() for b in self._batches.values()),
            "forward_buffers": len(self._fwd_buffers),
            "forward_pending": sum(b.size() for b in self._fwd_buffers.values()),
        }


# Singleton
message_batcher = MessageBatcher()
