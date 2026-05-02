# -*- coding: utf-8 -*-
"""
Codex-CLI subprocess health tracker (Wave 14-D — session 33).

Root cause: codex-cli subprocess (spawned by OpenClaw) occasionally hangs/deadlocks
on first-chunk delivery. Symptoms in production (2026-05-02):
- 909 failed health probes / day
- recurring `provider_timeout` errors for codex-cli/gpt-5.5
- user reports «отвечает не сразу, после ошибки повтор работает»
- pattern: first request hangs → cap fires (90s) → retry succeeds immediately
  (subprocess respawned by gateway).

This module tracks consecutive timeouts:
- 2+ failures in 5min → mark codex-cli unhealthy (skip for next 60s)
- caller checks `should_skip()` before issuing codex-cli requests
- successful response → reset counter

Singleton, thread-safe (asyncio + sync use both ok — uses simple module-level state).
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field

# Дефолты подобраны для production-сценария (Wave 14-D):
# - 45s — большинство codex-cli ответов приходят за <30s. 45s обозначает hang.
# - 2 timeout — позволяет случайному медленному запросу не выкосить provider.
# - 60s skip — достаточно для gateway respawn subprocess.
# - 5min window — фейлы старше 5 минут не учитываются (конфиг свежий).
_DEFAULT_FIRST_CHUNK_TIMEOUT_SEC = 45.0
_DEFAULT_FAILURES_THRESHOLD = 2
_DEFAULT_SKIP_DURATION_SEC = 60.0
_DEFAULT_FAILURE_WINDOW_SEC = 300.0


def _read_float_env(name: str, default: float) -> float:
    try:
        raw = os.getenv(name)
        if raw is None or not raw.strip():
            return default
        val = float(raw)
        return val if val > 0 else default
    except (TypeError, ValueError):
        return default


def _read_int_env(name: str, default: int) -> int:
    try:
        raw = os.getenv(name)
        if raw is None or not raw.strip():
            return default
        val = int(raw)
        return val if val > 0 else default
    except (TypeError, ValueError):
        return default


@dataclass
class CodexCliHealthState:
    """Хранит активный счётчик timeout'ов и время до конца skip-окна."""

    failure_timestamps: list[float] = field(default_factory=list)
    skip_until_ts: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # ---- параметры (читаются из env, можно override через monkeypatch) ----
    first_chunk_timeout_sec: float = field(
        default_factory=lambda: _read_float_env(
            "KRAB_CODEX_CLI_FIRST_CHUNK_TIMEOUT_SEC", _DEFAULT_FIRST_CHUNK_TIMEOUT_SEC
        )
    )
    failures_threshold: int = field(
        default_factory=lambda: _read_int_env(
            "KRAB_CODEX_CLI_FAILURES_THRESHOLD", _DEFAULT_FAILURES_THRESHOLD
        )
    )
    skip_duration_sec: float = field(
        default_factory=lambda: _read_float_env(
            "KRAB_CODEX_CLI_SKIP_DURATION_SEC", _DEFAULT_SKIP_DURATION_SEC
        )
    )
    failure_window_sec: float = field(
        default_factory=lambda: _read_float_env(
            "KRAB_CODEX_CLI_FAILURE_WINDOW_SEC", _DEFAULT_FAILURE_WINDOW_SEC
        )
    )

    def record_timeout(self, *, now: float | None = None) -> bool:
        """
        Регистрирует timeout codex-cli.

        Returns True если после этой регистрации пройден порог и активирован skip.
        """
        if now is None:
            now = time.monotonic()
        with self._lock:
            cutoff = now - self.failure_window_sec
            self.failure_timestamps = [t for t in self.failure_timestamps if t >= cutoff]
            self.failure_timestamps.append(now)
            if len(self.failure_timestamps) >= self.failures_threshold:
                self.skip_until_ts = now + self.skip_duration_sec
                return True
            return False

    def record_success(self, *, now: float | None = None) -> None:
        """Успешный ответ — сбрасываем все накопленные fail'ы и skip-окно."""
        if now is None:
            now = time.monotonic()
        with self._lock:
            self.failure_timestamps.clear()
            self.skip_until_ts = 0.0

    def should_skip(self, *, now: float | None = None) -> bool:
        """True если codex-cli сейчас помечен unhealthy (внутри skip-окна)."""
        if now is None:
            now = time.monotonic()
        with self._lock:
            if self.skip_until_ts <= 0.0:
                return False
            if now >= self.skip_until_ts:
                # окно прошло — сбрасываем (попробуем codex-cli снова)
                self.skip_until_ts = 0.0
                self.failure_timestamps.clear()
                return False
            return True

    def get_first_chunk_timeout(self) -> float:
        """Возвращает текущий cap на первый чанк codex-cli (0 = отключено)."""
        return float(self.first_chunk_timeout_sec) if self.first_chunk_timeout_sec > 0 else 0.0

    def reset(self) -> None:
        """Полный сброс (для тестов)."""
        with self._lock:
            self.failure_timestamps.clear()
            self.skip_until_ts = 0.0


# ---- module-level singleton ----
_state = CodexCliHealthState()


def get_state() -> CodexCliHealthState:
    """Singleton-доступ к health state (для тестов и runtime)."""
    return _state


def reset_state_for_tests() -> None:
    """Тестовая утилита — пересоздать state (env переменные перечитаются)."""
    global _state
    _state = CodexCliHealthState()


def is_codex_cli_model(model_id: str | None) -> bool:
    """Простой helper — нормализованная проверка codex-cli модели."""
    if not model_id:
        return False
    lowered = str(model_id).lower()
    return "codex" in lowered and "cli" in lowered
