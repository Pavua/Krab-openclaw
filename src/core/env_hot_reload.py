# -*- coding: utf-8 -*-
"""
Wave 106: hot-reload подмножества env-флагов без полного restart Krab.

Зачем: за Session 47 появилось много observability-флагов
(KRAB_SWARM_PROBE_ENABLED, KRAB_DISPATCHER_RECOVERY_ENABLED и т.д.). Toggle
любого требовал restart. Этот модуль перечитывает .env и обновляет
os.environ только для whitelisted observability-флагов (без credentials и
без путей), что безопасно делать на лету.

Точки входа:
- :func:`reload_safe_env` — programmatic API, возвращает diff;
- POST /api/admin/env/reload — endpoint в system_router;
- SIGUSR1 в userbot — invokes reload_safe_env через signal handler.

Whitelist строго ограничен observability-полем: feature flags + бюджеты.
Все credentials, path, host, port — НЕ перезагружаются (security).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# ── Whitelist безопасных к hot-reload env vars ─────────────────────────────
# Принцип: только observability-флаги и числовые budgets. Никаких
# credentials/path/host/port (изменение этих требует осознанного restart с
# полной валидацией).
SAFE_RELOAD_ENV_VARS: frozenset[str] = frozenset(
    {
        "KRAB_SWARM_PROBE_ENABLED",
        "KRAB_DISPATCHER_RECOVERY_ENABLED",
        "KRAB_LAUNCHD_HEALTH_MONITOR_ENABLED",
        "KRAB_EAR_PROBE_ENABLED",
        "KRAB_PROVIDER_QUARANTINE_ENABLED",
        "KRAB_TRANSLATION_CACHE_ENABLED",
        "KRAB_PRESSURE_AWARE_SELECTION",
        "KRAB_RATE_LIMIT_ENABLED",
        "KRAB_COST_BUDGET_MONITOR_ENABLED",
        "KRAB_DAILY_BUDGET_EUR",
        "KRAB_WEEKLY_BUDGET_EUR",
    }
)


# ── Prometheus counter (silent если prometheus_client недоступен) ──────────
try:
    from prometheus_client import Counter as _Counter  # type: ignore[import-not-found]

    _env_hot_reload_total = _Counter(
        "krab_env_hot_reload_total",
        "Wave 106: env hot-reload invocations by outcome",
        ["success"],
    )
except Exception:  # noqa: BLE001 - optional dependency
    _env_hot_reload_total = None  # type: ignore[assignment]


def _record_metric(success: bool) -> None:
    """Best-effort прометей; молча no-op если client не установлен."""
    if _env_hot_reload_total is None:
        return
    try:
        _env_hot_reload_total.labels(success="true" if success else "false").inc()
    except Exception:  # noqa: BLE001
        pass


def _parse_dotenv(path: Path) -> dict[str, str]:
    """
    Минималистичный парсер .env (KEY=VALUE, # комментарии, опциональные кавычки).

    Зачем своя реализация: dotenv.dotenv_values возвращает Optional[str] и
    не игнорирует malformed строки. Нам достаточно строгого matching, без
    подстановки переменных. Все ошибки строк логируются debug и пропускаются.
    """
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning(
            "env_hot_reload_dotenv_read_failed",
            path=str(path),
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return values
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        # Снять окружающие кавычки, если есть симметрично.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return values


def _resolve_dotenv_path(override: str | os.PathLike[str] | None = None) -> Path:
    """
    Определяем путь к .env: override → KRAB_DOTENV_PATH → CWD/.env.

    CWD используется как fallback, потому что launchd-юниты Krab стартуют
    из корня проекта. Если файл отсутствует — это не ошибка для hot-reload
    (просто diff будет пустой, success=True).
    """
    if override is not None:
        return Path(override).expanduser()
    env_override = os.environ.get("KRAB_DOTENV_PATH")
    if env_override:
        return Path(env_override).expanduser()
    return Path.cwd() / ".env"


def reload_safe_env(
    dotenv_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """
    Перечитать .env и обновить os.environ только для whitelisted vars.

    Returns:
        dict с полями:
        - ``ok``: bool — успешно ли произошёл reload (False только при IO-ошибке).
        - ``diff``: dict[str, tuple[old, new]] для изменённых флагов.
        - ``unchanged``: list[str] — whitelisted флаги, оставшиеся как были.
        - ``skipped``: list[str] — присутствующие в .env, но не в whitelist.
        - ``dotenv_path``: путь, из которого читали.

    Контракт: при отсутствии .env файла возвращает ok=True с пустым diff
    (это не ошибка для hot-reload). При IO-ошибке (permission) — ok=False.
    """
    path = _resolve_dotenv_path(dotenv_path)
    diff: dict[str, tuple[str | None, str | None]] = {}
    unchanged: list[str] = []
    skipped: list[str] = []

    if not path.exists():
        logger.info("env_hot_reload_no_dotenv", path=str(path))
        _record_metric(success=True)
        return {
            "ok": True,
            "diff": diff,
            "unchanged": [],
            "skipped": [],
            "dotenv_path": str(path),
            "reason": "dotenv_missing",
        }

    try:
        parsed = _parse_dotenv(path)
    except Exception as exc:  # noqa: BLE001 - defensive
        logger.error(
            "env_hot_reload_parse_failed",
            path=str(path),
            error=str(exc),
            error_type=type(exc).__name__,
        )
        _record_metric(success=False)
        return {
            "ok": False,
            "error": "parse_failed",
            "error_type": type(exc).__name__,
            "dotenv_path": str(path),
        }

    for key, new_value in parsed.items():
        if key not in SAFE_RELOAD_ENV_VARS:
            skipped.append(key)
            continue
        old_value = os.environ.get(key)
        if old_value == new_value:
            unchanged.append(key)
            continue
        os.environ[key] = new_value
        diff[key] = (old_value, new_value)

    logger.info(
        "env_hot_reload_applied",
        path=str(path),
        changed=len(diff),
        unchanged=len(unchanged),
        skipped=len(skipped),
    )
    _record_metric(success=True)
    return {
        "ok": True,
        "diff": {k: list(v) for k, v in diff.items()},
        "unchanged": unchanged,
        "skipped": skipped,
        "dotenv_path": str(path),
    }


def install_sigusr1_handler() -> bool:
    """
    Зарегистрировать SIGUSR1 → reload_safe_env.

    Returns:
        True если handler установлен; False если signal недоступен
        (Windows / non-main thread). Идемпотентно — повторный вызов
        перезаписывает handler тем же телом.
    """
    import signal as _signal

    if not hasattr(_signal, "SIGUSR1"):
        logger.warning("env_hot_reload_sigusr1_unavailable", platform="non-posix")
        return False

    def _handler(signum: int, frame: Any) -> None:  # noqa: ARG001
        # Signal handler должен быть максимально лёгким: только запуск reload.
        # Никаких await / asyncio внутри — reload_safe_env синхронен.
        try:
            result = reload_safe_env()
            logger.info(
                "env_hot_reload_signal_handled",
                signal="SIGUSR1",
                ok=result.get("ok"),
                changed=len(result.get("diff", {}) or {}),
            )
        except Exception as exc:  # noqa: BLE001 - signal handler must not raise
            logger.error(
                "env_hot_reload_signal_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )

    try:
        _signal.signal(_signal.SIGUSR1, _handler)
    except (ValueError, OSError) as exc:
        # ValueError — handler устанавливается не из главного потока.
        logger.warning(
            "env_hot_reload_sigusr1_install_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return False
    logger.info("env_hot_reload_sigusr1_installed")
    return True
