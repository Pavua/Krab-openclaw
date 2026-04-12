# -*- coding: utf-8 -*-
"""
Расширенные тесты capability_registry.py.

Покрываем:
- check_capability / get_denied_capabilities
- build_system_control_snapshot (probe-логика, OCR, hammerspoon)
- build_policy_matrix: edge cases (пустой ACL, None runtime)
- build_channel_capability_snapshot: disabled reserve, extra channels, workspace отсутствует
- build_capability_registry: system_control override, деградация экосистемы, channels contour
- resolve_access_mode: пробелы, неизвестные строки, AccessLevel enum
"""

from __future__ import annotations

from src.core.access_control import AccessLevel
from src.core.capability_registry import (
    build_capability_registry,
    build_channel_capability_snapshot,
    build_policy_matrix,
    build_system_control_snapshot,
    check_capability,
    get_denied_capabilities,
    resolve_access_mode,
)

# ---------------------------------------------------------------------------
# check_capability
# ---------------------------------------------------------------------------


def test_check_capability_owner_has_all_sensitive_caps() -> None:
    """Owner должен иметь acl_admin и runtime_mutation."""
    assert check_capability("owner", "acl_admin") is True
    assert check_capability("owner", "runtime_mutation") is True
    assert check_capability("owner", "browser_control") is True


def test_check_capability_guest_has_only_chat() -> None:
    """Guest получает только chat; всё остальное закрыто."""
    assert check_capability("guest", "chat") is True
    assert check_capability("guest", "web_search") is False
    assert check_capability("guest", "file_ops") is False
    assert check_capability("guest", "memory") is False


def test_check_capability_unknown_role_returns_false() -> None:
    """Несуществующая роль → False, не исключение."""
    assert check_capability("superadmin", "chat") is False
    assert check_capability("", "chat") is False


def test_check_capability_unknown_cap_returns_false() -> None:
    """Несуществующий capability → False для любой роли."""
    assert check_capability("owner", "nonexistent_capability") is False
    assert check_capability("full", "fly_to_moon") is False


def test_check_capability_partial_restricted_list() -> None:
    """Partial закрывает memory, inbox, approvals, file_ops, browser_control."""
    for cap in ("memory", "inbox", "approvals", "file_ops", "browser_control"):
        assert check_capability("partial", cap) is False


def test_check_capability_full_no_acl_admin_no_ui_automation() -> None:
    """Full не имеет acl_admin и ui_automation — только owner."""
    assert check_capability("full", "acl_admin") is False
    assert check_capability("full", "ui_automation") is False


# ---------------------------------------------------------------------------
# get_denied_capabilities
# ---------------------------------------------------------------------------


def test_get_denied_capabilities_guest_large_list() -> None:
    """Guest имеет почти все capabilities закрытыми."""
    denied = get_denied_capabilities("guest")
    # chat у guest разрешён, остальное — нет
    assert "chat" not in denied
    assert "web_search" in denied
    assert "memory" in denied
    assert len(denied) >= 15


def test_get_denied_capabilities_owner_minimal() -> None:
    """Owner denied-список очень маленький (только tor_proxy wishlist)."""
    denied = get_denied_capabilities("owner")
    assert "tor_proxy" in denied
    # privileged caps — не в denied
    assert "acl_admin" not in denied
    assert "runtime_mutation" not in denied


def test_get_denied_capabilities_unknown_role_empty() -> None:
    """Для несуществующей роли возвращается пустой список, не исключение."""
    assert get_denied_capabilities("wizard") == []


# ---------------------------------------------------------------------------
# resolve_access_mode — edge cases
# ---------------------------------------------------------------------------


def test_resolve_access_mode_strips_whitespace() -> None:
    """Лишние пробелы в строке access_level не должны ломать нормализацию."""
    assert resolve_access_mode(is_allowed_sender=True, access_level="  owner  ") == "owner"
    assert resolve_access_mode(is_allowed_sender=True, access_level=" partial ") == "partial"


def test_resolve_access_mode_unknown_string_fallback() -> None:
    """Неизвестная строка → fallback по is_allowed_sender."""
    assert resolve_access_mode(is_allowed_sender=True, access_level="superuser") == "full"
    assert resolve_access_mode(is_allowed_sender=False, access_level="superuser") == "guest"


def test_resolve_access_mode_enum_all_values() -> None:
    """AccessLevel enum — все четыре значения нормализуются корректно."""
    for level in (AccessLevel.OWNER, AccessLevel.FULL, AccessLevel.PARTIAL, AccessLevel.GUEST):
        result = resolve_access_mode(is_allowed_sender=False, access_level=level)
        assert result == level.value


# ---------------------------------------------------------------------------
# build_system_control_snapshot
# ---------------------------------------------------------------------------


def test_build_system_control_snapshot_all_probes_missing_gives_unknown() -> None:
    """Без probe-данных весь snapshot должен быть unknown."""
    snap = build_system_control_snapshot()
    assert snap["ok"] is True
    assert snap["status"] == "unknown"
    for cap_data in snap["capabilities"].values():
        assert cap_data["status"] in {"unknown"}
    assert snap["probes"]["browser_probed"] is False
    assert snap["probes"]["macos_probed"] is False


def test_build_system_control_snapshot_browser_ok_gives_ready_derived() -> None:
    """Browser probe ok=True → browser_control ready и screenshots ready (derived)."""
    snap = build_system_control_snapshot(browser_probe={"ok": True})
    assert snap["capabilities"]["browser_control"]["status"] == "ready"
    assert snap["capabilities"]["screenshots"]["status"] == "ready"
    assert snap["probes"]["browser_probed"] is True


def test_build_system_control_snapshot_browser_blocked() -> None:
    """Browser blocked → browser_control blocked, screenshots unavailable."""
    snap = build_system_control_snapshot(
        browser_probe={"ok": False, "blocked": True, "error": "CDP refused"}
    )
    assert snap["capabilities"]["browser_control"]["status"] == "blocked"
    # derived статус при not-ready → unavailable
    assert snap["capabilities"]["screenshots"]["status"] == "unavailable"


def test_build_system_control_snapshot_macos_ok_ocr_available() -> None:
    """macos ok + ocr_available → macos_control ready, ocr ready, ui_automation ready."""
    snap = build_system_control_snapshot(macos_probe={"ok": True, "ocr_available": True})
    assert snap["capabilities"]["macos_control"]["status"] == "ready"
    assert snap["capabilities"]["ocr"]["status"] == "ready"
    assert snap["capabilities"]["ui_automation"]["status"] == "ready"
    assert snap["capabilities"]["clipboard_read"]["status"] == "ready"


def test_build_system_control_snapshot_tor_probe_degraded() -> None:
    """Tor probe не ok и не blocked → degraded."""
    snap = build_system_control_snapshot(tor_probe={"ok": False, "error": "SOCKS timeout"})
    assert snap["capabilities"]["tor_proxy"]["status"] == "degraded"
    assert snap["capabilities"]["tor_proxy"]["error"] == "SOCKS timeout"
    assert snap["probes"]["tor_probed"] is True


def test_build_system_control_snapshot_hammerspoon_ok() -> None:
    """Hammerspoon probe ok → ready."""
    snap = build_system_control_snapshot(hammerspoon_probe={"ok": True})
    assert snap["capabilities"]["hammerspoon"]["status"] == "ready"


def test_build_system_control_snapshot_summary_counts() -> None:
    """Summary.ready считает только реально ready capabilities."""
    snap = build_system_control_snapshot(
        browser_probe={"ok": True},
        macos_probe={"ok": True, "ocr_available": True},
    )
    assert (
        snap["summary"]["ready"] >= 5
    )  # browser + screenshots + macos + clipboard_read/write + ocr + ui_automation
    assert snap["summary"]["total"] == len(snap["capabilities"])


# ---------------------------------------------------------------------------
# build_policy_matrix — edge cases
# ---------------------------------------------------------------------------


def test_build_policy_matrix_none_acl_and_none_runtime() -> None:
    """None acl_state и None runtime_lite не должны падать."""
    matrix = build_policy_matrix(
        operator_id="",
        account_id="",
        acl_state=None,
        web_write_requires_key=False,
        runtime_lite=None,
    )
    assert matrix["ok"] is True
    # Все четыре роли присутствуют
    for role in ("owner", "full", "partial", "guest"):
        assert role in matrix["roles"]
    # При пустом ACL subjects пустые
    assert matrix["roles"]["owner"]["subjects"] == []
    assert matrix["guardrails"]["telegram_userbot_state"] == "unknown"


def test_build_policy_matrix_trusted_roles_correct() -> None:
    """Trusted должно быть True только для owner и full."""
    matrix = build_policy_matrix(
        operator_id="x", account_id="y", acl_state={}, web_write_requires_key=False
    )
    assert matrix["roles"]["owner"]["trusted"] is True
    assert matrix["roles"]["full"]["trusted"] is True
    assert matrix["roles"]["partial"]["trusted"] is False
    assert matrix["roles"]["guest"]["trusted"] is False


# ---------------------------------------------------------------------------
# build_channel_capability_snapshot — disabled reserve, нет workspace
# ---------------------------------------------------------------------------


def test_build_channel_capability_snapshot_disabled_reserve() -> None:
    """Отключённый telegram reserve → статус disabled."""
    snap = build_channel_capability_snapshot(
        operator_profile={"operator_id": "U1", "account_id": "A1"},
        runtime_lite={},
        runtime_channels_config={"telegram": {"enabled": False}},
    )
    assert snap["ok"] is True
    reserve = snap["channels"][1]
    assert reserve["id"] == "telegram_reserve_bot"
    assert reserve["status"] == "disabled"
    assert snap["summary"]["reserve_safe"] is False


def test_build_channel_capability_snapshot_no_workspace() -> None:
    """Без workspace_state summary показывает нули и False флаги."""
    snap = build_channel_capability_snapshot(
        operator_profile={"operator_id": "U1", "account_id": "A1"},
        runtime_lite={},
    )
    assert snap["summary"]["shared_workspace_attached"] is False
    assert snap["summary"]["shared_workspace_dir"] == ""
    assert snap["summary"]["shared_memory_recent_entries"] == 0


def test_build_channel_capability_snapshot_parity_gap_always_present() -> None:
    """Parity gaps должны содержать хотя бы строку про Primary userbot edit-loop."""
    snap = build_channel_capability_snapshot(operator_profile={}, runtime_lite={})
    all_gaps = " ".join(snap["parity_gaps"]).lower()
    assert "edit-loop" in all_gaps or "edit" in all_gaps


# ---------------------------------------------------------------------------
# build_capability_registry — system_control override, ecosystem degraded
# ---------------------------------------------------------------------------


def _minimal_matrix() -> dict:
    """Минимальная policy matrix для использования в registry тестах."""
    return build_policy_matrix(
        operator_id="U", account_id="A", acl_state={}, web_write_requires_key=False
    )


def test_build_capability_registry_system_control_overrides_status() -> None:
    """system_control snapshot с status=ready переопределяет system_status в contours."""
    registry = build_capability_registry(
        operator_profile={"operator_id": "U", "account_id": "A", "account_mode": "split"},
        runtime_lite={"openclaw_auth_state": "error"},  # должен был дать degraded
        assistant_capabilities={},
        ecosystem_capabilities={},
        translator_readiness={},
        policy_matrix=_minimal_matrix(),
        system_control={"ok": True, "status": "ready"},
    )
    assert registry["contours"]["system"]["status"] == "ready"
    assert "control" in registry["contours"]["system"]


def test_build_capability_registry_ecosystem_degraded_when_service_fails() -> None:
    """Если не-krab сервис ok=False → ecosystem status degraded."""
    registry = build_capability_registry(
        operator_profile={},
        runtime_lite={"openclaw_auth_state": "ok"},
        assistant_capabilities={},
        ecosystem_capabilities={"services": {"voice_gateway": {"ok": False}, "krab": {"ok": True}}},
        translator_readiness={},
        policy_matrix=_minimal_matrix(),
    )
    assert registry["contours"]["ecosystem"]["status"] == "degraded"


def test_build_capability_registry_includes_channels_contour_when_provided() -> None:
    """При передаче channel_capabilities в registry появляется contour 'channels'."""
    chan_snap = build_channel_capability_snapshot(
        operator_profile={"operator_id": "U", "account_id": "A"},
        runtime_lite={"telegram_userbot": {"startup_state": "running"}},
        runtime_channels_config={
            "telegram": {
                "enabled": True,
                "dmPolicy": "allowlist",
                "groupPolicy": "allowlist",
                "allowFrom": ["1"],
                "groupAllowFrom": ["1"],
            }
        },
    )
    registry = build_capability_registry(
        operator_profile={},
        runtime_lite={"openclaw_auth_state": "ok"},
        assistant_capabilities={},
        ecosystem_capabilities={},
        translator_readiness={},
        policy_matrix=_minimal_matrix(),
        channel_capabilities=chan_snap,
    )
    assert "channels" in registry["contours"]
    assert registry["contours"]["channels"]["status"] in {"ready", "attention", "degraded"}


def test_build_capability_registry_summary_counts_contours() -> None:
    """summary.total_contours должен совпадать с len(contours)."""
    registry = build_capability_registry(
        operator_profile={},
        runtime_lite={"openclaw_auth_state": "ok"},
        assistant_capabilities={},
        ecosystem_capabilities={},
        translator_readiness={},
        policy_matrix=_minimal_matrix(),
    )
    assert registry["summary"]["total_contours"] == len(registry["contours"])
    # degraded_contours не отрицательный
    assert registry["summary"]["degraded_contours"] >= 0
