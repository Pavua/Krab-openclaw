# -*- coding: utf-8 -*-
"""Юнит-тесты расширенного health-report OpenClawClient."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src.core.openclaw_client import OpenClawClient


class FakeOpenClawClient(OpenClawClient):
    """Подменяет сетевой слой для детерминированных тестов."""

    def __init__(self):
        super().__init__(base_url="http://localhost:18789", api_key="")
        self.responses = {
            "auth": {"ok": True, "path": "/auth/providers/health", "tried": ["/v1/auth/providers/health", "/auth/providers/health"], "status": 200, "data": {"providers": ["openai-codex", "google-gemini-cli"]}},
            "browser": {"ok": True, "path": "/browser/health", "tried": ["/v1/browser/health", "/browser/health"], "status": 200, "data": {"ready": True}},
            "tools": {"ok": True, "path": "/tools/registry", "tried": ["/v1/tools", "/tools/registry"], "status": 200, "data": {"tools": ["web_search", "web_fetch"]}},
        }

    async def health_check(self) -> bool:  # type: ignore[override]
        return True

    async def _probe_first_available(self, paths, timeout=5):  # type: ignore[override]
        if "/v1/auth/providers/health" in paths:
            return self.responses["auth"]
        if "/v1/browser/health" in paths:
            return self.responses["browser"]
        return self.responses["tools"]

    async def invoke_tool(self, tool_name: str, args: dict):  # type: ignore[override]
        return {
            "details": {
                "results": [
                    {"title": "ok", "url": "https://example.com", "description": "test"}
                ]
            }
        }

    async def _request_json(  # type: ignore[override]
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        timeout: int = 15,
    ):
        if path in {"/v1/browser/smoke", "/browser/smoke", "/v1/automation/browser/smoke"}:
            return {
                "ok": True,
                "status": 200,
                "path": path,
                "data": {"url": (payload or {}).get("url", "https://example.com"), "title": "Browser OK"},
            }
        return {
            "ok": False,
            "status": 404,
            "path": path,
            "data": {"error": "not_found"},
        }

    def _inspect_local_lmstudio_profile(self):  # type: ignore[override]
        return {
            "path": "/tmp/auth-profiles.json",
            "present": True,
            "provider_hint": "lmstudio",
            "error": "",
        }


class FakeBrowserToolFallbackClient(FakeOpenClawClient):
    async def _request_json(  # type: ignore[override]
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        timeout: int = 15,
    ):
        # Эмулируем недоступность endpoint browser smoke.
        if path in {"/v1/browser/smoke", "/browser/smoke", "/v1/automation/browser/smoke"}:
            return {"ok": False, "status": 404, "path": path, "data": {"error": "not_found"}}
        return {"ok": True, "status": 200, "path": path, "data": {}}

    async def invoke_tool(self, tool_name: str, args: dict):  # type: ignore[override]
        if tool_name == "web_fetch":
            return {"content": [{"text": "ok"}], "details": {"title": "Fetched"}}
        return {"error": "tool_not_found"}


class FakeHtmlAuthClient(FakeOpenClawClient):
    async def _probe_first_available(self, paths, timeout=5):  # type: ignore[override]
        if "/v1/auth/providers/health" in paths:
            return {
                "ok": True,
                "path": "/v1/auth/providers/health",
                "tried": ["/v1/auth/providers/health"],
                "status": 200,
                "data": {"raw": "<!doctype html><html>control</html>"},
            }
        return await super()._probe_first_available(paths, timeout=timeout)


class FakeMissingLmstudioProfileClient(FakeOpenClawClient):
    def _inspect_local_lmstudio_profile(self):  # type: ignore[override]
        return {
            "path": "/tmp/auth-profiles.json",
            "present": False,
            "provider_hint": "lmstudio",
            "error": "lmstudio_profile_missing",
        }


class OpenClawClientHealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_json_network_error_is_safe(self):
        client = OpenClawClient(base_url="http://127.0.0.1:1", api_key="")
        result = await client._request_json("GET", "/health", timeout=1)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], 0)
        self.assertIn("error", result)

    async def test_get_auth_provider_health(self):
        client = FakeOpenClawClient()
        with patch.dict(
            "os.environ",
            {"OPENCLAW_REQUIRED_AUTH_PROVIDERS": "openai-codex,google-gemini-cli"},
            clear=False,
        ):
            payload = await client.get_auth_provider_health()
        self.assertTrue(payload["available"])
        self.assertEqual(payload["path"], "/auth/providers/health")
        self.assertEqual(payload["status"], 200)
        self.assertIn("openai-codex", payload["payload"]["providers"])
        self.assertTrue(payload["ready_for_subscriptions"])
        self.assertEqual(payload["missing_required"], [])
        self.assertEqual(payload["unhealthy_required"], [])
        self.assertTrue(payload["providers"]["openai-codex"]["healthy"])
        self.assertEqual(payload["status_reason"], "ok")

    async def test_get_tools_overview_count(self):
        client = FakeOpenClawClient()
        payload = await client.get_tools_overview()
        self.assertTrue(payload["available"])
        self.assertEqual(payload["tools_count"], 2)

    async def test_get_health_report_aggregates_sections(self):
        client = FakeOpenClawClient()
        with patch.dict(
            "os.environ",
            {"OPENCLAW_REQUIRED_AUTH_PROVIDERS": "openai-codex,google-gemini-cli"},
            clear=False,
        ):
            report = await client.get_health_report()
        self.assertTrue(report["gateway"])
        self.assertTrue(report["auth"]["available"])
        self.assertTrue(report["browser"]["available"])
        self.assertTrue(report["tools"]["available"])
        self.assertTrue(report["ready_for_subscriptions"])

    async def test_get_auth_provider_health_detects_missing_required(self):
        client = FakeOpenClawClient()
        with patch.dict(
            "os.environ",
            {"OPENCLAW_REQUIRED_AUTH_PROVIDERS": "openai-codex,google-gemini-cli,qwen-portal-auth"},
            clear=False,
        ):
            payload = await client.get_auth_provider_health()
        self.assertIn("qwen-portal-auth", payload["missing_required"])
        self.assertFalse(payload["ready_for_subscriptions"])

    async def test_get_auth_provider_health_marks_route_unavailable_for_html_payload(self):
        client = FakeHtmlAuthClient()
        payload = await client.get_auth_provider_health()
        self.assertFalse(payload["available"])
        self.assertEqual(payload["status_reason"], "gateway_route_unavailable")

    async def test_get_auth_provider_health_detects_missing_lmstudio_profile(self):
        client = FakeMissingLmstudioProfileClient()
        with patch.dict(
            "os.environ",
            {"OPENCLAW_REQUIRED_AUTH_PROVIDERS": "openai-codex,google-gemini-cli"},
            clear=False,
        ):
            payload = await client.get_auth_provider_health()
        self.assertFalse(payload["ready_for_subscriptions"])
        self.assertEqual(payload["status_reason"], "auth_missing_lmstudio_profile")

    async def test_get_deep_health_report_ready(self):
        client = FakeOpenClawClient()
        with patch.dict(
            "os.environ",
            {"OPENCLAW_REQUIRED_AUTH_PROVIDERS": "openai-codex,google-gemini-cli"},
            clear=False,
        ):
            report = await client.get_deep_health_report()
        self.assertTrue(report["ready"])
        self.assertEqual(report["issues"], [])
        self.assertTrue(report["tool_smoke"]["ok"])

    async def test_get_remediation_plan_no_issues(self):
        client = FakeOpenClawClient()
        with patch.dict(
            "os.environ",
            {
                "OPENCLAW_REQUIRED_AUTH_PROVIDERS": "openai-codex,google-gemini-cli",
                "OPENCLAW_API_KEY": "sk-test",
            },
            clear=False,
        ):
            plan = await client.get_remediation_plan()
        self.assertTrue(plan["ready"])
        self.assertEqual(plan["open_items"], 0)
        self.assertEqual(plan["steps"][0]["id"], "no_action_needed")

    async def test_get_remediation_plan_with_missing_provider(self):
        client = FakeOpenClawClient()
        with patch.dict(
            "os.environ",
            {
                "OPENCLAW_REQUIRED_AUTH_PROVIDERS": "openai-codex,google-gemini-cli,qwen-portal-auth",
                "OPENCLAW_API_KEY": "sk-test",
            },
            clear=False,
        ):
            plan = await client.get_remediation_plan()
        self.assertFalse(plan["ready"])
        self.assertGreater(plan["open_items"], 0)
        step_ids = {item["id"] for item in plan["steps"]}
        self.assertIn("enable_provider_qwen-portal-auth", step_ids)

    async def test_run_browser_smoke_via_endpoint(self):
        client = FakeOpenClawClient()
        smoke = await client.run_browser_smoke(url="https://example.com")
        self.assertTrue(smoke["ok"])
        self.assertEqual(smoke["channel"], "endpoint")
        self.assertIn("endpoint_attempts", smoke)

    async def test_run_browser_smoke_via_tool_fallback(self):
        client = FakeBrowserToolFallbackClient()
        smoke = await client.run_browser_smoke(url="https://example.com")
        self.assertTrue(smoke["ok"])
        self.assertEqual(smoke["channel"], "tool")
        self.assertEqual(smoke["tool"], "web_fetch")

    async def test_get_browser_smoke_report(self):
        client = FakeOpenClawClient()
        with patch.dict(
            "os.environ",
            {"OPENCLAW_REQUIRED_AUTH_PROVIDERS": "openai-codex,google-gemini-cli"},
            clear=False,
        ):
            report = await client.get_browser_smoke_report(url="https://example.com")
        self.assertIn("base", report)
        self.assertIn("browser_smoke", report)
        self.assertTrue(report["browser_smoke"]["ok"])


if __name__ == "__main__":
    unittest.main()
