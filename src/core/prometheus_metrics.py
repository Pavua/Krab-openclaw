# -*- coding: utf-8 -*-
"""
Prometheus metrics для Krab — simple text format (без prometheus_client).

Собираем счётчики/гейджи вручную и отдаём в text/plain version=0.0.4.
Все импорты опциональных модулей завёрнуты в try/except — missing модули
не ломают /metrics endpoint.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

# Процесс стартовал в этот момент (unix ts). Используется для krab uptime gauge.
_PROCESS_START_TIME: float = time.time()

# Счётчик использований adaptive rerank (mutable singleton для hot-path инкремента).
_ADAPTIVE_RERANK_COUNTER: list[int] = [0]

# Security: счётчик пропущенных LLM-ответов гостям в группах (SwMaster incident 2026-04-21).
# Словарь reason → count. Инкрементируется из userbot_bridge._process_message_serialized.
_GUEST_LLM_SKIPPED_COUNTER: dict[str, int] = {}

# Telegram FloodWait counter (alert TelegramRateLimited).
# Словарь caller → count. Инкрементируется из error_handler.safe_handler и
# других мест где ловится FloodWait. caller — free-form идентификатор
# (имя handler'а или "voice_profile.refresh"). Pre-registered с пустым
# словарём → /metrics всегда отдаёт # TYPE строку, alert не «мёртвый».
_TELEGRAM_FLOOD_WAIT_COUNTER: dict[str, int] = {}

# Session corruption counter. Инкрементируется из db_corruption_guard при
# обнаружении corruption и quarantine. Словарь kind → count (session/archive/…).
_SESSION_CORRUPTION_COUNTER: dict[str, int] = {}

# Startup duration (секунды от первого байта main.py до kraab_running).
# Выставляется однократно из bootstrap/runtime.py после kraab.start().
# 0.0 означает «ещё не замерено».
_STARTUP_DURATION_SECONDS: list[float] = [0.0]

# Agent Engine run counters (Phase C / Wave 17-B).
# {engine: {success: count}} — инкрементируются из record_agent_engine_metrics().
_AGENT_ENGINE_RUNS_COUNTER: dict[str, dict[str, int]] = {}

# Agent Engine fallback counter.
# {from_engine: {to_engine: count}} — fallback при unhealthy Hermes.
_AGENT_ENGINE_FALLBACK_COUNTER: dict[str, dict[str, int]] = {}

# Agent Engine latency (накопитель для avg — list сумм и count).
# {engine: [total_latency_sec, count]}
_AGENT_ENGINE_LATENCY_ACC: dict[str, list[float]] = {}


def inc_telegram_flood_wait(caller: str) -> None:
    """Инкремент krab_telegram_flood_wait_total{caller=...}.

    Безопасно вызывать из любого FloodWait-handler'а. Не бросает, не I/O.
    """
    key = (caller or "unknown")[:80]
    _TELEGRAM_FLOOD_WAIT_COUNTER[key] = _TELEGRAM_FLOOD_WAIT_COUNTER.get(key, 0) + 1


def inc_session_corruption(kind: str) -> None:
    """Инкремент krab_session_corruption_total{kind=...}.

    Вызывается из db_corruption_guard при обнаружении corruption и quarantine.
    kind ∈ {session, archive, ...} — строка из KnownDb.kind.
    Не бросает, не I/O.
    """
    key = (kind or "unknown")[:40]
    _SESSION_CORRUPTION_COUNTER[key] = _SESSION_CORRUPTION_COUNTER.get(key, 0) + 1


def set_startup_duration(elapsed_sec: float) -> None:
    """Выставляет krab_startup_duration_seconds.

    Вызывается однократно из bootstrap/runtime.py после kraab.start().
    Повторный вызов перезаписывает значение (рестарт в рамках одного процесса).
    Не бросает.
    """
    try:
        _STARTUP_DURATION_SECONDS[0] = max(0.0, float(elapsed_sec))
    except Exception:  # noqa: BLE001
        pass


def record_agent_engine_run(
    engine: str,
    success: bool,
    latency_sec: float,
) -> None:
    """Инкремент krab_agent_engine_runs_total и накопитель latency.

    engine ∈ {'openclaw', 'hermes'}. Вызывается из llm_flow engine dispatch.
    Не бросает, не I/O.
    """
    try:
        key = (engine or "unknown")[:40]
        success_label = "1" if success else "0"
        engine_bucket = _AGENT_ENGINE_RUNS_COUNTER.setdefault(key, {})
        engine_bucket[success_label] = engine_bucket.get(success_label, 0) + 1

        acc = _AGENT_ENGINE_LATENCY_ACC.setdefault(key, [0.0, 0])
        acc[0] += max(0.0, latency_sec)
        acc[1] += 1
    except Exception:  # noqa: BLE001
        pass


def record_agent_engine_fallback(from_engine: str, to_engine: str) -> None:
    """Инкремент krab_agent_engine_fallback_total{from_engine, to_engine}.

    Вызывается из agent_engine_resolver при fallback Hermes → OpenClaw.
    Не бросает.
    """
    try:
        from_key = (from_engine or "unknown")[:40]
        to_key = (to_engine or "unknown")[:40]
        bucket = _AGENT_ENGINE_FALLBACK_COUNTER.setdefault(from_key, {})
        bucket[to_key] = bucket.get(to_key, 0) + 1
    except Exception:  # noqa: BLE001
        pass


# === Wave 20-B: Google direct bypass метрики. ===
# Счётчик invocations + histogram latency + histogram thoughts-tokens.
# Если prometheus_client недоступен — все объекты None, helper record_google_bypass_call()
# делает None-check → no-op. Никогда не ломает hot path.
try:
    from prometheus_client import Counter as _CounterBypass  # type: ignore[import-not-found]
    from prometheus_client import Histogram as _HistogramBypass  # type: ignore[import-not-found]

    # Счётчик по модели и исходу (success / empty / error / fallback).
    krab_google_direct_bypass_total = _CounterBypass(
        "krab_google_direct_bypass_total",
        "Google direct bypass invocations (обходит OpenClaw WebSocket transport regression)",
        ["model", "outcome"],
    )

    # Полная latency одного bypass-вызова (от request до ответа).
    # Buckets 0.5–55s: охватывают и быстрый gemini-3-flash (~1s) и thinking-heavy (~30s).
    krab_google_direct_bypass_latency_seconds = _HistogramBypass(
        "krab_google_direct_bypass_latency_seconds",
        "Google direct bypass полная latency одного completion (секунды)",
        ["model"],
        buckets=(0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 13.0, 21.0, 34.0, 55.0),
    )

    # Токены на «думание» (thoughts) в Gemini extended thinking.
    # Помогает мониторить quota-расход thinking-mode.
    krab_google_direct_bypass_thoughts_tokens = _HistogramBypass(
        "krab_google_direct_bypass_thoughts_tokens",
        "Thoughts-токены, использованные Gemini extended thinking в bypass-вызове",
        ["model"],
        buckets=(0, 50, 100, 200, 500, 1000, 2000, 5000, 10000),
    )
except Exception:  # noqa: BLE001 - prometheus_client optional
    krab_google_direct_bypass_total = None  # type: ignore[assignment]
    krab_google_direct_bypass_latency_seconds = None  # type: ignore[assignment]
    krab_google_direct_bypass_thoughts_tokens = None  # type: ignore[assignment]


def record_google_bypass_call(
    *,
    model: str,
    outcome: str,
    latency_sec: float,
    thoughts_tokens: int = 0,
) -> None:
    """Записать metrics для одного bypass invocation.

    outcome ∈ {success, empty, error, fallback}.
    Fail-safe: никогда не бросает исключения, не ломает hot path.
    """
    try:
        m = (model or "unknown")[:80]
        o = (outcome or "unknown")[:20]
        if krab_google_direct_bypass_total is not None:
            krab_google_direct_bypass_total.labels(model=m, outcome=o).inc()
        if krab_google_direct_bypass_latency_seconds is not None:
            krab_google_direct_bypass_latency_seconds.labels(model=m).observe(
                max(0.0, float(latency_sec))
            )
        if thoughts_tokens > 0 and krab_google_direct_bypass_thoughts_tokens is not None:
            krab_google_direct_bypass_thoughts_tokens.labels(model=m).observe(
                float(thoughts_tokens)
            )
    except Exception:  # noqa: BLE001
        pass  # никогда не ломаем hot path


# === C6: Memory retrieval метрики (prometheus_client). ===
# Регистрируем один раз на уровне модуля. Если prometheus_client отсутствует —
# объекты становятся None, а вызывающий код (memory_retrieval.search) делает
# None-check перед inc/observe. Это сохраняет совместимость dev-окружений без
# опциональной зависимости.
try:
    from prometheus_client import Counter as _Counter  # type: ignore[import-not-found]
    from prometheus_client import Histogram as _Histogram  # type: ignore[import-not-found]

    _memory_retrieval_mode_total = _Counter(
        "krab_memory_retrieval_mode_total",
        "Количество retrieval queries по режиму (fts/vec/hybrid/none)",
        ["mode"],
    )
    _memory_retrieval_latency_seconds = _Histogram(
        "krab_memory_retrieval_latency_seconds",
        "Latency retrieval per phase (fts/vec/mmr/total)",
        ["phase"],
        buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
    )
    # sqlite-vec MATCH latency — trigger для миграции на HNSW.
    # При p95 > 100ms (~250k vectors на M4 Max) — пора уходить с linear scan.
    _vec_query_duration_seconds = _Histogram(
        "krab_vec_query_duration_seconds",
        "Latency of sqlite-vec MATCH queries (linear scan over vec_chunks)",
        ["k"],
        buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
    )
except Exception:  # noqa: BLE001 - prometheus_client optional
    _memory_retrieval_mode_total = None  # type: ignore[assignment]
    _memory_retrieval_latency_seconds = None  # type: ignore[assignment]
    _vec_query_duration_seconds = None  # type: ignore[assignment]


# === Feature K: Thread coherence metrics (observability-only). ===
# Histogram score (-1..1) + counter drift events (с лейблом explicit).
try:
    from prometheus_client import Counter as _Counter2  # type: ignore[import-not-found]
    from prometheus_client import Histogram as _Histogram2  # type: ignore[import-not-found]

    _thread_coherence_score = _Histogram2(
        "krab_thread_coherence_score",
        "Thread coherence score (-1..1) — semantic similarity текущего сообщения к предыдущим",
        buckets=(-1.0, -0.5, -0.2, 0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
    )
    _thread_coherence_drift_total = _Counter2(
        "krab_thread_coherence_drift_total",
        "Количество детектированных drift'ов в thread coherence",
        ["explicit"],
    )
except Exception:  # noqa: BLE001 - prometheus_client optional
    _thread_coherence_score = None  # type: ignore[assignment]
    _thread_coherence_drift_total = None  # type: ignore[assignment]


# === Idea 23: Per-handler latency dashboard. ===
# Histogram + Counter для каждого handler (`!ask`, `!search`, ...).
# Pure module — wire-up через декораторы/context manager в Wave 11-21 handlers
# (см. backlog). Buckets настроены под p50≈0.3s / p99≈10s типичных handler'ов.
try:
    from prometheus_client import Counter as _Counter3  # type: ignore[import-not-found]
    from prometheus_client import Histogram as _Histogram3  # type: ignore[import-not-found]

    _handler_latency_seconds = _Histogram3(
        "krab_handler_latency_seconds",
        "Per-handler latency (seconds) — измерение времени выполнения userbot-команд",
        ["handler"],
        buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0),
    )
    _handler_invocations_total = _Counter3(
        "krab_handler_invocations_total",
        "Per-handler invocations counter — статусы success/error/timeout",
        ["handler", "status"],
    )
except Exception:  # noqa: BLE001 - prometheus_client optional
    _handler_latency_seconds = None  # type: ignore[assignment]
    _handler_invocations_total = None  # type: ignore[assignment]


# === Wave 51-A: Prometheus exporters для Wave 44-V/47/48-A/49-F. ===
# Wave 50-E добавил alert rules ссылающиеся на 5 счётчиков ниже —
# но сами счётчики не экспортировались. Этот блок landit'ит экспортёры
# и одновременно wires hot-path inc() в соответствующих модулях
# (см. record_* helper'ы ниже). Все объекты опциональны: если
# prometheus_client недоступен → None и helper'ы no-op.
try:
    from prometheus_client import Counter as _Counter4  # type: ignore[import-not-found]

    # Wave 48-B: model fallback chain advance event.
    # Лейблы: from_model, to_model, reason (quota / provider_timeout / lm_empty_stream / ...).
    krab_model_fallback_engaged_total = _Counter4(
        "krab_model_fallback_engaged_total",
        "Number of model fallback events (route switched).",
        ["from_model", "to_model", "reason"],
    )
    # Wave 47 / Wave 44-V: codex-cli quota exhaustion → fallback transition.
    # kind ∈ {weekly, transient}.
    krab_codex_disabled_transition_total = _Counter4(
        "krab_codex_disabled_transition_total",
        "Codex quota exhausted → switched to fallback model.",
        ["kind"],
    )
    # Wave 48-A: per-chat startup catchup failure.
    krab_startup_catchup_chat_failed_total = _Counter4(
        "krab_startup_catchup_chat_failed_total",
        "Startup catchup failures per chat.",
        ["chat_id"],
    )
    # Wave 49-F: state snapshot copy/write failure.
    # reason ∈ {copy_failed, list_entry_failed, restore_failed, ...}.
    krab_state_snapshot_failed_total = _Counter4(
        "krab_state_snapshot_failed_total",
        "State snapshot failures (write/copy errors).",
        ["reason"],
    )
    # Wave 47: provider semantic timeout (HTTP 500 / provider_timeout).
    krab_provider_timeout_total = _Counter4(
        "krab_provider_timeout_total",
        "OpenClaw gateway provider timeouts (HTTP 500/timeout).",
        ["provider", "model"],
    )
except Exception:  # noqa: BLE001 - prometheus_client optional
    krab_model_fallback_engaged_total = None  # type: ignore[assignment]
    krab_codex_disabled_transition_total = None  # type: ignore[assignment]
    krab_startup_catchup_chat_failed_total = None  # type: ignore[assignment]
    krab_state_snapshot_failed_total = None  # type: ignore[assignment]
    krab_provider_timeout_total = None  # type: ignore[assignment]


def record_model_fallback_engaged(*, from_model: str, to_model: str, reason: str) -> None:
    """Инкремент krab_model_fallback_engaged_total.

    Вызывается из openclaw_client при каждом переключении на fallback.
    Fail-safe: никогда не бросает.
    """
    try:
        if krab_model_fallback_engaged_total is None:
            return
        krab_model_fallback_engaged_total.labels(
            from_model=(from_model or "unknown")[:80],
            to_model=(to_model or "unknown")[:80],
            reason=(reason or "unknown")[:40],
        ).inc()
    except Exception:  # noqa: BLE001
        pass


def record_codex_disabled_transition(*, kind: str) -> None:
    """Инкремент krab_codex_disabled_transition_total.

    Вызывается из codex_quota_state.mark_codex_disabled при первом
    transition (idempotent — caller передаёт is_transition флаг).
    """
    try:
        if krab_codex_disabled_transition_total is None:
            return
        krab_codex_disabled_transition_total.labels(
            kind=(kind or "weekly")[:20],
        ).inc()
    except Exception:  # noqa: BLE001
        pass


def record_startup_catchup_chat_failed(*, chat_id: int | str) -> None:
    """Инкремент krab_startup_catchup_chat_failed_total.

    Вызывается из message_catchup при per-chat fail.
    """
    try:
        if krab_startup_catchup_chat_failed_total is None:
            return
        krab_startup_catchup_chat_failed_total.labels(
            chat_id=str(chat_id)[:30],
        ).inc()
    except Exception:  # noqa: BLE001
        pass


def record_state_snapshot_failed(*, reason: str) -> None:
    """Инкремент krab_state_snapshot_failed_total.

    Вызывается из state_snapshots при copy/restore failures.
    """
    try:
        if krab_state_snapshot_failed_total is None:
            return
        krab_state_snapshot_failed_total.labels(
            reason=(reason or "unknown")[:40],
        ).inc()
    except Exception:  # noqa: BLE001
        pass


def record_provider_timeout(*, provider: str, model: str) -> None:
    """Инкремент krab_provider_timeout_total.

    Вызывается из openclaw_client при semantic_code == 'provider_timeout'.
    """
    try:
        if krab_provider_timeout_total is None:
            return
        krab_provider_timeout_total.labels(
            provider=(provider or "unknown")[:40],
            model=(model or "unknown")[:80],
        ).inc()
    except Exception:  # noqa: BLE001
        pass


def observe_handler_latency(
    handler_name: str,
    latency_sec: float,
    *,
    status: str = "success",
) -> None:
    """Записывает latency и инкрементирует счётчик для handler.

    Безопасно вызывать из любого места — fail-safe без prometheus_client.
    `status` ∈ {success, error, timeout}; произвольные значения тоже принимаются,
    но рекомендуется придерживаться enum'а для согласованности дашборда.
    """
    name = (handler_name or "unknown")[:60]
    st = (status or "success")[:20]
    try:
        if _handler_latency_seconds is not None:
            _handler_latency_seconds.labels(handler=name).observe(max(0.0, float(latency_sec)))
        if _handler_invocations_total is not None:
            _handler_invocations_total.labels(handler=name, status=st).inc()
    except Exception:  # noqa: BLE001
        pass


class _HandlerLatencyTimer:
    """Async context manager, замеряющий latency handler.

    При выходе через исключение — status="error"; иначе "success".
    Можно вручную выставить статус через `.set_status('timeout')`.
    """

    __slots__ = ("_handler", "_start", "_status")

    def __init__(self, handler_name: str) -> None:
        self._handler = handler_name
        self._start: float = 0.0
        self._status: str = "success"

    def set_status(self, status: str) -> None:
        """Принудительно установить статус (например, 'timeout')."""
        self._status = status

    async def __aenter__(self) -> _HandlerLatencyTimer:
        self._start = time.monotonic()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        latency = max(0.0, time.monotonic() - self._start)
        status = self._status
        if exc_type is not None and status == "success":
            status = "error"
        observe_handler_latency(self._handler, latency, status=status)
        # Не глотаем исключение — context manager только наблюдает.
        return None


def time_handler(handler_name: str) -> _HandlerLatencyTimer:
    """Async context manager для замера handler latency.

    Пример:
        async with time_handler('ask'):
            await do_ask()
    """
    return _HandlerLatencyTimer(handler_name)


# === Wave 55-C: Timing histograms (chain advance / response chars / smart retry). ===
# Три histogram'а для observability распределения времени и размера ответов.
# Если prometheus_client недоступен — объекты None, helper'ы no-op. Никогда
# не ломают hot path (try/except BLE001 во всех record_*).
try:
    from prometheus_client import Histogram as _Histogram5C  # type: ignore[import-not-found]

    # Время от первого сбоя текущей модели до начала следующей в цепочке (1-90s range).
    # Buckets: охватывают быстрые 1-5s переключения и медленные 60-90s таймауты.
    krab_chain_advance_duration_seconds = _Histogram5C(
        "krab_chain_advance_duration_seconds",
        "Time spent on each chain advance attempt (from current model failure to next start)",
        ["from_model", "to_model", "reason"],
        buckets=(1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 45.0, 60.0, 90.0),
    )

    # Распределение размера ответов LLM (количество символов) перед отправкой в Telegram.
    # Buckets: 50-5000 chars — охватывают от коротких команд до длинных сводок.
    krab_model_response_chars = _Histogram5C(
        "krab_model_response_chars",
        "Response size distribution (chars) before Telegram delivery",
        ["model"],
        buckets=(50, 200, 500, 1000, 2000, 5000),
    )

    # Wave 54-B smart retry wait time по исходу (success/failure).
    # Buckets: 5-90s — охватывают KRAB_CLOUD_RECOVERY_RETRY_DELAY_SEC диапазон.
    krab_smart_retry_wait_seconds = _Histogram5C(
        "krab_smart_retry_wait_seconds",
        "Wave 54-B cloud recovery retry wait time per outcome",
        ["outcome"],
        buckets=(5.0, 10.0, 15.0, 20.0, 30.0, 45.0, 60.0, 90.0),
    )
except Exception:  # noqa: BLE001 - prometheus_client optional
    krab_chain_advance_duration_seconds = None  # type: ignore[assignment]
    krab_model_response_chars = None  # type: ignore[assignment]
    krab_smart_retry_wait_seconds = None  # type: ignore[assignment]


def record_chain_advance_duration(
    *,
    from_model: str,
    to_model: str,
    reason: str,
    duration_sec: float,
) -> None:
    """Записывает duration одного chain advance в histogram.

    Вызывается из openclaw_client при переходе к следующей модели в цепочке.
    duration_sec — время с начала попытки текущей модели до момента advance.
    Fail-safe: никогда не бросает.
    """
    try:
        if krab_chain_advance_duration_seconds is None:
            return
        krab_chain_advance_duration_seconds.labels(
            from_model=(from_model or "unknown")[:80],
            to_model=(to_model or "unknown")[:80],
            reason=(reason or "unknown")[:40],
        ).observe(max(0.0, float(duration_sec)))
    except Exception:  # noqa: BLE001
        pass


def record_response_chars(*, model: str, char_count: int) -> None:
    """Записывает размер финального ответа LLM в histogram.

    Вызывается из openclaw_client._finalize_chat_response.
    Fail-safe: никогда не бросает.
    """
    try:
        if krab_model_response_chars is None:
            return
        krab_model_response_chars.labels(
            model=(model or "unknown")[:80],
        ).observe(max(0, int(char_count)))
    except Exception:  # noqa: BLE001
        pass


def record_smart_retry_wait(*, outcome: str, wait_sec: float) -> None:
    """Записывает Wave 54-B retry wait time в histogram.

    outcome ∈ {success, failure}.
    Вызывается из openclaw_client после cloud_recovery_retry_scheduled.
    Fail-safe: никогда не бросает.
    """
    try:
        if krab_smart_retry_wait_seconds is None:
            return
        krab_smart_retry_wait_seconds.labels(
            outcome=(outcome or "unknown")[:20],
        ).observe(max(0.0, float(wait_sec)))
    except Exception:  # noqa: BLE001
        pass


def observe_thread_coherence(score: float | None, *, drift: bool, explicit: bool) -> None:
    """Записывает thread coherence в Prometheus (fail-safe, no-op без prom_client)."""
    try:
        if _thread_coherence_score is not None and score is not None:
            _thread_coherence_score.observe(float(score))
        if drift and _thread_coherence_drift_total is not None:
            _thread_coherence_drift_total.labels(explicit=str(bool(explicit)).lower()).inc()
    except Exception:  # noqa: BLE001
        pass


def _sanitize_label(value: str) -> str:
    """Escape кавычек и переводов строк в значении label."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _format_metric(
    name: str,
    value: float | int,
    labels: dict[str, str] | None = None,
    help_text: str = "",
    mtype: str = "gauge",
) -> str:
    """Format single Prometheus metric."""
    lines: list[str] = []
    if help_text:
        lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} {mtype}")
    if labels:
        label_str = ",".join(f'{k}="{_sanitize_label(str(v))}"' for k, v in labels.items())
        lines.append(f"{name}{{{label_str}}} {value}")
    else:
        lines.append(f"{name} {value}")
    return "\n".join(lines)


def collect_metrics() -> str:
    """Main collector — возвращает Prometheus text."""
    lines: list[str] = []

    # === Memory Validator ===
    try:
        from src.core.memory_validator import memory_validator  # type: ignore[import-not-found]

        stats = getattr(memory_validator, "stats", {}) or {}
        for key in (
            "safe_total",
            "injection_blocked_total",
            "confirmed_total",
            "confirm_failed_total",
        ):
            lines.append(
                _format_metric(
                    f"krab_memory_validator_{key}",
                    stats.get(key, 0),
                    help_text=f"Memory validator {key}",
                    mtype="counter",
                )
            )
        try:
            pending_count = len(memory_validator.list_pending())
        except Exception:
            pending_count = 0
        lines.append(
            _format_metric(
                "krab_memory_validator_pending",
                pending_count,
                help_text="Memory validator pending confirmations",
            )
        )
    except Exception:
        pass

    # === Archive DB ===
    try:
        db_path = Path("~/.openclaw/krab_memory/archive.db").expanduser()
        if db_path.exists():
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                for table in ("messages", "chats", "chunks"):
                    try:
                        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                        lines.append(
                            _format_metric(
                                f"krab_archive_{table}_total",
                                count,
                                help_text=f"Archive.db {table} count",
                            )
                        )
                    except sqlite3.Error:
                        pass
                try:
                    embedded = conn.execute(
                        "SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL"
                    ).fetchone()[0]
                    lines.append(
                        _format_metric(
                            "krab_archive_chunks_embedded_total",
                            embedded,
                            help_text="Chunks with Model2Vec embedding",
                        )
                    )
                except sqlite3.OperationalError:
                    # No embedding column
                    pass
            finally:
                conn.close()
            try:
                lines.append(
                    _format_metric(
                        "krab_archive_db_size_bytes",
                        db_path.stat().st_size,
                        help_text="Archive.db file size",
                    )
                )
            except OSError:
                pass
    except Exception:
        pass

    # === Runtime Route ===
    try:
        from src.openclaw_client import openclaw_client

        route = getattr(openclaw_client, "last_runtime_route", None) or getattr(
            openclaw_client, "_last_runtime_route", None
        )
        if isinstance(route, dict) and route:
            status_ok = 1 if route.get("status") == "ok" else 0
            lines.append(
                _format_metric(
                    "krab_llm_route_ok",
                    status_ok,
                    labels={
                        "provider": str(route.get("provider", "unknown"))[:50],
                        "model": str(route.get("model", "unknown"))[:80],
                    },
                    help_text="Last LLM route status (1=ok, 0=error)",
                )
            )
    except Exception:
        pass

    # === Reminders ===
    try:
        from src.core.reminders_queue import reminders_queue  # type: ignore[import-not-found]

        pending = reminders_queue.list_pending()
        lines.append(
            _format_metric(
                "krab_reminders_pending_total",
                len(pending),
                help_text="Pending reminders",
            )
        )
    except Exception:
        pass

    # === Auto-restart ===
    try:
        from src.core.auto_restart_policy import _attempts_total as _arp_attempts

        for svc_name, attempt_count in _arp_attempts.items():
            lines.append(
                _format_metric(
                    "krab_auto_restart_attempts_total",
                    attempt_count,
                    labels={"service": str(svc_name)[:50]},
                    help_text="Total auto-restart attempts since process start",
                    mtype="counter",
                )
            )
    except Exception:
        pass

    # === Command invocations ===
    try:
        from src.core.command_registry import get_usage  # type: ignore[import-not-found]

        usage = get_usage()
        if usage:
            for cmd, count in usage.items():
                lines.append(
                    _format_metric(
                        "krab_command_invocations_total",
                        count,
                        labels={"command": cmd[:30]},
                        help_text="Total invocations per command",
                        mtype="counter",
                    )
                )
    except Exception:
        pass

    # === LLM route latency histogram ===
    try:
        from src.core.llm_latency_tracker import (
            llm_latency_tracker,  # type: ignore[import-not-found]
        )

        for series in llm_latency_tracker.snapshot():
            provider = series["provider"]
            model = series["model"]
            metric_name = "krab_llm_route_latency_seconds"
            # Заголовок один раз на имя (упрощённо — выводим перед первым bucket)
            lines.append(f"# HELP {metric_name} LLM route latency histogram (seconds)")
            lines.append(f"# TYPE {metric_name} histogram")
            for le_str, cnt in series["buckets"].items():
                label_str = (
                    f'provider="{_sanitize_label(provider)}",'
                    f'model="{_sanitize_label(model)}",'
                    f'le="{le_str}"'
                )
                lines.append(f"{metric_name}_bucket{{{label_str}}} {cnt}")
            # sum / count
            label_str_base = (
                f'provider="{_sanitize_label(provider)}",model="{_sanitize_label(model)}"'
            )
            lines.append(f"{metric_name}_sum{{{label_str_base}}} {series['sum']:.6f}")
            lines.append(f"{metric_name}_count{{{label_str_base}}} {series['count']}")
    except Exception:
        pass

    # === Chat filter modes ===
    try:
        from src.core.chat_filter_config import chat_filter_config  # type: ignore[import-not-found]

        stats = chat_filter_config.stats()
        for mode, count in stats.get("by_mode", {}).items():
            lines.append(
                _format_metric(
                    "krab_chat_filter_modes_total",
                    count,
                    labels={"mode": mode},
                    help_text="Chats per filter mode",
                    mtype="counter",
                )
            )
    except Exception:
        pass

    # === ChatWindow stats ===
    try:
        from src.core.chat_window_manager import (
            chat_window_manager,  # type: ignore[import-not-found]
        )

        cw = chat_window_manager.stats()
        lines.append(
            _format_metric(
                "krab_chat_windows_active",
                cw.get("active_windows", 0),
                help_text="Active ChatWindow instances",
            )
        )
        lines.append(
            _format_metric(
                "krab_chat_windows_capacity",
                cw.get("capacity", 0),
                help_text="Total ChatWindow capacity (sum of all window sizes)",
            )
        )
        lines.append(
            _format_metric(
                "krab_chat_windows_total_messages",
                cw.get("total_messages", 0),
                help_text="Total messages buffered across all ChatWindows",
            )
        )
        evicted = chat_window_manager.get_eviction_counts()
        for reason, count in evicted.items():
            lines.append(
                _format_metric(
                    "krab_chat_windows_evicted_total",
                    count,
                    labels={"reason": reason},
                    help_text="Total ChatWindow evictions by reason (lru, idle)",
                    mtype="counter",
                )
            )
    except Exception:
        pass

    # === Memory query relevance score percentiles ===
    try:
        from src.core.memory_retrieval_scores import rrf_score_window

        pcts = rrf_score_window.percentiles()
        if pcts:
            for quantile, value in pcts.items():
                lines.append(
                    _format_metric(
                        f"krab_memory_query_relevance_score_{quantile}",
                        round(value, 6),
                        help_text=f"RRF score distribution {quantile} (last {len(rrf_score_window)} queries)",
                    )
                )
    except Exception:
        pass

    # === Adaptive rerank usage ===
    lines.append(
        _format_metric(
            "krab_memory_adaptive_rerank_used_total",
            _ADAPTIVE_RERANK_COUNTER[0],
            help_text="Total adaptive rerank invocations (MEMORY_ADAPTIVE_RERANK_ENABLED=1)",
            mtype="counter",
        )
    )

    # === Stealth detection counters ===
    try:
        from src.core.stealth_metrics import get_counts as _stealth_get_counts

        stealth_counts = _stealth_get_counts()
        if stealth_counts:
            for layer, count in stealth_counts.items():
                lines.append(
                    _format_metric(
                        "krab_stealth_detection_total",
                        count,
                        labels={"layer": layer[:30]},
                        help_text="Anti-bot detection signals by layer (canvas/webgl/webrtc/captcha/ratelimit/blocked)",
                        mtype="counter",
                    )
                )
    except Exception:
        pass

    # === Telegram FloodWait (alert TelegramRateLimited) ===
    # Pre-register HELP/TYPE даже если счётчик пустой — чтобы alert
    # `increase(krab_telegram_flood_wait_total[15m])` не считался "no data".
    lines.append("# HELP krab_telegram_flood_wait_total Telegram FloodWait incidents by caller")
    lines.append("# TYPE krab_telegram_flood_wait_total counter")
    if not _TELEGRAM_FLOOD_WAIT_COUNTER:
        lines.append('krab_telegram_flood_wait_total{caller="none"} 0')
    else:
        for _fw_caller, _fw_count in _TELEGRAM_FLOOD_WAIT_COUNTER.items():
            label_str = f'caller="{_sanitize_label(_fw_caller)}"'
            lines.append(f"krab_telegram_flood_wait_total{{{label_str}}} {_fw_count}")

    # === Guest LLM skip (security ACL) ===
    for _skip_reason, _skip_count in _GUEST_LLM_SKIPPED_COUNTER.items():
        lines.append(
            _format_metric(
                "krab_guest_llm_skipped_total",
                _skip_count,
                labels={"reason": _skip_reason[:60]},
                help_text="LLM replies skipped for guests in groups (security ACL)",
                mtype="counter",
            )
        )

    # === Swarm per-team tool blocks (silent strip) ===
    try:
        from src.core.swarm_tool_allowlist import (  # type: ignore[import-not-found]
            get_blocked_tool_stats,
        )

        for (_team, _tool), _cnt in get_blocked_tool_stats().items():
            lines.append(
                _format_metric(
                    "krab_swarm_tool_blocked_total",
                    _cnt,
                    labels={"team": _team[:40], "tool": _tool[:80]},
                    help_text="Swarm per-team tool calls blocked by allowlist",
                    mtype="counter",
                )
            )
    except Exception:
        pass

    # === Session corruption counter ===
    # Pre-register HELP/TYPE даже если счётчик пустой — чтобы alert
    # `increase(krab_session_corruption_total[1h])` не считался "no data".
    lines.append(
        "# HELP krab_session_corruption_total DB corruption events requiring quarantine by kind"
    )
    lines.append("# TYPE krab_session_corruption_total counter")
    if not _SESSION_CORRUPTION_COUNTER:
        lines.append('krab_session_corruption_total{kind="none"} 0')
    else:
        for _corr_kind, _corr_count in _SESSION_CORRUPTION_COUNTER.items():
            label_str = f'kind="{_sanitize_label(_corr_kind)}"'
            lines.append(f"krab_session_corruption_total{{{label_str}}} {_corr_count}")

    # === Startup duration ===
    lines.append(
        _format_metric(
            "krab_startup_duration_seconds",
            _STARTUP_DURATION_SECONDS[0],
            help_text="Время от старта процесса до kraab_running (секунды)",
        )
    )

    # === Agent Engine runs (Phase C / Wave 17-B) ===
    # krab_agent_engine_runs_total{engine, success}
    lines.append(
        "# HELP krab_agent_engine_runs_total Total agent engine runs by engine and success"
    )
    lines.append("# TYPE krab_agent_engine_runs_total counter")
    if not _AGENT_ENGINE_RUNS_COUNTER:
        lines.append('krab_agent_engine_runs_total{engine="openclaw",success="0"} 0')
    else:
        for _ae_engine, _ae_bucket in _AGENT_ENGINE_RUNS_COUNTER.items():
            for _ae_success, _ae_count in _ae_bucket.items():
                label_str = f'engine="{_sanitize_label(_ae_engine)}",success="{_ae_success}"'
                lines.append(f"krab_agent_engine_runs_total{{{label_str}}} {_ae_count}")

    # krab_agent_engine_latency_seconds_avg{engine} — накопленный avg
    lines.append(
        "# HELP krab_agent_engine_latency_seconds_avg Average latency of agent engine runs"
    )
    lines.append("# TYPE krab_agent_engine_latency_seconds_avg gauge")
    if not _AGENT_ENGINE_LATENCY_ACC:
        lines.append('krab_agent_engine_latency_seconds_avg{engine="openclaw"} 0')
    else:
        for _ae_engine, _ae_acc in _AGENT_ENGINE_LATENCY_ACC.items():
            total_sec, count = _ae_acc[0], int(_ae_acc[1])
            avg = round(total_sec / count, 4) if count > 0 else 0.0
            label_str = f'engine="{_sanitize_label(_ae_engine)}"'
            lines.append(f"krab_agent_engine_latency_seconds_avg{{{label_str}}} {avg}")

    # krab_agent_engine_fallback_total{from_engine, to_engine}
    lines.append(
        "# HELP krab_agent_engine_fallback_total Fallback events when requested engine is unhealthy"
    )
    lines.append("# TYPE krab_agent_engine_fallback_total counter")
    if not _AGENT_ENGINE_FALLBACK_COUNTER:
        lines.append(
            'krab_agent_engine_fallback_total{from_engine="hermes",to_engine="openclaw"} 0'
        )
    else:
        for _ae_from, _ae_to_bucket in _AGENT_ENGINE_FALLBACK_COUNTER.items():
            for _ae_to, _ae_cnt in _ae_to_bucket.items():
                label_str = (
                    f'from_engine="{_sanitize_label(_ae_from)}",'
                    f'to_engine="{_sanitize_label(_ae_to)}"'
                )
                lines.append(f"krab_agent_engine_fallback_total{{{label_str}}} {_ae_cnt}")

    # === Timestamps ===
    lines.append(
        _format_metric(
            "krab_metrics_generated_at",
            int(time.time()),
            help_text="Metrics generation timestamp",
        )
    )
    lines.append(
        _format_metric(
            "krab_process_start_time_seconds",
            _PROCESS_START_TIME,
            help_text="Unix timestamp когда процесс owner panel стартовал",
        )
    )

    return "\n".join(lines) + "\n"
