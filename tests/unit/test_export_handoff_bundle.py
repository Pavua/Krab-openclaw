# -*- coding: utf-8 -*-
"""
Тесты handoff bundle builder для operator-workflow truth.

Покрываем:
1) attach summary показывает operator workflow counters и последний reply trail;
2) manifest сохраняет operator workflow snapshot machine-readable.
"""

from __future__ import annotations

from pathlib import Path

import scripts.export_handoff_bundle as handoff_module


def _runtime_snapshot_payload() -> dict:
    """Готовит минимальный runtime snapshot с operator workflow."""
    return {
        "git": {
            "branch": "codex/inbox-transport-trace-propagation",
            "head": "abc123",
        },
        "health": {
            "web_lite": {
                "json": {
                    "telegram_userbot_state": "running",
                    "openclaw_auth_state": "configured",
                    "last_runtime_route": {
                        "model": "google/gemini-3.1-pro-preview",
                        "provider": "google",
                    },
                }
            }
        },
        "known_issues": [],
        "recovery_branches": [],
        "operator_workflow": {
            "summary": {
                "open_items": 1,
                "pending_owner_requests": 0,
                "pending_owner_mentions": 0,
                "pending_owner_tasks": 1,
                "pending_approvals": 0,
            },
            "recent_replied_requests": [
                {
                    "item_id": "req-1",
                    "kind": "owner_request",
                    "metadata": {
                        "message_id": "55",
                        "reply_excerpt": "Transport persistence проверен.",
                    },
                    "identity": {
                        "trace_id": "telegram:123abc",
                    },
                }
            ],
            "recent_activity": [
                {
                    "ts_utc": "2026-03-12T23:30:00+00:00",
                    "action": "reply_sent",
                    "actor": "kraab",
                    "trace_id": "telegram:123abc",
                }
            ],
        },
    }


def test_attach_summary_mentions_operator_workflow_truth() -> None:
    """Attach summary должен кратко фиксировать pending/reply state operator workflow."""
    summary = handoff_module._build_attach_summary_md(
        runtime_snapshot=_runtime_snapshot_payload(),
        acceptance={},
        ops_evidence={},
    )

    assert "Operator workflow" in summary
    assert "pending_owner_tasks" in summary
    assert "recent_reply_trace" in summary
    assert "telegram:123abc" in summary


def test_handoff_manifest_carries_operator_workflow_snapshot(tmp_path: Path) -> None:
    """Manifest должен сохранять operator workflow как machine-readable блок."""
    bundle_dir = tmp_path / "handoff_bundle"
    bundle_dir.mkdir()
    (bundle_dir / "ATTACH_SUMMARY_RU.md").write_text("ok", encoding="utf-8")
    original_bundle_dir = handoff_module.BUNDLE_DIR
    try:
        handoff_module.BUNDLE_DIR = bundle_dir
        manifest = handoff_module._build_handoff_manifest(
            runtime_snapshot=_runtime_snapshot_payload(),
            acceptance={},
            ops_evidence={},
            bundle_zip_path=bundle_dir / "bundle.zip",
        )
    finally:
        handoff_module.BUNDLE_DIR = original_bundle_dir

    assert manifest["operator_workflow"]["summary"]["pending_owner_tasks"] == 1
    assert manifest["operator_workflow"]["recent_activity"][0]["action"] == "reply_sent"
