# -*- coding: utf-8 -*-
"""Process-level метрики: uptime, FloodWait, session corruption, startup duration,
guest LLM skip, adaptive rerank, agent engine, Wave 51-A exporters, Wave 55-C
histograms, Idea 23 handler latency.

Все record_* функции читают метрики через facade `src.core.prometheus_metrics`,
чтобы тесты могли monkey-patch'ить facade-уровень и видеть эффект.
"""

from __future__ import annotations

import time

# Процесс стартовал в этот момент (unix ts).
_PROCESS_START_TIME: float = time.time()

# Счётчик adaptive rerank invocations (mutable singleton).
_ADAPTIVE_RERANK_COUNTER: list[int] = [0]

# Guest LLM skip counter — reason → count.
_GUEST_LLM_SKIPPED_COUNTER: dict[str, int] = {}

# Telegram FloodWait counter — caller → count.
_TELEGRAM_FLOOD_WAIT_COUNTER: dict[str, int] = {}

# Session corruption counter — kind → count.
_SESSION_CORRUPTION_COUNTER: dict[str, int] = {}

# Startup duration (sec). Выставляется один раз из bootstrap/runtime.
_STARTUP_DURATION_SECONDS: list[float] = [0.0]

# Agent Engine counters (Wave 17-B).
_AGENT_ENGINE_RUNS_COUNTER: dict[str, dict[str, int]] = {}
_AGENT_ENGINE_FALLBACK_COUNTER: dict[str, dict[str, int]] = {}
_AGENT_ENGINE_LATENCY_ACC: dict[str, list[float]] = {}


def inc_telegram_flood_wait(caller: str) -> None:
    """Инкремент krab_telegram_flood_wait_total{caller=...}. Fail-safe."""
    key = (caller or "unknown")[:80]
    _TELEGRAM_FLOOD_WAIT_COUNTER[key] = _TELEGRAM_FLOOD_WAIT_COUNTER.get(key, 0) + 1


def inc_session_corruption(kind: str) -> None:
    """Инкремент krab_session_corruption_total{kind=...}. Fail-safe."""
    key = (kind or "unknown")[:40]
    _SESSION_CORRUPTION_COUNTER[key] = _SESSION_CORRUPTION_COUNTER.get(key, 0) + 1


def set_startup_duration(elapsed_sec: float) -> None:
    """Выставляет krab_startup_duration_seconds. Fail-safe."""
    try:
        _STARTUP_DURATION_SECONDS[0] = max(0.0, float(elapsed_sec))
    except Exception:  # noqa: BLE001
        pass


# === S66 Wave 3: uptime / handler tick age gauges =========================
#
# Дополняют Silent-Death Defense (Wave 63 series). Operator может строить
# графики "uptime по версии Krab" (после S64 W4 restart cause logging) и
# "dispatcher liveness over time".


def current_uptime_seconds(*, now: float | None = None) -> float:
    """Сколько секунд процесс Krab прожил с момента userbot_started.

    Источник правды — `_PROCESS_START_TIME` (module-load timestamp).
    Fail-safe: возвращает 0.0 при любых сбоях.
    """
    try:
        ts_now = float(now) if now is not None else time.time()
        return max(0.0, ts_now - float(_PROCESS_START_TIME))
    except Exception:  # noqa: BLE001
        return 0.0


def current_handler_tick_age_seconds(*, now: float | None = None) -> float:
    """Сколько секунд назад main dispatcher последний раз тикнул.

    Читает `_last_dispatcher_tick_ts` через Wave 70 weakref. Возвращает
    `-1.0` если userbot не зарегистрирован или ts ещё не выставлен —
    aligned с semantics `krab_main_dispatcher_tick_ago_seconds`.
    """
    try:
        from src.core.metrics.probes import _get_userbot_for_metrics  # noqa: PLC0415

        ub = _get_userbot_for_metrics()
        if ub is None:
            return -1.0
        tick_ts_raw = getattr(ub, "_last_dispatcher_tick_ts", None)
        if tick_ts_raw is None:
            return -1.0
        ts_now = float(now) if now is not None else time.time()
        return max(0.0, ts_now - float(tick_ts_raw))
    except Exception:  # noqa: BLE001
        return -1.0


def record_agent_engine_run(engine: str, success: bool, latency_sec: float) -> None:
    """Инкремент runs_total и накопитель latency. Fail-safe."""
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
    """Инкремент krab_agent_engine_fallback_total{from_engine, to_engine}. Fail-safe."""
    try:
        from_key = (from_engine or "unknown")[:40]
        to_key = (to_engine or "unknown")[:40]
        bucket = _AGENT_ENGINE_FALLBACK_COUNTER.setdefault(from_key, {})
        bucket[to_key] = bucket.get(to_key, 0) + 1
    except Exception:  # noqa: BLE001
        pass


# === Wave 51-A: model fallback / codex / startup catchup / state snapshot / provider timeout ===
try:
    from prometheus_client import Counter as _Counter4  # type: ignore[import-not-found]

    krab_model_fallback_engaged_total = _Counter4(
        "krab_model_fallback_engaged_total",
        "Number of model fallback events (route switched).",
        ["from_model", "to_model", "reason"],
    )
    krab_codex_disabled_transition_total = _Counter4(
        "krab_codex_disabled_transition_total",
        "Codex quota exhausted → switched to fallback model.",
        ["kind"],
    )
    krab_startup_catchup_chat_failed_total = _Counter4(
        "krab_startup_catchup_chat_failed_total",
        "Startup catchup failures per chat.",
        ["chat_id"],
    )
    krab_state_snapshot_failed_total = _Counter4(
        "krab_state_snapshot_failed_total",
        "State snapshot failures (write/copy errors).",
        ["reason"],
    )
    krab_provider_timeout_total = _Counter4(
        "krab_provider_timeout_total",
        "OpenClaw gateway provider timeouts (HTTP 500/timeout).",
        ["provider", "model"],
    )
except Exception:  # noqa: BLE001
    krab_model_fallback_engaged_total = None  # type: ignore[assignment]
    krab_codex_disabled_transition_total = None  # type: ignore[assignment]
    krab_startup_catchup_chat_failed_total = None  # type: ignore[assignment]
    krab_state_snapshot_failed_total = None  # type: ignore[assignment]
    krab_provider_timeout_total = None  # type: ignore[assignment]


def _facade():
    """Lazy import фасада — нужен чтобы тесты могли patch'ить facade-атрибуты."""
    import src.core.prometheus_metrics as _pm  # noqa: PLC0415

    return _pm


def record_model_fallback_engaged(*, from_model: str, to_model: str, reason: str) -> None:
    """Инкремент krab_model_fallback_engaged_total. Fail-safe."""
    try:
        metric = _facade().krab_model_fallback_engaged_total
        if metric is None:
            return
        metric.labels(
            from_model=(from_model or "unknown")[:80],
            to_model=(to_model or "unknown")[:80],
            reason=(reason or "unknown")[:40],
        ).inc()
    except Exception:  # noqa: BLE001
        pass


def record_codex_disabled_transition(*, kind: str) -> None:
    """Инкремент krab_codex_disabled_transition_total. Fail-safe."""
    try:
        metric = _facade().krab_codex_disabled_transition_total
        if metric is None:
            return
        metric.labels(kind=(kind or "weekly")[:20]).inc()
    except Exception:  # noqa: BLE001
        pass


def record_startup_catchup_chat_failed(*, chat_id: int | str) -> None:
    """Инкремент krab_startup_catchup_chat_failed_total. Fail-safe."""
    try:
        metric = _facade().krab_startup_catchup_chat_failed_total
        if metric is None:
            return
        metric.labels(chat_id=str(chat_id)[:30]).inc()
    except Exception:  # noqa: BLE001
        pass


def record_state_snapshot_failed(*, reason: str) -> None:
    """Инкремент krab_state_snapshot_failed_total. Fail-safe."""
    try:
        metric = _facade().krab_state_snapshot_failed_total
        if metric is None:
            return
        metric.labels(reason=(reason or "unknown")[:40]).inc()
    except Exception:  # noqa: BLE001
        pass


def record_provider_timeout(*, provider: str, model: str) -> None:
    """Инкремент krab_provider_timeout_total. Fail-safe."""
    try:
        metric = _facade().krab_provider_timeout_total
        if metric is None:
            return
        metric.labels(
            provider=(provider or "unknown")[:40],
            model=(model or "unknown")[:80],
        ).inc()
    except Exception:  # noqa: BLE001
        pass


# === Idea 23: Per-handler latency dashboard ===
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
except Exception:  # noqa: BLE001
    _handler_latency_seconds = None  # type: ignore[assignment]
    _handler_invocations_total = None  # type: ignore[assignment]


def observe_handler_latency(
    handler_name: str,
    latency_sec: float,
    *,
    status: str = "success",
) -> None:
    """Записывает latency и инкрементирует счётчик. Fail-safe."""
    name = (handler_name or "unknown")[:60]
    st = (status or "success")[:20]
    try:
        latency = _facade()._handler_latency_seconds
        invocations = _facade()._handler_invocations_total
        if latency is not None:
            latency.labels(handler=name).observe(max(0.0, float(latency_sec)))
        if invocations is not None:
            invocations.labels(handler=name, status=st).inc()
    except Exception:  # noqa: BLE001
        pass


class _HandlerLatencyTimer:
    """Async context manager — замеряет latency handler."""

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
        return None


def time_handler(handler_name: str) -> _HandlerLatencyTimer:
    """Async context manager для замера handler latency."""
    return _HandlerLatencyTimer(handler_name)


# === Wave 55-C: chain advance / response chars / smart retry histograms ===
try:
    from prometheus_client import Histogram as _Histogram5C  # type: ignore[import-not-found]

    krab_chain_advance_duration_seconds = _Histogram5C(
        "krab_chain_advance_duration_seconds",
        "Time spent on each chain advance attempt (from current model failure to next start)",
        ["from_model", "to_model", "reason"],
        buckets=(1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 45.0, 60.0, 90.0),
    )
    krab_model_response_chars = _Histogram5C(
        "krab_model_response_chars",
        "Response size distribution (chars) before Telegram delivery",
        ["model"],
        buckets=(50, 200, 500, 1000, 2000, 5000),
    )
    krab_smart_retry_wait_seconds = _Histogram5C(
        "krab_smart_retry_wait_seconds",
        "Wave 54-B cloud recovery retry wait time per outcome",
        ["outcome"],
        buckets=(5.0, 10.0, 15.0, 20.0, 30.0, 45.0, 60.0, 90.0),
    )
except Exception:  # noqa: BLE001
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
    """Записывает duration одного chain advance. Fail-safe."""
    try:
        metric = _facade().krab_chain_advance_duration_seconds
        if metric is None:
            return
        metric.labels(
            from_model=(from_model or "unknown")[:80],
            to_model=(to_model or "unknown")[:80],
            reason=(reason or "unknown")[:40],
        ).observe(max(0.0, float(duration_sec)))
    except Exception:  # noqa: BLE001
        pass


def record_response_chars(*, model: str, char_count: int) -> None:
    """Записывает размер финального ответа LLM. Fail-safe."""
    try:
        metric = _facade().krab_model_response_chars
        if metric is None:
            return
        metric.labels(model=(model or "unknown")[:80]).observe(max(0, int(char_count)))
    except Exception:  # noqa: BLE001
        pass


def record_smart_retry_wait(*, outcome: str, wait_sec: float) -> None:
    """Записывает Wave 54-B retry wait time. Fail-safe."""
    try:
        metric = _facade().krab_smart_retry_wait_seconds
        if metric is None:
            return
        metric.labels(outcome=(outcome or "unknown")[:20]).observe(max(0.0, float(wait_sec)))
    except Exception:  # noqa: BLE001
        pass
