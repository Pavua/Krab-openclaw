# -*- coding: utf-8 -*-
"""
Sentry инициализация — вызывается один раз при старте runtime.

Правила:
- SENTRY_DSN пустой или отсутствует → Sentry не поднимается (no-op);
- KRAB_ENV управляет тегом environment (default: production);
- traces_sample_rate=0.1 в production (10%), 1.0 в dev; override через
  SENTRY_TRACES_SAMPLE_RATE (float 0.0..1.0);
- profiles_sample_rate=0.1 в production (10%); override через
  SENTRY_PROFILES_SAMPLE_RATE — включает Performance Monitoring
  (traces + profiles) для latency tracking (memory retrieval, LLM calls);
- LoggingIntegration: level=INFO (breadcrumbs), event_level=ERROR (capture
  logger.error / logger.exception). Drop noise через before_send-фильтр
  (_BENIGN_ERROR_MARKERS).
- FastApiIntegration: 500-events из owner panel.
- AsyncioIntegration: unhandled task crashes.
- HttpxIntegration: breadcrumbs HTTP-вызовов (openclaw_client, gateway).
"""

from __future__ import annotations

import logging
import os
import threading
from collections import OrderedDict
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ── Wave 14-F (Session 33): per-session event dedupe ─────────────────────────
# Один и тот же runtime-error может уходить в Sentry сотни раз за инцидент
# (пример: 226× `db_corruption_detected_runtime` за 3.5 часа). Все 226 — same
# root cause, same stack — но Sentry квота забивается. Дедупим в рамках одного
# процесса по ключу f"{event_name}:{error_type}".
#
# Lifetime: set живёт на process lifetime; при restart Krab — fresh set
# (каждый restart получает свежий first-event signal).
# Modes (env KRAB_SENTRY_DEDUPE_MODE):
#   - "once_per_session" (default) — пускаем 1 событие, дальше None
#   - "every_nth"  — sample 1 of every N (KRAB_SENTRY_DEDUPE_EVERY_NTH=10)
#   - "disabled"   — current pre-Wave 14-F behavior, всё пропускаем
# LRU eviction at KRAB_SENTRY_DEDUPE_MAX_SIZE (default 100).
_DEDUPE_LOCK = threading.Lock()
_session_seen_events: OrderedDict[str, int] = OrderedDict()


def _read_int_env(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= minimum else default


def _dedupe_key(event: dict[str, Any]) -> str | None:
    """Compute key f"{event_name}:{error_type}" for dedupe.

    event_name = transaction OR logger OR message-prefix (fallback).
    error_type = exception type (если есть), иначе "log".
    Возвращает None если ничего вменяемого не извлекли — тогда не дедупим.
    """
    try:
        exc_type = ""
        for ex in (event.get("exception") or {}).get("values") or []:
            if isinstance(ex, dict) and ex.get("type"):
                exc_type = str(ex.get("type"))
                break
        event_name = (
            str(event.get("transaction") or "").strip() or str(event.get("logger") or "").strip()
        )
        if not event_name:
            msg = event.get("message")
            if isinstance(msg, str) and msg:
                event_name = msg[:80]
            else:
                logentry = event.get("logentry") or {}
                if isinstance(logentry, dict):
                    raw = str(logentry.get("message") or "").strip()
                    if raw:
                        event_name = raw[:80]
        if not event_name and not exc_type:
            return None
        return f"{event_name or 'unknown'}:{exc_type or 'log'}"
    except Exception:  # noqa: BLE001
        return None


def _should_dedupe_drop(event: dict[str, Any]) -> bool:
    """True → suppress (return None в _before_send). False → пропустить."""
    mode = os.getenv("KRAB_SENTRY_DEDUPE_MODE", "once_per_session").strip().lower()
    if mode == "disabled":
        return False
    key = _dedupe_key(event)
    if key is None:
        return False
    max_size = _read_int_env("KRAB_SENTRY_DEDUPE_MAX_SIZE", 100, minimum=1)
    every_nth = _read_int_env("KRAB_SENTRY_DEDUPE_EVERY_NTH", 10, minimum=1)
    with _DEDUPE_LOCK:
        if key in _session_seen_events:
            count = _session_seen_events[key] + 1
            _session_seen_events[key] = count
            _session_seen_events.move_to_end(key)
            if mode == "every_nth":
                # 1-я уже прошла (count=1 was sent). Теперь sample каждое N-е.
                # i.e. count in {1+N, 1+2N, ...} → пускаем; иначе drop.
                return ((count - 1) % every_nth) != 0
            # once_per_session: всё после первого — drop.
            return True
        # Первое появление ключа — добавляем, пускаем.
        _session_seen_events[key] = 1
        # LRU evict (oldest) если набрали слишком много.
        while len(_session_seen_events) > max_size:
            _session_seen_events.popitem(last=False)
        return False


def _reset_dedupe_state_for_tests() -> None:
    """Test-only helper: сбрасывает накопленные ключи между прогонами."""
    with _DEDUPE_LOCK:
        _session_seen_events.clear()


# Маркеры benign-ошибок, которые НЕ должны попадать в Sentry.
# Все три — transient HTTPException во время Krab boot (15-30s):
#   userbot_not_ready: 503 пока userbot ещё не connected;
#   router_not_configured: 503 пока model_router не успел init;
#   Client has not been started yet: pyrogram client startup race;
# Проявляются если запрос приходит в окно ~5-15s после старта web_app, до
# полной инициализации userbot/router. Не runtime bug — клиент должен retry
# по Retry-After.
_BENIGN_ERROR_MARKERS: tuple[str, ...] = (
    "userbot_not_ready",
    "router_not_configured",
    "Client has not been started yet",
    # Pyrogram storage race во время shutdown: внутренние Session-task'и
    # (restart/update_peers/_get) добегают до уже закрытой sqlite-базы.
    # Корневой race закрыт guard'ом в SessionMixin (см. session.py), а здесь
    # дропаем оставшийся шум, который генерируется внутри pyrogram-фоновых
    # задач уже после client.stop() и не подавляется нашим try/except.
    "Cannot operate on a closed database",
    # Session 28: chat-level баны и slowmode-ограничения. Это ожидаемые
    # ответы Telegram, а не runtime-баги — chat_ban_cache их уже обрабатывает
    # (mark_banned + silent skip incoming). Не нужно слать в Sentry повторно.
    # См. src/core/chat_ban_cache.py + Sentry issues PYTHON-FASTAPI-6J/6H.
    "USER_BANNED_IN_CHANNEL",
    "UserBannedInChannel",
    "ChatWriteForbidden",
    "You are limited from sending messages",
    # Pyrogram storage race на client.start() / restart_userbot: внутренний
    # Session-task возвращает None где ожидается int, и AttributeError
    # 'NoneType' object has no attribute 'to_bytes' всплывает из глубин
    # pyrogram. Это race между peer DC resolve и storage close — runtime
    # сам recovery'ится через retry. См. Sentry PYTHON-FASTAPI-6G.
    "'NoneType' object has no attribute 'to_bytes'",
    # PEER_ID_INVALID: Telegram отклоняет запрос если chat_id не известен
    # серверу (бот ни разу не взаимодействовал с этим peer'ом). Это штатная
    # ситуация — например при попытке ответить в чат, куда ещё не попали
    # сообщения. Не runtime-ошибка, не требует investigate в Sentry.
    "PEER_ID_INVALID",
    # NOTE: bare "CancelledError" marker удалён в Wave 6 (Session 33).
    # Раньше глотал ВСЕ asyncio.CancelledError, что создавало blind spot для
    # timeout-induced cancellations из asyncio.wait_for(...) в LLM/MCP/memory
    # путях. Теперь suppression только если cancel пришёл из app-shutdown
    # lifespan — см. _is_shutdown_cancelled_error().
    # DB corruption quarantine circuit breaker — ожидаемое поведение при
    # обнаружении повреждения БД: система сама переходит в safe-режим.
    # Не требует отдельного алерта в Sentry — уже логируется локально.
    "db_corruption_quarantined",
    # asyncio shutdown noise: задача была уничтожена пока находилась в pending
    # состоянии. Генерируется Python при gc pending tasks во время event loop
    # teardown (Sentry issue 6T, 3 events). Штатное поведение при остановке.
    "Task was destroyed but it is pending",
    # Session 33 Wave 5: main session integrity preflight events. Локально
    # логируются + при необходимости escalate через capture_message с tag
    # db_corruption=true (см. report_corruption_to_sentry). Не дублируем
    # шум через logging integration.
    "main_session_integrity_ok",
    "main_session_integrity_failed",
    "main_session_recovered_auto",
)


def _is_shutdown_cancelled_error(event: dict[str, Any], hint: dict[str, Any] | None = None) -> bool:
    """Return True only if CancelledError originated from app shutdown lifespan.

    Frame-aware narrowing: бывший bare-string маркер "CancelledError" глотал
    ВСЕ asyncio.CancelledError, включая timeout-induced cancellations из
    asyncio.wait_for(...) в LLM/MCP/memory путях. Теперь подавляем только если
    cancel пришёл из uvicorn/starlette lifespan teardown.

    Defensive: на любом неожиданном shape события возвращаем False (не
    глотаем — пусть событие дойдёт до Sentry, чем мы пропустим реальный bug).

    Session 39: расширено для logger.error events (uvicorn/starlette logger
    — 387 событий PYTHON-FASTAPI-Z за 14 дней утекали мимо фильтра потому что
    LoggingIntegration выставляет другие поля события — нет "exception" frames,
    но есть logger="uvicorn.error" + transaction может быть пуст. Проверяем
    также threads frames + breadcrumbs trace description.
    """
    try:
        # Fast path 1: transaction set FastAPI/Starlette на "lifespan" во время shutdown.
        transaction = str(event.get("transaction") or "").lower()
        if "lifespan" in transaction:
            return True
        # Fast path 2: logger=uvicorn.error/uvicorn.lifespan/starlette.* + value содержит CancelledError.
        # logger.error("Traceback...") path — Sentry не извлекает frames в exception блок.
        logger_name = str(event.get("logger") or "").lower()
        if logger_name in ("uvicorn.error", "uvicorn.lifespan", "uvicorn.lifespan.on"):
            return True
        # Fast path 3: trace context description "LifespanOn.*" (видно в Sentry для shutdown events).
        contexts = event.get("contexts") or {}
        if isinstance(contexts, dict):
            trace = contexts.get("trace") or {}
            if isinstance(trace, dict):
                description = str(trace.get("description") or "").lower()
                if "lifespan" in description:
                    return True
        # Slow path: проверяем верхние ~5 кадров стека в exception И threads.
        for collection_key in ("exception", "threads"):
            values = (event.get(collection_key) or {}).get("values") or []
            for v in values:
                if not isinstance(v, dict):
                    continue
                frames = (v.get("stacktrace") or {}).get("frames") or []
                if not isinstance(frames, list):
                    continue
                for frame in reversed(frames[-5:]):
                    if not isinstance(frame, dict):
                        continue
                    filename = str(frame.get("abs_path") or frame.get("filename") or "").lower()
                    if "starlette/routing" in filename or "uvicorn/lifespan" in filename:
                        return True
        return False
    except Exception:  # noqa: BLE001
        # Defensive: never swallow if we can't tell.
        return False


def _before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    """Drop benign events (например userbot_not_ready во время boot).

    Sentry hook: возвращает None → событие не отправляется.
    """
    try:
        # 1. Прямая проверка extra.error_code
        extra = event.get("extra") or {}
        if isinstance(extra, dict):
            error_code = str(extra.get("error_code") or "").strip()
            if error_code in _BENIGN_ERROR_MARKERS:
                return None

        # 2. HTTPException(503, "userbot_not_ready") — detail попадает в exception value.
        #    Дополнительная frame-aware проверка для CancelledError (см.
        #    _is_shutdown_cancelled_error) — подавляем только shutdown-cancellations.
        for ex in (event.get("exception", {}) or {}).get("values", []) or []:
            if not isinstance(ex, dict):
                continue
            value = str(ex.get("value") or "")
            ex_type = str(ex.get("type") or "")
            # Спец-кейс: CancelledError → frame-aware narrowing.
            # ex_type может быть "CancelledError" или qualified name типа
            # "asyncio.exceptions.CancelledError" — поэтому substring match.
            if "CancelledError" in ex_type or "CancelledError" in value:
                if _is_shutdown_cancelled_error(event, hint):
                    return None
                # Otherwise — let it through, runtime cancel timeouts must be visible.
                continue
            for marker in _BENIGN_ERROR_MARKERS:
                if marker in value or marker in ex_type:
                    return None

        # 3. logentry / message — на случай если warning попал через logging integration
        message = event.get("message")
        if isinstance(message, str):
            for marker in _BENIGN_ERROR_MARKERS:
                if marker in message:
                    return None
    except Exception:  # noqa: BLE001
        # Никогда не ломаем error reporting из-за бага в фильтре.
        return event
    # Wave 14-F: per-session dedupe — после benign-фильтра, чтобы дедуп
    # применялся только к событиям, которые иначе бы реально ушли в Sentry.
    try:
        if _should_dedupe_drop(event):
            return None
    except Exception:  # noqa: BLE001
        # Defensive: dedupe-bug не должен топить error reporting.
        return event
    return event


def _read_float_env(name: str, default: float) -> float:
    """Читает float из env с safe-clamp в [0.0, 1.0]. Invalid → default."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def init_sentry() -> bool:
    """
    Инициализирует Sentry SDK если SENTRY_DSN задан.
    Возвращает True если SDK поднят, False если пропущен.
    """
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        logger.debug("sentry_skipped", reason="SENTRY_DSN not set")
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration

        # Опциональные integrations: если import падает (старая версия sentry-sdk
        # или extras не установлены) — продолжаем с тем что есть, не валим init.
        integrations: list[Any] = [
            LoggingIntegration(
                level=logging.INFO,  # breadcrumbs от INFO+
                event_level=logging.ERROR,  # capture logger.error / logger.exception
            ),
        ]
        try:
            from sentry_sdk.integrations.fastapi import FastApiIntegration

            integrations.append(FastApiIntegration(transaction_style="endpoint"))
        except Exception as exc:  # noqa: BLE001
            logger.debug("sentry_integration_skipped", name="fastapi", error=str(exc))
        try:
            from sentry_sdk.integrations.asyncio import AsyncioIntegration

            integrations.append(AsyncioIntegration())
        except Exception as exc:  # noqa: BLE001
            logger.debug("sentry_integration_skipped", name="asyncio", error=str(exc))
        try:
            from sentry_sdk.integrations.httpx import HttpxIntegration

            integrations.append(HttpxIntegration())
        except Exception as exc:  # noqa: BLE001
            logger.debug("sentry_integration_skipped", name="httpx", error=str(exc))

        # default: production — чтобы prod-state не проваливался в dev-bucket
        env = os.getenv("KRAB_ENV", "production").strip()
        default_sample = 1.0 if env == "dev" else 0.1
        traces_sample_rate = _read_float_env("SENTRY_TRACES_SAMPLE_RATE", default_sample)
        profiles_sample_rate = _read_float_env("SENTRY_PROFILES_SAMPLE_RATE", default_sample)
        release = f"krab@{os.getenv('KRAB_VERSION', 'dev')}"

        sentry_sdk.init(
            dsn=dsn,
            environment=env,
            release=release,
            traces_sample_rate=traces_sample_rate,
            profiles_sample_rate=profiles_sample_rate,
            integrations=integrations,
            # Не шлём PII (Telegram user data может оседать в locals)
            send_default_pii=False,
            # Доп. защита: не слать локальные переменные (могут содержать tokens).
            include_local_variables=False,
            # Drop benign transient ошибки (userbot_not_ready во время boot)
            before_send=_before_send,
        )
        try:
            sentry_sdk.set_tag("agent_kin", "krab")
            sentry_sdk.set_tag("service", "krab-main")
        except Exception as exc:  # noqa: BLE001
            logger.debug("sentry_set_tag_failed", error=str(exc))
        logger.info(
            "sentry_initialized",
            environment=env,
            release=release,
            traces_sample_rate=traces_sample_rate,
            profiles_sample_rate=profiles_sample_rate,
            integrations=[type(i).__name__ for i in integrations],
        )
        return True
    except Exception as exc:  # noqa: BLE001
        # Sentry не должна ронять runtime при сбое инициализации
        logger.warning("sentry_init_failed", error=str(exc))
        return False
