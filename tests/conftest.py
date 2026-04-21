# -*- coding: utf-8 -*-
"""
tests/conftest.py — root-level autouse fixtures для изоляции тестов.

Проблема: 587 падений при полном прогоне `pytest tests/unit`, тогда как
каждый кластер файлов проходит 100% standalone.
Root cause: утечки состояния между файлами — мутируемые module-level
синглтоны, env-vars, sys.modules-патчи без восстановления.

Этот файл сбрасывает все известные источники утечек перед каждым тестом.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from typing import Any

import pytest
import structlog

# ---------------------------------------------------------------------------
# Sentinel — отличает "ключ отсутствовал" от "ключ был равен None".
# ---------------------------------------------------------------------------
_SENTINEL = object()


# ---------------------------------------------------------------------------
# Список sys.modules ключей, которые тесты подменяют без восстановления.
# Snapshot берётся СЕЙЧАС (при загрузке conftest), до запуска любого теста.
# ---------------------------------------------------------------------------
_MODULES_TO_GUARD = [
    "src.core.language_detect",
    "src.core.translator_engine",
    "src.core.auto_restart_manager",
    "src.core.provider_failover",
]

# Снимок оригинальных модулей до запуска тестов.
# Если модуль ещё не импортирован — _SENTINEL (восстановим удалением).
_MODULES_ORIGINAL: dict[str, Any] = {
    k: sys.modules.get(k, _SENTINEL) for k in _MODULES_TO_GUARD
}

# ---------------------------------------------------------------------------
# Env-переменные, которые тесты могут менять без monkeypatch.
# Снимок берётся при загрузке conftest.
# ---------------------------------------------------------------------------
_ENV_VARS_TO_GUARD = [
    "AUTO_REACTIONS_ENABLED",
    "KRAB_RAG_LLM_RERANK_ENABLED",
    "KRAB_CHROME_PROFILE_DIR",
    "KRAB_EXPERIMENTAL",
    "KRAB_AUTO_REACTION_RATE_LIMIT",
    "DEFAULT_WEATHER_CITY",
    "KRAB_PANEL_URL",
    "KRAB_PANEL_TIMEOUT_SEC",
    "WEB_API_KEY",
    "TELEGRAM_API_ID",
    "TELEGRAM_API_HASH",
    "OWNER_ID",
    "KRAB_SILENCE_MODE",
    "KRAB_NOTIFY_ENABLED",
]

_ENV_ORIGINAL: dict[str, Any] = {k: os.environ.get(k, _SENTINEL) for k in _ENV_VARS_TO_GUARD}

# ---------------------------------------------------------------------------
# Сохраняем глобальную конфигурацию structlog при загрузке conftest.
# test_correlation_id.py вызывает structlog.configure() с кастомными processors,
# что меняет глобальный pipeline — все последующие тесты получают PrintLogger
# который не принимает keyword args (TypeError: got unexpected keyword argument).
# ---------------------------------------------------------------------------
_STRUCTLOG_CONFIG_ORIGINAL: dict = structlog.get_config().copy()


@pytest.fixture(autouse=True)
def _reset_krab_global_state() -> Iterator[None]:
    """
    Сбрасывает все известные mutable module-level синглтоны перед тестом.

    Покрывает:
    - sys.modules патчи без восстановления (language_detect, translator_engine, etc.)
    - env-vars без monkeypatch
    - _error_counts в error_handler
    - _active_timers / _stopwatches в command_handlers
    - _ADAPTIVE_RERANK_COUNTER / _GUEST_LLM_SKIPPED_COUNTER в prometheus_metrics
    - _GEMINI_NONCE_MAP в gemini_cache_nonce
    - chat_filter_config singleton (in-memory rules)
    """
    # -- pre-test: восстанавливаем structlog конфигурацию --
    structlog.configure(**_STRUCTLOG_CONFIG_ORIGINAL)

    # -- pre-test: очищаем structlog contextvars --
    try:
        structlog.contextvars.clear_contextvars()
    except Exception:  # noqa: BLE001
        pass

    # -- pre-test: восстанавливаем sys.modules до оригинала из snapshot --
    for key, orig in _MODULES_ORIGINAL.items():
        if orig is _SENTINEL:
            sys.modules.pop(key, None)
        else:
            sys.modules[key] = orig

    # -- pre-test: восстанавливаем env-vars до оригинала из snapshot --
    for key, val in _ENV_ORIGINAL.items():
        if val is _SENTINEL:
            os.environ.pop(key, None)
        else:
            os.environ[key] = str(val)

    yield

    # -- post-test: восстанавливаем structlog снова --
    structlog.configure(**_STRUCTLOG_CONFIG_ORIGINAL)
    try:
        structlog.contextvars.clear_contextvars()
    except Exception:  # noqa: BLE001
        pass

    # -- post-test: восстанавливаем sys.modules снова (на случай если тест испортил) --
    for key, orig in _MODULES_ORIGINAL.items():
        if orig is _SENTINEL:
            sys.modules.pop(key, None)
        else:
            sys.modules[key] = orig

    # -- post-test: восстанавливаем env-vars --
    for key, val in _ENV_ORIGINAL.items():
        if val is _SENTINEL:
            os.environ.pop(key, None)
        else:
            os.environ[key] = str(val)

    # -- post-test: сброс module-level синглтонов --

    # prometheus_metrics counters
    try:
        from src.core import prometheus_metrics as _pm  # noqa: PLC0415

        _pm._ADAPTIVE_RERANK_COUNTER[0] = 0
        _pm._GUEST_LLM_SKIPPED_COUNTER.clear()
    except Exception:  # noqa: BLE001
        pass

    # error_handler _error_counts
    try:
        from src.core import error_handler as _eh  # noqa: PLC0415

        _eh._error_counts.clear()
    except Exception:  # noqa: BLE001
        pass

    # command_handlers _active_timers / _stopwatches
    try:
        from src.handlers import command_handlers as _ch  # noqa: PLC0415

        _ch._active_timers.clear()
        _ch._stopwatches.clear()
    except Exception:  # noqa: BLE001
        pass

    # gemini_cache_nonce
    try:
        from src.core import gemini_cache_nonce as _gcn  # noqa: PLC0415

        _gcn._GEMINI_NONCE_MAP.clear()
    except Exception:  # noqa: BLE001
        pass

    # chat_filter_config singleton (in-memory rules only, не трогаем файл)
    try:
        from src.core.chat_filter_config import chat_filter_config as _cfc  # noqa: PLC0415

        _cfc._rules.clear()
        _cfc._last_mtime = 0.0
    except Exception:  # noqa: BLE001
        pass

    # _TelegramSendQueue singleton — содержит asyncio.Queue привязанные к конкретному
    # event loop. После смены loop (следующий тест) они вызывают RuntimeError:
    # "Queue is bound to a different event loop".
    try:
        from src.userbot_bridge import _telegram_send_queue  # noqa: PLC0415

        _telegram_send_queue._queues.clear()
        _telegram_send_queue._workers.clear()
        _telegram_send_queue._slowmode_last_sent.clear()
    except Exception:  # noqa: BLE001
        pass
