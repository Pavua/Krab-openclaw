# -*- coding: utf-8 -*-
"""Wave 177: typing indicator observability метрики.

Метрики «Краб печатает...» / send_chat_action loop (см. `src/userbot/typing_indicator.py`,
Wave 173):
  - krab_typing_indicator_started_total{action}                — счётчик стартов
  - krab_typing_indicator_cancelled_total{reason}              — счётчик завершений
  - krab_typing_indicator_duration_seconds                     — histogram длительности
  - krab_typing_indicator_floodwait_total{chat_id_bucket}      — FloodWait по корзинам chat_id

Action:
  - typing           — обычный «печатает...»
  - recording_voice  — «записывает голосовое»
  - upload_photo     — «загружает фото»
  - upload_doc       — «загружает файл»
  - unknown          — неизвестное / fallback

Reason (cancel):
  - success    — нормальный выход из `async with`
  - error      — exception в теле блока (body raised)
  - timeout    — keep-alive отменён по таймауту извне (e.g. LLM idle kill)
  - floodwait  — индикатор остановлен из-за FloodWait в send_chat_action

chat_id_bucket:
  - hash(chat_id) % 100 → строка «00».. «99» (PII-safe: bucket вместо id).

Все вызовы fail-safe: при отсутствии prometheus_client / facade — silent no-op.
Tests patch'ат фасадные атрибуты `src.core.prometheus_metrics.*`.
"""

from __future__ import annotations

try:
    from prometheus_client import Counter, Histogram  # type: ignore[import-not-found]

    # Стандартные buckets для duration histogram (см. требование задачи).
    _DEFAULT_BUCKETS = (0.5, 1, 2, 5, 10, 15, 30, 60)

    krab_typing_indicator_started_total = Counter(
        "krab_typing_indicator_started_total",
        "Typing indicator activations per action (Wave 177)",
        ["action"],
    )
    krab_typing_indicator_cancelled_total = Counter(
        "krab_typing_indicator_cancelled_total",
        "Typing indicator deactivations per reason (Wave 177)",
        ["reason"],
    )
    krab_typing_indicator_duration_seconds = Histogram(
        "krab_typing_indicator_duration_seconds",
        "Typing indicator visible duration in seconds (Wave 177)",
        buckets=_DEFAULT_BUCKETS,
    )
    krab_typing_indicator_floodwait_total = Counter(
        "krab_typing_indicator_floodwait_total",
        "FloodWait errors during send_chat_action bucketed by chat_id hash (Wave 177)",
        ["chat_id_bucket"],
    )
except Exception:  # noqa: BLE001 — slim env без prometheus_client
    krab_typing_indicator_started_total = None  # type: ignore[assignment]
    krab_typing_indicator_cancelled_total = None  # type: ignore[assignment]
    krab_typing_indicator_duration_seconds = None  # type: ignore[assignment]
    krab_typing_indicator_floodwait_total = None  # type: ignore[assignment]


# Допустимые action / reason — лишние нормализуются в «unknown» / «error».
_ALLOWED_ACTIONS = {"typing", "recording_voice", "upload_photo", "upload_doc", "upload_video"}
_ALLOWED_REASONS = {"success", "error", "timeout", "floodwait"}


def _facade():
    """Lazy import фасада — позволяет тестам patch'ить facade-атрибуты."""
    import src.core.prometheus_metrics as _pm  # noqa: PLC0415

    return _pm


def _normalize_action(action: str | None) -> str:
    """Нормализация action label: только из whitelist, иначе `unknown`."""
    a = (action or "unknown").strip().lower()
    if a not in _ALLOWED_ACTIONS:
        return "unknown"
    return a


def _normalize_reason(reason: str | None) -> str:
    """Нормализация reason label: только из whitelist, иначе `error`."""
    r = (reason or "error").strip().lower()
    if r not in _ALLOWED_REASONS:
        return "error"
    return r


def _chat_bucket(chat_id: int | str | None) -> str:
    """`hash(chat_id) % 100` → строка «00».. «99» — PII-safe label.

    None / нерасшифровываемый chat_id → «00» (стабильно, не падаем).
    Используем `abs()` чтобы избежать отрицательных значений.
    """
    if chat_id is None:
        return "00"
    try:
        # Стабильный hash от строкового представления — Telegram chat_id может
        # быть int (DM/группа) или str (username). Берём по модулю 100.
        bucket = abs(hash(str(chat_id))) % 100
        return f"{bucket:02d}"
    except Exception:  # noqa: BLE001
        return "00"


def record_typing_started(action: str) -> None:
    """Счётчик старта typing indicator. Fail-safe."""
    try:
        a = _normalize_action(action)
        pm = _facade()
        if pm.krab_typing_indicator_started_total is not None:
            pm.krab_typing_indicator_started_total.labels(action=a).inc()
    except Exception:  # noqa: BLE001
        pass


def record_typing_cancelled(reason: str, duration_sec: float) -> None:
    """Счётчик завершения typing indicator + длительность в histogram. Fail-safe."""
    try:
        r = _normalize_reason(reason)
        pm = _facade()
        if pm.krab_typing_indicator_cancelled_total is not None:
            pm.krab_typing_indicator_cancelled_total.labels(reason=r).inc()

        if pm.krab_typing_indicator_duration_seconds is not None:
            d = float(duration_sec)
            if d < 0.0:
                d = 0.0
            pm.krab_typing_indicator_duration_seconds.observe(d)
    except Exception:  # noqa: BLE001
        pass


def record_typing_floodwait(chat_id: int | str | None) -> None:
    """Счётчик FloodWait по корзинам chat_id (без PII). Fail-safe."""
    try:
        bucket = _chat_bucket(chat_id)
        pm = _facade()
        if pm.krab_typing_indicator_floodwait_total is not None:
            pm.krab_typing_indicator_floodwait_total.labels(chat_id_bucket=bucket).inc()
    except Exception:  # noqa: BLE001
        pass


__all__ = [
    "krab_typing_indicator_started_total",
    "krab_typing_indicator_cancelled_total",
    "krab_typing_indicator_duration_seconds",
    "krab_typing_indicator_floodwait_total",
    "record_typing_started",
    "record_typing_cancelled",
    "record_typing_floodwait",
]
