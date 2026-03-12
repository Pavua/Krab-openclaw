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
    raw = "::".join([normalized_source, *normalized_parts]) if normalized_parts else normalized_source
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"{normalized_source}:{digest}"


__all__ = ["build_trace_id", "current_account_id", "current_operator_id"]
