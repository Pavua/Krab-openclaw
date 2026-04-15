"""
Chunking сообщений для Memory Layer (Track E).

Один chunk = связная "разговорная нить": набор сообщений, которые имеет смысл
эмбеддить и искать вместе. Эмбеддинг одиночного сообщения часто бесполезен —
"ok" без контекста матчится со всем на свете. Chunk'и — это основная единица
retrieval'а.

Стратегия группировки (в порядке приоритета):

1. **reply_to chains**: если у сообщения есть `reply_to_message_id`, и целевое
   сообщение уже находится в одном из недавних открытых chunk'ов — цепляем
   к тому же chunk'у. Это самый сильный сигнал "это про тот же разговор".

2. **Time-gap fallback**: если reply_to нет (или таргет потерян) — смотрим
   на разрыв с последним сообщением *текущего* chunk'а. gap > 5 мин → новый
   chunk. Это грубо, но для flat-dialog работает.

3. **Max size**: chunk не может быть бесконечным. По умолчанию:
   - `max_messages = 50`
   - `max_chars = 4000`
   Лимит важен для двух вещей: (a) эмбеддинг Model2Vec — усреднение, разбавление
   сигнала у слишком длинного текста; (b) FTS5 snippet size.

Принципы:
  - Детерминированность: один и тот же вход даёт один и тот же выход.
  - Идемпотентность: повторный вызов `chunk_messages(chunks)` после flat-ify
    не меняет группировку.
  - Iterative: для bootstrap'а больших архивов — не держим всё в памяти, только
    окно последних chunk'ов (`_lookback_chunks`).

NOTE: этот модуль не знает про PII scrubber. Редактирование — ответственность
вышестоящего слоя (memory_archive.py), чтобы chunking оставался чисто
структурной операцией.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable, Iterator


# ---------------------------------------------------------------------------
# Модели.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Message:
    """
    Минимальная проекция Telegram-сообщения для chunking'а.

    Используем в обход полной Pyrogram.Message — чтобы chunking можно было
    тестировать и запускать на чистом JSON-экспорте без pyrofork-зависимости.
    """

    message_id: str
    chat_id: str
    timestamp: datetime
    text: str
    sender_id: str | None = None
    reply_to_message_id: str | None = None


@dataclass
class Chunk:
    """Группа связанных сообщений (см. docstring модуля)."""

    chat_id: str
    messages: list[Message] = field(default_factory=list)

    @property
    def message_ids(self) -> set[str]:
        return {m.message_id for m in self.messages}

    @property
    def start_timestamp(self) -> datetime | None:
        return self.messages[0].timestamp if self.messages else None

    @property
    def end_timestamp(self) -> datetime | None:
        return self.messages[-1].timestamp if self.messages else None

    @property
    def char_len(self) -> int:
        return sum(len(m.text) for m in self.messages)

    @property
    def text(self) -> str:
        """Объединённый текст (с разделителем) — кормится в Model2Vec."""
        return "\n".join(m.text for m in self.messages if m.text)

    def append(self, msg: Message) -> None:
        self.messages.append(msg)

    def has_message(self, message_id: str) -> bool:
        return message_id in self.message_ids

    def is_empty(self) -> bool:
        return not self.messages


# ---------------------------------------------------------------------------
# Chunker.
# ---------------------------------------------------------------------------

DEFAULT_TIME_GAP = timedelta(minutes=5)
DEFAULT_MAX_MESSAGES = 50
DEFAULT_MAX_CHARS = 4000
DEFAULT_LOOKBACK = 10  # сколько предыдущих chunks держать открытыми для reply_to


class ChunkBuilder:
    """
    Инкрементальный построитель chunks.

    Использование:
        builder = ChunkBuilder()
        for msg in messages_in_chronological_order:
            builder.add(msg)
        all_chunks = builder.flush()

    Или сразу:
        chunks = list(chunk_messages(messages_in_chronological_order))

    Сообщения ДОЛЖНЫ подаваться отсортированными по timestamp ASC, иначе
    логика time-gap нарушится. Мы явно не сортируем внутри — это
    ответственность caller'а (bootstrap парсера или incremental worker'а).
    """

    def __init__(
        self,
        time_gap: timedelta = DEFAULT_TIME_GAP,
        max_messages: int = DEFAULT_MAX_MESSAGES,
        max_chars: int = DEFAULT_MAX_CHARS,
        lookback: int = DEFAULT_LOOKBACK,
    ) -> None:
        self._time_gap = time_gap
        self._max_messages = max_messages
        self._max_chars = max_chars
        # Окно последних закрытых chunks — для reply_to lookup'а.
        self._open_chunks: deque[Chunk] = deque(maxlen=lookback)
        self._closed_chunks: list[Chunk] = []
        self._current_chat_id: str | None = None

    # ------------------------------------------------------------------
    # Публичный API.
    # ------------------------------------------------------------------

    def add(self, msg: Message) -> None:
        """
        Добавляет сообщение. Решает, цеплять к существующему chunk'у или
        открывать новый.
        """
        # Смена chat_id — flush всех открытых chunks.
        # Это необычный путь (обычно caller группирует по чату), но
        # защищает от edge-case'ов.
        if self._current_chat_id is not None and msg.chat_id != self._current_chat_id:
            self._close_all()
        self._current_chat_id = msg.chat_id

        target_chunk = self._find_reply_target(msg)

        if target_chunk is not None:
            # Привязываем к найденному parent-chunk'у, если не переполнен.
            if self._can_append(target_chunk, msg):
                target_chunk.append(msg)
                return
            # Parent переполнен — всё равно открываем новый, но нужно
            # осознавать: цепочка reply может "разорваться" внешне.

        # Пробуем time-gap к самому свежему открытому chunk'у.
        if self._open_chunks:
            latest = self._open_chunks[-1]
            if (
                self._can_append(latest, msg)
                and self._within_time_gap(latest, msg)
            ):
                latest.append(msg)
                return

        # Иначе — новый chunk.
        self._start_new_chunk(msg)

    def flush(self) -> list[Chunk]:
        """
        Возвращает все chunks (включая ещё открытые) и сбрасывает state.
        После flush builder готов к переиспользованию.
        """
        self._close_all()
        result = self._closed_chunks
        self._closed_chunks = []
        self._current_chat_id = None
        return result

    # ------------------------------------------------------------------
    # Внутренние методы.
    # ------------------------------------------------------------------

    def _find_reply_target(self, msg: Message) -> Chunk | None:
        """Ищет chunk, содержащий сообщение, на которое идёт reply."""
        if not msg.reply_to_message_id:
            return None
        # Идём от самых новых к старым — обычно reply ссылается недалеко.
        for chunk in reversed(self._open_chunks):
            if chunk.has_message(msg.reply_to_message_id):
                return chunk
        return None

    def _within_time_gap(self, chunk: Chunk, msg: Message) -> bool:
        """True, если msg недалеко по времени от последнего в chunk'е."""
        end = chunk.end_timestamp
        if end is None:
            return True
        # Важно: если msg.timestamp < end (сообщение из прошлого) — считаем
        # что gap не нарушен (это может быть редкий out-of-order, не наша боль).
        delta = abs(msg.timestamp - end)
        return delta <= self._time_gap

    def _can_append(self, chunk: Chunk, msg: Message) -> bool:
        """Проверка лимитов размера."""
        if len(chunk.messages) >= self._max_messages:
            return False
        if chunk.char_len + len(msg.text) > self._max_chars:
            return False
        return True

    def _start_new_chunk(self, msg: Message) -> None:
        """Открывает новый chunk, при вытеснении старого — закрывает его."""
        new_chunk = Chunk(chat_id=msg.chat_id)
        new_chunk.append(msg)

        # Если deque переполнен — самый старый выталкивается.
        # Перехватываем это вручную: сохраним его в closed до вытеснения.
        if len(self._open_chunks) == self._open_chunks.maxlen:
            evicted = self._open_chunks[0]
            if not evicted.is_empty():
                self._closed_chunks.append(evicted)

        self._open_chunks.append(new_chunk)

    def _close_all(self) -> None:
        """Закрывает все открытые chunks в закрытые (final flush)."""
        while self._open_chunks:
            chunk = self._open_chunks.popleft()
            if not chunk.is_empty():
                self._closed_chunks.append(chunk)


def chunk_messages(
    messages: Iterable[Message],
    *,
    time_gap: timedelta = DEFAULT_TIME_GAP,
    max_messages: int = DEFAULT_MAX_MESSAGES,
    max_chars: int = DEFAULT_MAX_CHARS,
    lookback: int = DEFAULT_LOOKBACK,
) -> Iterator[Chunk]:
    """Удобный генератор — принимает сортированный поток и выдаёт chunks."""
    builder = ChunkBuilder(
        time_gap=time_gap,
        max_messages=max_messages,
        max_chars=max_chars,
        lookback=lookback,
    )
    for msg in messages:
        builder.add(msg)
    yield from builder.flush()
