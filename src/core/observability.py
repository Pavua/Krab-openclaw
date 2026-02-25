# -*- coding: utf-8 -*-
"""
Observability Core Module.
Tracks the system state, metrics, and event timeline for deep health diagnostics.
"""
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import structlog

logger = structlog.get_logger("Observability")

class LatencyTracker:
    """Ring buffer for calculating p50/p95 latencies over a sliding window of recent requests."""
    def __init__(self, max_size: int = 1000):
        self._latencies: deque[float] = deque(maxlen=max_size)
        
    def add(self, latency_ms: float) -> None:
        self._latencies.append(latency_ms)
        
    def get_percentile(self, p: float) -> float:
        if not self._latencies:
            return 0.0
        sorted_latencies = sorted(self._latencies)
        k = (len(sorted_latencies) - 1) * p
        f = int(k)
        c = min(f + 1, len(sorted_latencies) - 1)
        if f == c:
            return sorted_latencies[f]
        d0 = sorted_latencies[f] * (c - k)
        d1 = sorted_latencies[c] * (k - f)
        return d0 + d1

class MetricsRegistry:
    """In-memory metrics: counters, gauges, and latencies."""
    def __init__(self):
        self._counters: Dict[str, int] = {}
        self._gauges: Dict[str, float] = {}
        self._latency_tracker = LatencyTracker()

    def add_latency(self, latency_ms: float) -> None:
        self._latency_tracker.add(latency_ms)

    def inc(self, name: str, value: int = 1) -> None:
        self._counters[name] = self._counters.get(name, 0) + value

    def set_gauge(self, name: str, value: float) -> None:
        self._gauges[name] = float(value)

    def get_snapshot(self) -> Dict[str, Dict[str, Any]]:
        return {
            "counters": dict(self._counters),
            "gauges": dict(self._gauges),
            "latencies": {
                "p50_ms": round(self._latency_tracker.get_percentile(0.50), 2),
                "p95_ms": round(self._latency_tracker.get_percentile(0.95), 2),
            }
        }

class EventTimeline:
    """Ring buffer for the last N events."""
    def __init__(self, max_size: int = 200):
        self._events = deque(maxlen=max_size)

    def append(self, name: str, severity: str = "info", details: Optional[Dict[str, Any]] = None, channel: str = "system") -> None:
        """
        severity: info, warn, error, critical
        """
        event = {
            "ts": time.time(),
            "time_iso": datetime.now(timezone.utc).isoformat(),
            "name": name,
            "severity": severity,
            "channel": channel,
            "details": details or {}
        }
        self._events.append(event)
        
        # Also increment standard metrics for some events to correlate
        metrics.inc(f"event.{name}")

    def get_events(self, limit: int = 200, min_severity: Optional[str] = None, channel: Optional[str] = None) -> List[Dict[str, Any]]:
        result = []
        severity_levels = {"info": 0, "warn": 1, "error": 2, "critical": 3}
        min_level = severity_levels.get((min_severity or "").lower(), 0)

        for e in reversed(self._events):
            if channel and e["channel"] != channel:
                continue
            lvl = severity_levels.get(e["severity"].lower(), 0)
            if lvl < min_level:
                continue
            result.append(e)
            if len(result) >= limit:
                break
        return result

# Global Singletons specifically for zero-dependency instrumentation:
metrics = MetricsRegistry()
timeline = EventTimeline(max_size=200)

def track_event(name: str, severity: str = "info", details: Optional[Dict[str, Any]] = None, channel: str = "system") -> None:
    timeline.append(name, severity, details, channel)

def mask_secrets(payload: Any) -> Any:
    """Masks sensitive information in dictionaries and lists."""
    if isinstance(payload, dict):
        masked = {}
        for k, v in payload.items():
            k_lower = str(k).lower()
            if any(s in k_lower for s in ("api_key", "secret", "token", "password", "authorization")):
                masked[k] = "***MASKED***"
            else:
                masked[k] = mask_secrets(v)
        return masked
    elif isinstance(payload, list):
        return [mask_secrets(item) for item in payload]
    return payload

def build_ops_response(
    status: str,
    error_code: str = "",
    summary: str = "",
    data: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Standardized operational response format.
    status: ok | degraded | failed
    """
    return {
        "status": status,
        "error_code": error_code,
        "summary": summary,
        "data": data or {}
    }

def get_observability_snapshot() -> Dict[str, Any]:
    return {
        "metrics": mask_secrets(metrics.get_snapshot()),
        "timeline_tail": mask_secrets(timeline.get_events(limit=10))
    }
