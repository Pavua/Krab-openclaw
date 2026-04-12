# -*- coding: utf-8 -*-
"""
translator_live_trial_preflight.py — truthful preflight для controlled live trial.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _helper(project_root: Path | None, name: str) -> dict[str, Any]:
    """Возвращает structured helper-card для owner-панели и handoff."""
    root = Path(project_root or Path.cwd())
    path = root / name
    return {
        "label": name.replace(".command", ""),
        "path": str(path),
        "exists": path.exists(),
    }


def build_translator_live_trial_preflight(
    *,
    project_root: Path | None = None,
    runtime_lite: dict[str, Any] | None = None,
    translator_readiness: dict[str, Any] | None = None,
    delivery_matrix: dict[str, Any] | None = None,
    mobile_readiness: dict[str, Any] | None = None,
    signal_log: Path | None = None,
    current_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Собирает truthful preflight для controlled live trial ordinary-call translator path."""
    runtime_payload = dict(runtime_lite or {})
    readiness = dict(translator_readiness or {})
    delivery = dict(delivery_matrix or {})
    mobile = dict(mobile_readiness or {})
    ordinary_calls = (
        dict(delivery.get("ordinary_calls") or {})
        if isinstance(delivery.get("ordinary_calls"), dict)
        else {}
    )
    services = (
        dict(readiness.get("services") or {}) if isinstance(readiness.get("services"), dict) else {}
    )
    account_runtime = (
        dict(readiness.get("account_runtime") or {})
        if isinstance(readiness.get("account_runtime"), dict)
        else {}
    )
    mobile_actions = (
        dict(mobile.get("actions") or {}) if isinstance(mobile.get("actions"), dict) else {}
    )

    ordinary_status = str(ordinary_calls.get("status") or "blocked").strip() or "blocked"
    mobile_status = str(mobile.get("status") or "unknown").strip() or "unknown"
    gateway_ok = bool(((services.get("voice_gateway") or {}).get("ok")))
    workspace_ready = bool(account_runtime.get("shared_workspace_attached"))
    userbot_ready = bool(account_runtime.get("userbot_authorized"))

    blockers = [
        str(item).strip()
        for item in (ordinary_calls.get("blockers") or [])
        if str(item or "").strip()
    ]
    warnings = [
        str(item).strip()
        for item in (ordinary_calls.get("next_steps") or [])
        if str(item or "").strip()
    ]

    if ordinary_status == "trial_ready":
        status = "ready_for_trial"
    elif mobile_status == "not_configured":
        status = "companion_pending"
    elif mobile_status in {"registered", "attention"} or ordinary_status in {
        "device_ready",
        "in_progress",
    }:
        status = "session_pending"
    else:
        status = "blocked"

    checks = {
        "voice_gateway": {
            "ok": gateway_ok,
            "status": str((services.get("voice_gateway") or {}).get("status") or "unknown"),
        },
        "shared_workspace": {
            "ok": workspace_ready,
            "status": "ready" if workspace_ready else "missing",
        },
        "userbot_runtime": {
            "ok": userbot_ready,
            "status": "ready" if userbot_ready else "attention",
        },
        "mobile_companion": {
            "ok": mobile_status in {"registered", "bound"},
            "status": mobile_status,
        },
        "active_session": {
            "ok": ordinary_status in {"device_ready", "trial_ready"},
            "status": ordinary_status,
        },
    }

    next_step = str(mobile_actions.get("recommended_next_step") or "").strip()
    if not next_step:
        next_step = {
            "ready_for_trial": "run_controlled_live_trial",
            "companion_pending": "register_companion",
            "session_pending": "prepare_or_bind_session",
        }.get(status, "inspect_gateway_and_mobile_truth")

    return {
        "ok": True,
        "status": status,
        "ready": status == "ready_for_trial",
        "blockers": blockers,
        "warnings": warnings,
        "checks": checks,
        "translator": {
            "ordinary_calls_status": ordinary_status,
            "internet_calls_status": str(
                ((delivery.get("internet_calls") or {}).get("status")) or "planned"
            ),
            "selected_device_id": str(ordinary_calls.get("selected_device_id") or ""),
            "active_session_id": str(ordinary_calls.get("active_session_id") or ""),
            "active_session_status": str(ordinary_calls.get("active_session_status") or ""),
        },
        "helpers": {
            "start_full_ecosystem": _helper(project_root, "Start Full Ecosystem.command"),
            "start_voice_gateway": _helper(project_root, "Start Voice Gateway.command"),
            "prepare_xcode_project": _helper(
                project_root, "Prepare iPhone Companion Xcode Project.command"
            ),
            "release_gate": _helper(project_root, "Release Gate.command"),
        },
        "actions": {
            "next_step": next_step,
            "trial_prep_available": bool(mobile_actions.get("trial_prep_available")),
            "bind_available": bool(mobile_actions.get("bind_available")),
            "register_available": bool(mobile_actions.get("register_available")),
        },
        "runtime_lite": runtime_payload,
        "translator_readiness": readiness,
        "delivery_matrix": delivery,
        "mobile_readiness": mobile,
    }
