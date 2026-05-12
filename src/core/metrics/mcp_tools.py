# -*- coding: utf-8 -*-
"""Prometheus-метрики Wave 126 MCP per-tool invocation latency.

Histogram ``krab_mcp_tool_duration_seconds{server, tool}`` — длительность вызова.
Counter ``krab_mcp_tool_calls_total{server, tool, outcome}`` — итоги (ok/error/timeout).

Cardinality концерн: server * tool ~ 11 * 10 ≈ 100 серий на histogram,
плюс buckets (8) → ~800 серий. Counter outcome ∈ {ok, error, timeout} даёт
~300 серий. В пределах нормы для Prometheus.

Если ``prometheus_client`` недоступен — объекты None, helper-ы no-op.
Hot-path никогда не ломаем.
"""

from __future__ import annotations

import time
from typing import Any

try:
    from prometheus_client import Counter as _Counter  # type: ignore[import-not-found]
    from prometheus_client import Histogram as _Histogram  # type: ignore[import-not-found]

    krab_mcp_tool_duration_seconds: Any = _Histogram(
        "krab_mcp_tool_duration_seconds",
        "MCP tool invocation duration (Wave 126) by server and tool",
        ["server", "tool"],
        buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
    )
    krab_mcp_tool_calls_total: Any = _Counter(
        "krab_mcp_tool_calls_total",
        "MCP tool invocation counter (Wave 126) by server/tool/outcome",
        ["server", "tool", "outcome"],
    )
except Exception:  # noqa: BLE001 - prometheus_client опционален
    krab_mcp_tool_duration_seconds = None
    krab_mcp_tool_calls_total = None


_VALID_OUTCOMES = ("ok", "error", "timeout")


def _split_tool_name(full_tool_name: str) -> tuple[str, str]:
    """Разбирает имя на (server, tool).

    server__tool → (server, tool); voice:* → (voice, rest);
    native (peekaboo/web_search/tor_fetch) → (native, tool).
    """
    raw = full_tool_name or ""
    name = raw.strip()
    if not name:
        return ("unknown", "unknown")
    if "__" in name:
        server, tool = name.split("__", 1)
        return (server[:80] or "unknown", tool[:80] or "unknown")
    if name.startswith("voice:"):
        return ("voice", name.split(":", 1)[1][:80] or "unknown")
    # peekaboo / web_search / tor_fetch / userbot_self / vpn_*
    return ("native", name[:80])


def record_tool_call(
    *,
    full_tool_name: str,
    duration_seconds: float,
    outcome: str,
) -> None:
    """Записывает результат одного MCP tool invocation.

    outcome ∈ {ok, error, timeout}; invalid → "error".
    """
    server, tool = _split_tool_name(full_tool_name)
    safe_outcome = outcome if outcome in _VALID_OUTCOMES else "error"
    try:
        if krab_mcp_tool_duration_seconds is not None:
            krab_mcp_tool_duration_seconds.labels(server=server, tool=tool).observe(
                max(0.0, float(duration_seconds))
            )
    except Exception:  # noqa: BLE001
        pass
    try:
        if krab_mcp_tool_calls_total is not None:
            krab_mcp_tool_calls_total.labels(server=server, tool=tool, outcome=safe_outcome).inc()
    except Exception:  # noqa: BLE001
        pass


class ToolLatencyTimer:
    """Контекст-менеджер для wrap call_tool_unified.

    Использование:
        async with ToolLatencyTimer(full_tool_name) as t:
            ...
            t.mark_timeout()  # если поймали TimeoutError
    Иначе outcome автоматически = ok, при исключении = error.
    """

    def __init__(self, full_tool_name: str) -> None:
        self.full_tool_name = full_tool_name
        self._start: float = 0.0
        self._outcome: str = "ok"

    def __enter__(self) -> "ToolLatencyTimer":
        self._start = time.monotonic()
        return self

    def mark_timeout(self) -> None:
        self._outcome = "timeout"

    def mark_error(self) -> None:
        self._outcome = "error"

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        duration = max(0.0, time.monotonic() - self._start)
        outcome = self._outcome
        if exc_type is not None and outcome == "ok":
            if isinstance(exc, TimeoutError) or "Timeout" in (exc_type.__name__ or ""):
                outcome = "timeout"
            else:
                outcome = "error"
        record_tool_call(
            full_tool_name=self.full_tool_name,
            duration_seconds=duration,
            outcome=outcome,
        )
