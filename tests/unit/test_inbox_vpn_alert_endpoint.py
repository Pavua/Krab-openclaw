# -*- coding: utf-8 -*-
"""
VPN Phase C — alerts bridge endpoint tests.

POST /api/inbox/create-vpn-alert принимает алерты от VPN watchdog'ов
(`cert_guard`, `disk_guard`, `watchdog_vpn_panel`, `bruteforce_audit`)
и кладёт их в Krab inbox с kind=vpn_alert + dedupe по source_script+title.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.inbox_router import build_inbox_router


def _build_ctx() -> RouterContext:
    return RouterContext(
        deps={},
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda h, t: None,
    )


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(build_inbox_router(_build_ctx()))
    return TestClient(app)


def test_vpn_alert_post_creates_inbox_item(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST с валидными полями → upsert_item с kind=vpn_alert + source=vpn-watchdog."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    with patch(
        "src.modules.web_routers.inbox_router.inbox_service.upsert_item",
        return_value={"ok": True, "created": True, "item": {"item_id": "abc123"}},
    ) as mock_upsert:
        resp = _client().post(
            "/api/inbox/create-vpn-alert",
            json={
                "title": "Cert expires in 7 days",
                "body": "vpn.example.com cert expires 2026-05-06",
                "severity": "warning",
                "source_script": "cert_guard.sh",
                "metadata": {"domain": "vpn.example.com", "days_left": 7},
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["result"]["created"] is True

    mock_upsert.assert_called_once()
    kwargs = mock_upsert.call_args.kwargs
    assert kwargs["kind"] == "vpn_alert"
    assert kwargs["source"] == "vpn-watchdog"
    assert kwargs["severity"] == "warning"
    assert kwargs["title"] == "Cert expires in 7 days"
    assert kwargs["dedupe_key"] == "vpn_alert::cert_guard.sh::Cert expires in 7 days"
    # source_script + origin вшиваются в metadata
    assert kwargs["metadata"]["source_script"] == "cert_guard.sh"
    assert kwargs["metadata"]["origin"] == "vpn-watchdog"
    assert kwargs["metadata"]["domain"] == "vpn.example.com"


def test_vpn_alert_severity_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    """Все три валидные severity (info/warning/error) проходят и пробрасываются дальше."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    for sev in ("info", "warning", "error"):
        with patch(
            "src.modules.web_routers.inbox_router.inbox_service.upsert_item",
            return_value={"ok": True, "created": True, "item": {}},
        ) as mock_upsert:
            resp = _client().post(
                "/api/inbox/create-vpn-alert",
                json={
                    "title": f"alert-{sev}",
                    "body": "body",
                    "severity": sev,
                    "source_script": "watchdog.sh",
                },
            )
        assert resp.status_code == 200, sev
        assert mock_upsert.call_args.kwargs["severity"] == sev

    # Неизвестная severity → 400
    resp = _client().post(
        "/api/inbox/create-vpn-alert",
        json={
            "title": "x",
            "body": "y",
            "severity": "panic",
            "source_script": "w.sh",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "vpn_alert_invalid_severity"


def test_vpn_alert_missing_fields_returns_400(monkeypatch: pytest.MonkeyPatch) -> None:
    """Пустые title/body или source_script → 400 c понятным detail."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)

    # пустой title
    resp = _client().post(
        "/api/inbox/create-vpn-alert",
        json={"title": "", "body": "B", "source_script": "x.sh"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "vpn_alert_title_body_required"

    # пустой body
    resp = _client().post(
        "/api/inbox/create-vpn-alert",
        json={"title": "T", "body": "", "source_script": "x.sh"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "vpn_alert_title_body_required"

    # отсутствует source_script
    resp = _client().post(
        "/api/inbox/create-vpn-alert",
        json={"title": "T", "body": "B"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "vpn_alert_source_script_required"


def test_vpn_alert_multiple_alerts_dedupe_by_source_and_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Несколько алертов с одинаковым source_script+title → одинаковый dedupe_key
    (upsert_item обновит существующий item, а не создаст новый — это поведение
    `upsert_item`; здесь проверяем, что endpoint детерминированно строит ключ).
    """
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    with patch(
        "src.modules.web_routers.inbox_router.inbox_service.upsert_item",
        return_value={"ok": True, "created": False, "item": {}},
    ) as mock_upsert:
        for body_text in ("first body", "updated body", "third body"):
            resp = _client().post(
                "/api/inbox/create-vpn-alert",
                json={
                    "title": "Disk usage 95%",
                    "body": body_text,
                    "severity": "error",
                    "source_script": "disk_guard.sh",
                },
            )
            assert resp.status_code == 200

    # все 3 вызова должны идти на один и тот же dedupe_key
    dedupe_keys = [c.kwargs["dedupe_key"] for c in mock_upsert.call_args_list]
    assert len(dedupe_keys) == 3
    assert len(set(dedupe_keys)) == 1
    assert dedupe_keys[0] == "vpn_alert::disk_guard.sh::Disk usage 95%"
