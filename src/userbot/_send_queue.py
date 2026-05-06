"""
_TelegramSendQueue — Wave 31-D.

Per-chat serialised queue с exponential-backoff retry для исходящих
Telegram API вызовов. Вынесен из userbot_bridge.py для совместного
использования bridge и TelegramSendUtilsMixin без circular imports.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from ..core.chat_capability_cache import chat_capability_cache
from ..core.logger import get_logger
from ..core.telegram_rate_limiter import telegram_rate_limiter

logger = get_logger(__name__)


class _TelegramSendQueue:
    """
    Per-chat serialised queue with exponential-backoff retry for outgoing
    Telegram API calls (send_message, edit, reply).

    Зачем нужна очередь:
    - При долгих tool-chain задачах Telegram API может вернуть FLOOD_WAIT или
      временный timeout; без retry сообщение теряется бесследно.
    - Per-chat воркер гарантирует порядок доставки внутри одного чата и изолирует
      медленные чаты от быстрых.
    - Воркер ленив: стартует при первом вызове, самоостанавливается через 30 с простоя.
    """

    _MAX_RETRIES: int = 3
    _BASE_BACKOFF_SEC: float = 0.5

    def __init__(self) -> None:
        self._queues: dict[int, asyncio.Queue] = {}
        self._workers: dict[int, asyncio.Task] = {}
        # slowmode: chat_id → последний момент успешной отправки (time.monotonic())
        self._slowmode_last_sent: dict[int, float] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self, chat_id: int, coro_factory: Any) -> Any:
        """
        Ставит вызов Telegram API в очередь чата и ждёт результата.

        coro_factory — callable без аргументов, возвращающий корутину:
            lambda: client.send_message(chat_id, text)

        При FLOOD_WAIT или TimeoutError выполняет до _MAX_RETRIES попыток
        с экспоненциальным откатом. Остальные исключения пробрасываются.
        """
        queue = self._get_or_create_queue(chat_id)
        self._ensure_worker_running(chat_id)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        await queue.put((coro_factory, fut))
        return await fut

    async def stop_all(self) -> None:
        """Останавливает всех воркеров (вызывать при shutdown юзербота)."""
        for task in list(self._workers.values()):
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._workers.clear()
        self._queues.clear()
        self._slowmode_last_sent.clear()

    def reset(self) -> None:
        """
        Сбрасывает state без async-cancel (W32: для синхронного bootstrap).

        Вызывать при старте нового event loop'а, когда старые queues/workers
        привязаны к убитому loop'у и `await` недоступен. Задачи не
        отменяются (loop уже мёртв — `cancel()` бросит RuntimeError), просто
        отбрасываем ссылки: GC подчистит их вместе со старым loop'ом.
        """
        self._queues.clear()
        self._workers.clear()
        self._slowmode_last_sent.clear()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _enforce_slowmode_for_chat(self, chat_id: int) -> None:
        """
        Ждёт, если для чата установлен slowmode.
        Вызывать перед первой попыткой отправки — не в retry-цикле.
        """
        slow_sec = chat_capability_cache.get_slow_mode_seconds(chat_id)
        if not slow_sec or slow_sec <= 0:
            return
        last_sent = self._slowmode_last_sent.get(chat_id, 0.0)
        elapsed = time.monotonic() - last_sent
        remaining = slow_sec - elapsed
        if remaining > 0:
            logger.debug("slowmode_wait", chat_id=chat_id, wait_sec=round(remaining, 1))
            await asyncio.sleep(remaining)

    def _queue_matches_loop(
        self,
        queue: asyncio.Queue,
        current_loop: asyncio.AbstractEventLoop,
    ) -> bool:
        """True, если очередь может безопасно использоваться в `current_loop`.

        `asyncio.Queue` (через `_LoopBoundMixin`) лениво кэширует loop в
        `_loop`. Пока `_loop is None` — очередь ещё не привязана, подходит
        любому loop. При mismatch `_get_loop()` бросит
        `RuntimeError: bound to a different event loop`.
        """
        bound = getattr(queue, "_loop", None)
        if bound is None:
            return True
        return bound is current_loop

    def _get_or_create_queue(self, chat_id: int) -> asyncio.Queue:
        """Создаёт или возвращает per-chat очередь.

        W32: если существующая очередь привязана к другому event loop
        (сценарий рестарта userbot'а — модульный singleton переживает loop),
        пересоздаём её вместе с воркером. Без этого `queue.put()` бросает
        `RuntimeError: Queue bound to different event loop`.
        """
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        existing = self._queues.get(chat_id)
        if (
            existing is not None
            and current_loop is not None
            and not self._queue_matches_loop(existing, current_loop)
        ):
            bound = getattr(existing, "_loop", None)
            logger.warning(
                "telegram_send_queue_loop_mismatch_rebind",
                chat_id=chat_id,
                bound_closed=getattr(bound, "_closed", None),
            )
            # Старый воркер привязан к убитому loop — просто отбрасываем
            # ссылку; cancel() на foreign loop бросил бы RuntimeError.
            self._workers.pop(chat_id, None)
            existing = None

        if existing is None:
            self._queues[chat_id] = asyncio.Queue()
        return self._queues[chat_id]

    def _ensure_worker_running(self, chat_id: int) -> None:
        task = self._workers.get(chat_id)
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        # W32: если task из другого loop'а — игнорируем и создаём новый.
        if task is not None and current_loop is not None:
            task_loop = getattr(task, "_loop", None)
            if task_loop is not None and task_loop is not current_loop:
                task = None

        if task is None or task.done():
            self._workers[chat_id] = asyncio.create_task(
                self._worker(chat_id), name=f"tg-send-{chat_id}"
            )

    async def _worker(self, chat_id: int) -> None:
        queue = self._queues.get(chat_id)
        if queue is None:
            return
        while True:
            try:
                coro_factory, fut = await asyncio.wait_for(queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                # Очередь пустовала 30 с — воркер самоостанавливается.
                self._workers.pop(chat_id, None)
                return

            result_exc: BaseException | None = None
            result_val: Any = None

            # Slowmode: ждём перед отправкой, если у чата настроен slowmode.
            # Делаем ДО retry-цикла — не нужно засыпать снова при retry.
            await self._enforce_slowmode_for_chat(chat_id)

            for attempt in range(self._MAX_RETRIES):
                try:
                    # B.7: global API rate limit. acquire() → sleep если
                    # aggregate rate превысил soft cap (default 20 req/s).
                    # Ставим ДО coro_factory чтобы retry тоже учитывались.
                    await telegram_rate_limiter.acquire(purpose="send_queue")
                    result_val = await coro_factory()
                    # Фиксируем время успешной отправки для slowmode-трекинга.
                    self._slowmode_last_sent[chat_id] = time.monotonic()
                    result_exc = None
                    break
                except Exception as exc:  # noqa: BLE001
                    err_upper = str(exc).upper()
                    is_flood = "FLOOD" in err_upper
                    is_slowmode = "SLOWMODE_WAIT" in err_upper
                    is_timeout = isinstance(exc, (asyncio.TimeoutError, TimeoutError))
                    if is_slowmode:
                        # SlowmodeWait: обновляем last_sent и ждём
                        self._slowmode_last_sent[chat_id] = time.monotonic()
                        m = re.search(r"(\d+)", str(exc))
                        slow_wait = float(m.group(1)) if m else 10.0
                        logger.warning(
                            "slowmode_wait_from_error",
                            chat_id=chat_id,
                            wait_sec=slow_wait,
                        )
                        if attempt < self._MAX_RETRIES - 1:
                            await asyncio.sleep(slow_wait)
                            continue
                    if (is_flood or is_timeout) and attempt < self._MAX_RETRIES - 1:
                        delay = self._BASE_BACKOFF_SEC * (2**attempt)
                        if is_flood:
                            m = re.search(r"A wait of (\d+) seconds", str(exc), re.I)
                            if m:
                                delay = max(delay, float(m.group(1)))
                        await asyncio.sleep(delay)
                        continue
                    result_exc = exc
                    break

            if not fut.done():
                if result_exc is not None:
                    fut.set_exception(result_exc)
                else:
                    fut.set_result(result_val)
            queue.task_done()


# Singleton — один на весь процесс юзербота.
telegram_send_queue = _TelegramSendQueue()
