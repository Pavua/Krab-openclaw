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
import re
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ── Wave 44-E: PII redaction ────────────────────────────────────────────────
# До Wave 44-E активный bootstrap/sentry_init.py НЕ редактировал PII — любой
# logger.error содержащий TG bot token / API key / Bearer token утекал raw в
# Sentry SaaS. Старый src/core/sentry_integration.py имел такие паттерны, но
# был deprecated и не подключён к boot path. Порт сюда закрывает security gap.
#
# Порядок паттернов важен: специфичные раньше общих. Маркер `<TG_BOT_TOKEN>`
# / `<GOOGLE_API_KEY>` / `<API_KEY>` / `Bearer <TOKEN>` совпадает по форме
# с устаревшим redactor для совместимости тестов и логов.
_PII_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Bot API / Telegram токены: <numeric_id>:<35+ char alphanumeric>
    (re.compile(r"\b\d{9,11}:[A-Za-z0-9_-]{30,}\b"), "<TG_BOT_TOKEN>"),
    # Google API ключи: AIza… (35-39 chars обычно, жадно до границы)
    (re.compile(r"\bAIza[0-9A-Za-z_-]{20,}"), "<GOOGLE_API_KEY>"),
    # OpenAI / Anthropic / generic provider keys: sk-…
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), "<API_KEY>"),
    # Sentry DSN
    (re.compile(r"https://[a-zA-Z0-9]{16,}@[a-zA-Z0-9]+\.ingest\.[a-z.]+/\d+"), "<SENTRY_DSN>"),
    # Bearer / OAuth header tokens
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9_.\-=]{20,}"), "Bearer <TOKEN>"),
    # Wave 102: email — ДО phone чтобы домен с цифрами не сматчился phone'ом.
    (re.compile(r"[\w._%+-]+@[\w.-]+\.[A-Za-z]{2,}"), "<email>"),
    # Wave 102: phone — international +XXX или local 10-15 digits.
    (re.compile(r"\+?\d{10,15}"), "<phone>"),
)
# Маркер уже редактированной строки — для идемпотентности.
_REDACTED_MARKER_RE: re.Pattern[str] = re.compile(
    r"<(?:TG_BOT_TOKEN|GOOGLE_API_KEY|API_KEY|SENTRY_DSN|TOKEN|email|phone|user_id)>"
)

# Wave 102: size caps + user_id allowlist для агрессивной редакции.
_MESSAGE_MAX_LEN = 2000
_EXTRA_PAYLOAD_MAX_LEN = 5000
_USER_ID_EXTRA_KEYS: frozenset[str] = frozenset(
    {"user_id", "from_id", "chat_id", "peer_id", "telegram_user_id", "tg_user_id", "sender_id"}
)
_USER_ID_DIGIT_RE = re.compile(r"(?<!\d)\d{5,}(?!\d)")


def _redact_user_id_field(value: Any) -> Any:
    """Wave 102: int ≥10000 → <user_id>, str digits substitute, else passthrough."""
    if isinstance(value, int) and value >= 10000:
        return "<user_id>"
    if isinstance(value, str):
        return _USER_ID_DIGIT_RE.sub("<user_id>", value)
    return value


def _truncate_text(value: str, limit: int) -> str:
    """Wave 102: truncate с marker'ом + tail для context (последние 64 char)."""
    if not isinstance(value, str) or len(value) <= limit:
        return value
    head = value[: max(0, limit - 100)]
    tail = value[-64:]
    return f"{head}…<truncated {len(value)} chars>…{tail}"


def _redact_string(s: str) -> str:
    """Применяет все PII-паттерны к строке. Idempotent: если строка уже
    содержит только маркер — паттерны её не матчат повторно."""
    if not isinstance(s, str) or not s:
        return s or ""
    for pat, repl in _PII_PATTERNS:
        s = pat.sub(repl, s)
    return s


def _redact_dict(d: dict[str, Any]) -> dict[str, Any]:
    """Рекурсивный redact values в dict. Keys не трогаем (структура)."""
    out: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, str):
            out[k] = _redact_string(v)
        elif isinstance(v, dict):
            out[k] = _redact_dict(v)
        elif isinstance(v, list):
            out[k] = [_redact_string(x) if isinstance(x, str) else x for x in v]
        else:
            out[k] = v
    return out


def _apply_pii_redaction(event: dict[str, Any]) -> None:
    """In-place редакция PII во всех полях события Sentry, где может оседать
    raw текст: message, logentry.message, exception.value, breadcrumbs, extra,
    tags. Defensive: любой неожиданный shape — skip без exception."""
    try:
        if isinstance(event.get("message"), str):
            event["message"] = _redact_string(event["message"])
        logentry = event.get("logentry")
        if isinstance(logentry, dict) and isinstance(logentry.get("message"), str):
            logentry["message"] = _redact_string(logentry["message"])
        for ex in (event.get("exception", {}) or {}).get("values", []) or []:
            if isinstance(ex, dict) and isinstance(ex.get("value"), str):
                ex["value"] = _redact_string(ex["value"])
        for crumb in (event.get("breadcrumbs", {}) or {}).get("values", []) or []:
            if isinstance(crumb, dict):
                if isinstance(crumb.get("message"), str):
                    crumb["message"] = _redact_string(crumb["message"])
                if isinstance(crumb.get("data"), dict):
                    crumb["data"] = _redact_dict(crumb["data"])
        if isinstance(event.get("extra"), dict):
            event["extra"] = _redact_dict(event["extra"])
            # Wave 102: aggressive user_id redact для extra/tags allowlist keys.
            for k in list(event["extra"].keys()):
                if k in _USER_ID_EXTRA_KEYS:
                    event["extra"][k] = _redact_user_id_field(event["extra"][k])
            # Wave 102: cap extra.payload (most common bloat source).
            if isinstance(event["extra"].get("payload"), str):
                event["extra"]["payload"] = _truncate_text(
                    event["extra"]["payload"], _EXTRA_PAYLOAD_MAX_LEN
                )
        if isinstance(event.get("tags"), dict):
            event["tags"] = _redact_dict(event["tags"])
            for k in list(event["tags"].keys()):
                if k in _USER_ID_EXTRA_KEYS:
                    event["tags"][k] = _redact_user_id_field(event["tags"][k])
        # Wave 102: cap top-level message.
        if isinstance(event.get("message"), str):
            event["message"] = _truncate_text(event["message"], _MESSAGE_MAX_LEN)
    except Exception:  # noqa: BLE001
        # Никогда не ломаем error reporting из-за бага в редакции.
        pass


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
    # Wave (08.05.2026): Telegram MTProto штатные ситуации — handled через
    # rate_limiter / relogin / retry. Не runtime bugs.
    "FLOOD_WAIT_",
    "FloodWait",
    "AUTH_KEY_UNREGISTERED",
    "SESSION_REVOKED",
    "SESSION_PASSWORD_NEEDED",
    # Asyncio shutdown noise — handled через graceful teardown.
    "Event loop is closed",
    # Google Vertex AI / Anthropic transient — handled через retry/fallback.
    # См. EXPECTED_ERROR_PATTERNS в src/integrations/_bypass_perf.py.
    "DEADLINE_EXCEEDED",
    "RESOURCE_EXHAUSTED",
    "503 Service Unavailable",
    # Network reconnect noise (pyrogram/httpx) — handled через retry.
    "Network is unreachable",
    "Connection reset by peer",
    "ConnectionResetError",
    # Wave (08.05.2026): cli_subprocess noise — codex-cli sandbox falls back
    # to cloud routing on Telegram queries (telegram_query_detector).
    # Non-zero exit handled by retry layer.
    "cli_subprocess_nonzero_exit",
    # Wave 44-E (09.05.2026): split_brain markers — наши собственные intentional
    # escalations при detection split-brain (telegram updates flow stalled).
    # logger.error используется чтобы LaunchAgent перезапустил процесс через
    # _launchd_exit_78 — это by design, не Sentry-bug. PYTHON-FASTAPI-87 захватывал
    # эти события как "новые issue" каждый раз. Локально через structlog логи
    # сохраняются (фильтр работает только на Sentry capture).
    "split_brain_reconnect_did_not_restore_updates",
    "split_brain_escalation",
    # Wave 43-Z (09.05.2026): pyrogram-internal TCP StreamReader concurrency race.
    # Session.restart() вызывается через self.loop.create_task(self.restart()) из
    # handle_packet при ConnectionResetError. Несколько конкурентных задач пытаются
    # restart одновременно → два recv_worker() читают из одного StreamReader.
    # Это pyrogram-internal bug (pyrofork), не наш код. Krab обрабатывает через
    # _stop_swarm_team_clients / reserve_bot stop с per-team try/except.
    # Sentry issues: PYTHON-FASTAPI-4T (5 events) + PYTHON-FASTAPI-4S (3 events).
    "read() called while another coroutine is already waiting for incoming data",
    # Wave 170 (13.05.2026): working-as-designed defence-in-depth маркеры.
    # paid_gemini_guard_triggered: Wave 67 guard блокирует paid AI Studio запросы
    #   после отключения billing — ожидаемое поведение, не runtime-bug. Источник
    #   src/integrations/paid_gemini_guard.py (~17 events / 2h до даунгрейда).
    # telegram_session_zombie_escalation: intentional escalation когда session
    #   обнаружен в zombie state — LaunchAgent перезапускает процесс. Аналог
    #   split_brain_escalation выше.
    # macos_post_sleep_reinit_failed: ожидаемая транзитная ошибка после wake
    #   из sleep — retry layer успешно её разрешает. Не Sentry-bug.
    # Прецедент даунгрейда уровня логов: Wave 41-O (openclaw 500 → warning).
    "paid_gemini_guard_triggered",
    "telegram_session_zombie_escalation",
    "macos_post_sleep_reinit_failed",
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


def _is_pytest_event(event: dict[str, Any]) -> bool:
    """Return True если событие пришло из pytest-сессии (нужно дропнуть).

    Session 40: тестовые события утекали в Sentry production проект через
    общий SENTRY_DSN (см. PYTHON-FASTAPI-83/84/85/63). Маркеры:

    1. ``request.url`` начинается с ``http://testserver/`` — FastAPI TestClient
       prepends этот hostname для всех симулированных HTTP requests.
    2. Любое поле в ``extra`` содержит подстроку ``pytest-of-`` или
       ``popen-gw`` — это путь к tmp-директории pytest или xdist worker.
    3. ``sys.argv == ['-c']`` — pytest-xdist subprocess сигнатура (worker
       запускается через ``python -c "..."``, отсюда argv == ['-c']).

    Defensive: на любом неожиданном shape возвращаем False (не глотаем
    реальный prod event, лучше получить шумное событие чем потерять bug).

    Note: НЕ используем env var ``PYTEST_CURRENT_TEST`` для дополнительной
    проверки — pytest сам её всегда выставляет в своих session, поэтому
    она бесполезна для фильтрации event'ов которые real production code
    шлёт во время unit-тестов (например при тестировании Sentry-фильтра).
    """
    try:
        # 1. testserver URL — FastAPI TestClient
        request = event.get("request") or {}
        if isinstance(request, dict):
            url = str(request.get("url") or "")
            if url.startswith("http://testserver"):
                return True

        # 2. pytest paths в extra
        extra = event.get("extra") or {}
        if isinstance(extra, dict):
            for value in extra.values():
                value_str = str(value)
                if "pytest-of-" in value_str or "/popen-gw" in value_str:
                    return True
            # sys.argv == ['-c'] — pytest-xdist worker subprocess
            argv = extra.get("sys.argv")
            if isinstance(argv, list) and argv == ["-c"]:
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
        # 0. Pytest pollution filter (Session 40) — дропаем все события из
        #    тестовых сред чтобы не светить test-injected `:boom`, missing dirs
        #    в production Sentry проекте.
        if _is_pytest_event(event):
            return None

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
            # Wave 43-Z (09.05.2026): LoggingIntegration пишет uvicorn.error logger.error()
            # как logentry event без exception block. CancelledError от shutdown попадает
            # в message в виде "...asyncio.exceptions.CancelledError" в полном traceback.
            # _is_shutdown_cancelled_error не вызывался для logentry-path — root cause
            # PYTHON-FASTAPI-Z (390 events). Фиксируем: если CancelledError в тексте
            # и logger — uvicorn → применяем frame-aware narrowing.
            if "CancelledError" in message:
                if _is_shutdown_cancelled_error(event, hint):
                    return None
            for marker in _BENIGN_ERROR_MARKERS:
                if marker in message:
                    return None
        # 3b. logentry структурный — Sentry LoggingIntegration может положить message
        #     в event["logentry"]["message"] (dict) вместо event["message"] (str).
        logentry = event.get("logentry") or {}
        if isinstance(logentry, dict):
            le_msg = str(logentry.get("message") or "")
            if le_msg:
                if "CancelledError" in le_msg:
                    if _is_shutdown_cancelled_error(event, hint):
                        return None
                for marker in _BENIGN_ERROR_MARKERS:
                    if marker in le_msg:
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
    # Wave 44-E: PII redaction перед отправкой. In-place — мутирует event
    # фильтруя tokens/keys/Bearer/DSN из всех текстовых полей. Defensive:
    # внутренние exceptions проглочены, чтобы не ломать reporting.
    _apply_pii_redaction(event)
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


def _detect_git_release() -> str:
    """Определяет release-идентификатор для Sentry на основе git-состояния.

    Цель (Session 40): включить Sentry "auto-close on release deploy" — для
    этого release должен меняться при каждом коммите. Раньше release был
    статичным "dev", из-за чего issues с тэгом "Fixes: PYTHON-FASTAPI-XX"
    в commit message не закрывались автоматически.

    Стратегия:
    1. ``KRAB_VERSION`` env (если задан явно) — высший приоритет (handled
       выше в init_sentry).
    2. ``git rev-parse --short HEAD`` из репо — обычный prod путь.
    3. ``dev`` fallback — если git недоступен (worktree без .git, или
       не установлен subprocess timeout).

    Subprocess вызов timeout=2s чтобы не блокировать boot.
    """
    try:
        import subprocess  # noqa: PLC0415

        repo_root = Path(__file__).resolve().parents[2]
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        sha = result.stdout.strip()
        if sha and result.returncode == 0:
            return sha
    except Exception:  # noqa: BLE001
        pass
    return "dev"


def init_sentry() -> bool:
    """
    Инициализирует Sentry SDK если SENTRY_DSN задан.
    Возвращает True если SDK поднят, False если пропущен.

    Wave (08.05.2026): добавлен fallback на KRAB_SENTRY_DSN — раньше эта env
    игнорировалась, и если кто-то выставлял её как override (например в plist)
    — Sentry не поднимался. Теперь читаем оба варианта.
    """
    dsn = os.getenv("SENTRY_DSN", "").strip() or os.getenv("KRAB_SENTRY_DSN", "").strip()
    if not dsn:
        logger.debug("sentry_skipped", reason="SENTRY_DSN/KRAB_SENTRY_DSN not set")
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
        release = f"krab@{os.getenv('KRAB_VERSION', '') or _detect_git_release()}"

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
