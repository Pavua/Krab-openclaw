# -*- coding: utf-8 -*-
"""
Тесты operator-workflow snapshot для web runtime endpoint-ов.

Покрываем:
1) `/api/inbox/status` отдаёт не только summary, но и workflow buckets;
2) `/api/runtime/handoff` включает operator workflow для handoff truth;
3) `/api/ops/runtime_snapshot` тоже видит тот же workflow-срез.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.core.inbox_service import InboxService
from src.modules.web_app import WebApp


class _DummyRouter:
    """Минимальный роутер-заглушка для инициализации WebApp."""

    def __init__(self) -> None:
        self.openclaw_client = _FakeOpenClaw()
        self.active_tier = "free"
        self._stats = {"local_failures": 0, "cloud_failures": 0}
        self._preflight_cache = {}

    def get_model_info(self) -> dict:
        return {}


class _FakeOpenClaw:
    """Фейковый OpenClaw клиент для handoff/runtime snapshot тестов."""

    async def health_check(self) -> bool:
        return True

    async def health_report(self) -> dict:
        return {"ok": True, "status": "ok", "source": "fake-openclaw", "detail": {}}

    async def capabilities_report(self) -> dict:
        return {"ok": True, "status": "ok", "source": "fake-openclaw", "detail": {}}

    def get_last_runtime_route(self) -> dict:
        return {
            "channel": "cloud_primary",
            "provider": "google",
            "model": "google/gemini-3.1-pro-preview",
            "status": "ok",
        }

    def get_tier_state_export(self) -> dict:
        return {
            "active_tier": "free",
            "last_error_code": None,
            "last_provider_status": "ok",
            "last_recovery_action": "none",
        }

    async def get_cloud_runtime_check(self) -> dict:
        return {"ok": True, "provider": "google", "active_tier": "free"}


class _FakeHealthClient:
    """Фейковый соседний сервис с базовой health truth."""

    async def health_check(self) -> bool:
        return True

    async def health_report(self) -> dict:
        return {"ok": True, "status": "ok", "source": "fake-service", "detail": {}}

    async def capabilities_report(self) -> dict:
        return {"ok": True, "status": "ok", "source": "fake-service", "detail": {}}


class _FakeQueue:
    """Минимальная очередь для `/api/ops/runtime_snapshot`."""

    def get_metrics(self) -> dict:
        return {"active_tasks": 0, "queued_tasks": 0}


def _make_client(*, inbox: InboxService) -> TestClient:
    deps = {
        "router": _DummyRouter(),
        "openclaw_client": _FakeOpenClaw(),
        "black_box": None,
        "health_service": None,
        "provisioning_service": None,
        "ai_runtime": None,
        "reaction_engine": None,
        "voice_gateway_client": _FakeHealthClient(),
        "krab_ear_client": _FakeHealthClient(),
        "perceptor": None,
        "watchdog": None,
        "queue": _FakeQueue(),
        "kraab_userbot": None,
    }
    app = WebApp(deps, port=18080, host="127.0.0.1")
    return TestClient(app.app)


def _seed_inbox(inbox: InboxService) -> None:
    """Готовит минимальный workflow-набор для operator snapshot тестов."""
    inbox.upsert_owner_task(
        title="Проверить reserve-safe режим",
        body="Нужен transport smoke после restart.",
        task_key="reserve-safe-smoke",
    )
    approval = inbox.upsert_approval_request(
        title="Разрешить платный provider",
        body="Нужен production smoke.",
        request_key="paid-provider",
        approval_scope="money",
        requested_action="enable_paid_provider",
    )["item"]
    inbox.resolve_approval(approval["item_id"], approved=True)
    source_request = inbox.upsert_incoming_owner_request(
        chat_id="123",
        message_id="55",
        text="Проверь transport persistence",
        sender_username="owner",
        chat_type="private",
    )["item"]
    inbox.escalate_item_to_owner_task(
        source_item_id=source_request["item_id"],
        title="Собрать post-restart followup",
        body="Нужен отдельный owner-task из incoming request.",
        task_key="post-restart-followup",
        source="owner-ui",
    )
    inbox.record_incoming_owner_reply(
        chat_id="123",
        message_id="55",
        response_text="Transport persistence проверен.",
        delivery_mode="edit_and_reply",
        reply_message_ids=["7001"],
        note="llm_response_delivered",
    )


def test_inbox_status_returns_workflow_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`/api/inbox/status` должен отдавать workflow buckets вместе с summary."""
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    _seed_inbox(inbox)
    # inbox_router импортирует singleton на уровне модуля, патчим там
    monkeypatch.setattr("src.modules.web_routers.inbox_router.inbox_service", inbox)
    client = _make_client(inbox=inbox)

    resp = client.get("/api/inbox/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["summary"]["pending_owner_tasks"] == 2
    assert data["workflow"]["recent_replied_requests"][0]["metadata"]["message_id"] == "55"
    assert data["workflow"]["recent_replied_requests"][0]["metadata"]["reply_message_ids"] == [
        "7001"
    ]
    assert data["workflow"]["approval_history"][0]["identity"]["approval_scope"] == "money"
    assert (
        data["workflow"]["recent_approval_decisions"][0]["metadata"]["approval_decision"]
        == "approved"
    )
    assert data["workflow"]["recent_owner_actions"][0]["action"] == "approved"
    assert (
        data["workflow"]["escalated_owner_items"][0]["metadata"]["followup_latest_kind"]
        == "owner_task"
    )
    assert data["workflow"]["linked_followups"][0]["metadata"]["source_kind"] == "owner_request"


def test_runtime_handoff_contains_operator_workflow(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`/api/runtime/handoff` должен включать operator-workflow snapshot."""
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    _seed_inbox(inbox)
    monkeypatch.setattr("src.modules.web_app.inbox_service", inbox)
    client = _make_client(inbox=inbox)

    resp = client.get("/api/runtime/handoff")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["operator_workflow"]["summary"]["pending_owner_requests"] == 0
    assert (
        data["operator_workflow"]["pending_owner_tasks"][0]["metadata"]["task_key"]
        == "post-restart-followup"
    )
    assert data["operator_workflow"]["recent_activity"][0]["action"] == "reply_sent"
    assert (
        data["operator_workflow"]["recent_approval_decisions"][0]["metadata"]["approval_decision"]
        == "approved"
    )
    assert data["operator_workflow"]["linked_followups"][0]["metadata"]["source_item_id"]
    assert data["inbox_summary"]["pending_owner_tasks"] == 2


def test_ops_runtime_snapshot_contains_operator_workflow(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`/api/ops/runtime_snapshot` должен не терять workflow truth observability-среза."""
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    _seed_inbox(inbox)
    monkeypatch.setattr("src.modules.web_app.inbox_service", inbox)

    async def _fake_local_truth(self, router) -> dict:
        del self, router
        return {"runtime_reachable": False, "active_model": "", "loaded_models": []}

    monkeypatch.setattr(WebApp, "_resolve_local_runtime_truth", _fake_local_truth)
    client = _make_client(inbox=inbox)

    resp = client.get("/api/ops/runtime_snapshot")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["operator_workflow"]["summary"]["pending_owner_tasks"] == 2
    assert (
        data["operator_workflow"]["recent_replied_requests"][0]["metadata"]["reply_excerpt"]
        == "Transport persistence проверен."
    )
    assert data["operator_workflow"]["recent_owner_actions"][0]["action"] == "approved"
    assert data["operator_workflow"]["escalated_owner_items"][0]["metadata"]["followup_count"] == 1
    assert data["operator_workflow"]["trace_index"]
