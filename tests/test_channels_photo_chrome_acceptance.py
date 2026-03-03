"""
Тесты для scripts/channels_photo_chrome_acceptance.py.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "channels_photo_chrome_acceptance.py"
    spec = importlib.util.spec_from_file_location("channels_photo_chrome_acceptance", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def test_classify_channels_skips_not_configured_failures():
    module = _load_module()
    payload = {
        "channels": [
            {"name": "Telegram", "status": "OK", "meta": "works"},
            {"name": "BlueBubbles", "status": "FAIL", "meta": "not configured"},
            {"name": "Discord", "status": "FAIL", "meta": "token invalid"},
        ]
    }
    result = module._classify_channels(payload)
    assert result["required_total"] == 2
    assert len(result["passed"]) == 1
    assert len(result["failed"]) == 1
    assert len(result["skipped"]) == 1


def test_build_report_adds_auth_required_warning(monkeypatch):
    module = _load_module()

    responses = {
        "/api/health/lite": ({"ok": True}, None),
        "/api/openclaw/channels/status": ({"channels": [{"name": "Telegram", "status": "OK", "meta": "works"}]}, None),
        "/api/openclaw/browser-smoke": (
            {
                "available": True,
                "report": {
                    "browser_smoke": {
                        "gateway_reachable": True,
                        "browser_http_reachable": True,
                        "browser_http_state": "auth_required",
                    }
                },
            },
            None,
        ),
        "/api/openclaw/photo-smoke": ({"available": True, "report": {"photo_smoke": {"ok": True}}}, None),
        "/api/openclaw/control-compat/status": ({"runtime_channels_ok": True, "impact_level": "none"}, None),
    }

    def _fake_fetch(url: str, timeout_sec: float = 10.0):
        for suffix, value in responses.items():
            if url.endswith(suffix):
                return value
        return {}, "not_found"

    monkeypatch.setattr(module, "_fetch_json", _fake_fetch)
    monkeypatch.setattr(
        module,
        "_run_browser_action_probe",
        lambda: {"ok": True, "state": "ok", "blocking": False, "detail": "ok", "tabs_count": 1},
    )

    report = module.build_report("http://127.0.0.1:8080")
    assert report["ok"] is True
    assert any("auth_required" in item for item in report["warnings"])


def test_build_report_tab_not_connected_is_non_blocking_by_default(monkeypatch):
    module = _load_module()

    responses = {
        "/api/health/lite": ({"ok": True}, None),
        "/api/openclaw/channels/status": ({"channels": [{"name": "Telegram", "status": "OK", "meta": "works"}]}, None),
        "/api/openclaw/browser-smoke": (
            {
                "available": True,
                "report": {
                    "browser_smoke": {
                        "gateway_reachable": True,
                        "browser_http_reachable": True,
                        "browser_http_state": "attached",
                    }
                },
            },
            None,
        ),
        "/api/openclaw/photo-smoke": ({"available": True, "report": {"photo_smoke": {"ok": True}}}, None),
        "/api/openclaw/control-compat/status": ({"runtime_channels_ok": True, "impact_level": "none"}, None),
    }

    def _fake_fetch(url: str, timeout_sec: float = 10.0):
        for suffix, value in responses.items():
            if url.endswith(suffix):
                return value
        return {}, "not_found"

    monkeypatch.setattr(module, "_fetch_json", _fake_fetch)
    monkeypatch.setattr(
        module,
        "_run_browser_action_probe",
        lambda: {
            "ok": False,
            "state": "tab_not_connected",
            "blocking": False,
            "detail": "attach required",
            "tabs_count": 0,
        },
    )

    report = module.build_report("http://127.0.0.1:8080")
    assert report["ok"] is True
    assert report["checks"]["browser_action_ready"] is True
    assert any("tab_not_connected" in item for item in report["warnings"])


def test_build_report_tab_not_connected_is_blocking_in_strict_mode(monkeypatch):
    module = _load_module()

    responses = {
        "/api/health/lite": ({"ok": True}, None),
        "/api/openclaw/channels/status": ({"channels": [{"name": "Telegram", "status": "OK", "meta": "works"}]}, None),
        "/api/openclaw/browser-smoke": (
            {
                "available": True,
                "report": {
                    "browser_smoke": {
                        "gateway_reachable": True,
                        "browser_http_reachable": True,
                        "browser_http_state": "attached",
                    }
                },
            },
            None,
        ),
        "/api/openclaw/photo-smoke": ({"available": True, "report": {"photo_smoke": {"ok": True}}}, None),
        "/api/openclaw/control-compat/status": ({"runtime_channels_ok": True, "impact_level": "none"}, None),
    }

    def _fake_fetch(url: str, timeout_sec: float = 10.0):
        for suffix, value in responses.items():
            if url.endswith(suffix):
                return value
        return {}, "not_found"

    monkeypatch.setattr(module, "_fetch_json", _fake_fetch)
    monkeypatch.setattr(
        module,
        "_run_browser_action_probe",
        lambda: {
            "ok": False,
            "state": "tab_not_connected",
            "blocking": False,
            "detail": "attach required",
            "tabs_count": 0,
        },
    )

    report = module.build_report("http://127.0.0.1:8080", strict_browser_action=True)
    assert report["ok"] is False
    assert report["checks"]["browser_action_ready"] is False
