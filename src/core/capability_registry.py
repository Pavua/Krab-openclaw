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

from .access_control import AccessLevel, PARTIAL_ACCESS_COMMANDS, build_command_access_matrix
from .operator_identity import build_identity_envelope


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
        "screenshots": True,
        "ocr": True,
        "ui_automation": True,
        "clipboard_read": True,
        "clipboard_write": True,
        "tor_proxy": False,       # wishlist — не реализован (Phase 3 Шаг 7)
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
        "screenshots": True,
        "ocr": True,
        "ui_automation": False,   # только owner
        "clipboard_read": True,
        "clipboard_write": True,
        "tor_proxy": False,
        "voice_runtime": True,
        "model_routing": True,
        "acl_admin": False,
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
        "screenshots": False,
        "ocr": False,
        "ui_automation": False,
        "clipboard_read": False,
        "clipboard_write": False,
        "tor_proxy": False,
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
        "screenshots": False,
        "ocr": False,
        "ui_automation": False,
        "clipboard_read": False,
        "clipboard_write": False,
        "tor_proxy": False,
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
    command_access = build_command_access_matrix()
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
            "commands": dict((command_access.get("roles") or {}).get(role) or {}),
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
            "owner_only_commands": list(command_access.get("owner_only_commands") or []),
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
            "owner_only_command_count": int((command_access.get("summary") or {}).get("owner_only_count") or 0),
        },
    }


def build_channel_capability_snapshot(
    *,
    operator_profile: dict[str, Any],
    runtime_lite: dict[str, Any],
    runtime_channels_config: dict[str, Any] | None = None,
    policy_matrix: dict[str, Any] | None = None,
    workspace_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Строит truthful channel capability snapshot для primary/reserve/runtime каналов."""
    operator_payload = operator_profile if isinstance(operator_profile, dict) else {}
    runtime_state = runtime_lite if isinstance(runtime_lite, dict) else {}
    channels_config = runtime_channels_config if isinstance(runtime_channels_config, dict) else {}
    policy_payload = policy_matrix if isinstance(policy_matrix, dict) else {}
    workspace_payload = workspace_state if isinstance(workspace_state, dict) else {}

    operator_id = str(operator_payload.get("operator_id") or operator_payload.get("operator_name") or "").strip()
    account_id = str(operator_payload.get("account_id") or "").strip()

    telegram_userbot = runtime_state.get("telegram_userbot") if isinstance(runtime_state.get("telegram_userbot"), dict) else {}
    userbot_status = str(
        telegram_userbot.get("startup_state")
        or telegram_userbot.get("state")
        or runtime_state.get("telegram_userbot_state")
        or "unknown"
    ).strip() or "unknown"

    telegram_cfg = channels_config.get("telegram") if isinstance(channels_config.get("telegram"), dict) else {}
    reserve_enabled = bool(telegram_cfg.get("enabled"))
    reserve_dm_policy = str(telegram_cfg.get("dmPolicy") or "unknown").strip().lower() or "unknown"
    reserve_group_policy = str(telegram_cfg.get("groupPolicy") or "unknown").strip().lower() or "unknown"
    reserve_allow_from = telegram_cfg.get("allowFrom") if isinstance(telegram_cfg.get("allowFrom"), list) else []
    reserve_group_allow_from = (
        telegram_cfg.get("groupAllowFrom") if isinstance(telegram_cfg.get("groupAllowFrom"), list) else []
    )
    reserve_safe = reserve_enabled and reserve_dm_policy == "allowlist" and reserve_group_policy == "allowlist"

    reserve_status = "disabled"
    if reserve_enabled and reserve_safe:
        reserve_status = "reserve_safe"
    elif reserve_enabled:
        reserve_status = "attention"

    channels: list[dict[str, Any]] = [
        {
            "id": "telegram_userbot",
            "label": "Python Telegram userbot",
            "kind": "telegram_userbot",
            "transport_role": "primary",
            "status": userbot_status,
            "identity": build_identity_envelope(
                operator_id=operator_id,
                account_id=account_id,
                channel_id="telegram_userbot",
                team_id="owner",
                approval_scope="owner",
            ),
            "semantics": {
                "streaming": "buffered_edit_loop",
                "attachments": ["text", "photo", "audio", "voice"],
                "approvals": "owner_inbox_linked",
                "action_reporting": "confirmed",
                "runtime_self_check": "confirmed",
                "reasoning_visibility": "owner_optional_separate_trace",
            },
            "capabilities": {
                "inbound_owner_requests": True,
                "reply_trace": True,
                "shared_memory": True,
                "shared_workspace_attached": bool(workspace_payload.get("shared_workspace_attached")),
                "shared_workspace_path": str(workspace_payload.get("workspace_dir") or "").strip(),
                "owner_tools_confirmed": True,
                "access_roles": list(policy_payload.get("role_order") or []),
            },
            "notes": [
                "Это richest contour и основной owner transport текущего runtime.",
                "Здесь уже подтверждены inbox capture, reply trace и truthful self-check.",
                "Telegram edit-loop показывает промежуточный draft, но это ещё не нативный provider chunk-stream.",
            ],
        },
        {
            "id": "telegram_reserve_bot",
            "label": "Reserve Telegram Bot",
            "kind": "telegram_reserve_bot",
            "transport_role": "reserve_safe",
            "status": reserve_status,
            "identity": build_identity_envelope(
                operator_id=operator_id,
                account_id=account_id,
                channel_id="telegram_reserve_bot",
                team_id="reserve",
                approval_scope="owner",
            ),
            "semantics": {
                "streaming": "not_confirmed",
                "attachments": ["text"],
                "approvals": "not_confirmed",
                "action_reporting": "text_only",
                "runtime_self_check": "not_confirmed",
            },
            "capabilities": {
                "inbound_owner_requests": False,
                "reply_trace": False,
                "shared_memory": True,
                "shared_workspace_attached": bool(workspace_payload.get("shared_workspace_attached")),
                "shared_workspace_path": str(workspace_payload.get("workspace_dir") or "").strip(),
                "owner_tools_confirmed": False,
                "reserve_safe": reserve_safe,
            },
            "policy": {
                "enabled": reserve_enabled,
                "dm_policy": reserve_dm_policy,
                "group_policy": reserve_group_policy,
                "allow_from_count": len(reserve_allow_from),
                "group_allow_from_count": len(reserve_group_allow_from),
            },
            "notes": [
                "Это safe contour для деградации и emergency delivery, а не owner-rich primary transport.",
                "Memory общая, но owner-инструменты и полный runtime self-check здесь не подтверждаются.",
            ],
        },
    ]

    extra_runtime_channels: list[dict[str, Any]] = []
    for name, payload in sorted(channels_config.items()):
        normalized_name = str(name or "").strip().lower()
        if not normalized_name or normalized_name == "telegram":
            continue
        config_payload = payload if isinstance(payload, dict) else {}
        enabled = bool(config_payload.get("enabled"))
        extra_runtime_channels.append(
            {
                "id": f"runtime_{normalized_name}",
                "label": normalized_name,
                "kind": normalized_name,
                "transport_role": "runtime_channel",
                "status": "enabled" if enabled else "disabled",
                "identity": build_identity_envelope(
                    operator_id=operator_id,
                    account_id=account_id,
                    channel_id=normalized_name,
                    team_id="runtime",
                    approval_scope="owner",
                ),
                "semantics": {
                    "streaming": "unknown",
                    "attachments": ["text"],
                    "approvals": "unknown",
                    "action_reporting": "unknown",
                    "runtime_self_check": "unknown",
                },
                "capabilities": {
                    "configured": enabled,
                    "owner_tools_confirmed": False,
                },
                "policy": {
                    "enabled": enabled,
                    "dm_policy": str(config_payload.get("dmPolicy") or "").strip().lower(),
                    "group_policy": str(config_payload.get("groupPolicy") or "").strip().lower(),
                },
                "notes": [
                    "Канал найден в runtime-конфиге OpenClaw, но parity-семантика для него ещё не подтверждена.",
                ],
            }
        )

    channels.extend(extra_runtime_channels)

    parity_gaps: list[str] = []
    if not reserve_safe:
        parity_gaps.append("Reserve Telegram Bot ещё не подтверждён в reserve-safe parity режиме.")
    parity_gaps.append("Reserve Telegram Bot пока не подтверждает streaming и полноценный runtime self-check.")
    parity_gaps.append("Primary Telegram userbot пока даёт buffered edit-loop, а не полноценный provider chunk-stream.")
    parity_gaps.append("Attachment normalization и approval semantics пока richest только в Python userbot.")
    if extra_runtime_channels:
        parity_gaps.append("Дополнительные runtime channels найдены, но их parity semantics ещё не сведены к общему контракту.")

    ready_channels = sum(
        1
        for row in channels
        if str(row.get("status") or "").strip().lower() in {"running", "ready", "enabled", "reserve_safe"}
    )

    return {
        "ok": True,
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "channels": channels,
        "shared_workspace": workspace_payload,
        "summary": {
            "primary_transport": "telegram_userbot",
            "reserve_transport": "telegram_reserve_bot",
            "reserve_safe": reserve_safe,
            "shared_workspace_attached": bool(workspace_payload.get("shared_workspace_attached")),
            "shared_memory_ready": bool(workspace_payload.get("shared_memory_ready")),
            "shared_workspace_dir": str(workspace_payload.get("workspace_dir") or "").strip(),
            "shared_memory_recent_entries": int(workspace_payload.get("recent_memory_entries_count") or 0),
            "configured_runtime_channels": len(extra_runtime_channels),
            "total_channels": len(channels),
            "ready_channels": ready_channels,
        },
        "parity_gaps": parity_gaps,
    }


def build_system_control_snapshot(
    *,
    browser_probe: dict[str, Any] | None = None,
    macos_probe: dict[str, Any] | None = None,
    mcp_probe: dict[str, Any] | None = None,
    tor_probe: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Строит truthful System Control v2 capability snapshot (Phase 3).

    Принимает опциональные результаты live-проб bridge-классов:
    - browser_probe: результат BrowserBridge.health_check() (Шаг 2)
    - macos_probe:   результат MacOSAutomation.health_check() (Шаг 4)
    - mcp_probe:     результат MCPClient.health_check() (Шаг 2)

    Если проба не передана → статус capability = "unknown".
    Это отличается от "unavailable" (проверено и недоступно) и "blocked" (CDP/порт заблокирован).
    """
    browser_payload = browser_probe if isinstance(browser_probe, dict) else {}
    macos_payload = macos_probe if isinstance(macos_probe, dict) else {}
    mcp_payload = mcp_probe if isinstance(mcp_probe, dict) else {}
    tor_payload = tor_probe if isinstance(tor_probe, dict) else {}

    def _probe_status(payload: dict[str, Any]) -> tuple[str, str]:
        """Returns (status, error) from a probe result dict."""
        if not payload:
            return "unknown", ""
        if payload.get("ok"):
            return "ready", ""
        if payload.get("blocked"):
            return "blocked", str(payload.get("error") or "").strip()
        return "degraded", str(payload.get("error") or "").strip()

    browser_status, browser_error = _probe_status(browser_payload)
    macos_status, macos_error = _probe_status(macos_payload)
    mcp_status, mcp_error = _probe_status(mcp_payload)
    tor_status, tor_error = _probe_status(tor_payload)

    def _derived(base_status: str) -> str:
        """Derives a capability status from its required base status."""
        if base_status == "unknown":
            return "unknown"
        if base_status == "ready":
            return "ready"
        return "unavailable"

    capabilities: dict[str, Any] = {
        "browser_control": {
            "status": browser_status,
            "error": browser_error,
            "role_requirement": "owner_or_full",
            "note": "CDP через port 9222; Шаг 2 добавит BrowserBridge.health_check()",
        },
        "screenshots": {
            "status": _derived(browser_status),
            "depends_on": "browser_control",
            "role_requirement": "owner_or_full",
            "note": "browser_bridge.take_screenshot() — Шаг 3",
        },
        "mcp_relay": {
            "status": mcp_status,
            "error": mcp_error,
            "role_requirement": "owner_or_full",
            "note": "MCP клиент через OpenClaw; health_check() активен",
        },
        "macos_control": {
            "status": macos_status,
            "error": macos_error,
            "role_requirement": "owner_or_full",
            "note": "macos_automation.py — osascript + Accessibility; Шаг 4",
        },
        "ui_automation": {
            "status": _derived(macos_status),
            "depends_on": "macos_control",
            "role_requirement": "owner_only",
            "note": "click/type/focus через AppleScript — Шаг 4",
        },
        "clipboard_read": {
            "status": _derived(macos_status),
            "depends_on": "macos_control",
            "role_requirement": "owner_or_full",
        },
        "clipboard_write": {
            "status": _derived(macos_status),
            "depends_on": "macos_control",
            "role_requirement": "owner_or_full",
        },
        "ocr": {
            "status": "ready" if macos_payload.get("ocr_available") else (
                "unavailable" if macos_payload and not macos_payload.get("ocr_available") else "unknown"
            ),
            "depends_on": "macos_control",
            "role_requirement": "owner_or_full",
            "note": "tesseract CLI — brew install tesseract; Шаг 5",
        },
        "tor_proxy": {
            "status": tor_status,
            "error": tor_error,
            "role_requirement": "owner_only",
            "note": "SOCKS5 :9050 через tor_bridge; TOR_ENABLED в .env",
        },
    }

    ready_count = sum(1 for c in capabilities.values() if c.get("status") == "ready")
    unknown_count = sum(1 for c in capabilities.values() if c.get("status") == "unknown")
    unavailable_count = sum(1 for c in capabilities.values() if c.get("status") in {"unavailable", "degraded", "blocked"})
    not_impl_count = sum(1 for c in capabilities.values() if c.get("status") == "not_implemented")

    if ready_count > 0:
        overall_status = "ready"
    elif unknown_count > 0:
        overall_status = "unknown"
    else:
        overall_status = "degraded"

    return {
        "ok": True,
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": overall_status,
        "capabilities": capabilities,
        "probes": {
            "browser_probed": bool(browser_payload),
            "macos_probed": bool(macos_payload),
            "mcp_probed": bool(mcp_payload),
            "tor_probed": bool(tor_payload),
        },
        "summary": {
            "total": len(capabilities),
            "ready": ready_count,
            "unknown": unknown_count,
            "unavailable": unavailable_count,
            "not_implemented": not_impl_count,
        },
        "notes": [
            "Phase 3 Шаг 1: capability matrix зарегистрирована.",
            "Phase 3 Шаг 2: BrowserBridge.health_check() и MCPClient.health_check() — активны.",
            "Phase 3 Шаг 4: MacOSAutomation.health_check() — активен.",
        ],
    }


def build_capability_registry(
    *,
    operator_profile: dict[str, Any],
    runtime_lite: dict[str, Any],
    assistant_capabilities: dict[str, Any],
    ecosystem_capabilities: dict[str, Any],
    translator_readiness: dict[str, Any],
    policy_matrix: dict[str, Any],
    channel_capabilities: dict[str, Any] | None = None,
    system_control: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Собирает единый registry snapshot поверх уже подтверждённых truthful-срезов."""
    assistant_payload = assistant_capabilities if isinstance(assistant_capabilities, dict) else {}
    ecosystem_payload = ecosystem_capabilities if isinstance(ecosystem_capabilities, dict) else {}
    translator_payload = translator_readiness if isinstance(translator_readiness, dict) else {}
    runtime_state = runtime_lite if isinstance(runtime_lite, dict) else {}
    operator_payload = operator_profile if isinstance(operator_profile, dict) else {}

    ecosystem_services = ecosystem_payload.get("services") if isinstance(ecosystem_payload.get("services"), dict) else {}
    channel_payload = channel_capabilities if isinstance(channel_capabilities, dict) else {}
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
    system_control_payload = system_control if isinstance(system_control, dict) else {}
    system_status = "ok"
    if str(runtime_state.get("openclaw_auth_state") or "").strip().lower() not in {"ok", "configured", "ready"}:
        system_status = "degraded"
    if browser_readiness and str(browser_readiness.get("readiness") or "").strip().lower() == "blocked":
        system_status = "degraded"
    # Если есть live system_control snapshot — он переопределяет system_status
    if system_control_payload and str(system_control_payload.get("status") or "").strip().lower() in {"ready", "degraded", "unknown"}:
        system_status = str(system_control_payload.get("status")).strip().lower()
    channel_summary = channel_payload.get("summary") if isinstance(channel_payload.get("summary"), dict) else {}
    channels_status = "degraded"
    if bool(channel_summary.get("reserve_safe")) and bool(channel_summary.get("ready_channels")):
        channels_status = "ready"
    elif bool(channel_summary.get("ready_channels")):
        channels_status = "attention"

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
            **({"control": system_control_payload} if system_control_payload else {}),
        },
    }
    if channel_payload:
        contours["channels"] = {
            "status": channels_status,
            "summary": channel_summary,
            "parity_gaps": list(channel_payload.get("parity_gaps") or []),
            "channels": list(channel_payload.get("channels") or []),
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
            "reserve_transport": "telegram_reserve_bot",
            "translator_backend": str(translator_payload.get("canonical_backend") or "").strip(),
        },
        "notes": [
            "Registry намеренно собирается поверх уже существующих truthful endpoint'ов, а не заменяет их магией.",
            "Policy matrix фиксирует не только health, но и реальные границы owner/full/partial/guest контуров.",
            "Этот snapshot рассчитан на owner UI, handoff bundle и будущий capability-aware routing.",
        ],
    }


def check_capability(access_level: str, capability: str) -> bool:
    """
    Runtime enforcement: проверяет имеет ли role доступ к capability.

    Используется перед выполнением действий — не декларативно, а реально блокирует.
    """
    role_caps = _ROLE_CAPABILITIES.get(access_level)
    if role_caps is None:
        return False
    return bool(role_caps.get(capability, False))


def get_denied_capabilities(access_level: str) -> list[str]:
    """Возвращает список capabilities которые запрещены для этого уровня доступа."""
    role_caps = _ROLE_CAPABILITIES.get(access_level, {})
    return [cap for cap, allowed in role_caps.items() if not allowed]


__all__ = [
    "build_capability_registry",
    "build_channel_capability_snapshot",
    "build_policy_matrix",
    "build_system_control_snapshot",
    "check_capability",
    "get_denied_capabilities",
    "resolve_access_mode",
]
