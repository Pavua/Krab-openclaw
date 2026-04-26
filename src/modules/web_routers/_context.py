# -*- coding: utf-8 -*-
"""
RouterContext — DI контейнер для extracted APIRouter модулей.

Phase 1 scaffold (Session 24). Каждый router-модуль (Phase 2+) будет
получать инстанс RouterContext вместо ``self`` от WebApp class.

Это decouples routers от WebApp — они становятся независимо тестируемыми
и не зависят от full WebApp class hierarchy.

См. ``docs/CODE_SPLITS_PLAN.md`` § "Architecture decision".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class RouterContext:
    """Контекст для router-модулей. Передаётся в build_<domain>_router(...).

    Attributes:
        deps: dict со всеми инжектируемыми singletons (kraab_userbot,
              memory_manager, voice_gateway_client, и т.п.) — то же что
              ``WebApp.deps`` сегодня.
        project_root: путь до корня проекта (для file lookup, scripts).
        web_api_key_fn: callable возвращающий API key (для auth check
              в защищённых endpoints).
        assert_write_access_fn: callable для проверки write permissions —
              raises HTTPException(403) если нет доступа.
        rate_state: mutable dict для rate-limiting (assistant endpoints).
              Shared между WebApp instance и router'ами по reference.
        idempotency_state: mutable dict для idempotency keys (assistant POST).
              Shared by reference.
    """

    deps: dict[str, Any]
    project_root: Path
    web_api_key_fn: Callable[[], str | None]
    assert_write_access_fn: Callable[..., None]
    rate_state: dict[str, Any] = field(default_factory=dict)
    idempotency_state: dict[str, Any] = field(default_factory=dict)

    def get_dep(self, name: str, default: Any = None) -> Any:
        """Удобный alias для self.deps.get(name, default)."""
        return self.deps.get(name, default)
