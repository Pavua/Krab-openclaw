# -*- coding: utf-8 -*-
"""Wave 138: voice STT (Whisper) cost + latency tracking.

Метрики транскрипции голосовых сообщений:
  - krab_voice_stt_total{provider, outcome}                   — счётчик попыток
  - krab_voice_stt_duration_seconds{provider}                 — latency
  - krab_voice_stt_cost_eur_total{provider}                   — расходы (только paid path)

Provider:
  - local_whisper   — mlx_whisper (Apple Silicon, free)
  - voice_gateway   — Krab Voice Gateway локальный (free)
  - openai_whisper  — OpenAI Whisper API (paid, $0.006/min ≈ 0.0055 EUR/min)

Outcome: ok / error / timeout.

Все вызовы fail-safe: если prometheus_client недоступен → None метрики,
helper молча игнорирует. Тесты patch'ат через facade `prometheus_metrics`.
"""

from __future__ import annotations

try:
    from prometheus_client import Counter as _CounterSTT  # type: ignore[import-not-found]
    from prometheus_client import Histogram as _HistogramSTT  # type: ignore[import-not-found]

    krab_voice_stt_total = _CounterSTT(
        "krab_voice_stt_total",
        "Voice STT transcription attempts per provider/outcome (Wave 138)",
        ["provider", "outcome"],
    )
    krab_voice_stt_duration_seconds = _HistogramSTT(
        "krab_voice_stt_duration_seconds",
        "Voice STT latency seconds per provider (Wave 138)",
        ["provider"],
        buckets=(0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0),
    )
    krab_voice_stt_cost_eur_total = _CounterSTT(
        "krab_voice_stt_cost_eur_total",
        "Voice STT estimated cost EUR (Wave 138, paid providers only)",
        ["provider"],
    )
except Exception:  # noqa: BLE001 — slim env без prometheus_client
    krab_voice_stt_total = None  # type: ignore[assignment]
    krab_voice_stt_duration_seconds = None  # type: ignore[assignment]
    krab_voice_stt_cost_eur_total = None  # type: ignore[assignment]


# Разрешённые provider / outcome — лишние значения нормализуются
_ALLOWED_PROVIDERS = {"local_whisper", "voice_gateway", "openai_whisper"}
_ALLOWED_OUTCOMES = {"ok", "error", "timeout"}

# Paid providers + EUR cost-per-minute (OpenAI Whisper: $0.006/min → ~0.0055 EUR/min @ EURUSD≈1.09)
_PAID_PROVIDER_COST_EUR_PER_MIN: dict[str, float] = {
    "openai_whisper": 0.0055,
}


def _facade():
    """Lazy import фасада — позволяет тестам patch'ить facade-атрибуты."""
    import src.core.prometheus_metrics as _pm  # noqa: PLC0415

    return _pm


def _normalize_provider(provider: str | None) -> str:
    p = (provider or "unknown").strip().lower()
    if p not in _ALLOWED_PROVIDERS:
        return "unknown"
    return p


def _normalize_outcome(outcome: str | None) -> str:
    o = (outcome or "error").strip().lower()
    if o not in _ALLOWED_OUTCOMES:
        return "error"
    return o


def record_voice_stt(
    *,
    provider: str,
    outcome: str,
    duration_seconds: float | None = None,
    audio_seconds: float | None = None,
) -> None:
    """Записать метрики одной транскрипции. Fail-safe.

    provider:         local_whisper | voice_gateway | openai_whisper
    outcome:          ok | error | timeout
    duration_seconds: latency самой STT-операции (для histogram). None — пропустить.
    audio_seconds:    длительность исходного аудио (для cost EUR).
                      Cost учитывается только для paid провайдеров.
    """
    try:
        p = _normalize_provider(provider)
        o = _normalize_outcome(outcome)
        pm = _facade()

        if pm.krab_voice_stt_total is not None:
            pm.krab_voice_stt_total.labels(provider=p, outcome=o).inc()

        if duration_seconds is not None and pm.krab_voice_stt_duration_seconds is not None:
            d = float(duration_seconds)
            if d < 0.0:
                d = 0.0
            pm.krab_voice_stt_duration_seconds.labels(provider=p).observe(d)

        # Cost — только paid providers + успешные/timeout (мы потратились на запрос)
        if (
            audio_seconds is not None
            and p in _PAID_PROVIDER_COST_EUR_PER_MIN
            and pm.krab_voice_stt_cost_eur_total is not None
        ):
            secs = float(audio_seconds)
            if secs > 0.0:
                cost_eur = (secs / 60.0) * _PAID_PROVIDER_COST_EUR_PER_MIN[p]
                pm.krab_voice_stt_cost_eur_total.labels(provider=p).inc(cost_eur)
    except Exception:  # noqa: BLE001
        pass


__all__ = [
    "krab_voice_stt_total",
    "krab_voice_stt_duration_seconds",
    "krab_voice_stt_cost_eur_total",
    "record_voice_stt",
]
