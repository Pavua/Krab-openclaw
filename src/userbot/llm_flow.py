# -*- coding: utf-8 -*-
"""
LLM request flow — основной конвейер обработки LLM-запросов через OpenClaw.

Содержит `_run_llm_request_flow` (~750 строк) и вспомогательные методы:
таймаут-расчёты, progress-уведомления, background handoff.

Часть декомпозиции `src/userbot_bridge.py` (session 4+, 2026-04-09).
См. `docs/USERBOT_BRIDGE_SPLIT_PROPOSAL.md` для полной стратегии.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
import traceback
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pyrogram.types import Message

from ..config import config
from ..core.logger import get_logger
from ..core.openclaw_task_poller import (
    STAGNATION_THRESHOLD_SEC,
    check_gateway_http_alive,
    check_tasks_hung,
    detect_stagnation,
    format_task_progress_for_telegram,
    poll_active_tasks,
)

# Маркер reason для asyncio.CancelledError при стагнации LLM-call.
# Ловим только эту reason-строку — generic CancelledError всё ещё пробрасываем выше.
LLM_STAGNATION_CANCEL_REASON = "llm_stagnation_detected"

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Module-level helpers (ранее в userbot_bridge.py, используются _run_llm_request_flow)
# ---------------------------------------------------------------------------


def _current_runtime_primary_model() -> str:
    """
    Возвращает primary-модель из живого OpenClaw runtime.

    Почему helper нужен здесь:
    - truthful self-check не должен опираться на stale `.env` значение;
    - owner userbot должен видеть тот же primary, что реально выставлен в
      `~/.openclaw/openclaw.json`, даже если в этом канале ещё не было
      подтверждённого LLM-маршрута.
    """
    from ..core.openclaw_runtime_models import get_runtime_primary_model

    return str(get_runtime_primary_model() or "").strip()


def _resolve_openclaw_stream_timeouts(*, has_photo: bool) -> tuple[float, float]:
    """
    Возвращает (first_chunk_timeout_sec, chunk_timeout_sec) для OpenClaw stream.

    Почему отдельный таймаут первого чанка:
    - тяжёлые локальные модели (например Qwen 27B) могут долго выдавать первый токен;
    - после старта стрима интервалы между чанками обычно заметно меньше.
    """
    chunk_timeout_sec = float(getattr(config, "OPENCLAW_CHUNK_TIMEOUT_SEC", 180.0))
    # 1800s (30 мин) для текстовых задач — покрывает агентные loop'ы (Меркадона, VL-турнир),
    # которые буферизуют все tool-вызовы внутри OpenClaw и шлют первый chunk только по завершении.
    # До 600s было слишком мало: OpenClaw активно работал в дашборде, но Краб видел "тишину".
    default_first = 1200.0 if has_photo else 1800.0
    # Для фото-разбора допускаем отдельный override первого чанка:
    # vision-модели/большие контексты стабильно дольше выходят на первый токен.
    if has_photo:
        first_key = "OPENCLAW_PHOTO_FIRST_CHUNK_TIMEOUT_SEC"
    else:
        first_key = "OPENCLAW_FIRST_CHUNK_TIMEOUT_SEC"
    first_chunk_timeout_sec = float(
        getattr(
            config,
            first_key,
            max(chunk_timeout_sec, default_first),
        )
    )

    # Нижние границы для защиты от слишком маленьких env-значений.
    chunk_timeout_sec = max(15.0, chunk_timeout_sec)
    first_chunk_timeout_sec = max(chunk_timeout_sec, 30.0, first_chunk_timeout_sec)
    return first_chunk_timeout_sec, chunk_timeout_sec


def _resolve_openclaw_buffered_response_timeout(
    *,
    has_photo: bool,
    first_chunk_timeout_sec: float,
) -> float:
    """
    Возвращает верхнюю границу ожидания buffered-ответа OpenClaw.

    Почему нужен отдельный hard-timeout:
    - в текущем контуре `send_message_stream()` буферизует `stream=False` ответ,
      поэтому первый Telegram chunk приходит только после полного completion;
    - soft-timeout первого чанка полезен как сигнал "ответ идёт слишком долго",
      но не должен рубить ещё живую fallback-цепочку OpenClaw раньше gateway timeout;
    - даём разумный запас сверх первого ожидания, чтобы не зависать бесконечно.
    """
    default_total_timeout_sec = 1020.0 if has_photo else 900.0
    return max(default_total_timeout_sec, float(first_chunk_timeout_sec or 0.0) + 60.0)


def _resolve_openclaw_progress_notice_schedule(
    *,
    has_photo: bool,
    first_chunk_timeout_sec: float,
) -> tuple[float, float]:
    """
    Возвращает (initial_sec, repeat_sec) для ранних тех-уведомлений userbot.

    Почему это вынесено отдельно:
    - hard/soft-timeout отвечают за устойчивость транспорта;
    - progress-notice отвечает за UX ожидания и не должен зависеть от 7-минутного окна.
    """
    if has_photo:
        initial_key = "OPENCLAW_PHOTO_PROGRESS_NOTICE_INITIAL_SEC"
        repeat_key = "OPENCLAW_PHOTO_PROGRESS_NOTICE_REPEAT_SEC"
        default_initial_sec = 30.0
        default_repeat_sec = 60.0
    else:
        initial_key = "OPENCLAW_PROGRESS_NOTICE_INITIAL_SEC"
        repeat_key = "OPENCLAW_PROGRESS_NOTICE_REPEAT_SEC"
        default_initial_sec = 20.0
        default_repeat_sec = 45.0
    initial_sec = float(getattr(config, initial_key, default_initial_sec))
    repeat_sec = float(getattr(config, repeat_key, default_repeat_sec))
    initial_sec = max(5.0, min(float(first_chunk_timeout_sec or 0.0), initial_sec))
    repeat_sec = max(15.0, repeat_sec)
    return initial_sec, repeat_sec


def _build_openclaw_progress_wait_notice(
    *,
    route_model: str,
    attempt: int | None,
    elapsed_sec: float,
    notice_index: int,
    tool_calls_summary: str = "",
    gateway_progress: str = "",
    gateway_dead: bool = False,
) -> str:
    """
    Формирует раннее тех-уведомление о том, что buffered-запрос всё ещё жив.

    Текст честный: показывает текущий инструмент и стадию работы.
    Эмодзи выбирается по типу активного инструмента для быстрого визуального понимания.
    """
    route_line = _build_openclaw_route_notice_line(
        route_model=route_model,
        attempt=attempt,
    )
    elapsed_f = float(elapsed_sec or 0.0)
    # Форматируем время: секунды до 60 сек, потом — минуты
    if elapsed_f < 60:
        elapsed_label = f"~{max(1, int(round(elapsed_f)))} сек"
    else:
        mins = int(elapsed_f // 60)
        secs = int(elapsed_f % 60)
        elapsed_label = f"~{mins} мин {secs:02d} сек"

    # Определяем эмодзи и описание по типу активного инструмента
    TOOL_EMOJIS: dict[str, str] = {  # noqa: N806
        "search": "🔍",
        "web": "🌐",
        "browser": "🌐",
        "file": "📁",
        "read": "📖",
        "write": "✏️",
        "code": "💻",
        "python": "🐍",
        "bash": "⚙️",
        "shell": "⚙️",
        "memory": "🧠",
        "recall": "🧠",
        "screenshot": "📸",
        "vision": "👁️",
        "telegram": "📱",
        "mcp": "🔌",
        "api": "🔗",
        "fetch": "📡",
        "http": "📡",
        "think": "💭",
        "reason": "💭",
        "plan": "📋",
    }

    tool_emoji = "🛠️"
    tool_name_display = ""
    if tool_calls_summary:
        summary_lower = tool_calls_summary.lower()
        for key, emoji in TOOL_EMOJIS.items():
            if key in summary_lower:
                tool_emoji = emoji
                break
        # Извлекаем первую narration-строку (например "🌐 Открываю браузер...")
        first_line = tool_calls_summary.split("\n", 1)[0].strip()
        if (
            first_line
            and not first_line.startswith("✅")
            and not first_line.startswith("Инструментов")
        ):
            tool_name_display = first_line

    # Определяем есть ли running tools по счётчику "Инструментов: done/total"
    _tc_m = (
        re.search(r"Инструментов:\s*(\d+)/(\d+)", tool_calls_summary)
        if tool_calls_summary
        else None
    )
    _has_running = _tc_m and int(_tc_m.group(1)) < int(_tc_m.group(2))

    if tool_calls_summary and _has_running:
        if tool_name_display:
            lead = f"{tool_name_display}"
        else:
            lead = f"{tool_emoji} Вызов инструмента — жду результат."
    elif tool_calls_summary:
        lead = f"✅ Инструменты отработали — {tool_emoji} собираю итоговый ответ."
    elif notice_index <= 1:
        lead = "🧩 Запрос принят — OpenClaw обрабатывает задачу."
    elif notice_index <= 3:
        lead = (
            f"⚙️ OpenClaw работает в агентном режиме ({elapsed_label}).\n"
            "Инструменты выполняются внутри цепочки — ответ придёт по завершении."
        )
    else:
        lead = (
            f"🔄 Агентная задача ещё выполняется — {elapsed_label}.\n"
            "Если интересно что именно — загляни в дашборд :18789 (Chat → текущая сессия)."
        )

    result = f"{lead}\n⏱ Прошло: {elapsed_label}" + route_line
    if tool_calls_summary:
        result += f"\n\n{tool_calls_summary}"
    # Прогресс задач из OpenClaw Gateway SQLite
    if gateway_dead:
        result += "\n\n⚠️ Gateway не отвечает на /healthz"
    elif gateway_progress:
        result += f"\n\n📋 Gateway:\n{gateway_progress}"
    return result


def _build_openclaw_slow_wait_notice(*, route_model: str, attempt: int | None) -> str:
    """
    Формирует честное уведомление о долгом buffered-ожидании.

    Сообщение намеренно объясняет, что запрос ещё жив, а userbot не завис навсегда.
    """
    route_line = _build_openclaw_route_notice_line(
        route_model=route_model,
        attempt=attempt,
    )
    return (
        "⏳ Ответ собирается дольше обычного. Продолжаю ждать fallback-цепочку OpenClaw,"
        " не дублируй сообщение." + route_line
    )


def _build_openclaw_route_notice_line(*, route_model: str, attempt: int | None) -> str:
    """
    Формирует truthful-строку о текущем маршруте buffered-запроса.

    Почему это отдельно:
    - Telegram notice должен показывать не только стартовую модель, но и
      фактическую текущую попытку fallback-цепочки;
    - одна и та же логика нужна и для ранних progress-notice, и для slow-wait notice.
    """
    normalized_model = str(route_model or "").strip()
    normalized_attempt = int(attempt or 0) or None
    parts: list[str] = []
    if normalized_model:
        parts.append(f"Текущий маршрут: `{normalized_model}`")
    if normalized_attempt:
        parts.append(f"попытка `{normalized_attempt}`")
        if normalized_attempt > 1:
            parts.append("fallback активен")
    if not parts:
        return ""
    return "\n" + " · ".join(parts) + "."


# ---------------------------------------------------------------------------
# Mixin class
# ---------------------------------------------------------------------------


class LLMFlowMixin:
    """
    Конвейер LLM-запросов: stream-обработка, таймауты, background handoff.

    Mixin для `KraabUserbot`: содержит `_run_llm_request_flow` (основной LLM pipeline),
    `_finish_ai_request_background` и вспомогательные методы. Зависит от
    `self.client`, `self._safe_edit`, `self._deliver_response_parts` и других
    методов KraabUserbot / соседних mixin'ов через MRO.
    """

    async def _finish_ai_request_background_after_previous(
        self,
        *,
        previous_task: asyncio.Task,
        **kwargs: Any,
    ) -> None:
        """
        Запускает новую background-задачу только после завершения предыдущей.

        Почему это отдельный helper:
        - новый owner-запрос не должен падать обратно в inline-path только потому,
          что в чате уже есть активный фоновой run;
        - так мы честно помечаем входящий запрос как `background_started`, не
          удерживая per-chat lock до конца старой задачи;
        - если предыдущая задача упала, новая всё равно должна стартовать.
        """
        chat_id = str(kwargs.get("chat_id") or "").strip()
        try:
            await asyncio.shield(previous_task)
        except asyncio.CancelledError:
            logger.warning("chat_background_task_previous_cancelled", chat_id=chat_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("chat_background_task_previous_failed", chat_id=chat_id, error=str(exc))
        await self._finish_ai_request_background(**kwargs)

    @staticmethod
    def _build_background_handoff_notice(query: str) -> str:
        """
        Возвращает честный текст для момента, когда длинный запрос уходит в фон.

        Это не «готовый ответ», а явное подтверждение, что Краб принял задачу,
        отпустил lock чата и продолжит обработку в background-режиме.
        """
        safe_query = str(query or "").strip() or "запрос"
        return (
            f"🦀 Принял запрос: `{safe_query}`\n\n"
            "⏳ Задача продолжает выполняться в фоне. "
            "Финальный ответ пришлю отдельным сообщением, как только обработка завершится."
        )

    async def _run_llm_request_flow(
        self,
        *,
        message: Message,
        temp_msg: Message,
        is_self: bool,
        query: str,
        chat_id: str,
        runtime_chat_id: str,
        access_profile: Any,
        is_allowed_sender: bool,
        incoming_item_result: dict[str, Any] | None,
        images: list[str],
        force_cloud: bool,
        system_prompt: str,
        action_stop_event: asyncio.Event,
        action_task: asyncio.Task,
        prefer_send_message_for_background: bool = False,
        show_progress_notices: bool = True,
    ) -> None:
        """Общий long-path LLM/tool flow для inline и background режима."""
        # Ленивые импорты модулей, которые живут в userbot_bridge / соседних пакетах.
        # ВАЖНО: module-level helpers импортируются из userbot_bridge (а не напрямую),
        # чтобы monkeypatch в тестах работал корректно (тесты патчат на userbot_bridge).
        import src.userbot_bridge as _ub  # noqa: I001 — ленивый импорт для monkeypatch

        from pyrogram import enums

        from ..core.access_control import AccessLevel
        from ..core.chat_capability_cache import chat_capability_cache
        from ..openclaw_client import openclaw_client

        _flow_start_ts = time.time()  # Точка отсчёта для auto-inject медиафайлов
        full_response = ""
        full_response_raw = ""
        last_edit_time = 0.0
        timeout_error_was_sent = False
        _reaction_sent = False  # флаг: уже поставили ✅/❌ на исходное сообщение
        # Progress-уведомления только в личных чатах или для self-сообщений.
        # В группах обрабатываем молча — финальный ответ всё равно отправляем.
        _show_progress = show_progress_notices

        first_chunk_timeout_sec, chunk_timeout_sec = _resolve_openclaw_stream_timeouts(
            has_photo=bool(images)
        )
        buffered_response_timeout_sec = _resolve_openclaw_buffered_response_timeout(
            has_photo=bool(images),
            first_chunk_timeout_sec=first_chunk_timeout_sec,
        )
        progress_notice_initial_sec, progress_notice_repeat_sec = (
            _resolve_openclaw_progress_notice_schedule(
                has_photo=bool(images),
                first_chunk_timeout_sec=first_chunk_timeout_sec,
            )
        )
        max_output_tokens = int(
            getattr(
                config,
                "USERBOT_PHOTO_MAX_OUTPUT_TOKENS" if images else "USERBOT_MAX_OUTPUT_TOKENS",
                0,
            )
            or 0
        )
        effective_query = self._build_effective_user_query(
            query=query,
            has_images=bool(images),
        )

        # Гостевой режим: отключаем tools/exec для GUEST-уровня
        _guest_disable_tools = (
            bool(getattr(config, "GUEST_TOOLS_DISABLED", True))
            and hasattr(access_profile, "level")
            and str(getattr(access_profile.level, "value", access_profile.level)).lower() == "guest"
        )
        stream = openclaw_client.send_message_stream(
            message=effective_query,
            chat_id=runtime_chat_id,
            system_prompt=system_prompt,
            images=images,
            force_cloud=force_cloud,
            max_output_tokens=max_output_tokens if max_output_tokens > 0 else None,
            disable_tools=_guest_disable_tools,
        )
        stream_iter = stream.__aiter__()
        # Регистрируем текущий task в клиенте для hard-cancel из watchdog'а.
        # detect_stagnation видит зависшую runs.sqlite-задачу и зовёт cancel_current_request() →
        # .cancel() вернётся сюда как CancelledError с reason=stagnation.
        _llm_current_task = asyncio.current_task()
        if _llm_current_task is not None and hasattr(
            openclaw_client, "register_current_request_task"
        ):
            try:
                openclaw_client.register_current_request_task(_llm_current_task)
            except Exception:  # noqa: BLE001
                pass
        received_any_chunk = False
        started_wait_at = time.monotonic()
        slow_first_chunk_notice_sent = False
        progress_notice_count = 0
        next_progress_notice_sec = float(progress_notice_initial_sec)
        tool_progress_poll_sec = float(
            getattr(config, "OPENCLAW_TOOL_PROGRESS_POLL_SEC", 4.0) or 4.0
        )
        tool_progress_poll_sec = max(0.01, tool_progress_poll_sec)
        next_tool_progress_sec = tool_progress_poll_sec
        last_tool_summary = ""
        last_tool_activity_ts = time.monotonic()  # последнее изменение tool_summary или первый чанк
        last_progress_notice_text = ""
        no_tool_activity_timeout_sec = float(
            getattr(config, "OPENCLAW_NO_TOOL_ACTIVITY_TIMEOUT_SEC", 300.0) or 300.0
        )
        no_tool_activity_timeout_sec = max(60.0, no_tool_activity_timeout_sec)
        # Gateway watchdog: интервал HTTP health-check (каждые 30 сек)
        gateway_http_check_interval_sec = 30.0
        next_gateway_http_check_sec = gateway_http_check_interval_sec
        last_gateway_progress = ""  # последний SQLite-прогресс (для дедупликации)
        gateway_http_dead = False  # True если /healthz не ответил
        startup_route_model = str(
            _current_runtime_primary_model() or getattr(config, "MODEL", "") or ""
        ).strip()
        next_chunk_task = asyncio.create_task(stream_iter.__anext__())

        try:
            while True:
                if received_any_chunk:
                    wait_timeout = chunk_timeout_sec
                elif slow_first_chunk_notice_sent:
                    wait_timeout = chunk_timeout_sec
                else:
                    wait_timeout = first_chunk_timeout_sec
                elapsed_wait_sec = time.monotonic() - started_wait_at
                remaining_total_timeout_sec = max(
                    0.0, buffered_response_timeout_sec - elapsed_wait_sec
                )
                if not received_any_chunk:
                    if remaining_total_timeout_sec <= 0.0:
                        logger.error(
                            "openclaw_buffered_response_timeout",
                            chat_id=chat_id,
                            elapsed_sec=round(elapsed_wait_sec, 3),
                            hard_timeout_sec=buffered_response_timeout_sec,
                            has_photo=bool(images),
                        )
                        route_meta = {}
                        if hasattr(openclaw_client, "get_last_runtime_route"):
                            try:
                                route_meta = openclaw_client.get_last_runtime_route() or {}
                            except Exception:
                                route_meta = {}
                        route_model = str(
                            route_meta.get("model")
                            or _current_runtime_primary_model()
                            or getattr(config, "MODEL", "")
                            or ""
                        ).strip()
                        if hasattr(openclaw_client, "_set_last_runtime_route"):
                            try:
                                openclaw_client._set_last_runtime_route(  # noqa: SLF001
                                    channel="error",
                                    model=route_model or "unknown",
                                    route_reason="userbot_buffered_wait_timeout",
                                    route_detail="Userbot дождался buffered OpenClaw дольше допустимого окна",
                                    status="error",
                                    error_code="first_chunk_timeout",
                                    force_cloud=force_cloud,
                                )
                            except Exception:
                                pass
                        full_response = (
                            "❌ OpenClaw слишком долго собирает первый ответ. "
                            "Похоже, цепочка fallback зависла или все cloud-кандидаты перегружены. "
                            "Попробуй `!model local` или повтори запрос позже."
                        )
                        timeout_error_was_sent = True
                        if next_chunk_task and not next_chunk_task.done():
                            next_chunk_task.cancel()
                            try:
                                await next_chunk_task
                            except (asyncio.CancelledError, StopAsyncIteration):
                                pass
                            except Exception:
                                pass
                        try:
                            await stream.aclose()
                        except Exception:
                            pass
                        break
                    if not slow_first_chunk_notice_sent:
                        wait_timeout = min(
                            wait_timeout,
                            max(0.0, first_chunk_timeout_sec - elapsed_wait_sec),
                        )
                    if next_progress_notice_sec > 0.0:
                        wait_timeout = min(
                            wait_timeout,
                            max(0.0, next_progress_notice_sec - elapsed_wait_sec),
                        )
                    if next_tool_progress_sec > 0.0:
                        wait_timeout = min(
                            wait_timeout,
                            max(0.0, next_tool_progress_sec - elapsed_wait_sec),
                        )
                    if next_gateway_http_check_sec > 0.0:
                        wait_timeout = min(
                            wait_timeout,
                            max(0.0, next_gateway_http_check_sec - elapsed_wait_sec),
                        )
                    wait_timeout = min(wait_timeout, remaining_total_timeout_sec)
                try:
                    done, _ = await asyncio.wait({next_chunk_task}, timeout=wait_timeout)
                    if not done:
                        raise asyncio.TimeoutError
                    chunk = next_chunk_task.result()
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    elapsed_wait_sec = time.monotonic() - started_wait_at

                    if received_any_chunk:
                        logger.error(
                            "openclaw_stream_chunk_timeout",
                            chat_id=chat_id,
                            timeout_sec=wait_timeout,
                            first_chunk=False,
                            has_photo=bool(images),
                        )
                        full_response = (
                            "❌ Модель слишком долго пишет ответ (оборвано на полуслове)."
                        )
                        timeout_error_was_sent = True  # → force_new_message, не тихий edit
                        if next_chunk_task and not next_chunk_task.done():
                            next_chunk_task.cancel()
                            try:
                                await next_chunk_task
                            except (asyncio.CancelledError, StopAsyncIteration):
                                pass
                            except Exception:
                                pass
                        try:
                            await stream.aclose()
                        except Exception:
                            pass
                        break

                    # Fetch tool summary if needed for intervals
                    tool_summary = ""
                    if hasattr(openclaw_client, "get_active_tool_calls_summary"):
                        try:
                            tool_summary = openclaw_client.get_active_tool_calls_summary()
                        except Exception:
                            tool_summary = ""

                    # Отслеживаем последнюю активность инструментов
                    if tool_summary != last_tool_summary:
                        last_tool_activity_ts = time.monotonic()

                    # Idle-detection: нет ни чанков, ни активности инструментов слишком долго
                    idle_sec = time.monotonic() - last_tool_activity_ts
                    if (
                        received_any_chunk  # ответ начался — ждём активности дальше
                        and not tool_summary  # нет активных tool-вызовов
                        and idle_sec >= no_tool_activity_timeout_sec
                    ):
                        logger.error(
                            "openclaw_no_tool_activity_timeout",
                            chat_id=chat_id,
                            idle_sec=round(idle_sec, 1),
                            timeout_sec=no_tool_activity_timeout_sec,
                        )
                        _hung_model = startup_route_model or "неизвестная модель"
                        full_response = (
                            f"❌ OpenClaw начал отвечать, но застрял на {int(idle_sec // 60)} мин "
                            f"(нет новых данных, нет инструментов; модель: `{_hung_model}`). "
                            "Попробуй `!reset` и повтори запрос."
                        )
                        timeout_error_was_sent = True
                        if next_chunk_task and not next_chunk_task.done():
                            next_chunk_task.cancel()
                            try:
                                await next_chunk_task
                            except (asyncio.CancelledError, StopAsyncIteration):
                                pass
                            except Exception:
                                pass
                        try:
                            await stream.aclose()
                        except Exception:
                            pass
                        break

                    handled_interval = False

                    # Handle Tool Progress
                    if elapsed_wait_sec >= next_tool_progress_sec - 1e-6:
                        handled_interval = True
                        # Поллинг задач из OpenClaw Gateway SQLite
                        gateway_tasks = poll_active_tasks()
                        gateway_progress_text = format_task_progress_for_telegram(gateway_tasks)
                        # Watchdog: все running задачи зависли > 3 мин?
                        hung_sec = check_tasks_hung(gateway_tasks, hung_threshold_sec=180.0)
                        if hung_sec is not None:
                            logger.warning(
                                "openclaw_gateway_possibly_hung",
                                stale_sec=int(hung_sec),
                                chat_id=chat_id,
                            )
                        # Hard stagnation cancel: если gateway watchdog видит, что
                        # last_event_at у running/queued задачи не обновлялся > threshold —
                        # codex-cli subprocess hung после gateway restart. Отменяем.
                        _stagnation_threshold = float(
                            getattr(
                                config,
                                "LLM_STAGNATION_THRESHOLD_SEC",
                                STAGNATION_THRESHOLD_SEC,
                            )
                        )
                        _stagnant_tasks = detect_stagnation(
                            gateway_tasks, threshold_sec=_stagnation_threshold
                        )
                        if _stagnant_tasks:
                            for _st in _stagnant_tasks:
                                _st_age_sec = int(
                                    time.time() - (_st.last_event_at_ms // 1000)
                                )
                                logger.warning(
                                    "llm_stagnation_detected",
                                    task_id=_st.task_id,
                                    label=_st.label,
                                    status=_st.status,
                                    last_event_age_sec=_st_age_sec,
                                    threshold_sec=int(_stagnation_threshold),
                                    chat_id=chat_id,
                                )
                            # Показываем owner'у что детектирована стагнация
                            stagnation_msg = (
                                f"⚠️ LLM-провайдер не отвечает "
                                f"{int(_stagnation_threshold)}+ сек. "
                                f"Запрос отменён. Попробуй снова или переключи "
                                f"модель через `!model switch <name>`."
                            )
                            if _show_progress:
                                try:
                                    if is_self:
                                        message = await self._safe_edit(
                                            message,
                                            f"🦀 {query}\n\n{stagnation_msg}",
                                        )
                                    else:
                                        temp_msg = await self._safe_edit(
                                            temp_msg, stagnation_msg
                                        )
                                except Exception as exc:  # noqa: BLE001
                                    logger.warning(
                                        "llm_stagnation_notice_delivery_failed",
                                        chat_id=chat_id,
                                        error=str(exc),
                                    )
                            # Закрываем stream + cancel pending chunk task
                            if next_chunk_task and not next_chunk_task.done():
                                next_chunk_task.cancel()
                                try:
                                    await next_chunk_task
                                except (asyncio.CancelledError, StopAsyncIteration):
                                    pass
                                except Exception:  # noqa: BLE001
                                    pass
                            try:
                                await stream.aclose()
                            except Exception:  # noqa: BLE001
                                pass
                            # Сигнализируем клиенту что нужно cancel его side
                            # (on-the-fly cleanup tool calls, active HTTP request).
                            if hasattr(openclaw_client, "cancel_current_request"):
                                try:
                                    openclaw_client.cancel_current_request()
                                except Exception:  # noqa: BLE001
                                    pass
                            # Bubble CancelledError с маркером reason — обёртка
                            # _finish_ai_request_background проверяет str(e) и не
                            # перевыбрасывает как generic error.
                            raise asyncio.CancelledError(LLM_STAGNATION_CANCEL_REASON)
                        if tool_summary or gateway_progress_text or gateway_http_dead:
                            route_meta = {}
                            if hasattr(openclaw_client, "get_last_runtime_route"):
                                try:
                                    route_meta = openclaw_client.get_last_runtime_route() or {}
                                except Exception:
                                    route_meta = {}
                            route_model = str(
                                route_meta.get("model")
                                or startup_route_model
                                or getattr(config, "MODEL", "")
                                or ""
                            ).strip()
                            route_attempt = int(route_meta.get("attempt") or 0) or None
                            progress_notice = _build_openclaw_progress_wait_notice(
                                route_model=route_model,
                                attempt=route_attempt,
                                elapsed_sec=elapsed_wait_sec,
                                notice_index=max(1, progress_notice_count),
                                tool_calls_summary=tool_summary,
                                gateway_progress=gateway_progress_text,
                                gateway_dead=gateway_http_dead,
                            )
                            if _show_progress and (
                                progress_notice != last_progress_notice_text
                                or tool_summary != last_tool_summary
                                or gateway_progress_text != last_gateway_progress
                            ):
                                try:
                                    if is_self:
                                        message = await self._safe_edit(
                                            message, f"🦀 {query}\n\n{progress_notice}"
                                        )
                                    else:
                                        temp_msg = await self._safe_edit(temp_msg, progress_notice)
                                    last_progress_notice_text = progress_notice
                                    last_tool_summary = tool_summary
                                    last_gateway_progress = gateway_progress_text
                                except Exception as exc:
                                    logger.warning(
                                        "openclaw_tool_progress_notice_delivery_failed",
                                        chat_id=chat_id,
                                        route_model=route_model,
                                        route_attempt=route_attempt,
                                        error=str(exc),
                                        has_photo=bool(images),
                                    )
                        next_tool_progress_sec = elapsed_wait_sec + tool_progress_poll_sec

                    # Handle Slow First Chunk
                    if (
                        not slow_first_chunk_notice_sent
                        and elapsed_wait_sec >= float(first_chunk_timeout_sec) - 1e-6
                    ):
                        handled_interval = True
                        slow_first_chunk_notice_sent = True
                        route_meta = {}
                        if hasattr(openclaw_client, "get_last_runtime_route"):
                            try:
                                route_meta = openclaw_client.get_last_runtime_route() or {}
                            except Exception:
                                route_meta = {}
                        route_model = str(
                            route_meta.get("model")
                            or startup_route_model
                            or getattr(config, "MODEL", "")
                            or ""
                        ).strip()
                        route_attempt = int(route_meta.get("attempt") or 0) or None
                        logger.warning(
                            "openclaw_first_chunk_slow_waiting_more",
                            chat_id=chat_id,
                            elapsed_sec=round(elapsed_wait_sec, 3),
                            soft_timeout_sec=first_chunk_timeout_sec,
                            hard_timeout_sec=buffered_response_timeout_sec,
                            route_model=route_model,
                            route_attempt=route_attempt,
                            has_photo=bool(images),
                        )
                        slow_notice = _build_openclaw_slow_wait_notice(
                            route_model=route_model,
                            attempt=route_attempt,
                        )
                        if _show_progress:
                            try:
                                if is_self:
                                    message = await self._safe_edit(
                                        message, f"🦀 {query}\n\n{slow_notice}"
                                    )
                                else:
                                    temp_msg = await self._safe_edit(temp_msg, slow_notice)
                            except Exception as exc:
                                # P0 (2026-04-09): здесь раньше был литеральный `...` как positional arg,
                                # из-за чего structlog stdlib factory падал на `event % (Ellipsis,)` →
                                # TypeError "not all arguments converted". Заменено на штатные kwargs.
                                logger.warning(
                                    "openclaw_slow_notice_delivery_failed",
                                    chat_id=chat_id,
                                    error=str(exc),
                                    error_type=type(exc).__name__,
                                )
                        # We don't continue immediately, we might have progress notice to send

                    # Handle Progress Notice Keepalive
                    if (
                        next_progress_notice_sec > 0.0
                        and elapsed_wait_sec >= next_progress_notice_sec - 1e-6
                    ):
                        handled_interval = True
                        route_meta = {}
                        if hasattr(openclaw_client, "get_last_runtime_route"):
                            try:
                                route_meta = openclaw_client.get_last_runtime_route() or {}
                            except Exception:
                                route_meta = {}
                        route_model = str(
                            route_meta.get("model")
                            or startup_route_model
                            or getattr(config, "MODEL", "")
                            or ""
                        ).strip()
                        route_attempt = int(route_meta.get("attempt") or 0) or None
                        progress_notice_count += 1
                        logger.info(
                            "openclaw_first_chunk_progress_notice",
                            chat_id=chat_id,
                            elapsed_sec=round(elapsed_wait_sec, 3),
                            notice_index=progress_notice_count,
                            route_model=route_model,
                            route_attempt=route_attempt,
                            has_photo=bool(images),
                        )
                        # Поллинг задач из OpenClaw Gateway SQLite для keepalive notice
                        _kp_gateway_tasks = poll_active_tasks()
                        _kp_gateway_progress = format_task_progress_for_telegram(_kp_gateway_tasks)
                        last_gateway_progress = _kp_gateway_progress
                        progress_notice = _build_openclaw_progress_wait_notice(
                            route_model=route_model,
                            attempt=route_attempt,
                            elapsed_sec=elapsed_wait_sec,
                            notice_index=progress_notice_count,
                            tool_calls_summary=tool_summary,
                            gateway_progress=_kp_gateway_progress,
                            gateway_dead=gateway_http_dead,
                        )
                        if _show_progress:
                            try:
                                if is_self:
                                    message = await self._safe_edit(
                                        message, f"🦀 {query}\n\n{progress_notice}"
                                    )
                                else:
                                    temp_msg = await self._safe_edit(temp_msg, progress_notice)
                                last_progress_notice_text = progress_notice
                                last_tool_summary = tool_summary
                            except Exception as exc:
                                # P0 (2026-04-09): литеральный `...` в kwargs ломал structlog stdlib.
                                # Важный инвариант: вся цепочка _run_llm_request_flow запускается в фоне
                                # через _finish_ai_request_background, и TypeError отсюда валил весь
                                # stream до получения первого chunk'а — Краб "зависал" после 15 сек.
                                logger.warning(
                                    "openclaw_progress_notice_delivery_failed",
                                    chat_id=chat_id,
                                    notice_index=progress_notice_count,
                                    error=str(exc),
                                    error_type=type(exc).__name__,
                                )
                        next_progress_notice_sec = elapsed_wait_sec + progress_notice_repeat_sec
                        next_tool_progress_sec = elapsed_wait_sec + tool_progress_poll_sec

                    # Handle Gateway HTTP Health Check (каждые 30 сек)
                    if elapsed_wait_sec >= next_gateway_http_check_sec - 1e-6:
                        handled_interval = True
                        _gw_alive = await check_gateway_http_alive()
                        if not _gw_alive:
                            if not gateway_http_dead:
                                logger.warning(
                                    "openclaw_gateway_http_not_responding",
                                    chat_id=chat_id,
                                    elapsed_sec=round(elapsed_wait_sec, 1),
                                )
                            gateway_http_dead = True
                        else:
                            if gateway_http_dead:
                                logger.info(
                                    "openclaw_gateway_http_recovered",
                                    chat_id=chat_id,
                                    elapsed_sec=round(elapsed_wait_sec, 1),
                                )
                            gateway_http_dead = False
                        next_gateway_http_check_sec = elapsed_wait_sec + gateway_http_check_interval_sec

                    if handled_interval:
                        continue

                    # If it wasn't an expected interval, and we haven't received a chunk,
                    # AND we have passed the initial slow_first_chunk_notice_sent (so wait_timeout becomes chunk_timeout_sec)
                    # then this is a real timeout:
                    if slow_first_chunk_notice_sent:
                        logger.error(
                            "openclaw_stream_chunk_timeout",
                            chat_id=chat_id,
                            timeout_sec=wait_timeout,
                            first_chunk=True,
                            has_photo=bool(images),
                        )
                        full_response = "❌ Модель отвечает слишком долго. Попробуй ещё раз или переключись на `!model cloud` / `!model local`."
                        timeout_error_was_sent = True
                        if next_chunk_task and not next_chunk_task.done():
                            next_chunk_task.cancel()
                            try:
                                await next_chunk_task
                            except (asyncio.CancelledError, StopAsyncIteration):
                                pass
                            except Exception:
                                pass
                        try:
                            await stream.aclose()
                        except Exception:
                            pass
                        break

                    # If none of the conditions triggered an abort, wait for the next timeout
                    # (This shouldn't be reached if timers are exact, but floats are floats)
                    continue

                full_response_raw += chunk
                received_any_chunk = True
                last_tool_activity_ts = time.monotonic()
                stream_display = (
                    self._extract_live_stream_text(
                        full_response_raw,
                        allow_reasoning=bool(
                            getattr(config, "TELEGRAM_STREAM_SHOW_REASONING", False)
                        ),
                    )
                    if bool(getattr(config, "STRIP_REPLY_TO_TAGS", True))
                    else full_response_raw
                )
                if stream_display:
                    full_response = stream_display

                update_interval = float(
                    getattr(config, "TELEGRAM_STREAM_UPDATE_INTERVAL_SEC", 0.75) or 0.75
                )
                update_interval = max(0.25, update_interval)
                if stream_display and (time.time() - last_edit_time > update_interval):
                    last_edit_time = time.time()
                    try:
                        display = f"{stream_display} ▌"
                        if is_self:
                            message = await self._safe_edit(message, f"🦀 {query}\n\n{display}")
                        else:
                            temp_msg = await self._safe_edit(temp_msg, display)
                    except Exception as exc:
                        logger.warning(
                            "openclaw_stream_edit_delivery_failed",
                            chat_id=chat_id,
                            error=str(exc),
                            has_photo=bool(images),
                        )
                next_chunk_task = asyncio.create_task(stream_iter.__anext__())

            if not full_response:
                full_response = self._extract_live_stream_text(
                    full_response_raw, allow_reasoning=False
                )
            if not full_response:
                full_response = "❌ Модель не вернула ответ."

            if not str(full_response).strip():
                full_response = "❌ Модель вернула пустой ответ. Попробуй повторить запрос."

            if bool(getattr(config, "STRIP_REPLY_TO_TAGS", True)):
                full_response = self._strip_transport_markup(full_response)
                if not full_response:
                    full_response = "❌ Модель вернула пустой ответ. Попробуй повторить запрос."
            full_response = self._normalize_user_visible_fallback_text(full_response)

            # Извлекаем MEDIA:-ссылки, которые OpenClaw-агент вставляет в ответ
            # когда генерирует аудио или прикрепляет файлы из workspace.
            # Формат: `MEDIA:/abs/path/to/file` на отдельной строке.
            logger.info(
                "media_parse_attempt",
                chat_id=chat_id,
                has_media_keyword="MEDIA:" in (full_response or ""),
                response_tail=repr((full_response or "")[-200:]),
            )
            _media_refs = re.findall(r"(?m)^MEDIA:\s*(\S+)\s*$", full_response)
            if _media_refs:
                logger.info("media_refs_found", chat_id=chat_id, paths=_media_refs)
                full_response = re.sub(r"(?m)^MEDIA:\s*\S+\s*$", "", full_response).strip()
            else:
                logger.info("media_refs_not_found", chat_id=chat_id)
                # Fallback: агент иногда генерирует голосовой файл, но забывает
                # вставить MEDIA:-строку в ответ (говорит "слушай ниже!" без протокола).
                # Если свежий /tmp/voice_reply.* существует (mtime < 15 мин) — инжектируем.
                if not self._looks_like_error_surface_text(full_response):
                    _now_ts = time.time()
                    _auto_voice_candidates = [
                        "/tmp/voice_reply.ogg",
                        "/tmp/voice_reply.opus",
                        "/tmp/voice_reply.mp3",
                    ]
                    for _vpath in _auto_voice_candidates:
                        try:
                            _vmtime = os.path.getmtime(_vpath)
                            # Инжектим только файлы, созданные/перезаписанные
                            # в рамках текущего LLM flow. Иначе после первого
                            # успешного голосового ответа stale-ogg переиспользуется
                            # на каждый следующий текстовый reply ("залипает звук").
                            if _vmtime < _flow_start_ts - 30:
                                logger.debug(
                                    "voice_auto_inject_skip_stale",
                                    path=_vpath,
                                    mtime=_vmtime,
                                    flow_start_ts=_flow_start_ts,
                                )
                                continue
                            _age = _now_ts - _vmtime
                            if os.path.exists(_vpath):
                                logger.info(
                                    "media_auto_inject_fallback",
                                    chat_id=chat_id,
                                    path=_vpath,
                                    age_sec=round(_age, 1),
                                )
                                _media_refs = [_vpath]
                                break
                        except OSError:
                            pass

                    # Fallback для скриншотов: агент сохраняет PNG в /tmp/ через puppeteer,
                    # но никогда не вставляет MEDIA:-строку. Ищем PNG/JPG созданные
                    # ПОСЛЕ начала текущего flow (_flow_start_ts) — это точно наши файлы.
                    if not _media_refs:
                        _screenshot_candidates: list[tuple[float, str]] = []
                        _png_magic = b"\x89PNG\r\n\x1a\n"
                        _min_image_bytes = 1024  # < 1 KB — явно не screenshot
                        try:
                            for _fname in os.listdir("/tmp"):
                                if _fname.lower().endswith((".png", ".jpg", ".jpeg")):
                                    _fpath = f"/tmp/{_fname}"
                                    try:
                                        _mtime = os.path.getmtime(_fpath)
                                        # Файл создан/изменён во время текущего LLM flow
                                        if _mtime < _flow_start_ts - 30:
                                            continue
                                        _fsize = os.path.getsize(_fpath)
                                        if _fsize < _min_image_bytes:
                                            logger.debug(
                                                "screenshot_auto_inject_skip_too_small",
                                                path=_fpath,
                                                size=_fsize,
                                            )
                                            continue
                                        # Проверяем magic bytes для PNG — защита от test-артефактов
                                        if _fname.lower().endswith(".png"):
                                            with open(_fpath, "rb") as _f:
                                                _header = _f.read(8)
                                            if _header != _png_magic:
                                                logger.debug(
                                                    "screenshot_auto_inject_skip_bad_magic",
                                                    path=_fpath,
                                                )
                                                continue
                                        _screenshot_candidates.append((_mtime, _fpath))
                                    except OSError:
                                        pass
                        except OSError:
                            pass
                        # Берём до 3 самых свежих скриншотов, от нового к старому
                        _screenshot_candidates.sort(key=lambda x: x[0], reverse=True)
                        _injected_screenshots = [p for _, p in _screenshot_candidates[:3]]
                        if _injected_screenshots:
                            logger.info(
                                "screenshot_auto_inject_fallback",
                                chat_id=chat_id,
                                paths=_injected_screenshots,
                            )
                            _media_refs = _injected_screenshots

            # Auto-retry: если full_response содержит retryable infrastructure error —
            # поднимаем LLMRetryableError. Обёртка в _finish_ai_request_background
            # поймает его и повторит запрос. Не применяется при ошибках пользователя.
            from .llm_retry import LLMRetryableError, is_retryable_error_text

            _auto_retry_count = int(getattr(config, "OPENCLAW_AUTO_RETRY_COUNT", 1))
            # Проверяем retryable условие: timeout path ИЛИ semantic error в тексте ответа
            _should_retry = (
                _auto_retry_count > 0
                and is_retryable_error_text(full_response)
                and (timeout_error_was_sent or not received_any_chunk or not full_response_raw)
            )
            if _should_retry:
                logger.info(
                    "llm_auto_retry_signal",
                    chat_id=chat_id,
                    full_response_tail=repr((full_response or "")[-200:]),
                    timeout_error_was_sent=timeout_error_was_sent,
                    received_any_chunk=received_any_chunk,
                )
                raise LLMRetryableError(
                    f"retryable_llm_error: {full_response[:120]}",
                    error_text=full_response,
                )

            full_response = self._apply_deferred_action_guard(full_response)
            self._remember_hidden_reasoning_trace(
                chat_id=chat_id,
                query=query,
                raw_response=full_response_raw,
                final_response=full_response,
                access_level=access_profile.level,
            )

            full_response = self._apply_optional_disclosure(
                chat_id=chat_id,
                text=full_response,
            )

            # Экранируем URL в группах: admin-боты (например, HOW2AI) удаляют
            # сообщения с кликабельными ссылками у не-админов. Оборачиваем в
            # бэктики — Telegram рендерит их как код, не как гиперссылку.
            _chat_type = getattr(getattr(message, "chat", None), "type", None)
            if _chat_type not in (enums.ChatType.PRIVATE, enums.ChatType.BOT):
                full_response = self.escape_urls_for_restricted_groups(full_response)

            delivery_result = await self._deliver_response_parts(
                source_message=message,
                temp_message=temp_msg,
                is_self=is_self,
                query=query,
                full_response=full_response,
                prefer_send_message_for_background=prefer_send_message_for_background,
                force_new_message=timeout_error_was_sent,
            )
            self._record_incoming_reply_to_inbox(
                incoming_item_result=incoming_item_result,
                response_text=full_response,
                delivery_result=delivery_result,
                note="llm_response_delivered_background"
                if prefer_send_message_for_background
                else "llm_response_delivered",
            )

            # Реакция ✅ — ответ доставлен успешно.
            # Только для owner (is_self), не для ошибочных ответов.
            if is_self and not timeout_error_was_sent and not self._looks_like_error_surface_text(full_response):
                asyncio.create_task(self._send_message_reaction(message, "✅"))
                _reaction_sent = True

            # Post-response relay: если Краб пообещал передать в ответе, а входящее
            # не попало в _RELAY_INTENT_KEYWORDS — форсируем relay.
            if not is_self and full_response and not timeout_error_was_sent:
                response_lower = full_response.lower()
                if any(kw in response_lower for kw in _ub._RELAY_PROMISE_IN_RESPONSE):
                    asyncio.create_task(
                        self._escalate_relay_to_owner(
                            message=message,
                            user=getattr(message, "from_user", None)
                            or getattr(message, "sender_chat", None),
                            query=query,
                            chat_type="private",
                        )
                    )

            # Forwarding B: входящее от гостя → форвардим owner-у, чтобы ничего не
            # потерялось (аптека написала "препараты приехали" — owner должен знать).
            # Пропускаем если уже сработал relay intent (pre-LLM эскалация) — там уже
            # есть уведомление, не дублируем.
            if (
                not is_self
                and access_profile.level == AccessLevel.GUEST
                and bool(getattr(config, "FORWARD_UNKNOWN_INCOMING", True))
                and full_response
                and not timeout_error_was_sent
                and not self._detect_relay_intent(query)
            ):
                asyncio.create_task(
                    self._forward_guest_incoming_to_owner(
                        message=message,
                        query=query,
                        krab_response=full_response,
                    )
                )

            # Отправляем файлы из MEDIA:-ссылок (голос, сгенерированный OpenClaw-агентом
            # через workspace, или другие вложения). Аудио-файлы идут как voice-message.
            _agent_sent_voice = False
            _audio_exts = {".mp3", ".ogg", ".opus", ".m4a", ".aac", ".wav", ".aiff"}
            _image_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
            for _mpath in _media_refs:
                if not os.path.exists(_mpath):
                    logger.warning("media_file_not_found", path=_mpath)
                    # Техошибки — только в DM владельца, не спамим в групповой чат
                    _err_chat = "me" if message.chat.id < 0 else message.chat.id
                    await self.client.send_message(
                        _err_chat, f"⚠️ Медиафайл не найден: `{_mpath}`"
                    )
                    continue
                try:
                    _ext = os.path.splitext(_mpath)[1].lower()
                    if _ext in _audio_exts:
                        await self._send_delivery_chat_action(
                            self.client,
                            message.chat.id,
                            getattr(
                                enums.ChatAction, "UPLOAD_AUDIO", enums.ChatAction.RECORD_AUDIO
                            ),
                        )
                        await self.client.send_voice(message.chat.id, _mpath)
                        _agent_sent_voice = True
                    elif _ext in _image_exts:
                        await self._send_delivery_chat_action(
                            self.client,
                            message.chat.id,
                            getattr(enums.ChatAction, "UPLOAD_PHOTO", enums.ChatAction.TYPING),
                        )
                        try:
                            await self.client.send_photo(message.chat.id, _mpath)
                        except Exception as _photo_err:  # noqa: BLE001
                            _err_str = str(_photo_err)
                            if "IMAGE_PROCESS_FAILED" in _err_str or "PHOTO_INVALID" in _err_str:
                                logger.warning(
                                    "send_photo_fallback_to_document",
                                    path=_mpath,
                                    error=_err_str,
                                )
                                await self.client.send_document(message.chat.id, _mpath)
                            else:
                                raise
                    else:
                        await self._send_delivery_chat_action(
                            self.client,
                            message.chat.id,
                            getattr(enums.ChatAction, "UPLOAD_DOCUMENT", enums.ChatAction.TYPING),
                        )
                        await self.client.send_document(message.chat.id, _mpath)
                    logger.info("media_sent", path=_mpath, ext=_ext)
                except Exception as _me:  # noqa: BLE001
                    logger.error("media_send_failed", path=_mpath, error=str(_me))
                    # Техошибки — только в DM владельца, не спамим в групповой чат
                    _err_chat = "me" if message.chat.id < 0 else message.chat.id
                    await self.client.send_message(
                        _err_chat,
                        f"⚠️ Не удалось отправить медиа `{os.path.basename(_mpath)}`: `{str(_me)[:200]}`",
                    )

            # Запускаем Python TTS (edge_tts) только если агент не прислал аудио сам.
            # Это предотвращает дублирование: оба движка не должны отвечать голосом.
            if self._should_send_voice_for_response(full_response) and not _agent_sent_voice:
                # Per-chat blocklist: в некоторых чатах TTS помечается модерацией
                # как spam → yung_nagato получал USER_BANNED_IN_CHANNEL в How2AI.
                # Пропускаем voice, но текстовая доставка продолжает работать штатно.
                if self._is_voice_blocked_for_chat(chat_id):
                    logger.info(
                        "voice_reply_skipped_blocklist",
                        chat_id=chat_id,
                        reason="chat_in_voice_blocklist",
                    )
                    return
                # B.6 capability cache: если Telegram прямо сказал что voice в этом
                # чате запрещён (ChatPermissions.can_send_voices=False или media-level
                # запрещён), тоже пропускаем voice и идём только текстом. В отличие
                # от blocklist это automatic — зависит от сервера, а не от owner.
                # Возвращается True/False/None; None == "не знаем", тогда default
                # разрешать (back-compat).
                if chat_capability_cache.is_voice_allowed(chat_id) is False:
                    logger.info(
                        "voice_reply_skipped_capability",
                        chat_id=chat_id,
                        reason="voice_disallowed_by_chat_permissions",
                    )
                    return
                # Пропускаем TTS для очень коротких ответов (< 10 символов): Telegram
                # отклоняет голосовые < ~1 секунды, что роняет весь delivery pipeline.
                _tts_text = (full_response or "").strip()
                if len(_tts_text) >= 10:
                    from ..voice_engine import text_to_speech as _text_to_speech  # noqa: I001

                    voice_path = await _text_to_speech(
                        _tts_text,
                        speed=self.voice_reply_speed,
                        voice=self.voice_reply_voice,
                    )
                    if voice_path:
                        try:
                            logger.info("tts_voice_send_attempt", chat_id=chat_id, path=voice_path)
                            await self._send_delivery_chat_action(
                                self.client,
                                message.chat.id,
                                getattr(
                                    enums.ChatAction, "UPLOAD_AUDIO", enums.ChatAction.RECORD_AUDIO
                                ),
                            )
                            await self.client.send_voice(message.chat.id, voice_path)
                            logger.info("tts_voice_send_ok", chat_id=chat_id, path=voice_path)
                        except Exception as _ve:  # noqa: BLE001
                            logger.error("tts_voice_send_failed", error=str(_ve))
                        finally:
                            if os.path.exists(voice_path):
                                os.remove(voice_path)
        finally:
            # Реакция ❌ — если ошибка и ещё не поставили ✅.
            if is_self and not _reaction_sent and (timeout_error_was_sent or not full_response):
                asyncio.create_task(self._send_message_reaction(message, "❌"))
            action_stop_event.set()
            action_task.cancel()
            await asyncio.gather(action_task, return_exceptions=True)
            # Снимаем регистрацию task у клиента: следующий flow запишет свой.
            if hasattr(openclaw_client, "register_current_request_task"):
                try:
                    openclaw_client.register_current_request_task(None)
                except Exception:  # noqa: BLE001
                    pass

    async def _finish_ai_request_background(self, **kwargs: Any) -> None:
        """Доводит long LLM/tool path до конца уже после release per-chat lock."""
        from ..core.chat_ban_cache import BANNED_ERROR_CODES, chat_ban_cache
        from .llm_retry import (
            LLMRetryableError,
            build_final_error_notice,
            build_retry_notice,
        )

        chat_id = str(kwargs.get("chat_id") or "").strip()
        incoming_item_result = kwargs.get("incoming_item_result")
        temp_msg = kwargs.get("temp_msg")

        # Конфигурация auto-retry
        max_retries = int(getattr(config, "OPENCLAW_AUTO_RETRY_COUNT", 1))
        retry_delay_sec = float(getattr(config, "OPENCLAW_AUTO_RETRY_DELAY_SEC", 2.0))
        last_retryable_error_text = ""
        retry_attempt = 0

        try:
            while True:
                try:
                    await self._run_llm_request_flow(
                        **kwargs, prefer_send_message_for_background=True
                    )
                    return  # успешно — выходим
                except asyncio.CancelledError as cancel_err:
                    # Стагнационный cancel через watchdog: сообщение пользователю уже
                    # показано внутри _run_llm_request_flow. Тихо выходим, не ретраим.
                    # Любой другой CancelledError пробрасываем наверх (как и раньше).
                    if LLM_STAGNATION_CANCEL_REASON in str(cancel_err):
                        logger.warning(
                            "llm_stagnation_cancel_handled",
                            chat_id=chat_id,
                            reason=LLM_STAGNATION_CANCEL_REASON,
                        )
                        return
                    raise
                except LLMRetryableError as retry_err:
                    last_retryable_error_text = retry_err.error_text
                    if retry_attempt >= max_retries:
                        # Все попытки исчерпаны — показываем финальную ошибку
                        logger.warning(
                            "llm_auto_retry_exhausted",
                            chat_id=chat_id,
                            attempts=retry_attempt,
                            max_retries=max_retries,
                            error_text=last_retryable_error_text[:200],
                        )
                        final_text = build_final_error_notice(
                            original_error=last_retryable_error_text,
                            attempts_made=retry_attempt,
                            max_retries=max_retries,
                        )
                        try:
                            if temp_msg is not None:
                                await self._safe_edit(temp_msg, final_text)
                        except Exception:  # noqa: BLE001
                            pass
                        return
                    # Уведомляем о retry и делаем паузу
                    retry_attempt += 1
                    notice = build_retry_notice(
                        attempt=retry_attempt,
                        max_retries=max_retries,
                        delay_sec=retry_delay_sec,
                    )
                    logger.info(
                        "llm_auto_retry_attempt",
                        chat_id=chat_id,
                        attempt=retry_attempt,
                        max_retries=max_retries,
                        delay_sec=retry_delay_sec,
                        error_text=last_retryable_error_text[:200],
                    )
                    try:
                        if temp_msg is not None:
                            temp_msg = await self._safe_edit(temp_msg, notice)
                    except Exception:  # noqa: BLE001
                        pass
                    await asyncio.sleep(retry_delay_sec)
                    # Продолжаем loop — следующая попытка
                    continue

        except Exception as exc:  # noqa: BLE001
            error_type_name = type(exc).__name__
            logger.error(
                "background_ai_request_failed",
                chat_id=chat_id,
                error=str(exc),
                error_type=error_type_name,
                traceback=traceback.format_exc(),
            )
            # Если это persistent chat-level bar (USER_BANNED_IN_CHANNEL /
            # ChatWriteForbidden / UserDeactivated / ChannelPrivate), помечаем
            # чат в ban cache, чтобы следующие сообщения из него не гоняли
            # LLM + Telegram API впустую. Это и есть цель B.8 — защита от
            # повторных отказов пока Telegram сам не снимет ограничение.
            if error_type_name in BANNED_ERROR_CODES and chat_id:
                try:
                    chat_ban_cache.mark_banned(chat_id, error_type_name)
                except Exception as _cache_exc:  # noqa: BLE001
                    logger.warning(
                        "chat_ban_cache_mark_failed",
                        chat_id=chat_id,
                        error=str(_cache_exc),
                    )
            error_text = (
                "❌ Фоновая обработка запроса завершилась ошибкой. Попробуй повторить сообщение."
            )
            try:
                if temp_msg is not None:
                    # Техошибки — только в DM владельца, не спамим в групповой чат
                    _err_chat = "me" if temp_msg.chat.id < 0 else temp_msg.chat.id
                    await self.client.send_message(_err_chat, error_text)
            except Exception as notify_exc:  # noqa: BLE001
                # silent-failure-hunter review (B.7): раньше тут был пустой pass.
                # Это значит что если и notify fails (тот же USER_BANNED_IN_CHANNEL
                # или FloodWait на error path), owner/caller никогда не узнает
                # что они не получили уведомление об ошибке. Теперь хотя бы
                # в лог попадает.
                logger.warning(
                    "background_error_notify_failed",
                    chat_id=chat_id,
                    notify_error=str(notify_exc),
                    notify_error_type=type(notify_exc).__name__,
                    original_error_type=error_type_name,
                )
            self._record_incoming_reply_to_inbox(
                incoming_item_result=incoming_item_result,
                response_text=error_text,
                delivery_result={
                    "delivery_mode": "background_error",
                    "text_message_ids": [],
                    "parts_count": 1,
                },
                note="llm_response_background_error",
            )
