# -*- coding: utf-8 -*-
"""
operator_identity.py — общий identity/trace foundation для operator-aware контуров.

Что это:
- единая точка правды для `operator_id`, `account_id` и `trace_id`;
- базовый helper-слой для inbox, approvals, mentions и transport-correlations;
- per-account identity, не завязанная на repo-level mutable state.

Зачем нужно:
- master plan требует стабильный identity envelope между учётками и каналами;
- нельзя плодить хэширование account/operator в нескольких модулях с риском drift;
- trace-id должен строиться единообразно, чтобы дальше его можно было
  протащить в web/runtime/userbot и в будущие approval flows.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any


def current_operator_id() -> str:
    """Возвращает имя текущего оператора для identity-конверта."""
    home_dir = Path.home()
    return str(os.getenv("USER", "") or "").strip() or home_dir.name


def current_account_id() -> str:
    """Возвращает стабильный account-id для текущей macOS-учётки."""
    home_dir = Path.home()
    raw = f"{current_operator_id()}|{home_dir}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def build_trace_id(source: str, *parts: object) -> str:
    """
    Строит компактный trace-id для корреляции событий.

    Почему детерминированный хэш, а не UUID:
    - trace нужен не только как уникальный маркер, но и как повторяемый correlation key;
    - одинаковые входы должны давать одинаковый trace-id там, где это уместно
      для de-duplication и пост-мортем анализа.
    """
    normalized_source = str(source or "runtime").strip().lower() or "runtime"
    normalized_parts = [str(part or "").strip() for part in parts if str(part or "").strip()]
    raw = (
        "::".join([normalized_source, *normalized_parts]) if normalized_parts else normalized_source
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"{normalized_source}:{digest}"


def build_identity_envelope(
    *,
    operator_id: str = "",
    account_id: str = "",
    channel_id: str = "",
    team_id: str = "",
    trace_id: str = "",
    approval_scope: str = "owner",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Собирает типизированный identity-envelope для runtime/web/channel слоёв.

    Зачем это отдельно от InboxIdentity:
    - не все контуры хотят тянуть dataclass inbox-сервиса;
    - web snapshots, capability registry и channel matrix должны использовать ту же
      identity-семантику без ручного дублирования полей;
    - это безопасный bridge между machine-readable JSON и persisted inbox-layer.
    """
    payload = {
        "operator_id": str(operator_id or current_operator_id()).strip(),
        "account_id": str(account_id or current_account_id()).strip(),
        "channel_id": str(channel_id or "").strip(),
        "team_id": str(team_id or "").strip(),
        "trace_id": str(trace_id or "").strip(),
        "approval_scope": str(approval_scope or "owner").strip() or "owner",
    }
    extra = dict(metadata or {})
    if extra:
        payload["metadata"] = extra
    return payload


__all__ = [
    "build_identity_envelope",
    "build_trace_id",
    "current_account_id",
    "current_operator_id",
]
