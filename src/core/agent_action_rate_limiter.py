# -*- coding: utf-8 -*-
"""
Wave 44-S-safety-net: per-action-type rate limiter для агентных действий.

Цель: защита от runaway-loop / hallucination-burst, когда LLM вызывает один
и тот же tool десятками раз за секунду из-за bug или prompt injection.

### Семантика

- Per-action bucket с sliding window (60s).
- Default budgets:
    send_to_swarm    : 10/min
    screenshot       :  5/min
    run_command      : 30/min
    send_dm          : 20/min
    bash             : 60/min
    default          : 30/min (для незарегистрированных action types)
- Burst trip: если total actions/min > 50 — `tripped=True`, требуется
  явный `release_trip()` от owner, иначе все check_action() вернут False.

### НЕ трогает

- Telegram-level outbound (это `telegram_rate_limiter.py`).
- Sync vs async — оба safe (используется `threading.Lock`, no asyncio).
- Persistence — sliding window in-memory; trip state persists в JSON.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

DEFAULT_BUDGETS: dict[str, int] = {
    "send_to_swarm": 10,
    "screenshot": 5,
    "run_command": 30,
    "send_dm": 20,
    "bash": 60,
    "default": 30,
}
WINDOW_SEC = 60.0
BURST_THRESHOLD = 50  # total per-min across ALL action types

_TRIP_STATE_PATH = Path(
    os.environ.get(
        "KRAB_AGENT_RATE_TRIP_PATH",
        "/Users/pablito/.openclaw/krab_runtime_state/agent_action_trip.json",
    )
)


class AgentActionRateLimiter:
    """Thread-safe per-action-type sliding window limiter."""

    def __init__(
        self,
        budgets: dict[str, int] | None = None,
        window_sec: float = WINDOW_SEC,
        burst_threshold: int = BURST_THRESHOLD,
        trip_state_path: Path = _TRIP_STATE_PATH,
    ) -> None:
        self._budgets = dict(budgets or DEFAULT_BUDGETS)
        self._window_sec = window_sec
        self._burst_threshold = burst_threshold
        self._trip_state_path = trip_state_path
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._tripped = self._load_trip_state()

    def _budget_for(self, action: str) -> int:
        return self._budgets.get(action, self._budgets["default"])

    def _prune(self, action: str, now: float) -> None:
        bucket = self._buckets[action]
        cutoff = now - self._window_sec
        while bucket and bucket[0] < cutoff:
            bucket.popleft()

    def _total_in_window(self, now: float) -> int:
        cutoff = now - self._window_sec
        total = 0
        for action, bucket in self._buckets.items():
            # prune in-place
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            total += len(bucket)
        return total

    def check_action(self, action: str) -> tuple[bool, str]:
        """
        Returns (allowed, reason). НЕ записывает trip — это делает trip_burst().

        Если tripped — всегда False с reason="tripped".
        Если per-action budget exhausted — False с reason="budget_exhausted:<N>/<budget>".
        Иначе — True, reason="ok".
        """
        with self._lock:
            if self._tripped:
                return False, "tripped"
            now = time.monotonic()
            self._prune(action, now)
            count = len(self._buckets[action])
            budget = self._budget_for(action)
            if count >= budget:
                return False, f"budget_exhausted:{count}/{budget}"
            return True, "ok"

    def record_action(self, action: str) -> dict[str, int | bool | str]:
        """
        Атомарно: prune + check + record. Returns dict с состоянием.
        НЕ raise — caller должен сам решать что делать с allowed=False.
        """
        with self._lock:
            if self._tripped:
                return {
                    "allowed": False,
                    "reason": "tripped",
                    "count": len(self._buckets[action]),
                    "budget": self._budget_for(action),
                }
            now = time.monotonic()
            self._prune(action, now)
            count = len(self._buckets[action])
            budget = self._budget_for(action)
            if count >= budget:
                return {
                    "allowed": False,
                    "reason": "budget_exhausted",
                    "count": count,
                    "budget": budget,
                }
            self._buckets[action].append(now)
            # Burst detection across all actions
            total = self._total_in_window(now)
            if total > self._burst_threshold:
                self._tripped = True
                self._save_trip_state(reason=f"burst:{total}>{self._burst_threshold}")
                return {
                    "allowed": False,
                    "reason": "burst_tripped",
                    "count": count + 1,
                    "budget": budget,
                    "total": total,
                }
            return {
                "allowed": True,
                "reason": "ok",
                "count": count + 1,
                "budget": budget,
            }

    def is_tripped(self) -> bool:
        with self._lock:
            return self._tripped

    def release_trip(self) -> None:
        """Снять trip — должно вызываться только owner-командой."""
        with self._lock:
            self._tripped = False
            try:
                self._trip_state_path.unlink()
            except FileNotFoundError:
                pass

    def _load_trip_state(self) -> bool:
        try:
            data = json.loads(self._trip_state_path.read_text("utf-8"))
            return bool(data.get("tripped", False))
        except (OSError, ValueError):
            return False

    def _save_trip_state(self, reason: str) -> None:
        try:
            self._trip_state_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "tripped": True,
                "reason": reason,
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            self._trip_state_path.write_text(json.dumps(payload), "utf-8")
        except OSError:
            pass


_singleton: AgentActionRateLimiter | None = None
_singleton_lock = threading.Lock()


def get_limiter() -> AgentActionRateLimiter:
    """Process-wide singleton. Lazy-init на первом вызове."""
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = AgentActionRateLimiter()
    return _singleton
