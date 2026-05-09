"""Wave 53-G: Codex quota recovery probe — persisted per-account state.

Хранит:
- failures per account → exponential backoff (base=1h, max=24h)
- last_probe_ts / next_probe_ts per account (ISO)
- global_stats: total_probes / successes / failures

Файл: ~/.openclaw/krab_runtime_state/codex_quota_probe_state.json

Схема:
{
  "accounts": {
    "primary": {
      "failures": 0,
      "last_probe_ts": "2026-05-10T12:00:00+00:00",
      "next_probe_ts": "2026-05-10T13:00:00+00:00"
    },
    ...
  },
  "global_stats": {
    "total_probes": N,
    "successes": N,
    "failures": N
  }
}

Формулы:
- jitter: interval = base ± rand(0, 0.2 * base)   (±20%)
- backoff: min(base * 2^failures, MAX_INTERVAL_SEC)
- reset:   failures → 0 при успехе
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..core.logger import get_logger

logger = get_logger(__name__)

PROBE_STATE_FILE = Path.home() / ".openclaw/krab_runtime_state/codex_quota_probe_state.json"

# Базовый интервал — 1 час в секундах
BASE_INTERVAL_SEC: int = 3600
# Максимальный интервал backoff — 24 часа
MAX_INTERVAL_SEC: int = 24 * 3600
# Коэффициент jitter — ±20% от интервала
JITTER_FACTOR: float = 0.20


def _load_probe_state() -> dict[str, Any]:
    """Загружает persisted probe state. Graceful при ошибке."""
    if not PROBE_STATE_FILE.exists():
        return {"accounts": {}, "global_stats": {"total_probes": 0, "successes": 0, "failures": 0}}
    try:
        return json.loads(PROBE_STATE_FILE.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("quota_probe_state_load_error", error=str(exc))
        return {"accounts": {}, "global_stats": {"total_probes": 0, "successes": 0, "failures": 0}}


def _save_probe_state(state: dict[str, Any]) -> None:
    """Атомарно записывает probe state."""
    PROBE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROBE_STATE_FILE.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(PROBE_STATE_FILE)
    except Exception as exc:  # noqa: BLE001
        logger.warning("quota_probe_state_save_error", error=str(exc))
        try:
            tmp.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass


def compute_next_interval(failures: int, base_sec: int = BASE_INTERVAL_SEC) -> float:
    """Вычисляет следующий интервал с backoff + jitter.

    Формула:
      raw = min(base * 2^failures, MAX_INTERVAL_SEC)
      jitter_delta = rand(0, JITTER_FACTOR * raw)  — добавляется или вычитается
      result = raw ± jitter_delta

    Args:
        failures: количество последовательных неудач для аккаунта.
        base_sec: базовый интервал (по умолчанию BASE_INTERVAL_SEC).

    Returns:
        Интервал в секундах с применённым jitter.
    """
    # Экспоненциальный backoff с потолком 24h
    raw = min(base_sec * (2**failures), MAX_INTERVAL_SEC)
    # Jitter: случайно добавляем или вычитаем до 20%
    jitter_range = JITTER_FACTOR * raw
    jitter_delta = random.uniform(-jitter_range, jitter_range)
    result = raw + jitter_delta
    # Не опускаемся ниже 60 секунд
    return max(60.0, result)


def is_account_in_cooldown(account_name: str) -> bool:
    """Возвращает True, если аккаунт ещё в cooldown и пробу надо пропустить."""
    state = _load_probe_state()
    acct = state.get("accounts", {}).get(account_name, {})
    next_probe = acct.get("next_probe_ts")
    if not next_probe:
        return False
    try:
        next_dt = datetime.fromisoformat(next_probe)
        return datetime.now(timezone.utc) < next_dt
    except Exception:  # noqa: BLE001
        return False


def record_probe_attempt(account_name: str) -> None:
    """Фиксирует факт попытки пробы (до результата)."""
    state = _load_probe_state()
    # Обновляем global stats
    stats = state.setdefault("global_stats", {"total_probes": 0, "successes": 0, "failures": 0})
    stats["total_probes"] = stats.get("total_probes", 0) + 1
    # last_probe_ts для аккаунта
    acct = state.setdefault("accounts", {}).setdefault(account_name, {})
    acct["last_probe_ts"] = datetime.now(timezone.utc).isoformat()
    _save_probe_state(state)
    logger.info(
        "quota_probe_attempt",
        account=account_name,
        total_probes=stats["total_probes"],
    )


def record_probe_success(account_name: str) -> None:
    """Фиксирует успешную пробу: сбрасывает failures, вычисляет следующий интервал."""
    state = _load_probe_state()
    stats = state.setdefault("global_stats", {"total_probes": 0, "successes": 0, "failures": 0})
    stats["successes"] = stats.get("successes", 0) + 1
    acct = state.setdefault("accounts", {}).setdefault(account_name, {})
    # Сбрасываем счётчик неудач
    acct["failures"] = 0
    now = datetime.now(timezone.utc)
    acct["last_probe_ts"] = now.isoformat()
    # Следующая проба через base_interval ± jitter (без backoff — failures=0)
    next_interval = compute_next_interval(failures=0)
    next_ts = now + timedelta(seconds=next_interval)
    acct["next_probe_ts"] = next_ts.isoformat()
    _save_probe_state(state)
    logger.info(
        "quota_probe_success",
        account=account_name,
        next_probe_in_sec=int(next_interval),
        total_successes=stats["successes"],
    )


def record_probe_failure(account_name: str) -> float:
    """Фиксирует неудачу пробы: увеличивает failures, применяет exponential backoff.

    Returns:
        Интервал в секундах до следующей попытки.
    """
    state = _load_probe_state()
    stats = state.setdefault("global_stats", {"total_probes": 0, "successes": 0, "failures": 0})
    stats["failures"] = stats.get("failures", 0) + 1
    acct = state.setdefault("accounts", {}).setdefault(account_name, {})
    # Увеличиваем failures для этого аккаунта
    current_failures = acct.get("failures", 0) + 1
    acct["failures"] = current_failures
    now = datetime.now(timezone.utc)
    acct["last_probe_ts"] = now.isoformat()
    # Backoff: base * 2^failures, capped at 24h, с jitter
    next_interval = compute_next_interval(failures=current_failures)
    next_ts = now + timedelta(seconds=next_interval)
    acct["next_probe_ts"] = next_ts.isoformat()
    _save_probe_state(state)
    logger.warning(
        "quota_probe_failure_backoff",
        account=account_name,
        consecutive_failures=current_failures,
        next_probe_in_sec=int(next_interval),
        total_failures=stats["failures"],
    )
    return next_interval


def get_probe_stats() -> dict[str, Any]:
    """Возвращает текущий probe state для CLI/диагностики."""
    return _load_probe_state()


__all__ = [
    "BASE_INTERVAL_SEC",
    "MAX_INTERVAL_SEC",
    "JITTER_FACTOR",
    "PROBE_STATE_FILE",
    "compute_next_interval",
    "get_probe_stats",
    "is_account_in_cooldown",
    "record_probe_attempt",
    "record_probe_failure",
    "record_probe_success",
]
