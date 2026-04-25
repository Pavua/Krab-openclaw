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

import asyncio
import os
import sys
from collections.abc import Iterator
from typing import Any

import pytest
import structlog

# ---------------------------------------------------------------------------
# Session 21 baseline cleanup: исключаем тесты, привязанные к удалённым
# скриптам (FileNotFoundError при collection).
# ---------------------------------------------------------------------------
collect_ignore_glob = [
    "tools/test_sync_krab_agent_skills.py",
]


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
# W32: полный snapshot os.environ для systemic env-isolation.
# Фиксируется один раз при загрузке conftest (до любого теста). Любые
# тесты, которые делают os.environ[...] = ... напрямую (без monkeypatch),
# не смогут протекать в следующие тесты — добавленные ключи удаляются,
# изменённые значения восстанавливаются.
# ---------------------------------------------------------------------------
_FULL_ENV_SNAPSHOT: dict[str, str] = dict(os.environ)

# Ключи, которые намеренно выставляются в tests/integration/conftest.py и
# НЕ должны трогаться глобальным snapshot (они уже в snapshot, но оставляем
# явно для документации).
# _FULL_ENV_SNAPSHOT уже содержит их, если они были выставлены до загрузки
# этого файла. Если integration/conftest.py выставил их ПОЗЖЕ — они не
# попадут в snapshot и будут удалены. Чтобы это не ломало интеграцию,
# перечисляем защищённые ключи ниже.
_ENV_ALWAYS_PRESERVE: set[str] = {
    "TELEGRAM_API_ID",
    "TELEGRAM_API_HASH",
    "PATH",
    "HOME",
    "USER",
    "LANG",
    "LC_ALL",
    "TMPDIR",
    "VIRTUAL_ENV",
    "PYTHONPATH",
}

# Префиксы env-ключей, которыми управляет pytest/рантайм — не трогать.
_ENV_PRESERVE_PREFIXES: tuple[str, ...] = (
    "PYTEST_",
    "_PYTEST_",
    "PY_COLORS",
    "COV_",
)

# ---------------------------------------------------------------------------
# Сохраняем глобальную конфигурацию structlog при загрузке conftest.
# test_correlation_id.py вызывает structlog.configure() с кастомными processors,
# что меняет глобальный pipeline — все последующие тесты получают PrintLogger
# который не принимает keyword args (TypeError: got unexpected keyword argument).
# ---------------------------------------------------------------------------
_STRUCTLOG_CONFIG_ORIGINAL: dict = structlog.get_config().copy()


# ---------------------------------------------------------------------------
# Wave 12: snapshot ВСЕХ class-level Config атрибутов.
# Тесты используют Config.update_setting(...) или прямое присваивание
# Config.X = value, обходящее monkeypatch. Без полного snapshot мутации
# class-attrs протекают между файлами и ломают get_set_value / is_valid /
# notify_status / lmstudio etc.
# ---------------------------------------------------------------------------
def _snapshot_config_class_attrs() -> dict[str, Any]:
    try:
        # Берём канонический класс через singleton userbot_bridge.config —
        # он переживает importlib.reload(src.config).
        import src.userbot_bridge as _ub  # noqa: PLC0415

        _C = type(_ub.config)
        return {
            k: getattr(_C, k)
            for k in vars(_C)
            if not k.startswith("_") and k.isupper()
        }
    except Exception:  # noqa: BLE001
        return {}


_CONFIG_CLASS_SNAPSHOT: dict[str, Any] = _snapshot_config_class_attrs()


def _canonical_config_class():
    try:
        import src.userbot_bridge as _ub  # noqa: PLC0415

        return type(_ub.config)
    except Exception:  # noqa: BLE001
        return None


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

    # -- pre-test: восстанавливаем Config class attrs из snapshot --
    # Используем КАНОНИЧЕСКИЙ класс через userbot_bridge.config (переживает reload).
    if _CONFIG_CLASS_SNAPSHOT:
        _C = _canonical_config_class()
        if _C is not None:
            for _k, _v in _CONFIG_CLASS_SNAPSHOT.items():
                try:
                    setattr(_C, _k, _v)
                except (AttributeError, TypeError):
                    pass

    # -- pre-test: чистим instance-атрибуты config singleton --
    # (monkeypatch предыдущего теста мог оставить instance-shadow на
    # Config singleton — см. подробное объяснение в post-test секции).
    # Чистим ВСЕ известные ссылки на singleton, потому что после
    # importlib.reload(src.config) разные модули держат разные экземпляры.
    _cfg_singletons_seen: set[int] = set()
    try:
        import src.config as _src_cfg_mod  # noqa: PLC0415

        candidates = []
        if hasattr(_src_cfg_mod, "config"):
            candidates.append(_src_cfg_mod.config)
        try:
            import src.userbot_bridge as _ub_mod  # noqa: PLC0415

            if hasattr(_ub_mod, "config"):
                candidates.append(_ub_mod.config)
        except Exception:  # noqa: BLE001
            pass
        try:
            from src.handlers import command_handlers as _ch_mod  # noqa: PLC0415

            if hasattr(_ch_mod, "config"):
                candidates.append(_ch_mod.config)
        except Exception:  # noqa: BLE001
            pass
        for _cfg in candidates:
            if id(_cfg) in _cfg_singletons_seen:
                continue
            _cfg_singletons_seen.add(id(_cfg))
            for _k in [k for k in list(vars(_cfg).keys()) if not k.startswith("__")]:
                try:
                    delattr(_cfg, _k)
                except (AttributeError, TypeError):
                    pass
    except Exception:  # noqa: BLE001
        pass

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

    # -- post-test: восстанавливаем Config class attrs из snapshot --
    if _CONFIG_CLASS_SNAPSHOT:
        try:
            from src.config import Config as _C  # noqa: PLC0415

            for _k, _v in _CONFIG_CLASS_SNAPSHOT.items():
                try:
                    if getattr(_C, _k, _SENTINEL) is not _v:
                        setattr(_C, _k, _v)
                except (AttributeError, TypeError):
                    pass
        except Exception:  # noqa: BLE001
            pass

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

    # Wave 12: главная утечка между файлами — `config` singleton (Config()).
    # Тесты используют `monkeypatch.setattr(config, "X", ...)` или
    # `patch("...command_handlers.config.X", ...)`, которые СОЗДАЮТ instance-attr
    # на singleton. После teardown monkeypatch/patch значение восстанавливается,
    # но как INSTANCE-атрибут, который теперь маскирует CLASS-атрибут Config.X.
    # Следующие тесты, которые делают `Config.X = ...` (class-level), не видят
    # эффекта — instance shadow перекрывает.
    # Решение: после каждого теста удаляем все instance-атрибуты с config.
    try:
        from src.config import config as _cfg_singleton  # noqa: PLC0415

        # Сохраняем только служебные dunder/private атрибуты, чистим прочее.
        _instance_attrs = [
            k for k in list(vars(_cfg_singleton).keys()) if not k.startswith("__")
        ]
        for _k in _instance_attrs:
            try:
                delattr(_cfg_singleton, _k)
            except (AttributeError, TypeError):
                pass
    except Exception:  # noqa: BLE001
        pass

    # -- post-test: W32 systemic env cleanup --
    # Удаляем ключи, добавленные тестом сверх snapshot (не трогая preserved).
    _current_keys = set(os.environ.keys())
    _snapshot_keys = set(_FULL_ENV_SNAPSHOT.keys())
    for key in _current_keys - _snapshot_keys:
        if key in _ENV_ALWAYS_PRESERVE:
            continue
        if key.startswith(_ENV_PRESERVE_PREFIXES):
            continue
        try:
            del os.environ[key]
        except KeyError:
            pass
    # Восстанавливаем изменённые значения до snapshot.
    for key, val in _FULL_ENV_SNAPSHOT.items():
        if key.startswith(_ENV_PRESERVE_PREFIXES):
            continue
        if os.environ.get(key) != val:
            os.environ[key] = val


@pytest.fixture(autouse=True)
def _cancel_leftover_asyncio_tasks() -> Iterator[None]:
    """
    W32: отменяет фоновые asyncio.Task, оставленные предыдущим тестом.

    После теста, использовавшего event loop с `asyncio.create_task()`
    без await, незавершённые таски могут всплыть в следующем тесте
    как "Task was destroyed but it is pending!" и дестабилизировать
    fixtures, которые создают свой loop.
    """
    yield
    try:
        loop = asyncio.get_event_loop_policy().get_event_loop()
    except Exception:  # noqa: BLE001
        return
    if loop.is_closed() or loop.is_running():
        return
    try:
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
    except RuntimeError:
        return
    for task in pending:
        task.cancel()
    if pending:
        try:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        except Exception:  # noqa: BLE001
            pass
