# -*- coding: utf-8 -*-
"""
Wave 123: Prometheus метрики для Voice Gateway client (TTS observability).

Voice Gateway (`:8090`) вызывает платный TTS (ElevenLabs / Google) для
sessions. До этой волны cost/latency Voice Gateway не виден в Prometheus.

Метрики:
- `krab_voice_gateway_requests_total{outcome}` — Counter ok/error/timeout.
- `krab_voice_gateway_request_duration_seconds` — Histogram latency,
  buckets 0.5 / 1 / 2 / 5 / 10 / 30 сек (TTS обычно 0.5-5 сек, edge до 30).
- `krab_voice_gateway_chars_total` — Counter озвученных символов (только ok).
- `krab_voice_gateway_cost_eur_total` — Counter projected cost EUR (только ok).

Cost rate берётся из env `KRAB_TTS_COST_PER_CHAR` (EUR per char) lazy при
каждом вызове helper'a — даёт hot-reload без рестарта.

Fail-safe: при отсутствии `prometheus_client` (slim test env) используется
no-op заглушка; ошибки prometheus_client глотаются в helper'e.
"""

from __future__ import annotations

import os
from typing import Any

try:
    from prometheus_client import Counter, Histogram  # type: ignore[import-not-found]

    _HAS_PROM = True
except Exception:  # pragma: no cover — slim env без prometheus_client

    class _Noop:
        def labels(self, *_a: Any, **_kw: Any) -> "_Noop":
            return self

        def inc(self, *_a: Any, **_kw: Any) -> None:
            return None

        def observe(self, *_a: Any, **_kw: Any) -> None:
            return None

    Counter = Histogram = _Noop  # type: ignore[assignment,misc]
    _HAS_PROM = False


# Default cost rate если env не задан — 0.0 (cost не списывается).
# ElevenLabs typical pricing: ~$0.00018/char Pro tier ⇒ €0.000165/char.
_DEFAULT_COST_PER_CHAR_EUR: float = 0.0


def _resolve_cost_per_char() -> float:
    """Читает `KRAB_TTS_COST_PER_CHAR` lazy, чтобы hot-reload работал."""
    raw = os.getenv("KRAB_TTS_COST_PER_CHAR", "").strip()
    if not raw:
        return _DEFAULT_COST_PER_CHAR_EUR
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_COST_PER_CHAR_EUR
    return value if value >= 0.0 else _DEFAULT_COST_PER_CHAR_EUR


# Counter по исходу TTS-запроса. Labels: outcome=ok|error|timeout.
krab_voice_gateway_requests_total = Counter(
    "krab_voice_gateway_requests_total",
    "Voice Gateway TTS requests by outcome (Wave 123)",
    ["outcome"],
)

# Histogram длительности TTS-запроса. Buckets подобраны под expected p50/p99.
krab_voice_gateway_request_duration_seconds = Histogram(
    "krab_voice_gateway_request_duration_seconds",
    "Voice Gateway TTS request duration in seconds (Wave 123)",
    buckets=(0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
)

# Counter озвученных символов (только успешные запросы).
krab_voice_gateway_chars_total = Counter(
    "krab_voice_gateway_chars_total",
    "Total characters sent to Voice Gateway TTS (Wave 123)",
)

# Counter projected cost (EUR), только success.
krab_voice_gateway_cost_eur_total = Counter(
    "krab_voice_gateway_cost_eur_total",
    "Projected cost of Voice Gateway TTS calls in EUR (Wave 123)",
)


def record_voice_request(chars: int, outcome: str, duration_sec: float) -> None:
    """
    Зарегистрировать TTS-вызов в Prometheus.

    Args:
        chars: число символов в озвученном тексте; чарgы и cost списываются
               только при outcome == "ok" (error/timeout не тарифицируются).
        outcome: "ok" / "error" / "timeout".
        duration_sec: длительность вызова в секундах (включая retry/network).

    Fail-safe: все ошибки prometheus_client глотаются — hot-path не должен
    падать из-за телеметрии.
    """
    try:
        normalized_outcome = (outcome or "error").strip().lower()
        if normalized_outcome not in {"ok", "error", "timeout"}:
            normalized_outcome = "error"
        krab_voice_gateway_requests_total.labels(outcome=normalized_outcome).inc()
        if duration_sec >= 0.0:
            krab_voice_gateway_request_duration_seconds.observe(float(duration_sec))
        if normalized_outcome == "ok" and chars > 0:
            krab_voice_gateway_chars_total.inc(float(chars))
            rate = _resolve_cost_per_char()
            if rate > 0.0:
                krab_voice_gateway_cost_eur_total.inc(float(chars) * rate)
    except Exception:  # noqa: BLE001 — телеметрия не должна валить hot-path
        return


__all__ = [
    "krab_voice_gateway_requests_total",
    "krab_voice_gateway_request_duration_seconds",
    "krab_voice_gateway_chars_total",
    "krab_voice_gateway_cost_eur_total",
    "record_voice_request",
]
