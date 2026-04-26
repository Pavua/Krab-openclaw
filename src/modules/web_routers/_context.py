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
from typing import Any, Awaitable, Callable, Optional


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
    default_port: int = 8080
    # Phase 2 Wave F: shared mutable holder для boot_ts (lazy init).
    # WebApp передаёт sebnyo же list-ref что и в `_boot_ts`, чтобы router
    # видел те же значения.
    boot_ts_holder: list[float] = field(default_factory=list)
    # Phase 2 Wave I: optional async provider для runtime_lite snapshot.
    # Если None — router передаёт ``runtime_lite=None`` в helper (matrix
    # будет построена без runtime-аугментации). WebApp factory передаёт
    # ``self._collect_runtime_lite_snapshot`` для сохранения исходного
    # контракта endpoint'ов.
    runtime_lite_provider: Optional[Callable[[], Awaitable[dict[str, Any]]]] = None

    def get_dep(self, name: str, default: Any = None) -> Any:
        """Удобный alias для self.deps.get(name, default)."""
        return self.deps.get(name, default)

    def assert_write_access(self, header_key: str, token: str) -> None:
        """Делегирует ``_helpers.assert_write_access`` — env-based проверка
        ``WEB_API_KEY``. Method добавлен в Phase 2 (Session 25); legacy
        ``assert_write_access_fn`` сохранён для backwards-compat.
        """
        from ._helpers import assert_write_access as _impl

        _impl(header_key, token)

    def public_base_url(self) -> str:
        """Возвращает внешний base URL панели через ``_helpers``."""
        from ._helpers import get_public_base_url

        return get_public_base_url(default_port=self.default_port)

    def policy_matrix_snapshot(
        self,
        *,
        runtime_lite: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Делегирует ``_helpers.collect_policy_matrix_snapshot``.

        Phase 2 Wave H (Session 25): unblock extraction для policy/capabilities
        endpoints без зависимости от WebApp instance.
        """
        from ._helpers import collect_policy_matrix_snapshot

        return collect_policy_matrix_snapshot(runtime_lite=runtime_lite)

    def get_boot_ts(self) -> float:
        """Возвращает boot timestamp Krab runtime (lazy init).

        Используется для /api/uptime и dashboard summary. Holder shared
        с WebApp instance (через factory ``_make_router_context``), так что
        первый вызов из любого места устанавливает значение for-all.
        """
        import time as _t

        if not self.boot_ts_holder:
            self.boot_ts_holder.append(_t.time())
        return self.boot_ts_holder[0]

    async def collect_runtime_lite(
        self,
        *,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """Возвращает runtime_lite snapshot через provider или {}.

        Phase 2 Wave I (Session 25): unblock policy/matrix extraction —
        router получает runtime_lite через ctx без прямого доступа к WebApp.

        Phase 2 Wave P (Session 25): добавлен ``force_refresh`` kwarg
        (для endpoints, которым нужен свежий snapshot минуя TTL-cache),
        делегируется через ``_helpers.collect_runtime_lite_via_provider``
        для совместимости с provider'ами разных сигнатур.
        """
        from ._helpers import collect_runtime_lite_via_provider

        return await collect_runtime_lite_via_provider(
            self.runtime_lite_provider,
            force_refresh=force_refresh,
        )
