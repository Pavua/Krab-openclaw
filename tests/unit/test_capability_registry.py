# -*- coding: utf-8 -*-
"""
Тесты capability registry foundation.

Покрываем:
1) нормализацию access-mode без drift между контурами;
2) policy matrix для owner/full/partial/guest;
3) агрегированный registry поверх truthful capability-срезов.
"""

from __future__ import annotations

from src.core.access_control import AccessLevel
from src.core.capability_registry import (
    build_capability_registry,
    build_channel_capability_snapshot,
    build_policy_matrix,
    resolve_access_mode,
)


def test_resolve_access_mode_prefers_explicit_level_and_safe_fallback() -> None:
    """Explicit access-level должен побеждать fallback по allowed-sender."""
    assert resolve_access_mode(is_allowed_sender=True, access_level=AccessLevel.PARTIAL) == "partial"
    assert resolve_access_mode(is_allowed_sender=False, access_level="owner") == "owner"
    assert resolve_access_mode(is_allowed_sender=True, access_level=None) == "full"
    assert resolve_access_mode(is_allowed_sender=False, access_level=None) == "guest"


def test_build_policy_matrix_exposes_role_capabilities_and_guardrails() -> None:
    """Policy matrix должна честно описывать роли, ACL-summary и guardrails."""
    matrix = build_policy_matrix(
        operator_id="USER2",
        account_id="abc123def456",
        acl_state={
            "owner": ["pablito"],
            "full": ["teammate"],
            "partial": ["guest1", "guest2"],
        },
        web_write_requires_key=True,
        runtime_lite={
            "openclaw_auth_state": "ok",
            "last_runtime_route": {"channel": "telegram", "model": "google/gemini-3.1-pro-preview"},
            "telegram_userbot": {"startup_state": "running"},
        },
    )

    assert matrix["ok"] is True
    assert matrix["roles"]["owner"]["capabilities"]["browser_control"] is True
    assert matrix["roles"]["partial"]["capabilities"]["browser_control"] is False
    assert matrix["roles"]["full"]["capabilities"]["acl_admin"] is False
    assert "acl" not in matrix["roles"]["full"]["commands"]["commands"]
    assert "acl" in matrix["guardrails"]["owner_only_commands"]
    assert matrix["guardrails"]["web_write_requires_key"] is True
    assert matrix["guardrails"]["current_route_model"] == "google/gemini-3.1-pro-preview"
    assert matrix["summary"]["partial_subjects"] == 2


def test_build_capability_registry_aggregates_truthful_contours() -> None:
    """Unified registry должен собирать operator/runtime/policy/contours в один snapshot."""
    matrix = build_policy_matrix(
        operator_id="USER2",
        account_id="abc123def456",
        acl_state={},
        web_write_requires_key=False,
        runtime_lite={
            "openclaw_auth_state": "ok",
            "last_runtime_route": {"channel": "telegram", "model": "google/gemini-3.1-pro-preview"},
            "telegram_userbot_state": "running",
            "scheduler_enabled": True,
        },
    )

    registry = build_capability_registry(
        operator_profile={
            "operator_id": "USER2",
            "account_id": "abc123def456",
            "account_mode": "split_runtime_per_account",
        },
        runtime_lite={
            "openclaw_auth_state": "ok",
            "last_runtime_route": {"channel": "telegram", "model": "google/gemini-3.1-pro-preview"},
            "telegram_userbot_state": "running",
            "scheduler_enabled": True,
        },
        assistant_capabilities={
            "mode": "web_native",
            "endpoint": "/api/assistant/query",
            "task_types": ["chat", "coding"],
            "policy_matrix_endpoint": "/api/policy/matrix",
        },
        ecosystem_capabilities={
            "services": {
                "krab": {"ok": True},
                "voice_gateway": {"ok": True},
                "krab_ear": {"ok": True},
            }
        },
        translator_readiness={
            "readiness": "ready",
            "canonical_backend": "krab_voice_gateway",
            "foundation_ready": True,
            "live_voice_ready": False,
            "runtime": {"voice_profile": {"enabled": False, "delivery": "text+voice"}},
        },
        policy_matrix=matrix,
    )

    assert registry["ok"] is True
    assert registry["operator"]["account_mode"] == "split_runtime_per_account"
    assert registry["contours"]["assistant"]["mode"] == "web_native"
    assert registry["contours"]["translator"]["canonical_backend"] == "krab_voice_gateway"
    assert registry["summary"]["primary_transport"] == "telegram_userbot"


def test_build_channel_capability_snapshot_marks_reserve_safe_and_parity_gaps() -> None:
    """Channel snapshot должен различать primary userbot и reserve-safe bot."""
    snapshot = build_channel_capability_snapshot(
        operator_profile={
            "operator_id": "USER2",
            "account_id": "abc123def456",
        },
        runtime_lite={
            "telegram_userbot": {"startup_state": "running"},
        },
        runtime_channels_config={
            "telegram": {
                "enabled": True,
                "dmPolicy": "allowlist",
                "groupPolicy": "allowlist",
                "allowFrom": ["312322764"],
                "groupAllowFrom": ["312322764"],
            },
            "imessage": {
                "enabled": True,
                "dmPolicy": "open",
            },
        },
        policy_matrix={
            "role_order": ["owner", "full", "partial", "guest"],
        },
        workspace_state={
            "workspace_dir": "/Users/pablito/.openclaw/workspace-main-messaging",
            "shared_workspace_attached": True,
            "shared_memory_ready": True,
            "recent_memory_entries_count": 4,
        },
    )

    assert snapshot["ok"] is True
    assert snapshot["summary"]["reserve_safe"] is True
    assert snapshot["summary"]["shared_workspace_attached"] is True
    assert snapshot["summary"]["shared_workspace_dir"].endswith("workspace-main-messaging")
    assert snapshot["summary"]["shared_memory_recent_entries"] == 4
    assert snapshot["summary"]["primary_transport"] == "telegram_userbot"
    assert snapshot["shared_workspace"]["shared_memory_ready"] is True
    assert snapshot["channels"][0]["identity"]["operator_id"] == "USER2"
    assert snapshot["channels"][0]["semantics"]["streaming"] == "buffered_edit_loop"
    assert snapshot["channels"][0]["semantics"]["reasoning_visibility"] == "owner_optional_separate_trace"
    assert snapshot["channels"][0]["capabilities"]["shared_workspace_attached"] is True
    assert snapshot["channels"][1]["policy"]["dm_policy"] == "allowlist"
    assert snapshot["channels"][1]["capabilities"]["shared_workspace_path"].endswith("workspace-main-messaging")
    assert any("parity semantics" in item.lower() for item in snapshot["parity_gaps"])
