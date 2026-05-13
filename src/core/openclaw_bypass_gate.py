# -*- coding: utf-8 -*-
"""Wave 245: OpenClaw bypass gate — emergency direct routing когда gateway broken.

Назначение
----------
Когда OpenClaw gateway сломан (например, malformed `mcp_servers.context7`,
`mlx-local-kv4` не bound к main agent, или Cherry Studio возвращает
"internal error"), пользователь может включить аварийный bypass, при котором
**все** Krab LLM-запросы идут напрямую в выбранный backend, минуя
OpenClaw gateway полностью.

Поведение
---------
- ENV: ``KRAB_OPENCLAW_BYPASS_ENABLED`` (default ``0``).
- Когда ``1``/``true``/``yes``/``on`` — bypass активен.
- При активации log warning один раз для operator awareness.
- Затрагивает:
  - ``OpenClawClient.send_message_stream`` — короткий путь через MLX local
    или LM Studio direct (`_direct_lm_fallback`), без всего OpenClaw chain;
  - ``OpenClawAdapter.stream`` (Hermes AgentEngine) — то же самое;
  - swarm DM bots и cron pipeline — наследуют автоматически (используют
    ту же ``send_message_stream``).

Риски
-----
- Нет MCP tools (всё, что предоставляет OpenClaw layer — недоступно);
- Нет fallback chain (если local backend упал — нет cloud fallback);
- Нет audit log внутри OpenClaw runtime.

См. ``docs/OPENCLAW_BYPASS_GUIDE.md``.
"""

from __future__ import annotations

import os
import threading

from .logger import get_logger

logger = get_logger(__name__)

# ENV name — единый source of truth для всего bypass-кода.
_ENV_NAME = "KRAB_OPENCLAW_BYPASS_ENABLED"

# Чтобы не спамить warning при каждом вызове — флаг "уже залогировано".
_warned_lock = threading.Lock()
_warned_state: dict[str, bool] = {"enabled": False, "disabled": False}


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def is_openclaw_bypass_enabled() -> bool:
    """Возвращает True, если bypass включён через env.

    Читается каждый раз (без кэша), чтобы оператор мог переключить
    флаг через ``export`` без рестарта Krab. При first transition в
    ``enabled``/``disabled`` логируется warning/info (idempotent).
    """
    enabled = _truthy(os.environ.get(_ENV_NAME))
    _log_state_transition(enabled)
    return enabled


def _log_state_transition(enabled: bool) -> None:
    """Лог при первом обнаружении enabled/disabled (idempotent)."""
    with _warned_lock:
        if enabled and not _warned_state["enabled"]:
            _warned_state["enabled"] = True
            _warned_state["disabled"] = False
            logger.warning(
                "openclaw_bypass_enabled",
                env=_ENV_NAME,
                note=(
                    "Все LLM-запросы идут напрямую в local backend (MLX/LM Studio), "
                    "минуя OpenClaw. MCP tools и cloud fallback chain недоступны."
                ),
            )
        elif (not enabled) and _warned_state["enabled"] and not _warned_state["disabled"]:
            # Был включён → выключился. Лог info, чтобы оператор увидел recovery.
            _warned_state["disabled"] = True
            _warned_state["enabled"] = False
            logger.info("openclaw_bypass_disabled", env=_ENV_NAME)


def _reset_warning_state_for_tests() -> None:
    """Только для тестов: сбрасывает idempotent-флаг логирования."""
    with _warned_lock:
        _warned_state["enabled"] = False
        _warned_state["disabled"] = False


__all__ = [
    "is_openclaw_bypass_enabled",
    "_reset_warning_state_for_tests",
]
