# -*- coding: utf-8 -*-
"""
Promoted helpers для router модулей — Phase 2 foundation (Session 25).

Module-level functions, не зависящие от WebApp instance. Используются
RouterContext через delegating-методы и могут быть импортированы напрямую
из routers.

Обе функции совместимы с existing call sites в ``WebApp`` и читают
конфигурацию из env (``WEB_API_KEY``, ``WEB_PUBLIC_BASE_URL``, ``WEB_HOST``).

См. ``docs/CODE_SPLITS_PLAN.md`` § "Phase 2 advanced" → RouterContext infra.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

from fastapi import HTTPException


def get_web_api_key() -> str:
    """Возвращает текущее значение ``WEB_API_KEY`` (может быть пустым)."""
    return os.getenv("WEB_API_KEY", "").strip()


def get_public_base_url(default_port: int = 8080) -> str:
    """Возвращает внешний base URL панели.

    Приоритет:
    1. ``WEB_PUBLIC_BASE_URL`` — explicit override (без trailing slash).
    2. ``http://{WEB_HOST или 127.0.0.1}:{default_port}``.
    """
    explicit = os.getenv("WEB_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if explicit:
        return explicit
    display_host = os.getenv("WEB_HOST", "127.0.0.1").strip() or "127.0.0.1"
    return f"http://{display_host}:{default_port}"


def assert_write_access(header_key: str, token: str) -> None:
    """Проверяет доступ к write-эндпоинтам web API.

    Если ``WEB_API_KEY`` не установлен — открытый доступ (no-op).
    Иначе сверяет либо header (``X-Krab-Web-Key``), либо query param ``token``
    с expected value. Несовпадение → ``HTTPException(403)``.
    """
    expected = get_web_api_key()
    if not expected:
        return

    provided = (header_key or "").strip() or (token or "").strip()
    if provided != expected:
        raise HTTPException(status_code=403, detail="forbidden: invalid WEB_API_KEY")


async def collect_runtime_lite_via_provider(
    provider: Any,
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Вызывает runtime_lite snapshot через переданный provider.

    Promoted shim для Phase 2 Wave P (Session 25). Полная decoupled-promote
    исходного ``WebApp._collect_runtime_lite_snapshot`` отложена — у него
    глубокий граф зависимостей (``_build_runtime_lite_snapshot_uncached`` →
    ``_overlay_tier_state_on_last_runtime_route`` →
    ``_normalize_telegram_session_truth`` → ``_telegram_session_snapshot`` →
    ``_lmstudio_model_snapshot`` → ``_derive_openclaw_auth_state`` →
    ``_runtime_operator_profile`` + кэш на ``self``). Их вместе ~600 LOC
    с mutating cache state — promote только всем стеком, без частичного
    extract'a.

    Этот helper — функциональный fallback: router'ы могут получить snapshot
    через ``ctx.collect_runtime_lite()`` (уже unblock'ило большинство
    extractions), либо напрямую через эту функцию + provider (например,
    в тестах через ``AsyncMock``).

    Args:
        provider: callable (sync or async) возвращающий snapshot dict;
            обычно ``WebApp._collect_runtime_lite_snapshot``.
        force_refresh: если provider поддерживает ``force_refresh`` kwarg —
            будет передан, иначе игнорируется.

    Returns:
        dict со snapshot или ``{}`` если provider == None.
    """
    if provider is None:
        return {}
    try:
        result = provider(force_refresh=force_refresh)
    except TypeError:
        # provider не принимает force_refresh — fallback к bare call.
        result = provider()
    if hasattr(result, "__await__"):
        result = await result
    return dict(result or {})


def collect_policy_matrix_snapshot(
    *,
    runtime_lite: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Собирает policy matrix поверх ACL и live runtime-lite truth.

    Promoted из ``WebApp._policy_matrix_snapshot`` (Session 25 Wave H).
    Зависимости разрешаются через module-level helpers / core imports —
    self-state не требуется.
    """
    # Локальные импорты чтобы избежать циклов при загрузке web_routers package.
    from src.core.access_control import load_acl_runtime_state
    from src.core.capability_registry import build_policy_matrix
    from src.core.operator_identity import current_account_id, current_operator_id

    return build_policy_matrix(
        operator_id=current_operator_id(),
        account_id=current_account_id(),
        acl_state=load_acl_runtime_state(),
        web_write_requires_key=bool(get_web_api_key()),
        runtime_lite=runtime_lite or {},
    )


# ---------------------------------------------------------------------------
# Pure utility helpers (Session 26 Phase 2 audit follow-up).
# Promoted из ``WebApp`` static-методов в module-level — позволяют router'ам
# импортировать напрямую без обращения к WebApp instance. Wrapper-методы
# в ``WebApp`` сохранены для совместимости (тесты могут monkeypatch).
# ---------------------------------------------------------------------------


def tail_text(text: str, max_chars: int = 2000) -> str:
    """Возвращает хвост текста с ограничением длины."""
    payload = str(text or "")
    if len(payload) <= max_chars:
        return payload
    return payload[-max_chars:]


def mask_secret(value: str) -> str:
    """Маскирует секрет для UI/логов: видны только префикс и суффикс."""
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 6:
        return "*" * len(text)
    return f"{text[:3]}...{text[-3:]}"


def bool_env(value: str, default: bool = False) -> bool:
    """Безопасно нормализует булево значение из env/строки."""
    raw = str(value or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def project_root() -> Path:
    """Возвращает корень проекта Krab (родитель ``src/``)."""
    return Path(__file__).resolve().parents[3]


def clone_jsonish_dict(payload: dict[str, Any]) -> dict[str, Any]:
    """Возвращает безопасную неглубокую копию dict/list payload для runtime-cache."""
    cloned: dict[str, Any] = {}
    for key, value in dict(payload or {}).items():
        if isinstance(value, list):
            cloned[key] = list(value)
        elif isinstance(value, dict):
            cloned[key] = dict(value)
        else:
            cloned[key] = value
    return cloned


def clone_jsonish_payload(payload: Any) -> Any:
    """Возвращает глубокую копию JSON-подобного payload для cache/fallback ответов."""
    return copy.deepcopy(payload)


def float_env(name: str, default: float, *, min_value: float, max_value: float) -> float:
    """Читает float из env с безопасным clamp."""
    raw = str(os.getenv(name, str(default)) or str(default)).strip()
    try:
        value = float(raw)
    except Exception:
        value = float(default)
    return max(float(min_value), min(float(value), float(max_value)))
