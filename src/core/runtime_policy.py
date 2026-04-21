# -*- coding: utf-8 -*-
"""
runtime_policy.py — единая truth-модель runtime-режимов и provider policy.

Что это:
- общий helper для новых полей master-plan: `runtime_mode`, `primary_policy`,
  `fallback_policy`, `release_safe`, `login_state`, `cost_tier`,
  `stability_score`;
- одна точка, где зафиксированы различия между personal/runtime/lab режимами
  и между стабильными/экспериментальными provider-контурaми.

Зачем нужно:
- web panel, handoff и runtime snapshots должны говорить об одном и том же,
  а не придумывать policy-поля каждый в своём формате;
- multi-account и aggressive-subscription use-case требуют честно различать
  `personal-primary` и `release-safe`, иначе UI будет ложно обнадёживать;
- этот слой должен быть read-only и безопасным, без скрытых side-effect.
"""

from __future__ import annotations

import os
from typing import Any

_RUNTIME_MODE_ALIASES = {
    "personal": "personal-runtime",
    "personal-runtime": "personal-runtime",
    "release": "release-safe-runtime",
    "release-safe": "release-safe-runtime",
    "release-safe-runtime": "release-safe-runtime",
    "lab": "lab-runtime",
    "lab-runtime": "lab-runtime",
}

_PROVIDER_POLICY_DEFAULTS: dict[str, dict[str, Any]] = {
    "codex-cli": {
        "primary_policy": "personal-primary",
        "fallback_policy": "fallback-allowed",
        "release_safe": False,
        "cost_tier": "subscription",
        "base_stability_score": 0.76,
    },
    "google-gemini-cli": {
        "primary_policy": "personal-primary",
        "fallback_policy": "fallback-allowed",
        "release_safe": False,
        "cost_tier": "subscription",
        "base_stability_score": 0.72,
    },
    "openai-codex": {
        "primary_policy": "personal-primary",
        "fallback_policy": "fallback-only",
        "release_safe": False,
        "cost_tier": "subscription",
        "base_stability_score": 0.42,
    },
    "qwen-portal": {
        "primary_policy": "lab-only",
        "fallback_policy": "fallback-only",
        "release_safe": False,
        "cost_tier": "subscription",
        "base_stability_score": 0.28,
    },
    "google-antigravity": {
        "primary_policy": "blocked",
        "fallback_policy": "lab-only",
        "release_safe": False,
        "cost_tier": "subscription",
        "base_stability_score": 0.12,
    },
    "google": {
        "primary_policy": "release-safe",
        "fallback_policy": "fallback-allowed",
        "release_safe": True,
        "cost_tier": "api",
        "base_stability_score": 0.82,
    },
    "openai": {
        "primary_policy": "release-safe",
        "fallback_policy": "fallback-allowed",
        "release_safe": True,
        "cost_tier": "api",
        "base_stability_score": 0.8,
    },
    "lmstudio": {
        "primary_policy": "release-safe",
        "fallback_policy": "fallback-allowed",
        "release_safe": True,
        "cost_tier": "local",
        "base_stability_score": 0.7,
    },
}


def current_runtime_mode() -> str:
    """
    Возвращает канонический runtime-режим текущей учётки.

    Почему default именно `personal-runtime`:
    - проект сейчас активно использует подписки и CLI/OAuth-контуры для
      личного daily-use;
    - `release-safe` должен включаться явно, а не по молчаливому default.
    """
    raw = (
        str(
            os.getenv("KRAB_RUNTIME_MODE", "")
            or os.getenv("OPENCLAW_RUNTIME_MODE", "")
            or "personal-runtime"
        )
        .strip()
        .lower()
    )
    return _RUNTIME_MODE_ALIASES.get(raw, "personal-runtime")


def runtime_mode_release_safe(runtime_mode: str) -> bool:
    """Показывает, должен ли текущий режим избегать хрупких подписочных primary."""
    return str(runtime_mode or "").strip().lower() == "release-safe-runtime"


def provider_runtime_policy(
    provider_name: str,
    *,
    readiness: str = "",
    auth_mode: str = "",
    oauth_status: str = "",
    helper_available: bool = False,
    legacy: bool = False,
    cli_login_ready: bool = False,
    quota_state: str = "",
) -> dict[str, Any]:
    """
    Возвращает machine-readable policy для provider-карточек и runtime snapshot.

    Здесь intentionally нет строгой «математики продакшн-SLO».
    Нужна практичная truth-модель для owner panel и handoff:
    - можно ли считать provider release-safe;
    - годится ли он как personal primary;
    - нужен ли login/recovery прямо сейчас;
    - насколько route выглядит стабильным относительно остальных.
    """
    normalized_provider = str(provider_name or "").strip().lower()
    defaults = dict(_PROVIDER_POLICY_DEFAULTS.get(normalized_provider, {}))
    primary_policy = str(defaults.get("primary_policy") or "fallback-only")
    fallback_policy = str(defaults.get("fallback_policy") or "fallback-only")
    release_safe = bool(defaults.get("release_safe", False))
    cost_tier = str(defaults.get("cost_tier") or "unknown")
    stability_score = float(defaults.get("base_stability_score", 0.55))

    normalized_readiness = str(readiness or "").strip().lower()
    normalized_auth_mode = str(auth_mode or "").strip().lower()
    normalized_oauth_status = str(oauth_status or "").strip().lower()
    normalized_quota_state = str(quota_state or "").strip().lower()

    if legacy:
        primary_policy = "lab-only"
        fallback_policy = "lab-only"
        release_safe = False
        stability_score = min(stability_score, 0.2)

    if normalized_readiness == "ready":
        login_state = "ready"
    elif normalized_readiness == "attention":
        login_state = "attention"
        stability_score -= 0.18
    elif normalized_readiness == "blocked":
        login_state = (
            "login_required"
            if helper_available or normalized_auth_mode in {"oauth", "cli"}
            else "unavailable"
        )
        stability_score -= 0.34
    else:
        login_state = "unknown"
        stability_score -= 0.1

    if normalized_oauth_status in {"expired", "missing"} and normalized_auth_mode == "oauth":
        login_state = "login_required"
        stability_score -= 0.14
    if normalized_auth_mode == "cli" and not cli_login_ready:
        login_state = "login_required" if helper_available else "attention"
        stability_score -= 0.1
    if normalized_quota_state in {"limited", "rate_limited", "cooldown"}:
        stability_score -= 0.1
    elif normalized_quota_state in {"exhausted", "blocked"}:
        stability_score -= 0.2

    stability_score = round(max(0.05, min(0.99, stability_score)), 2)

    return {
        "runtime_mode": current_runtime_mode(),
        "primary_policy": primary_policy,
        "fallback_policy": fallback_policy,
        "release_safe": release_safe,
        "login_state": login_state,
        "cost_tier": cost_tier,
        "stability_score": stability_score,
    }


def allow_experimental_for_chat(chat_id: int | None) -> bool:
    """
    Разрешить ли экспериментальные команды в данном чате.

    Возвращает True если:
    - установлена переменная окружения KRAB_EXPERIMENTAL=1, ИЛИ
    - chat_id совпадает с owner-чатом (из OWNER_CHAT_ID env),
      ИЛИ chat_id равен None/0 (системный/owner-контекст без явного чата).
    """
    if os.getenv("KRAB_EXPERIMENTAL") == "1":
        return True

    # None или 0 — считаем owner-контекстом (безопасный default)
    if not chat_id:
        return True

    # Проверяем OWNER_CHAT_ID env (один чат) и OWNER_CHAT_IDS (список через запятую)
    owner_chat_id_raw = os.getenv("OWNER_CHAT_ID", "").strip()
    if owner_chat_id_raw:
        try:
            if int(owner_chat_id_raw) == chat_id:
                return True
        except ValueError:
            pass

    owner_chat_ids_raw = os.getenv("OWNER_CHAT_IDS", "").strip()
    if owner_chat_ids_raw:
        for part in owner_chat_ids_raw.split(","):
            part = part.strip()
            if part:
                try:
                    if int(part) == chat_id:
                        return True
                except ValueError:
                    pass

    return False


__all__ = [
    "allow_experimental_for_chat",
    "current_runtime_mode",
    "provider_runtime_policy",
    "runtime_mode_release_safe",
]
