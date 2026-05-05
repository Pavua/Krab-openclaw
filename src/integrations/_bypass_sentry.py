"""Wave 30-B: helper для Sentry breadcrumbs в bypass модулях.

Все bypass модули (cli_subprocess_bypass, google_vertex_direct,
anthropic_vertex_direct, google_genai_direct) используют этот helper
чтобы иметь единый post-mortem trail в Sentry events.

ENV gate: KRAB_BYPASS_SENTRY_BREADCRUMBS_ENABLED (default ON).
"""

from __future__ import annotations

import os
from typing import Any


def _is_breadcrumbs_enabled() -> bool:
    """Включены ли bypass-breadcrumbs. Default ON (opt-out через env=0)."""
    return os.environ.get("KRAB_BYPASS_SENTRY_BREADCRUMBS_ENABLED", "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def add_bypass_breadcrumb(
    *,
    bypass_kind: str,
    event: str,
    model: str = "",
    extra: dict[str, Any] | None = None,
    level: str = "info",
) -> None:
    """Добавляет Sentry breadcrumb для bypass-событий.

    Если sentry_sdk недоступен, disabled или ENV gate выключен — no-op.
    Никогда не кидает исключений: breadcrumbs не должны ронять hot-path.

    Args:
        bypass_kind: тип bypass ('cli', 'vertex', 'anthropic-vertex',
                     'google-direct', 'gemma').
        event: событие ('engaged', 'success', 'failure', 'timeout', 'empty_retry').
        model: bare model id для Sentry data (без provider-префикса).
        extra: дополнительные поля в breadcrumb.data.
        level: уровень breadcrumb ('info', 'warning', 'error').
    """
    if not _is_breadcrumbs_enabled():
        return
    try:
        import sentry_sdk  # type: ignore[import-not-found]

        sentry_sdk.add_breadcrumb(
            category=f"bypass.{bypass_kind}",
            message=f"{bypass_kind}_{event}",
            level=level,
            data={"model": model, **(extra or {})},
        )
    except Exception:  # noqa: BLE001 — breadcrumbs не должны ронять hot-path
        pass
