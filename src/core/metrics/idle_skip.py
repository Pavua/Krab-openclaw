# -*- coding: utf-8 -*-
"""S62 W6: Prometheus counters for idle observability skip markers.

Покрывает четыре idle/skip потока, добавленных в S55-S61:

- ``krab_bypass_idle_skip_total{reason}`` — S55 D: local primary bypass
  пропуски (``has_photo`` / ``cloud_or_cli_model``).
- ``krab_vision_idle_skip_total{reason}`` — S56 C: Phase 1 vision idle skip
  (``cloud_route_preferred`` и т.п.).
- ``krab_translator_idle_skip_total{reason}`` — S61 W2: Phase 2 local
  translator idle skip.
- ``krab_verifier_samples_total{status}`` — S57 P3.1: local draft verifier
  события (``sampled`` / ``skipped_not_sampled`` / ``skipped_env_disabled``
  / ``skipped_empty_input``).
- ``krab_codex_idle_skip_total{reason}`` — S62 W4: codex CLI subprocess
  bypass idle skip (``weekly_quota_exhausted`` / ``disabled_via_env`` /
  ``subprocess_unavailable``). Добавлено S63 W1.

Pattern: ``pressure_aware.py`` (Wave 86) — prometheus_client optional,
in-memory dict для render fallback в ``collect.py`` если client отсутствует
или для тестов. Best-effort: helper-функции никогда не бросают.
"""

from __future__ import annotations

try:
    from prometheus_client import Counter as _CounterIS  # type: ignore[import-not-found]

    _bypass_idle_skip_total = _CounterIS(
        "krab_bypass_idle_skip_total",
        "S55 D: local primary bypass idle skips by reason",
        ["reason"],
    )
    _vision_idle_skip_total = _CounterIS(
        "krab_vision_idle_skip_total",
        "S56 C: Phase 1 vision (frame_describe_local) idle skips by reason",
        ["reason"],
    )
    _translator_idle_skip_total = _CounterIS(
        "krab_translator_idle_skip_total",
        "S61 W2: Phase 2 local translator idle skips by reason",
        ["reason"],
    )
    _verifier_samples_total = _CounterIS(
        "krab_verifier_samples_total",
        "S57 P3.1: local draft verifier sample events by status",
        ["status"],
    )
    _codex_idle_skip_total = _CounterIS(
        "krab_codex_idle_skip_total",
        "S62 W4: codex CLI subprocess bypass idle skips by reason",
        ["reason"],
    )
except Exception:  # noqa: BLE001 — prometheus_client optional
    _bypass_idle_skip_total = None  # type: ignore[assignment]
    _vision_idle_skip_total = None  # type: ignore[assignment]
    _translator_idle_skip_total = None  # type: ignore[assignment]
    _verifier_samples_total = None  # type: ignore[assignment]
    _codex_idle_skip_total = None  # type: ignore[assignment]


# Сырой in-memory счётчик (text render fallback + тестовая инспекция).
_BYPASS_IDLE_SKIP_COUNTER: dict[str, int] = {}
_VISION_IDLE_SKIP_COUNTER: dict[str, int] = {}
_TRANSLATOR_IDLE_SKIP_COUNTER: dict[str, int] = {}
_VERIFIER_SAMPLES_COUNTER: dict[str, int] = {}
_CODEX_IDLE_SKIP_COUNTER: dict[str, int] = {}


def _inc(
    in_memory: dict[str, int],
    prom_counter: object | None,
    label_value: str,
) -> None:
    """Internal best-effort inc — обновляет dict + prometheus_client если есть."""
    try:
        key = str(label_value) if label_value else "unknown"
        in_memory[key] = in_memory.get(key, 0) + 1
        if prom_counter is not None:
            # mypy: prometheus Counter имеет .labels(...).inc()
            prom_counter.labels(reason=key).inc()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 — инструментация best-effort
        pass


def inc_bypass_idle_skip(reason: str) -> None:
    """S55 D: фиксирует пропуск local primary bypass. Best-effort."""
    _inc(_BYPASS_IDLE_SKIP_COUNTER, _bypass_idle_skip_total, reason)


def inc_vision_idle_skip(reason: str) -> None:
    """S56 C: фиксирует пропуск local vision path. Best-effort."""
    _inc(_VISION_IDLE_SKIP_COUNTER, _vision_idle_skip_total, reason)


def inc_translator_idle_skip(reason: str) -> None:
    """S61 W2: фиксирует пропуск local translator. Best-effort."""
    _inc(_TRANSLATOR_IDLE_SKIP_COUNTER, _translator_idle_skip_total, reason)


def inc_codex_idle_skip(reason: str) -> None:
    """S62 W4: фиксирует пропуск codex CLI subprocess bypass. Best-effort.

    ``reason`` — одно из: ``weekly_quota_exhausted`` / ``disabled_via_env`` /
    ``subprocess_unavailable``.
    """
    _inc(_CODEX_IDLE_SKIP_COUNTER, _codex_idle_skip_total, reason)


def inc_verifier_sample(status: str) -> None:
    """S57 P3.1: фиксирует sample event верификатора. Best-effort.

    ``status`` — одно из: ``sampled`` / ``skipped_not_sampled`` /
    ``skipped_env_disabled`` / ``skipped_empty_input``.
    """
    try:
        key = str(status) if status else "unknown"
        _VERIFIER_SAMPLES_COUNTER[key] = _VERIFIER_SAMPLES_COUNTER.get(key, 0) + 1
        if _verifier_samples_total is not None:
            _verifier_samples_total.labels(status=key).inc()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass


__all__ = [
    "_BYPASS_IDLE_SKIP_COUNTER",
    "_CODEX_IDLE_SKIP_COUNTER",
    "_TRANSLATOR_IDLE_SKIP_COUNTER",
    "_VERIFIER_SAMPLES_COUNTER",
    "_VISION_IDLE_SKIP_COUNTER",
    "_bypass_idle_skip_total",
    "_codex_idle_skip_total",
    "_translator_idle_skip_total",
    "_verifier_samples_total",
    "_vision_idle_skip_total",
    "inc_bypass_idle_skip",
    "inc_codex_idle_skip",
    "inc_translator_idle_skip",
    "inc_verifier_sample",
    "inc_vision_idle_skip",
]
