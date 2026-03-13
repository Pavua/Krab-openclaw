# -*- coding: utf-8 -*-
"""
capability_registry.py — единый foundation-слой Capability Registry + Policy Matrix.

Что это:
- общий helper для сборки capability truth по контурам `assistant/userbot/ecosystem/translator/system`;
- единая policy matrix для ролей `owner/full/partial/guest`;
- безопасная точка синхронизации между web-панелью, userbot fast-path и future phase'ами.

Зачем нужно:
- master plan требует единый capability registry вместо нескольких разрозненных truth-срезов;
- policy/ACL нельзя описывать отдельно в web и Telegram с риском drift;
- этот модуль даёт foundation, на который дальше можно опирать inbox, channel parity,
  system control v2 и translator/product layers.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .access_control import AccessLevel, PARTIAL_ACCESS_COMMANDS


_ROLE_CAPABILITIES: dict[str, dict[str, bool]] = {
    AccessLevel.OWNER.value: {
        "chat": True,
        "runtime_truth": True,
        "web_search": True,
        "memory": True,
        "inbox": True,
        "approvals": True,
        "file_ops": True,
        "browser_control": True,
        "macos_control": True,
        "voice_runtime": True,
        "model_routing": True,
        "acl_admin": True,
        "runtime_mutation": True,
    },
    AccessLevel.FULL.value: {
        "chat": True,
        "runtime_truth": True,
        "web_search": True,
        "memory": True,
        "inbox": True,
        "approvals": True,
        "file_ops": True,
        "browser_control": True,
        "macos_control": True,
        "voice_runtime": True,
        "model_routing": True,
        "acl_admin": True,
        "runtime_mutation": True,
    },
    AccessLevel.PARTIAL.value: {
        "chat": True,
        "runtime_truth": True,
        "web_search": True,
        "memory": False,
        "inbox": False,
        "approvals": False,
        "file_ops": False,
        "browser_control": False,
        "macos_control": False,
        "voice_runtime": False,
        "model_routing": False,
        "acl_admin": False,
        "runtime_mutation": False,
    },
    AccessLevel.GUEST.value: {
        "chat": True,
        "runtime_truth": False,
        "web_search": False,
        "memory": False,
        "inbox": False,
        "approvals": False,
        "file_ops": False,
        "browser_control": False,
        "macos_control": False,
        "voice_runtime": False,
        "model_routing": False,
        "acl_admin": False,
        "runtime_mutation": False,
    },
}

_ROLE_NOTES: dict[str, list[str]] = {
    AccessLevel.OWNER.value: [
        "Владелец видит и управляет полным runtime/tool-контуром.",
        "Owner-политика нужна для approval, ACL и рискованных write-операций.",
    ],
    AccessLevel.FULL.value: [
        "Full-контур сейчас следует production truth userbot и раскрывает почти весь tool/runtime слой.",
        "Этот уровень остаётся доверенным и должен назначаться осознанно.",
    ],
    AccessLevel.PARTIAL.value: [
        "Частичный доступ ограничен safe-командами и truthful self-check.",
        "Файлы, браузер, панель, approval и admin-контур здесь закрыты.",
    ],
    AccessLevel.GUEST.value: [
        "Гостевой контур получает только обычный текстовый assistant-режим.",
        "Внутренние owner/full инструменты и policy-write действия скрыты.",
    ],
}


def resolve_access_mode(
    *,
    is_allowed_sender: bool,
    access_level: str | AccessLevel | None,
) -> str:
    """Нормализует access_level в единый runtime access mode."""
    if isinstance(access_level, AccessLevel):
        return access_level.value
    normalized = str(access_level or "").strip().lower()
    if normalized in {
        AccessLevel.OWNER.value,
        AccessLevel.FULL.value,
        AccessLevel.PARTIAL.value,
        AccessLevel.GUEST.value,
    }:
        return normalized
    return AccessLevel.FULL.value if is_allowed_sender else AccessLevel.GUEST.value


def build_policy_matrix(
    *,
    operator_id: str,
    account_id: str,
    acl_state: dict[str, list[str]] | None,
    web_write_requires_key: bool,
    runtime_lite: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Строит унифицированную policy matrix для owner UI и runtime handoff."""
    runtime_state = runtime_lite if isinstance(runtime_lite, dict) else {}
    acl_payload = acl_state if isinstance(acl_state, dict) else {}
    roles: dict[str, Any] = {}
    for role in (
        AccessLevel.OWNER.value,
        AccessLevel.FULL.value,
        AccessLevel.PARTIAL.value,
        AccessLevel.GUEST.value,
    ):
        roles[role] = {
            "label": role.upper(),
            "trusted": role in {AccessLevel.OWNER.value, AccessLevel.FULL.value},
            "subjects": sorted({str(item).strip() for item in acl_payload.get(role, []) if str(item).strip()}),
            "capabilities": dict(_ROLE_CAPABILITIES[role]),
            "notes": list(_ROLE_NOTES[role]),
        }

    last_route = runtime_state.get("last_runtime_route") if isinstance(runtime_state.get("last_runtime_route"), dict) else {}
    telegram_state = runtime_state.get("telegram_userbot") if isinstance(runtime_state.get("telegram_userbot"), dict) else {}

    return {
        "ok": True,
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "operator_id": str(operator_id or "").strip(),
        "account_id": str(account_id or "").strip(),
        "role_order": [
            AccessLevel.OWNER.value,
            AccessLevel.FULL.value,
            AccessLevel.PARTIAL.value,
            AccessLevel.GUEST.value,
        ],
        "roles": roles,
        "guardrails": {
            "partial_commands": sorted(PARTIAL_ACCESS_COMMANDS),
            "web_write_requires_key": bool(web_write_requires_key),
            "split_runtime_per_account": True,
            "telegram_userbot_state": str(
                telegram_state.get("startup_state")
                or telegram_state.get("state")
                or runtime_state.get("telegram_userbot_state")
                or "unknown"
            ).strip(),
            "openclaw_auth_state": str(runtime_state.get("openclaw_auth_state") or "unknown").strip(),
            "current_route_model": str(last_route.get("model") or "").strip(),
            "current_route_channel": str(last_route.get("channel") or "").strip(),
        },
        "summary": {
            "owner_subjects": len(roles[AccessLevel.OWNER.value]["subjects"]),
            "full_subjects": len(roles[AccessLevel.FULL.value]["subjects"]),
            "partial_subjects": len(roles[AccessLevel.PARTIAL.value]["subjects"]),
            "guest_policy": "implicit_default",
        },
    }


def build_capability_registry(
    *,
    operator_profile: dict[str, Any],
    runtime_lite: dict[str, Any],
    assistant_capabilities: dict[str, Any],
    ecosystem_capabilities: dict[str, Any],
    translator_readiness: dict[str, Any],
    policy_matrix: dict[str, Any],
) -> dict[str, Any]:
    """Собирает единый registry snapshot поверх уже подтверждённых truthful-срезов."""
    assistant_payload = assistant_capabilities if isinstance(assistant_capabilities, dict) else {}
    ecosystem_payload = ecosystem_capabilities if isinstance(ecosystem_capabilities, dict) else {}
    translator_payload = translator_readiness if isinstance(translator_readiness, dict) else {}
    runtime_state = runtime_lite if isinstance(runtime_lite, dict) else {}
    operator_payload = operator_profile if isinstance(operator_profile, dict) else {}

    ecosystem_services = ecosystem_payload.get("services") if isinstance(ecosystem_payload.get("services"), dict) else {}
    assistant_status = "ok"
    ecosystem_status = "ok" if all(
        bool((service or {}).get("ok", False))
        for name, service in ecosystem_services.items()
        if name != "krab"
    ) else "degraded"
    translator_status = str(translator_payload.get("readiness") or "unknown").strip() or "unknown"
    telegram_userbot = runtime_state.get("telegram_userbot") if isinstance(runtime_state.get("telegram_userbot"), dict) else {}
    telegram_status = str(
        telegram_userbot.get("startup_state")
        or telegram_userbot.get("state")
        or runtime_state.get("telegram_userbot_state")
        or "unknown"
    ).strip() or "unknown"
    browser_readiness = runtime_state.get("browser_readiness") if isinstance(runtime_state.get("browser_readiness"), dict) else {}
    voice_profile = translator_payload.get("runtime", {}).get("voice_profile") if isinstance(translator_payload.get("runtime"), dict) else {}
    system_status = "ok"
    if str(runtime_state.get("openclaw_auth_state") or "").strip().lower() not in {"ok", "configured", "ready"}:
        system_status = "degraded"
    if browser_readiness and str(browser_readiness.get("readiness") or "").strip().lower() == "blocked":
        system_status = "degraded"

    contours = {
        "assistant": {
            "status": assistant_status,
            "mode": str(assistant_payload.get("mode") or "unknown").strip(),
            "endpoint": str(assistant_payload.get("endpoint") or "").strip(),
            "task_types": list(assistant_payload.get("task_types") or []),
            "policy_matrix_endpoint": str(assistant_payload.get("policy_matrix_endpoint") or "").strip(),
        },
        "telegram_userbot": {
            "status": telegram_status,
            "primary_transport": True,
            "access_roles": list(policy_matrix.get("role_order") or []),
            "startup_error_code": str(telegram_userbot.get("startup_error_code") or "").strip(),
        },
        "ecosystem": {
            "status": ecosystem_status,
            "services": ecosystem_services,
        },
        "translator": {
            "status": translator_status,
            "canonical_backend": str(translator_payload.get("canonical_backend") or "").strip(),
            "foundation_ready": bool(translator_payload.get("foundation_ready")),
            "live_voice_ready": bool(translator_payload.get("live_voice_ready")),
        },
        "system": {
            "status": system_status,
            "browser_readiness": str(browser_readiness.get("readiness") or "unknown").strip() if browser_readiness else "unknown",
            "scheduler_enabled": bool(runtime_state.get("scheduler_enabled")),
            "voice_delivery": str(voice_profile.get("delivery") or "").strip(),
            "voice_enabled": bool(voice_profile.get("enabled")),
        },
    }

    ready_contours = sum(
        1 for contour in contours.values()
        if str(contour.get("status") or "").strip().lower() in {"ok", "ready", "running"}
    )

    return {
        "ok": True,
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "operator": {
            "operator_id": str(operator_payload.get("operator_id") or operator_payload.get("operator_name") or "").strip(),
            "account_id": str(operator_payload.get("account_id") or "").strip(),
            "account_mode": str(operator_payload.get("account_mode") or "").strip(),
        },
        "runtime": {
            "openclaw_auth_state": str(runtime_state.get("openclaw_auth_state") or "").strip(),
            "last_runtime_route": runtime_state.get("last_runtime_route") or {},
            "telegram_userbot_state": telegram_status,
            "scheduler_enabled": bool(runtime_state.get("scheduler_enabled")),
        },
        "policy_matrix": policy_matrix,
        "contours": contours,
        "summary": {
            "total_contours": len(contours),
            "ready_contours": ready_contours,
            "degraded_contours": max(0, len(contours) - ready_contours),
            "primary_transport": "telegram_userbot",
            "translator_backend": str(translator_payload.get("canonical_backend") or "").strip(),
        },
        "notes": [
            "Registry намеренно собирается поверх уже существующих truthful endpoint'ов, а не заменяет их магией.",
            "Policy matrix фиксирует не только health, но и реальные границы owner/full/partial/guest контуров.",
            "Этот snapshot рассчитан на owner UI, handoff bundle и будущий capability-aware routing.",
        ],
    }


__all__ = [
    "build_capability_registry",
    "build_policy_matrix",
    "resolve_access_mode",
]
